"""vm_delete is the reference implementation of HLD §7 (revised 2026-09-16).

Three levels, all on one vocabulary (``confirm: bool = False``):

* L1 — every response states the blast radius: what would be (or was) destroyed.
* L2 — a bare call previews; nothing is destroyed unless ``confirm=True``.
* L3 — refusal. A powered-on VM, an unreadable identity, or a name that matches
  more than one VM is refused outright. ``confirm=True`` alone is not enough:
  the caller must echo ``acknowledge_blast_radius`` — the stable keys the
  preview returned — and they are re-measured before anything is destroyed.

The principle is "the system must not act blind and amplify the risk": a
confirmation that does not carry what was confirmed is an approval of whatever
happens to be there by the time it lands.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pyVmomi import vim
from vmware_policy.budget import reset_budget
from vmware_policy.policy import reset_policy_engine
from vmware_policy.undo import reset_undo_store

import vmware_aiops.mcp_server.tools.vm as vm_tools
from vmware_aiops.ops import inventory, vm_lifecycle
from vmware_aiops.ops.inventory import AmbiguousVMError, find_vm_by_name

ON = vim.VirtualMachine.PowerState.poweredOn
OFF = vim.VirtualMachine.PowerState.poweredOff

#: What the preview of the default ``_vm()`` returned as ``acknowledge_with``.
PREVIEWED = {"instance_uuid": "5012-aaaa", "disk_count": 2, "snapshot_count": 2}


@pytest.fixture(autouse=True)
def harness(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_HOME", str(tmp_path))
    reset_policy_engine()
    reset_budget()
    reset_undo_store()
    yield
    reset_policy_engine()
    reset_budget()
    reset_undo_store()


def _disk(label: str, gb: int) -> vim.vm.device.VirtualDisk:
    d = vim.vm.device.VirtualDisk()
    d.deviceInfo = vim.Description(label=label, summary="")
    d.capacityInKB = gb * 1024 * 1024
    d.backing = vim.vm.device.VirtualDisk.FlatVer2BackingInfo(
        fileName="[ds1] x.vmdk", thinProvisioned=True,
    )
    return d


def _snapshots(n: int):
    if n == 0:
        return None
    info = MagicMock()
    info.rootSnapshotList = [MagicMock(childSnapshotList=[]) for _ in range(n)]
    return info


def _vm(name="web-01", power=OFF, disks=(("Hard disk 1", 40), ("Hard disk 2", 100)),
        snapshots=2, instance_uuid="5012-aaaa"):
    vm = MagicMock(name=f"vm:{name}")
    vm.name = name
    vm.runtime.powerState = power
    vm.runtime.host.name = "esx-01"
    if instance_uuid is None:
        vm.config = None
    else:
        vm.config.instanceUuid = instance_uuid
        vm.config.hardware.device = [_disk(label, gb) for label, gb in disks]
    vm.snapshot = _snapshots(snapshots)
    return vm


@pytest.fixture
def inventory_of(monkeypatch):
    """Serve ``_collect`` from a fixed list of VMs; return the list for inspection."""
    def install(*vms):
        monkeypatch.setattr(
            inventory, "_collect",
            lambda _si, _types, _props: [(vm, {"name": vm.name}) for vm in vms],
        )
        monkeypatch.setattr(vm_tools, "_get_connection", lambda _t=None: MagicMock())
        return vms
    return install


# ─── the finder refuses to guess ──────────────────────────────────────────────


def test_find_vm_by_name_refuses_a_duplicated_name(inventory_of):
    inventory_of(_vm("web-01"), _vm("web-01", instance_uuid="5012-bbbb"))
    with pytest.raises(AmbiguousVMError, match="2 VMs are named 'web-01'"):
        find_vm_by_name(MagicMock(), "web-01")


def test_find_vm_by_name_still_returns_a_unique_match_and_none(inventory_of):
    only = _vm("web-01")
    inventory_of(only, _vm("web-02"))
    assert find_vm_by_name(MagicMock(), "web-01") is only
    assert find_vm_by_name(MagicMock(), "nope") is None


def test_ambiguous_name_reaches_the_caller_not_a_generic_failure(inventory_of):
    a, b = inventory_of(_vm("web-01"), _vm("web-01", instance_uuid="5012-bbbb"))
    out = vm_tools.vm_delete("web-01", confirm=True,
                             acknowledge_blast_radius={"instance_uuid": "5012-aaaa"})
    assert "2 VMs are named 'web-01'" in out["error"]
    a.Destroy_Task.assert_not_called()
    b.Destroy_Task.assert_not_called()


# ─── L2: a bare call previews ─────────────────────────────────────────────────


def test_bare_call_previews_and_destroys_nothing(inventory_of):
    (vm,) = inventory_of(_vm())
    out = vm_tools.vm_delete("web-01")
    vm.Destroy_Task.assert_not_called()
    vm.PowerOff.assert_not_called()
    assert out["action"] == "preview"


# ─── L1: the blast radius is stated ───────────────────────────────────────────


def test_preview_states_the_blast_radius(inventory_of):
    inventory_of(_vm())
    br = vm_tools.vm_delete("web-01")["blast_radius"]
    assert br["vm"] == "web-01"
    assert br["instance_uuid"] == "5012-aaaa"
    assert br["host"] == "esx-01"
    assert br["disk_count"] == 2
    assert br["total_disk_gb"] == 140
    assert br["snapshot_count"] == 2
    assert br["blockers"] == []
    assert br["acknowledge_with"] == {
        "instance_uuid": "5012-aaaa", "disk_count": 2, "snapshot_count": 2,
    }


def test_acknowledgement_carries_only_stable_keys(inventory_of):
    """Power state and host drift on their own; echoing them would make the
    acknowledgement fail for reasons unrelated to what is being destroyed."""
    inventory_of(_vm())
    ack = vm_tools.vm_delete("web-01")["blast_radius"]["acknowledge_with"]
    assert set(ack) == {"instance_uuid", "disk_count", "snapshot_count"}


# ─── L3: confirm alone is not enough ──────────────────────────────────────────


def test_confirm_without_acknowledgement_is_refused(inventory_of):
    (vm,) = inventory_of(_vm())
    out = vm_tools.vm_delete("web-01", confirm=True)
    vm.Destroy_Task.assert_not_called()
    assert "acknowledge_blast_radius" in out["error"]


def test_confirm_with_a_stale_acknowledgement_is_refused(inventory_of):
    """The preview said 2 snapshots; someone took a third since."""
    (vm,) = inventory_of(_vm(snapshots=3))
    out = vm_tools.vm_delete(
        "web-01", confirm=True,
        acknowledge_blast_radius=PREVIEWED,
    )
    vm.Destroy_Task.assert_not_called()
    assert "snapshot_count" in out["error"]


def test_confirm_on_a_different_vm_with_the_same_name_is_refused(inventory_of):
    """Deleted and recreated between preview and confirm: same name, new VM."""
    (vm,) = inventory_of(_vm(instance_uuid="5012-cccc"))
    out = vm_tools.vm_delete(
        "web-01", confirm=True,
        acknowledge_blast_radius=PREVIEWED,
    )
    vm.Destroy_Task.assert_not_called()
    assert "instance_uuid" in out["error"]


def test_matching_acknowledgement_destroys_once_and_reports_what(inventory_of, monkeypatch):
    monkeypatch.setattr(vm_lifecycle, "_wait_for_task", lambda _t, **_k: None)
    (vm,) = inventory_of(_vm())
    ack = vm_tools.vm_delete("web-01")["blast_radius"]["acknowledge_with"]
    out = vm_tools.vm_delete("web-01", confirm=True, acknowledge_blast_radius=ack)
    vm.Destroy_Task.assert_called_once()
    assert out["action"] == "deleted"
    assert out["blast_radius"]["total_disk_gb"] == 140


def test_the_vm_destroyed_is_the_vm_that_was_measured(monkeypatch):
    """No second lookup by name between the check and the destroy: a VM
    recreated under the same name in that window must not be the one deleted."""
    monkeypatch.setattr(vm_lifecycle, "_wait_for_task", lambda _t, **_k: None)
    monkeypatch.setattr(vm_tools, "_get_connection", lambda _t=None: MagicMock())
    measured, recreated = _vm(), _vm()
    served = iter([measured, measured, recreated, recreated])
    monkeypatch.setattr(
        inventory, "_collect",
        lambda _si, _types, _props: [(vm := next(served), {"name": vm.name})],
    )
    ack = vm_tools.vm_delete("web-01")["blast_radius"]["acknowledge_with"]
    vm_tools.vm_delete("web-01", confirm=True, acknowledge_blast_radius=ack)
    measured.Destroy_Task.assert_called_once()
    recreated.Destroy_Task.assert_not_called()


def test_powered_on_vm_is_refused_even_when_confirmed(inventory_of):
    """The docstring always said 'must be powered off'; the code powered it off
    for you. A running VM is serving something — deleting it is two decisions."""
    (vm,) = inventory_of(_vm(power=ON))
    preview = vm_tools.vm_delete("web-01")
    assert preview["blast_radius"]["blockers"]
    ack = preview["blast_radius"]["acknowledge_with"]
    out = vm_tools.vm_delete("web-01", confirm=True, acknowledge_blast_radius=ack)
    vm.PowerOff.assert_not_called()
    vm.Destroy_Task.assert_not_called()
    assert "vm_power_off" in out["error"]


def test_unreadable_identity_is_refused(inventory_of):
    (vm,) = inventory_of(_vm(instance_uuid=None))
    preview = vm_tools.vm_delete("web-01")
    assert preview["blast_radius"]["unmeasured"]
    out = vm_tools.vm_delete(
        "web-01", confirm=True,
        acknowledge_blast_radius={"instance_uuid": None, "disk_count": None, "snapshot_count": 0},
    )
    vm.Destroy_Task.assert_not_called()
    assert "Could not read instance_uuid, disks" in out["error"]


# ─── the executor others depend on is unchanged ───────────────────────────────


def test_ops_delete_vm_keeps_its_semantics_for_cli_and_daemon(inventory_of, monkeypatch):
    """The TTL daemon calls ops.delete_vm; it powers off then destroys."""
    monkeypatch.setattr(vm_lifecycle, "_wait_for_task", lambda _t, **_k: None)
    (vm,) = inventory_of(_vm(power=ON))
    vm_lifecycle.delete_vm(MagicMock(), "web-01")
    vm.PowerOff.assert_called_once()
    vm.Destroy_Task.assert_called_once()


# ─── the schema advertises the gate ───────────────────────────────────────────


def test_schema_defaults_confirm_to_false():
    import asyncio

    from vmware_aiops.mcp_server.server import mcp

    tool = next(t for t in asyncio.run(mcp.list_tools()) if t.name == "vm_delete")
    props = tool.inputSchema["properties"]
    assert props["confirm"]["default"] is False
    assert "acknowledge_blast_radius" in props
    assert tool.inputSchema.get("required") == ["vm_name"]


# ─── review findings, 2026-09-19 ──────────────────────────────────────────────


def test_suspended_vm_is_refused(inventory_of):
    """Deleting a suspended VM discards the paused workload in its memory."""
    (vm,) = inventory_of(_vm(power=vim.VirtualMachine.PowerState.suspended))
    preview = vm_tools.vm_delete("web-01")
    out = vm_tools.vm_delete("web-01", confirm=True,
                             acknowledge_blast_radius=preview["blast_radius"]["acknowledge_with"])
    vm.Destroy_Task.assert_not_called()
    assert "suspended" in out["error"]


def test_a_disk_without_device_info_is_unmeasured_not_a_crash(inventory_of):
    vm = _vm()
    vm.config.hardware.device[0].deviceInfo = None
    inventory_of(vm)
    out = vm_tools.vm_delete("web-01", confirm=True, acknowledge_blast_radius=PREVIEWED)
    vm.Destroy_Task.assert_not_called()
    assert "Could not read disks" in out["error"]


def test_only_power_state_unreadable_is_still_refused(inventory_of):
    vm = _vm()
    def unreadable(_self):
        # Not AttributeError: MagicMock would answer that with a fresh mock.
        raise TypeError("unreadable")

    type(vm.runtime).powerState = property(unreadable)
    inventory_of(vm)
    out = vm_tools.vm_delete("web-01", confirm=True, acknowledge_blast_radius=PREVIEWED)
    vm.Destroy_Task.assert_not_called()
    assert "power_state" in out["error"]


def test_a_changed_disk_count_is_refused(inventory_of):
    three = (("Hard disk 1", 40), ("Hard disk 2", 100), ("Hard disk 3", 1))
    (vm,) = inventory_of(_vm(disks=three))
    out = vm_tools.vm_delete("web-01", confirm=True, acknowledge_blast_radius=PREVIEWED)
    vm.Destroy_Task.assert_not_called()
    assert "disk_count" in out["error"]


def test_a_boolean_is_not_a_count(inventory_of):
    (vm,) = inventory_of(_vm(disks=(("Hard disk 1", 40),), snapshots=0))
    ack = {"instance_uuid": "5012-aaaa", "disk_count": True, "snapshot_count": False}
    out = vm_tools.vm_delete("web-01", confirm=True, acknowledge_blast_radius=ack)
    vm.Destroy_Task.assert_not_called()
    assert "error" in out


def test_total_size_counts_disks_beyond_the_listed_cap(inventory_of):
    from vmware_aiops.ops.vm_delete_gate import MAX_LISTED_DISKS

    n = MAX_LISTED_DISKS + 4
    inventory_of(_vm(disks=tuple((f"Hard disk {i}", 1) for i in range(n))))
    br = vm_tools.vm_delete("web-01")["blast_radius"]
    assert len(br["disks"]) == MAX_LISTED_DISKS
    assert br["disk_count"] == n and br["total_disk_gb"] == n


# ─── a plan is not a way around the gate ──────────────────────────────────────


def test_a_plan_delete_step_without_acknowledgement_is_refused(inventory_of):
    from vmware_aiops.ops import plan_executor

    (vm,) = inventory_of(_vm())
    with pytest.raises(Exception, match="acknowledge_blast_radius"):
        plan_executor._dispatch(MagicMock(), "delete_vm", {"vm_name": "web-01"})
    vm.Destroy_Task.assert_not_called()


def test_a_plan_delete_step_on_a_running_vm_is_refused(inventory_of):
    from vmware_aiops.ops import plan_executor

    (vm,) = inventory_of(_vm(power=ON))
    ack = vm_tools.vm_delete("web-01")["blast_radius"]["acknowledge_with"]
    with pytest.raises(Exception, match="powered on"):
        plan_executor._dispatch(MagicMock(), "delete_vm",
                                {"vm_name": "web-01", "acknowledge_blast_radius": ack})
    vm.PowerOff.assert_not_called()
    vm.Destroy_Task.assert_not_called()


def test_a_plan_delete_step_with_the_acknowledgement_deletes(inventory_of, monkeypatch):
    from vmware_aiops.ops import plan_executor

    monkeypatch.setattr(vm_lifecycle, "_wait_for_task", lambda _t, **_k: None)
    (vm,) = inventory_of(_vm())
    out = plan_executor._dispatch(MagicMock(), "delete_vm",
                                  {"vm_name": "web-01", "acknowledge_blast_radius": PREVIEWED})
    vm.Destroy_Task.assert_called_once()
    assert "140.0 GB" in out


def test_a_plan_accepts_the_acknowledgement_parameter():
    from vmware_aiops.ops.planner import _ACTION_SCHEMA

    assert "acknowledge_blast_radius" in _ACTION_SCHEMA["delete_vm"]["optional"]


def test_rolling_back_a_created_vm_still_deletes_it(inventory_of, monkeypatch):
    """Rollback undoes what the plan itself created; it keeps the executor."""
    from vmware_aiops.ops import plan_executor

    monkeypatch.setattr(vm_lifecycle, "_wait_for_task", lambda _t, **_k: None)
    (vm,) = inventory_of(_vm(power=ON))
    plan_executor._rollback_dispatch(MagicMock(), "delete_vm", {"vm_name": "web-01"})
    vm.Destroy_Task.assert_called_once()
