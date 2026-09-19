"""The blast-radius gate in front of VM deletion (HLD §7, revised 2026-09-16).

``vm_lifecycle.delete_vm`` stays the executor the CLI and the TTL daemon call.
This module is what the MCP tool puts in front of it:

* ``measure_vm_delete`` reads what a deletion would destroy (L1).
* ``vm_delete_blast_radius`` is the preview a bare call returns (L2).
* ``delete_vm_acknowledged`` re-measures, refuses on a blocker, an unmeasured
  field or an acknowledgement that no longer matches, and only then destroys
  the very object it measured (L3).

The acknowledgement carries **stable** keys only — identity and counts that do
not drift on their own. Power state and host are shown but not echoed: they
change without anyone deciding anything, and an echo that fails for those
reasons teaches the caller to stop reading it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pyVmomi import vim, vmodl
from vmware_policy import sanitize

from vmware_aiops.ops import vm_lifecycle

if TYPE_CHECKING:
    from pyVmomi.vim import ServiceInstance

#: The keys a caller must echo back from the preview to delete.
ACK_KEYS = ("instance_uuid", "disk_count", "snapshot_count")

#: Disks listed individually in the blast radius; the counts cover the rest.
MAX_LISTED_DISKS = 16

_READ_ERRORS = (vmodl.MethodFault, AttributeError, TypeError)


class DeleteRefusedError(Exception):
    """Raised when a confirmed deletion is refused (blocker, unmeasured, stale)."""


def _read_disks(vm: vim.VirtualMachine) -> list[dict] | None:
    """Every virtual disk's label and provisioned size; None if any is unreadable.

    Provisioned size is an upper bound on what is freed: a linked clone's
    delta or an RDM's mapping file is smaller than the size shown.
    """
    try:
        return [
            {
                "label": sanitize(dev.deviceInfo.label, 100),
                "size_gb": round(dev.capacityInKB / (1024 * 1024), 1),
            }
            for dev in vm.config.hardware.device or []
            if isinstance(dev, vim.vm.device.VirtualDisk)
        ]
    except _READ_ERRORS:
        return None


def _read(fn, fallback=None):
    try:
        return fn()
    except _READ_ERRORS:
        return fallback


def measure_vm_delete(vm: vim.VirtualMachine) -> dict[str, Any]:
    """What deleting ``vm`` would destroy, and what stands in the way."""
    instance_uuid = _read(lambda: vm.config.instanceUuid)
    disks = _read_disks(vm)
    snapshot_count = _read(
        lambda: vm_lifecycle._count_snapshots(vm.snapshot) if vm.snapshot else 0
    )
    power_state = _read(lambda: vm.runtime.powerState)

    unmeasured = [
        field for field, value in (
            ("instance_uuid", instance_uuid),
            ("disks", disks),
            ("snapshot_count", snapshot_count),
            ("power_state", power_state),
        ) if value is None
    ]
    blockers = []
    if power_state == vim.VirtualMachine.PowerState.poweredOn:
        blockers.append(
            "The VM is powered on. Power it off first with vm_power_off, "
            "then preview the deletion again."
        )
    elif power_state == vim.VirtualMachine.PowerState.suspended:
        blockers.append(
            "The VM is suspended: its memory holds a paused running workload that "
            "deleting would discard. Power it off first with vm_power_off, then "
            "preview the deletion again."
        )

    disk_count = len(disks) if disks is not None else None
    radius: dict[str, Any] = {
        "vm": sanitize(_read(lambda: vm.name, ""), 200),
        "instance_uuid": instance_uuid,
        "host": sanitize(_read(lambda: vm.runtime.host.name, "") or "", 200) or None,
        "power_state": str(power_state) if power_state is not None else None,
        "disks": (disks or [])[:MAX_LISTED_DISKS],
        "disk_count": disk_count,
        "total_disk_gb": round(sum(d["size_gb"] for d in disks), 1) if disks is not None else None,
        "snapshot_count": snapshot_count,
        "unmeasured": unmeasured,
        "blockers": blockers,
        "acknowledge_with": None if unmeasured else {
            "instance_uuid": instance_uuid,
            "disk_count": disk_count,
            "snapshot_count": snapshot_count,
        },
    }
    return radius


def vm_delete_blast_radius(si: ServiceInstance, vm_name: str) -> dict[str, Any]:
    """The preview: measure, destroy nothing."""
    return measure_vm_delete(vm_lifecycle._require_vm(si, vm_name))


def _refusal(radius: dict[str, Any], acknowledge: dict | None) -> str | None:
    if radius["blockers"]:
        return " ".join(radius["blockers"])
    if radius["unmeasured"]:
        return (
            f"Could not read {', '.join(radius['unmeasured'])} for this VM, so what "
            "the deletion would destroy is unknown and it is refused. Check the "
            "VM in vCenter (permissions, orphaned or inaccessible state) and retry."
        )
    if not isinstance(acknowledge, dict):
        return (
            "confirm=True needs acknowledge_blast_radius: call vm_delete without "
            "confirm, show the user the blast_radius, then pass its "
            "acknowledge_with object back unchanged."
        )
    expected = radius["acknowledge_with"]
    # Compared with type: True == 1 in Python, so `!=` alone would accept
    # disk_count=True for a one-disk VM.
    stale = [
        k for k in ACK_KEYS
        if type(acknowledge.get(k)) is not type(expected[k]) or acknowledge.get(k) != expected[k]
    ]
    if stale:
        now = ", ".join(f"{k}={expected[k]!r}" for k in stale)
        return (
            f"The VM changed since the preview ({now} now). Nothing was deleted. "
            "Preview again and confirm against what is there now."
        )
    return None


def delete_vm_acknowledged(
    si: ServiceInstance, vm_name: str, acknowledge: dict | None
) -> dict[str, Any]:
    """Re-measure, refuse unless the acknowledgement still holds, then destroy."""
    vm = vm_lifecycle._require_vm(si, vm_name)
    radius = measure_vm_delete(vm)
    reason = _refusal(radius, acknowledge)
    if reason:
        raise DeleteRefusedError(f"vm_delete refused for '{radius['vm']}': {reason}")
    vm_lifecycle._wait_for_task(vm.Destroy_Task())
    return radius
