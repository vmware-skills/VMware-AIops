"""create_vm must build a NIC that CreateVM_Task accepts on standalone ESXi.

Issue #69: on ESXi 8.0.1 (no vCenter) every create failed with
``Invalid configuration for device '2'`` / ``InvalidDeviceSpec``. The reporter
isolated it to one field: the vNIC set ``addressType="assigned"`` with no
``macAddress``. Without ``addressType`` the host generates a MAC and the same
spec succeeds. ``assigned`` means "this MAC was assigned by vCenter", so it
was never right for a spec that supplies no MAC.

The same report noted that a failed create leaves an empty VM directory on the
datastore. The error now says so and names the directory, so a caller knows
there is something to clean up before retrying with the same name.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pyVmomi import vim

from vmware_aiops.ops import vm_lifecycle


@pytest.fixture
def captured(monkeypatch):
    """Run create_vm against a fake inventory and capture the ConfigSpec."""
    seen: dict[str, object] = {}
    folder = MagicMock(childEntity=[])

    def create_vm_task(config, pool):
        seen["config"] = config
        return "task"

    folder.CreateVM_Task.side_effect = create_vm_task
    datacenter = SimpleNamespace(vmFolder=folder)
    monkeypatch.setattr(vm_lifecycle, "resolve_datacenter", lambda si, name: datacenter)
    monkeypatch.setattr(
        vm_lifecycle, "find_compute_resource",
        lambda dc, cluster: SimpleNamespace(resourcePool="pool"))
    monkeypatch.setattr(vm_lifecycle, "find_datastore_by_name", lambda si, name: object())
    monkeypatch.setattr(vm_lifecycle, "_wait_for_task", lambda task: None)
    return seen


def _nic(config: vim.vm.ConfigSpec) -> vim.vm.device.VirtualEthernetCard:
    nics = [c.device for c in config.deviceChange
            if isinstance(c.device, vim.vm.device.VirtualEthernetCard)]
    assert len(nics) == 1
    return nics[0]


def test_nic_leaves_mac_generation_to_the_host(captured):
    vm_lifecycle.create_vm(MagicMock(), "probe", cpu=1, memory_mb=512, disk_gb=1,
                           datastore_name="datastore1")
    nic = _nic(captured["config"])
    assert nic.addressType is None
    assert nic.macAddress is None


def test_nic_still_targets_the_requested_network(captured):
    vm_lifecycle.create_vm(MagicMock(), "probe", network_name="iSCSI-A",
                           datastore_name="datastore1")
    nic = _nic(captured["config"])
    assert nic.backing.deviceName == "iSCSI-A"
    assert nic.connectable.startConnected is True


def test_failed_create_names_the_directory_it_may_leave(captured, monkeypatch):
    def fail(task):
        raise vm_lifecycle.TaskFailedError(
            "Invalid configuration for device '2'. | fault=vim.fault.InvalidDeviceSpec")

    monkeypatch.setattr(vm_lifecycle, "_wait_for_task", fail)
    with pytest.raises(vm_lifecycle.TaskFailedError) as exc:
        vm_lifecycle.create_vm(MagicMock(), "probe", datastore_name="datastore1")
    message = str(exc.value)
    assert "InvalidDeviceSpec" in message
    assert "[datastore1] probe/" in message
