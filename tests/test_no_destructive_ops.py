"""Safety boundary tests — destructive CLI commands must have confirm guards.

The guard lives in the CLI command layer (which owns the interactive
confirmation flow), not in ops/ — ops functions are also invoked by the
MCP server, where a blocking prompt would hang the stdio transport;
there, safety comes from @vmware_tool risk_level policy checks and
destructiveHint annotations that tell the agent to confirm with the user.

History: this file originally asserted _double_confirm inside ops/ functions —
the wrong layer — so it failed permanently while telling us nothing.
Rewritten 2026-06-08 (family-wide pass; same fix as VMware-Aria/NSX).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

DESTRUCTIVE_CLI_COMMANDS: list[tuple[str, str]] = [
    ("vmware_aiops/cli/vm.py", "vm_power_off"),
    ("vmware_aiops/cli/vm.py", "vm_delete"),
    ("vmware_aiops/cli/vm.py", "vm_snapshot_revert"),
    ("vmware_aiops/cli/vm.py", "vm_snapshot_delete"),
    ("vmware_aiops/cli/vm.py", "vm_clone"),
    ("vmware_aiops/cli/vm.py", "vm_migrate"),
    ("vmware_aiops/cli/cluster.py", "cluster_delete_cmd"),
    ("vmware_aiops/cli/cluster.py", "cluster_remove_host_cmd"),
    ("vmware_aiops/cli/alarm.py", "alarm_reset"),
]


def _has_confirm_guard(file_path: Path, func_name: str) -> bool:
    tree = ast.parse(file_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            source = ast.dump(node)
            return "_double_confirm" in source
    return False


@pytest.mark.unit
class TestDestructiveCliSafety:
    """Every destructive CLI command must include a confirm guard."""

    @pytest.mark.parametrize("rel_path,func_name", DESTRUCTIVE_CLI_COMMANDS)
    def test_has_confirm_guard(self, rel_path: str, func_name: str) -> None:
        path = REPO_ROOT / rel_path
        assert path.exists(), f"{path} not found"
        assert _has_confirm_guard(path, func_name), (
            f"{func_name} in {rel_path} lacks a _double_confirm safety guard"
        )
