"""Follow-ups to the 2026-10-17 composite-tool review.

1. A preview tells the truth about the policy. restore_vm_clean_slate,
   provision_vm_guest and run_vm_guest_command_with_output were refused by
   rules.yaml only at confirm=True; their previews showed no blocker, so the
   user agreed to something that was then refused.
2. A delete that powers the VM off first is checked as power_off_vm too: the CLI
   ``vm delete``, the TTL daemon and a plan rollback deleting the VM it created
   all powered off inline, and a deny on power_off_vm did not stop them.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pyVmomi import vim
from vmware_policy.budget import reset_budget
from vmware_policy.policy import reset_policy_engine
from vmware_policy.undo import reset_undo_store

import vmware_aiops.mcp_server.tools.guest as guest_tools
import vmware_aiops.mcp_server.tools.ttl as ttl_tools
from vmware_aiops.ops import plan_gate, ttl, vm_lifecycle
from vmware_aiops.ops.gate import GateRefusedError
from vmware_aiops.scanner import scheduler

ON = vim.VirtualMachine.PowerState.poweredOn
OFF = vim.VirtualMachine.PowerState.poweredOff


def _rules(home, *operations):
    ops = ", ".join(f'"{o}"' for o in operations)
    (home / "rules.yaml").write_text(
        f"deny:\n  - name: lab-rule\n    operations: [{ops}]\n    reason: \"Not here.\"\n",
        encoding="utf-8")
    reset_policy_engine()


@pytest.fixture(autouse=True)
def harness(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_HOME", str(tmp_path))
    reset_policy_engine()
    reset_budget()
    reset_undo_store()
    for mod in (guest_tools, ttl_tools):
        monkeypatch.setattr(mod, "_get_connection", lambda target=None: MagicMock())
    yield tmp_path
    reset_policy_engine()
    reset_budget()
    reset_undo_store()


@pytest.fixture
def rows(monkeypatch):
    captured: list[dict] = []

    class _Recorder:
        def log(self, **kw):
            captured.append(kw)

    monkeypatch.setattr("vmware_policy.guard.get_engine", lambda: _Recorder())
    return captured


def _vm(state):
    calls: list[str] = []
    vm = SimpleNamespace(
        runtime=SimpleNamespace(powerState=state),
        config=SimpleNamespace(instanceUuid="uuid-1"),
        PowerOff=lambda: calls.append("off") or "task-off",
        Destroy_Task=lambda: calls.append("destroy") or "task-destroy",
    )
    return vm, calls


@pytest.fixture
def no_wait(monkeypatch):
    monkeypatch.setattr(vm_lifecycle, "_wait_for_task", lambda task: None)


# ── 1. previews show policy refusals ─────────────────────────────────────────


@pytest.mark.unit
def test_clean_slate_preview_shows_the_revert_deny(harness, monkeypatch, rows):
    _rules(harness, "revert_vm_snapshot")
    monkeypatch.setattr("vmware_aiops.ops.ttl.measure_clean_slate",
                        lambda si, n, s: {"vm": n, "blockers": [], "unmeasured": []})

    out = ttl_tools.restore_vm_clean_slate("web-01", target="t")

    assert out["action"] == "preview"
    blockers = out["blast_radius"]["blockers"]
    assert any("revert_vm_snapshot" in b for b in blockers), blockers
    # A preview asks, it does not record a denial: nothing was attempted.
    assert not any(r.get("status") == "denied" for r in rows)


@pytest.mark.unit
def test_clean_slate_preview_keeps_its_own_blockers(harness, monkeypatch):
    _rules(harness, "power_off_vm")
    monkeypatch.setattr("vmware_aiops.ops.ttl.measure_clean_slate",
                        lambda si, n, s: {"blockers": ["no snapshot named baseline"],
                                          "unmeasured": []})

    blockers = ttl_tools.restore_vm_clean_slate("web-01", target="t")["blast_radius"]["blockers"]

    assert blockers[0] == "no snapshot named baseline"
    assert any("power_off_vm" in b for b in blockers[1:])


@pytest.mark.unit
def test_clean_slate_preview_without_rules_has_no_policy_blocker(monkeypatch):
    monkeypatch.setattr("vmware_aiops.ops.ttl.measure_clean_slate",
                        lambda si, n, s: {"blockers": [], "unmeasured": []})
    assert ttl_tools.restore_vm_clean_slate("web-01", target="t")["blast_radius"]["blockers"] == []


@pytest.mark.unit
def test_provision_preview_shows_the_guest_command_deny(harness, monkeypatch):
    _rules(harness, "run_vm_guest_command")
    monkeypatch.setattr(guest_tools, "measure_guest_provision",
                        lambda *a, **k: {"blockers": [], "unmeasured": []})

    out = guest_tools.provision_vm_guest("web-01", "root", "s3cret", [{"type": "exec"}],
                                         target="t")

    assert out["action"] == "preview"
    assert any("run_vm_guest_command" in b for b in out["blast_radius"]["blockers"])
    assert "s3cret" not in str(out)


@pytest.mark.unit
def test_with_output_preview_shows_the_download_deny(harness, monkeypatch):
    _rules(harness, "download_vm_guest_file")
    monkeypatch.setattr(guest_tools, "measure_guest_exec_output",
                        lambda *a, **k: {"blockers": [], "unmeasured": []})

    out = guest_tools.run_vm_guest_command_with_output("web-01", "/bin/id", username="root",
                                                       password="pw", target="t")

    assert out["action"] == "preview"
    assert any("download_vm_guest_file" in b for b in out["blast_radius"]["blockers"])


# ── 2. an inline power-off is checked as power_off_vm ────────────────────────


@pytest.mark.unit
def test_ops_delete_asks_before_powering_off(monkeypatch, no_wait):
    vm, calls = _vm(ON)
    monkeypatch.setattr(vm_lifecycle, "_require_vm", lambda si, n: vm)

    def refuse():
        raise GateRefusedError("power_off_vm denied")

    with pytest.raises(GateRefusedError):
        vm_lifecycle.delete_vm(MagicMock(), "web-01", before_power_off=refuse)
    assert calls == []


@pytest.mark.unit
def test_ops_delete_of_a_powered_off_vm_does_not_ask(monkeypatch, no_wait):
    vm, calls = _vm(OFF)
    monkeypatch.setattr(vm_lifecycle, "_require_vm", lambda si, n: vm)

    vm_lifecycle.delete_vm(MagicMock(), "web-01",
                           before_power_off=lambda: pytest.fail("asked for an off VM"))
    assert calls == ["destroy"]


def _cli_delete(monkeypatch, state):
    from typer.testing import CliRunner

    from vmware_aiops.cli import app

    vm, calls = _vm(state)
    monkeypatch.setattr(vm_lifecycle, "_require_vm", lambda si, n: vm)
    monkeypatch.setattr(vm_lifecycle, "_wait_for_task", lambda task: None)
    monkeypatch.setattr("vmware_aiops.cli.vm._get_connection",
                        lambda *a, **k: (MagicMock(), MagicMock()))
    monkeypatch.setattr(vm_lifecycle, "get_vm_info", lambda si, n: {"power_state": str(state)})
    monkeypatch.setattr("vmware_aiops.ops.vm_delete_gate.vm_delete_blast_radius",
                        lambda si, n: {"unmeasured": ["x"]})
    monkeypatch.setattr("vmware_aiops.cli.vm._double_confirm", lambda *a, **k: None)
    monkeypatch.setattr("vmware_aiops.cli.vm._show_state_preview", lambda *a, **k: None)
    result = CliRunner().invoke(app, ["vm", "delete", "web-01", "--target", "t"])
    return result, calls


@pytest.mark.unit
def test_cli_delete_of_a_running_vm_is_refused_when_power_off_is_denied(harness, monkeypatch):
    _rules(harness, "power_off_vm")
    result, calls = _cli_delete(monkeypatch, ON)
    assert result.exit_code != 0
    assert "power_off_vm" in result.output
    assert calls == []


@pytest.mark.unit
def test_cli_delete_of_an_off_vm_is_not_affected_by_a_power_off_deny(harness, monkeypatch):
    _rules(harness, "power_off_vm")
    result, calls = _cli_delete(monkeypatch, OFF)
    assert result.exit_code == 0, result.output
    assert calls == ["destroy"]


def _expired():
    return ttl.TTLEntry(vm_name="ttl-vm", expires_at="2026-01-01T00:00:00+00:00",
                        target="t", instance_uuid="uuid-1")


@pytest.mark.unit
def test_ttl_daemon_does_not_power_off_when_power_off_is_denied(harness, monkeypatch, rows):
    _rules(harness, "power_off_vm")
    vm, calls = _vm(ON)
    monkeypatch.setattr(vm_lifecycle, "_require_vm", lambda si, n: vm)
    monkeypatch.setattr(vm_lifecycle, "_wait_for_task", lambda task: None)
    monkeypatch.setattr(scheduler, "_last_ttl_outcome", {}, raising=False)
    monkeypatch.setattr(scheduler, "get_expired_entries", lambda: [_expired()])
    monkeypatch.setattr(scheduler, "verify_ttl_identity", lambda si, e: None)
    removed: list[str] = []
    monkeypatch.setattr(scheduler, "remove_entry", removed.append)
    conn = MagicMock()

    for _ in range(3):  # the job runs every minute
        scheduler._run_ttl_check(conn)

    assert calls == []
    assert removed == []  # kept: the deny may be lifted, then it expires as set
    denied = [r for r in rows if r.get("status") == "denied"]
    # One row for the unchanged outcome, not one per cycle (review 2026-10-18).
    assert len(denied) == 1 and denied[0]["tool"] == "delete_vm", denied
    assert "power" in str(denied[0]).lower()


@pytest.mark.unit
def test_ttl_daemon_power_off_check_that_fails_is_an_error_not_a_deny(monkeypatch, rows):
    def broken(*a, **k):
        raise OSError("rules.yaml unreadable")

    monkeypatch.setattr(scheduler, "guard", lambda skill, tool, *a, **k:
                        broken() if tool == "power_off_vm" else None)
    vm, calls = _vm(ON)
    monkeypatch.setattr(vm_lifecycle, "_require_vm", lambda si, n: vm)
    monkeypatch.setattr(scheduler, "_last_ttl_outcome", {}, raising=False)
    monkeypatch.setattr(scheduler, "get_expired_entries", lambda: [_expired()])
    monkeypatch.setattr(scheduler, "verify_ttl_identity", lambda si, e: None)
    monkeypatch.setattr(scheduler, "remove_entry", lambda n: pytest.fail("entry dropped"))

    scheduler._run_ttl_check(MagicMock())

    assert calls == []
    assert [r["status"] for r in rows if r.get("tool") == "delete_vm"] == ["error"]


def _rollback_step():
    return {"index": 1, "action": "delete_vm", "rollback": True,
            "params": {"vm_name": "made-by-plan", "instance_uuid": "uuid-1"}}


def _created_vm(state, uuid="uuid-1"):
    vm, calls = _vm(state)
    vm.config.instanceUuid = uuid
    return vm, calls


def _delete_created(monkeypatch, vm):
    from vmware_aiops.ops import plan_executor

    monkeypatch.setattr(vm_lifecycle, "_require_vm", lambda si, n: vm)
    monkeypatch.setattr(vm_lifecycle, "_wait_for_task", lambda task: None)
    return plan_executor._delete_created_vm(MagicMock(), _rollback_step()["params"],
                                            plan_gate.rollback_power_off_check("t"))


@pytest.mark.unit
def test_rollback_delete_of_a_running_vm_checks_power_off(harness, monkeypatch, rows):
    """Checked by the executor after its UUID check, just before the power-off."""
    _rules(harness, "power_off_vm")
    vm, calls = _created_vm(ON)
    with pytest.raises(GateRefusedError, match="power_off_vm"):
        _delete_created(monkeypatch, vm)
    assert calls == []
    assert any(r["tool"] == "power_off_vm" and r["status"] == "denied" for r in rows)


@pytest.mark.unit
def test_rollback_delete_of_an_off_vm_is_not_affected(harness, monkeypatch):
    _rules(harness, "power_off_vm")
    vm, calls = _created_vm(OFF)
    _delete_created(monkeypatch, vm)
    assert calls == ["destroy"]


@pytest.mark.unit
def test_rollback_of_a_different_vm_reports_identity_not_policy(harness, monkeypatch):
    """Review 2026-10-18: the UUID mismatch is the reason, not the power-off deny."""
    _rules(harness, "power_off_vm")
    vm, calls = _created_vm(ON, uuid="uuid-OTHER")
    monkeypatch.setattr("vmware_aiops.ops.inventory.find_vm_by_name", lambda si, n: vm)

    _, blockers, _ = plan_gate._gate([_rollback_step()], 0, MagicMock(), "t", False)
    assert blockers == []
    with pytest.raises(GateRefusedError, match="different VM"):
        _delete_created(monkeypatch, vm)
    assert calls == []


@pytest.mark.unit
def test_check_step_leaves_the_rollback_power_off_to_the_executor(harness, monkeypatch, rows):
    """One check, at the moment of the power-off: no second denied row from the gate."""
    _rules(harness, "power_off_vm")
    vm, _ = _created_vm(ON)
    monkeypatch.setattr("vmware_aiops.ops.inventory.find_vm_by_name", lambda si, n: vm)
    plan_gate.check_step(MagicMock(), _rollback_step(), target="t")
    assert not any(r.get("tool") == "power_off_vm" for r in rows)


@pytest.mark.unit
def test_rollback_preview_whose_power_state_cannot_be_read_shows_the_deny(harness, monkeypatch):
    _rules(harness, "power_off_vm")

    def boom(si, n):
        raise RuntimeError("lost connection")

    monkeypatch.setattr("vmware_aiops.ops.inventory.find_vm_by_name", boom)
    _, blockers, _ = plan_gate._gate([_rollback_step()], 0, MagicMock(), "t", False)
    assert any("power_off_vm" in b for b in blockers)


@pytest.mark.unit
def test_set_vm_ttl_reaches_power_off():
    from vmware_aiops.policy_roles import REACHES

    assert "power_off_vm" in REACHES["set_vm_ttl"]


@pytest.mark.unit
def test_rollback_preview_shows_the_power_off_deny(harness, monkeypatch):
    _rules(harness, "power_off_vm")
    vm, _ = _vm(ON)
    monkeypatch.setattr("vmware_aiops.ops.inventory.find_vm_by_name", lambda si, n: vm)

    fields, blockers, _ = plan_gate._gate([_rollback_step()], 0, MagicMock(), "t", False)

    assert fields == {"check": "refused by policy"}
    assert any("power_off_vm" in b for b in blockers)


# ── review 2026-10-18 round 2: check only what the call will do ──────────────


def _clean_slate_radius(state):
    return lambda si, n, s: {"blockers": [], "unmeasured": [], "power_state": state,
                             "powers_off_first": state == "poweredOn"}


@pytest.mark.unit
def test_clean_slate_of_an_off_vm_is_not_affected_by_a_power_off_deny(harness, monkeypatch):
    _rules(harness, "power_off_vm")
    monkeypatch.setattr("vmware_aiops.ops.ttl.measure_clean_slate", _clean_slate_radius("poweredOff"))
    ran = []
    monkeypatch.setattr("vmware_aiops.ops.vm_lifecycle.clean_slate",
                        lambda *a, **k: ran.append(k) or "x reverted to snapshot y")

    assert ttl_tools.restore_vm_clean_slate("web-01", target="t")["blast_radius"]["blockers"] == []
    out = ttl_tools.restore_vm_clean_slate("web-01", confirm=True, target="t")
    assert out["action"] == "reverted" and ran


@pytest.mark.unit
def test_clean_slate_of_a_running_vm_is_stopped_at_the_power_off(harness, monkeypatch, rows):
    _rules(harness, "power_off_vm")
    monkeypatch.setattr("vmware_aiops.ops.ttl.measure_clean_slate", _clean_slate_radius("poweredOn"))
    vm, calls = _vm(ON)
    monkeypatch.setattr(vm_lifecycle, "_require_vm", lambda si, n: vm)
    monkeypatch.setattr(vm_lifecycle, "resolve_snapshot", lambda vm, n: (MagicMock(), None))
    monkeypatch.setattr(vm_lifecycle, "_wait_for_task", lambda task: None)

    preview_blockers = ttl_tools.restore_vm_clean_slate("web-01", target="t")["blast_radius"]["blockers"]
    out = ttl_tools.restore_vm_clean_slate("web-01", confirm=True, target="t")

    assert any("power_off_vm" in b for b in preview_blockers)
    assert "error" in out and calls == []
    assert any(r["tool"] == "power_off_vm" and r["status"] == "denied" for r in rows)


@pytest.mark.unit
def test_an_exec_only_provision_is_not_affected_by_an_upload_deny(harness, monkeypatch):
    _rules(harness, "upload_vm_guest_file")
    monkeypatch.setattr(guest_tools, "measure_guest_provision",
                        lambda *a, **k: {"instance_uuid": "uuid-1", "blockers": [], "unmeasured": []})
    ran = []
    monkeypatch.setattr(guest_tools, "guest_provision", lambda *a, **k: ran.append(1) or {})
    steps = [{"type": "exec", "command": "id"}]

    assert guest_tools.provision_vm_guest("w", "root", "pw", steps,
                                          target="t")["blast_radius"]["blockers"] == []
    guest_tools.provision_vm_guest("w", "root", "pw", steps, confirm=True, target="t")
    assert ran == [1]


@pytest.mark.unit
def test_a_provision_with_an_upload_is_refused_by_an_upload_deny(harness, monkeypatch):
    _rules(harness, "upload_vm_guest_file")
    monkeypatch.setattr(guest_tools, "measure_guest_provision",
                        lambda *a, **k: {"blockers": [], "unmeasured": []})
    monkeypatch.setattr(guest_tools, "guest_provision", lambda *a, **k: pytest.fail("ran"))
    steps = [{"type": "exec", "command": "id"},
             {"type": "upload", "local_path": "/x", "guest_path": "/y"}]

    out = guest_tools.provision_vm_guest("w", "root", "pw", steps, confirm=True, target="t")
    assert "error" in out


@pytest.mark.unit
def test_an_unknown_provision_step_checks_every_guest_tool():
    from vmware_aiops.ops.step_policy import used_by_provision

    assert used_by_provision([{"type": "exec"}, {"type": "reboot"}]) is None
    assert used_by_provision([]) is None


@pytest.mark.unit
def test_set_vm_ttl_preview_and_confirm_ask_about_delete(harness, monkeypatch):
    """A TTL the daemon could never act on is shown and refused when it is set."""
    _rules(harness, "delete_vm")
    monkeypatch.setattr("vmware_aiops.ops.ttl.measure_ttl",
                        lambda si, n, m: {"blockers": [], "unmeasured": [],
                                          "power_state": "poweredOff", "instance_uuid": "u"})
    monkeypatch.setattr("vmware_aiops.ops.ttl.set_ttl", lambda *a, **k: pytest.fail("scheduled"))

    preview_out = ttl_tools.set_vm_ttl("web-01", 30, target="t")
    assert any("delete_vm" in b for b in preview_out["blast_radius"]["blockers"])
    assert "error" in ttl_tools.set_vm_ttl("web-01", 30, confirm=True, target="t")


@pytest.mark.unit
def test_set_vm_ttl_on_an_off_vm_ignores_a_power_off_deny(harness, monkeypatch):
    _rules(harness, "power_off_vm")
    monkeypatch.setattr("vmware_aiops.ops.ttl.measure_ttl",
                        lambda si, n, m: {"blockers": [], "unmeasured": [],
                                          "power_state": "poweredOff", "instance_uuid": "u"})
    monkeypatch.setattr("vmware_aiops.ops.ttl.set_ttl", lambda *a, **k: "TTL set")

    assert ttl_tools.set_vm_ttl("web-01", 30, confirm=True, target="t")["action"] == "scheduled"


def _ttl_writer(home: str, name: str, start) -> None:
    import json as _json
    import time as _time

    from vmware_aiops.ops import ttl as _ttl

    _ttl._TTL_FILE = __import__("pathlib").Path(home) / "ttl.json"
    real_load = _ttl._load_ttl_store

    def slow_load():
        store = real_load()
        _time.sleep(0.2)  # widen the lost-update window
        return store

    _ttl._load_ttl_store = slow_load
    start.wait()
    _ttl.set_ttl(name, 30, target="t", instance_uuid=name)
    _json.loads(_ttl._TTL_FILE.read_text(encoding="utf-8"))


@pytest.mark.unit
def test_ttl_entries_set_from_separate_processes_are_all_kept(tmp_path):
    """Review 2026-10-18: the MCP server and the daemon are separate processes."""
    import json
    import multiprocessing as mp

    ctx = mp.get_context("spawn")
    start = ctx.Event()
    procs = [ctx.Process(target=_ttl_writer, args=(str(tmp_path), f"vm{i}", start))
             for i in range(4)]
    for p in procs:
        p.start()
    start.set()
    for p in procs:
        p.join(60)
    assert all(p.exitcode == 0 for p in procs)
    store = json.loads((tmp_path / "ttl.json").read_text(encoding="utf-8"))
    assert sorted(store) == ["vm0", "vm1", "vm2", "vm3"]


# ── review 2026-10-18 round 3 ────────────────────────────────────────────────


def _window_rules(home):
    """Only a maintenance window, hours from now: high/critical work is outside it."""
    import datetime as _dt

    start = (_dt.datetime.now() + _dt.timedelta(hours=3)).strftime("%H:%M")
    end = (_dt.datetime.now() + _dt.timedelta(hours=4)).strftime("%H:%M")
    (home / "rules.yaml").write_text(
        f'maintenance_window:\n  start: "{start}"\n  end: "{end}"\n', encoding="utf-8")
    reset_policy_engine()


@pytest.mark.unit
def test_set_vm_ttl_outside_the_window_is_not_refused_for_the_window(harness, monkeypatch):
    """The deletion runs later; the daemon asks about the window at expiry."""
    _window_rules(harness)
    monkeypatch.setattr("vmware_aiops.ops.ttl.measure_ttl",
                        lambda si, n, m: {"blockers": [], "unmeasured": [],
                                          "power_state": "poweredOn", "instance_uuid": "u"})
    monkeypatch.setattr("vmware_aiops.ops.ttl.set_ttl", lambda *a, **k: "TTL set")
    from vmware_aiops.ops.step_policy import tool_refusal

    assert tool_refusal("delete_vm", {}, "t", label="x", record=False), "control: window applies"
    assert ttl_tools.set_vm_ttl("web-01", 600, target="t")["blast_radius"]["blockers"] == []
    assert ttl_tools.set_vm_ttl("web-01", 600, confirm=True, target="t")["action"] == "scheduled"


@pytest.mark.unit
def test_cli_set_ttl_is_refused_by_a_delete_deny_and_records_the_uuid(harness, monkeypatch):
    from typer.testing import CliRunner

    from vmware_aiops.cli import app

    monkeypatch.setattr("vmware_aiops.cli.vm._get_connection",
                        lambda *a, **k: (MagicMock(), MagicMock()))
    monkeypatch.setattr("vmware_aiops.ops.ttl.measure_ttl",
                        lambda si, n, m: {"instance_uuid": "uuid-7", "power_state": "poweredOff",
                                          "blockers": [], "unmeasured": []})
    recorded = []
    monkeypatch.setattr("vmware_aiops.ops.ttl.set_ttl",
                        lambda *a, **k: recorded.append(k) or "TTL set")
    monkeypatch.setattr("vmware_aiops.cli.vm._double_confirm", lambda *a, **k: None)

    ok = CliRunner().invoke(app, ["vm", "set-ttl", "web-01", "-m", "30", "--target", "t"])
    assert ok.exit_code == 0, ok.output
    assert recorded[0]["instance_uuid"] == "uuid-7"

    _rules(harness, "delete_vm")
    refused = CliRunner().invoke(app, ["vm", "set-ttl", "web-01", "-m", "30", "--target", "t"])
    assert refused.exit_code != 0 and "delete_vm" in refused.output
    assert len(recorded) == 1


@pytest.mark.unit
def test_an_unhashable_provision_step_type_checks_every_tool():
    from vmware_aiops.ops.step_policy import used_by_provision

    assert used_by_provision([{"type": ["exec"]}]) is None


@pytest.mark.unit
def test_a_ttl_entry_that_cannot_be_removed_does_not_stop_the_batch(monkeypatch):
    monkeypatch.setattr(scheduler, "_last_ttl_outcome", {}, raising=False)
    entries = [ttl.TTLEntry(vm_name=n, expires_at="2026-01-01T00:00:00+00:00", target="t",
                            instance_uuid=n) for n in ("a", "b")]
    monkeypatch.setattr(scheduler, "get_expired_entries", lambda: entries)
    monkeypatch.setattr(scheduler, "verify_ttl_identity", lambda si, e: None)
    monkeypatch.setattr(scheduler, "_audit", lambda *a, **k: None)
    deleted = []
    monkeypatch.setattr(scheduler, "delete_vm", lambda si, n, **k: deleted.append(n) or "ok")

    def remove(name):
        if name == "a":
            raise TimeoutError("store locked")

    monkeypatch.setattr(scheduler, "remove_entry", remove)
    scheduler._run_ttl_check(MagicMock())
    assert deleted == ["a", "b"]


@pytest.mark.unit
def test_rollback_plan_requires_a_power_off_check():
    import inspect

    from vmware_aiops.ops import plan_executor

    param = inspect.signature(plan_executor.rollback_plan).parameters["power_off_check"]
    assert param.default is inspect.Parameter.empty
