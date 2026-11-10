"""Final backend sizing controls the task, not a model's claimed percentage."""

from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from vmware_policy.policy import PolicyDenied, PolicyEngine
from vmware_aiops.ops import vm_lifecycle as ops


@pytest.fixture
def vm(monkeypatch, tmp_path):
    from vmware_policy import policy

    rules = tmp_path / "rules.yaml"
    rules.write_text(
        "change_limits:\n  max_cpu_change_pct: 25\n  max_memory_change_pct: 50\n", encoding="utf-8"
    )
    engine = PolicyEngine(rules)
    monkeypatch.setattr(policy, "_engine", engine)
    vm = SimpleNamespace(
        config=SimpleNamespace(
            hardware=SimpleNamespace(numCPU=4, memoryMB=4096), changeVersion="version-7"
        ),
        ReconfigVM_Task=Mock(),
    )
    monkeypatch.setattr(ops, "_require_vm", lambda *a, **k: vm)
    monkeypatch.setattr(ops, "_wait_for_task", lambda _: None)
    return vm


@pytest.mark.parametrize("changes", [{"cpu": 8}, {"cpu": 2}, {"memory_mb": 8192}])
def test_over_limit_never_starts_a_task(vm, changes):
    with pytest.raises(PolicyDenied, match="exceeds"):
        ops.reconfigure_vm(object(), "fixture", **changes)
    vm.ReconfigVM_Task.assert_not_called()


def test_exact_limits_bind_same_configuration_version(vm):
    ops.reconfigure_vm(object(), "fixture", cpu=5, memory_mb=6144)
    spec = vm.ReconfigVM_Task.call_args.kwargs["spec"]
    assert spec.numCPUs == 5 and spec.memoryMB == 6144
    assert spec.changeVersion == "version-7"
    assert vm.ReconfigVM_Task.call_count == 1


def test_missing_version_does_not_start_unbound_resize(vm):
    vm.config.changeVersion = None
    with pytest.raises(PolicyDenied, match="changeVersion"):
        ops.reconfigure_vm(object(), "fixture", cpu=5)
    vm.ReconfigVM_Task.assert_not_called()


def test_product_rejecting_stale_version_is_not_retried(vm):
    from pyVmomi import vim

    vm.ReconfigVM_Task.side_effect = vim.fault.ConcurrentAccess()
    with pytest.raises(vim.fault.ConcurrentAccess):
        ops.reconfigure_vm(object(), "fixture", cpu=5)
    assert vm.ReconfigVM_Task.call_count == 1


def test_mcp_limit_refusal_is_audited_as_denied(vm, monkeypatch):
    from vmware_aiops.mcp_server.tools import vm as tools
    from vmware_policy import decorators

    recorded = []
    monkeypatch.setattr(tools, "_get_connection", lambda _: object())
    monkeypatch.setattr(decorators, "audit_call", lambda *a, **kw: recorded.append(kw))
    with pytest.raises(PolicyDenied, match="exceeds"):
        tools.reconfigure_vm("fixture", cpu=8, target="fixture-target")
    vm.ReconfigVM_Task.assert_not_called()
    assert recorded and recorded[-1]["status"] == "denied"
    assert recorded[-1]["result"]["rule"] == "change_limits"


def _mcp_resize(cpu):
    import asyncio
    import mcp.types as types
    from vmware_aiops.mcp_server.server import mcp

    handler = mcp._mcp_server.request_handlers[types.CallToolRequest]
    request = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(
            name="reconfigure_vm", arguments={"vm_name": "fixture", "cpu": cpu, "target": "fixture-target"}
        ),
    )
    return asyncio.run(handler(request)).root


def test_denial_reaches_the_mcp_frame(vm, monkeypatch):
    from vmware_aiops.mcp_server.tools import vm as tools
    monkeypatch.setattr(tools, "_get_connection", lambda _: object())
    result = _mcp_resize(8)
    assert result.isError is True
    assert "max_cpu_change_pct" in " ".join(getattr(c, "text", "") for c in result.content)
    vm.ReconfigVM_Task.assert_not_called()


def test_allowed_boundary_remains_success_on_the_wire(vm, monkeypatch):
    from vmware_aiops.mcp_server.tools import vm as tools
    monkeypatch.setattr(tools, "_get_connection", lambda _: object())
    result = _mcp_resize(5)
    assert result.isError is False
    assert vm.ReconfigVM_Task.call_count == 1
    assert vm.ReconfigVM_Task.call_args.kwargs["spec"].changeVersion == "version-7"


def test_post_clone_limit_refusal_stops_later_steps_without_recloning(vm, monkeypatch):
    from vmware_aiops.ops import vm_deploy
    clone = Mock(return_value="created")
    power = Mock()
    snapshot = Mock()
    monkeypatch.setattr(vm_deploy, "find_vm_by_name", lambda *a: object())
    monkeypatch.setattr(vm_deploy, "clone_vm", clone)
    monkeypatch.setattr(vm_deploy, "power_on_vm", power)
    monkeypatch.setattr(vm_deploy, "create_snapshot", snapshot)
    result = vm_deploy.batch_clone(object(), "source", ["new-vm"], cpu=8, snapshot_name="baseline", power_on=True)
    assert result[0]["status"] == "error"
    assert "max_cpu_change_pct" in str(result[0]["messages"])
    clone.assert_called_once()
    power.assert_not_called()
    snapshot.assert_not_called()
    vm.ReconfigVM_Task.assert_not_called()
