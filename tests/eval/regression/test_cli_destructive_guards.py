"""Irreversible and guest-execution CLI commands must double-confirm.

``vm guest-exec`` runs an arbitrary command inside a guest OS and was once the
only such command in this CLI without a confirmation. Nothing enforced the rule,
so the one command where it mattered most was the one that drifted.

**What "must confirm" means here — and why it is not simply "[WRITE]".** The CLI
confirms *irreversible* and *guest-execution* operations, not every state change:
``vm create`` / ``vm power-on`` / ``snapshot create`` are ``[WRITE]`` but additive
and intentionally do NOT prompt. There is no single reliable marker for the
"needs a prompt" set — it is the author's per-command judgement:

* ``destructiveHint`` marks data-destroying ops (delete / revert / clean-slate /
  power-off) but is ``False`` for clone and migrate;
* ``--dry-run`` is added to some *additive* previews too (create, power-on);
* ``[WRITE]`` is far broader than "dangerous".

So this file does NOT claim to guard every destructive command (an earlier
version derived from ``[WRITE]`` and, via a tool-name↔ops-name mismatch, silently
guarded only two — 踩坑 #43). It guards the subset that is **unambiguously**
must-confirm: every tool the MCP layer marks ``destructiveHint=True``. That is
derived structurally (marker → the ops function the CLI actually calls), and the
intersection with real CLI commands is asserted to be broad, not just its two
source sets non-empty.

Until 2026-08-30 the marker was joined by a hard-coded set of guest-execution
tool names, because those tools were annotated ``destructiveHint: false`` while
running arbitrary commands as root. Correcting the annotations dissolved the
list — which is the better outcome, since the list had already drifted past
``vm_guest_exec_output``.
"""

from __future__ import annotations

import ast
import asyncio
import pathlib

_REPO = pathlib.Path(__file__).resolve().parents[3]
CLI_DIR = _REPO / "vmware_aiops" / "cli"
TOOLS_DIR = _REPO / "vmware_aiops" / "mcp_server" / "tools"
assert CLI_DIR.is_dir(), f"CLI package not found at {CLI_DIR} — the scan would find nothing"
assert TOOLS_DIR.is_dir(), f"MCP tools not found at {TOOLS_DIR} — the derivation would be empty"

_CONFIRM = "_double_confirm"


def _must_confirm_tool_names() -> frozenset[str]:
    """MCP tool names that a CLI command MUST double-confirm before running.

    One source now: ``destructiveHint=True``. This used to be that marker
    *plus* a hard-coded ``_GUEST_EXEC_TOOLS`` set, which existed only because
    the guest tools were annotated ``destructiveHint: false`` while running
    arbitrary commands as root — the list was a patch over a wrong label, and it
    had already gone stale by omitting ``vm_guest_exec_output``. The labels were
    corrected (see ``test_tool_annotations_match_behaviour``), so the patch is
    removed rather than extended: a fifth guest tool now arrives in this set by
    being annotated honestly, not by someone remembering to edit a frozenset.
    """
    from vmware_aiops.mcp_server.server import mcp

    names = set()
    for tool in asyncio.run(mcp.list_tools()):
        ann = getattr(tool, "annotations", None)
        if ann is not None and getattr(ann, "destructiveHint", None):
            names.add(tool.name)
    return frozenset(names)


def _ops_imported_in(tree: ast.Module) -> set[str]:
    return {
        alias.asname or alias.name
        for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom) and "ops" in (n.module or "").split(".")
        for alias in n.names
    }


def _calls_in(node: ast.AST) -> set[str]:
    return {
        c.func.id
        for c in ast.walk(node)
        if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
    }


def _must_confirm_ops() -> frozenset[str]:
    """Ops-function names called by the body of every must-confirm tool.

    Structural: for each tool FUNCTION (name == MCP tool name) that is
    must-confirm, keep the names it calls that were imported from an ``ops``
    module — the state-changing ops functions, named the way the CLI calls them.
    """
    targets = _must_confirm_tool_names()
    ops: set[str] = set()
    for path in sorted(TOOLS_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        file_ops = _ops_imported_in(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in targets:
                ops |= _calls_in(node) & file_ops
    return frozenset(ops)


def _cli_commands() -> list[tuple[str, str, set[str]]]:
    out = []
    for path in sorted(CLI_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if not any(
                isinstance(d, ast.Call)
                and isinstance(d.func, ast.Attribute)
                and d.func.attr == "command"
                for d in node.decorator_list
            ):
                continue
            calls = _calls_in(node) | {
                alias.name.rsplit(".", 1)[-1]
                for n in ast.walk(node)
                if isinstance(n, ast.ImportFrom)
                for alias in n.names
            }
            out.append((path.name, node.name, calls))
    return out


def _matched() -> tuple[list[str], list[str]]:
    """(commands driving a must-confirm op, of those the ones lacking confirm)."""
    ops = _must_confirm_ops()
    driving, unguarded = [], []
    for file, fn, calls in _cli_commands():
        if calls & ops:
            driving.append(f"{file}:{fn}")
            if _CONFIRM not in calls:
                unguarded.append(f"{file}:{fn}")
    return driving, unguarded


def test_irreversible_and_guest_exec_cli_commands_double_confirm():
    assert _must_confirm_ops(), "no must-confirm ops derived — vacuous"
    assert _cli_commands(), "no Typer commands found — vacuous"

    driving, unguarded = _matched()

    # Anti-vacuity on the INTERSECTION with real commands, not just the source
    # sets. AIops marks 16 tools destructiveHint=True, 9 of which have a CLI
    # command; if the marker→ops→command derivation silently stops matching,
    # this collapses and the check would pass while guarding nothing — the
    # failure this file exists to prevent.
    assert len(driving) >= 8, (
        f"only {len(driving)} CLI commands matched a must-confirm op ({driving}) — "
        f"the derivation is likely stale. A check that matches almost nothing is "
        f"worse than none."
    )
    assert not unguarded, (
        f"these irreversible/guest-exec CLI commands never call {_CONFIRM}: {unguarded}"
    )


def test_specific_high_blast_radius_commands_are_covered():
    """Pin named commands so a broad-but-wrong derivation cannot pass the floor."""
    driving, _ = _matched()
    names = {d.split(":", 1)[1] for d in driving}
    for must in ("vm_delete", "vm_guest_exec_cmd", "vm_snapshot_revert"):
        assert must in names, (
            f"{must} is no longer derived as must-confirm — the destructiveHint /"
            f" guest-exec → ops-function derivation stopped resolving it"
        )


def test_double_confirm_prompts_twice(monkeypatch):
    """A destructive operation must ask for two confirmations."""
    import typer

    from vmware_aiops.cli import _common

    asked = []
    monkeypatch.setattr(typer, "confirm", lambda msg, **kw: asked.append(msg))
    _common._double_confirm("删除", "web-01")
    assert len(asked) == 2, "both confirmations must still be asked"
