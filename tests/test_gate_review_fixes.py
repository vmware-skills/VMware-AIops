"""Findings from the independent review of the HLD §7 gate (2026-09-19).

Each test reproduces one finding against the gate as first written:

* H1 — a plan created against the default target could be applied to another.
* H2 — the plan preview hid step parameters, and a plan bypassed per-tool L3.
* H3 — a guest command past the preview cap was shown truncated and run in full.
* M1 — a refusal with several blockers lost its remedy to the 500-char cap.
* M3 — clean slate reported success after only powering off; measurement and
  executors disagreed about which snapshot a name meant.
* M4 — a TTL deleted whatever VM had the name at expiry.
* M5 — plan rollback deleted a created VM by name without checking identity.
* L1 — plan responses echoed step passwords.
* L2 — network/DRS previews had no blockers/unmeasured.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from pyVmomi import vim
from vmware_policy.budget import reset_budget
from vmware_policy.policy import reset_policy_engine
from vmware_policy.undo import reset_undo_store

import vmware_aiops.mcp_server.tools.guest as guest_tools
import vmware_aiops.mcp_server.tools.plan as plan_tools
import vmware_aiops.mcp_server.tools.ttl as ttl_tools
import vmware_aiops.mcp_server.tools.vm as vm_tools
from vmware_aiops.ops import gate as gate_mod
from vmware_aiops.ops import inventory, plan_executor, planner, ttl, vm_lifecycle
from vmware_aiops.ops.gate import GateRefusedError, refuse_on

#: What ``_safe_error`` passes through is capped at 500; the remedy must fit.
MAX_REFUSAL_CHARS = 480

ON = vim.VirtualMachine.PowerState.poweredOn
OFF = vim.VirtualMachine.PowerState.poweredOff
PASSWORD = "s3cret-Plan-Pw!"


@pytest.fixture(autouse=True)
def harness(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_HOME", str(tmp_path))
    reset_policy_engine()
    reset_budget()
    reset_undo_store()
    si = MagicMock(name="si")
    for mod in (plan_tools, ttl_tools, vm_tools, guest_tools):
        monkeypatch.setattr(mod, "_get_connection", lambda _t=None: si)
    monkeypatch.setattr(vm_lifecycle, "_wait_for_task", lambda _t, **_k: None)
    monkeypatch.setattr(ttl, "_TTL_FILE", tmp_path / "ttl.json")
    monkeypatch.setattr(planner, "_PLANS_DIR", tmp_path / "plans")
    yield
    reset_policy_engine()
    reset_budget()
    reset_undo_store()


# ─── builders ─────────────────────────────────────────────────────────────────


def _snap(name, moid, children=()):
    node = MagicMock(name=f"snap:{moid}")
    node.name = name
    node.description = ""
    node.createTime = "2026-09-01 10:00:00"
    node.state = OFF
    node.snapshot = MagicMock(name=f"snapref:{moid}")
    node.snapshot._moId = moid
    node.childSnapshotList = list(children)
    return node


def _vm(name, power=ON, tools="guestToolsRunning", uuid=None, snaps=()):
    vm = MagicMock(name=f"vm:{name}")
    vm.name = name
    vm.runtime.powerState = power
    vm.runtime.host.name = "esx-01"
    vm.guest.toolsRunningStatus = tools
    vm.guest.guestFamily = "linuxGuest"
    vm.config.instanceUuid = uuid or f"uuid-{name}"
    vm.config.hardware.device = []
    if snaps:
        vm.snapshot.rootSnapshotList = list(snaps)
        vm.snapshot.currentSnapshot = None
    else:
        vm.snapshot = None
    return vm


@pytest.fixture
def served(monkeypatch):
    """Serve these VMs by name to every inventory lookup."""
    def install(*vms):
        monkeypatch.setattr(
            inventory, "_collect",
            lambda _si, types, _p: [(v, {"name": v.name}) for v in vms]
            if vim.VirtualMachine in types else [],
        )
        return vms
    return install


def _write_plan(steps, status="pending", target="vc01", plan_id="plan-r-0001"):
    planner._PLANS_DIR.mkdir(parents=True, exist_ok=True)
    plan = {
        "plan_id": plan_id, "created_at": "2026-09-19T00:00:00+00:00",
        "target": target, "status": status,
        "steps": steps, "summary": {"total_steps": len(steps)},
    }
    (planner._PLANS_DIR / f"{plan_id}.json").write_text(json.dumps(plan), encoding="utf-8")
    return plan_id


def _step(index, action, params, status="pending"):
    rb_action, rb_params = planner._build_rollback(action, params)
    return {
        "index": index, "action": action, "params": params,
        "rollback_action": rb_action, "rollback_params": rb_params,
        "status": status, "result": None, "executed_at": None,
    }


def _ack(vm):
    return {"instance_uuid": vm.config.instanceUuid, "disk_count": 0, "snapshot_count": 0}


@pytest.fixture
def dispatched(monkeypatch):
    calls: list = []
    monkeypatch.setattr(plan_executor, "_dispatch",
                        lambda _si, action, params: calls.append((action, params)) or "ok")
    return calls


# ═══ H1: None is a target of its own ═══════════════════════════════════════════


def test_plan_made_on_the_default_target_is_refused_on_a_named_one(served, dispatched):
    served(_vm("web-01"))
    pid = _write_plan([_step(0, "power_on", {"vm_name": "web-01"})], target=None)
    assert plan_tools.vm_apply_plan(pid, target="prod")["blast_radius"]["blockers"]
    out = plan_tools.vm_apply_plan(pid, confirm=True, target="prod")
    assert dispatched == []
    assert "default target" in out["error"]


def test_plan_made_on_the_default_target_applies_on_the_default(served, dispatched):
    served(_vm("web-01"))
    pid = _write_plan([_step(0, "power_on", {"vm_name": "web-01"})], target=None)
    out = plan_tools.vm_apply_plan(pid, confirm=True)
    assert out["status"] == "completed" and dispatched


def test_rollback_of_a_default_target_plan_is_refused_on_a_named_one(dispatched, monkeypatch):
    undone: list = []
    monkeypatch.setattr(plan_executor, "_rollback_dispatch",
                        lambda _si, a, p: undone.append(a) or "ok")
    pid = _write_plan([_step(0, "power_on", {"vm_name": "a"}, status="success"),
                       _step(1, "power_off", {"vm_name": "b"}, status="failed")],
                      status="failed", target=None)
    out = plan_tools.vm_rollback_plan(pid, confirm=True, target="prod")
    assert undone == [] and "default target" in out["error"]


# ═══ H2: the preview shows what runs, and each step meets its tool's L3 ══════════


def test_plan_preview_shows_every_parameter_with_the_password_redacted(served, dispatched):
    served(_vm("db-01"))
    pid = _write_plan([
        _step(0, "guest_exec", {"vm_name": "db-01", "command": "/bin/sh",
                                "arguments": "-c 'rm -rf /var/lib/mysql'",
                                "username": "root", "password": PASSWORD}),
        _step(1, "delete_snapshot", {"vm_name": "db-01", "snapshot_name": "s",
                                     "remove_children": True}),
    ])
    out = plan_tools.vm_apply_plan(pid, target="vc01")
    steps = out["blast_radius"]["steps"]
    assert steps[0]["params"]["arguments"] == "-c 'rm -rf /var/lib/mysql'"
    assert steps[0]["params"]["username"] == "root"
    assert steps[0]["params"]["password"] != PASSWORD
    assert steps[1]["params"]["remove_children"] is True
    assert PASSWORD not in repr(out)


def test_plan_step_blocked_by_its_tools_gate_refuses_the_whole_plan(served, dispatched):
    """vm_power_off refuses a graceful shutdown without Tools; so must the plan."""
    served(_vm("web-01", tools="guestToolsNotRunning"))
    pid = _write_plan([
        _step(0, "power_on", {"vm_name": "other"}),
        _step(1, "power_off", {"vm_name": "web-01"}),
    ])
    br = plan_tools.vm_apply_plan(pid, target="vc01")["blast_radius"]
    assert any(b.startswith("Step 1") and "VMware Tools" in b for b in br["blockers"])
    out = plan_tools.vm_apply_plan(pid, confirm=True, target="vc01")
    assert dispatched == []
    assert "Step 1" in out["error"]


def test_plan_delete_of_a_running_vm_is_refused_before_step_zero(served, dispatched):
    vm = served(_vm("old-01", power=ON))[0]
    pid = _write_plan([
        _step(0, "power_on", {"vm_name": "x"}),
        _step(1, "delete_vm", {"vm_name": "old-01", "acknowledge_blast_radius": _ack(vm)}),
    ])
    out = plan_tools.vm_apply_plan(pid, confirm=True, target="vc01")
    assert dispatched == []
    assert "Step 1" in out["error"] and "powered on" in out["error"]


def test_plan_step_measurement_that_cannot_read_refuses(served, dispatched):
    vm = _vm("web-01")
    vm.config = None
    served(vm)
    pid = _write_plan([_step(0, "power_off", {"vm_name": "web-01", "force": True})])
    br = plan_tools.vm_apply_plan(pid, target="vc01")["blast_radius"]
    assert any("Step 0" in u and "instance_uuid" in u for u in br["unmeasured"])
    out = plan_tools.vm_apply_plan(pid, confirm=True, target="vc01")
    assert dispatched == [] and "error" in out


def test_plan_step_on_a_missing_vm_is_a_blocker(served, dispatched):
    served()
    pid = _write_plan([_step(0, "revert_snapshot", {"vm_name": "ghost", "snapshot_name": "s"})])
    br = plan_tools.vm_apply_plan(pid, target="vc01")["blast_radius"]
    assert any(b.startswith("Step 0") and "not found" in b for b in br["blockers"])


def test_power_off_then_delete_defers_the_delete_check(served, dispatched):
    vm = served(_vm("old-01", power=ON))[0]
    pid = _write_plan([
        _step(0, "power_off", {"vm_name": "old-01", "force": True}),
        _step(1, "delete_vm", {"vm_name": "old-01", "acknowledge_blast_radius": _ack(vm)}),
    ])
    br = plan_tools.vm_apply_plan(pid, target="vc01")["blast_radius"]
    assert br["blockers"] == [] and br["unmeasured"] == []
    assert br["steps"][1]["check"] == "deferred"
    assert br["steps"][1]["deferred_until_after_step"] == 0
    assert "deferred to step 1" in br["steps"][1]["check_note"]
    assert br["steps"][0]["check"] == "measured"


def test_a_step_on_a_vm_an_earlier_step_creates_is_deferred(served, dispatched):
    served()
    pid = _write_plan([
        _step(0, "create_vm", {"vm_name": "new-01"}),
        _step(1, "guest_exec", {"vm_name": "new-01", "command": "/bin/true",
                                "username": "root", "password": PASSWORD}),
    ])
    br = plan_tools.vm_apply_plan(pid, target="vc01")["blast_radius"]
    assert br["blockers"] == []
    assert br["steps"][1]["check"] == "deferred"


def test_deferred_check_runs_just_before_the_step_and_stops_the_plan(served, dispatched):
    """power_off 'ran' (the dispatch is recorded, the VM stays on): the delete refuses."""
    vm = served(_vm("old-01", power=ON))[0]
    pid = _write_plan([
        _step(0, "power_off", {"vm_name": "old-01", "force": True}),
        _step(1, "delete_vm", {"vm_name": "old-01", "acknowledge_blast_radius": _ack(vm)}),
        _step(2, "power_on", {"vm_name": "later"}),
    ])
    out = plan_tools.vm_apply_plan(pid, confirm=True, target="vc01")
    assert [c[0] for c in dispatched] == ["power_off"]
    vm.Destroy_Task.assert_not_called()
    assert out["status"] == "failed"
    step = out["steps"][1]
    assert step["status"] == "failed" and "powered on" in step["result"]
    assert "Step 1" in step["result"]
    assert out["steps"][2]["status"] == "skipped"


def test_every_destructive_step_is_checked_immediately_before_it_runs(
    served, dispatched, monkeypatch,
):
    from vmware_aiops.ops import plan_gate

    order: list = []
    real = plan_gate.measure_step
    monkeypatch.setattr(plan_gate, "measure_step",
                        lambda si, step: order.append(("check", step["index"])) or real(si, step))
    monkeypatch.setattr(plan_executor, "_dispatch",
                        lambda _si, a, p: order.append(("run", a)) or "ok")
    served(_vm("a", power=ON), _vm("b", power=ON))
    pid = _write_plan([
        _step(0, "power_off", {"vm_name": "a", "force": True}),
        _step(1, "power_on", {"vm_name": "c"}),
        _step(2, "power_off", {"vm_name": "b", "force": True}),
    ])
    plan_tools.vm_apply_plan(pid, confirm=True, target="vc01")
    applied = order[order.index(("run", "power_off")) - 1:]
    assert applied == [("check", 0), ("run", "power_off"), ("run", "power_on"),
                       ("check", 2), ("run", "power_off")]


# ═══ H3: a command the preview cannot show whole is refused ═══════════════════


def test_guest_exec_over_the_cap_is_flagged_and_refused(served, monkeypatch):
    from vmware_aiops.ops.guest_gate import MAX_COMMAND_CHARS

    served(_vm("web-01"))
    ran: list = []
    monkeypatch.setattr(guest_tools, "guest_exec", lambda *a, **k: ran.append(a) or {})
    args = "x" * (MAX_COMMAND_CHARS + 50)
    br = guest_tools.vm_guest_exec("web-01", "/bin/sh", "ops", arguments=args,
                                   password=PASSWORD)["blast_radius"]
    assert br["arguments_length"] == MAX_COMMAND_CHARS + 50
    assert br["truncated"] is True
    out = guest_tools.vm_guest_exec("web-01", "/bin/sh", "ops", arguments=args,
                                    password=PASSWORD, confirm=True)
    assert ran == [] and str(MAX_COMMAND_CHARS + 50) in out["error"]


def test_guest_exec_output_command_over_the_cap_is_refused(served, monkeypatch):
    from vmware_aiops.ops.guest_gate import MAX_COMMAND_CHARS

    served(_vm("web-01"))
    monkeypatch.setattr(guest_tools, "guest_exec_with_output", lambda *a, **k: {})
    cmd = "echo " + "y" * MAX_COMMAND_CHARS
    br = guest_tools.vm_guest_exec_output("web-01", cmd, "ops", password=PASSWORD)["blast_radius"]
    assert br["command_length"] == len(cmd) and br["truncated"] is True
    assert "error" in guest_tools.vm_guest_exec_output(
        "web-01", cmd, "ops", password=PASSWORD, confirm=True)


def test_guest_exec_under_the_cap_is_not_truncated(served):
    served(_vm("web-01"))
    br = guest_tools.vm_guest_exec("web-01", "/bin/true", "ops",
                                   password=PASSWORD)["blast_radius"]
    assert br["truncated"] is False and br["command_length"] == len("/bin/true")
    assert br["blockers"] == []


def test_provision_exec_step_over_the_cap_is_refused(served, monkeypatch):
    from vmware_aiops.ops.guest_gate import MAX_COMMAND_CHARS

    served(_vm("web-01"))
    ran: list = []
    monkeypatch.setattr(guest_tools, "guest_provision", lambda *a, **k: ran.append(a) or {})
    steps = [{"type": "exec", "command": "z" * (MAX_COMMAND_CHARS + 1)}]
    br = guest_tools.vm_guest_provision("web-01", "ops", PASSWORD, steps)["blast_radius"]
    assert br["steps"][0]["command_length"] == MAX_COMMAND_CHARS + 1
    assert br["steps"][0]["truncated"] is True
    out = guest_tools.vm_guest_provision("web-01", "ops", PASSWORD, steps, confirm=True)
    assert ran == [] and "step 1" in out["error"]


# ═══ M1: the refusal keeps its remedy ═════════════════════════════════════════


def test_refusal_with_many_blockers_leads_with_the_first_remedy():
    long_list = ", ".join(f"snapshot-{i:03d}-nightly-backup" for i in range(40))
    radius = {"blockers": [
        f"Snapshot 'x' not found on this VM (available: {long_list}). Run "
        "vm_list_snapshots for exact names; they are case-sensitive.",
        "Second blocker. Do the second thing.",
        "Third blocker. Do the third thing.",
    ]}
    with pytest.raises(GateRefusedError) as exc:
        refuse_on(radius, "vm_revert_snapshot")
    msg = str(exc.value)
    assert len(msg) <= MAX_REFUSAL_CHARS
    assert msg.startswith("vm_revert_snapshot refused: Snapshot 'x' not found")
    assert "Run vm_list_snapshots for exact names" in msg
    assert "and 2 more blockers" in msg and "preview" in msg


def test_gate_publishes_the_refusal_cap():
    assert gate_mod.MAX_REFUSAL_CHARS == MAX_REFUSAL_CHARS


def test_refusal_with_one_blocker_has_no_count():
    with pytest.raises(GateRefusedError) as exc:
        refuse_on({"blockers": ["Only one. Fix it."]}, "t")
    assert str(exc.value) == "t refused: Only one. Fix it."


def test_refusal_on_many_unmeasured_fields_stays_under_the_cap():
    radius = {"blockers": [], "unmeasured": [f"field_{i:04d}_that_is_long" for i in range(60)]}
    with pytest.raises(GateRefusedError) as exc:
        refuse_on(radius, "t")
    msg = str(exc.value)
    assert len(msg) <= MAX_REFUSAL_CHARS and "retry" in msg


def test_plan_refusal_reaches_the_caller_with_its_remedy(served, dispatched):
    served()
    pid = _write_plan([
        _step(i, "revert_snapshot", {"vm_name": f"ghost-{i}", "snapshot_name": "s"})
        for i in range(4)
    ])
    out = plan_tools.vm_apply_plan(pid, confirm=True, target="vc01")
    assert len(out["error"]) <= MAX_REFUSAL_CHARS
    assert "list_virtual_machines" in out["error"] and "3 more blockers" in out["error"]


# ═══ M3: measurement and executor agree on the snapshot ═══════════════════════


def test_clean_slate_that_did_not_revert_is_an_error(served, monkeypatch):
    served(_vm("lab-01", power=ON, snaps=[_snap("baseline", "snapshot-1")]))
    monkeypatch.setattr(vm_lifecycle, "clean_slate",
                        lambda *_a, **_k: "Clean Slate: Snapshot 'baseline' not found. "
                                          "Available: none")
    out = ttl_tools.vm_clean_slate("lab-01", confirm=True)
    assert "did not act" in out["error"]


def test_clean_slate_executor_does_not_power_off_when_the_snapshot_is_missing(served):
    (vm,) = served(_vm("lab-01", power=ON, snaps=[_snap("other", "snapshot-1")]))
    msg = vm_lifecycle.clean_slate(MagicMock(), "lab-01", "baseline")
    vm.PowerOff.assert_not_called()
    assert "not found" in msg


def test_a_snapshot_name_with_control_characters_is_the_one_the_list_shows(served):
    """list_snapshots shows sanitize(name); the gate must find what the executor finds."""
    node = _snap("base\x07line", "snapshot-9")
    served(_vm("web-01", snaps=[node]))
    br = vm_tools.vm_revert_snapshot("web-01", "baseline")["blast_radius"]
    assert br["blockers"] == [] and br["snapshot"]["id"] == "snapshot-9"
    out = vm_tools.vm_revert_snapshot("web-01", "baseline", confirm=True)
    assert out["action"] == "reverted"
    node.snapshot.RevertToSnapshot_Task.assert_called_once()


@pytest.mark.parametrize("tool", ["vm_revert_snapshot", "vm_delete_snapshot"])
def test_names_that_collide_after_sanitising_are_refused(served, tool):
    a, b = _snap("baseline", "snapshot-1"), _snap("base​line", "snapshot-2")
    served(_vm("web-01", snaps=[a, b]))
    out = getattr(vm_tools, tool)("web-01", "baseline", confirm=True)
    for node in (a, b):
        node.snapshot.RevertToSnapshot_Task.assert_not_called()
        node.snapshot.RemoveSnapshot_Task.assert_not_called()
    assert "2 snapshots" in out["error"]


def test_clean_slate_names_that_collide_after_sanitising_are_refused(served):
    a, b = _snap("baseline", "snapshot-1"), _snap("base\x00line", "snapshot-2")
    (vm,) = served(_vm("lab-01", power=ON, snaps=[a, b]))
    out = ttl_tools.vm_clean_slate("lab-01", confirm=True)
    vm.PowerOff.assert_not_called()
    assert "2 snapshots" in out["error"]


def test_executor_refuses_an_ambiguous_name_instead_of_taking_the_first(served):
    a, b = _snap("nightly", "snapshot-1"), _snap("nightly", "snapshot-2")
    served(_vm("web-01", snaps=[a, b]))
    msg = vm_lifecycle.revert_to_snapshot(MagicMock(), "web-01", "nightly")
    a.snapshot.RevertToSnapshot_Task.assert_not_called()
    assert "2 snapshots" in msg


# ═══ M4: the TTL deletes the VM it was set on, not whatever has its name ══════


def test_ttl_entry_stores_target_and_instance_uuid(served):
    served(_vm("lab-01", power=OFF, uuid="uuid-A"))
    ttl_tools.vm_set_ttl("lab-01", 30, confirm=True, target="vc01")
    entry = ttl.get_ttl("lab-01")
    assert entry["instance_uuid"] == "uuid-A" and entry["target"] == "vc01"


def test_old_ttl_entries_without_uuid_still_load(tmp_path):
    ttl._TTL_FILE.write_text(json.dumps({"old": {
        "vm_name": "old", "expires_at": "2026-01-01T00:00:00+00:00", "target": None}}),
        encoding="utf-8")
    (entry,) = ttl.get_expired_entries()
    assert entry.vm_name == "old" and entry.instance_uuid is None


def test_ttl_measurement_keeps_non_power_blockers(served, monkeypatch):
    from vmware_aiops.ops import vm_delete_gate

    served(_vm("lab-01", power=ON))
    real = vm_delete_gate.measure_vm_delete
    monkeypatch.setattr(vm_delete_gate, "measure_vm_delete",
                        lambda vm: {**real(vm), "blockers": real(vm)["blockers"]
                                    + ["Something else blocks this."]})
    br = ttl_tools.vm_set_ttl("lab-01", 30)["blast_radius"]
    assert br["blockers"] == ["Something else blocks this."]
    assert "error" in ttl_tools.vm_set_ttl("lab-01", 30, confirm=True)
    assert ttl.get_ttl("lab-01") is None


def _expired(uuid):
    return ttl.TTLEntry(vm_name="ttl-vm", expires_at="2026-01-01T00:00:00+00:00",
                        target="vc01", instance_uuid=uuid)


def test_daemon_refuses_to_delete_a_different_vm_with_the_same_name(served, monkeypatch):
    from vmware_aiops.scanner import scheduler

    monkeypatch.setattr(scheduler, "_last_ttl_outcome", {}, raising=False)
    served(_vm("ttl-vm", power=OFF, uuid="uuid-NEW"))
    removed, deleted, audited = [], [], []
    monkeypatch.setattr(scheduler, "get_expired_entries", lambda: [_expired("uuid-OLD")])
    monkeypatch.setattr(scheduler, "remove_entry", removed.append)
    monkeypatch.setattr(scheduler, "delete_vm", lambda si, n: deleted.append(n))
    monkeypatch.setattr(scheduler, "_audit",
                        lambda tool, params, status, result, *a: audited.append((status, result)))
    scheduler._run_ttl_check(MagicMock())
    assert deleted == [] and removed == ["ttl-vm"]
    assert audited and audited[0][0] == "error" and "uuid-OLD" in str(audited[0][1])


def test_daemon_deletes_the_vm_whose_uuid_matches(served, monkeypatch):
    from vmware_aiops.scanner import scheduler

    monkeypatch.setattr(scheduler, "_last_ttl_outcome", {}, raising=False)
    served(_vm("ttl-vm", power=OFF, uuid="uuid-SAME"))
    removed, deleted = [], []
    monkeypatch.setattr(scheduler, "get_expired_entries", lambda: [_expired("uuid-SAME")])
    monkeypatch.setattr(scheduler, "remove_entry", removed.append)
    monkeypatch.setattr(scheduler, "delete_vm", lambda si, n: deleted.append(n) or "ok")
    monkeypatch.setattr(scheduler, "_audit", lambda *a, **k: None)
    scheduler._run_ttl_check(MagicMock())
    assert deleted == ["ttl-vm"] and removed == ["ttl-vm"]


# ═══ M5: rollback deletes only the VM the plan created ════════════════════════


def test_apply_records_the_created_vms_instance_uuid(served, dispatched):
    served(_vm("new-01", uuid="uuid-created"))
    pid = _write_plan([_step(0, "create_vm", {"vm_name": "new-01"}),
                       _step(1, "power_on", {"vm_name": "x"})])
    out = plan_tools.vm_apply_plan(pid, confirm=True, target="vc01")
    assert out["steps"][0]["rollback_params"]["instance_uuid"] == "uuid-created"


def test_rollback_refuses_to_delete_a_vm_that_is_not_the_one_created(served):
    (vm,) = served(_vm("new-01", power=OFF, uuid="uuid-someone-else"))
    step = _step(0, "create_vm", {"vm_name": "new-01"}, status="success")
    step["rollback_params"] = {**step["rollback_params"], "instance_uuid": "uuid-created"}
    pid = _write_plan([step, _step(1, "power_on", {"vm_name": "x"}, status="failed")],
                      status="failed")
    out = plan_tools.vm_rollback_plan(pid, confirm=True, target="vc01")
    vm.Destroy_Task.assert_not_called()
    (res,) = out["rollback_results"]
    assert res["rollback_status"] == "refused" and "uuid-created" in res["error"]
    assert out["status"] == "failed" and out["stopped_at_step"] == 0


def test_rollback_deletes_the_vm_whose_uuid_matches(served):
    (vm,) = served(_vm("new-01", power=OFF, uuid="uuid-created"))
    step = _step(0, "create_vm", {"vm_name": "new-01"}, status="success")
    step["rollback_params"] = {**step["rollback_params"], "instance_uuid": "uuid-created"}
    pid = _write_plan([step, _step(1, "power_on", {"vm_name": "x"}, status="failed")],
                      status="failed")
    out = plan_tools.vm_rollback_plan(pid, confirm=True, target="vc01")
    vm.Destroy_Task.assert_called_once()
    assert out["rollback_results"][0]["rollback_status"] == "success"


def test_rollback_without_a_recorded_identity_refuses_that_step(served):
    (vm,) = served(_vm("new-01", power=OFF))
    pid = _write_plan([_step(0, "create_vm", {"vm_name": "new-01"}, status="success"),
                       _step(1, "power_on", {"vm_name": "x"}, status="failed")],
                      status="failed")
    br = plan_tools.vm_rollback_plan(pid, target="vc01")["blast_radius"]
    assert br["would_run"][0]["verifies_instance_uuid"] is None
    out = plan_tools.vm_rollback_plan(pid, confirm=True, target="vc01")
    vm.Destroy_Task.assert_not_called()
    assert out["rollback_results"][0]["rollback_status"] == "refused"
    assert out["status"] == "failed" and planner.load_plan(pid)["status"] == "failed"


# ═══ L1: plan responses never carry a step password ═══════════════════════════


def test_create_plan_response_redacts_passwords(served):
    served(_vm("web-01"))
    out = plan_tools.vm_create_plan([{"action": "guest_exec", "vm_name": "web-01",
                                      "command": "/bin/true", "username": "root",
                                      "password": PASSWORD}], target="vc01")
    assert "plan_id" in out and PASSWORD not in repr(out)


def test_apply_response_redacts_passwords(served, dispatched):
    served(_vm("web-01"))
    pid = _write_plan([_step(0, "guest_exec", {"vm_name": "web-01", "command": "/bin/true",
                                               "username": "root", "password": PASSWORD}),
                       _step(1, "power_on", {"vm_name": "x"})])
    out = plan_tools.vm_apply_plan(pid, confirm=True, target="vc01")
    assert out["status"] == "completed" and PASSWORD not in repr(out)
    # The executor itself still got the real password.
    assert dispatched[0][1]["password"] == PASSWORD


def test_rollback_without_a_recorded_identity_never_matches_an_unreadable_one(served):
    """No recorded UUID and a VM whose UUID reads None must not count as 'the same VM'."""
    vm = _vm("new-01", power=OFF)
    vm.config.instanceUuid = None
    served(vm)
    pid = _write_plan([_step(0, "create_vm", {"vm_name": "new-01"}, status="success"),
                       _step(1, "power_on", {"vm_name": "x"}, status="failed")],
                      status="failed")
    out = plan_tools.vm_rollback_plan(pid, confirm=True, target="vc01")
    vm.Destroy_Task.assert_not_called()
    assert "did not record" in out["rollback_results"][0]["error"]
