"""Shared pieces of the MCP confirmation gate (HLD §7, revised 2026-09-16).

Every in-scope tool takes ``confirm: bool = False``. A bare call previews: it
returns ``{"action": "preview", "blast_radius": {...}, "hint": ...}`` and
changes nothing. ``confirm=True`` acts, unless a blocker or an unmeasurable
field makes the tool refuse — then it raises ``GateRefusedError`` with a
teaching message, which ``tool_errors`` turns into the ``{"error": ...}``
envelope and the audit row records as a failure.

``acknowledge_blast_radius`` (the echo of stable keys) is required only where a
tool says so; ``vm_delete`` is the reference.
"""

from __future__ import annotations

from typing import Any


class GateRefusedError(Exception):
    """A confirmed call refused: blocker, unmeasured field, or stale acknowledgement."""


def preview(blast_radius: dict[str, Any], hint: str | None = None) -> dict[str, Any]:
    """The response of a bare (``confirm=False``) call."""
    return {
        "action": "preview",
        "blast_radius": blast_radius,
        "hint": hint or "Nothing was changed. Show blast_radius to the user; "
                        "re-run with confirm=True only after they agree.",
    }


#: Longest refusal message. ``_safe_error`` passes authored text through at up to
#: 500 characters; a message longer than that loses its end, which is where the
#: remedy is. 480 leaves room for the envelope's own prefix.
MAX_REFUSAL_CHARS = 480

_ELLIPSIS = " … "


def fit(text: str, budget: int) -> str:
    """``text`` in at most ``budget`` characters, keeping its start and its end.

    A blocker names the problem first and the remedy last, so the middle — a
    long list of snapshot names, say — is what gives way.
    """
    if len(text) <= budget:
        return text
    keep = max(budget - len(_ELLIPSIS), 0)
    head = keep // 2
    return text[:head] + _ELLIPSIS + text[len(text) - (keep - head):]


def _lead(prefix: str, first: str, more: int, noun: str) -> str:
    suffix = (
        f" …and {more} more {noun}{'s' if more > 1 else ''}; see blast_radius in the preview."
        if more else ""
    )
    return prefix + fit(first, MAX_REFUSAL_CHARS - len(prefix) - len(suffix)) + suffix


def refusal_message(blast_radius: dict[str, Any], tool: str) -> str | None:
    """The teaching message for a refused call, or None when nothing stands in the way.

    It leads with the first blocker — what is wrong and what to do — and counts
    the rest, so the remedy survives the length cap however many blockers there
    are; the preview lists them all.
    """
    prefix = f"{tool} refused: "
    blockers = blast_radius.get("blockers") or []
    if blockers:
        return _lead(prefix, blockers[0], len(blockers) - 1, "blocker")
    unmeasured = blast_radius.get("unmeasured") or []
    if unmeasured:
        tail = (
            ", so what this would change is unknown. Check the object in vCenter "
            "(permissions, inaccessible state) and retry."
        )
        fields = fit(", ".join(unmeasured), MAX_REFUSAL_CHARS - len(prefix) - 20 - len(tail))
        return f"{prefix}could not read {fields}{tail}"
    return None


def refuse_on(blast_radius: dict[str, Any], tool: str) -> None:
    """Raise if the measurement found a blocker or could not read something."""
    message = refusal_message(blast_radius, tool)
    if message:
        raise GateRefusedError(message)
