"""Every CLI command that performs a write is wrapped by @guarded (HLD I-1, I-8).

A write CLI command must route through vmware_policy's guard() + audit_call() —
the same enforcement @vmware_tool gives the MCP surface — so ``vmware-aiops vm
delete`` run through Bash is authorized and audited to ~/.vmware/audit.db exactly
like the ``vm_delete`` MCP tool. Without @guarded a CLI write bypassed policy and
landed only in the legacy per-skill log (the gap HLD §2.1 documents).

The write set is DERIVED, never hand-listed (踩坑 #43): a tool annotated
``readOnlyHint=False`` is a write; the ops functions its body calls — reached by
a bare name OR ``module.func`` on an ops-module import — are the state-changing
ops; a CLI ``@command`` calling one is a write command and must carry @guarded.
The attribute-call case is not optional: the deploy tools call
``vm_deploy.deploy_ova`` that way, and a derivation blind to it silently skips all
seven deploy commands — the "label promises more than content" shape.
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


def _write_tool_names() -> frozenset[str]:
    from vmware_aiops.mcp_server.server import mcp

    return frozenset(
        t.name
        for t in asyncio.run(mcp.list_tools())
        if getattr(getattr(t, "annotations", None), "readOnlyHint", None) is False
    )


def _ops_refs(tree: ast.AST) -> tuple[dict[str, str], set[str]]:
    """(local name -> REAL ops function name, ops-module aliases).

    An aliased import (``from ops.mod import realname as _alias``) maps
    ``_alias -> realname`` so an aliased call resolves to the same op an
    un-aliased import names. REST skills alias their ops (``... as _create``)
    while the CLI imports the real name; both must derive to the real name or
    the MCP→ops→CLI intersection is empty (the shape that hid all 13 NSX writes).
    """
    func_map: dict[str, str] = {}
    mods: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module:
            parts = n.module.split(".")
            if "ops" in parts:
                if parts[-1] == "ops":
                    mods.update(a.asname or a.name for a in n.names)
                else:
                    for a in n.names:
                        func_map[a.asname or a.name] = a.name
    return func_map, mods


def _ops_calls(node: ast.AST, func_map: dict[str, str], mods: set[str]) -> set[str]:
    """Real ops function names called in ``node`` — via ``f()`` or ``mod.f()``."""
    out: set[str] = set()
    for c in ast.walk(node):
        if not isinstance(c, ast.Call):
            continue
        f = c.func
        if isinstance(f, ast.Name) and f.id in func_map:
            out.add(func_map[f.id])
        elif (
            isinstance(f, ast.Attribute)
            and isinstance(f.value, ast.Name)
            and f.value.id in mods
        ):
            out.add(f.attr)
    return out


def _write_ops() -> frozenset[str]:
    targets = _write_tool_names()
    ops: set[str] = set()
    for path in sorted(TOOLS_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        func_map, mods = _ops_refs(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in targets:
                ops |= _ops_calls(node, func_map, mods)
    return frozenset(ops)


def _decorator_names(node: ast.FunctionDef) -> set[str]:
    names: set[str] = set()
    for d in node.decorator_list:
        t = d.func if isinstance(d, ast.Call) else d
        if isinstance(t, ast.Name):
            names.add(t.id)
        elif isinstance(t, ast.Attribute):
            names.add(t.attr)
    return names


def _cli_write_commands() -> tuple[list[str], list[str]]:
    """(write commands, of those the ones missing @guarded)."""
    write_ops = _write_ops()
    assert write_ops, "no write ops derived — vacuous"
    writing: list[str] = []
    unguarded: list[str] = []
    for path in sorted(CLI_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        func_map, mods = _ops_refs(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if not any(
                isinstance(d, ast.Call)
                and isinstance(getattr(d, "func", None), ast.Attribute)
                and d.func.attr == "command"
                for d in node.decorator_list
            ):
                continue
            if _ops_calls(node, func_map, mods) & write_ops:
                label = f"{path.name}:{node.name}"
                writing.append(label)
                if "guarded" not in _decorator_names(node):
                    unguarded.append(label)
    return writing, unguarded


def test_every_write_cli_command_is_guarded():
    writing, unguarded = _cli_write_commands()
    # 33 is the real count on 2026-09-11: a drop means a command fell out of the derivation.
    assert len(writing) >= 33, (
        f"only {len(writing)} write CLI commands derived ({writing}) — the "
        f"MCP→ops→CLI derivation is likely stale; a check matching almost nothing "
        f"is worse than none."
    )
    assert not unguarded, (
        f"these CLI commands call a [WRITE] ops function but are not @guarded, so "
        f"they bypass policy + audit (HLD I-1): {unguarded}"
    )


# Guarded CLI writes that deliberately have NO MCP twin, name -> reason. Such a
# command keeps its own name (there is no MCP tool name for a rule to share),
# but it must be listed here on purpose: a command that silently lost its twin
# (an MCP tool renamed or dropped) would otherwise pass unnoticed. Empty today —
# every guarded AIops CLI write mirrors exactly one MCP write tool.
_CLI_ONLY_WRITES: dict[str, str] = {}


def _op_to_mcp_tools() -> tuple[dict[str, set[str]], dict[str, str]]:
    """(write ops function -> MCP write tools whose body calls it,
    MCP write tool -> the ``tools/`` module stem that defines it)."""
    targets = _write_tool_names()
    assert targets, "no write tools (readOnlyHint=False) — derivation vacuous"
    op_tools: dict[str, set[str]] = {}
    tool_module: dict[str, str] = {}
    for path in sorted(TOOLS_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        func_map, mods = _ops_refs(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in targets:
                tool_module[node.name] = path.stem
                for op in _ops_calls(node, func_map, mods):
                    op_tools.setdefault(op, set()).add(node.name)
    return op_tools, tool_module


def _mcp_tool_risk(tool: str, tool_module: dict[str, str]) -> object:
    import importlib

    mod = importlib.import_module(f"vmware_aiops.mcp_server.tools.{tool_module[tool]}")
    return getattr(getattr(mod, tool), "_risk_level", None)


def test_guarded_cli_writes_carry_their_mcp_tool_name():
    """A deny rule names a tool; it must stop the CLI twin of that tool too (HLD I-3).

    ``@guarded`` defaults the tool name to the function's ``__name__``, so ``vm
    snapshot-create`` was guarded as ``vm_snapshot_create`` while its MCP twin is
    ``vm_create_snapshot`` (and ``vm guest-exec`` as ``vm_guest_exec_cmd``, ``deploy
    ova`` as ``deploy_ova_cmd``) — a rule denying the MCP tool refused the agent
    and let the same write through the CLI, and the two surfaces wrote the one
    audit sink under two names. The twin is DERIVED: the MCP write tool that
    calls the same ops function the command calls.
    """
    import importlib

    op_tools, tool_module = _op_to_mcp_tools()
    write_ops = frozenset(op_tools)
    assert write_ops, "no write ops derived — vacuous"
    checked: list[str] = []
    mismatched: list[str] = []
    cli_only_seen: set[str] = set()
    for path in sorted(CLI_DIR.rglob("*.py")):
        if path.stem == "__init__":
            continue
        module = importlib.import_module(f"vmware_aiops.cli.{path.stem}")
        tree = ast.parse(path.read_text(encoding="utf-8"))
        func_map, mods = _ops_refs(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            fn = getattr(module, node.name, None)
            if not getattr(fn, "_is_guarded", False):
                continue  # unguarded writes are test_every_write_cli_command_is_guarded's finding
            ops = _ops_calls(node, func_map, mods) & write_ops
            twins = set().union(*(op_tools[o] for o in ops)) if ops else set()
            if not twins:
                if node.name in _CLI_ONLY_WRITES:
                    cli_only_seen.add(node.name)
                    continue
                mismatched.append(
                    f"{node.name}: guarded but mirrors no MCP write tool — give it "
                    f"one, or list it in _CLI_ONLY_WRITES with the reason"
                )
                continue
            assert len(twins) == 1, (
                f"{node.name} maps to several MCP tools {sorted(twins)} — pick "
                f"the one it mirrors explicitly"
            )
            (twin,) = twins
            checked.append(node.name)
            twin_risk = _mcp_tool_risk(twin, tool_module)
            if fn._guarded_tool != twin:
                mismatched.append(
                    f"{node.name}: guarded as {fn._guarded_tool!r}, MCP tool {twin!r}"
                )
            elif fn._risk_level != twin_risk:
                mismatched.append(
                    f"{node.name}: risk {fn._risk_level!r}, MCP tool {twin!r} risk {twin_risk!r}"
                )
    stale = set(_CLI_ONLY_WRITES) - cli_only_seen
    assert not stale, (
        f"_CLI_ONLY_WRITES lists {sorted(stale)}, which are not guarded twin-less "
        f"CLI writes any more — drop the stale entries"
    )
    # 33 = every guarded CLI write today (alarm 2, cluster 8, deploy 7, vm 16).
    # A floor below the real count lets a command that lost @guarded — or lost
    # its MCP twin in the derivation — drop out of this check unnoticed. Raise
    # it when a guarded write is added; never lower it to make a failure pass.
    assert len(checked) >= 33, (
        f"only {len(checked)} guarded CLI writes checked ({sorted(checked)}) — "
        f"expected at least 33; a command dropped out of the name-alignment check"
    )
    assert not mismatched, (
        "these CLI writes are guarded under a different name or risk than their "
        "MCP tool, so one deny rule does not scope both surfaces — pass the MCP "
        "tool name to @guarded(...): " + "; ".join(mismatched)
    )


def test_high_blast_radius_commands_are_derived_and_guarded():
    """Pin named commands so a broad-but-wrong derivation cannot pass the floor.

    ``deploy_ova_cmd`` in particular only appears when the derivation follows the
    ``vm_deploy.deploy_ova`` attribute call — its presence proves that path works.
    """
    writing, _ = _cli_write_commands()
    names = {w.split(":", 1)[1] for w in writing}
    for must in ("vm_delete", "vm_clean_slate", "cluster_delete_cmd", "deploy_ova_cmd"):
        assert must in names, (
            f"{must} is no longer derived as a write command — the readOnlyHint→"
            f"ops→command derivation stopped resolving it"
        )
