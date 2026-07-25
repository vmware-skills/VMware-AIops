"""VM TTL (Time-To-Live) management.

VMs can be assigned an expiry time. When the TTL expires, the VM is
automatically deleted by the scheduler daemon.

Storage: ~/.vmware-aiops/ttl.json  (JSON dict of vm_name → TTL entry)
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from vmware_policy import paginated

logger = logging.getLogger("vmware-aiops.ttl")

_TTL_FILE = Path.home() / ".vmware-aiops" / "ttl.json"


@dataclass
class TTLEntry:
    """A single VM TTL record."""

    vm_name: str
    expires_at: str  # ISO 8601 UTC
    target: str | None = None  # vCenter/ESXi target name (None → default)


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


def _load_ttl_store() -> dict[str, dict]:
    """Load the TTL store from disk. Returns empty dict if not found."""
    if not _TTL_FILE.exists():
        return {}
    try:
        return json.loads(_TTL_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load TTL store: %s", e)
        return {}


def _save_ttl_store(store: dict[str, dict]) -> None:
    """Persist the TTL store to disk (owner-only)."""
    from vmware_aiops._fsutil import secure_chmod_file, secure_mkdir

    secure_mkdir(_TTL_FILE.parent)
    _TTL_FILE.write_text(json.dumps(store, indent=2), encoding="utf-8")
    secure_chmod_file(_TTL_FILE)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def set_ttl(vm_name: str, minutes: int, target: str | None = None) -> str:
    """Register a VM TTL. Returns confirmation message.

    Args:
        vm_name: Name of the VM to expire.
        minutes: Time until deletion, in minutes (min 1).
        target: Optional target name from config; None uses default.
    """
    if minutes < 1:
        return "TTL must be at least 1 minute."

    expires_at = datetime.now(timezone.utc).replace(microsecond=0)
    from datetime import timedelta
    expires_at = expires_at + timedelta(minutes=minutes)

    store = _load_ttl_store()
    entry = TTLEntry(
        vm_name=vm_name,
        expires_at=expires_at.isoformat(),
        target=target,
    )
    store[vm_name] = asdict(entry)
    _save_ttl_store(store)

    logger.info("TTL set for VM '%s': expires at %s (UTC)", vm_name, expires_at.isoformat())
    return (
        f"TTL set for VM '{vm_name}': expires in {minutes} minute(s) "
        f"at {expires_at.strftime('%Y-%m-%dT%H:%M:%SZ')} (UTC). "
        f"The daemon will auto-delete it when the TTL expires."
    )


def preview_ttl(vm_name: str, minutes: int, target: str | None = None) -> str:
    """Compute the TTL target time WITHOUT writing it (for --dry-run).

    Returns a human-readable preview describing which VM would be deleted
    and when. Does not touch the TTL store.
    """
    if minutes < 1:
        return "TTL must be at least 1 minute."

    from datetime import timedelta

    expires_at = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(minutes=minutes)
    return (
        f"Would set TTL for VM '{vm_name}': expires in {minutes} minute(s) "
        f"at {expires_at.strftime('%Y-%m-%dT%H:%M:%SZ')} (UTC). "
        f"The daemon would auto-delete VM '{vm_name}' when the TTL expires."
    )


def cancel_ttl(vm_name: str) -> str:
    """Cancel a VM's TTL. Returns confirmation message."""
    store = _load_ttl_store()
    if vm_name not in store:
        return f"No TTL registered for VM '{vm_name}'."
    del store[vm_name]
    _save_ttl_store(store)
    logger.info("TTL cancelled for VM '%s'", vm_name)
    return f"TTL cancelled for VM '{vm_name}'."


def list_ttl() -> dict:
    """Return all registered TTL entries with status.

    Returns the family list envelope; the whole TTL store is read from disk,
    so ``total`` is the real entry count and nothing is truncated.
    """
    store = _load_ttl_store()
    now = datetime.now(timezone.utc)
    results = []
    for entry in store.values():
        expires = datetime.fromisoformat(entry["expires_at"])
        remaining = expires - now
        remaining_minutes = max(0, int(remaining.total_seconds() / 60))
        results.append({
            "vm_name": entry["vm_name"],
            "expires_at": entry["expires_at"],
            "target": entry.get("target"),
            "remaining_minutes": remaining_minutes,
            "expired": expires <= now,
        })
    rows = sorted(results, key=lambda x: x["expires_at"])
    return paginated(rows, total=len(rows))


def get_expired_entries() -> list[TTLEntry]:
    """Return all TTL entries that have expired. Does NOT remove them."""
    store = _load_ttl_store()
    now = datetime.now(timezone.utc)
    expired = []
    for entry_dict in store.values():
        expires = datetime.fromisoformat(entry_dict["expires_at"])
        if expires <= now:
            expired.append(TTLEntry(**entry_dict))
    return expired


def remove_entry(vm_name: str) -> None:
    """Remove a TTL entry after deletion (called by scheduler)."""
    store = _load_ttl_store()
    if vm_name in store:
        del store[vm_name]
        _save_ttl_store(store)
