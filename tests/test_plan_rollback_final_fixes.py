"""Final-review findings on plan rollback (2026-09-19).

* 1 — a plan that creates a VM and powers it on could never be rolled back:
  the power_on rollback was a graceful power_off, which the gate refuses when
  VMware Tools is not running (always, for a VM with no OS), so rollback
  stopped before the delete_vm that removes the created VM. A power_off whose
  VM is already off was refused the same way, although vm_power_off returns
  ``noop`` for it.
* 2 — a created VM whose instance UUID was not recorded was reported "failed,
  continue" at rollback (plan ends ``rolled_back``, cannot be retried) while
  the preview called that step "refused".
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

ON = vim.VirtualMachine.PowerState.poweredOn
OFF = vim.VirtualMachine.PowerState.poweredOff
NO_TOOLS = "guestToolsNotRunning"


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


def _vm(name, power=OFF, tools=NO_TOOLS, uuid=None):
    vm = MagicMock(name=f"vm:{name}")
    vm.name = name
    vm.runtime.powerState = power
    vm.runtime.host.name = "esx-01"
    vm.guest.toolsRunningStatus = tools
    vm.config.instanceUuid = uuid or f"uuid-{name}"
    vm.config.hardware.device = []
    vm.datastore = []
    vm.snapshot = None
    return vm


@pytest.fixture
def world(monkeypatch):
    state = {"vms": [], "destroyed": [], "powered_off": []}

    def collect(_si, types, _p):
        pool = state["vms"] if vim.VirtualMachine in types else []
        return [(o, {"name": o.name}) for o in pool]

    monkeypatch.setattr(inventory, "_collect", collect)
    return state


@pytest.fixture
def executor(world, monkeypatch):
    """Forward steps act on the world; rollback runs the real _rollback_dispatch."""

    def find(name):
        return next(v for v in world["vms"] if v.name == name)

    def destroy_task(vm):
        def run():
            world["destroyed"].append(vm.name)
            world["vms"] = [v for v in world["vms"] if v is not vm]
        return run

    def dispatch(_si, action, params):
        if action == "create_vm":
            vm = _vm(params["vm_name"])
            vm.Destroy_Task.side_effect = destroy_task(vm)
            world["vms"].append(vm)
        elif action == "power_on":
            find(params["vm_name"]).runtime.powerState = ON
        elif action == "power_off":
            world["powered_off"].append((params["vm_name"], params.get("force", False)))
            find(params["vm_name"]).runtime.powerState = OFF
        elif action == "reconfigure":
            raise RuntimeError("reconfigure failed")
        return "ok"

    monkeypatch.setattr(plan_executor, "_dispatch", dispatch)
    return world


def _write_plan(steps, status="pending", plan_id="plan-f-0001"):
    planner._PLANS_DIR.mkdir(parents=True, exist_ok=True)
    plan = {"plan_id": plan_id, "created_at": "2026-09-19T00:00:00+00:00",
            "target": "vc01", "status": status, "steps": steps,
            "summary": {"total_steps": len(steps)}}
    (planner._PLANS_DIR / f"{plan_id}.json").write_text(json.dumps(plan), encoding="utf-8")
    return plan_id


def _step(index, action, params, status="pending"):
    rb_action, rb_params = planner._build_rollback(action, params)
    return {"index": index, "action": action, "params": params,
            "rollback_action": rb_action, "rollback_params": rb_params,
            "status": status, "result": None, "executed_at": None}


# ═══ 1a: the rollback of a power_on is a hard power-off ══════════════════════


def test_power_on_rollback_is_a_hard_power_off():
    assert planner._build_rollback("power_on", {"vm_name": "new-vm"}) == (
        "power_off", {"vm_name": "new-vm", "force": True})


def test_create_then_power_on_rolls_back_to_the_vm_deleted_without_tools(executor):
    pid = _write_plan([
        _step(0, "create_vm", {"vm_name": "new-vm"}),
        _step(1, "power_on", {"vm_name": "new-vm"}),
        _step(2, "reconfigure", {"vm_name": "new-vm", "cpu": 4}),
    ])
    applied = plan_tools.vm_apply_plan(pid, confirm=True, target="vc01")
    assert applied["status"] == "failed"

    preview = plan_tools.vm_rollback_plan(pid, target="vc01")["blast_radius"]
    assert preview["blockers"] == [] and preview["unmeasured"] == []

    out = plan_tools.vm_rollback_plan(pid, confirm=True, target="vc01")
    assert out["status"] == "rolled_back", out
    assert [r["rollback_status"] for r in out["rollback_results"]] == ["success", "success"]
    assert executor["powered_off"] == [("new-vm", True)]
    assert executor["destroyed"] == ["new-vm"] and executor["vms"] == []


# ═══ 1b: a measurement whose action would be a noop passes ═══════════════════


@pytest.mark.parametrize("rollback", [False, True])
def test_power_off_of_an_already_off_vm_passes_as_noop(world, rollback):
    world["vms"] = [_vm("new-vm", power=OFF, tools=NO_TOOLS)]
    step = {"index": 1, "action": "power_off", "params": {"vm_name": "new-vm"}}
    if rollback:
        step["rollback"] = True
    plan_gate.check_step(MagicMock(), step)  # does not raise


def test_graceful_power_off_without_tools_on_a_running_vm_is_still_refused(world):
    world["vms"] = [_vm("new-vm", power=ON, tools=NO_TOOLS)]
    with pytest.raises(Exception, match="VMware Tools is not running"):
        plan_gate.check_step(MagicMock(), {"index": 1, "action": "power_off",
                                           "params": {"vm_name": "new-vm"}})


def test_a_forward_power_off_of_an_off_vm_is_not_a_blocker_in_the_preview(executor):
    executor["vms"] = [_vm("new-vm", power=OFF, tools=NO_TOOLS)]
    pid = _write_plan([_step(0, "power_off", {"vm_name": "new-vm"})])
    br = plan_tools.vm_apply_plan(pid, target="vc01")["blast_radius"]
    assert br["blockers"] == []
    (entry,) = br["steps"]
    assert entry["check"] == "measured" and entry["measured"]["noop"] is True
    out = plan_tools.vm_apply_plan(pid, confirm=True, target="vc01")
    assert out["status"] == "completed", out


# ═══ 2: an unrecorded instance UUID is a refusal, as the preview said ════════


def test_created_vm_without_recorded_uuid_stops_rollback_as_refused(executor):
    executor["vms"] = [_vm("new-vm", power=OFF)]
    create = _step(0, "create_vm", {"vm_name": "new-vm"}, status="success")
    create["rollback_params"] = {"vm_name": "new-vm", "instance_uuid": None}
    pid = _write_plan([
        create,
        _step(1, "power_on", {"vm_name": "other"}, status="success"),
        _step(2, "reconfigure", {"vm_name": "new-vm"}, status="failed"),
    ], status="failed")
    executor["vms"].append(_vm("other", power=ON))

    preview = plan_tools.vm_rollback_plan(pid, target="vc01")["blast_radius"]
    assert "refused" in next(s for s in preview["would_run"]
                             if s["step_index"] == 0)["identity_note"]

    out = plan_tools.vm_rollback_plan(pid, confirm=True, target="vc01")
    assert out["status"] == "failed", out
    assert out["stopped_at_step"] == 0
    refused = out["rollback_results"][-1]
    assert refused["step_index"] == 0 and refused["rollback_status"] == "refused"
    assert "instance UUID" in refused["error"] and "vm_delete" in refused["error"]
    assert "Step 0" in out["hint"]
    assert executor["destroyed"] == [] and executor["powered_off"] == [("other", True)]
    saved = planner.load_plan(pid)
    assert saved["status"] == "failed"
    assert saved["steps"][0]["status"] == "success"
    assert saved["steps"][1]["status"] == "rolled_back"
