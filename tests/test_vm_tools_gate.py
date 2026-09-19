"""HLD §7 gate on vm_power_off, vm_migrate, vm_revert_snapshot, vm_delete_snapshot.

vm_delete is the reference (tests/test_vm_delete_blast_radius.py). These four
take ``confirm: bool = False`` and no acknowledgement — Pilot drives them with
``confirm=True`` alone after its own approval step:

* L2 — a bare call previews and calls no write API.
* L1 — preview and acting response both carry ``blast_radius``.
* L3 — ``confirm=True`` refuses on a blocker or an unreadable field.

The undo token of vm_power_off ("power it back on") must only be filed when the
call actually powered the VM off — never for a preview or a no-op.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pyVmomi import vim
from vmware_policy.budget import reset_budget
from vmware_policy.policy import reset_policy_engine
from vmware_policy.undo import get_undo_store, reset_undo_store

import vmware_aiops.mcp_server.tools.vm as vm_tools
from vmware_aiops.ops import inventory, vm_lifecycle

ON = vim.VirtualMachine.PowerState.poweredOn
OFF = vim.VirtualMachine.PowerState.poweredOff
SUSPENDED = vim.VirtualMachine.PowerState.suspended


@pytest.fixture(autouse=True)
def harness(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_HOME", str(tmp_path))
    monkeypatch.setattr(vm_lifecycle, "_wait_for_task", lambda _t, **_k: None)
    monkeypatch.setattr(vm_lifecycle.time, "sleep", lambda _s: None)
    reset_policy_engine()
    reset_budget()
    reset_undo_store()
    yield
    reset_policy_engine()
    reset_budget()
    reset_undo_store()


@pytest.fixture
def audit_rows(monkeypatch):
    rows: list[dict] = []

    class _Recorder:
        def log(self, **kw):
            rows.append(kw)

    monkeypatch.setattr("vmware_policy.guard.get_engine", lambda: _Recorder())
    return rows


def _unreadable(_self):
    # Not AttributeError: MagicMock would answer that with a fresh mock.
    raise TypeError("unreadable")


def _make_unreadable(obj, attr):
    """Make ``obj.attr`` raise on read (per-instance: MagicMock subclasses per mock)."""
    setattr(type(obj), attr, property(_unreadable))


# ─── inventory builders ──────────────────────────────────────────────────────


def _host(name="esx-02", state="connected", maintenance=False, pool=True):
    h = MagicMock(name=f"host:{name}")
    h.name = name
    h.runtime.connectionState = state
    h.runtime.inMaintenanceMode = maintenance
    h.parent.resourcePool = MagicMock() if pool else None
    return h


def _datastore(name, hosts):
    ds = MagicMock(name=f"ds:{name}")
    ds.name = name
    ds.host = [MagicMock(key=h) for h in hosts]
    return ds


def _snap(name, moid, children=(), state=OFF, created="2026-09-01 10:00:00"):
    node = MagicMock(name=f"snap:{name}")
    node.name = name
    node.description = f"{name} desc"
    node.createTime = created
    node.state = state
    node.snapshot = MagicMock(name=f"snapref:{moid}")
    node.snapshot._moId = moid
    node.childSnapshotList = list(children)
    return node


def _tree(*roots, current=None):
    info = MagicMock()
    info.rootSnapshotList = list(roots)
    info.currentSnapshot = current
    return info


def _default_tree():
    """baseline ── pre-upgrade ── post-upgrade ; and a second root 'other'."""
    post = _snap("post-upgrade", "snapshot-3")
    pre = _snap("pre-upgrade", "snapshot-2", children=[post])
    base = _snap("baseline", "snapshot-1", children=[pre])
    other = _snap("other", "snapshot-4")
    return _tree(base, other, current=post.snapshot)


def _vm(name="web-01", power=ON, tools="guestToolsRunning", host=None,
        datastores=None, snapshots="default", instance_uuid="5012-aaaa"):
    vm = MagicMock(name=f"vm:{name}")
    vm.name = name
    vm.runtime.powerState = power
    vm.runtime.host = host if host is not None else _host("esx-01")
    vm.guest.toolsRunningStatus = tools
    vm.config.instanceUuid = instance_uuid
    vm.datastore = datastores if datastores is not None else []
    vm.snapshot = _default_tree() if snapshots == "default" else snapshots
    return vm


@pytest.fixture
def inventory_of(monkeypatch):
    """Serve ``_collect`` by type from fixed lists of VMs, hosts and datastores."""
    def install(vms=(), hosts=(), datastores=()):
        by_type = {vim.VirtualMachine: vms, vim.HostSystem: hosts, vim.Datastore: datastores}

        def collect(_si, types, _props):
            return [(o, {"name": o.name}) for t in types for o in by_type.get(t, ())]

        monkeypatch.setattr(inventory, "_collect", collect)
        monkeypatch.setattr(vm_tools, "_get_connection", lambda _t=None: MagicMock())
    return install


def _schema(name):
    from vmware_aiops.mcp_server.server import mcp

    return next(t for t in asyncio.run(mcp.list_tools()) if t.name == name).inputSchema


@pytest.mark.parametrize("tool", [
    "vm_power_off", "vm_migrate", "vm_revert_snapshot", "vm_delete_snapshot",
])
def test_schema_defaults_confirm_to_false(tool):
    schema = _schema(tool)
    assert schema["properties"]["confirm"]["default"] is False
    assert "confirm" not in schema.get("required", [])
    assert "acknowledge_blast_radius" not in schema["properties"]


# ═══ vm_power_off ════════════════════════════════════════════════════════════


def _shuts_down(vm):
    def go(*_a, **_k):
        vm.runtime.powerState = OFF
    return go


def test_power_off_bare_call_previews_and_writes_nothing(inventory_of):
    vm = _vm()
    inventory_of(vms=[vm])
    out = vm_tools.vm_power_off("web-01")
    vm.ShutdownGuest.assert_not_called()
    vm.PowerOff.assert_not_called()
    assert out["action"] == "preview"
    br = out["blast_radius"]
    assert br["vm"] == "web-01"
    assert br["instance_uuid"] == "5012-aaaa"
    assert br["host"] == "esx-01"
    assert br["power_state"] == "poweredOn"
    assert br["tools_status"] == "guestToolsRunning"
    assert br["mode"] == "guest_shutdown"
    assert br["blockers"] == [] and br["unmeasured"] == []


def test_power_off_force_preview_says_hard_power_off(inventory_of):
    vm = _vm()
    inventory_of(vms=[vm])
    br = vm_tools.vm_power_off("web-01", force=True)["blast_radius"]
    vm.PowerOff.assert_not_called()
    assert br["mode"] == "hard_power_off"


@pytest.mark.parametrize("confirm", [False, True])
def test_power_off_already_off_is_a_noop(inventory_of, confirm):
    vm = _vm(power=OFF)
    inventory_of(vms=[vm])
    out = vm_tools.vm_power_off("web-01", confirm=confirm)
    assert out["action"] == "noop"
    assert out["blast_radius"]["power_state"] == "poweredOff"
    vm.ShutdownGuest.assert_not_called()
    vm.PowerOff.assert_not_called()


def test_power_off_confirmed_graceful_shuts_down_once(inventory_of):
    vm = _vm()
    vm.ShutdownGuest.side_effect = _shuts_down(vm)
    inventory_of(vms=[vm])
    out = vm_tools.vm_power_off("web-01", confirm=True)
    vm.ShutdownGuest.assert_called_once()
    vm.PowerOff.assert_not_called()
    assert out["action"] == "powered_off"
    assert out["blast_radius"]["mode"] == "guest_shutdown"


def test_power_off_confirmed_force_powers_off_once(inventory_of):
    vm = _vm()
    vm.PowerOff.side_effect = _shuts_down(vm)
    inventory_of(vms=[vm])
    out = vm_tools.vm_power_off("web-01", force=True, confirm=True)
    vm.PowerOff.assert_called_once()
    assert out["action"] == "powered_off"
    assert out["blast_radius"]["mode"] == "hard_power_off"


def test_power_off_graceful_without_tools_is_refused(inventory_of):
    vm = _vm(tools="guestToolsNotRunning")
    inventory_of(vms=[vm])
    assert vm_tools.vm_power_off("web-01")["blast_radius"]["blockers"]
    out = vm_tools.vm_power_off("web-01", confirm=True)
    vm.ShutdownGuest.assert_not_called()
    vm.PowerOff.assert_not_called()
    assert "force=True" in out["error"]


def test_power_off_graceful_on_a_suspended_vm_is_refused(inventory_of):
    vm = _vm(power=SUSPENDED)
    inventory_of(vms=[vm])
    out = vm_tools.vm_power_off("web-01", confirm=True)
    vm.ShutdownGuest.assert_not_called()
    vm.PowerOff.assert_not_called()
    assert "suspended" in out["error"]


def test_power_off_unreadable_power_state_is_refused(inventory_of):
    vm = _vm()
    _make_unreadable(vm.runtime, "powerState")
    inventory_of(vms=[vm])
    out = vm_tools.vm_power_off("web-01", force=True, confirm=True)
    vm.PowerOff.assert_not_called()
    assert "power_state" in out["error"]


def test_power_off_unreadable_identity_is_refused(inventory_of):
    vm = _vm()
    _make_unreadable(vm.config, "instanceUuid")
    inventory_of(vms=[vm])
    out = vm_tools.vm_power_off("web-01", force=True, confirm=True)
    vm.PowerOff.assert_not_called()
    assert "instance_uuid" in out["error"]


def test_power_off_unreadable_tools_refuses_graceful_but_not_force(inventory_of):
    """Tools status only matters for a guest shutdown."""
    vm = _vm()
    _make_unreadable(vm.guest, "toolsRunningStatus")
    vm.PowerOff.side_effect = _shuts_down(vm)
    inventory_of(vms=[vm])
    out = vm_tools.vm_power_off("web-01", confirm=True)
    vm.ShutdownGuest.assert_not_called()
    assert "tools_status" in out["error"]
    out = vm_tools.vm_power_off("web-01", force=True, confirm=True)
    vm.PowerOff.assert_called_once()
    assert out["action"] == "powered_off"


def test_power_off_refusal_is_audited_as_a_failure(inventory_of, audit_rows):
    inventory_of(vms=[_vm(tools="guestToolsNotRunning")])
    vm_tools.vm_power_off("web-01", confirm=True)
    assert audit_rows[-1]["status"] == "error"


# ── the undo token follows the act, not the call ─────────────────────────────


def test_power_off_preview_files_no_undo_token(inventory_of):
    inventory_of(vms=[_vm()])
    vm_tools.vm_power_off("web-01")
    assert get_undo_store().list() == [], "a preview filed 'power it back on'"


def test_power_off_noop_files_no_undo_token(inventory_of):
    """Powering on a VM that was already off is not the inverse of anything."""
    inventory_of(vms=[_vm(power=OFF)])
    vm_tools.vm_power_off("web-01", confirm=True)
    assert get_undo_store().list() == []


def test_power_off_that_acted_files_its_undo_token(inventory_of):
    """Control: otherwise 'no token' above passes if undo is dead."""
    vm = _vm()
    vm.PowerOff.side_effect = _shuts_down(vm)
    inventory_of(vms=[vm])
    out = vm_tools.vm_power_off("web-01", force=True, confirm=True)
    tokens = get_undo_store().list()
    assert len(tokens) == 1 and tokens[0]["tool"] == "vm_power_off"
    assert out["_undo_id"]


def test_power_off_that_did_not_finish_files_no_undo_token(inventory_of):
    """Guest shutdown initiated but the VM is still on after the wait."""
    vm = _vm()
    inventory_of(vms=[vm])
    out = vm_tools.vm_power_off("web-01", confirm=True)
    vm.ShutdownGuest.assert_called_once()
    assert out["action"] == "still_running"
    assert get_undo_store().list() == []


# ═══ vm_migrate ══════════════════════════════════════════════════════════════


def _migration_world(power=ON, target_kwargs=None, shared=True):
    src = _host("esx-01")
    dst = _host("esx-02", **(target_kwargs or {}))
    ds1 = _datastore("ds-shared", [src, dst] if shared else [src])
    ds2 = _datastore("ds-other", [dst])
    vm = _vm(power=power, host=src, datastores=[ds1])
    return vm, src, dst, ds1, ds2


def test_migrate_bare_call_previews_and_writes_nothing(inventory_of):
    vm, src, dst, ds1, ds2 = _migration_world()
    inventory_of(vms=[vm], hosts=[src, dst], datastores=[ds1, ds2])
    out = vm_tools.vm_migrate("web-01", "esx-02")
    vm.Relocate.assert_not_called()
    assert out["action"] == "preview"
    br = out["blast_radius"]
    assert br["vm"] == "web-01" and br["instance_uuid"] == "5012-aaaa"
    assert br["source_host"] == "esx-01"
    assert br["source_datastores"] == ["ds-shared"]
    assert br["target_host"] == {"name": "esx-02", "connection_state": "connected",
                                 "in_maintenance_mode": False}
    assert br["target_datastore"] is None
    assert br["power_state"] == "poweredOn"
    assert br["migration"] == "live vMotion"
    assert br["blockers"] == [] and br["unmeasured"] == []


def test_migrate_preview_names_cold_and_storage_migration(inventory_of):
    vm, src, dst, ds1, ds2 = _migration_world(power=OFF)
    inventory_of(vms=[vm], hosts=[src, dst], datastores=[ds1, ds2])
    br = vm_tools.vm_migrate("web-01", "esx-02", to_datastore="ds-other")["blast_radius"]
    assert br["migration"] == "cold migration + storage migration"
    assert br["target_datastore"] == "ds-other"


def test_migrate_confirmed_relocates_once(inventory_of, monkeypatch):
    # RelocateSpec type-checks its fields; the hosts here are mocks.
    monkeypatch.setattr(vim.vm, "RelocateSpec", lambda **kw: SimpleNamespace(**kw))
    vm, src, dst, ds1, ds2 = _migration_world()
    inventory_of(vms=[vm], hosts=[src, dst], datastores=[ds1, ds2])
    out = vm_tools.vm_migrate("web-01", "esx-02", confirm=True)
    vm.Relocate.assert_called_once()
    assert vm.Relocate.call_args.kwargs["spec"].host is dst
    assert out["action"] == "migrated"
    assert out["blast_radius"]["target_host"]["name"] == "esx-02"


@pytest.mark.parametrize("confirm", [False, True])
def test_migrate_to_the_current_host_is_a_noop(inventory_of, confirm):
    vm, src, dst, ds1, ds2 = _migration_world()
    inventory_of(vms=[vm], hosts=[src, dst], datastores=[ds1, ds2])
    out = vm_tools.vm_migrate("web-01", "esx-01", confirm=confirm)
    vm.Relocate.assert_not_called()
    assert out["action"] == "noop"


@pytest.mark.parametrize("world, to_host, to_ds, expect", [
    ({}, "esx-99", None, "esx-99"),
    ({"target_kwargs": {"state": "disconnected"}}, "esx-02", None, "disconnected"),
    ({"target_kwargs": {"maintenance": True}}, "esx-02", None, "maintenance mode"),
    ({"target_kwargs": {"pool": False}}, "esx-02", None, "resource pool"),
    ({}, "esx-02", "ds-nope", "ds-nope"),
    ({"shared": False}, "esx-02", None, "to_datastore"),
], ids=["host-not-found", "disconnected", "maintenance", "no-pool",
       "ds-not-found", "no-shared-storage"])
def test_migrate_blockers_refuse(inventory_of, world, to_host, to_ds, expect):
    vm, src, dst, ds1, ds2 = _migration_world(**world)
    inventory_of(vms=[vm], hosts=[src, dst], datastores=[ds1, ds2])
    preview = vm_tools.vm_migrate("web-01", to_host, to_datastore=to_ds)
    assert preview["blast_radius"]["blockers"]
    out = vm_tools.vm_migrate("web-01", to_host, to_datastore=to_ds, confirm=True)
    vm.Relocate.assert_not_called()
    assert expect in out["error"]


def test_migrate_unreadable_target_maintenance_state_is_refused(inventory_of):
    vm, src, dst, ds1, ds2 = _migration_world()
    _make_unreadable(dst.runtime, "inMaintenanceMode")
    inventory_of(vms=[vm], hosts=[src, dst], datastores=[ds1, ds2])
    out = vm_tools.vm_migrate("web-01", "esx-02", confirm=True)
    vm.Relocate.assert_not_called()
    assert "target_in_maintenance_mode" in out["error"]


def test_migrate_unreadable_storage_access_is_refused(inventory_of):
    vm, src, dst, ds1, ds2 = _migration_world()
    _make_unreadable(ds1, "host")
    inventory_of(vms=[vm], hosts=[src, dst], datastores=[ds1, ds2])
    out = vm_tools.vm_migrate("web-01", "esx-02", confirm=True)
    vm.Relocate.assert_not_called()
    assert "storage_access" in out["error"]


def test_migrate_a_vm_without_a_current_host_is_refused(inventory_of):
    vm, src, dst, ds1, ds2 = _migration_world()
    vm.runtime.host = None
    inventory_of(vms=[vm], hosts=[src, dst], datastores=[ds1, ds2])
    # The executor refuses too; the preview is where the gate itself shows.
    assert "no current host" in " ".join(
        vm_tools.vm_migrate("web-01", "esx-02")["blast_radius"]["blockers"])
    out = vm_tools.vm_migrate("web-01", "esx-02", confirm=True)
    vm.Relocate.assert_not_called()
    assert "no current host" in out["error"]


# ═══ vm_revert_snapshot ══════════════════════════════════════════════════════


def _snapshot_nodes(vm):
    base = vm.snapshot.rootSnapshotList[0]
    pre = base.childSnapshotList[0]
    post = pre.childSnapshotList[0]
    return base, pre, post


def test_revert_bare_call_previews_and_writes_nothing(inventory_of):
    vm = _vm()
    inventory_of(vms=[vm])
    out = vm_tools.vm_revert_snapshot("web-01", "pre-upgrade")
    for node in _snapshot_nodes(vm):
        node.snapshot.RevertToSnapshot_Task.assert_not_called()
    assert out["action"] == "preview"
    br = out["blast_radius"]
    assert br["vm"] == "web-01" and br["instance_uuid"] == "5012-aaaa"
    assert br["snapshot"] == {"name": "pre-upgrade", "id": "snapshot-2",
                              "created": "2026-09-01 10:00:00", "level": 1}
    assert br["snapshot_count"] == 4
    assert br["power_state"] == "poweredOn"
    assert br["power_state_after"] == "poweredOff"
    assert br["is_current_snapshot"] is False
    assert "discard" in br["effect"]
    assert br["blockers"] == [] and br["unmeasured"] == []


def test_revert_confirmed_reverts_the_named_snapshot_once(inventory_of):
    vm = _vm()
    inventory_of(vms=[vm])
    base, pre, post = _snapshot_nodes(vm)
    out = vm_tools.vm_revert_snapshot("web-01", "pre-upgrade", confirm=True)
    pre.snapshot.RevertToSnapshot_Task.assert_called_once()
    base.snapshot.RevertToSnapshot_Task.assert_not_called()
    post.snapshot.RevertToSnapshot_Task.assert_not_called()
    assert out["action"] == "reverted"
    assert out["blast_radius"]["snapshot"]["id"] == "snapshot-2"


def test_revert_to_a_missing_snapshot_is_refused(inventory_of):
    vm = _vm()
    inventory_of(vms=[vm])
    assert vm_tools.vm_revert_snapshot("web-01", "nope")["blast_radius"]["blockers"]
    out = vm_tools.vm_revert_snapshot("web-01", "nope", confirm=True)
    assert "nope" in out["error"] and "vm_list_snapshots" in out["error"]


def test_revert_on_a_vm_without_snapshots_is_refused(inventory_of):
    inventory_of(vms=[_vm(snapshots=None)])
    out = vm_tools.vm_revert_snapshot("web-01", "baseline", confirm=True)
    assert "no snapshots" in out["error"]


def test_revert_to_an_ambiguous_snapshot_name_is_refused(inventory_of):
    """vSphere allows two snapshots with the same name; do not pick the first."""
    a = _snap("nightly", "snapshot-10", created="2026-09-01")
    b = _snap("nightly", "snapshot-11", created="2026-09-02")
    a.childSnapshotList = [b]
    vm = _vm(snapshots=_tree(a))
    inventory_of(vms=[vm])
    br = vm_tools.vm_revert_snapshot("web-01", "nightly")["blast_radius"]
    assert br["blockers"] and br["matches"] == 2
    out = vm_tools.vm_revert_snapshot("web-01", "nightly", confirm=True)
    a.snapshot.RevertToSnapshot_Task.assert_not_called()
    b.snapshot.RevertToSnapshot_Task.assert_not_called()
    assert "2 snapshots" in out["error"]


def test_revert_with_an_unreadable_snapshot_tree_is_refused(inventory_of):
    vm = _vm()
    base, _pre, _post = _snapshot_nodes(vm)
    _make_unreadable(base, "childSnapshotList")
    inventory_of(vms=[vm])
    out = vm_tools.vm_revert_snapshot("web-01", "baseline", confirm=True)
    base.snapshot.RevertToSnapshot_Task.assert_not_called()
    assert "snapshot_tree" in out["error"]


def test_revert_unreadable_power_state_is_refused(inventory_of):
    vm = _vm()
    _make_unreadable(vm.runtime, "powerState")
    inventory_of(vms=[vm])
    base, _pre, _post = _snapshot_nodes(vm)
    out = vm_tools.vm_revert_snapshot("web-01", "baseline", confirm=True)
    base.snapshot.RevertToSnapshot_Task.assert_not_called()
    assert "power_state" in out["error"]


# ═══ vm_delete_snapshot ══════════════════════════════════════════════════════


def test_delete_snapshot_bare_call_previews_and_writes_nothing(inventory_of):
    vm = _vm()
    inventory_of(vms=[vm])
    out = vm_tools.vm_delete_snapshot("web-01", "baseline")
    for node in _snapshot_nodes(vm):
        node.snapshot.RemoveSnapshot_Task.assert_not_called()
    assert out["action"] == "preview"
    br = out["blast_radius"]
    assert br["snapshot"]["id"] == "snapshot-1"
    assert br["remove_children"] is False
    assert br["child_snapshot_count"] == 2
    assert br["children"] == ["pre-upgrade", "post-upgrade"]
    assert br["snapshots_removed"] == 1
    assert br["snapshot_count"] == 4
    assert br["blockers"] == [] and br["unmeasured"] == []


def test_delete_snapshot_with_children_counts_the_subtree(inventory_of):
    inventory_of(vms=[_vm()])
    br = vm_tools.vm_delete_snapshot("web-01", "baseline", remove_children=True)["blast_radius"]
    assert br["remove_children"] is True
    assert br["snapshots_removed"] == 3


def test_delete_snapshot_confirmed_removes_once(inventory_of):
    vm = _vm()
    inventory_of(vms=[vm])
    base, pre, _post = _snapshot_nodes(vm)
    pre.snapshot.RemoveSnapshot_Task.return_value._moId = "task-7"
    out = vm_tools.vm_delete_snapshot("web-01", "pre-upgrade", remove_children=True, confirm=True)
    pre.snapshot.RemoveSnapshot_Task.assert_called_once_with(removeChildren=True)
    base.snapshot.RemoveSnapshot_Task.assert_not_called()
    assert out["action"] == "snapshot_delete_started"
    assert "task-7" in out["result"]
    assert out["blast_radius"]["snapshots_removed"] == 2


def test_delete_snapshot_missing_is_refused(inventory_of):
    inventory_of(vms=[_vm()])
    out = vm_tools.vm_delete_snapshot("web-01", "nope", confirm=True)
    assert "nope" in out["error"] and "vm_list_snapshots" in out["error"]


def test_delete_snapshot_ambiguous_name_is_refused(inventory_of):
    a = _snap("nightly", "snapshot-10")
    b = _snap("nightly", "snapshot-11")
    vm = _vm(snapshots=_tree(a, b))
    inventory_of(vms=[vm])
    out = vm_tools.vm_delete_snapshot("web-01", "nightly", confirm=True)
    a.snapshot.RemoveSnapshot_Task.assert_not_called()
    b.snapshot.RemoveSnapshot_Task.assert_not_called()
    assert "2 snapshots" in out["error"]


def test_delete_snapshot_lists_children_up_to_the_cap(inventory_of):
    from vmware_aiops.ops.vm_gate import MAX_LISTED

    kids = [_snap(f"c{i}", f"snapshot-c{i}") for i in range(MAX_LISTED + 5)]
    root = _snap("root", "snapshot-r", children=kids)
    inventory_of(vms=[_vm(snapshots=_tree(root))])
    br = vm_tools.vm_delete_snapshot("web-01", "root", remove_children=True)["blast_radius"]
    assert len(br["children"]) == MAX_LISTED
    assert br["child_snapshot_count"] == MAX_LISTED + 5
    assert br["snapshots_removed"] == MAX_LISTED + 6


def test_delete_snapshot_unreadable_tree_is_refused(inventory_of):
    vm = _vm()
    _make_unreadable(vm, "snapshot")
    inventory_of(vms=[vm])
    out = vm_tools.vm_delete_snapshot("web-01", "baseline", confirm=True)
    assert "snapshot_tree" in out["error"]


def test_snapshot_refusal_is_audited_as_a_failure(inventory_of, audit_rows):
    inventory_of(vms=[_vm()])
    vm_tools.vm_delete_snapshot("web-01", "nope", confirm=True)
    assert audit_rows[-1]["status"] == "error"


# ═══ the world changed between the measurement and the act ══════════════════


@pytest.mark.parametrize("tool, executor, args, said", [
    ("vm_migrate", "migrate_vm", ("web-01", "esx-02"),
     "Target host 'esx-02' not found."),
    ("vm_revert_snapshot", "revert_to_snapshot", ("web-01", "baseline"),
     "Snapshot 'baseline' not found. Available: none"),
    ("vm_delete_snapshot", "delete_snapshot", ("web-01", "baseline"),
     "Snapshot 'baseline' not found. Available: none"),
])
def test_an_executor_refusal_after_the_gate_is_an_error(
    inventory_of, audit_rows, monkeypatch, tool, executor, args, said,
):
    """The executors answer some refusals with a string; that is not success."""
    vm, src, dst, ds1, ds2 = _migration_world()
    inventory_of(vms=[vm], hosts=[src, dst], datastores=[ds1, ds2])
    monkeypatch.setattr(vm_tools, executor, lambda *_a, **_k: said)
    out = getattr(vm_tools, tool)(*args, confirm=True)
    assert said in out["error"] and "did not act" in out["error"]
    assert audit_rows[-1]["status"] == "error"
