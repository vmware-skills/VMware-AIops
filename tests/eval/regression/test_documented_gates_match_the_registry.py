"""The documentation may not promise a gate the MCP surface does not have.

From the 2026-08-30 real-hardware round. The finding was reported as "36 of 43
write tools have no confirmation and no dry-run", which is true, and as "the
tiered-approval machinery v1.8.7 had was removed", which is not: ``vm_delete``
has never taken a confirmation parameter in any revision of this repo, and the
only structural MCP-side restriction that ever existed — the v1.8.0 read-only
switch — was removed on purpose in v1.8.7 with the reasoning recorded in the
family security HLD (§5, §7, §9, decision **D-2**, 2026-07-21).

So the code is doing what it was designed to do and the documents are the
defect. `README.md` stated, in a Safety Features table with no surface named,
that "All destructive ops ... require 2 sequential confirmations — no bypass
flags"; `capabilities.md` told an agent that every L3 tool passes through
"connection check → policy check → audit log → optional double-confirm". A
reader — operator or agent — came away believing a guardrail existed on the
path they were about to use. That is worse than the absence itself, because a
believed guardrail is one nobody compensates for.

What this file pins is the *agreement*, in both directions:

1. The gate inventory in ``capabilities.md`` is derived from the live registry
   and compared to it. A 44th write tool makes the numbers wrong and this test
   red until the sentence is corrected — the mechanical link between a document
   and the code that 形态 #6 says is the only thing that stops prose drifting.
2. No user-facing document states a confirmation requirement without naming the
   surface it applies to. The CLI really does double-confirm; the claim is only
   false when it is left unscoped.

Controls are as load-bearing as the assertions here. The wrong way to make this
file green is to sprinkle ``confirm=`` over the write surface, so the read tools
are checked to have acquired nothing, and the seven writes that genuinely do
preview are driven end to end through both legs.
"""

from __future__ import annotations

import asyncio
import pathlib
import re

_REPO = pathlib.Path(__file__).resolve().parents[3]
CAPABILITIES = _REPO / "skills" / "vmware-aiops" / "references" / "capabilities.md"

#: User-facing documents — the ones a reader lands on to learn what protects
#: them. ``RELEASE_NOTES.md`` and ``docs/`` are excluded on purpose: they are a
#: historical record and a design archive, and rewriting what a past release
#: said it did would be falsifying the record rather than fixing a claim.
DOC_GLOBS = ("README.md", "README-CN.md", "SECURITY.md", "skills/**/*.md")

#: A sentence asserting that confirmation is *required*. Deliberately narrow: it
#: matches the claim, not every occurrence of the word "confirm" (a tool
#: parameter named ``confirm`` and a table cell reading "Double" are not claims).
_CONFIRMATION_CLAIM = re.compile(
    r"double[ -]?confirm"          # "double confirmation", "double-confirm"
    r"|sequential confirmations"   # "2 sequential confirmations"
    r"|双重确认"                     # the same claim in the Chinese README
    r"|连续两次确认",
    re.IGNORECASE,
)

#: The claim is true of the CLI and false of the MCP path, so naming the surface
#: is what makes it honest. A line mentioning either surface has been scoped —
#: and so has a line that *is* a CLI invocation, which most of the annotated
#: cheat-sheet entries are ("vmware-aiops cluster delete ...  # double confirm").
#: Accepting those is not a loophole: an unscoped claim is only misleading when
#: nothing on the line says which surface it is about, and a command line does.
_NAMES_A_SURFACE = re.compile(r"\bCLI\b|\bMCP\b|(?:^|\s)vmware-aiops\s", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Derived truth — read from the registry, never from a list kept in this file
# ---------------------------------------------------------------------------


def _tools():
    from vmware_aiops.mcp_server.server import mcp

    tools = asyncio.run(mcp.list_tools())
    assert tools, "list_tools() returned nothing — every assertion below is vacuous"
    return tools


def _writes() -> list:
    return [t for t in _tools() if t.annotations.readOnlyHint is False]


def _has_confirm(tool) -> bool:
    return "confirm" in (tool.inputSchema or {}).get("properties", {})


def _derived_inventory() -> tuple[int, frozenset[str], int]:
    """(write tool count, names of the confirm-gated ones, ungated count)."""
    writes = _writes()
    gated = frozenset(t.name for t in writes if _has_confirm(t))
    return len(writes), gated, len(writes) - len(gated)


# ---------------------------------------------------------------------------
# The documented inventory
# ---------------------------------------------------------------------------


def _inventory_block() -> str:
    text = CAPABILITIES.read_text(encoding="utf-8")
    start = text.find("<!-- gate-inventory")
    end = text.find("<!-- /gate-inventory -->")
    assert start != -1 and end > start, (
        f"{CAPABILITIES} has no <!-- gate-inventory --> ... <!-- /gate-inventory --> "
        f"block. The counts below have nothing to be checked against, so a wrong "
        f"count in the prose would read as agreement."
    )
    return text[start:end]


def _documented_number(label: str, block: str) -> int:
    m = re.search(rf"{label}:\s*\**\s*(\d+)", block)
    assert m, f"the gate-inventory block states no '{label}: <n>' line"
    return int(m.group(1))


def test_the_documented_gate_inventory_matches_the_live_registry() -> None:
    """Counts and names, both directions, against ``mcp.list_tools()``.

    This is the whole point of the file: a 44th write tool changes the derived
    numbers, and the sentence a reader trusts has to be corrected before the
    suite is green again.
    """
    block = _inventory_block()
    writes, gated, ungated = _derived_inventory()

    assert _documented_number("Write tools", block) == writes
    assert _documented_number("Confirm-gated", block) == len(gated)
    assert _documented_number("Ungated", block) == ungated

    documented_names = set(re.findall(r"`([a-z0-9_]+)`", block))
    assert documented_names >= gated, (
        f"the gate-inventory block does not name these confirm-gated tools: "
        f"{sorted(gated - documented_names)}"
    )
    # And it must not name a tool as gated that is not. Restricted to the line
    # that lists them so the surrounding prose can mention ``vm_delete`` freely.
    listed_line = next(
        line for line in block.splitlines() if re.search(r"Confirm-gated:", line)
    )
    listed = set(re.findall(r"`([a-z0-9_]+)`", listed_line))
    assert listed == gated, (
        f"the gate-inventory names {sorted(listed)} as confirm-gated; the "
        f"registry says {sorted(gated)}"
    )


def _doc_files() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for pattern in DOC_GLOBS:
        files.extend(sorted(_REPO.glob(pattern)))
    assert files, f"no documents matched {DOC_GLOBS} under {_REPO} — vacuous scan"
    return files


def test_no_document_promises_a_confirmation_without_naming_the_surface() -> None:
    """The claim is true of the CLI and false of the MCP path.

    An unscoped "all destructive operations require two confirmations" is read
    by whoever is holding it as applying to the surface they are on, and for
    two thirds of the write surface that is wrong.
    """
    matched = 0
    unscoped: list[str] = []
    for path in _doc_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not _CONFIRMATION_CLAIM.search(line):
                continue
            matched += 1
            if not _NAMES_A_SURFACE.search(line):
                unscoped.append(f"{path.relative_to(_REPO)}:{lineno}: {line.strip()}")

    # Positive control. These documents *should* describe the CLI's
    # double-confirmation — it is real and it is the family's headline safety
    # feature. If the claim has vanished from every file, this check has stopped
    # checking anything and would pass forever (形态 #1).
    assert matched >= 5, (
        f"only {matched} confirmation claims found across {len(_doc_files())} "
        f"documents — the pattern has stopped matching the prose it guards"
    )
    assert not unscoped, (
        "these lines state that confirmation is required without naming the "
        "surface, so they read as covering the MCP tools too:\n  "
        + "\n  ".join(unscoped)
    )


# ---------------------------------------------------------------------------
# Controls
# ---------------------------------------------------------------------------


def test_no_read_tool_acquired_a_confirmation_parameter() -> None:
    """The wrong fix for this finding is to gate everything.

    A read tool that suddenly demands ``confirm=True`` costs the agent a round
    trip to learn nothing, and would make the count assertions above pass by
    moving tools between the wrong two sets.
    """
    offenders = [t.name for t in _tools() if t.annotations.readOnlyHint and _has_confirm(t)]
    assert not offenders, f"read-only tools asking for confirmation: {offenders}"


def test_a_confirm_gated_write_previews_and_then_writes(monkeypatch) -> None:
    """End to end through both legs of the one gate that does exist.

    ``confirm`` on these seven is a preview switch, not an approval gate, and
    the documentation says so — which is only worth saying if it is true. The
    default leg must reach vSphere for validation but issue no task; the
    confirmed leg must issue it.
    """
    import types

    from pyVmomi import vim

    from vmware_aiops.mcp_server import _shared
    from vmware_aiops.ops import network_mgmt

    created: list = []

    class FakeDVS:
        name = "DSwitch"
        portgroup: list = []

        def CreateDVPortgroup_Task(self, spec):  # noqa: N802 - pyVmomi API name
            created.append(spec)
            return "fake-task"

    monkeypatch.setattr(network_mgmt, "_get_objects", lambda si, types_: [FakeDVS()])
    monkeypatch.setattr(network_mgmt, "_wait_for_task", lambda t: None)
    monkeypatch.setattr(_shared, "_get_connection", lambda target=None: object())
    assert vim  # the fake stands in for a real switch; import pins the dependency
    assert types

    from vmware_aiops.mcp_server.tools.network import create_dvs_portgroup

    preview = create_dvs_portgroup(name="pg-new", dvs_name="DSwitch", vlan_id=100)
    assert not created, f"the default (preview) leg wrote to vSphere: {created}"
    assert preview.get("preview") or "confirm" in str(preview), (
        f"the preview leg must say how to proceed, got {preview}"
    )

    applied = create_dvs_portgroup(
        name="pg-new", dvs_name="DSwitch", vlan_id=100, confirm=True
    )
    assert created, f"confirm=True did not create the portgroup, got {applied}"
