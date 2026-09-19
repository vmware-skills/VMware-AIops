"""Blast-radius measurement for the gated VM tools (HLD §7, revised 2026-09-16).

``vm_power_off``, ``vm_migrate``, ``vm_revert_snapshot`` and
``vm_delete_snapshot`` preview by default. This module measures what each would
change — with reads only — and names what stands in the way (``blockers``) or
could not be read (``unmeasured``). The MCP tools refuse on either
(``gate.refuse_on``) and only then call the same ``vm_lifecycle`` executors the
CLI and the plan executor use, whose behaviour is unchanged.

``vm_delete`` has its own module (``vm_delete_gate``) because it also requires an
acknowledgement echo; these four do not, so Pilot can drive them with
``confirm=True`` after its own approval step.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from pyVmomi import vim, vmodl
from vmware_policy import sanitize

from vmware_aiops.ops import vm_lifecycle
from vmware_aiops.ops.gate import GateRefusedError
from vmware_aiops.ops.inventory import find_datastore_by_name, find_host_by_name

if TYPE_CHECKING:
    from pyVmomi.vim import ServiceInstance

#: Identifiers listed individually in a blast radius; counts cover the rest.
MAX_LISTED = 16

_READ_ERRORS = (vmodl.MethodFault, AttributeError, TypeError)
_MISSING = object()

_OFF = vim.VirtualMachine.PowerState.poweredOff
_SUSPENDED = vim.VirtualMachine.PowerState.suspended
_TOOLS_NOT_RUNNING = "guestToolsNotRunning"


def _read(fn: Callable[[], Any], fallback: Any = None) -> Any:
    try:
        return fn()
    except _READ_ERRORS:
        return fallback


def _name(obj: Any) -> str | None:
    value = _read(lambda: obj.name)
    return sanitize(value, 200) if isinstance(value, str) else None


def _unmeasured(**fields: Any) -> list[str]:
    return [field for field, value in fields.items() if value is None]


def _identity(vm: vim.VirtualMachine) -> dict[str, Any]:
    """Name, stable id and power state — the part every radius shares."""
    power_state = _read(lambda: vm.runtime.powerState)
    return {
        "vm": _name(vm) or "",
        "instance_uuid": _read(lambda: vm.config.instanceUuid),
        "power_state": str(power_state) if power_state is not None else None,
    }


def read_power_state(vm: vim.VirtualMachine) -> str | None:
    """The VM's power state now, as a string; None if unreadable."""
    state = _read(lambda: vm.runtime.powerState)
    return str(state) if state is not None else None


def vm_identity_radius(si: ServiceInstance, vm_name: str) -> dict[str, Any]:
    """Which VM a step without a gated tool of its own acts on (plan reset/suspend)."""
    ident = _identity(vm_lifecycle._require_vm(si, vm_name))
    return {
        **ident,
        "blockers": [],
        "unmeasured": _unmeasured(
            instance_uuid=ident["instance_uuid"], power_state=ident["power_state"]),
    }


# ─── power off ───────────────────────────────────────────────────────────────


def power_off_radius(
    si: ServiceInstance, vm_name: str, force: bool
) -> tuple[vim.VirtualMachine, dict[str, Any]]:
    """What powering ``vm_name`` off would do. Returns the VM measured, too."""
    vm = vm_lifecycle._require_vm(si, vm_name)
    ident = _identity(vm)
    tools = _read(lambda: vm.guest.toolsRunningStatus)
    tools_status = str(tools) if tools is not None else None
    host = _read(lambda: vm.runtime.host)

    unmeasured = _unmeasured(
        instance_uuid=ident["instance_uuid"], power_state=ident["power_state"],
    )
    if not force and tools_status is None:
        unmeasured.append("tools_status")

    blockers: list[str] = []
    if not force and ident["power_state"] == str(_SUSPENDED):
        blockers.append(
            "The VM is suspended, so a guest shutdown cannot run. force=True powers it "
            "off and discards the suspended memory state — decide that with the user."
        )
    elif not force and tools_status == _TOOLS_NOT_RUNNING:
        blockers.append(
            "VMware Tools is not running in the guest, so a graceful shutdown cannot "
            "be delivered. force=True is a hard power-off (like pulling the plug; "
            "unsaved guest data can be lost) — preview it and decide with the user."
        )

    return vm, {
        **ident,
        "host": _name(host) if host is not None else None,
        "tools_status": tools_status,
        "mode": "hard_power_off" if force else "guest_shutdown",
        "effect": (
            "The VM is cut off immediately without a guest shutdown; unsaved guest "
            "data and in-flight writes can be lost." if force else
            "The guest OS is asked to shut down via VMware Tools; its services stop."
        ),
        "noop": ident["power_state"] == str(_OFF),
        "blockers": blockers,
        "unmeasured": unmeasured,
    }


# ─── migrate ─────────────────────────────────────────────────────────────────


def _storage_reachable(datastores: list, host: Any) -> bool | None:
    """True if ``host`` mounts every datastore; None if a mount list is unreadable."""
    try:
        return all(host in [m.key for m in ds.host] for ds in datastores)
    except _READ_ERRORS:
        return None


def _migration_kind(power_state: str | None, same_host: bool, storage: bool) -> str | None:
    if power_state is None:
        return None
    compute = "live vMotion" if power_state == "poweredOn" else "cold migration"
    if same_host:
        return "storage migration only"
    return f"{compute} + storage migration" if storage else compute


def _target_host_checks(
    host: Any, to_host: str, blockers: list[str], unmeasured: list[str]
) -> dict[str, Any] | None:
    """Readiness of the destination host; appends what blocks it."""
    if host is None:
        blockers.append(
            f"Target host '{sanitize(to_host, 200)}' not found. Run cluster_info for "
            "the exact host names in the cluster, then preview again."
        )
        return None
    state = _read(lambda: host.runtime.connectionState)
    maintenance = _read(lambda: host.runtime.inMaintenanceMode)
    pool = _read(lambda: host.parent.resourcePool, _MISSING)
    if state is None:
        unmeasured.append("target_connection_state")
    elif str(state) != "connected":
        blockers.append(
            f"Target host '{sanitize(to_host, 200)}' is {state}, not connected. "
            "Pick a connected host from cluster_info."
        )
    if maintenance is None:
        unmeasured.append("target_in_maintenance_mode")
    elif maintenance:
        blockers.append(
            f"Target host '{sanitize(to_host, 200)}' is in maintenance mode and will "
            "not accept VMs. Pick another host, or exit maintenance mode first."
        )
    if pool is _MISSING:
        unmeasured.append("target_resource_pool")
    elif pool is None:
        blockers.append(
            f"Target host '{sanitize(to_host, 200)}' has no resource pool (standalone "
            "host outside a cluster?), so it cannot receive a migration."
        )
    return {
        "name": _name(host),
        "connection_state": str(state) if state is not None else None,
        "in_maintenance_mode": maintenance,
    }


def migrate_radius(
    si: ServiceInstance, vm_name: str, to_host: str, to_datastore: str | None
) -> dict[str, Any]:
    """What migrating ``vm_name`` to ``to_host`` (and ``to_datastore``) would do."""
    vm = vm_lifecycle._require_vm(si, vm_name)
    ident = _identity(vm)
    src_host = _read(lambda: vm.runtime.host, _MISSING)
    src_datastores = _read(lambda: list(vm.datastore or []))

    unmeasured = _unmeasured(
        instance_uuid=ident["instance_uuid"], power_state=ident["power_state"],
        source_datastores=src_datastores,
    )
    blockers: list[str] = []
    if src_host is _MISSING:
        unmeasured.append("source_host")
        src_name = None
    elif src_host is None:
        blockers.append(
            "The VM has no current host (still provisioning, or orphaned), so it "
            "cannot be migrated. Check it in vCenter."
        )
        src_name = None
    else:
        src_name = _name(src_host)

    same_host = src_name is not None and src_name == to_host
    radius: dict[str, Any] = {
        **ident,
        "source_host": src_name,
        "source_datastores": [_name(ds) for ds in (src_datastores or [])][:MAX_LISTED],
        "target_datastore": to_datastore,
        "noop": same_host and to_datastore is None,
        "blockers": blockers,
        "unmeasured": unmeasured,
    }
    if radius["noop"]:
        radius["target_host"] = {"name": src_name}
        radius["migration"] = None
        return radius

    target = find_host_by_name(si, to_host)
    radius["target_host"] = _target_host_checks(target, to_host, blockers, unmeasured)
    radius["migration"] = _migration_kind(ident["power_state"], same_host, bool(to_datastore))

    if to_datastore is not None:
        if find_datastore_by_name(si, to_datastore) is None:
            blockers.append(
                f"Target datastore '{sanitize(to_datastore, 200)}' not found. Run "
                "list_all_datastores (vmware-storage) for exact names."
            )
    elif target is not None and src_datastores:
        reachable = _storage_reachable(src_datastores, target)
        if reachable is None:
            unmeasured.append("storage_access")
        elif not reachable:
            blockers.append(
                f"Target host '{sanitize(to_host, 200)}' does not mount the VM's "
                "datastore(s), so a compute-only vMotion would be rejected. Pass "
                "to_datastore=<a datastore the target host mounts> to move the "
                "storage too."
            )
    return radius


# ─── snapshots ───────────────────────────────────────────────────────────────


def _walk(nodes: list, level: int = 0):
    for node in nodes or []:
        yield node, level
        yield from _walk(node.childSnapshotList, level + 1)


def _descendants(node: Any) -> list:
    return [n for n, _level in _walk(node.childSnapshotList)]


def _snapshot_summary(node: Any, level: int) -> dict[str, Any]:
    moid = _read(lambda: node.snapshot._moId)
    return {
        "name": sanitize(node.name, 200),
        "id": moid if isinstance(moid, str) else None,
        "created": str(_read(lambda: node.createTime)),
        "level": level,
    }


def _locate_snapshot(
    vm: vim.VirtualMachine, snapshot_name: str
) -> tuple[dict[str, Any], Any]:
    """The shared part of both snapshot radii: identity, tree, and the match."""
    ident = _identity(vm)
    try:
        info = vm.snapshot
        nodes = list(_walk(info.rootSnapshotList)) if info else []
        # The executors resolve the name the same way (raw or as shown), so
        # the gate measures the very node they would act on.
        meant = {id(n) for n in vm_lifecycle.matching_snapshots([n for n, _ in nodes],
                                                                 snapshot_name)}
        matches = [(n, lvl) for n, lvl in nodes if id(n) in meant]
        current = _read(lambda: info.currentSnapshot._moId) if info else None
    except _READ_ERRORS:
        nodes, matches, current = None, [], None

    unmeasured = _unmeasured(
        instance_uuid=ident["instance_uuid"], power_state=ident["power_state"],
        snapshot_tree=nodes,
    )
    blockers: list[str] = []
    shown = sanitize(snapshot_name, 200)
    if nodes is not None and not nodes:
        blockers.append(f"The VM has no snapshots, so there is no '{shown}' to use.")
    elif nodes is not None and not matches:
        available = ", ".join(sanitize(n.name, 100) for n, _ in nodes[:MAX_LISTED])
        blockers.append(
            f"Snapshot '{shown}' not found on this VM (available: {available}). "
            "Run vm_list_snapshots for exact names; they are case-sensitive."
        )
    elif len(matches) > 1:
        created = ", ".join(str(n.createTime) for n, _ in matches[:MAX_LISTED])
        blockers.append(
            f"{len(matches)} snapshots on this VM match '{shown}' (created {created}): "
            "duplicate names, or names that differ only by control/invisible "
            "characters. This tool refuses to guess which one you meant. Rename one "
            "in vCenter's snapshot manager, then preview again."
        )

    node, level = matches[0] if len(matches) == 1 else (None, None)
    radius: dict[str, Any] = {
        **ident,
        "snapshot": _snapshot_summary(node, level) if node is not None else None,
        "matches": len(matches),
        "snapshot_count": len(nodes) if nodes is not None else None,
        "blockers": blockers,
        "unmeasured": unmeasured,
    }
    if node is not None:
        radius["is_current_snapshot"] = (
            current is not None and current == radius["snapshot"]["id"]
        )
    return radius, node


def revert_snapshot_radius(
    si: ServiceInstance, vm_name: str, snapshot_name: str
) -> dict[str, Any]:
    """What reverting ``vm_name`` to ``snapshot_name`` would discard."""
    vm = vm_lifecycle._require_vm(si, vm_name)
    radius, node = _locate_snapshot(vm, snapshot_name)
    if node is not None:
        state = _read(lambda: node.state)
        radius["power_state_after"] = str(state) if state is not None else None
    radius["effect"] = (
        "Everything written to the VM's disks since the snapshot was taken — and, "
        "if the VM is running, its current memory state — is discarded. The VM "
        "returns to the power state it had when the snapshot was taken. Snapshots "
        "themselves are kept."
    )
    return radius


def delete_snapshot_radius(
    si: ServiceInstance, vm_name: str, snapshot_name: str, remove_children: bool
) -> dict[str, Any]:
    """What deleting ``snapshot_name`` (and optionally its subtree) would remove."""
    vm = vm_lifecycle._require_vm(si, vm_name)
    radius, node = _locate_snapshot(vm, snapshot_name)
    radius["remove_children"] = remove_children
    if node is not None:
        try:
            kids = _descendants(node)
        except _READ_ERRORS:
            kids = None
            radius["unmeasured"].append("child_snapshots")
        if kids is not None:
            radius["child_snapshot_count"] = len(kids)
            radius["children"] = [sanitize(k.name, 200) for k in kids[:MAX_LISTED]]
            radius["snapshots_removed"] = 1 + len(kids) if remove_children else 1
    radius["effect"] = (
        "The snapshot's delta disk is consolidated into its parent and the restore "
        "point is gone for good. The VM's current state does not change. "
        + ("Every child snapshot below it is deleted too." if remove_children else
           "Child snapshots are kept and re-parented.")
    )
    return radius


# ─── after the act ───────────────────────────────────────────────────────────


def did_not_act(tool: str, message: str) -> GateRefusedError:
    """The executor returned a refusal string (the world changed after measuring)."""
    return GateRefusedError(
        f"{tool} did not act: {message} Preview again to see the current state."
    )
