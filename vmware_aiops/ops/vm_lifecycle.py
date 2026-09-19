"""VM lifecycle operations: create, delete, power, snapshot, clone, migrate."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from pyVmomi import vim

from vmware_policy import sanitize

from vmware_aiops.ops.inventory import (
    InventoryError,
    find_compute_resource,
    find_datastore_by_name,
    find_host_by_name,
    find_vm_by_name,
    resolve_datacenter,
)

if TYPE_CHECKING:
    from pyVmomi.vim import ServiceInstance


class VMNotFoundError(Exception):
    """Raised when a VM is not found by name."""


class TaskFailedError(Exception):
    """Raised when a vSphere task fails."""


class TaskStillRunning(Exception):
    """Raised when a task outlives its wait budget but is still progressing.

    Carries the task moref id so callers can keep polling instead of treating
    the timeout as a failure (the vCenter task is NOT cancelled).
    """

    def __init__(self, task_id: str, timeout: int) -> None:
        self.task_id = task_id
        self.timeout = timeout
        super().__init__(
            f"Task still running after {timeout}s — this is NOT a failure. "
            f"The vCenter task is still progressing (large snapshot consolidation "
            f"or clone can take many minutes). Task id: {task_id}. "
            f"Poll with: vm task-status {task_id} --target <target>"
        )


def _task_moid(task) -> str:
    """Return the managed-object id of a task for later reconnection."""
    return task._moId


def _wait_for_task(task, timeout: int = 300) -> object:
    """Wait for a vSphere task to complete.

    Raises TaskStillRunning (not TimeoutError) when the budget is exceeded so
    callers can surface the task id and keep polling — a slow snapshot
    consolidation is not a failure.
    """
    start = time.time()
    while task.info.state in (vim.TaskInfo.State.running, vim.TaskInfo.State.queued):
        if time.time() - start > timeout:
            raise TaskStillRunning(_task_moid(task), timeout)
        time.sleep(2)

    if task.info.state == vim.TaskInfo.State.success:
        return task.info.result

    err = task.info.error
    if err is None:
        raise TaskFailedError(
            f"Task {_task_moid(task)} failed but vCenter attached no fault detail. "
            f"Run vm_task_status with task_id='{_task_moid(task)}' for its final state, "
            f"and check get_events (vmware-monitor skill) around now for the underlying "
            f"cause before retrying."
        )

    parts: list[str] = []
    primary = getattr(err, "msg", None) or type(err).__name__
    parts.append(str(primary))
    fault_type = type(err).__name__
    if fault_type and fault_type not in primary:
        parts.append(f"fault={fault_type}")
    cause = getattr(err, "faultCause", None)
    if cause is not None and getattr(cause, "msg", None):
        parts.append(f"caused_by={cause.msg}")
    fault_msgs = getattr(err, "faultMessage", None) or []
    for fm in fault_msgs:
        m = getattr(fm, "message", None)
        if m:
            parts.append(f"detail={m}")

    raise TaskFailedError("Task failed: " + " | ".join(parts))


def _require_vm(si: ServiceInstance, vm_name: str) -> vim.VirtualMachine:
    """Find a VM or raise VMNotFoundError."""
    vm = find_vm_by_name(si, vm_name)
    if vm is None:
        raise VMNotFoundError(
            f"VM '{vm_name}' not found. Run list_virtual_machines (vmware-monitor skill, "
            f"filter by name e.g. '{vm_name[:3]}*') to see available VMs and copy an "
            f"exact name — vSphere VM names are case-sensitive."
        )
    return vm


# ─── Info ─────────────────────────────────────────────────────────────────────


def get_vm_info(si: ServiceInstance, vm_name: str) -> dict:
    """Get detailed VM information."""
    vm = _require_vm(si, vm_name)
    config = vm.config
    guest = vm.guest
    runtime = vm.runtime

    disks = []
    nics = []
    if config and config.hardware:
        for dev in config.hardware.device:
            if isinstance(dev, vim.vm.device.VirtualDisk):
                disks.append({
                    "label": sanitize(dev.deviceInfo.label),
                    "size_gb": round(dev.capacityInKB / (1024 * 1024), 1),
                    "thin": getattr(dev.backing, "thinProvisioned", None),
                })
            elif isinstance(dev, vim.vm.device.VirtualEthernetCard):
                nics.append({
                    "label": sanitize(dev.deviceInfo.label),
                    "mac": dev.macAddress,
                    "connected": dev.connectable.connected if dev.connectable else False,
                    "network": sanitize(dev.backing.deviceName)
                    if hasattr(dev.backing, "deviceName")
                    else sanitize(str(dev.backing)),
                })

    return {
        "name": sanitize(vm.name),
        "power_state": str(runtime.powerState),
        "cpu": config.hardware.numCPU if config else 0,
        "memory_mb": config.hardware.memoryMB if config else 0,
        "guest_os": sanitize(config.guestFullName) if config else "N/A",
        "guest_id": config.guestId if config else "N/A",
        "uuid": config.uuid if config else "N/A",
        "instance_uuid": config.instanceUuid if config else "N/A",
        "host": sanitize(runtime.host.name) if runtime.host else "N/A",
        "ip_address": guest.ipAddress if guest else None,
        "hostname": sanitize(guest.hostName) if guest and guest.hostName else None,
        "tools_status": str(guest.toolsRunningStatus) if guest else "N/A",
        "tools_version": str(guest.toolsVersion) if guest and guest.toolsVersion else "N/A",
        "disks": disks,
        "nics": nics,
        "annotation": sanitize(config.annotation, max_len=1000) if config and config.annotation else "",
        "snapshot_count": _count_snapshots(vm.snapshot) if vm.snapshot else 0,
    }


def _count_snapshots(snapshot_info) -> int:
    """Count total snapshots recursively."""
    count = 0
    if snapshot_info and snapshot_info.rootSnapshotList:
        for snap in snapshot_info.rootSnapshotList:
            count += 1 + _count_children(snap)
    return count


def _count_children(snap_tree) -> int:
    count = 0
    for child in (snap_tree.childSnapshotList or []):
        count += 1 + _count_children(child)
    return count


# ─── Power Operations ────────────────────────────────────────────────────────


def power_on_vm(si: ServiceInstance, vm_name: str) -> str:
    """Power on a VM."""
    vm = _require_vm(si, vm_name)
    if vm.runtime.powerState == vim.VirtualMachine.PowerState.poweredOn:
        return f"VM '{vm_name}' is already powered on."
    task = vm.PowerOn()
    _wait_for_task(task)
    return f"VM '{vm_name}' powered on successfully."


def power_off_vm(si: ServiceInstance, vm_name: str, force: bool = False) -> str:
    """Power off a VM. Graceful (guest shutdown) by default, force if specified."""
    vm = _require_vm(si, vm_name)
    if vm.runtime.powerState == vim.VirtualMachine.PowerState.poweredOff:
        return f"VM '{vm_name}' is already powered off."

    if force:
        task = vm.PowerOff()
        _wait_for_task(task)
        return f"VM '{vm_name}' force powered off."

    # Graceful shutdown via VMware Tools
    try:
        vm.ShutdownGuest()
        # Wait for power off (no task returned for ShutdownGuest)
        for _ in range(60):
            time.sleep(2)
            if vm.runtime.powerState == vim.VirtualMachine.PowerState.poweredOff:
                return f"VM '{vm_name}' gracefully shut down."
        return (
            f"VM '{vm_name}' shutdown initiated but still running "
            f"after 120s. Use --force if needed."
        )
    except vim.fault.ToolsUnavailable:
        return (
            f"VMware Tools not running on '{vm_name}'. "
            f"Use --force for hard power off."
        )


def reset_vm(si: ServiceInstance, vm_name: str) -> str:
    """Reset (hard reboot) a VM."""
    vm = _require_vm(si, vm_name)
    task = vm.Reset()
    _wait_for_task(task)
    return f"VM '{vm_name}' reset successfully."


def suspend_vm(si: ServiceInstance, vm_name: str) -> str:
    """Suspend a VM."""
    vm = _require_vm(si, vm_name)
    task = vm.Suspend()
    _wait_for_task(task)
    return f"VM '{vm_name}' suspended successfully."


# ─── Create / Delete ─────────────────────────────────────────────────────────


def create_vm(
    si: ServiceInstance,
    vm_name: str,
    cpu: int = 2,
    memory_mb: int = 4096,
    disk_gb: int = 40,
    network_name: str = "VM Network",
    datastore_name: str | None = None,
    folder_path: str | None = None,
    guest_id: str = "otherGuest64",
    datacenter_name: str | None = None,
    cluster: str | None = None,
) -> str:
    """Create a new VM with basic configuration."""
    # Find datacenter and folder
    try:
        datacenter = resolve_datacenter(si, datacenter_name)
        compute_resource = find_compute_resource(datacenter, cluster)
    except InventoryError as e:
        return str(e)
    vm_folder = datacenter.vmFolder
    if folder_path:
        for part in folder_path.split("/"):
            found = False
            for child in vm_folder.childEntity:
                if hasattr(child, "childEntity") and child.name == part:
                    vm_folder = child
                    found = True
                    break
            if not found:
                return f"Folder '{folder_path}' not found."

    # Find resource pool
    resource_pool = compute_resource.resourcePool

    # Find datastore
    if datastore_name:
        ds = find_datastore_by_name(si, datastore_name)
        if ds is None:
            return f"Datastore '{datastore_name}' not found."
        ds_path = f"[{datastore_name}] {vm_name}"
    else:
        ds_path = f"{vm_name}"

    # VM config spec
    vmx_file = vim.vm.FileInfo(vmPathName=ds_path)

    # SCSI controller
    scsi_spec = vim.vm.device.VirtualDeviceSpec(
        operation=vim.vm.device.VirtualDeviceSpec.Operation.add,
        device=vim.vm.device.ParaVirtualSCSIController(
            key=1000,
            sharedBus=vim.vm.device.VirtualSCSIController.Sharing.noSharing,
        ),
    )

    # Disk
    disk_spec = vim.vm.device.VirtualDeviceSpec(
        fileOperation=vim.vm.device.VirtualDeviceSpec.FileOperation.create,
        operation=vim.vm.device.VirtualDeviceSpec.Operation.add,
        device=vim.vm.device.VirtualDisk(
            backing=vim.vm.device.VirtualDisk.FlatVer2BackingInfo(
                diskMode="persistent",
                thinProvisioned=True,
            ),
            capacityInKB=disk_gb * 1024 * 1024,
            controllerKey=1000,
            unitNumber=0,
        ),
    )

    # NIC
    nic_spec = vim.vm.device.VirtualDeviceSpec(
        operation=vim.vm.device.VirtualDeviceSpec.Operation.add,
        device=vim.vm.device.VirtualVmxnet3(
            backing=vim.vm.device.VirtualEthernetCard.NetworkBackingInfo(
                useAutoDetect=False,
                deviceName=network_name,
            ),
            connectable=vim.vm.device.VirtualDevice.ConnectInfo(
                startConnected=True,
                allowGuestControl=True,
                connected=True,
            ),
            addressType="assigned",
        ),
    )

    config_spec = vim.vm.ConfigSpec(
        name=vm_name,
        memoryMB=memory_mb,
        numCPUs=cpu,
        files=vmx_file,
        guestId=guest_id,
        deviceChange=[scsi_spec, disk_spec, nic_spec],
    )

    task = vm_folder.CreateVM_Task(config=config_spec, pool=resource_pool)
    _wait_for_task(task)
    return (
        f"VM '{vm_name}' created successfully "
        f"(CPU: {cpu}, Mem: {memory_mb}MB, Disk: {disk_gb}GB)."
    )


def delete_vm(si: ServiceInstance, vm_name: str) -> str:
    """Delete a VM. Powers off first if running."""
    vm = _require_vm(si, vm_name)

    if vm.runtime.powerState == vim.VirtualMachine.PowerState.poweredOn:
        task = vm.PowerOff()
        _wait_for_task(task)

    task = vm.Destroy_Task()
    _wait_for_task(task)
    return f"VM '{vm_name}' deleted successfully."


# ─── Reconfigure ──────────────────────────────────────────────────────────────


def reconfigure_vm(
    si: ServiceInstance,
    vm_name: str,
    cpu: int | None = None,
    memory_mb: int | None = None,
) -> str:
    """Reconfigure VM CPU and/or memory. VM should be powered off for memory changes."""
    vm = _require_vm(si, vm_name)

    if cpu is None and memory_mb is None:
        return "Nothing to change. Specify --cpu and/or --memory."

    spec = vim.vm.ConfigSpec()
    changes = []
    if cpu is not None:
        spec.numCPUs = cpu
        changes.append(f"CPU: {cpu}")
    if memory_mb is not None:
        spec.memoryMB = memory_mb
        changes.append(f"Memory: {memory_mb}MB")

    task = vm.ReconfigVM_Task(spec=spec)
    _wait_for_task(task)
    return f"VM '{vm_name}' reconfigured: {', '.join(changes)}."


# ─── Snapshots ────────────────────────────────────────────────────────────────


def create_snapshot(
    si: ServiceInstance,
    vm_name: str,
    snap_name: str,
    description: str = "",
    memory: bool = True,
    quiesce: bool = False,
) -> str:
    """Create a VM snapshot.

    quiesce requires running VMware Tools — leave it False for freshly
    deployed VMs (the default) to avoid ApplicationQuiesceFault.
    """
    vm = _require_vm(si, vm_name)
    task = vm.CreateSnapshot_Task(
        name=snap_name,
        description=description,
        memory=memory,
        quiesce=quiesce,
    )
    _wait_for_task(task)
    return f"Snapshot '{snap_name}' created for VM '{vm_name}'."


def list_snapshots(si: ServiceInstance, vm_name: str) -> list[dict]:
    """List all snapshots for a VM."""
    vm = _require_vm(si, vm_name)
    if not vm.snapshot:
        return []

    results: list[dict] = []

    def _walk(snap_list, level: int = 0) -> None:
        for snap in snap_list:
            results.append({
                "name": sanitize(snap.name),
                "description": sanitize(snap.description, max_len=1000),
                "created": str(snap.createTime),
                "state": str(snap.state),
                "level": level,
                "snapshot_ref": snap.snapshot,
            })
            if snap.childSnapshotList:
                _walk(snap.childSnapshotList, level + 1)

    _walk(vm.snapshot.rootSnapshotList)
    return results


def snapshot_nodes(vm: vim.VirtualMachine) -> list:
    """Every snapshot tree node of ``vm``, depth first; [] when it has none."""
    found: list = []

    def _walk(snap_list) -> None:
        for snap in snap_list or []:
            found.append(snap)
            _walk(snap.childSnapshotList)

    if vm.snapshot:
        _walk(vm.snapshot.rootSnapshotList)
    return found


def matching_snapshots(nodes: list, snap_name: str) -> list:
    """The nodes ``snap_name`` could mean.

    ``list_snapshots`` shows ``sanitize(name)``, so a caller who copies a name
    from it may pass the sanitized form of a raw name that carries control or
    invisible characters. A node matches on its raw name or its shown name; two
    nodes that match the same string — duplicates, or names that differ only by
    stripped characters — are ambiguous, and every executor and the MCP gate
    refuse them rather than pick one.
    """
    return [n for n in nodes if n.name == snap_name or sanitize(n.name) == snap_name]


def resolve_snapshot(vm: vim.VirtualMachine, snap_name: str) -> tuple[object | None, str | None]:
    """The one snapshot node ``snap_name`` means, or why there is not exactly one."""
    nodes = snapshot_nodes(vm)
    matches = matching_snapshots(nodes, snap_name)
    if len(matches) == 1:
        return matches[0], None
    shown = sanitize(snap_name, 200)
    if not matches:
        available = ", ".join(sanitize(n.name) for n in nodes) or "none"
        return None, f"Snapshot '{shown}' not found. Available: {available}"
    return None, (
        f"Snapshot '{shown}' not changed: {len(matches)} snapshots on VM "
        f"'{sanitize(vm.name, 200)}' match that name (duplicates, or names that differ "
        "only by control/invisible characters). Rename one in vCenter, then retry."
    )


def revert_to_snapshot(
    si: ServiceInstance, vm_name: str, snap_name: str
) -> str:
    """Revert VM to a named snapshot."""
    node, refusal = resolve_snapshot(_require_vm(si, vm_name), snap_name)
    if node is None:
        return refusal

    task = node.snapshot.RevertToSnapshot_Task()
    _wait_for_task(task)
    return f"VM '{vm_name}' reverted to snapshot '{snap_name}'."


def delete_snapshot(
    si: ServiceInstance,
    vm_name: str,
    snap_name: str,
    remove_children: bool = False,
    *,
    wait: bool = True,
    timeout: int = 1800,
) -> str:
    """Delete a named snapshot, consolidating its delta disk into the parent.

    Snapshot consolidation is the slowest write operation: old or large delta
    disks can take many minutes. ``timeout`` defaults to 1800s (30 min) rather
    than the 300s used by metadata ops. When ``wait`` is False the task is fired
    and the task id returned immediately (use ``get_task_status`` to poll) — this
    avoids blocking an agent's context window on a long consolidation.
    """
    node, refusal = resolve_snapshot(_require_vm(si, vm_name), snap_name)
    if node is None:
        return refusal

    task = node.snapshot.RemoveSnapshot_Task(removeChildren=remove_children)
    task_id = _task_moid(task)

    if not wait:
        return (
            f"Snapshot delete started for '{snap_name}' on VM '{vm_name}'. "
            f"Task id: {task_id}. Poll with: vm task-status {task_id}"
        )

    try:
        _wait_for_task(task, timeout=timeout)
    except TaskStillRunning as e:
        return (
            f"Snapshot '{snap_name}' delete on VM '{vm_name}' is still running "
            f"after {e.timeout}s (large delta consolidation) — NOT failed. "
            f"Task id: {task_id}. Poll with: vm task-status {task_id}"
        )
    return f"Snapshot '{snap_name}' deleted from VM '{vm_name}'."


def get_task_status(si: ServiceInstance, task_id: str) -> dict:
    """Poll a previously-started vSphere task by its managed-object id.

    Returns a structured status so an agent can check a long-running async
    operation (e.g. snapshot delete fired with wait=False) without re-running
    it. A task id that vCenter has already garbage-collected after completion
    surfaces as state 'gone' with guidance, not an exception.

    A failed task reports its fault under ``task_error``, never ``error``. The
    poll itself succeeded — reading the status of a task that failed is a
    working read — and a top-level ``error`` key is the family's envelope for
    "this call failed". Emitting it here made three things wrong at once: the
    audit row, the circuit breaker (three polls of one failed task booked three
    phantom failures), and the agent's own reading, which could not tell a
    broken poll from a broken task.
    """
    task = vim.Task(task_id, si._stub)
    try:
        info = task.info
    except Exception:  # noqa: BLE001 — translated to a teaching status
        return {
            "task_id": task_id,
            "state": "gone",
            "note": (
                "vCenter no longer knows this task id. Completed tasks are "
                "garbage-collected after a while — if the operation finished "
                "successfully this is expected. Re-list the resource to confirm."
            ),
        }

    state = str(info.state)
    result: dict = {
        "task_id": task_id,
        "state": state,
        "progress_pct": getattr(info, "progress", None),
        "operation": getattr(info, "descriptionId", None),
        "entity": getattr(info, "entityName", None),
    }
    if state == "error" and info.error is not None:
        result["task_error"] = getattr(info.error, "msg", None) or type(info.error).__name__
    return result


# ─── Clone ────────────────────────────────────────────────────────────────────


def clone_vm(
    si: ServiceInstance,
    vm_name: str,
    new_name: str,
    *,
    target_host: str | None = None,
    target_datastore: str | None = None,
    power_on: bool = False,
) -> str:
    """Clone a VM with the same configuration.

    Without ``target_host`` / ``target_datastore`` vCenter places the clone on
    the **source VM/template's** host and datastore. To land it elsewhere
    (different site, different cluster, different storage), pass both.
    """
    vm = _require_vm(si, vm_name)
    folder = vm.parent

    relocate_spec = vim.vm.RelocateSpec()

    if target_host:
        host = find_host_by_name(si, target_host)
        if host is None:
            return (
                f"Target host '{target_host}' not found. "
                f"List hosts: vmware-aiops cluster list-hosts"
            )
        relocate_spec.host = host
        if host.parent and getattr(host.parent, "resourcePool", None):
            relocate_spec.pool = host.parent.resourcePool
        else:
            return (
                f"Target host '{target_host}' has no resource pool "
                "(standalone host outside cluster?). Cannot clone."
            )

    if target_datastore:
        ds = find_datastore_by_name(si, target_datastore)
        if ds is None:
            return (
                f"Target datastore '{target_datastore}' not found. "
                f"List: vmware-aiops datastore list"
            )
        relocate_spec.datastore = ds

    clone_spec = vim.vm.CloneSpec(
        location=relocate_spec,
        powerOn=power_on,
        template=False,
    )

    task = vm.Clone(folder=folder, name=new_name, spec=clone_spec)
    _wait_for_task(task, timeout=600)

    placement = []
    if target_host:
        placement.append(f"host={target_host}")
    if target_datastore:
        placement.append(f"datastore={target_datastore}")
    where = f" ({', '.join(placement)})" if placement else " (inherited from source)"
    return f"VM '{vm_name}' cloned as '{new_name}'{where}."


# ─── Migrate (vMotion) ───────────────────────────────────────────────────────


def migrate_vm(
    si: ServiceInstance,
    vm_name: str,
    target_host_name: str,
    *,
    target_datastore: str | None = None,
) -> str:
    """Migrate (vMotion) a VM to another host, optionally with storage vMotion.

    If the target host does not have access to the VM's current datastore,
    ``target_datastore`` is **required** — otherwise vCenter rejects the
    Relocate task with "host has no access to the source datastore".
    """
    vm = _require_vm(si, vm_name)
    target_host = find_host_by_name(si, target_host_name)
    if target_host is None:
        return f"Target host '{target_host_name}' not found."

    if not vm.runtime.host:
        return f"VM '{vm_name}' has no current host (may be provisioning). Cannot migrate."

    current_host = vm.runtime.host.name
    if current_host == target_host_name and target_datastore is None:
        return f"VM '{vm_name}' is already on host '{target_host_name}'."

    if target_host.parent is None or getattr(target_host.parent, "resourcePool", None) is None:
        return (
            f"Target host '{target_host_name}' has no resource pool "
            "(standalone host outside cluster?). Cannot migrate."
        )

    # Pre-flight: storage accessibility (run before building RelocateSpec)
    src_ds = vm.datastore[0] if vm.datastore else None
    resolved_ds = None
    if target_datastore:
        resolved_ds = find_datastore_by_name(si, target_datastore)
        if resolved_ds is None:
            return (
                f"Target datastore '{target_datastore}' not found. "
                f"List: vmware-aiops datastore list"
            )
    # Datastore.host is HostMount[] — the HostSystem is in each mount's .key
    elif src_ds is not None and target_host not in [m.key for m in src_ds.host]:
        return (
            f"Target host '{target_host_name}' has no access to source datastore "
            f"'{src_ds.name}'. Cross-host vMotion requires shared storage OR "
            f"pass --to-datastore=<name> to also perform storage vMotion. "
            f"List datastores: vmware-aiops datastore list"
        )

    relocate_spec = vim.vm.RelocateSpec(
        host=target_host,
        pool=target_host.parent.resourcePool,
    )
    if resolved_ds is not None:
        relocate_spec.datastore = resolved_ds

    task = vm.Relocate(spec=relocate_spec)
    _wait_for_task(task, timeout=600)

    ds_note = f" + datastore={target_datastore}" if target_datastore else ""
    return f"VM '{vm_name}' migrated from '{current_host}' to '{target_host_name}'{ds_note}."


# ─── Clean Slate ──────────────────────────────────────────────────────────────


def clean_slate(
    si: ServiceInstance,
    vm_name: str,
    snapshot_name: str = "baseline",
) -> str:
    """Revert VM to a baseline snapshot (Clean Slate).

    Powers off the VM first if it is running, then reverts to the named
    snapshot.  Intended for lab/dev VMs where you want a clean starting
    state after a task.

    Args:
        si: vSphere ServiceInstance.
        vm_name: Name of the VM to revert.
        snapshot_name: Snapshot to revert to (default: "baseline").
    """
    vm = _require_vm(si, vm_name)
    # Resolved before anything changes: a missing or ambiguous snapshot used to
    # be found out only after the VM had been powered off.
    node, refusal = resolve_snapshot(vm, snapshot_name)
    if node is None:
        return f"Clean Slate: not reverted, VM left as it was. {refusal}"

    # Power off if running — revert is more predictable on a powered-off VM
    if vm.runtime.powerState == vim.VirtualMachine.PowerState.poweredOn:
        task = vm.PowerOff()
        _wait_for_task(task)

    _wait_for_task(node.snapshot.RevertToSnapshot_Task())
    return f"Clean Slate: VM '{vm_name}' reverted to snapshot '{snapshot_name}'."
