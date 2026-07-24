"""v1.5.25 regression tests — clone/migrate targeting, MCP tools, Python 3.10 compat.

Each test guards a specific production bug. Failures here block release.

Bugs prevented:
- Clone lands on template host (no target_host param)
- Migrate fails with no shared storage (no target_datastore param)
- MCP server missing vm_clone / vm_migrate / vm_delete / vm_snapshot_*
- cli/mcp_config.py NameError on json
- _wait_for_task drops fault chain
- FastMCP schema build crashes on Python 3.10 (PEP 604 unions)
"""
from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

import pytest


# ── Bug A: clone_vm must accept target_host / target_datastore ──


def test_clone_vm_accepts_target_host_and_datastore() -> None:
    from vmware_aiops.ops.vm_lifecycle import clone_vm

    params = inspect.signature(clone_vm).parameters
    assert "target_host" in params, "clone_vm must accept target_host (else clone lands on template host)"
    assert "target_datastore" in params, "clone_vm must accept target_datastore"


def test_clone_vm_passes_target_host_to_relocate_spec() -> None:
    """Verify target_host actually reaches vim.vm.RelocateSpec.host.

    Uses a stub RelocateSpec/CloneSpec because pyVmomi's strict type checking
    rejects MagicMock for vim.HostSystem fields.
    """
    from vmware_aiops.ops import vm_lifecycle

    captured = {}

    class StubRelocateSpec:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class StubCloneSpec:
        def __init__(self, location=None, powerOn=False, template=False, **kw):
            self.location = location
            captured["spec"] = self

    mock_si = MagicMock()
    mock_vm = MagicMock()
    mock_host = MagicMock(name="HostSystem")
    mock_host.parent.resourcePool = MagicMock(name="ResourcePool")

    with patch.object(vm_lifecycle, "_require_vm", return_value=mock_vm), \
         patch.object(vm_lifecycle, "find_host_by_name", return_value=mock_host), \
         patch.object(vm_lifecycle, "_wait_for_task", return_value=None), \
         patch.object(vm_lifecycle.vim.vm, "RelocateSpec", StubRelocateSpec), \
         patch.object(vm_lifecycle.vim.vm, "CloneSpec", StubCloneSpec):
        vm_lifecycle.clone_vm(mock_si, "src", "new", target_host="esxi-home")

    spec = captured["spec"]
    assert spec.location.host is mock_host, "target_host did not reach RelocateSpec.host"
    assert spec.location.pool is mock_host.parent.resourcePool, "pool not set from target_host"


# ── Bug B: migrate_vm cross-storage requires target_datastore ──


def test_migrate_vm_accepts_target_datastore() -> None:
    from vmware_aiops.ops.vm_lifecycle import migrate_vm

    params = inspect.signature(migrate_vm).parameters
    assert "target_datastore" in params, "migrate_vm must accept target_datastore for cross-storage vMotion"


def test_migrate_vm_warns_when_target_host_cannot_see_source_datastore() -> None:
    """No shared storage between hosts → must instruct user to pass --to-datastore."""
    from vmware_aiops.ops import vm_lifecycle

    mock_si = MagicMock()
    mock_vm = MagicMock()
    mock_target_host = MagicMock()
    mock_target_host.parent.resourcePool = MagicMock()
    src_host = MagicMock()
    src_ds = MagicMock()
    src_ds.name = "office-nas"
    src_ds.host = [src_host]  # target_host NOT in this list
    mock_vm.runtime.host.name = "esxi-office"
    mock_vm.datastore = [src_ds]

    with patch.object(vm_lifecycle, "_require_vm", return_value=mock_vm), \
         patch.object(vm_lifecycle, "find_host_by_name", return_value=mock_target_host):
        result = vm_lifecycle.migrate_vm(mock_si, "vm1", "esxi-home")

    assert "no access" in result.lower() or "to-datastore" in result.lower(), \
        f"expected teaching error about missing shared storage, got: {result}"


# ── Bug C: MCP server must expose clone/migrate/delete/snapshot tools ──


def test_mcp_server_exposes_clone_migrate_delete_snapshot() -> None:
    """v1.5.x: MCP server was missing 6 write tools that CLI had — agents
    using the MCP transport could not clone, migrate, delete, or snapshot VMs."""
    import asyncio

    from vmware_aiops.mcp_server.server import mcp

    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}

    required = {
        "vm_clone",
        "vm_migrate",
        "vm_delete",
        "vm_create_snapshot",
        "vm_revert_snapshot",
        "vm_delete_snapshot",
    }
    missing = required - names
    assert not missing, f"MCP server missing required write tools: {missing}"


# ── Bug D: cli/mcp_config.py NameError on json ──


def test_mcp_config_install_imports_json() -> None:
    """v1.5.x: cli/mcp_config.py used json.loads but never imported json."""
    import vmware_aiops.cli.mcp_config as m

    assert hasattr(m, "json"), "mcp_config.py must import json at module level"


# ── Bug E: _wait_for_task preserves fault chain ──


def test_wait_for_task_preserves_fault_cause() -> None:
    """Without faultCause / faultMessage, users get a useless 'Task failed'
    message and can't diagnose host/datastore/permission issues."""
    from vmware_aiops.ops.vm_lifecycle import TaskFailedError, _wait_for_task

    task = MagicMock()
    task.info.state = "error"

    err = MagicMock()
    err.msg = "A specified parameter was not correct"
    cause = MagicMock()
    cause.msg = "spec.location.host"
    err.faultCause = cause
    err.faultMessage = []
    task.info.error = err

    # Force loop exit immediately
    with patch("vmware_aiops.ops.vm_lifecycle.vim") as mock_vim:
        mock_vim.TaskInfo.State.running = "running"
        mock_vim.TaskInfo.State.queued = "queued"
        mock_vim.TaskInfo.State.success = "success"
        with pytest.raises(TaskFailedError) as exc:
            _wait_for_task(task)

    msg = str(exc.value)
    assert "caused_by" in msg or "spec.location.host" in msg, \
        f"fault cause was dropped: {msg}"


# ── Bug F: Python 3.10 PEP 604 in MCP tool signatures ──


def test_mcp_cmd_guards_python_version() -> None:
    """server.py used `str | None` which crashes FastMCP/Pydantic on Python 3.10
    with 'issubclass() arg 1 must be a class'. Belt: mcp_cmd checks version."""
    import vmware_aiops.cli._root as root

    src = inspect.getsource(root.mcp_cmd)
    assert "version_info" in src and "3, 10" in src, \
        "mcp_cmd must guard against Python < 3.10 (v1.5.27 loosened from 3.11)"


def test_mcp_server_uses_optional_not_pep604() -> None:
    """Suspenders: FastMCP-reflected tool signatures should use Optional[X], not
    X | None, so install on Python 3.10 doesn't crash at decorator time.

    Tool signatures now live in vmware_aiops/mcp_server/tools/*.py plus the shared helpers in
    _shared.py; scan all of them, not just the (now thin) server.py entrypoint.
    """
    from pathlib import Path

    mcp_dir = Path(__file__).resolve().parents[3] / "vmware_aiops" / "mcp_server"
    files = [mcp_dir / "server.py", mcp_dir / "_shared.py"]
    files += sorted((mcp_dir / "tools").glob("*.py"))

    bad = []
    for path in files:
        src = path.read_text(encoding="utf-8")
        # exclude comments / docstrings — look only at type annotations
        for i, line in enumerate(src.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"'):
                continue
            if " | None" in line and ":" in line and ("def " in line or stripped.endswith(",")):
                bad.append(f"{path.name}:{i}: {line.strip()}")
    assert not bad, (
        "vmware_aiops.mcp_server tool signatures must not use PEP 604 "
        "`X | None`:\n" + "\n".join(bad)
    )
