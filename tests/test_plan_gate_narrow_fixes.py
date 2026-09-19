"""Narrow-review findings on the plan gate (2026-09-19).

A plan must never be a way around a tool's gate. Each test reproduces one finding:

* 1 — an action in the executor's dispatch table could be neither measured nor
  classified non-destructive; ``iscsi_remove_target`` ran "not measured".
* 2 — ``migrate`` was classified non-destructive although ``vm_migrate`` is
  gated; the four storage actions (gated in vmware-storage) had no measurement.
* 3 — rollback ran every action except delete_vm unmeasured.
* 4 — the secret-key pattern missed ``pwd``/``auth`` and hid ``bypass``.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from pyVmomi import vim
from vmware_policy.budget import reset_budget
from vmware_policy.policy import reset_policy_engine
from vmware_policy.undo import reset_undo_store

import vmware_aiops.mcp_server.tools.plan as plan_tools
from vmware_aiops.ops import inventory, plan_executor, plan_gate, planner, vm_lifecycle
from vmware_aiops.ops.gate import GateRefusedError

ON = vim.VirtualMachine.PowerState.poweredOn
OFF = vim.VirtualMachine.PowerState.poweredOff
SUSPENDED = vim.VirtualMachine.PowerState.suspended
STORAGE = {
    "iscsi_enable": "storage_iscsi_enable",
    "iscsi_add_target": "storage_iscsi_add_target",
    "iscsi_remove_target": "storage_iscsi_remove_target",
    "storage_rescan": "storage_rescan",
}


@pytest.fixture(autouse=True)
def harness(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_HOME", str(tmp_path))
    reset_policy_engine()
    reset_budget()
    reset_undo_store()
    monkeypatch.setattr(plan_tools, "_get_connection", lambda _t=None: MagicMock(name="si"))
    monkeypatch.setattr(vm_lifecycle, "_wait_for_task", lambda _t, **_k: None)
    monkeypatch.setattr(planner, "_PLANS_DIR", tmp_path / "plans")
    yield
    reset_policy_engine()
    reset_budget()
    reset_undo_store()


def _snap(name):
    node = MagicMock(name=f"snap:{name}")
    node.name = name
    node.createTime = "2026-09-01 10:00:00"
    node.snapshot._moId = f"snapshot-{name}"
    node.childSnapshotList = []
    return node


def _vm(name, power=ON, host="esx-01", snaps=()):
    vm = MagicMock(name=f"vm:{name}")
    vm.name = name
    vm.runtime.powerState = power
    vm.runtime.host.name = host
    vm.guest.toolsRunningStatus = "guestToolsRunning"
    vm.config.instanceUuid = f"uuid-{name}"
    vm.config.hardware.device = []
    vm.datastore = []
    if snaps:
        vm.snapshot.rootSnapshotList = list(snaps)
        vm.snapshot.currentSnapshot = None
    else:
        vm.snapshot = None
    return vm


def _host(name, maintenance=False):
    host = MagicMock(name=f"host:{name}")
    host.name = name
    host.runtime.connectionState = "connected"
    host.runtime.inMaintenanceMode = maintenance
    return host


@pytest.fixture
def world(monkeypatch):
    """VMs and hosts served by name; the lists can change mid-run."""
    state = {"vms": [], "hosts": []}

    def collect(_si, types, _p):
        pool = state["vms"] if vim.VirtualMachine in types else (
            state["hosts"] if vim.HostSystem in types else [])
        return [(o, {"name": o.name}) for o in pool]

    monkeypatch.setattr(inventory, "_collect", collect)
    return state


@pytest.fixture
def dispatched(monkeypatch):
    calls: list = []
    monkeypatch.setattr(plan_executor, "_dispatch",
                        lambda _si, a, p: calls.append(("do", a)) or "ok")
    monkeypatch.setattr(plan_executor, "_rollback_dispatch",
                        lambda _si, a, p: calls.append(("undo", a, p)) or "ok")
    return calls


def _write_plan(steps, status="pending", target="vc01", plan_id="plan-n-0001"):
    planner._PLANS_DIR.mkdir(parents=True, exist_ok=True)
    plan = {"plan_id": plan_id, "created_at": "2026-09-19T00:00:00+00:00",
            "target": target, "status": status, "steps": steps,
            "summary": {"total_steps": len(steps)}}
    (planner._PLANS_DIR / f"{plan_id}.json").write_text(json.dumps(plan), encoding="utf-8")
    return plan_id


def _step(index, action, params, status="pending"):
    rb_action, rb_params = planner._build_rollback(action, params)
    return {"index": index, "action": action, "params": params,
            "rollback_action": rb_action, "rollback_params": rb_params,
            "status": status, "result": None, "executed_at": None}


# ═══ 1: every dispatchable action is classified — structurally ════════════════


def _dispatch_actions() -> set[str]:
    return set(plan_executor.dispatch_actions())


def test_dispatch_actions_are_the_plan_schema_actions():
    assert _dispatch_actions() == set(planner._ACTION_SCHEMA)


@pytest.mark.parametrize("action", sorted(set(planner._ACTION_SCHEMA)))
def test_every_dispatch_action_is_measured_refused_or_harmless_with_a_reason(action):
    """Neither a measurement nor a refusal nor a stated reason → the action is unchecked."""
    assert action in _dispatch_actions()
    measured = action in plan_gate._MEASURES
    refused = action in plan_gate.REFUSED_ACTIONS
    reason = plan_gate.NON_DESTRUCTIVE_ACTIONS.get(action)
    assert measured + refused + bool(reason) == 1, action
    if reason:
        assert isinstance(reason, str) and len(reason) > 20
    assert (action in plan_gate.GATED_ACTIONS) == (measured or refused)


def test_a_gated_action_that_loses_its_measurement_refuses(world, monkeypatch):
    """Fail closed: no measurement is not a pass."""
    world["vms"] = [_vm("web-01")]
    monkeypatch.delitem(plan_gate._MEASURES, "power_off")
    with pytest.raises(GateRefusedError, match="no measurement"):
        plan_gate.check_step(MagicMock(), {"index": 0, "action": "power_off",
                                           "params": {"vm_name": "web-01"}})
    pid = _write_plan([_step(0, "power_off", {"vm_name": "web-01"})])
    br = plan_tools.vm_apply_plan(pid, target="vc01")["blast_radius"]
    assert any("no measurement" in b for b in br["blockers"])


def test_an_unclassified_action_refuses_at_check_step():
    with pytest.raises(GateRefusedError):
        plan_gate.check_step(MagicMock(), {"index": 0, "action": "format_disk", "params": {}})


# ═══ 2a: migrate is measured the way vm_migrate measures it ═══════════════════


def test_migrate_is_measured_in_the_preview(world, dispatched):
    world["vms"] = [_vm("web-01")]
    world["hosts"] = [_host("esx-02", maintenance=True)]
    pid = _write_plan([_step(0, "migrate", {"vm_name": "web-01", "target_host": "esx-02"})])
    br = plan_tools.vm_apply_plan(pid, target="vc01")["blast_radius"]
    (entry,) = br["steps"]
    assert entry["check"] == "measured"
    assert entry["measured"]["target_host"]["name"] == "esx-02"
    assert any("maintenance mode" in b for b in br["blockers"])
    out = plan_tools.vm_apply_plan(pid, confirm=True, target="vc01")
    assert dispatched == [] and "maintenance mode" in out["error"]


def test_migrate_is_checked_just_before_it_runs(world):
    world["vms"] = [_vm("web-01")]
    step = {"index": 3, "action": "migrate",
            "params": {"vm_name": "web-01", "target_host": "esx-gone"}}
    with pytest.raises(GateRefusedError, match="esx-gone"):
        plan_gate.check_step(MagicMock(), step)


def test_a_clean_migrate_plan_runs(world, dispatched):
    web = _vm("web-01")
    world["vms"] = [web]
    world["hosts"] = [_host("esx-02")]
    pid = _write_plan([_step(0, "migrate", {"vm_name": "web-01", "target_host": "esx-02"})])
    out = plan_tools.vm_apply_plan(pid, confirm=True, target="vc01")
    assert out["status"] == "completed" and dispatched == [("do", "migrate")]


# ═══ 2b: storage actions are refused in plans, pointing at vmware-storage ══════


@pytest.mark.parametrize("action,tool", sorted(STORAGE.items()))
def test_storage_action_in_a_plan_is_a_blocker(action, tool, world, dispatched):
    params = {"host_name": "esx-01", "address": "10.0.0.9"}
    params = {k: params[k] for k in planner._ACTION_SCHEMA[action]["required"]}
    pid = _write_plan([_step(0, "power_on", {"vm_name": "a"}), _step(1, action, params)])
    br = plan_tools.vm_apply_plan(pid, target="vc01")["blast_radius"]
    blocker = next(b for b in br["blockers"] if b.startswith("Step 1"))
    assert tool in blocker and "vmware-storage" in blocker
    out = plan_tools.vm_apply_plan(pid, confirm=True, target="vc01")
    assert dispatched == [] and tool in out["error"]


@pytest.mark.parametrize("action,tool", sorted(STORAGE.items()))
def test_storage_action_is_refused_at_check_step(action, tool):
    with pytest.raises(GateRefusedError, match=tool):
        plan_gate.check_step(MagicMock(), {"index": 0, "action": action,
                                           "params": {"host_name": "esx-01"}})


def test_storage_action_is_a_blocker_even_when_the_target_does_not_match(world, dispatched):
    pid = _write_plan([_step(0, "iscsi_remove_target",
                             {"host_name": "esx-01", "address": "10.0.0.9"})])
    br = plan_tools.vm_apply_plan(pid, target="other")["blast_radius"]
    assert any("storage_iscsi_remove_target" in b for b in br["blockers"])


# ═══ 3: rollback steps are checked like forward steps ═════════════════════════


def _failed(*steps):
    return _write_plan(list(steps) + [_step(9, "power_on", {"vm_name": "x"}, status="failed")],
                       status="failed")


def test_rollback_preview_measures_each_destructive_rollback_step(world, dispatched):
    world["vms"] = [_vm("web-02", power=ON), _vm("web-02", power=ON)]
    pid = _failed(_step(0, "power_on", {"vm_name": "web-02"}, status="success"))
    br = plan_tools.vm_rollback_plan(pid, target="vc01")["blast_radius"]
    (entry,) = br["would_run"]
    assert entry["check"] == "measured"
    # Two VMs share the name: the measurement is a blocker, and refuses.
    assert any(b.startswith("Step 0 (rollback power_off)") for b in br["blockers"])
    out = plan_tools.vm_rollback_plan(pid, confirm=True, target="vc01")
    assert dispatched == [] and "error" in out


def test_rollback_power_off_is_measured_as_a_hard_power_off(world, dispatched):
    world["vms"] = [_vm("web-02", power=ON)]
    pid = _failed(_step(0, "power_on", {"vm_name": "web-02"}, status="success"))
    br = plan_tools.vm_rollback_plan(pid, target="vc01")["blast_radius"]
    (entry,) = br["would_run"]
    assert entry["measured"]["mode"] == "hard_power_off" and br["blockers"] == []


def test_rollback_preview_refuses_on_an_unmeasurable_rollback_step(world, dispatched,
                                                                    monkeypatch):
    world["vms"] = [_vm("web-02")]
    monkeypatch.setitem(plan_gate._MEASURES, "power_off",
                        lambda _si, _p: (_ for _ in ()).throw(RuntimeError("boom")))
    pid = _failed(_step(0, "power_on", {"vm_name": "web-02"}, status="success"))
    br = plan_tools.vm_rollback_plan(pid, target="vc01")["blast_radius"]
    assert br["unmeasured"]
    out = plan_tools.vm_rollback_plan(pid, confirm=True, target="vc01")
    assert dispatched == [] and "error" in out


def test_rollback_storage_step_is_refused(world, dispatched):
    pid = _failed(_step(0, "iscsi_add_target", {"host_name": "esx-01", "address": "10.0.0.9"},
                        status="success"))
    br = plan_tools.vm_rollback_plan(pid, target="vc01")["blast_radius"]
    assert any("storage_iscsi_remove_target" in b for b in br["blockers"])
    out = plan_tools.vm_rollback_plan(pid, confirm=True, target="vc01")
    assert dispatched == [] and "storage_iscsi_remove_target" in out["error"]


def test_a_failed_check_during_rollback_stops_it_with_a_teaching_error(world, dispatched,
                                                                        monkeypatch):
    """Clean at preview; the world moves; the check before the next step stops the rollback."""
    world["vms"] = [_vm("web-02"), _vm("web-03", snaps=[_snap("pre")])]

    def undo(_si, action, params):
        dispatched.append(("undo", action, params))
        world["vms"] = [v for v in world["vms"] if v.name != "web-03"]
        return "ok"

    monkeypatch.setattr(plan_executor, "_rollback_dispatch", undo)
    pid = _failed(
        _step(0, "create_snapshot", {"vm_name": "web-03", "snapshot_name": "pre"},
              status="success"),
        _step(1, "power_on", {"vm_name": "web-02"}, status="success"),
    )
    assert plan_tools.vm_rollback_plan(pid, target="vc01")["blast_radius"]["blockers"] == []
    out = plan_tools.vm_rollback_plan(pid, confirm=True, target="vc01")
    assert [c[1] for c in dispatched] == ["power_off"]
    refused = out["rollback_results"][-1]
    assert refused["step_index"] == 0 and refused["rollback_status"] == "refused"
    assert "web-03" in refused["error"] and "not found" in refused["error"]
    assert out["status"] == "failed" and "Step 0" in out["hint"]
    # The plan stays failed, so rollback can be run again once it is resolved;
    # the step already undone is not undone twice.
    saved = planner.load_plan(pid)
    assert saved["status"] == "failed"
    assert [s["status"] for s in saved["steps"][:2]] == ["success", "rolled_back"]


def test_rollback_check_is_deferred_behind_an_earlier_rollback_step(world, dispatched):
    """Rollback runs in reverse; a step whose VM an earlier rollback step changes is deferred."""
    world["vms"] = [_vm("web-02")]
    pid = _failed(
        _step(0, "create_snapshot", {"vm_name": "web-02", "snapshot_name": "pre"},
              status="success"),
        _step(1, "power_on", {"vm_name": "web-02"}, status="success"),
    )
    br = plan_tools.vm_rollback_plan(pid, target="vc01")["blast_radius"]
    first, second = br["would_run"]
    assert first["check"] == "measured"
    assert second["check"] == "deferred" and second["deferred_until_after_step"] == 1


# ═══ 4: secret keys are matched by token ═════════════════════════════════════


@pytest.mark.parametrize("key", [
    "password", "passwd", "pwd", "db_pwd", "secret", "client_secret", "token",
    "authToken", "api_key", "apikey", "apiKey", "auth", "basic_auth", "credential",
    "credentials", "private_key", "privateKey", "passphrase", "PASSWORD",
])
def test_secret_keys_are_redacted(key):
    assert plan_gate.redact({key: "v"}) == {key: plan_gate.REDACTED}


@pytest.mark.parametrize("key", [
    "bypass", "passthrough", "author", "compass", "vm_name", "command",
    "authority_host", "tokenizer_path",
])
def test_ordinary_keys_are_not_redacted(key):
    assert plan_gate.redact({key: "v"}) == {key: "v"}
