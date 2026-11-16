"""create_vm on standalone ESXi: the NIC spec, diskless creates, and cleanup.

Issue #69: on ESXi 8.0.1 (no vCenter) every create failed with
``Invalid configuration for device '2'`` / ``InvalidDeviceSpec``. The reporter
isolated it to one field: the vNIC set ``addressType="assigned"`` with no
``macAddress``. Without ``addressType`` the host generates a MAC and the same
spec succeeds. ``assigned`` means "this MAC was assigned by vCenter", so it
was never right for a spec that supplies no MAC.

The same report asked for two more things:

- A failed ``CreateVM_Task`` leaves an empty VM directory on the datastore.
  create_vm now removes it, but only when it can show the directory is its
  own: absent before the create and empty after. Anything else is reported,
  never deleted.
- ``disk_gb=0`` creates the VM without a disk, so a write-permission probe
  costs nothing on the datastore.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import typer
from pyVmomi import vim

from vmware_aiops.cli._common import _validate_vm_params
from vmware_aiops.ops import vm_lifecycle

INVALID_SPEC = "Invalid configuration for device '2'. | fault=vim.fault.InvalidDeviceSpec"


class FakeHost:
    """Datastore browser, FileManager and task results for one create_vm call.

    ``before`` / ``after``: the VM directory's state on either side of the
    create: None (absent), a list of file names, or an exception to raise.
    """

    def __init__(self, monkeypatch, *, create_fails=False, before=None, after=None,
                 delete_fails=False):
        self.config = None
        self.deleted: list[str] = []
        self.searched: list[str] = []
        self.created = False
        self._before, self._after = before, after
        self._create_fails, self._delete_fails = create_fails, delete_fails

        folder = MagicMock(childEntity=[])
        folder.CreateVM_Task.side_effect = self._create
        self.datacenter = SimpleNamespace(vmFolder=folder)
        self.datastore = SimpleNamespace(browser=MagicMock())
        self.datastore.browser.SearchDatastore_Task.side_effect = self._search
        self.si = MagicMock()
        self.si.content.fileManager.DeleteDatastoreFile_Task.side_effect = self._delete

        monkeypatch.setattr(vm_lifecycle, "resolve_datacenter", lambda si, name: self.datacenter)
        monkeypatch.setattr(vm_lifecycle, "find_compute_resource",
                            lambda dc, cluster: SimpleNamespace(resourcePool="pool"))
        monkeypatch.setattr(vm_lifecycle, "find_datastore_by_name",
                            lambda si, name: self.datastore)
        monkeypatch.setattr(vm_lifecycle, "_wait_for_task", self._wait)

    def _create(self, config, pool):
        self.config = config
        return ("create",)

    def _search(self, datastorePath, searchSpec):  # noqa: N803 — pyVmomi keyword names
        return ("search", datastorePath, tuple(searchSpec.matchPattern))

    def _delete(self, name, datacenter):
        assert datacenter is self.datacenter
        return ("delete", name)

    def _state(self):
        return self._after if self.created else self._before

    def _wait(self, task, timeout=300):
        kind = task[0]
        if kind == "create":
            self.created = True
            if self._create_fails:
                raise vm_lifecycle.TaskFailedError(INVALID_SPEC)
            return None
        if kind == "delete":
            if self._delete_fails:
                raise vm_lifecycle.TaskFailedError("Cannot delete file")
            self.deleted.append(task[1])
            return None
        _, path, _patterns = task
        self.searched.append(path)
        state = self._state()
        if isinstance(state, Exception):
            raise state
        if state is None:  # the real _wait_for_task wraps the fault and keeps it
            raise vm_lifecycle.TaskFailedError(
                f"Task failed: File {path} was not found", fault=vim.fault.FileNotFound(file=path))
        return SimpleNamespace(file=[vim.host.DatastoreBrowser.FileInfo(path=n) for n in state])


def _create(host, name="probe", **kwargs):
    kwargs.setdefault("datastore_name", "datastore1")
    return vm_lifecycle.create_vm(host.si, name, cpu=1, memory_mb=512, **kwargs)


def _devices(host, kind):
    return [c.device for c in host.config.deviceChange if isinstance(c.device, kind)]


# ─── NIC spec ────────────────────────────────────────────────────────────────


def test_nic_leaves_mac_generation_to_the_host(monkeypatch):
    host = FakeHost(monkeypatch)
    _create(host, disk_gb=1)
    (nic,) = _devices(host, vim.vm.device.VirtualEthernetCard)
    assert nic.addressType is None
    assert nic.macAddress is None


def test_nic_still_targets_the_requested_network(monkeypatch):
    host = FakeHost(monkeypatch)
    _create(host, network_name="iSCSI-A")
    (nic,) = _devices(host, vim.vm.device.VirtualEthernetCard)
    assert nic.backing.deviceName == "iSCSI-A"
    assert nic.connectable.startConnected is True


# ─── Diskless create ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("disk_gb", [0, 2])
@pytest.mark.parametrize("name", ["probe", "probe with spaces"])
def test_create_supplies_datastore_root_not_a_parent_directory(monkeypatch, disk_gb, name):
    host = FakeHost(monkeypatch)
    _create(host, name=name, disk_gb=disk_gb, datastore_name="Lab datastore")
    # CreateVM_Task treats a non-vmx path as a parent directory and appends
    # <name>/<name>.vmx. Supplying <name> here creates <name>/<name>/ instead,
    # leaving an empty parent after Destroy_Task (issue #69).
    assert host.config.files.vmPathName == "[Lab datastore]"
    assert host.config.name == name
    assert host.deleted == []


def test_disk_zero_creates_no_disk_but_keeps_controller_and_nic(monkeypatch):
    host = FakeHost(monkeypatch)
    result = _create(host, disk_gb=0)
    assert _devices(host, vim.vm.device.VirtualDisk) == []
    assert len(_devices(host, vim.vm.device.ParaVirtualSCSIController)) == 1
    assert len(_devices(host, vim.vm.device.VirtualEthernetCard)) == 1
    assert "Disk: none" in result


def test_positive_disk_is_still_created(monkeypatch):
    host = FakeHost(monkeypatch)
    _create(host, disk_gb=2)
    (disk,) = _devices(host, vim.vm.device.VirtualDisk)
    assert disk.capacityInKB == 2 * 1024 * 1024


@pytest.mark.parametrize("bad", [-1, 65_537, "40", None, True, 1.5])
def test_out_of_range_or_non_integer_disk_is_rejected_first(monkeypatch, bad):
    # vm_deploy passes disk_gb straight from a YAML spec, so types are not guaranteed.
    host = FakeHost(monkeypatch)
    with pytest.raises(ValueError, match="disk_gb"):
        _create(host, disk_gb=bad)
    assert host.config is None
    assert host.searched == []


def test_cli_accepts_disk_zero_and_rejects_negative():
    _validate_vm_params(disk_gb=0)
    with pytest.raises(typer.BadParameter, match="0-65536"):
        _validate_vm_params(disk_gb=-1)


# ─── Cleanup after a failed create ───────────────────────────────────────────


def test_failed_create_removes_the_empty_directory_it_left(monkeypatch):
    host = FakeHost(monkeypatch, create_fails=True, before=None, after=[])
    with pytest.raises(vm_lifecycle.TaskFailedError) as exc:
        _create(host)
    assert host.deleted == ["[datastore1] probe"]
    message = str(exc.value)
    assert "InvalidDeviceSpec" in message
    assert "Removed the empty '[datastore1] probe/'" in message


def test_failed_create_with_nothing_left_behind_deletes_nothing(monkeypatch):
    host = FakeHost(monkeypatch, create_fails=True, before=None, after=None)
    with pytest.raises(vm_lifecycle.TaskFailedError) as exc:
        _create(host)
    assert host.deleted == []
    assert "InvalidDeviceSpec" in str(exc.value)
    assert "No '[datastore1] probe/' directory was left behind." in str(exc.value)


def test_directory_that_existed_before_is_never_deleted(monkeypatch):
    host = FakeHost(monkeypatch, create_fails=True, before=[], after=[])
    with pytest.raises(vm_lifecycle.TaskFailedError) as exc:
        _create(host)
    assert host.deleted == []
    assert "existed before this create" in str(exc.value)
    assert "probe_1/" in str(exc.value)


def test_directory_with_files_is_reported_not_deleted(monkeypatch):
    host = FakeHost(monkeypatch, create_fails=True, before=None, after=["probe.vmx"])
    with pytest.raises(vm_lifecycle.TaskFailedError) as exc:
        _create(host)
    assert host.deleted == []
    assert "1 file(s)" in str(exc.value)


def test_unknown_prior_state_is_never_deleted(monkeypatch):
    host = FakeHost(monkeypatch, create_fails=True, before=RuntimeError("browser down"),
                    after=[])
    with pytest.raises(vm_lifecycle.TaskFailedError) as exc:
        _create(host)
    assert host.deleted == []
    assert "remove it before retrying" in str(exc.value)


def test_failed_cleanup_keeps_the_original_error(monkeypatch):
    host = FakeHost(monkeypatch, create_fails=True, before=None, after=[], delete_fails=True)
    with pytest.raises(vm_lifecycle.TaskFailedError) as exc:
        _create(host)
    message = str(exc.value)
    assert "InvalidDeviceSpec" in message
    assert "could not remove" in message


def test_only_the_vm_directory_is_probed_never_the_datastore_root(monkeypatch):
    host = FakeHost(monkeypatch, create_fails=True, before=None, after=[])
    with pytest.raises(vm_lifecycle.TaskFailedError):
        _create(host)
    assert host.searched == ["[datastore1] probe", "[datastore1] probe"]


@pytest.mark.parametrize("name", ["a/b", "50%", "x*", "[x]", "café"])
def test_names_vsphere_escapes_are_never_cleaned_up(monkeypatch, name):
    host = FakeHost(monkeypatch, create_fails=True, before=None, after=[])
    with pytest.raises(vm_lifecycle.TaskFailedError) as exc:
        _create(host, name=name)
    assert host.deleted == []
    assert host.searched == []
    assert "remove it before retrying" in str(exc.value)


def test_without_a_datastore_name_nothing_is_probed_or_deleted(monkeypatch):
    host = FakeHost(monkeypatch, create_fails=True, before=None, after=[])
    with pytest.raises(vm_lifecycle.TaskFailedError) as exc:
        _create(host, datastore_name=None)
    assert host.deleted == [] and host.searched == []
    assert "remove it before retrying" in str(exc.value)


def test_task_failed_error_keeps_the_vsphere_fault():
    from vmware_aiops.ops._vm_tasks import _wait_for_task

    fault = vim.fault.FileNotFound(file="[ds] x", msg="File [ds] x was not found")
    task = SimpleNamespace(_moId="task-1", info=SimpleNamespace(
        state=vim.TaskInfo.State.error, error=fault))
    with pytest.raises(vm_lifecycle.TaskFailedError) as exc:
        _wait_for_task(task)
    assert exc.value.fault is fault


def test_successful_create_touches_no_files(monkeypatch):
    host = FakeHost(monkeypatch, before=None, after=["probe.vmx"])
    _create(host)
    assert host.deleted == []
