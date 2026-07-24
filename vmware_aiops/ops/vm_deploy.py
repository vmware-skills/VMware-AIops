"""VM deployment: all fast-provisioning channels for VM creation.

Channels:
1. OVA import — deploy from OVA file (local or datastore)
2. ISO attach — create empty VM + mount ISO
3. Full clone — full copy from source VM
4. Linked clone — instant clone from snapshot (shared base disk, COW delta)
5. Template deploy — clone from vSphere template
6. Batch deploy — YAML spec for multiple VMs via any channel above

Composes existing VM lifecycle operations (create, clone, snapshot, power)
with new OVA/ISO/linked-clone/template capabilities.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import yaml
from pyVmomi import vim

from vmware_aiops.ops.inventory import (
    InventoryError,
    find_compute_resource,
    find_datastore_by_name,
    find_vm_by_name,
    resolve_datacenter,
)

# OVA import lives in its own module; re-exported so existing
# ``vm_deploy.deploy_ova`` call sites and ``batch_deploy`` keep working.
from vmware_aiops.ops.ova_deploy import deploy_ova
from vmware_aiops.ops.vm_lifecycle import (
    _wait_for_task,
    clone_vm,
    create_snapshot,
    create_vm,
    power_on_vm,
    reconfigure_vm,
)

if TYPE_CHECKING:
    from pyVmomi.vim import ServiceInstance

_log = logging.getLogger("vmware-aiops.deploy")

__all__ = [
    "deploy_ova",
    "attach_iso",
    "batch_clone",
    "linked_clone",
    "convert_to_template",
    "convert_to_vm",
    "deploy_from_template",
    "batch_linked_clone",
    "load_deploy_spec",
    "batch_deploy",
]


# ─── ISO Attach ──────────────────────────────────────────────────────────────


def attach_iso(
    si: ServiceInstance,
    vm_name: str,
    iso_ds_path: str,
) -> str:
    """Attach an ISO from a datastore to a VM's CD-ROM drive.

    Args:
        si: vSphere ServiceInstance
        vm_name: Target VM name
        iso_ds_path: Datastore path e.g. "[datastore1] iso/ubuntu.iso"

    Returns:
        Status message.
    """
    vm = find_vm_by_name(si, vm_name)
    if vm is None:
        return f"VM '{vm_name}' not found."

    # Find existing CD-ROM or create one
    cdrom = None
    ide_controller = None
    if vm.config and vm.config.hardware:
        for dev in vm.config.hardware.device:
            if isinstance(dev, vim.vm.device.VirtualCdrom):
                cdrom = dev
            elif isinstance(dev, vim.vm.device.VirtualIDEController):
                ide_controller = dev

    if cdrom:
        # Reconfigure existing CD-ROM to use ISO backing
        cdrom_spec = vim.vm.device.VirtualDeviceSpec(
            operation=vim.vm.device.VirtualDeviceSpec.Operation.edit,
            device=vim.vm.device.VirtualCdrom(
                key=cdrom.key,
                controllerKey=cdrom.controllerKey,
                unitNumber=cdrom.unitNumber,
                backing=vim.vm.device.VirtualCdrom.IsoBackingInfo(
                    fileName=iso_ds_path,
                ),
                connectable=vim.vm.device.VirtualDevice.ConnectInfo(
                    startConnected=True,
                    connected=True,
                    allowGuestControl=True,
                ),
            ),
        )
    else:
        # Add new CD-ROM device
        if ide_controller is None:
            return f"VM '{vm_name}' has no IDE controller for CD-ROM."

        cdrom_spec = vim.vm.device.VirtualDeviceSpec(
            operation=vim.vm.device.VirtualDeviceSpec.Operation.add,
            device=vim.vm.device.VirtualCdrom(
                controllerKey=ide_controller.key,
                unitNumber=0,
                backing=vim.vm.device.VirtualCdrom.IsoBackingInfo(
                    fileName=iso_ds_path,
                ),
                connectable=vim.vm.device.VirtualDevice.ConnectInfo(
                    startConnected=True,
                    connected=True,
                    allowGuestControl=True,
                ),
            ),
        )

    config_spec = vim.vm.ConfigSpec(deviceChange=[cdrom_spec])
    task = vm.ReconfigVM_Task(spec=config_spec)
    _wait_for_task(task)
    return f"ISO '{iso_ds_path}' attached to VM '{vm_name}'."


# ─── Batch Clone ─────────────────────────────────────────────────────────────


def batch_clone(
    si: ServiceInstance,
    source_vm_name: str,
    vm_names: list[str],
    cpu: int | None = None,
    memory_mb: int | None = None,
    snapshot_name: str | None = None,
    power_on: bool = False,
) -> list[dict]:
    """Clone multiple VMs from a source VM (gold image).

    For each clone:
    1. Clone from source
    2. Reconfigure CPU/memory (if specified)
    3. Create baseline snapshot (if specified)
    4. Power on (if specified)

    Returns:
        List of result dicts with name, status, message.
    """
    source = find_vm_by_name(si, source_vm_name)
    if source is None:
        return [{"name": source_vm_name, "status": "error",
                 "message": f"Source VM '{source_vm_name}' not found."}]

    results: list[dict] = []
    for name in vm_names:
        result = {"name": name, "status": "ok", "messages": []}
        try:
            # 1. Clone
            msg = clone_vm(si, source_vm_name, name)
            result["messages"].append(msg)

            # 2. Reconfigure
            if cpu is not None or memory_mb is not None:
                msg = reconfigure_vm(si, name, cpu=cpu, memory_mb=memory_mb)
                result["messages"].append(msg)

            # 3. Snapshot
            if snapshot_name:
                msg = create_snapshot(si, name, snapshot_name,
                                      description="Baseline snapshot",
                                      memory=False)
                result["messages"].append(msg)

            # 4. Power on
            if power_on:
                msg = power_on_vm(si, name)
                result["messages"].append(msg)

        except Exception as e:
            result["status"] = "error"
            result["messages"].append(str(e))

        results.append(result)
        _log.info("Batch clone %s: %s", name, result["status"])

    return results


# ─── Linked Clone (from snapshot, instant) ───────────────────────────────────


def linked_clone(
    si: ServiceInstance,
    source_vm_name: str,
    new_name: str,
    snapshot_name: str,
    cpu: int | None = None,
    memory_mb: int | None = None,
    power_on: bool = False,
    baseline_snapshot: str | None = None,
    target_host: str | None = None,
) -> str:
    """Create a linked clone from a VM snapshot.

    Linked clones share the base disk with the source VM and use a
    copy-on-write (COW) delta disk. This makes creation near-instant
    and uses minimal disk space.

    Requirements:
    - Source VM must have at least one snapshot.
    - The named snapshot must exist.

    Args:
        source_vm_name: Source VM to clone from.
        new_name: Name for the new linked clone.
        snapshot_name: Snapshot to use as the clone base.
        cpu: Override CPU count (optional).
        memory_mb: Override memory (optional).
        power_on: Power on after creation.
        baseline_snapshot: Create a new snapshot on the clone (optional).
    """
    from vmware_aiops.ops.vm_lifecycle import list_snapshots

    source = find_vm_by_name(si, source_vm_name)
    if source is None:
        return f"Source VM '{source_vm_name}' not found."

    # Find the snapshot
    snaps = list_snapshots(si, source_vm_name)
    target_snap = next((s for s in snaps if s["name"] == snapshot_name), None)
    if target_snap is None:
        available = ", ".join(s["name"] for s in snaps) or "none"
        return f"Snapshot '{snapshot_name}' not found. Available: {available}"

    # Linked clone spec: use snapshot as disk move type
    relocate_spec = vim.vm.RelocateSpec(
        diskMoveType=vim.vm.RelocateSpec.DiskMoveOptions.createNewChildDiskBacking,
    )
    if target_host:
        from vmware_aiops.ops.inventory import find_host_by_name

        host = find_host_by_name(si, target_host)
        if host is None:
            return f"Target host '{target_host}' not found."
        if host.parent is None or getattr(host.parent, "resourcePool", None) is None:
            return f"Target host '{target_host}' has no resource pool."
        relocate_spec.host = host
        relocate_spec.pool = host.parent.resourcePool
    clone_spec = vim.vm.CloneSpec(
        location=relocate_spec,
        powerOn=False,
        template=False,
        snapshot=target_snap["snapshot_ref"],
    )

    folder = source.parent
    task = source.Clone(folder=folder, name=new_name, spec=clone_spec)
    _wait_for_task(task, timeout=300)
    result_parts = [
        f"Linked clone '{new_name}' created from "
        f"'{source_vm_name}' @ snapshot '{snapshot_name}'."
    ]

    # Reconfigure
    if cpu is not None or memory_mb is not None:
        msg = reconfigure_vm(si, new_name, cpu=cpu, memory_mb=memory_mb)
        result_parts.append(msg)

    # Baseline snapshot on clone
    if baseline_snapshot:
        msg = create_snapshot(si, new_name, baseline_snapshot,
                              description="Baseline snapshot", memory=False)
        result_parts.append(msg)

    # Power on
    if power_on:
        msg = power_on_vm(si, new_name)
        result_parts.append(msg)

    return " | ".join(result_parts)


# ─── Template Operations ────────────────────────────────────────────────────


def convert_to_template(si: ServiceInstance, vm_name: str) -> str:
    """Convert a VM to a vSphere template.

    The VM must be powered off. After conversion, it cannot be powered on
    directly — it can only be used as a clone source.
    """
    vm = find_vm_by_name(si, vm_name)
    if vm is None:
        return f"VM '{vm_name}' not found."

    if vm.runtime.powerState != vim.VirtualMachine.PowerState.poweredOff:
        return f"VM '{vm_name}' must be powered off before converting to template."

    vm.MarkAsTemplate()
    return f"VM '{vm_name}' converted to template."


def convert_to_vm(
    si: ServiceInstance,
    template_name: str,
    host_name: str | None = None,
) -> str:
    """Convert a template back to a regular VM."""
    from vmware_aiops.ops.inventory import find_host_by_name

    vm = find_vm_by_name(si, template_name)
    if vm is None:
        return f"Template '{template_name}' not found."

    if not vm.config.template:
        return f"'{template_name}' is already a VM, not a template."

    # Need a resource pool — find from host or use first available
    if host_name:
        host = find_host_by_name(si, host_name)
        if host is None:
            return f"Host '{host_name}' not found."
        pool = host.parent.resourcePool
    else:
        try:
            datacenter = resolve_datacenter(si)
            pool = find_compute_resource(datacenter).resourcePool
        except InventoryError as e:
            return str(e)

    vm.MarkAsVirtualMachine(pool=pool)
    return f"Template '{template_name}' converted back to VM."


def deploy_from_template(
    si: ServiceInstance,
    template_name: str,
    new_name: str,
    datastore_name: str | None = None,
    cpu: int | None = None,
    memory_mb: int | None = None,
    power_on: bool = False,
    snapshot_name: str | None = None,
    target_host: str | None = None,
) -> str:
    """Deploy a new VM by cloning from a vSphere template.

    Without ``target_host`` the new VM lands on the **template's** host —
    pass ``target_host`` to place it on a specific ESXi.

    Args:
        template_name: Name of the source template.
        new_name: Name for the new VM.
        datastore_name: Target datastore (optional, uses template's if omitted).
        cpu: Override CPU count (optional).
        memory_mb: Override memory (optional).
        power_on: Power on after deploy.
        snapshot_name: Create baseline snapshot (optional).
        target_host: Target ESXi host name (optional, uses template's host if omitted).
    """
    from vmware_aiops.ops.inventory import find_host_by_name

    template = find_vm_by_name(si, template_name)
    if template is None:
        return f"Template '{template_name}' not found."

    if not template.config.template:
        return f"'{template_name}' is not a template. Use 'clone' instead."

    relocate_spec = vim.vm.RelocateSpec()
    if target_host:
        host = find_host_by_name(si, target_host)
        if host is None:
            return f"Target host '{target_host}' not found."
        if host.parent is None or getattr(host.parent, "resourcePool", None) is None:
            return f"Target host '{target_host}' has no resource pool."
        relocate_spec.host = host
        relocate_spec.pool = host.parent.resourcePool
    if datastore_name:
        ds = find_datastore_by_name(si, datastore_name)
        if ds is None:
            return f"Datastore '{datastore_name}' not found."
        relocate_spec.datastore = ds

    clone_spec = vim.vm.CloneSpec(
        location=relocate_spec,
        powerOn=False,
        template=False,
    )

    folder = template.parent
    task = template.Clone(folder=folder, name=new_name, spec=clone_spec)
    _wait_for_task(task, timeout=600)
    result_parts = [f"VM '{new_name}' deployed from template '{template_name}'."]

    # Reconfigure
    if cpu is not None or memory_mb is not None:
        msg = reconfigure_vm(si, new_name, cpu=cpu, memory_mb=memory_mb)
        result_parts.append(msg)

    # Baseline snapshot
    if snapshot_name:
        msg = create_snapshot(si, new_name, snapshot_name,
                              description="Baseline snapshot", memory=False)
        result_parts.append(msg)

    # Power on
    if power_on:
        msg = power_on_vm(si, new_name)
        result_parts.append(msg)

    return " | ".join(result_parts)


# ─── Batch Linked Clone ─────────────────────────────────────────────────────


def batch_linked_clone(
    si: ServiceInstance,
    source_vm_name: str,
    snapshot_name: str,
    vm_names: list[str],
    cpu: int | None = None,
    memory_mb: int | None = None,
    power_on: bool = False,
    baseline_snapshot: str | None = None,
) -> list[dict]:
    """Create multiple linked clones from a source VM snapshot.

    This is the fastest batch provisioning method — each clone shares the
    source disk and only stores delta changes.
    """
    results: list[dict] = []
    for name in vm_names:
        result = {"name": name, "status": "ok", "messages": []}
        try:
            msg = linked_clone(
                si, source_vm_name, name, snapshot_name,
                cpu=cpu, memory_mb=memory_mb,
                power_on=power_on, baseline_snapshot=baseline_snapshot,
            )
            result["messages"].append(msg)
        except Exception as e:
            result["status"] = "error"
            result["messages"].append(str(e))
        results.append(result)
    return results


# ─── Batch Deploy from YAML ─────────────────────────────────────────────────


def load_deploy_spec(spec_path: str) -> dict:
    """Load and validate a YAML deployment spec.

    Expected format:
    ```yaml
    defaults:
      cpu: 4
      memory_mb: 8192
      disk_gb: 100
      network: "VM Network"
      datastore: datastore1
      snapshot: clean-slate
      power_on: true

    # Provisioning channel (pick one):
    #   source: golden-vm             # Full clone from VM
    #   template: ubuntu-template     # Clone from vSphere template
    #   linked_clone:                 # Linked clone (fastest)
    #     source: golden-vm
    #     snapshot: clean-state

    vms:
      - name: sandbox-01
      - name: sandbox-02
        cpu: 8
        memory_mb: 16384
      - name: sandbox-win-01
        guest_id: windows2019srv_64Guest
        iso: "[datastore1] iso/win2022.iso"
        ova: /path/to/image.ova       # Per-VM OVA override
    ```
    """
    with open(spec_path, encoding="utf-8") as f:
        spec = yaml.safe_load(f)

    if not spec or "vms" not in spec:
        raise ValueError(
            f"Invalid deploy spec: missing top-level 'vms:' section in {spec_path}. "
            f"Add a 'vms:' list of VM entries — copy the layout from examples/deploy.yaml "
            f"in the vmware-aiops repo — then re-run the batch deploy."
        )

    return spec


def batch_deploy(
    si: ServiceInstance,
    spec_path: str,
) -> list[dict]:
    """Deploy multiple VMs from a YAML specification file.

    Supports all provisioning channels:
    - Clone mode: 'source' specified → full clone from VM
    - Template mode: 'template' specified → clone from vSphere template
    - Linked clone mode: 'linked_clone' specified → instant clone from snapshot
    - OVA mode: per-VM 'ova' field → deploy from OVA file
    - Create mode: fallback → create empty VM (optionally with ISO)

    Returns:
        List of result dicts per VM.
    """
    spec = load_deploy_spec(spec_path)
    defaults = spec.get("defaults", {})
    source_vm = spec.get("source")
    template = spec.get("template")
    lc_config = spec.get("linked_clone")
    vm_specs = spec["vms"]

    results: list[dict] = []

    for vm_spec in vm_specs:
        name = vm_spec["name"]
        # Merge defaults with per-VM overrides
        cpu = vm_spec.get("cpu", defaults.get("cpu", 2))
        memory_mb = vm_spec.get("memory_mb", defaults.get("memory_mb", 4096))
        disk_gb = vm_spec.get("disk_gb", defaults.get("disk_gb", 40))
        network = vm_spec.get("network", defaults.get("network", "VM Network"))
        datastore = vm_spec.get("datastore", defaults.get("datastore"))
        snapshot = vm_spec.get("snapshot", defaults.get("snapshot"))
        do_power_on = vm_spec.get("power_on", defaults.get("power_on", False))
        iso = vm_spec.get("iso")
        ova = vm_spec.get("ova")
        guest_id = vm_spec.get("guest_id", defaults.get("guest_id", "otherGuest64"))

        result = {"name": name, "status": "ok", "messages": []}

        try:
            if ova:
                # OVA mode (per-VM)
                msg = deploy_ova(
                    si, ova_path=ova, vm_name=name,
                    datastore_name=datastore or "",
                    network_name=network,
                )
                result["messages"].append(msg)

            elif lc_config:
                # Linked clone mode (fastest)
                msg = linked_clone(
                    si, source_vm_name=lc_config["source"],
                    new_name=name,
                    snapshot_name=lc_config["snapshot"],
                    cpu=cpu, memory_mb=memory_mb,
                )
                result["messages"].append(msg)

            elif template:
                # Template mode
                msg = deploy_from_template(
                    si, template_name=template, new_name=name,
                    datastore_name=datastore, cpu=cpu, memory_mb=memory_mb,
                )
                result["messages"].append(msg)

            elif source_vm:
                # Full clone mode
                msg = clone_vm(si, source_vm, name)
                result["messages"].append(msg)
                if cpu or memory_mb:
                    msg = reconfigure_vm(si, name, cpu=cpu, memory_mb=memory_mb)
                    result["messages"].append(msg)

            else:
                # Create empty VM mode
                msg = create_vm(
                    si, vm_name=name, cpu=cpu, memory_mb=memory_mb,
                    disk_gb=disk_gb, network_name=network,
                    datastore_name=datastore, guest_id=guest_id,
                )
                result["messages"].append(msg)

            # Attach ISO if specified (works with all modes)
            if iso:
                msg = attach_iso(si, name, iso)
                result["messages"].append(msg)

            # Create baseline snapshot
            if snapshot:
                msg = create_snapshot(si, name, snapshot,
                                      description="Baseline snapshot",
                                      memory=False)
                result["messages"].append(msg)

            # Power on
            if do_power_on:
                msg = power_on_vm(si, name)
                result["messages"].append(msg)

        except Exception as e:
            result["status"] = "error"
            result["messages"].append(str(e))

        results.append(result)
        _log.info("Batch deploy %s: %s", name, result["status"])

    return results
