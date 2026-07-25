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
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def previous_scores(path: Path = SCORES_PATH) -> dict[str, Any]:
    """Load the last recorded run, or ``{}`` on a first run / unreadable file."""
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("scores", {})
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


def documented_args(description: str, schema: dict[str, Any]) -> tuple[int, int]:
    """Return ``(documented, total)`` schema properties named in ``description``.

    An undocumented parameter is one a model must guess the meaning of from its
    name alone. That is survivable for ``target`` and not survivable for
    ``top_n`` or ``folder_filter``.
    """
    props = tuple((schema or {}).get("properties", {}))
    if not props:
        return (0, 0)
    low = description.lower()
    return (sum(1 for p in props if p.lower() in low), len(props))
