"""TTL and Clean Slate tools: auto-delete scheduling, baseline reset."""

from typing import Optional

from vmware_policy import vmware_tool

from vmware_aiops.mcp_server._shared import _get_connection, mcp, tool_errors
from vmware_aiops.ops.gate import preview, refuse_on


# destructiveHint: the deletion is deferred, not absent — the daemon carries it
# out later, unattended. Issue #25 already settled this for the CLI (double
# confirmation + --dry-run) and SKILL.md lists it among the destructive
# operations; the annotation was the last place still saying otherwise.
def _ttl_undo(params: dict, result: object) -> Optional[dict]:
    """The inverse of a TTL that was actually scheduled; nothing for a preview."""
    if not (isinstance(result, dict) and result.get("action") == "scheduled"):
        return None
    return {
        "tool": "vm_cancel_ttl",
        "params": {"vm_name": params.get("vm_name"), "target": params.get("target")},
        "skill": "aiops",
        "note": "Inverse of vm_set_ttl: cancel the scheduled auto-delete.",
    }


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="medium", undo=_ttl_undo)
@tool_errors("dict")
def vm_set_ttl(
    vm_name: str,
    minutes: int,
    confirm: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE] Set a Time-To-Live (TTL) for a VM. The daemon auto-deletes it when expired.

    Without confirm=True this only previews: it returns blast_radius — the VM
    that will be deleted (identity, host, disks, total size, snapshot count),
    when (expires_at), and any TTL it replaces — and schedules nothing. Show
    that to the user and get their explicit decision. Do not set confirm=True
    on your own because the user asked earlier: they have not seen the
    preview yet. Refused when the VM's identity or disks cannot be read.

    Use this for short-lived lab VMs so cleanup is not forgotten; cancel with
    vm_cancel_ttl and review pending expiries with vm_list_ttl. The scheduler
    daemon must be running (`vmware-aiops daemon start`) or nothing is ever
    deleted; it powers a running VM off first. TTLs persist in
    ~/.vmware-aiops/ttl.json. Returns a dict (action, blast_radius).

    Args:
        vm_name: Name of the VM to auto-delete.
        minutes: Minutes until deletion (minimum 1).
        confirm: False (default) returns the blast radius and changes nothing. True applies it.
        target: Optional vCenter/ESXi target name from config.
    """
    from vmware_aiops.ops.ttl import measure_ttl
    from vmware_aiops.ops.ttl import set_ttl as _set_ttl
    si = _get_connection(target)
    radius = measure_ttl(si, vm_name, minutes)
    if not confirm:
        return preview(radius)
    refuse_on(radius, "vm_set_ttl")
    result = _set_ttl(vm_name, minutes, target=target,
                      instance_uuid=radius["instance_uuid"])
    return {"action": "scheduled", "result": result, "blast_radius": radius}


# destructiveHint is False here and True on vm_set_ttl directly above, and the
# difference is the whole point: set_ttl schedules an unattended deletion,
# cancel_ttl calls it off. This one was marked destructive too — copied from its
# neighbour — which made the family's "destructive tools double-confirm in the
# CLI" gate demand two prompts before *preventing* a deletion.
@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@vmware_tool(risk_level="medium")
@tool_errors("str")
def vm_cancel_ttl(vm_name: str) -> str:
    """[WRITE] Cancel an existing TTL for a VM (prevents auto-deletion).

    Returns a status string. Use vm_list_ttl first for the exact vm_name.
    This only removes the schedule and never touches the VM itself.

    Args:
        vm_name: Name of the VM whose TTL should be cancelled.
    """
    from vmware_aiops.ops.ttl import cancel_ttl as _cancel_ttl
    return _cancel_ttl(vm_name)


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@vmware_tool(risk_level="low")
@tool_errors("dict")
def vm_list_ttl() -> dict:
    """[READ] List all VMs with TTLs registered, including expiry time and status.

    Use this first to find the exact vm_name for vm_cancel_ttl.
    Returns the list envelope: 'items' holds TTL entries with
    remaining_minutes and expired flag, and 'returned'/'total'/'truncated'
    state whether the listing is complete. The whole TTL store is read, so
    truncated is always false.
    """
    from vmware_aiops.ops.ttl import list_ttl as _list_ttl
    return _list_ttl()


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="high")
@tool_errors("dict")
def vm_clean_slate(
    vm_name: str,
    snapshot_name: str = "baseline",
    confirm: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE] Revert a VM to its baseline snapshot (Clean Slate).

    Without confirm=True this only previews: it returns blast_radius (VM
    identity, power state and whether it is powered off first, the snapshot
    and when it was taken, snapshot count, blockers) and changes nothing.
    Show that to the user and get their explicit decision. Do not set
    confirm=True on your own because the user asked earlier: they have not
    seen the preview yet.

    With confirm=True: powers off the VM first if it is running, then reverts
    to the named snapshot. Use this to reset a lab/dev VM to a clean starting
    state after a task completes. Irreversible — everything written since the
    snapshot is lost. Refused: no snapshot of that name, more than one, or a
    VM whose state cannot be read. Returns a dict (action, blast_radius).

    Args:
        vm_name: Name of the VM to revert.
        snapshot_name: Snapshot name to revert to (default: "baseline").
        confirm: False (default) returns the blast radius and changes nothing. True applies it.
        target: Optional vCenter/ESXi target name from config.
    """
    from vmware_aiops.ops.ttl import measure_clean_slate
    from vmware_aiops.ops.vm_gate import did_not_act
    from vmware_aiops.ops.vm_lifecycle import clean_slate
    si = _get_connection(target)
    radius = measure_clean_slate(si, vm_name, snapshot_name)
    if not confirm:
        return preview(radius)
    refuse_on(radius, "vm_clean_slate")
    result = clean_slate(si, vm_name, snapshot_name=snapshot_name)
    if " reverted to snapshot " not in result:
        raise did_not_act("vm_clean_slate", result)
    return {"action": "reverted", "result": result, "blast_radius": radius}
