"""Plan → Apply: sequential plan execution with rollback support."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from vmware_aiops.ops.planner import delete_plan, load_plan, save_plan

if TYPE_CHECKING:
    from pyVmomi.vim import ServiceInstance

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Action dispatcher — maps action names to ops functions
# ---------------------------------------------------------------------------


def _delete_acknowledged(si: ServiceInstance, vm_name: str, ack: Any) -> str:
    from vmware_aiops.ops.vm_delete_gate import delete_vm_acknowledged

    r = delete_vm_acknowledged(si, vm_name, ack)
    return (
        f"VM '{r['vm']}' deleted ({r['disk_count']} disk(s), {r['total_disk_gb']} GB, "
        f"{r['snapshot_count']} snapshot(s))."
    )


def _created_instance_uuid(si: ServiceInstance, vm_name: str) -> str | None:
    """The instance UUID of the VM a step just created; None if it cannot be read."""
    from vmware_aiops.ops.inventory import find_vm_by_name

    try:
        vm = find_vm_by_name(si, vm_name)
        uuid = vm.config.instanceUuid if vm is not None else None
    except Exception:  # noqa: BLE001 - not recorded; rollback then refuses this step
        logger.warning("Could not read the instance UUID of created VM '%s'", vm_name,
                       exc_info=True)
        return None
    return uuid if isinstance(uuid, str) and uuid else None


def _delete_created_vm(si: ServiceInstance, params: dict[str, Any]) -> str:
    """Delete the VM a plan step created — only if it is still that VM.

    The name alone is not identity: the VM could have been deleted and another
    created under its name since. The instance UUID recorded when the step ran
    must match, or nothing is deleted.
    """
    from pyVmomi import vim

    from vmware_aiops.ops.gate import GateRefusedError
    from vmware_aiops.ops.vm_lifecycle import _require_vm, _wait_for_task

    name, recorded = params["vm_name"], params.get("instance_uuid")
    if not recorded:
        raise GateRefusedError(
            f"Not deleted: this plan did not record the instance UUID of the VM it "
            f"created as '{name}', so the VM now named that cannot be shown to be it. "
            "Check it in vCenter and delete it with vm_delete if it is."
        )
    vm = _require_vm(si, name)
    actual = vm.config.instanceUuid
    if actual != recorded:
        raise GateRefusedError(
            f"Not deleted: the VM named '{name}' has instance UUID {actual}, but the VM "
            f"this plan created had {recorded}. It is a different VM; check it in vCenter."
        )
    if vm.runtime.powerState == vim.VirtualMachine.PowerState.poweredOn:
        _wait_for_task(vm.PowerOff())
    _wait_for_task(vm.Destroy_Task())
    return f"VM '{name}' (instance UUID {recorded}) deleted."


def _rollback_dispatch(si: ServiceInstance, action: str, params: dict[str, Any]) -> str:
    """Rollback of a step this plan itself executed.

    Undoing a ``create_vm`` / clone / deploy deletes the VM that step created,
    which is the rollback contract the user invoked explicitly — but only after
    checking it is still that VM (``_delete_created_vm``). Every other rollback
    action goes through ``_dispatch``.
    """
    if action == "delete_vm":
        return _delete_created_vm(si, params)
    return _dispatch(si, action, params)


def dispatch_actions() -> frozenset[str]:
    """Every action the executor can run — what ``plan_gate`` must classify."""
    return frozenset(_dispatch_table(None, {}))


def _dispatch(si: ServiceInstance, action: str, params: dict[str, Any]) -> str:
    """Execute a single action. Returns result string."""
    dispatch_table = _dispatch_table(si, params)
    handler = dispatch_table.get(action)
    if handler is None:
        raise ValueError(
            f"Unknown plan action: '{action}'. Supported: {sorted(dispatch_table)}. "
            f"Edit the plan to use one of those exact action names, then re-run "
            f"vm_apply_plan (CLI: vmware-aiops plan list shows pending plans)."
        )
    result = handler()
    return str(result) if result is not None else "OK"


def _dispatch_table(si: Any, params: dict[str, Any]) -> dict[str, Callable[[], Any]]:
    """The action → executor table; building it runs nothing."""
    from vmware_aiops.ops.vm_lifecycle import (
        clone_vm,
        create_snapshot,
        create_vm,
        delete_snapshot,
        migrate_vm,
        power_off_vm,
        power_on_vm,
        reconfigure_vm,
        reset_vm,
        revert_to_snapshot,
        suspend_vm,
    )
    from vmware_aiops.ops.guest_ops import guest_download, guest_exec, guest_upload
    from vmware_aiops.ops.vm_deploy import (
        attach_iso,
        convert_to_template,
        deploy_from_template,
        deploy_ova,
        linked_clone,
    )

    return {
        "power_on": lambda: power_on_vm(si, params["vm_name"]),
        "power_off": lambda: power_off_vm(si, params["vm_name"], force=params.get("force", False)),
        "reset": lambda: reset_vm(si, params["vm_name"]),
        "suspend": lambda: suspend_vm(si, params["vm_name"]),
        # Omitted/None optional params are dropped so create_vm's own
        # defaults apply (e.g. network_name="VM Network").
        "create_vm": lambda: create_vm(
            si, params["vm_name"],
            **{
                key: params[key]
                for key in (
                    "cpu", "memory_mb", "disk_gb", "network_name",
                    "datastore_name", "folder_path", "guest_id",
                )
                if params.get(key) is not None
            },
        ),
        # A plan must not be the way around vm_delete's gate: the step needs
        # the acknowledgement from a vm_delete preview, and is refused without.
        "delete_vm": lambda: _delete_acknowledged(
            si, params["vm_name"], params.get("acknowledge_blast_radius"),
        ),
        "reconfigure": lambda: reconfigure_vm(
            si, params["vm_name"],
            cpu=params.get("cpu"),
            memory_mb=params.get("memory_mb"),
        ),
        "create_snapshot": lambda: create_snapshot(
            si, params["vm_name"], params["snapshot_name"],
            description=params.get("description", ""),
            memory=params.get("memory", True),
            quiesce=params.get("quiesce", False),
        ),
        "delete_snapshot": lambda: delete_snapshot(
            si, params["vm_name"], params["snapshot_name"],
            remove_children=params.get("remove_children", False),
        ),
        "revert_snapshot": lambda: revert_to_snapshot(
            si, params["vm_name"], params["snapshot_name"],
        ),
        "clone": lambda: clone_vm(si, params["vm_name"], params["new_name"]),
        "migrate": lambda: migrate_vm(si, params["vm_name"], params["target_host"]),
        "deploy_ova": lambda: deploy_ova(
            si, params["ova_path"], params["vm_name"],
            datastore_name=params["datastore_name"],
            network_name=params["network_name"],
            folder_path=params.get("folder_path"),
            power_on=params.get("power_on", False),
            snapshot_name=params.get("snapshot_name"),
        ),
        "deploy_template": lambda: deploy_from_template(
            si, params["template_name"], params["new_name"],
            datastore_name=params.get("datastore_name"),
            cpu=params.get("cpu"),
            memory_mb=params.get("memory_mb"),
            power_on=params.get("power_on", False),
            snapshot_name=params.get("snapshot_name"),
        ),
        "linked_clone": lambda: linked_clone(
            si, params["source_vm_name"], params["new_name"],
            snapshot_name=params["snapshot_name"],
            cpu=params.get("cpu"),
            memory_mb=params.get("memory_mb"),
            power_on=params.get("power_on", False),
            baseline_snapshot=params.get("baseline_snapshot"),
        ),
        "attach_iso": lambda: attach_iso(si, params["vm_name"], params["iso_ds_path"]),
        "convert_to_template": lambda: convert_to_template(si, params["vm_name"]),
        "guest_exec": lambda: guest_exec(
            si, params["vm_name"], params["command"],
            params["username"], params["password"],
            arguments=params.get("arguments", ""),
            working_directory=params.get("working_directory"),
        ),
        "guest_upload": lambda: guest_upload(
            si, params["vm_name"], params["local_path"],
            params["guest_path"], params["username"], params["password"],
        ),
        "guest_download": lambda: guest_download(
            si, params["vm_name"], params["guest_path"],
            params["local_path"], params["username"], params["password"],
        ),
        # Cluster operations
        "create_cluster": lambda: _cluster_create(si, params),
        "delete_cluster": lambda: _cluster_delete(si, params),
        "configure_cluster": lambda: _cluster_configure(si, params),
        "cluster_add_host": lambda: _cluster_add_host(si, params),
        "cluster_remove_host": lambda: _cluster_remove_host(si, params),
        # iSCSI / Storage operations
        "iscsi_enable": lambda: _iscsi_enable(si, params),
        "iscsi_add_target": lambda: _iscsi_add_target(si, params),
        "iscsi_remove_target": lambda: _iscsi_remove_target(si, params),
        "storage_rescan": lambda: _storage_rescan(si, params),
    }


# ---------------------------------------------------------------------------
# Cluster dispatch helpers
# ---------------------------------------------------------------------------


def _cluster_create(si: ServiceInstance, params: dict[str, Any]) -> str:
    from vmware_aiops.ops.cluster_mgmt import create_cluster
    return create_cluster(
        si, cluster_name=params["cluster_name"],
        datacenter_name=params.get("datacenter_name"),
        ha_enabled=params.get("ha_enabled", False),
        drs_enabled=params.get("drs_enabled", False),
        drs_behavior=params.get("drs_behavior", "fullyAutomated"),
    )


def _cluster_delete(si: ServiceInstance, params: dict[str, Any]) -> str:
    from vmware_aiops.ops.cluster_mgmt import delete_cluster
    return delete_cluster(si, params["cluster_name"])


def _cluster_configure(si: ServiceInstance, params: dict[str, Any]) -> str:
    from vmware_aiops.ops.cluster_mgmt import configure_cluster
    return configure_cluster(
        si, cluster_name=params["cluster_name"],
        ha_enabled=params.get("ha_enabled"),
        drs_enabled=params.get("drs_enabled"),
        drs_behavior=params.get("drs_behavior"),
    )


def _cluster_add_host(si: ServiceInstance, params: dict[str, Any]) -> str:
    from vmware_aiops.ops.cluster_mgmt import add_host_to_cluster
    return add_host_to_cluster(si, cluster_name=params["cluster_name"], host_name=params["host_name"])


def _cluster_remove_host(si: ServiceInstance, params: dict[str, Any]) -> str:
    from vmware_aiops.ops.cluster_mgmt import remove_host_from_cluster
    return remove_host_from_cluster(si, cluster_name=params["cluster_name"], host_name=params["host_name"])


# ---------------------------------------------------------------------------
# iSCSI dispatch helpers
# ---------------------------------------------------------------------------


def _iscsi_enable(si: ServiceInstance, params: dict[str, Any]) -> str:
    from vmware_aiops.ops.iscsi_config import enable_software_iscsi
    return enable_software_iscsi(si, params["host_name"])


def _iscsi_add_target(si: ServiceInstance, params: dict[str, Any]) -> str:
    from vmware_aiops.ops.iscsi_config import add_iscsi_target
    return add_iscsi_target(si, params["host_name"], address=params["address"], port=params.get("port", 3260))


def _iscsi_remove_target(si: ServiceInstance, params: dict[str, Any]) -> str:
    from vmware_aiops.ops.iscsi_config import remove_iscsi_target
    return remove_iscsi_target(si, params["host_name"], address=params["address"], port=params.get("port", 3260))


def _storage_rescan(si: ServiceInstance, params: dict[str, Any]) -> str:
    from vmware_aiops.ops.iscsi_config import rescan_storage
    return rescan_storage(si, params["host_name"])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def apply_plan(
    si: ServiceInstance,
    plan_id: str,
    step_check: Callable[[ServiceInstance, dict[str, Any]], None] | None = None,
) -> dict:
    """Execute a plan step by step.

    Returns the final plan state dict with per-step results.
    On success, the plan file is deleted.
    On failure, the plan file is kept with status info and rollback_available flag.

    ``step_check`` runs immediately before each step and stops the plan by
    raising (the MCP tool passes ``plan_gate.check_step``, which re-measures
    every destructive step the way its own tool would). A step that creates a
    VM records that VM's instance UUID in its rollback parameters, so rollback
    deletes that VM and no other.
    """
    from vmware_aiops.ops.gate import GateRefusedError

    plan = load_plan(plan_id)
    if plan is None:
        return {"error": f"Plan '{plan_id}' not found"}
    if plan["status"] != "pending":
        return {"error": f"Plan '{plan_id}' status is '{plan['status']}', expected 'pending'"}

    plan["status"] = "executing"
    save_plan(plan)

    failed_index: int | None = None

    for step in plan["steps"]:
        now = datetime.now(timezone.utc).isoformat()
        step["executed_at"] = now
        try:
            if step_check is not None:
                step_check(si, step)
            result = _dispatch(si, step["action"], step["params"])
            step["status"] = "success"
            step["result"] = result
            if step.get("rollback_action") == "delete_vm":
                step["rollback_params"] = {
                    **step["rollback_params"],
                    "instance_uuid": _created_instance_uuid(
                        si, step["rollback_params"]["vm_name"]),
                }
            logger.info(
                "Plan %s step %d (%s): success",
                plan_id, step["index"], step["action"],
            )
        except Exception as exc:
            step["status"] = "failed"
            step["result"] = str(exc)
            step["refused_by_gate"] = isinstance(exc, GateRefusedError)
            failed_index = step["index"]
            logger.error(
                "Plan %s step %d (%s): FAILED — %s",
                plan_id, step["index"], step["action"], exc,
            )
            break

    # Mark remaining steps as skipped
    if failed_index is not None:
        for step in plan["steps"]:
            if step["index"] > failed_index:
                step["status"] = "skipped"

    if failed_index is None:
        plan["status"] = "completed"
        save_plan(plan)
        delete_plan(plan_id)
        logger.info("Plan %s completed successfully, file deleted", plan_id)
    else:
        plan["status"] = "failed"
        # Check if rollback is possible for executed steps
        executed_steps = [s for s in plan["steps"] if s["status"] == "success"]
        rollback_possible = any(s["rollback_action"] is not None for s in executed_steps)
        plan["rollback_available"] = rollback_possible
        save_plan(plan)

    return plan


def _stop_refused(
    plan: dict, step: dict, rollback_results: list[dict], exc: Exception,
) -> dict:
    """Stop a rollback at a refused step; the plan stays ``failed`` so it can be rerun."""
    rollback_results.append({
        "step_index": step["index"],
        "action": step["action"],
        "rollback_action": step.get("rollback_action"),
        "rollback_status": "refused",
        "error": str(exc),
    })
    logger.error(
        "Plan %s step %d rollback (%s): REFUSED — %s",
        plan["plan_id"], step["index"], step.get("rollback_action"), exc,
    )
    plan["rollback_results"] = rollback_results
    save_plan(plan)
    return {
        "plan_id": plan["plan_id"],
        "status": "failed",
        "stopped_at_step": step["index"],
        "rollback_results": rollback_results,
    }


def rollback_plan(
    si: ServiceInstance,
    plan_id: str,
    step_check: Callable[[ServiceInstance, dict[str, Any]], None] | None = None,
) -> dict:
    """Rollback already-executed steps of a failed plan in reverse order.

    Only rolls back steps that have a rollback_action defined.
    Steps marked irreversible are skipped with a warning.

    ``step_check`` runs immediately before each rollback action, given the
    rollback as a step (``index`` of the step it undoes, ``action`` and
    ``params`` of the rollback). If it raises, the rollback stops there: that
    step is reported ``refused`` with the check's message, nothing after it
    runs, and the plan stays ``failed`` so rollback can be run again once the
    cause is resolved (steps already undone are marked and not undone twice).
    A rollback that its own identity check refuses (``GateRefusedError`` from
    ``_delete_created_vm``: no instance UUID recorded, or a different VM under
    that name) stops the same way. Any other failing rollback action does not
    stop the others.
    """
    from vmware_aiops.ops.gate import GateRefusedError

    plan = load_plan(plan_id)
    if plan is None:
        return {"error": f"Plan '{plan_id}' not found"}
    if plan["status"] != "failed":
        return {"error": f"Plan '{plan_id}' status is '{plan['status']}', rollback only available for 'failed' plans"}

    # Get successfully executed steps in reverse order
    executed_steps = [
        s for s in reversed(plan["steps"]) if s["status"] == "success"
    ]

    if not executed_steps:
        return {"error": "No executed steps to rollback"}

    rollback_results: list[dict] = []

    for step in executed_steps:
        rollback_action = step.get("rollback_action")
        rollback_params = step.get("rollback_params")

        if rollback_action is None:
            entry = {
                "step_index": step["index"],
                "action": step["action"],
                "rollback_status": "skipped",
                "reason": "irreversible",
            }
            rollback_results.append(entry)
            logger.warning(
                "Plan %s step %d (%s): irreversible, skipping rollback",
                plan_id, step["index"], step["action"],
            )
            continue

        if step_check is not None:
            try:
                step_check(si, {"index": step["index"], "action": rollback_action,
                                "params": rollback_params or {}, "rollback": True})
            except Exception as exc:
                return _stop_refused(plan, step, rollback_results, exc)

        try:
            result = _rollback_dispatch(si, rollback_action, rollback_params)
            step["status"] = "rolled_back"
            entry = {
                "step_index": step["index"],
                "action": step["action"],
                "rollback_action": rollback_action,
                "rollback_status": "success",
                "result": result,
            }
            rollback_results.append(entry)
            logger.info(
                "Plan %s step %d rollback (%s): success",
                plan_id, step["index"], rollback_action,
            )
        except GateRefusedError as exc:
            # Refused by the rollback's own identity check (no instance UUID
            # recorded, or a different VM under that name): the same stop as a
            # refused check, as the preview said.
            return _stop_refused(plan, step, rollback_results, exc)
        except Exception as exc:
            entry = {
                "step_index": step["index"],
                "action": step["action"],
                "rollback_action": rollback_action,
                "rollback_status": "failed",
                "error": str(exc),
            }
            rollback_results.append(entry)
            logger.error(
                "Plan %s step %d rollback (%s): FAILED — %s",
                plan_id, step["index"], rollback_action, exc,
            )
            # Continue rolling back other steps even if one fails

    plan["status"] = "rolled_back"
    plan["rollback_results"] = rollback_results
    save_plan(plan)

    return {
        "plan_id": plan_id,
        "status": "rolled_back",
        "rollback_results": rollback_results,
    }
