"""The blast-radius gate in front of plan apply and rollback (HLD §7).

A plan can delete VMs, power them off, revert snapshots and run guest commands,
so ``vm_apply_plan`` and ``vm_rollback_plan`` preview first. A plan must not be
a way around the gate of the tool that does the same thing on its own:

* The preview lists every step with its full parameters (secrets redacted), so
  the user sees the guest command, ``force``, ``remove_children``, paths.
* Each destructive step is measured the way its MCP tool measures it
  (``vm_gate`` / ``guest_gate`` / ``cluster_gate`` / ``vm_delete_gate``), and its
  ``blockers`` / ``unmeasured`` become the plan's, prefixed with the step index.
* A step whose object an earlier step of the same plan creates or changes
  cannot be measured now; its check is **deferred** — shown as such, and run by
  ``check_step`` immediately before that step at apply time. The same check
  runs before every destructive step, deferred or not, because the world moves
  between the preview and the act.

A ``delete_vm`` step also keeps its own acknowledgement, checked by
``plan_executor`` through ``vm_delete_gate``; a step without it is a blocker
here, so the plan is refused before step 0 rather than failing half-applied.

Every action the executor can run is classified here, on purpose, in exactly
one of three places: ``_MEASURES`` (checked the way its tool checks it),
``REFUSED_ACTIONS`` (gated by a tool this skill cannot measure — the plan is
refused), or ``NON_DESTRUCTIVE_ACTIONS`` with the reason it needs no check.
``check_step`` fails closed: an action in none of them, or a gated action that
has lost its measurement, is refused rather than run unchecked. Rollback steps
go through the same check.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from vmware_policy import sanitize

from vmware_aiops.ops.gate import GateRefusedError, refusal_message
from vmware_aiops.ops.planner import _ACTION_SCHEMA, load_plan

if TYPE_CHECKING:
    from pyVmomi.vim import ServiceInstance

logger = logging.getLogger(__name__)

#: Plan actions that destroy data, change a running workload, or run code in a
#: guest — what the preview counts as destructive. Which actions are *checked*
#: is ``GATED_ACTIONS`` (below), which also holds ``migrate`` and the storage
#: actions whose own tools are gated.
DESTRUCTIVE_ACTIONS = frozenset({
    "delete_vm", "power_off", "reset", "suspend",
    "delete_snapshot", "revert_snapshot",
    "guest_exec", "guest_upload", "guest_download",
    "delete_cluster", "cluster_remove_host", "iscsi_remove_target",
})

#: Actions that run unchecked, each with the reason no check is needed. A new
#: action lands here only with a reason; otherwise it is refused.
NON_DESTRUCTIVE_ACTIONS: dict[str, str] = {
    "power_on": "Starts a stopped VM; nothing is removed, and vm_power_on is not gated.",
    "create_vm": "Creates a new VM; nothing existing changes, and rollback deletes "
                 "only the VM it created (checked by instance UUID).",
    "reconfigure": "Changes CPU/memory of one VM, shown in full in the preview; "
                   "vm_reconfigure is not gated.",
    "create_snapshot": "Adds a restore point; nothing is removed.",
    "clone": "Copies a VM under a new name; the source is only read.",
    "deploy_ova": "Creates a new VM from an OVA; nothing existing changes.",
    "deploy_template": "Creates a new VM from a template; nothing existing changes.",
    "linked_clone": "Creates a new VM from a snapshot; the source is only read.",
    "attach_iso": "Connects an ISO to a VM's CD drive; no data is removed.",
    "convert_to_template": "Marks a powered-off VM as a template; its disks are kept.",
    "create_cluster": "Creates an empty cluster; nothing existing changes.",
    "configure_cluster": "Changes HA/DRS settings shown in the preview; "
                         "cluster_configure is not gated.",
    "cluster_add_host": "Moves a host into a cluster; no VM or data is removed.",
}

#: Actions gated by a vmware-storage tool this skill cannot measure. They stay in
#: the dispatch table so existing plan files load, but a plan holding one is
#: refused at preview, at apply and at rollback. Value: the tool to use instead.
REFUSED_ACTIONS: dict[str, str] = {
    "iscsi_enable": "storage_iscsi_enable",
    "iscsi_add_target": "storage_iscsi_add_target",
    "iscsi_remove_target": "storage_iscsi_remove_target",
    "storage_rescan": "storage_rescan",
}

#: Step parameters that identify what a step acts on. Credentials never appear.
_IDENTITY_KEYS = (
    "vm_name", "new_name", "source_vm_name", "template_name", "snapshot_name",
    "cluster_name", "host_name", "address",
)

#: Parameter names whose values are never shown or echoed, matched as whole
#: tokens of the key (split on ``_``/``-``/camelCase), so ``db_pwd`` and
#: ``authToken`` match and ``bypass``/``passthrough``/``author`` do not.
_SECRET_TOKEN = re.compile(
    r"(?:^|_)(?:password|passwd|passphrase|pwd|secret|token|api_?key|auth"
    r"|credentials?|private_key)s?(?:_|$)"
)
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
REDACTED = "***redacted***"

#: Longest parameter value the preview shows whole (the guest gate's command cap).
MAX_PARAM_CHARS = 1000

#: Actions whose ``vm_name`` is read, not changed (``clone`` copies its source).
_READS_VM_NAME = frozenset({"clone"})


def is_secret_key(key: Any) -> bool:
    """True if a parameter named ``key`` holds a secret."""
    normal = re.sub(r"[^a-z0-9]+", "_", _CAMEL.sub("_", str(key)).lower())
    return bool(_SECRET_TOKEN.search(normal))


class PlanStateError(ValueError):
    """The plan does not exist or is not in a state this call can act on."""


# ─── showing parameters ──────────────────────────────────────────────────────


def redact(value: Any) -> Any:
    """``value`` with every secret-looking key's value replaced; a new object."""
    if isinstance(value, dict):
        return {
            k: (REDACTED if is_secret_key(k) and v is not None else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def redact_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """A plan (or apply result) safe to return: step parameters redacted."""
    if not isinstance(plan, dict) or not isinstance(plan.get("steps"), list):
        return plan
    return {
        **plan,
        "steps": [
            {**s, "params": redact(s.get("params")),
             "rollback_params": redact(s.get("rollback_params"))}
            if isinstance(s, dict) else s
            for s in plan["steps"]
        ],
    }


def _display(value: Any) -> Any:
    """What the preview shows for a parameter: redacted, strings sanitized and capped."""
    value = redact(value)
    if isinstance(value, dict):
        return {k: _display(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_display(v) for v in value]
    if isinstance(value, str):
        return sanitize(value, MAX_PARAM_CHARS)
    return value


def _long_params(index: Any, params: dict[str, Any]) -> list[str]:
    """A blocker per parameter the preview cannot show whole."""
    return [
        f"Step {index}: parameter '{sanitize(str(k), 60)}' is {len(v)} characters; the "
        f"preview shows only the first {MAX_PARAM_CHARS}, so what would run cannot be "
        "reviewed in full. Shorten it (a script uploaded first, for a guest command) "
        "and create the plan again."
        for k, v in params.items()
        if isinstance(v, str) and not is_secret_key(k) and len(v) > MAX_PARAM_CHARS
    ]


def _identity(params: dict[str, Any] | None) -> dict[str, str]:
    return {
        k: sanitize(str(params[k]), 200)
        for k in _IDENTITY_KEYS if params and params.get(k) is not None
    }


# ─── what a step changes, what its check depends on ──────────────────────────


def _changes(step: dict[str, Any]) -> set[tuple[str, str]]:
    """The objects a step creates or changes, as (kind, name)."""
    action, params = step.get("action"), step.get("params") or {}
    changed: set[tuple[str, str]] = set()
    if params.get("vm_name") and action not in _READS_VM_NAME:
        changed.add(("vm", str(params["vm_name"])))
    if params.get("new_name"):
        changed.add(("vm", str(params["new_name"])))
    for kind in ("cluster", "host"):
        if params.get(f"{kind}_name"):
            changed.add((kind, str(params[f"{kind}_name"])))
    return changed


def _depends_on(step: dict[str, Any]) -> tuple[set[tuple[str, str]], bool]:
    """The objects a step's check reads, and whether it reads every VM's state.

    Removing a host or deleting a cluster is measured on the VMs it carries,
    which any earlier VM step may have powered off, moved or created.
    """
    action, params = step.get("action"), step.get("params") or {}
    if action == "delete_cluster":
        return {("cluster", str(params.get("cluster_name")))}, True
    if action == "cluster_remove_host":
        return {("cluster", str(params.get("cluster_name"))),
                ("host", str(params.get("host_name")))}, True
    return {("vm", str(params.get("vm_name")))}, False


def _deferred_after(steps: list[dict[str, Any]], position: int) -> Any:
    """The index of the latest earlier step that changes what this step's check reads."""
    wanted, any_vm = _depends_on(steps[position])
    for earlier in reversed(steps[:position]):
        changed = _changes(earlier)
        if changed & wanted or (any_vm and any(kind == "vm" for kind, _ in changed)):
            return earlier.get("index")
    return None


# ─── measuring one step as its tool would ────────────────────────────────────


def _measure_delete_vm(si: ServiceInstance, p: dict[str, Any]) -> dict[str, Any]:
    from vmware_aiops.ops import vm_delete_gate

    radius = vm_delete_gate.vm_delete_blast_radius(si, p["vm_name"])
    ack = p.get("acknowledge_blast_radius")
    if isinstance(ack, dict) and not radius["blockers"] and not radius["unmeasured"]:
        stale = vm_delete_gate._refusal(radius, ack)
        if stale:
            return {**radius, "blockers": [stale]}
    return radius


def _measure_power_off(si: ServiceInstance, p: dict[str, Any]) -> dict[str, Any]:
    from vmware_aiops.ops.vm_gate import power_off_radius

    return power_off_radius(si, p["vm_name"], bool(p.get("force", False)))[1]


def _measure_guest(kind: str) -> Callable[[ServiceInstance, dict[str, Any]], dict[str, Any]]:
    def measure(si: ServiceInstance, p: dict[str, Any]) -> dict[str, Any]:
        from vmware_aiops.ops import guest_gate

        if kind == "exec":
            return guest_gate.measure_guest_exec(
                si, p["vm_name"], p["command"], p["username"],
                arguments=p.get("arguments", ""), working_directory=p.get("working_directory"),
            )
        if kind == "upload":
            return guest_gate.measure_guest_upload(
                si, p["vm_name"], p["local_path"], p["guest_path"], p["username"])
        return guest_gate.measure_guest_download(
            si, p["vm_name"], p["guest_path"], p["local_path"], p["username"])
    return measure


def _measure_vm_state(si: ServiceInstance, p: dict[str, Any]) -> dict[str, Any]:
    from vmware_aiops.ops.vm_gate import vm_identity_radius

    return vm_identity_radius(si, p["vm_name"])


def _measure_snapshot(kind: str) -> Callable[[ServiceInstance, dict[str, Any]], dict[str, Any]]:
    def measure(si: ServiceInstance, p: dict[str, Any]) -> dict[str, Any]:
        from vmware_aiops.ops import vm_gate

        if kind == "revert":
            return vm_gate.revert_snapshot_radius(si, p["vm_name"], p["snapshot_name"])
        return vm_gate.delete_snapshot_radius(
            si, p["vm_name"], p["snapshot_name"], bool(p.get("remove_children", False)))
    return measure


def _measure_migrate(si: ServiceInstance, p: dict[str, Any]) -> dict[str, Any]:
    from vmware_aiops.ops.vm_gate import migrate_radius

    # The plan step carries no datastore: a compute-only move, as the executor runs it.
    return migrate_radius(si, p["vm_name"], p["target_host"], None)


def _measure_cluster_delete(si: ServiceInstance, p: dict[str, Any]) -> dict[str, Any]:
    from vmware_aiops.ops.cluster_gate import measure_cluster_delete

    return measure_cluster_delete(si, p["cluster_name"])


def _measure_host_removal(si: ServiceInstance, p: dict[str, Any]) -> dict[str, Any]:
    from vmware_aiops.ops.cluster_gate import measure_host_removal

    return measure_host_removal(si, p["cluster_name"], p["host_name"])


#: The measurement each gated action gets — its MCP tool's, where it has one.
_MEASURES: dict[str, Callable[[ServiceInstance, dict[str, Any]], dict[str, Any]]] = {
    "delete_vm": _measure_delete_vm,
    "power_off": _measure_power_off,
    "migrate": _measure_migrate,
    "reset": _measure_vm_state,
    "suspend": _measure_vm_state,
    "revert_snapshot": _measure_snapshot("revert"),
    "delete_snapshot": _measure_snapshot("delete"),
    "guest_exec": _measure_guest("exec"),
    "guest_upload": _measure_guest("upload"),
    "guest_download": _measure_guest("download"),
    "delete_cluster": _measure_cluster_delete,
    "cluster_remove_host": _measure_host_removal,
}

#: Every action that is checked before it runs: measured, or refused outright.
GATED_ACTIONS = frozenset(_MEASURES) | frozenset(REFUSED_ACTIONS)


def _refused_blocker(action: str) -> str:
    tool = REFUSED_ACTIONS[action]
    return (
        f"{action} is gated in vmware-storage and this skill cannot measure it, so a "
        f"plan will not run it. Remove the step, create the plan again, and run "
        f"{tool} (vmware-storage) on its own, previewing it first."
    )


def _unchecked_blocker(action: Any) -> str:
    return (
        f"'{sanitize(str(action), 60)}' has no measurement in this skill and is not "
        "classified as needing none, so it will not run unchecked. Create the plan "
        "without it and use the action's own tool."
    )


def _teaching_errors() -> tuple[type[Exception], ...]:
    from vmware_aiops.ops.cluster_mgmt import ClusterError, ClusterNotFoundError
    from vmware_aiops.ops.guest_ops import GuestOpsError
    from vmware_aiops.ops.inventory import InventoryError
    from vmware_aiops.ops.vm_lifecycle import VMNotFoundError

    return (VMNotFoundError, InventoryError, ClusterError, ClusterNotFoundError,
            GuestOpsError, KeyError, ValueError)


def measure_step(si: ServiceInstance, step: dict[str, Any]) -> dict[str, Any] | None:
    """The step's blast radius as its tool measures it; None if it has no measurement.

    A teaching error from the lookup (VM not found, ambiguous name, cluster not
    found) is a blocker; anything else that stops the measurement is unmeasured.
    Either refuses.
    """
    measure = _MEASURES.get(step.get("action"))
    if measure is None:
        return None
    try:
        return measure(si, step.get("params") or {})
    except _teaching_errors() as exc:
        text = f"missing parameter {exc}" if isinstance(exc, KeyError) else str(exc)
        return {"blockers": [sanitize(text, 500)], "unmeasured": []}
    except Exception as exc:  # noqa: BLE001 - reported as unmeasured, which refuses
        logger.warning("Measuring plan step %s failed", step.get("index"), exc_info=True)
        return {"blockers": [], "unmeasured": [f"measurement ({type(exc).__name__})"]}


def _step_label(step: dict[str, Any]) -> str:
    kind = "rollback " if step.get("rollback") else ""
    return f"Step {step.get('index')} ({kind}{step.get('action')})"


#: A measurement whose action would change nothing (VM already off, already on
#: that host) passes, as its MCP tool returns ``noop`` before any refusal.
_NOOP_NOTE = "noop: nothing would change (as its tool reports), so this step is not refused."


def check_step(si: ServiceInstance, step: dict[str, Any]) -> None:
    """Run a step's check immediately before it runs; raise to stop the plan.

    Fails closed: only an action classified non-destructive runs unchecked.
    A measured step whose radius says ``noop`` passes — its tool returns
    ``noop`` for it rather than refusing.
    A rollback ``delete_vm`` (``step["rollback"]``) is checked by the executor
    against the instance UUID it recorded, not here.
    """
    action = step.get("action")
    if action in NON_DESTRUCTIVE_ACTIONS:
        return
    if step.get("rollback") and action == "delete_vm":
        return
    label = f"{_step_label(step)}, checked just before it would run,"
    if action in REFUSED_ACTIONS:
        raise GateRefusedError(f"{label} refused: {_refused_blocker(action)}")
    radius = measure_step(si, step)
    if radius is None:
        raise GateRefusedError(f"{label} refused: no measurement — {_unchecked_blocker(action)}")
    if radius.get("noop"):
        return
    message = refusal_message(radius, label)
    if message:
        raise GateRefusedError(message)


# ─── the plan-level measurements ─────────────────────────────────────────────


def _load(plan_id: str, status: str, wrong_status: str) -> dict[str, Any]:
    plan = load_plan(plan_id)
    if plan is None:
        raise PlanStateError(
            f"Plan '{sanitize(plan_id, 100)}' not found. Run vm_list_plans for the "
            "plan ids that exist."
        )
    if plan.get("status") != status:
        raise PlanStateError(wrong_status.format(plan_id=plan_id, status=plan.get("status")))
    return plan


def _target_blocker(plan: dict[str, Any], target: str | None) -> list[str]:
    """A plan was checked against one vCenter; running it against another is blind.

    ``None`` — the default target — is a target of its own: a plan created
    without one runs only without one.
    """
    planned = plan.get("target")
    if target == planned:
        return []

    def said(value: str | None) -> str:
        return f"target='{sanitize(value, 100)}'" if value else "the default target"

    rerun = (f"target='{sanitize(planned, 100)}'" if planned
             else "no target (the default)")
    return [
        f"The plan was created against {said(planned)} but this call targets "
        f"{said(target)}. Re-run with {rerun}, or create the plan again on the "
        "target you mean."
    ]


def _step_entry(
    step: dict[str, Any], steps: list[dict[str, Any]], position: int, si: Any,
) -> tuple[dict[str, Any], list[str], list[str]]:
    """One step as the preview shows it, plus its blockers and unmeasured fields."""
    index, action, params = step.get("index"), step.get("action"), step.get("params") or {}
    entry: dict[str, Any] = {
        "index": index,
        "action": action,
        "params": _display(params),
        "target": _identity(params),
        "destructive": action in DESTRUCTIVE_ACTIONS,
        "gated": action in GATED_ACTIONS,
        "reversible": step.get("rollback_action") is not None,
    }
    blockers = _long_params(index, params)
    if action not in _ACTION_SCHEMA:
        blockers.append(
            f"Step {index} has unknown action '{sanitize(str(action), 60)}'. "
            "Create the plan again with vm_create_plan."
        )
        return entry, blockers, []
    if action == "delete_vm" and not isinstance(params.get("acknowledge_blast_radius"), dict):
        blockers.append(
            f"Step {index} (delete_vm '{sanitize(str(params.get('vm_name')), 200)}') has "
            "no acknowledge_blast_radius, so it would be refused after the earlier "
            "steps ran. Preview that VM with vm_delete, put its acknowledge_with into "
            "the step, and create the plan again."
        )
    fields, gate_blockers, unmeasured = _gate(steps, position, si)
    return {**entry, **fields}, blockers + gate_blockers, unmeasured


def _gate(
    steps: list[dict[str, Any]], position: int, si: Any,
) -> tuple[dict[str, Any], list[str], list[str]]:
    """How one step (forward or rollback) is checked: preview fields, blockers, unmeasured.

    The same rule as ``check_step``: non-destructive actions pass, refused and
    unclassified ones block, gated ones are measured now or deferred.
    """
    step = steps[position]
    action, index = step.get("action"), step.get("index")
    prefix = f"{_step_label(step)}: "
    if action in NON_DESTRUCTIVE_ACTIONS or (step.get("rollback") and action == "delete_vm"):
        return {}, [], []
    if action in REFUSED_ACTIONS:
        return {"check": "refused"}, [prefix + _refused_blocker(action)], []
    if action not in _MEASURES:
        return ({"check": "refused"},
                [prefix + "no measurement — " + _unchecked_blocker(action)], [])
    after = _deferred_after(steps, position)
    if si is None:
        return {"check": "not measured",
                "check_note": "The plan's target does not match; nothing was read."}, [], []
    if after is not None:
        return {
            "check": "deferred",
            "deferred_until_after_step": after,
            "check_note": (
                f"deferred to step {index}: step {after} of this plan creates or changes "
                "what this step's check reads, so it is measured immediately before this "
                "step runs; if it fails then, the plan stops there."
            ),
        }, [], []
    radius = measure_step(si, step)
    if radius.get("noop"):
        return {"check": "measured", "measured": radius, "check_note": _NOOP_NOTE}, [], []
    return (
        {"check": "measured", "measured": radius},
        [prefix + b for b in radius.get("blockers") or []],
        [prefix + u for u in radius.get("unmeasured") or []],
    )


def measure_apply(
    plan_id: str, target: str | None, connect: Callable[[], Any],
) -> dict[str, Any]:
    """Every step the plan would run, with its parameters and its tool's measurement.

    ``connect`` is called only when the target matches the plan's: there is no
    point reading a vCenter the plan was not made for.
    """
    plan = _load(
        plan_id, "pending",
        "Plan '{plan_id}' status is '{status}', expected 'pending'. Only a pending plan "
        "can be applied; create a new one with vm_create_plan.",
    )
    blockers = _target_blocker(plan, target)
    si = None if blockers else connect()
    steps = [s for s in plan.get("steps") or [] if isinstance(s, dict)]
    entries, unmeasured = [], []
    for position, step in enumerate(steps):
        entry, step_blockers, step_unmeasured = _step_entry(step, steps, position, si)
        entries.append(entry)
        blockers += step_blockers
        unmeasured += step_unmeasured
    destructive = [s["index"] for s in entries if s["destructive"]]
    return {
        "plan_id": plan["plan_id"],
        "plan_target": plan.get("target"),
        "step_count": len(entries),
        "steps": entries,
        "destructive_step_count": len(destructive),
        "destructive_steps": destructive,
        "deferred_steps": [s["index"] for s in entries if s.get("check") == "deferred"],
        "irreversible_steps": [s["index"] for s in entries if not s["reversible"]],
        "note": "Steps run in order; each destructive step is checked again just before "
                "it runs. A failed or refused step stops the plan and nothing rolls back "
                "on its own.",
        "unmeasured": unmeasured,
        "blockers": blockers,
    }


def measure_rollback(
    plan_id: str, target: str | None, connect: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """The rollback steps that would run, in order, each checked as a forward step is.

    Each gated rollback action is measured the way its tool measures it (or
    deferred behind an earlier rollback step that changes what it reads); its
    blockers and unmeasured fields refuse the rollback. ``connect`` is called
    only when the target matches the plan's.
    """
    plan = _load(
        plan_id, "failed",
        "Plan '{plan_id}' status is '{status}', rollback only available for 'failed' plans.",
    )
    executed = [s for s in reversed(plan.get("steps") or []) if s.get("status") == "success"]
    if not executed:
        raise PlanStateError(
            f"No executed steps to rollback in plan '{sanitize(plan_id, 100)}': nothing it "
            "ran succeeded, so there is nothing to undo."
        )
    blockers = _target_blocker(plan, target)
    si = None if blockers or connect is None else connect()
    runs = [
        {"index": s.get("index"), "action": s.get("rollback_action"),
         "params": s.get("rollback_params") or {}, "rollback": True}
        for s in executed if s.get("rollback_action") is not None
    ]
    unmeasured: list[str] = []
    would_run, skipped = [], []
    for step in executed:
        rb_action = step.get("rollback_action")
        if rb_action is None:
            skipped.append({"step_index": step.get("index"), "action": step.get("action"),
                            "target": _identity(step.get("params"))})
            continue
        entry = {
            "step_index": step.get("index"),
            "action": step.get("action"),
            "rollback_action": rb_action,
            "target": _identity(step.get("rollback_params")),
            "destructive": rb_action in DESTRUCTIVE_ACTIONS,
        }
        position = len(would_run)
        fields, step_blockers, step_unmeasured = _gate(runs, position, si)
        entry.update(fields)
        blockers += step_blockers
        unmeasured += step_unmeasured
        if rb_action == "delete_vm":
            uuid = (step.get("rollback_params") or {}).get("instance_uuid")
            entry["verifies_instance_uuid"] = uuid
            entry["identity_note"] = (
                "Deleted only if the VM with this name still has this instance UUID."
                if uuid else
                "The instance UUID of the VM this step created was not recorded, so "
                "this rollback step will be refused; check the VM in vCenter and delete "
                "it with vm_delete if it is the one."
            )
        would_run.append(entry)
    return {
        "plan_id": plan["plan_id"],
        "plan_target": plan.get("target"),
        "would_run": would_run,
        "would_run_count": len(would_run),
        "destructive_rollback_count": sum(1 for s in would_run if s["destructive"]),
        "deletes_vms": [s["target"].get("vm_name") for s in would_run
                        if s["rollback_action"] == "delete_vm"],
        "skipped_irreversible": skipped,
        "note": "Rolling back a created VM powers it off and deletes it with its disks, "
                "after checking it is the VM the plan created. Each destructive rollback "
                "step is checked again just before it runs; a refused check stops the "
                "rollback there and the plan stays failed, so it can be rolled back again "
                "once resolved (a created VM whose instance UUID was not recorded is "
                "refused the same way). A rollback step that fails when run does not "
                "stop the others. Rolling back a power-on is a hard power-off; a VM "
                "that is already off is a noop.",
        "unmeasured": unmeasured,
        "blockers": blockers,
    }
