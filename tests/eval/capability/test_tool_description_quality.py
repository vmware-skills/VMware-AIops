"""Capability — can a model pick the right tool from the description alone?

What this measures
------------------
CLAUDE.md requires every tool description to answer three questions ("written
for a new colleague"): **When** to use this tool rather than a sibling, **What**
goes in and comes back, and the **Gotchas**. This eval scores every registered
tool against that mandate on six binary dimensions, and reports one aggregate
percentage.

Why it matters for a small model
--------------------------------
A large model compensates for a thin description: it has seen vSphere, it infers
that ``get_alarms`` probably needs no arguments and that ``cluster_health_summary``
supersedes hand-stitching three list calls. A small model cannot infer any of
that. For it the description *is* the API — an unstated routing rule is a
routing rule that does not exist, and an undocumented parameter is a parameter it
will hallucinate a value for. Description quality is therefore not documentation
polish; it is the substrate the weak-model experience is built on, and it is the
one input we control without touching a single line of vSphere code.

The six dimensions, and why each one earns a point
--------------------------------------------------
``marker``   — the ``[READ]``/``[WRITE]`` prefix CLAUDE.md mandates. A model
               deciding whether an action is safe to auto-run reads this first.
``what``     — states the return shape. Without it a model does not know whether
               to expect an envelope, a scalar, or a bundle, and mis-parses.
``when``     — states when to prefer this tool, or names the tool to prefer
               instead. This is the routing signal; see above.
``gotcha``   — states a caveat, bound, or failure mode. **The noisiest of the
               six, and the one to trust least.** It is keyword matching, and
               ordinary phrasing collects the point by accident: a docstring
               ending "Read-only." matches ``only``; an enum listing ``WARNING``
               matches ``warning``; a tool whose job is to confirm something
               matched ``confirm`` in its own first sentence until markers
               appearing in the tool's own name were excluded. Three independent
               reviewers flagged this within one pass. Read a high ``gotcha`` as
               "nothing contradicts the claim", not as evidence the caveat is
               there — and prefer sharpening this dimension over celebrating it.
``args``     — every schema property carries its own ``description``, which is
               where an MCP client actually shows it. A description that only
               restates the property name (``storage_class``: "Storage class.")
               does not count — it leaves the model exactly where it started,
               and neither does a passing mention in the prose. Scored as a
               full point only when *all* are documented; partial credit is
               deliberately withheld because one undocumented parameter is
               enough to produce a bad call.
``next_hop`` — names another concrete tool to call after this one. The single
               most load-bearing dimension for a weak model, which otherwise
               stops after one call and summarises whatever it got.

How to read the score
---------------------
This is a 0–100 quality index over the whole surface, not a pass rate. Interpret
it in bands: **<50** the surface is under-described and small-model routing will
be guesswork; **50–70** workable, with identifiable weak tools; **70–85** good;
**>85** approaching the ceiling this rubric can detect, at which point the rubric
should be sharpened rather than celebrated. Track the direction across releases;
the absolute number only means something relative to the same rubric.

The assertions are collapse floors set far below the current score — see
``_scoring.py`` for why they must stay boring.
"""

from __future__ import annotations

import pytest

from ._scoring import (
    GOTCHA_MARKERS,
    WHAT_MARKERS,
    WHEN_MARKERS,
    Score,
    documented_args,
    has_any,
    undocumented_args,
    value_set_candidates,
)

pytestmark = pytest.mark.capability

#: Dimensions, in the order they are reported.
DIMENSIONS = ("marker", "what", "when", "gotcha", "args", "next_hop")

#: A description "names a next hop" if it mentions a tool that is not itself.
#: Compared against the live registry so a renamed tool stops counting rather
#: than silently scoring a point for a dangling reference.


def _grade(tool, all_names: frozenset[str]) -> dict[str, bool]:
    desc = tool.description or ""
    stripped = desc.lstrip()
    documented, total = documented_args(tool.inputSchema or {})
    others = all_names - {tool.name}
    # A gotcha marker that is also a word in the tool's own name is describing
    # what the tool does, not warning about it: `confirm_draft` earned the point
    # for the word "confirm" in its own first sentence. Dropping the tool's own
    # name tokens from the marker set is the narrowest fix that removes the free
    # point without weakening the dimension for tools that really do warn.
    own = {w for w in tool.name.lower().split("_") if w}
    gotcha_markers = tuple(m for m in GOTCHA_MARKERS if m.strip() not in own)
    return {
        "marker": stripped.startswith("[READ]") or stripped.startswith("[WRITE]"),
        "what": has_any(desc, WHAT_MARKERS),
        "when": has_any(desc, WHEN_MARKERS),
        "gotcha": has_any(desc, gotcha_markers),
        "args": total == 0 or documented == total,
        "next_hop": any(name in desc for name in others),
    }


@pytest.fixture(scope="session")
def graded(tools) -> dict[str, dict[str, bool]]:
    names = frozenset(t.name for t in tools)
    return {t.name: _grade(t, names) for t in tools}


def test_description_quality_index(board, tools, graded):
    """Record the aggregate 0-100 index over every tool × every dimension."""
    earned = sum(sum(g.values()) for g in graded.values())
    possible = len(graded) * len(DIMENSIONS)

    per_dimension = {
        dim: round(100.0 * sum(g[dim] for g in graded.values()) / len(graded), 1)
        for dim in DIMENSIONS
    }
    weakest = sorted(graded, key=lambda n: (sum(graded[n].values()), n))[:8]

    score = board.add(
        Score(
            name="tool_description_quality",
            value=earned,
            maximum=possible,
            detail={
                "tools_graded": len(graded),
                "per_dimension_pct": per_dimension,
                "weakest_tools": {n: sorted(d for d, ok in graded[n].items() if not ok) for n in weakest},
            },
        )
    )
    print(f"\n[capability] tool_description_quality = {score.pct}%  ({earned}/{possible})")
    print(f"             per-dimension: {per_dimension}")

    assert score.pct >= 40.0, (
        f"description quality collapsed to {score.pct}% — the tool surface is no "
        "longer self-describing enough for a model to route without guessing"
    )


def test_read_write_marker_is_universal(board, graded):
    """The one dimension that is genuinely binary, tracked separately.

    CLAUDE.md makes the ``[READ]``/``[WRITE]`` prefix mandatory, and unlike the
    other five this one has no judgement in it — so it is the only dimension
    whose floor is set at the mandate itself. It is recorded as a score anyway
    so the trend file tells a complete story.
    """
    marked = sum(g["marker"] for g in graded.values())
    score = board.add(
        Score(
            name="read_write_marker_coverage",
            value=marked,
            maximum=len(graded),
            detail={"unmarked": sorted(n for n, g in graded.items() if not g["marker"])},
        )
    )
    print(f"\n[capability] read_write_marker_coverage = {score.pct}%")
    assert score.pct == 100.0, f"tools missing the mandated marker: {score.detail['unmarked']}"


def test_parameter_documentation_coverage(board, tools):
    """Fraction of all schema parameters that the description actually explains.

    Reported per-parameter rather than per-tool so that one badly documented
    wide tool cannot hide behind many well documented narrow ones. An
    undocumented parameter is where a small model invents a value.

    Reads the schema, where an MCP client shows a parameter's description.
    Before that it read the prose alone, and scored eleven of twelve skills
    under this floor at a moment when every parameter on every one of those
    surfaces carried a real description — see :func:`undocumented_args`. The
    detail also carries ``value_set_candidates``, a triage list that is
    deliberately no part of the score.
    """
    documented = total = 0
    gaps: dict[str, list[str]] = {}
    for t in tools:
        missing = undocumented_args(t.inputSchema or {})
        count = len((t.inputSchema or {}).get("properties", {}))
        total += count
        documented += count - len(missing)
        if missing:
            gaps[t.name] = sorted(missing)

    score = board.add(
        Score(
            name="parameter_documentation_coverage",
            value=documented,
            maximum=total,
            unit="parameters",
            detail={
                "undocumented_by_tool": gaps,
                # Reported, never asserted — see value_set_candidates.
                "value_set_candidates": value_set_candidates(tools),
            },
        )
    )
    print(f"\n[capability] parameter_documentation_coverage = {score.pct}%  ({documented}/{total})")
    assert score.pct >= 60.0, f"parameters left to guesswork: {gaps}"
