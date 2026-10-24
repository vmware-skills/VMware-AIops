"""The smaller findings of the 2026-10-17 write round on home-vcenter.

* power_off_vm on a VM that is already off answered ``noop`` *and* a "VMware
  Tools is not running" blocker -- two contradicting answers for one call.
* get_vm_task_status said a garbage task id was "gone ... expected" (as if the
  operation had finished), and any failure to read the task -- a dropped
  connection too -- turned into that same reassuring answer.
* A vMotion refused for CPU features (FeatureRequirementsNotMet, AVX-512 on the
  lab) arrived as a long fault string cut at 500 characters with no next step.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pyVmomi import vim, vmodl

from vmware_aiops.ops import vm_gate, vm_lifecycle


def _vm(power, tools="guestToolsNotRunning"):
    vm = MagicMock()
    vm.name = "web-01"
    vm.runtime.powerState = power
    vm.guest.toolsRunningStatus = tools
    vm.config.instanceUuid = "uuid-1"
    return vm


@pytest.mark.unit
def test_powering_off_a_vm_that_is_off_is_a_noop_without_blockers(monkeypatch) -> None:
    monkeypatch.setattr(vm_lifecycle, "_require_vm",
                        lambda si, n: _vm(vim.VirtualMachine.PowerState.poweredOff))
    _vm_obj, radius = vm_gate.power_off_radius(MagicMock(), "web-01", force=False)

    assert radius["noop"] is True
    assert radius["blockers"] == []


@pytest.mark.unit
def test_powering_off_a_running_vm_without_tools_is_still_blocked(monkeypatch) -> None:
    monkeypatch.setattr(vm_lifecycle, "_require_vm",
                        lambda si, n: _vm(vim.VirtualMachine.PowerState.poweredOn))
    _vm_obj, radius = vm_gate.power_off_radius(MagicMock(), "web-01", force=False)

    assert radius["noop"] is False
    assert any("VMware Tools is not running" in b for b in radius["blockers"])


class _Task:
    def __init__(self, raises):
        self._raises = raises

    @property
    def info(self):
        raise self._raises


@pytest.mark.unit
def test_an_unknown_task_id_does_not_claim_the_operation_finished(monkeypatch) -> None:
    monkeypatch.setattr(vm_lifecycle.vim, "Task",
                        lambda tid, stub: _Task(vmodl.fault.ManagedObjectNotFound()))
    out = vm_lifecycle.get_task_status(SimpleNamespace(_stub=None), "garbage!!")

    assert out["state"] == "gone"
    assert "expected" not in out["note"]
    assert "wrong" in out["note"]


@pytest.mark.unit
def test_a_failed_task_read_is_not_reported_as_gone(monkeypatch) -> None:
    monkeypatch.setattr(vm_lifecycle.vim, "Task",
                        lambda tid, stub: _Task(ConnectionError("dropped")))
    with pytest.raises(ConnectionError):
        vm_lifecycle.get_task_status(SimpleNamespace(_stub=None), "task-1")


@pytest.mark.unit
def test_a_cpu_feature_refusal_leads_with_the_remedy(monkeypatch) -> None:
    long_detail = "detail=" + "x" * 900
    failure = vm_lifecycle.TaskFailedError(
        "Task failed: The target host does not support the virtual machine's current "
        f"hardware requirements. | fault=vim.fault.FeatureRequirementsNotMet | {long_detail}"
    )
    vm = MagicMock()
    vm.runtime.host.name = "esx-01"
    vm.datastore = []
    host = MagicMock()
    host.parent.resourcePool = vim.ResourcePool("resgroup-1", None)
    monkeypatch.setattr(vm_lifecycle, "_require_vm", lambda si, n, **k: vm)
    monkeypatch.setattr(vm_lifecycle, "find_host_by_name", lambda si, n: host)
    monkeypatch.setattr(vm_lifecycle.vim.vm, "RelocateSpec", lambda **k: MagicMock())

    def fail(_task, timeout=0):
        raise failure

    monkeypatch.setattr(vm_lifecycle, "_wait_for_task", fail)
    with pytest.raises(vm_lifecycle.TaskFailedError) as info:
        vm_lifecycle.migrate_vm(MagicMock(), "web-01", "esx-02")

    text = str(info.value)
    assert "power" in text[:300].lower() and "EVC" in text[:300]
