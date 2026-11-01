"""A confirmed write acts on the VM (and snapshot) the gate measured, or on nothing.

The MCP write tools measure the VM, then hand its *name* to an executor that
looks it up again (HLD §7 batches 2-4 leftover, 2026-09-19). A VM recreated
under the same name between the two lookups would be changed instead of the
one in the blast radius. The executors now take the measured instance UUID
(and snapshot id) and refuse when the object they find is a different one.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pyVmomi import vim
from vmware_policy.budget import reset_budget
from vmware_policy.policy import reset_policy_engine
from vmware_policy.undo import reset_undo_store

import vmware_aiops.mcp_server.tools.guest as guest_tools
import vmware_aiops.mcp_server.tools.vm as vm_tools
from vmware_aiops.ops import guest_ops, vm_lifecycle

ON = vim.VirtualMachine.PowerState.poweredOn
CHANGED = "not the VM that was previewed"


@pytest.fixture(autouse=True)
def harness(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_HOME", str(tmp_path / "ops"))
    reset_policy_engine()
    reset_budget()
    reset_undo_store()
    si = MagicMock(name="si")
    for mod in (vm_tools, guest_tools):
        monkeypatch.setattr(mod, "_get_connection", lambda target=None: si)
    monkeypatch.setattr(vm_lifecycle, "_wait_for_task", lambda *a, **k: None)
    yield si
    reset_policy_engine()
    reset_budget()
    reset_undo_store()


def _snapshot(name: str, moid: str) -> MagicMock:
    node = MagicMock(name=f"snap:{moid}")
    node.name = name
    node.snapshot._moId = moid
    node.childSnapshotList = []
    return node


def _vm(uuid: str, snapshot_moid: str = "snapshot-1") -> MagicMock:
    vm = MagicMock(name=f"vm:{uuid}")
    vm.name = "web-01"
    vm.config.instanceUuid = uuid
    vm.runtime.powerState = ON
    vm.runtime.host.name = "esx-01"
    vm.guest.toolsRunningStatus = "guestToolsRunning"
    vm.datastore = []
    vm.snapshot.rootSnapshotList = [_snapshot("before", snapshot_moid)]
    return vm


def _radius(**extra) -> dict:
    return {"vm": "web-01", "instance_uuid": "uuid-A", "power_state": "poweredOn",
            "noop": False, "blockers": [], "unmeasured": [], **extra}


@pytest.fixture
def finds(monkeypatch):
    """The executor's own lookup finds this VM."""
    def install(vm):
        monkeypatch.setattr(vm_lifecycle, "find_vm_by_name", lambda _si, _n: vm)
        monkeypatch.setattr(guest_ops, "find_vm_by_name", lambda _si, _n: vm)
        return vm
    return install


def _error(out) -> str:
    """The refusal text, or the whole result when the tool went ahead."""
    if isinstance(out, dict):
        return out.get("error", repr(out))
    return out


# ─── power off / migrate ─────────────────────────────────────────────────────


def test_power_off_refuses_a_vm_recreated_under_the_same_name(monkeypatch, finds):
    measured, replacement = _vm("uuid-A"), finds(_vm("uuid-B"))
    monkeypatch.setattr(vm_tools, "power_off_radius", lambda si, n, f: (measured, _radius()))

    out = vm_tools.power_off_vm("web-01", force=True, confirm=True)

    assert CHANGED in _error(out)
    replacement.PowerOff.assert_not_called()
    measured.PowerOff.assert_not_called()


def test_power_off_still_acts_on_the_measured_vm(monkeypatch, finds):
    same = finds(_vm("uuid-A"))
    monkeypatch.setattr(vm_tools, "power_off_radius", lambda si, n, f: (same, _radius()))

    vm_tools.power_off_vm("web-01", force=True, confirm=True)

    same.PowerOff.assert_called_once()


def test_migrate_refuses_a_vm_recreated_under_the_same_name(monkeypatch, finds):
    replacement = finds(_vm("uuid-B"))
    monkeypatch.setattr(vm_tools, "migrate_radius", lambda *a: _radius())
    monkeypatch.setattr(vm_lifecycle, "find_host_by_name", lambda _si, _n: MagicMock())

    out = vm_tools.migrate_vm("web-01", "esx-02", confirm=True)

    assert CHANGED in _error(out)
    replacement.Relocate.assert_not_called()


# ─── snapshots ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("tool", ["revert", "delete"])
def test_snapshot_tools_refuse_a_vm_recreated_under_the_same_name(monkeypatch, finds, tool):
    replacement = finds(_vm("uuid-B"))
    radius = _radius(snapshot={"name": "before", "id": "snapshot-1"})
    monkeypatch.setattr(vm_tools, "revert_snapshot_radius", lambda *a: radius)
    monkeypatch.setattr(vm_tools, "delete_snapshot_radius", lambda *a: radius)

    if tool == "revert":
        out = vm_tools.revert_vm_snapshot("web-01", "before", confirm=True)
    else:
        out = vm_tools.delete_vm_snapshot("web-01", "before", confirm=True)

    assert CHANGED in _error(out)
    node = replacement.snapshot.rootSnapshotList[0]
    node.snapshot.RevertToSnapshot_Task.assert_not_called()
    node.snapshot.RemoveSnapshot_Task.assert_not_called()


@pytest.mark.parametrize("tool", ["revert", "delete"])
def test_snapshot_tools_refuse_a_snapshot_recreated_under_the_same_name(monkeypatch, finds, tool):
    vm = finds(_vm("uuid-A", snapshot_moid="snapshot-2"))
    radius = _radius(snapshot={"name": "before", "id": "snapshot-1"})
    monkeypatch.setattr(vm_tools, "revert_snapshot_radius", lambda *a: radius)
    monkeypatch.setattr(vm_tools, "delete_snapshot_radius", lambda *a: radius)

    if tool == "revert":
        out = vm_tools.revert_vm_snapshot("web-01", "before", confirm=True)
    else:
        out = vm_tools.delete_vm_snapshot("web-01", "before", confirm=True)

    assert "not the snapshot that was previewed" in _error(out)
    node = vm.snapshot.rootSnapshotList[0]
    node.snapshot.RevertToSnapshot_Task.assert_not_called()
    node.snapshot.RemoveSnapshot_Task.assert_not_called()


def test_snapshot_revert_still_acts_on_the_measured_snapshot(monkeypatch, finds):
    vm = finds(_vm("uuid-A", snapshot_moid="snapshot-1"))
    monkeypatch.setattr(vm_tools, "revert_snapshot_radius",
                        lambda *a: _radius(snapshot={"name": "before", "id": "snapshot-1"}))

    vm_tools.revert_vm_snapshot("web-01", "before", confirm=True)

    vm.snapshot.rootSnapshotList[0].snapshot.RevertToSnapshot_Task.assert_called_once()


@pytest.mark.parametrize("tool", ["revert", "delete"])
@pytest.mark.parametrize("missing_id", [None, ""])
def test_snapshot_tools_refuse_when_snapshot_id_cannot_be_measured(
    finds, tool, missing_id
):
    vm = finds(_vm("uuid-A", snapshot_moid=missing_id))

    if tool == "revert":
        out = vm_tools.revert_vm_snapshot("web-01", "before", confirm=True)
    else:
        out = vm_tools.delete_vm_snapshot("web-01", "before", confirm=True)

    assert "snapshot_id" in _error(out)
    node = vm.snapshot.rootSnapshotList[0]
    node.snapshot.RevertToSnapshot_Task.assert_not_called()
    node.snapshot.RemoveSnapshot_Task.assert_not_called()


# ─── guest ───────────────────────────────────────────────────────────────────


def test_guest_command_refuses_a_vm_recreated_under_the_same_name(monkeypatch, finds, harness):
    finds(_vm("uuid-B"))
    monkeypatch.setattr(guest_tools, "measure_guest_exec", lambda *a, **k: _radius())

    out = guest_tools.run_vm_guest_command("web-01", "/bin/true", "root", "pw", confirm=True)

    assert CHANGED in _error(out)
    harness.RetrieveContent().guestOperationsManager.processManager \
        .StartProgramInGuest.assert_not_called()


def test_guest_upload_refuses_a_vm_recreated_under_the_same_name(
    monkeypatch, finds, harness, tmp_path
):
    finds(_vm("uuid-B"))
    src = tmp_path / "f.txt"
    src.write_text("x", encoding="utf-8")
    monkeypatch.setattr(guest_tools, "measure_guest_upload", lambda *a, **k: _radius())

    out = guest_tools.upload_vm_guest_file("web-01", str(src), "/tmp/f.txt", "root", "pw",
                                           confirm=True)

    assert CHANGED in _error(out)
    harness.RetrieveContent().guestOperationsManager.fileManager \
        .InitiateFileTransferToGuest.assert_not_called()


@pytest.mark.parametrize("fn", ["guest_exec_with_output", "guest_provision"])
def test_every_confirm_gated_guest_executor_takes_the_measured_uuid(fn):
    import inspect

    assert "expect_instance_uuid" in inspect.signature(getattr(guest_ops, fn)).parameters
