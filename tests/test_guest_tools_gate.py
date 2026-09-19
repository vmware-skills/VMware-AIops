"""The four guest-writing MCP tools sit behind the HLD §7 gate (revised 2026-09-16).

``vm_guest_exec``, ``vm_guest_exec_output``, ``vm_guest_upload`` and
``vm_guest_provision`` hand caller-supplied content to a guest OS. Until this
change each of them acted on the first call.

* L2 — a bare call previews: nothing is started in the guest, nothing is
  transferred, and the ops function the CLI also calls is not reached.
* L1 — preview and acting response both carry ``blast_radius``: the VM
  (name + instance UUID), the guest account (never the password), what would
  run or be written, and ``blockers`` / ``unmeasured``.
* L3 — ``confirm=True`` refuses on a blocker (VMware Tools not running, VM not
  powered on, local file missing, a malformed provisioning step) or on anything
  the blast radius could not read.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, PropertyMock

import pytest
from pyVmomi import vim
from vmware_policy.budget import reset_budget
from vmware_policy.policy import reset_policy_engine
from vmware_policy.undo import reset_undo_store

import vmware_aiops.mcp_server.tools.guest as guest_tools
from vmware_aiops.ops import inventory

ON = vim.VirtualMachine.PowerState.poweredOn
OFF = vim.VirtualMachine.PowerState.poweredOff
PASSWORD = "s3cret-Guest-Pw!"

GATED = ("vm_guest_exec", "vm_guest_exec_output", "vm_guest_upload", "vm_guest_provision")


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


def _vm(name="web-01", power=ON, tools="guestToolsRunning", family="linuxGuest",
        instance_uuid="5012-aaaa"):
    vm = MagicMock(name=f"vm:{name}")
    vm.name = name
    vm.runtime.powerState = power
    vm.guest.toolsRunningStatus = tools
    vm.guest.guestFamily = family
    if instance_uuid is None:
        vm.config = None
    else:
        vm.config.instanceUuid = instance_uuid
    return vm


class _Calls:
    """Records calls to the ops functions the tools reach when they act."""

    def __init__(self):
        self.calls: list[tuple[str, tuple, dict]] = []

    def names(self):
        return [c[0] for c in self.calls]


@pytest.fixture
def world(monkeypatch):
    """One VM in inventory, a mock SI, and recorders in place of the ops writers."""
    si = MagicMock(name="si")
    rec = _Calls()

    def install(*vms):
        monkeypatch.setattr(
            inventory, "_collect",
            lambda _si, _types, _props: [(vm, {"name": vm.name}) for vm in vms],
        )
        return vms

    monkeypatch.setattr(guest_tools, "_get_connection", lambda _t=None: si)

    def fake(name, result):
        def _f(*a, **k):
            rec.calls.append((name, a, k))
            return result
        return _f

    monkeypatch.setattr(guest_tools, "guest_exec", fake(
        "guest_exec", {"exit_code": 0, "stdout": "", "stderr": "", "timed_out": False,
                       "command": "/bin/true", "pid": 7}))
    monkeypatch.setattr(guest_tools, "guest_exec_with_output", fake(
        "guest_exec_with_output", {"exit_code": 0, "stdout": "ok", "stderr": "",
                                   "timed_out": False, "command": "df -h",
                                   "os_family": "linuxGuest"}))
    monkeypatch.setattr(guest_tools, "guest_upload", fake(
        "guest_upload", "Uploaded 5 bytes to /etc/motd on VM 'web-01'"))
    monkeypatch.setattr(guest_tools, "guest_provision", fake(
        "guest_provision", {"success": True, "completed_steps": 1, "total_steps": 1,
                            "results": [], "error": None}))
    install(_vm())
    return type("W", (), {"si": si, "rec": rec, "install": staticmethod(install)})


@pytest.fixture
def local_file(tmp_path):
    p = tmp_path / "motd"
    p.write_bytes(b"hello")
    return p


def _exec(**kw):
    return guest_tools.vm_guest_exec(
        "web-01", "/bin/bash", "ops", arguments="-c 'whoami'", password=PASSWORD,
        working_directory="/tmp", **kw)


def _exec_output(**kw):
    return guest_tools.vm_guest_exec_output("web-01", "df -h", "ops", password=PASSWORD, **kw)


def _upload(local, **kw):
    return guest_tools.vm_guest_upload(
        "web-01", str(local), "/etc/motd", "ops", password=PASSWORD, **kw)


def _provision(local, steps=None, **kw):
    steps = steps if steps is not None else [
        {"type": "exec", "command": "apt-get install -y nginx"},
        {"type": "upload", "local_path": str(local), "guest_path": "/etc/nginx/x.conf"},
        {"type": "service", "name": "nginx", "action": "restart"},
        {"type": "exec", "command": "nginx -t"},
    ]
    return guest_tools.vm_guest_provision("web-01", "ops", PASSWORD, steps, **kw)


def _call(tool, local, **kw):
    return {
        "vm_guest_exec": lambda: _exec(**kw),
        "vm_guest_exec_output": lambda: _exec_output(**kw),
        "vm_guest_upload": lambda: _upload(local, **kw),
        "vm_guest_provision": lambda: _provision(local, **kw),
    }[tool]()


def _no_guest_write(si):
    gom = si.RetrieveContent.return_value.guestOperationsManager
    gom.processManager.StartProgramInGuest.assert_not_called()
    gom.fileManager.InitiateFileTransferToGuest.assert_not_called()


# ─── L2: a bare call changes nothing ──────────────────────────────────────────


@pytest.mark.parametrize("tool", GATED)
def test_bare_call_previews_and_writes_nothing(world, local_file, tool):
    out = _call(tool, local_file)
    assert out["action"] == "preview", out
    assert "blast_radius" in out and out["hint"]
    assert world.rec.calls == [], f"{tool} reached the ops writer on a bare call"
    _no_guest_write(world.si)


@pytest.mark.parametrize("tool", GATED)
def test_schema_defaults_confirm_to_false(tool):
    from vmware_aiops.mcp_server.server import mcp

    t = next(t for t in asyncio.run(mcp.list_tools()) if t.name == tool)
    prop = t.inputSchema["properties"]["confirm"]
    assert prop["default"] is False
    assert "confirm" not in t.inputSchema.get("required", [])


@pytest.mark.parametrize("tool", GATED)
def test_docstring_carries_the_normative_wording(tool):
    doc = getattr(guest_tools, tool).__doc__
    assert doc.startswith("[WRITE]")
    assert "confirm: False (default) returns the blast radius and changes nothing. " \
           "True applies it." in " ".join(doc.split())
    assert "Do not set confirm=True on your own" in " ".join(doc.split())


# ─── L1: the blast radius ─────────────────────────────────────────────────────


@pytest.mark.parametrize("tool", GATED)
def test_blast_radius_names_the_vm_and_account_never_the_password(world, local_file, tool):
    br = _call(tool, local_file)["blast_radius"]
    assert br["vm"] == "web-01"
    assert br["instance_uuid"] == "5012-aaaa"
    assert br["username"] == "ops"
    assert br["tools_running_status"] == "guestToolsRunning"
    assert br["power_state"] == "poweredOn"
    assert br["blockers"] == [] and br["unmeasured"] == []
    assert PASSWORD not in repr(br)
    assert "password" not in br


def test_exec_radius_carries_command_arguments_and_directory(world, local_file):
    br = _exec()["blast_radius"]
    assert br["command"] == "/bin/bash"
    assert br["arguments"] == "-c 'whoami'"
    assert br["working_directory"] == "/tmp"


def test_exec_radius_caps_a_huge_command(world):
    # Kept just past the cap: vmware-policy's audit redaction is superlinear in
    # parameter length (50k chars took minutes), which is a policy-side issue.
    from vmware_aiops.ops.guest_gate import MAX_COMMAND_CHARS

    br = guest_tools.vm_guest_exec("web-01", "/bin/sh", "ops",
                                   arguments="x" * (MAX_COMMAND_CHARS + 200),
                                   password=PASSWORD)["blast_radius"]
    assert len(br["arguments"]) <= MAX_COMMAND_CHARS


def test_exec_output_radius_carries_command_and_shell(world):
    br = _exec_output()["blast_radius"]
    assert br["command"] == "df -h"
    assert br["shell"] == "/bin/sh -c"
    assert br["os_family"] == "linuxGuest"
    assert br["timeout_s"] == 300


def test_exec_output_radius_uses_cmd_on_windows(world):
    world.install(_vm(family="windowsGuest"))
    br = _exec_output()["blast_radius"]
    assert br["shell"].startswith("C:\\Windows\\System32\\cmd.exe")


def test_upload_radius_carries_paths_and_size(world, local_file):
    br = _upload(local_file)["blast_radius"]
    assert br["local_path"] == str(local_file)
    assert br["local_size_bytes"] == 5
    assert br["guest_path"] == "/etc/motd"
    assert br["overwrites_existing_guest_file"] is True


def test_provision_radius_lists_every_step_with_counts(world, local_file):
    br = _provision(local_file)["blast_radius"]
    assert br["step_count"] == 4
    assert br["steps_by_type"] == {"exec": 2, "upload": 1, "service": 1}
    steps = br["steps"]
    assert [s["step"] for s in steps] == [1, 2, 3, 4]
    assert steps[0] == {"step": 1, "type": "exec", "command": "apt-get install -y nginx",
                        "command_length": 24, "truncated": False}
    assert steps[1]["local_path"] == str(local_file)
    assert steps[1]["local_size_bytes"] == 5
    assert steps[1]["guest_path"] == "/etc/nginx/x.conf"
    assert steps[2]["command"] == "systemctl restart nginx"
    assert steps[3]["command"] == "nginx -t"


def test_provision_lists_every_step_even_past_sixteen(world, local_file):
    steps = [{"type": "exec", "command": f"echo {i}"} for i in range(40)]
    br = _provision(local_file, steps=steps)["blast_radius"]
    assert len(br["steps"]) == 40 and br["step_count"] == 40


# ─── acting: confirm=True runs once and still reports the radius ──────────────


@pytest.mark.parametrize("tool,op", [
    ("vm_guest_exec", "guest_exec"),
    ("vm_guest_exec_output", "guest_exec_with_output"),
    ("vm_guest_upload", "guest_upload"),
    ("vm_guest_provision", "guest_provision"),
])
def test_confirm_acts_once_and_returns_blast_radius(world, local_file, tool, op):
    out = _call(tool, local_file, confirm=True)
    assert world.rec.names() == [op]
    assert out["blast_radius"]["vm"] == "web-01"
    assert out["action"] != "preview"
    assert "error" not in out or out["error"] is None


def test_confirmed_exec_passes_the_real_password_to_the_ops_call(world):
    _exec(confirm=True)
    _, args, kwargs = world.rec.calls[0]
    assert PASSWORD in args
    assert kwargs["arguments"] == "-c 'whoami'"
    assert kwargs["working_directory"] == "/tmp"


def test_upload_now_returns_a_dict(world, local_file):
    out = _upload(local_file, confirm=True)
    assert isinstance(out, dict)
    assert out["action"] == "uploaded"
    assert "Uploaded 5 bytes" in out["message"]


# ─── L3: blockers refuse ──────────────────────────────────────────────────────


@pytest.mark.parametrize("tool", GATED)
def test_tools_not_running_is_a_blocker_and_refuses(world, local_file, tool):
    world.install(_vm(tools="guestToolsNotRunning"))
    preview = _call(tool, local_file)
    assert any("VMware Tools" in b for b in preview["blast_radius"]["blockers"])
    out = _call(tool, local_file, confirm=True)
    assert "VMware Tools" in out["error"]
    assert "refused" in out["error"]
    assert world.rec.calls == []


@pytest.mark.parametrize("tool", GATED)
def test_powered_off_vm_is_a_blocker_and_refuses(world, local_file, tool):
    world.install(_vm(power=OFF))
    assert _call(tool, local_file)["blast_radius"]["blockers"]
    out = _call(tool, local_file, confirm=True)
    assert "vm_power_on" in out["error"]
    assert world.rec.calls == []


@pytest.mark.parametrize("tool", GATED)
def test_unreadable_identity_refuses(world, local_file, tool):
    world.install(_vm(instance_uuid=None))
    assert _call(tool, local_file)["blast_radius"]["unmeasured"] == ["instance_uuid"]
    out = _call(tool, local_file, confirm=True)
    assert "could not read instance_uuid" in out["error"]
    assert world.rec.calls == []


@pytest.mark.parametrize("tool", GATED)
def test_unreadable_power_state_refuses(world, local_file, tool):
    vm = _vm()
    type(vm.runtime).powerState = PropertyMock(side_effect=vim.fault.NoPermission())
    world.install(vm)
    assert "power_state" in _call(tool, local_file)["blast_radius"]["unmeasured"]
    out = _call(tool, local_file, confirm=True)
    assert "could not read" in out["error"]
    assert world.rec.calls == []


@pytest.mark.parametrize("tool", GATED)
def test_unreadable_tools_status_refuses(world, local_file, tool):
    vm = _vm()
    type(vm.guest).toolsRunningStatus = PropertyMock(side_effect=vim.fault.NoPermission())
    world.install(vm)
    assert "tools_running_status" in _call(tool, local_file)["blast_radius"]["unmeasured"]
    out = _call(tool, local_file, confirm=True)
    assert "could not read" in out["error"]
    assert world.rec.calls == []


def test_upload_missing_local_file_refuses(world, tmp_path):
    missing = tmp_path / "nope"
    br = _upload(missing)["blast_radius"]
    assert any("not an existing regular file" in b for b in br["blockers"])
    out = _upload(missing, confirm=True)
    assert "not an existing regular file" in out["error"]
    assert world.rec.calls == []


def test_upload_unreadable_local_file_refuses(world, local_file, monkeypatch):
    import vmware_aiops.ops.guest_gate as gate_mod

    monkeypatch.setattr(gate_mod.os, "access", lambda *_a, **_k: False)
    out = _upload(local_file, confirm=True)
    assert "Cannot read local upload source" in out["error"]
    assert world.rec.calls == []


@pytest.mark.parametrize("steps,needle", [
    ([], "no steps"),
    ([{"type": "reboot"}], "step 1: unknown type 'reboot'"),
    ([{"type": "exec"}], "step 1: missing 'command'"),
    ([{"type": "upload", "guest_path": "/x"}], "step 1: missing 'local_path'"),
    ([{"type": "service"}], "step 1: missing 'name'"),
    (["apt-get update"], "step 1: not an object"),
])
def test_provision_malformed_steps_refuse(world, local_file, steps, needle):
    assert any(needle in b for b in _provision(local_file, steps=steps)["blast_radius"]["blockers"])
    out = _provision(local_file, steps=steps, confirm=True)
    assert needle in out["error"]
    assert world.rec.calls == []


def test_provision_upload_step_with_missing_file_refuses(world, tmp_path):
    steps = [{"type": "exec", "command": "true"},
             {"type": "upload", "local_path": str(tmp_path / "gone"), "guest_path": "/x"}]
    out = _provision(tmp_path, steps=steps, confirm=True)
    assert "step 2" in out["error"] and "not an existing regular file" in out["error"]
    assert world.rec.calls == []


def test_provision_service_step_on_windows_refuses(world, local_file):
    world.install(_vm(family="windowsGuest"))
    out = _provision(local_file, steps=[{"type": "service", "name": "w3svc"}], confirm=True)
    assert "systemctl" in out["error"]
    assert world.rec.calls == []


def test_ambiguous_name_reaches_the_caller(world, local_file):
    world.install(_vm(), _vm(instance_uuid="5012-bbbb"))
    out = _exec(confirm=True)
    assert "2 VMs are named 'web-01'" in out["error"]
    assert world.rec.calls == []


def test_missing_vm_teaches_the_next_step(world):
    world.install()
    out = _exec()
    assert "not found" in out["error"] and "list_virtual_machines" in out["error"]


# ─── refusals are audited as failures ─────────────────────────────────────────


@pytest.fixture
def audit_rows(monkeypatch):
    rows: list[dict] = []

    class _Recorder:
        def log(self, **kw):
            rows.append(kw)

    monkeypatch.setattr("vmware_policy.guard.get_engine", lambda: _Recorder())
    return rows


def test_refusal_is_audited_as_a_failure_and_password_is_redacted(world, audit_rows):
    world.install(_vm(tools="guestToolsNotRunning"))
    _exec(confirm=True)
    assert audit_rows and audit_rows[0]["status"] == "error"
    assert PASSWORD not in repr(audit_rows)


def test_preview_is_audited_as_dry_run(world, audit_rows):
    _exec()
    # vmware-policy >= 1.17.0 records a confirm=False preview as "dry_run".
    assert audit_rows and audit_rows[0]["status"] == "dry_run"
