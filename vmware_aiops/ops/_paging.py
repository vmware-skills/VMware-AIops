"""Offset-aware pagination on top of the family envelope.

``vmware_policy.paginated`` derives truncation from ``returned < total``, which
is exactly right for a page that starts at zero and wrong the moment ``offset``
is involved: the last page of five rows fetched with ``offset=4`` returns one
row, and ``1 < 5`` reads as "there is more" — with a hint advising the caller to
raise the limit, which cannot help someone already at the end.

Prose could be vague about this. ``truncated`` cannot: it is a flag an agent
acts on. So the two list tools that support ``offset`` share this helper rather
than each carrying its own correction, which is how one of them ends up fixed
and the other does not.
"""

from __future__ import annotations

from typing import Any

from vmware_policy import paginated


def paginated_window(
    window: list[dict], *, total: int, limit: int, offset: int, **extra: Any
) -> dict[str, Any]:
    """Wrap one already-sliced page in the family envelope.

    Args:
        window: The rows for this page, already sliced by the caller.
        total: The full collection size, before slicing.
        limit: The limit that produced the page; ``0`` or less means unlimited.
        offset: How many rows were skipped to reach it.
        **extra: Extra top-level keys, forwarded to ``paginated`` — which
            refuses any that would shadow the envelope's own six. That refusal
            is the point: a caller with something to say about completeness
            (e.g. hosts it could not read) must say it under its own key rather
            than repurposing ``hint``, whose family meaning is fixed.

    Returns:
        The envelope, with ``truncated`` and ``hint`` computed against the
        caller's position in the collection rather than against the page size.
        ``offset`` is added when non-zero so a page can be placed.
    """
    result = paginated(window, limit=limit if limit > 0 else None, total=total, **extra)
    consumed = offset + result["returned"]
    result["truncated"] = consumed < total
    if not result["truncated"]:
        result["hint"] = None
    elif offset:
        result["hint"] = (
            f"Showing {result['returned']} of {total}, starting at {offset}. "
            f"Pass offset={consumed} for the next page."
        )
    if offset:
        result["offset"] = offset
    return result
