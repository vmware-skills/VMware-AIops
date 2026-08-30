"""VM lifecycle and snapshot tools: power, clone, migrate, delete, snapshots."""

from typing import Optional

from vmware_policy import paginated, vmware_tool

from vmware_aiops.mcp_server._shared import _get_connection, mcp, tool_errors
from vmware_aiops.ops.vm_lifecycle import (
    clone_vm,
    create_snapshot,
    create_vm,
    delete_snapshot,
    delete_vm,
    get_task_status,
    list_snapshots,
    migrate_vm,
    power_off_vm,
    power_on_vm,
    reconfigure_vm,
    revert_to_snapshot,
)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@vmware_tool(
    risk_level="medium",
    undo=lambda params, result: {
        "tool": "vm_power_off",
        "params": {"vm_name": params.get("vm_name"), "target": params.get("target")},
        "skill": "aiops",
        "note": "Inverse of vm_power_on: power the VM back off.",
    },
)
@tool_errors("str")
def vm_power_on(vm_name: str, target: Optional[str] = None) -> str:
    """[WRITE] Power on a virtual machine.

    Returns a status string; an already-on VM is a no-op. Reverse with
    vm_power_off. Call this first when a VM is off: guest tools such as
    vm_guest_exec only work once VMware Tools has finished booting.

    Args:
        vm_name: Exact name of the virtual machine.
        target: Optional vCenter/ESXi target name from config. Uses default if omitted.
    """
    si = _get_connection(target)
    return power_on_vm(si, vm_name)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(
    risk_level="medium",
    undo=lambda params, result: {
        "tool": "vm_power_on",
        "params": {"vm_name": params.get("vm_name"), "target": params.get("target")},
        "skill": "aiops",
        "note": "Inverse of vm_power_off: power the VM back on.",
    },
)
@tool_errors("str")
def vm_power_off(
    vm_name: str,
    force: bool = False,
    target: Optional[str] = None,
) -> str:
    """[WRITE] Power off a VM — graceful guest shutdown by default, hard power-off with force=True.

    Graceful mode calls VMware Tools guest shutdown and waits up to 120s; if Tools is
    not running or shutdown stalls, the response tells you to retry with force=True.
    An already-off VM returns success without change. Use vm_power_on to start a VM;
    vm_delete requires the VM to be off first.

    Args:
        vm_name: Exact VM name as shown in vCenter inventory (case-sensitive).
        force: False (default) = graceful guest shutdown via VMware Tools;
            True = immediate hard power-off (risks guest filesystem damage).
        target: vCenter/ESXi target from config.yaml; omit for the default target.

    Returns:
        Status string: shut down, force powered off, already off, or a Tools hint.
    """
    si = _get_connection(target)
    return power_off_vm(si, vm_name, force=force)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(
    risk_level="medium",
    undo=lambda params, result: {
        "tool": "vm_delete",
        "params": {"vm_name": params.get("vm_name"), "target": params.get("target")},
        "skill": "aiops",
        "note": "Inverse of vm_create: delete the VM just created (it is powered off).",
    },
)
@tool_errors("str")
def vm_create(
    vm_name: str,
    cpu: int = 2,
    memory_mb: int = 4096,
    disk_gb: int = 40,
    network_name: str = "VM Network",
    datastore_name: Optional[str] = None,
    folder_path: Optional[str] = None,
    target: Optional[str] = None,
) -> str:
    """[WRITE] Create a new empty VM with the given hardware sizing.

    Creates a powered-off VM with one disk and one NIC. To populate it, attach an
    ISO (attach_iso_to_vm) and power it on, or use deploy_vm_from_ova or vm_clone
    for a ready-to-run guest. Fails before creating anything if the datastore is
    not found. Returns a status string with the new VM name.

    Args:
        vm_name: Name for the new VM; must not already exist.
        cpu: vCPU count (default 2).
        memory_mb: Memory in MB (default 4096).
        disk_gb: Primary disk size in GB (default 40).
        network_name: Port group for the NIC (default "VM Network").
        datastore_name: Target datastore; omit for the first accessible one.
        folder_path: vCenter folder path; omit for the datacenter root.
        target: vCenter/ESXi target from config.yaml; omit for the default.
    """
    si = _get_connection(target)
    return create_vm(
        si, vm_name=vm_name, cpu=cpu, memory_mb=memory_mb,
        disk_gb=disk_gb, network_name=network_name,
        datastore_name=datastore_name, folder_path=folder_path,
    )


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="medium")
@tool_errors("str")
def vm_reconfigure(
    vm_name: str,
    cpu: Optional[int] = None,
    memory_mb: Optional[int] = None,
    target: Optional[str] = None,
) -> str:
    """[WRITE] Change a VM's vCPU count and/or memory.

    Pass only the fields you want to change; omitted fields are left untouched.
    Hot-add of CPU/memory requires it to be enabled on the VM and a running guest;
    otherwise power the VM off first (vm_power_off).

    Args:
        vm_name: Exact name of the VM to reconfigure.
        cpu: New vCPU count; omit to leave unchanged.
        memory_mb: New memory in MB; omit to leave unchanged.
        target: vCenter/ESXi target name from config.yaml; omit to use the default target.

    Returns:
        Status string describing the applied change, or a VM-not-found error.
    """
    si = _get_connection(target)
    return reconfigure_vm(si, vm_name, cpu=cpu, memory_mb=memory_mb)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(
    risk_level="high",
    undo=lambda params, result: {
        "tool": "vm_delete",
        "params": {"vm_name": params.get("new_name"), "target": params.get("target")},
        "skill": "aiops",
        "note": "Inverse of vm_clone: delete the clone (power it off first if running).",
    },
)
@tool_errors("str")
def vm_clone(
    vm_name: str,
    new_name: str,
    to_host: Optional[str] = None,
    to_datastore: Optional[str] = None,
    power_on: bool = False,
    target: Optional[str] = None,
) -> str:
    """[WRITE] Clone a VM. Without to_host/to_datastore the clone lands on the source's host+datastore.

    Returns a status string naming the clone. Full independent copy — slow and
    full disk cost; prefer deploy_linked_clone for near-instant test copies and
    batch_clone_vms for many at once. Cloning a running VM may capture a
    crash-consistent disk.

    Args:
        vm_name: Source VM (or template) name.
        new_name: Name for the new clone.
        to_host: Target ESXi host name (default: source's host).
        to_datastore: Target datastore name (default: source's datastore).
        power_on: Power on the clone after creation.
        target: vCenter/ESXi target name from config.
    """
    si = _get_connection(target)
    return clone_vm(
        si, vm_name, new_name,
        target_host=to_host,
        target_datastore=to_datastore,
        power_on=power_on,
    )


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="high")
@tool_errors("str")
def vm_migrate(
    vm_name: str,
    to_host: str,
    to_datastore: Optional[str] = None,
    target: Optional[str] = None,
) -> str:
    """[WRITE] Migrate (vMotion) a VM to another host, optionally with storage vMotion.

    Returns a status string. If the target host has no access to the VM's current
    datastore, you MUST pass to_datastore — vCenter rejects cross-host vMotion
    without shared storage. Run cluster_info first for valid destination host names.

    Args:
        vm_name: VM to migrate.
        to_host: Target ESXi host name.
        to_datastore: Target datastore (required for cross-storage hosts).
        target: vCenter/ESXi target name from config.
    """
    si = _get_connection(target)
    return migrate_vm(si, vm_name, to_host, target_datastore=to_datastore)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="critical")
@tool_errors("str")
def vm_delete(vm_name: str, target: Optional[str] = None) -> str:
    """[WRITE] Delete a VM (irreversible). VM must be powered off.

    Returns a status string. Power it off first with vm_power_off. This destroys
    the VM's disks, so confirm with the user before calling; use vm_set_ttl
    instead when you only want the VM to expire later.

    Args:
        vm_name: VM to delete. Must be powered off.
        target: vCenter/ESXi target name from config.
    """
    si = _get_connection(target)
    return delete_vm(si, vm_name)


# idempotentHint: false. CreateSnapshot_Task is called unconditionally and
# vSphere allows siblings with the same name, so a second call leaves a second
# snapshot. The field is what a client reads before retrying, and this family
# retries transient failures once — a timeout on a snapshot that had in fact
# succeeded would be retried into a second delta-disk chain.
@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(
    risk_level="medium",
    undo=lambda params, result: {
        "tool": "vm_delete_snapshot",
        "params": {
            "vm_name": params.get("vm_name"),
            "snapshot_name": params.get("snapshot_name"),
            "target": params.get("target"),
        },
        "skill": "aiops",
        "note": "Inverse of vm_create_snapshot: delete the snapshot just created.",
    },
)
@tool_errors("str")
def vm_create_snapshot(
    vm_name: str,
    snapshot_name: str,
    description: str = "",
    memory: bool = False,
    quiesce: bool = False,
    target: Optional[str] = None,
) -> str:
    """[WRITE] Create a snapshot of a VM.

    Returns a status string. Use this before a risky change so vm_revert_snapshot
    can undo it, then reclaim the space with vm_delete_snapshot — snapshots left
    for days grow delta disks and must not be treated as backups.

    Args:
        vm_name: VM to snapshot.
        snapshot_name: Snapshot name.
        description: Optional description.
        memory: Include memory state (heavier, allows resume).
        quiesce: Quiesce guest filesystem (requires running VMware Tools).
        target: vCenter/ESXi target name from config.
    """
    si = _get_connection(target)
    return create_snapshot(
        si, vm_name, snapshot_name,
        description=description, memory=memory, quiesce=quiesce,
    )


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="high")
@tool_errors("str")
def vm_revert_snapshot(
    vm_name: str,
    snapshot_name: str,
    target: Optional[str] = None,
) -> str:
    """[WRITE] Revert a VM to a named snapshot (loses changes since snapshot).

    Returns a status string. Run vm_list_snapshots first for exact names.
    Irreversible — everything written since the snapshot is lost, so confirm with
    the user. To reclaim space without changing state use vm_delete_snapshot.

    Args:
        vm_name: VM to revert.
        snapshot_name: Snapshot to revert to.
        target: vCenter/ESXi target name from config.
    """
    si = _get_connection(target)
    return revert_to_snapshot(si, vm_name, snapshot_name)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="high")
@tool_errors("str")
def vm_delete_snapshot(
    vm_name: str,
    snapshot_name: str,
    remove_children: bool = False,
    wait: bool = False,
    target: Optional[str] = None,
) -> str:
    """[WRITE] Permanently delete a named snapshot, consolidating its delta disk into the parent.

    Frees disk space and does NOT change the VM's current state (unlike
    vm_revert_snapshot, which discards changes since the snapshot). Works while the VM
    is powered on. Run vm_list_snapshots first for exact names. Irreversible: confirm
    with the user before calling.

    Consolidation is slow for old/large deltas (often minutes). By default (wait=False)
    this returns a task id immediately so it does not block your context — poll it with
    vm_task_status. Set wait=True only for small snapshots (blocks up to 30 min).

    Args:
        vm_name: Exact name of the VM owning the snapshot.
        snapshot_name: Exact snapshot name from vm_list_snapshots output.
        remove_children: False (default) = children are kept and consolidated;
            True = delete the entire snapshot subtree below this one as well.
        wait: False (default) = async, return task id at once; True = block.
        target: vCenter/ESXi target from config.yaml; omit for the default target.

    Returns:
        Status string with a task id (poll via vm_task_status), or a not-found message.
    """
    si = _get_connection(target)
    return delete_snapshot(
        si, vm_name, snapshot_name, remove_children=remove_children, wait=wait
    )


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@vmware_tool(risk_level="low")
@tool_errors("dict")
def vm_task_status(task_id: str, target: Optional[str] = None) -> dict:
    """[READ] Poll a long-running vSphere task by its id (from an async vm_delete_snapshot).

    Use after vm_delete_snapshot returns a task id, instead of re-running the delete.
    Returns state (queued/running/success/error/gone), progress percent, and the entity
    name. 'gone' means vCenter already garbage-collected a completed task — re-list the
    resource to confirm the final state. A failed task reports its fault under
    'task_error'; a top-level 'error' key would mean this poll itself failed.

    Args:
        task_id: The task id string returned by an async write operation.
        target: vCenter/ESXi target name from config.yaml; omit to use the default target.

    Returns:
        Dict with task_id, state, progress_pct, operation, entity, and task_error/note
        when relevant.
    """
    si = _get_connection(target)
    return get_task_status(si, task_id)


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@vmware_tool(risk_level="low")
@tool_errors("dict")
def vm_list_snapshots(vm_name: str, target: Optional[str] = None) -> dict:
    """[READ] List the full snapshot tree of a VM, including nested child snapshots.

    Read-only, no side effects. Call this before vm_revert_snapshot, vm_delete_snapshot,
    or deploy_linked_clone to get exact snapshot names. 'items' is empty when the
    VM has no snapshots.

    Args:
        vm_name: Exact VM name as shown in vCenter inventory.
        target: vCenter/ESXi target name from config.yaml; omit to use the default target.

    Returns:
        The list envelope. 'items' is one dict per snapshot: name, description,
        created, state (power state at snapshot time), level (0 = root). The whole
        tree is walked, so 'total' is the real count and 'truncated' is always false.
    """
    si = _get_connection(target)
    snaps = list_snapshots(si, vm_name)
    rows = [
        {k: v for k, v in s.items() if k != "snapshot_ref"}
        for s in snaps
    ]
    return paginated(rows, total=len(rows))
