"""Regression tests — fixes from the 2026-06 code review.

Each test guards a verified bug:

- R1: _safe_error swallowed domain exceptions' teaching messages
- R2: _safe_error call sites passed a constant 'aiops' label instead of tool name
- R3: TTL entry was removed in `finally` — transient failure orphaned the VM
- R4: get_active_alarms collected propagated alarm states up to 4x (dedupe)
- R5: plan create_vm forwarded network_name=None, overriding the "VM Network" default
- R6: _find_triggered_alarm could Destroy() a container view twice
- R7: urlopen without timeout could hang the MCP stdio server forever
- R8: CLI commands dumped raw tracebacks for missing config / bad VM names
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

_REPO = Path(__file__).resolve().parents[3]


# ── R1: domain exceptions pass through _safe_error with teaching text ──


def test_safe_error_passes_domain_exception_messages() -> None:
    from vmware_aiops.mcp_server.server import _safe_error
    from vmware_aiops.ops.cluster_mgmt import ClusterError, ClusterNotFoundError
    from vmware_aiops.ops.guest_ops import GuestOpsError
    from vmware_aiops.ops.vm_lifecycle import TaskFailedError, VMNotFoundError

    msg = "VM 'web-99' not found. Run vm_list to see available VMs."
    assert msg in _safe_error(VMNotFoundError(msg), "vm_power_on")

    for exc_type in (GuestOpsError, TaskFailedError, ClusterNotFoundError, ClusterError):
        teaching = f"{exc_type.__name__}: teaching message with fix hint"
        assert "teaching message with fix hint" in _safe_error(exc_type(teaching), "tool")

    assert "took too long" in _safe_error(TimeoutError("took too long"), "tool")


def test_safe_error_passes_connection_error_through() -> None:
    # issue #24: a dropped connection must surface its teaching hint through
    # MCP, matching the CLI path (which catches OSError). Before the fix it
    # was masked to a generic "operation failed".
    from vmware_aiops.mcp_server.server import _safe_error

    hint = "Connection to vcenter-prod dropped. Run 'vmware-aiops doctor'."
    out = _safe_error(ConnectionError(hint), "vm_power_on")
    assert hint in out
    assert "operation failed" not in out


def test_safe_error_still_masks_unknown_exceptions() -> None:
    from vmware_aiops.mcp_server.server import _safe_error

    out = _safe_error(RuntimeError("internal host:port leak"), "tool")
    assert "host:port" not in out
    assert "operation failed" in out


# ── R2: error handling labels errors with the enclosing tool name ──


def test_tool_errors_decorator_labels_with_function_name() -> None:
    """The repeated try/except → _safe_error blocks are collapsed into the
    @tool_errors decorator, which derives the label from func.__name__ — so the
    R2 invariant (label == tool name) now holds by construction, for every shape.
    """
    from vmware_aiops.mcp_server._shared import tool_errors

    captured = {}

    def _probe_safe_error(exc, tool):
        captured["tool"] = tool
        return "boom"

    with patch("vmware_aiops.mcp_server._shared._safe_error", side_effect=_probe_safe_error):
        @tool_errors("str")
        def vm_power_on(vm_name: str):  # name must reach _safe_error verbatim
            raise RuntimeError("x")

        out = vm_power_on("v")

    assert captured["tool"] == "vm_power_on"
    assert out == "Error: boom Run 'vmware-aiops doctor' to verify connectivity and credentials."


def test_no_tool_module_passes_a_hardcoded_safe_error_label() -> None:
    """Defence in depth: tool modules must rely on @tool_errors (no inline
    _safe_error(e, '...') call sites that could drift from the tool name)."""
    tools_dir = _REPO / "vmware_aiops" / "mcp_server" / "tools"
    offenders = []
    for path in sorted(tools_dir.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_safe_error"
            ):
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, (
        "tool modules must use @tool_errors, not inline _safe_error(): "
        + ", ".join(offenders)
    )


# ── R3: TTL entry survives a transient deletion failure ──


def _ttl_entry(vm_name: str = "ttl-vm"):
    from vmware_aiops.ops.ttl import TTLEntry

    return TTLEntry(vm_name=vm_name, expires_at="2026-01-01T00:00:00+00:00", target=None)


def test_ttl_entry_kept_on_transient_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from vmware_aiops.scanner import scheduler

    removed: list[str] = []
    monkeypatch.setattr(scheduler, "get_expired_entries", lambda: [_ttl_entry()])
    monkeypatch.setattr(scheduler, "remove_entry", removed.append)
    monkeypatch.setattr(
        scheduler, "delete_vm",
        lambda si, name: (_ for _ in ()).throw(RuntimeError("vCenter task timeout")),
    )

    scheduler._run_ttl_check(MagicMock())
    assert removed == [], "transient failure must NOT drop the TTL entry"


def test_ttl_entry_removed_when_vm_already_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    from vmware_aiops.ops.vm_lifecycle import VMNotFoundError
    from vmware_aiops.scanner import scheduler

    removed: list[str] = []
    monkeypatch.setattr(scheduler, "get_expired_entries", lambda: [_ttl_entry()])
    monkeypatch.setattr(scheduler, "remove_entry", removed.append)
    monkeypatch.setattr(
        scheduler, "delete_vm",
        lambda si, name: (_ for _ in ()).throw(VMNotFoundError("VM 'ttl-vm' not found")),
    )

    scheduler._run_ttl_check(MagicMock())
    assert removed == ["ttl-vm"], "VM already gone — stale entry must be dropped"


def test_ttl_entry_removed_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    from vmware_aiops.scanner import scheduler

    removed: list[str] = []
    monkeypatch.setattr(scheduler, "get_expired_entries", lambda: [_ttl_entry()])
    monkeypatch.setattr(scheduler, "remove_entry", removed.append)
    monkeypatch.setattr(scheduler, "delete_vm", lambda si, name: f"VM '{name}' deleted")

    scheduler._run_ttl_check(MagicMock())
    assert removed == ["ttl-vm"]


# ── R4: get_active_alarms returns each triggered alarm exactly once ──


def test_get_active_alarms_no_duplicates_from_propagation() -> None:
    from pyVmomi import vim

    from tests.eval.regression._pc_fakes import NoLazyMO, make_si
    from vmware_aiops.ops.health import get_active_alarms

    host = SimpleNamespace(name="esxi-1")
    alarm_state = SimpleNamespace(
        overallStatus="red",
        alarm=SimpleNamespace(info=SimpleNamespace(name="Host CPU usage")),
        entity=host,
        time="2026-06-11 00:00:00",
        acknowledged=False,
    )

    # The same alarm state is aggregated by rootFolder AND propagated to the host
    # container view — both fetched via batched PropertyCollector.
    si = make_si({
        vim.Folder: [(NoLazyMO("root"), {"triggeredAlarmState": [alarm_state]})],
        vim.Datacenter: [],
        vim.ClusterComputeResource: [],
        vim.HostSystem: [
            (NoLazyMO("host:esxi-1"), {"triggeredAlarmState": [alarm_state]}),
        ],
    })

    alarms = get_active_alarms(si)

    assert len(alarms) == 1, f"propagated alarm must be deduplicated, got {len(alarms)}"
    assert alarms[0]["alarm_name"] == "Host CPU usage"
    assert alarms[0]["entity_name"] == "esxi-1"
    assert alarms[0]["severity"] == "critical"


# ── R5: plan create_vm drops None params so create_vm defaults apply ──


def test_plan_create_vm_drops_none_optional_params(monkeypatch: pytest.MonkeyPatch) -> None:
    from vmware_aiops.ops import plan_executor, vm_lifecycle

    captured: dict = {}

    def _fake_create_vm(si, vm_name, **kwargs):
        captured["vm_name"] = vm_name
        captured["kwargs"] = kwargs
        return f"VM '{vm_name}' created"

    monkeypatch.setattr(vm_lifecycle, "create_vm", _fake_create_vm)

    # network_name omitted entirely + explicit None datastore: both must be dropped
    plan_executor._dispatch(
        None, "create_vm",
        {"vm_name": "web-01", "cpu": 4, "datastore_name": None},
    )
    assert captured["vm_name"] == "web-01"
    assert captured["kwargs"]["cpu"] == 4
    assert "network_name" not in captured["kwargs"], (
        "None network_name must be dropped so create_vm's 'VM Network' default applies"
    )
    assert "datastore_name" not in captured["kwargs"]

    # explicit values still pass through
    plan_executor._dispatch(
        None, "create_vm",
        {"vm_name": "web-02", "network_name": "prod-net"},
    )
    assert captured["kwargs"]["network_name"] == "prod-net"


# ── R6: _find_triggered_alarm destroys each container view exactly once ──


def test_find_triggered_alarm_destroys_container_exactly_once() -> None:
    from pyVmomi import vim

    from tests.eval.regression._pc_fakes import NoLazyMO, make_si
    from vmware_aiops.ops.alarm_mgmt import _find_triggered_alarm

    # Entity matches the searched name but carries no matching alarm — the search
    # exhausts all types; each per-type container view must be destroyed once.
    fixtures = {
        t: [(NoLazyMO(f"{t.__name__}:vm-1"), {"name": "vm-1", "triggeredAlarmState": []})]
        for t in (
            vim.VirtualMachine,
            vim.HostSystem,
            vim.ClusterComputeResource,
            vim.Datacenter,
            vim.Datastore,
        )
    }
    si = make_si(fixtures)

    with pytest.raises(ValueError, match="not found"):
        _find_triggered_alarm(si, "vm-1", "Some Alarm")

    assert si.views, "expected container views to be created"
    for stub in si.views:
        assert stub.calls == 1, (
            f"container view destroyed {stub.calls}x, expected exactly 1"
        )


# ── R7: every urlopen call passes a timeout ──


@pytest.mark.parametrize(
    "rel_path",
    [
        "vmware_aiops/ops/guest_ops.py",
        "vmware_aiops/ops/ova_deploy.py",
    ],
)
def test_urlopen_calls_have_timeout(rel_path: str) -> None:
    src = (_REPO / rel_path).read_text(encoding="utf-8")
    tree = ast.parse(src)
    found = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name != "urlopen":
            continue
        found += 1
        kw_names = {kw.arg for kw in node.keywords}
        assert "timeout" in kw_names, (
            f"{rel_path}:{node.lineno}: urlopen without timeout can hang the MCP server"
        )
    assert found > 0, f"expected urlopen calls in {rel_path}"


# ── R8: CLI prints one-line teaching error instead of traceback ──


def test_cli_missing_config_prints_one_line_error(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from vmware_aiops.cli import app

    missing = tmp_path / "no-such-config.yaml"
    result = CliRunner().invoke(
        app, ["vm", "power-on", "web-01", "--config", str(missing)]
    )

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "Error:" in result.output
    assert "Config file not found" in result.output


def test_cli_domain_exception_prints_teaching_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from vmware_aiops.cli import _common, app
    from vmware_aiops.ops.vm_lifecycle import VMNotFoundError

    def _fake_get_connection(target, config_path=None):
        raise VMNotFoundError("VM 'web-99' not found. Did you mean 'web-09'?")

    monkeypatch.setattr(_common, "_get_connection", _fake_get_connection)
    # vm.py imports _get_connection by name at module import — patch there too.
    from vmware_aiops.cli import vm as vm_module

    monkeypatch.setattr(vm_module, "_get_connection", _fake_get_connection)

    result = CliRunner().invoke(app, ["vm", "power-on", "web-99"])

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "web-99" in result.output
