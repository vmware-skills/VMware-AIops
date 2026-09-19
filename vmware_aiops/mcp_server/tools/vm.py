"""VM lifecycle and snapshot tools: power, clone, migrate, delete, snapshots."""

from typing import Optional

from vmware_policy import paginated, vmware_tool

from vmware_aiops.mcp_server._shared import _get_connection, mcp, tool_errors
from vmware_aiops.ops.gate import preview, refuse_on
from vmware_aiops.ops.vm_delete_gate import (
    delete_vm_acknowledged,
    vm_delete_blast_radius,
)
from vmware_aiops.ops.vm_gate import (
    delete_snapshot_radius,
    did_not_act,
    migrate_radius,
    power_off_radius,
    read_power_state,
    revert_snapshot_radius,
)
from vmware_aiops.ops.vm_lifecycle import (
    clone_vm,
    create_snapshot,
    create_vm,
    delete_snapshot,
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


def _undo_power_off(params: dict, result: object) -> Optional[dict]:
    """Inverse of vm_power_off — only when the call actually powered the VM off.

    A preview, a no-op on an already-off VM and a shutdown that did not finish
    all return normally, and vmware-policy files an undo token for any normal
    return. "Power it back on" for a VM this call never turned off is worse
    than no token at all.
    """
    if not isinstance(result, dict) or result.get("action") != "powered_off":
        return None
    return {
        "tool": "vm_power_on",
        "params": {"vm_name": params.get("vm_name"), "target": params.get("target")},
        "skill": "aiops",
        "note": "Inverse of vm_power_off: power the VM back on.",
    }


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="medium", undo=_undo_power_off)
@tool_errors("dict")
def vm_power_off(
    vm_name: str,
    force: bool = False,
    confirm: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE] Power off a VM — graceful guest shutdown by default, hard power-off with force=True.

    Without confirm=True this only previews: it returns blast_radius (VM name and
    instance UUID, host, power state, VMware Tools status, and whether this is a
    guest shutdown or a hard power-off) and changes nothing. Show it to the user
    and get their decision. Do not set confirm=True on your own because the user
    asked earlier: they have not seen the preview yet.

    Graceful mode calls VMware Tools guest shutdown and waits up to 120s; if it
    does not finish, action is "still_running". Refused: a graceful shutdown when
    Tools is not running or the VM is suspended (preview force=True instead), and
    a VM whose identity or power state cannot be read. An already-off VM returns
    action "noop". Use vm_power_on to start a VM; vm_delete requires it off first.

    Args:
        vm_name: Exact VM name as shown in vCenter inventory (case-sensitive).
        force: False (default) = graceful guest shutdown via VMware Tools;
            True = immediate hard power-off (risks guest filesystem damage).
        confirm: False (default) returns the blast radius and changes nothing. True applies it.
        target: vCenter/ESXi target from config.yaml; omit for the default target.

    Returns:
        Dict with action (preview, noop, powered_off, still_running), blast_radius,
        and the executor's message under result.
    """
    si = _get_connection(target)
    vm, radius = power_off_radius(si, vm_name, force)
    if radius["noop"]:
        return {"action": "noop", "blast_radius": radius,
                "result": f"VM '{radius['vm']}' is already powered off; nothing changed."}
    if not confirm:
        return preview(radius)
    refuse_on(radius, "vm_power_off")
    message = power_off_vm(si, vm_name, force=force)
    if read_power_state(vm) == "poweredOff":
        return {"action": "powered_off", "result": message, "blast_radius": radius}
    return {
        "action": "still_running", "result": message, "blast_radius": radius,
        "hint": "The VM is still on. Check the guest, or preview force=True with the user.",
    }


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
@tool_errors("dict")
def vm_migrate(
    vm_name: str,
    to_host: str,
    to_datastore: Optional[str] = None,
    confirm: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE] Migrate (vMotion) a VM to another host, optionally with storage vMotion.

    Without confirm=True this only previews: it returns blast_radius (VM and
    instance UUID, source host and datastores, target host with its connection
    and maintenance state, target datastore, and live vMotion vs cold migration)
    and moves nothing. Show it to the user and get their decision. Do not set
    confirm=True on your own because the user asked earlier: they have not seen
    the preview yet.

    Refused: a target host that is not found, not connected, in maintenance mode
    or outside a cluster; a target datastore that is not found; a target host
    that does not mount the VM's datastores when to_datastore is omitted (vCenter
    rejects cross-host vMotion without shared storage — pass to_datastore); and
    anything above that cannot be read. The VM's current host with no
    to_datastore returns action "noop". Run cluster_info first for host names.

    Args:
        vm_name: VM to migrate.
        to_host: Target ESXi host name.
        to_datastore: Target datastore (required for cross-storage hosts).
        confirm: False (default) returns the blast radius and changes nothing. True applies it.
        target: vCenter/ESXi target name from config.

    Returns:
        Dict with action (preview, noop, migrated), blast_radius, and result.
    """
    si = _get_connection(target)
    radius = migrate_radius(si, vm_name, to_host, to_datastore)
    if radius["noop"]:
        return {"action": "noop", "blast_radius": radius,
                "result": f"VM '{radius['vm']}' is already on host '{to_host}'; nothing changed."}
    if not confirm:
        return preview(radius)
    refuse_on(radius, "vm_migrate")
    message = migrate_vm(si, vm_name, to_host, target_datastore=to_datastore)
    if " migrated from " not in message:
        raise did_not_act("vm_migrate", message)
    return {"action": "migrated", "result": message, "blast_radius": radius}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="critical")
@tool_errors("dict")
def vm_delete(
    vm_name: str,
    confirm: bool = False,
    acknowledge_blast_radius: Optional[dict] = None,
    target: Optional[str] = None,
) -> dict:
    """[WRITE] Delete a VM and its disks and snapshots (irreversible).

    Without confirm=True this only previews: it returns blast_radius (identity,
    host, disks, total size, snapshot count, blockers) and destroys nothing.
    Show that to the user and get their explicit decision. Do not set
    confirm=True on your own because the user said "delete" earlier: they
    have not seen what it destroys yet.

    To delete, call again with confirm=True and acknowledge_blast_radius set to
    the preview's acknowledge_with object, unchanged. The VM is re-measured
    first; if it changed (another snapshot, a different VM under the same
    name), nothing is deleted and you must preview again.

    Refused outright: a powered-on or suspended VM (power it off with vm_power_off first),
    a VM whose disks or identity cannot be read, and a name that matches more
    than one VM. Use vm_set_ttl instead when the VM should only expire later.

    Args:
        vm_name: Exact name of the VM to delete.
        confirm: False (default) previews; True deletes, with the acknowledgement.
        acknowledge_blast_radius: The preview's acknowledge_with object.
        target: vCenter/ESXi target name from config.
    """
    si = _get_connection(target)
    if not confirm:
        return {
            "action": "preview",
            "blast_radius": vm_delete_blast_radius(si, vm_name),
            "hint": "Nothing was deleted. Show blast_radius to the user; to delete, re-run "
                    "with confirm=True and acknowledge_blast_radius set to its acknowledge_with.",
        }
    radius = delete_vm_acknowledged(si, vm_name, acknowledge_blast_radius)
    return {"action": "deleted", "deleted": radius["vm"], "blast_radius": radius}


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
@tool_errors("dict")
def vm_revert_snapshot(
    vm_name: str,
    snapshot_name: str,
    confirm: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE] Revert a VM to a named snapshot (loses changes since snapshot).

    Without confirm=True this only previews: it returns blast_radius (VM and
    instance UUID, the snapshot's name, id and creation time, total snapshot
    count, current power state and the power state after the revert) and changes
    nothing. Show it to the user and get their decision. Do not set confirm=True
    on your own because the user asked earlier: they have not seen the preview yet.

    Irreversible — everything written since the snapshot is lost. Refused: a
    snapshot name that is not found, a name that matches more than one snapshot
    on the VM (vSphere allows duplicates; rename one first), and a VM whose
    snapshot tree, identity or power state cannot be read. Run vm_list_snapshots
    first for exact names. To reclaim space without changing state use
    vm_delete_snapshot.

    Args:
        vm_name: VM to revert.
        snapshot_name: Snapshot to revert to.
        confirm: False (default) returns the blast radius and changes nothing. True applies it.
        target: vCenter/ESXi target name from config.

    Returns:
        Dict with action (preview, reverted), blast_radius, and result.
    """
    si = _get_connection(target)
    radius = revert_snapshot_radius(si, vm_name, snapshot_name)
    if not confirm:
        return preview(radius)
    refuse_on(radius, "vm_revert_snapshot")
    message = revert_to_snapshot(si, vm_name, snapshot_name)
    # Success is recognised, not failure: the executor answers every refusal
    # (not found, ambiguous) with a sentence, and a new one must not read as done.
    if " reverted to snapshot " not in message:
        raise did_not_act("vm_revert_snapshot", message)
    return {"action": "reverted", "result": message, "blast_radius": radius}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="high")
@tool_errors("dict")
def vm_delete_snapshot(
    vm_name: str,
    snapshot_name: str,
    remove_children: bool = False,
    wait: bool = False,
    confirm: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE] Permanently delete a named snapshot, consolidating its delta disk into the parent.

    Without confirm=True this only previews: it returns blast_radius (VM and
    instance UUID, the snapshot's name, id and creation time, remove_children,
    how many child snapshots sit below it and how many snapshots would be
    removed) and deletes nothing. Show it to the user and get their decision. Do
    not set confirm=True on your own because the user asked earlier: they have
    not seen the preview yet.

    Frees disk space and does NOT change the VM's current state (unlike
    vm_revert_snapshot). Works while the VM is powered on. Refused: a snapshot
    name that is not found, a name that matches more than one snapshot on the VM
    (vSphere allows duplicates; rename one first), and a snapshot tree that cannot
    be read. Run vm_list_snapshots first for exact names.

    Consolidation is slow for old/large deltas (often minutes). By default (wait=False)
    this returns a task id immediately so it does not block your context — poll it with
    vm_task_status. Set wait=True only for small snapshots (blocks up to 30 min).

    Args:
        vm_name: Exact name of the VM owning the snapshot.
        snapshot_name: Exact snapshot name from vm_list_snapshots output.
        remove_children: False (default) = children are kept and consolidated;
            True = delete the entire snapshot subtree below this one as well.
        wait: False (default) = async, return task id at once; True = block.
        confirm: False (default) returns the blast radius and changes nothing. True applies it.
        target: vCenter/ESXi target from config.yaml; omit for the default target.

    Returns:
        Dict with action (preview, snapshot_delete_started, snapshot_deleted),
        blast_radius, and result (carries the task id to poll via vm_task_status).
    """
    si = _get_connection(target)
    radius = delete_snapshot_radius(si, vm_name, snapshot_name, remove_children)
    if not confirm:
        return preview(radius)
    refuse_on(radius, "vm_delete_snapshot")
    message = delete_snapshot(
        si, vm_name, snapshot_name, remove_children=remove_children, wait=wait
    )
    if "deleted from VM" in message:
        action = "snapshot_deleted"
    elif "Snapshot delete started" in message or "is still running" in message:
        action = "snapshot_delete_started"
    else:
        raise did_not_act("vm_delete_snapshot", message)
    return {"action": action, "result": message, "blast_radius": radius}


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
