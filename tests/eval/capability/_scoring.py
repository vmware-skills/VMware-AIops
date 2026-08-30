"""Scoring primitives shared by this skill's capability evals.

Why this exists
---------------
A regression eval answers a yes/no question ("does bug #31 still bite?") and
must sit at 100%. A capability eval answers a *how well* question ("can a small
model actually drive this tool surface?") and is expected to sit below 100%
forever — the number is the product, not the pass/fail.

So every capability eval here does two things:

1. **records a score** into ``_scores.json`` next to this file, so the next
   release can diff against it rather than re-deriving a feeling; and
2. **asserts a floor**, deliberately set well under the current score. The floor
   is a ratchet against collapse, not a quality bar. A test going red here means
   something fell off a cliff, not that the surface is imperfect.

Do not raise a floor to match a score. The floor's job is to stay boring.

Token estimation
----------------
``estimate_tokens`` is a BPE approximation (word/punct segmentation × 0.75), not
a real tokenizer — none of the family venvs carry ``tiktoken`` and a capability
eval must not add a dependency to be runnable. It lands within roughly ±15% of
cl100k on this kind of English-plus-JSON text, which is ample: every budget here
is a *trend* measurement compared against the same estimator in the previous
release, and against thresholds chosen with the error bar already in mind.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

SCORES_PATH = Path(__file__).with_name("_scores.json")

_WORDISH = re.compile(r"\w+|[^\w\s]")


def estimate_tokens(text: str) -> int:
    """Approximate BPE token count for ``text``. See module docstring for error bar."""
    if not text:
        return 0
    return int(len(_WORDISH.findall(text)) * 0.75)


@dataclass(frozen=True)
class Score:
    """One recorded capability measurement.

    ``value``/``maximum`` are the raw numbers; ``pct`` is what release-to-release
    comparison actually reads. ``detail`` carries the per-item breakdown so a
    regression in the aggregate can be traced to the item that caused it without
    re-running anything.
    """

    name: str
    value: float
    maximum: float
    unit: str = "points"
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def pct(self) -> float:
        if self.maximum == 0:
            return 0.0
        return round(100.0 * self.value / self.maximum, 1)

    def as_dict(self) -> dict[str, Any]:
        return {
            "value": round(self.value, 2),
            "maximum": round(self.maximum, 2),
            "unit": self.unit,
            "pct": self.pct,
            "detail": self.detail,
        }


@dataclass
class ScoreBoard:
    """Session-scoped collector.

    The dataclass itself is mutable by necessity — pytest hands results in one
    test at a time — but ``add`` never mutates a :class:`Score`, and ``records``
    is replaced rather than appended in place, so no caller can observe a
    half-updated board.
    """

    records: tuple[Score, ...] = ()

    def add(self, score: Score) -> Score:
        self.records = (*self.records, score)
        return score

    def as_dict(self) -> dict[str, Any]:
        return {s.name: s.as_dict() for s in sorted(self.records, key=lambda s: s.name)}

    def write(self, path: Path = SCORES_PATH) -> None:
        """Persist this run's scores, never shrinking the recorded baseline.

        A partial selection — ``pytest tests/eval/capability/test_x.py`` — used
        to rewrite the file with only the metrics that run collected, silently
        deleting the rest. Running one measurement across the family reduced all
        twelve baselines from thirteen metrics to three, and one ``git add -A``
        would have made that permanent: the next release would have had nothing
        to diff against, which is the mixed-provenance corruption this file
        exists to prevent.

        Metrics from earlier runs are carried forward and marked ``stale`` so a
        reader can tell a fresh measurement from an inherited one. Nothing is
        lost, and nothing pretends to be newer than it is.
        """
        if not self.records:
            return
        fresh = self.as_dict()
        merged = dict(fresh)
        for name, value in previous_scores(path).items():
            if name not in merged:
                carried = dict(value) if isinstance(value, dict) else value
                if isinstance(carried, dict):
                    carried["stale"] = True
                merged[name] = carried
        payload = {
            "_comment": (
                "Capability eval scores. Regenerate with: pytest -m capability. "
                "These are tracked trends, not pass/fail gates — see _scoring.py. "
                "Entries marked stale=true were carried over from an earlier run "
                "because this run did not measure them."
            ),
            "scores": merged,
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def previous_scores(path: Path = SCORES_PATH) -> dict[str, Any]:
    """Load the last recorded run, or ``{}`` on a first run / unreadable file."""
    try:
        return json.loads(path.read_text()).get("scores", {})
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------------------
# Text probes reused by several evals
# ---------------------------------------------------------------------------

#: Phrases that signal a description told the agent *when* to reach for this
#: tool rather than a sibling. Routing is the single hardest thing for a small
#: model to infer, because inferring it requires holding the whole tool list.
WHEN_MARKERS = (
    "use this",
    "use it",
    "use for",
    "use when",
    "instead of",
    " instead",
    "prefer ",
    "before ",
    "first",
    "start here",
    "rather than",
    "for detail",
    "drill into",
    "follow up",
    "then ",
    # "Use after a storage array presents new LUNs" is a complete when-clause.
    # Without these the rubric scored it zero, and the only way to earn the
    # point was to reword it to "Use this when ..." — identical meaning, no
    # information added. A rubric that pays for phrasing buys churn.
    "use after",
    "after ",
    "once ",
    "whenever ",
)

#: Phrases that signal the description stated what comes back.
WHAT_MARKERS = ("returns", "return ", "yields", "reports", "->", "→")

#: Phrases that signal a caveat — the class of information a strong model infers
#: from experience and a weak model simply never learns.
GOTCHA_MARKERS = (
    "note",
    "only",
    "requires",
    "does not",
    "do not",
    "cannot",
    "never",
    "always",
    "may ",
    "must ",
    "caution",
    "warning",
    "n/a",
    "point-in-time",
    "no trending",
    "not supported",
    "unavailable",
    "beware",
    "careful",
    "irreversible",
    "cannot be undone",
    "double",
    "confirm",
    "dry-run",
    "dry run",
    "skip",
    "fall back",
    "fallback",
    "if the",
    "when the",
    "unless",
    "except",
)


def has_any(text: str, markers: tuple[str, ...]) -> bool:
    """Does ``text`` contain any marker, ignoring how the source happens to wrap?

    Whitespace is collapsed first. Markers carry trailing spaces (``"before "``,
    ``"then "``), so a docstring that wrapped at exactly that word scored zero
    for content it plainly contained -- ``"...check the rule count before\\n
    deleting"`` missed ``"before "`` on a line break. Two of one skill's
    forty-two apparent gaps were this, which means the rubric was reporting
    formatting as absence and inviting a rewrite that changes nothing.
    """
    low = " ".join(text.lower().split())
    return any(m in low for m in markers)


#: Words that carry no information about a *particular* parameter, so they
#: cannot be what makes its description say more than its name already does.
#: Deliberately short. Every addition here makes the restatement rule stricter
#: for every skill at once, so a word earns its place only if it is filler in
#: every sentence it can appear in -- ``critical`` or ``seconds`` never qualify.
_EMPTY_WORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "be", "by", "default", "for", "id",
        "if", "in", "is", "it", "its", "name", "of", "on", "optional", "or",
        "set", "string", "that", "the", "this", "to", "use", "used", "value",
        "values", "when", "with",
    }
)

_WORDS = re.compile(r"[a-z0-9][a-z0-9'-]*")


def _says_more_than_its_name(param: str, text: str) -> bool:
    """Does ``text`` tell a model anything ``param`` does not already say?

    ``storage_class: "Storage class."`` and ``description: "Optional
    description."`` are schema descriptions that carry zero information -- a
    model reading them learns exactly what it knew from the property name. They
    are the only kind of filled-in description this rubric refuses to credit.

    The bar is one surviving word, not a word count, and that is the whole
    design. ``control_plane_count: "1 or 3."`` is four characters and is the
    single most useful description on that surface; a length rule would fail it
    and pay instead for ``"The number of control plane nodes."``, which says
    less. This rubric must never buy padding (see :func:`has_any`).
    """
    own = set(_WORDS.findall(param.lower()))
    return any(w not in own and w not in _EMPTY_WORDS for w in _WORDS.findall(text.lower()))


def undocumented_args(schema: dict[str, Any]) -> tuple[str, ...]:
    """Schema properties a model would have to guess the meaning of.

    A parameter counts as documented when its **schema property** carries a
    ``description`` that says more than the property name
    (:func:`_says_more_than_its_name`). The schema is what an MCP client puts in
    front of the model, one description attached to the one argument it
    describes, so it is the only place this can honestly be measured.

    Reading the schema is the correction, and it was overdue. This used to check
    the prose alone -- was the property *name* a substring of the tool
    description -- which was a fair proxy while every ``Args:`` block was still
    embedded in that text. ``vmware_policy.describe_tool_parameters`` then moved
    those entries into the JSON schema and stripped the block from the prose;
    both copies otherwise ship in every ``tools/list`` response and the tightest
    manifest budget in the family could not carry them twice. The grader kept
    reading the half that had been emptied on purpose and scored the saving as a
    documentation gap: eleven of twelve skills fell under this test's floor at a
    moment when all 889 of their parameters carried a real description.

    **The prose is not a fallback, and that is deliberate.** Crediting a
    parameter because its name appears somewhere in the description was tried
    here first and had to be removed: stripping every schema description out of
    vmware-debug left this test still reading 100%, because that surface's
    prose mentions each argument in passing anyway. A rule written to catch
    exactly that regression must not contain a clause that hides it (形态 #5).
    Dropping it costs nothing measured -- every skill scores the same with it and
    without it -- and buys back the ability to fail.

    Returned as names rather than a count so the score and the gap report cannot
    disagree -- they were two separate predicates, and a second copy of a rule is
    a copy that drifts (形态 #6).
    """
    props = (schema or {}).get("properties", {})
    if not props:
        return ()
    return tuple(
        name
        for name, prop in props.items()
        if not _says_more_than_its_name(name, (prop or {}).get("description") or "")
    )


def documented_args(schema: dict[str, Any]) -> tuple[int, int]:
    """``(documented, total)`` schema properties, derived from :func:`undocumented_args`."""
    total = len((schema or {}).get("properties", {}))
    return (total - len(undocumented_args(schema)), total)


#: A description offers a closed set when it uses a closure word...
_CLOSED_SET = re.compile(r"\bone of\b|\beither\b|\bor\b", re.I)
#: ...and does not hedge the literals as examples, a format, or an open set.
_OPEN_SET = re.compile(
    r"e\.g\.|for example|such as|\blike\b|format|suffix|substring|pattern|\bany\b|\betc\b",
    re.I,
)
#: Quoted literals, the shape a permitted value is written in.
_LITERAL = re.compile(r"""["'`]([A-Za-z0-9_-]{1,24})["'`]""")


def value_set_candidates(tools) -> dict[str, list[str]]:
    """Parameters that read like a closed set but carry no schema ``enum``.

    **A lead list, not a verdict. Never assert on this.** It is keyword matching
    over prose and its precision is about two in three: on the family surface it
    returns seventeen candidates, of which roughly eleven are genuine
    (``drs_behavior``, ``rule_type``, ``gateway_type``, ``action``, ``severity``,
    ``time_source``) and the rest are not enums at all -- ``since`` and ``last``
    quote duration *formats*, ``objects`` and ``available_skills`` quote
    *examples*. That is the same accuracy as the ``gotcha`` dimension, which this
    suite already tells readers to trust least, and it is precisely why this is
    recorded in a score's ``detail`` for a human to triage rather than wired to a
    floor. A noisy gate would pay people to reword descriptions that are already
    correct.

    Worth recording anyway, because it tracks the half of the 2026-08-30 failure
    that is still open. Moving parameter docs into the schema fixed the wrong
    *name* half; a wrong *value* -- ``power_state="running"`` -- still returns
    zero rows where there were eleven, with nothing raised at any layer, and
    ``enum`` coverage across the family is still 0%. Closing a candidate means
    changing the signature to ``Literal[...]``, which is a code change to a
    published surface and a separate decision from grading.
    """
    found: dict[str, list[str]] = {}
    for tool in tools:
        for name, prop in ((tool.inputSchema or {}).get("properties", {})).items():
            prop = prop or {}
            if prop.get("enum"):
                continue
            text = prop.get("description") or ""
            literals = sorted(set(_LITERAL.findall(text)))
            if len(literals) >= 2 and _CLOSED_SET.search(text) and not _OPEN_SET.search(text):
                found[f"{tool.name}.{name}"] = literals
    return found
