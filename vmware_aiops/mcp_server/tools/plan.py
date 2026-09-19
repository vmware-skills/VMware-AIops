"""Plan → Apply tools: create/apply/rollback multi-step plans, list plans."""

from typing import Any, Optional

from vmware_policy import vmware_tool

from vmware_aiops.mcp_server._shared import _get_connection, mcp, tool_errors
from vmware_aiops.ops.gate import preview, refuse_on
from vmware_aiops.ops.plan_executor import apply_plan, rollback_plan
from vmware_aiops.ops.plan_gate import check_step, measure_apply, measure_rollback, redact_plan
from vmware_aiops.ops.planner import create_plan, list_plans


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="medium")
@tool_errors("dict")
def vm_create_plan(
    operations: list[dict[str, Any]],
    target: Optional[str] = None,
) -> dict:
    """[WRITE] Create an execution plan for multi-step VM operations.

    Use for 2+ steps or 2+ VMs. Validates actions, checks the targets exist
    in vSphere, and generates a plan with rollback info per step.

    Each operation is a dict with "action" key plus action-specific params.
    Allowed actions: power_on, power_off, reset, suspend, create_vm,
    delete_vm, reconfigure, create_snapshot, delete_snapshot,
    revert_snapshot, clone, migrate, deploy_ova, deploy_template,
    linked_clone, attach_iso, convert_to_template.

    Returns plan dict with plan_id, steps, summary (vms_affected,
    irreversible_steps, rollback_available). Show to user for confirmation
    before calling vm_apply_plan.

    Args:
        operations: List of operation dicts, each with "action" + params.
        target: Optional vCenter/ESXi target name from config.
    """
    si = _get_connection(target)
    # Guest steps carry a password; the plan file keeps it, the response does not.
    return redact_plan(create_plan(si, operations, target=target))


# destructiveHint: a plan can delete VMs, power them off, revert snapshots and
# run guest commands. The annotation said False because applying "only runs
# steps"; the steps are the destruction.
@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="medium")
@tool_errors("dict")
def vm_apply_plan(
    plan_id: str,
    confirm: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE] Execute a previously created plan step by step.

    Without confirm=True this only previews: it returns blast_radius listing
    every step (index, action, target object), the count and indices of the
    destructive ones (delete, power off, revert, guest commands...), and
    blockers — and runs nothing. Show that to the user and get their explicit
    decision. Do not set confirm=True on your own because the user asked
    earlier: they have not seen the preview yet.

    Each step is shown with its full parameters (passwords redacted). Each
    destructive step is measured as its own tool would measure it (vm_delete,
    vm_power_off, vm_revert_snapshot, vm_guest_exec, cluster_delete ...), and
    that tool's blockers refuse the plan. A step on something an earlier step
    creates or changes is marked check "deferred": it is measured immediately
    before it runs, and the plan stops there if it fails.

    Refused: a target other than the one the plan was created against (no
    target is a target of its own), a step its tool would refuse, anything it
    could not read, a delete_vm step without its acknowledge_blast_radius
    (from a vm_delete preview), and any iscsi_* or storage_rescan step — those
    are gated in vmware-storage; run storage_iscsi_* / storage_rescan there.

    With confirm=True steps run sequentially. On failure: stops immediately,
    keeps the plan file with per-step results, and returns rollback_available.
    On success: deletes the plan file. If a step fails and rollback_available
    is true, ask the user whether to rollback, then call vm_rollback_plan.

    Args:
        plan_id: The plan ID returned by vm_create_plan.
        confirm: False (default) returns the blast radius and changes nothing. True applies it.
        target: The vCenter/ESXi target the plan was created against.
    """
    radius = measure_apply(plan_id, target, lambda: _get_connection(target))
    if not confirm:
        return preview(radius)
    refuse_on(radius, "vm_apply_plan")
    si = _get_connection(target)
    result = apply_plan(si, plan_id, step_check=check_step)
    return {**redact_plan(result), **_failure_hint(result), "blast_radius": radius}


def _failure_hint(result: dict) -> dict:
    """What to tell the agent when a plan stopped part-way."""
    if result.get("status") != "failed":
        return {}
    refused = next((s for s in result.get("steps") or []
                    if s.get("refused_by_gate")), None)
    parts = []
    if refused is not None:
        parts.append(
            f"Step {refused.get('index')} was refused by its check just before it ran "
            f"(see its result); the steps before it ran."
        )
    if result.get("rollback_available"):
        parts.append(
            "Ask the user: 'Do you want to rollback the already-executed steps?' If "
            "yes, preview vm_rollback_plan and show it to them."
        )
    return {"hint": "Plan failed. " + " ".join(parts)} if parts else {}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="medium")
@tool_errors("dict")
def vm_rollback_plan(
    plan_id: str,
    confirm: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE] Rollback executed steps of a failed plan in reverse order.

    Without confirm=True this only previews: it returns blast_radius listing
    the rollback steps that would run (in order, with the VMs a rollback
    deletes) and those skipped as irreversible — and runs nothing. Show that
    to the user and get their explicit decision. Do not set confirm=True on
    your own because the user asked earlier: they have not seen the preview
    yet.

    Only call this after vm_apply_plan returns status='failed'; check
    vm_list_plans first for the plan_id. Irreversible steps (delete_vm,
    revert_snapshot, etc.) are skipped with a warning. Each destructive
    rollback step (power off, delete snapshot, cluster delete, host removal)
    is measured as its own tool measures it, in the preview and again just
    before it runs; a refused check stops the rollback there and the plan
    stays 'failed'. Refused on a target other than the one the plan was
    created against.

    Args:
        plan_id: The plan ID of the failed plan.
        confirm: False (default) returns the blast radius and changes nothing. True applies it.
        target: The vCenter/ESXi target the plan was created against.
    """
    radius = measure_rollback(plan_id, target, lambda: _get_connection(target))
    if not confirm:
        return preview(radius)
    refuse_on(radius, "vm_rollback_plan")
    si = _get_connection(target)
    result = rollback_plan(si, plan_id, step_check=check_step)
    return {**result, **_rollback_stop_hint(result), "blast_radius": radius}


def _rollback_stop_hint(result: dict) -> dict:
    """What to tell the agent when a rollback check stopped the rollback."""
    if result.get("stopped_at_step") is None:
        return {}
    return {"hint": (
        f"Rollback stopped at Step {result['stopped_at_step']}: rolling it back was "
        "refused (see its error). Steps listed before it "
        "were rolled back; none after it ran. The plan is still 'failed': resolve what "
        "the error names, then preview vm_rollback_plan again."
    )}


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@vmware_tool(risk_level="low")
@tool_errors("dict")
def vm_list_plans() -> dict:
    """[READ] List all pending/failed plans.

    Use this first to find a plan_id for vm_apply_plan or vm_rollback_plan.
    Returns the list envelope: 'items' holds plan summaries (plan_id,
    created_at, status, steps count, VMs affected), and 'returned'/'total'/
    'truncated' state whether the listing is complete. Every plan file is
    read, so truncated is always false. Listing never deletes: stale plans
    (>24h) are swept by vm_create_plan, not by this tool.
    """
    return list_plans()
