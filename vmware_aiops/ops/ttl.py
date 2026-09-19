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
from typing import TYPE_CHECKING

from vmware_policy import paginated

if TYPE_CHECKING:
    from pyVmomi.vim import ServiceInstance

logger = logging.getLogger("vmware-aiops.ttl")

_TTL_FILE = Path.home() / ".vmware-aiops" / "ttl.json"


@dataclass
class TTLEntry:
    """A single VM TTL record."""

    vm_name: str
    expires_at: str  # ISO 8601 UTC
    target: str | None = None  # vCenter/ESXi target name (None → default)
    # The VM the TTL was set on. The daemon deletes only a VM with this
    # instance UUID; None (entries written before it was recorded, or by the
    # CLI) keeps the old name-only behaviour.
    instance_uuid: str | None = None


class TTLIdentityError(Exception):
    """The VM now carrying a TTL entry's name is not the VM the TTL was set on."""


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


def set_ttl(
    vm_name: str,
    minutes: int,
    target: str | None = None,
    instance_uuid: str | None = None,
) -> str:
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
        instance_uuid=instance_uuid,
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
            expired.append(TTLEntry(
                vm_name=entry_dict["vm_name"],
                expires_at=entry_dict["expires_at"],
                target=entry_dict.get("target"),
                instance_uuid=entry_dict.get("instance_uuid"),
            ))
    return expired


def verify_ttl_identity(si: ServiceInstance, entry: TTLEntry) -> None:
    """Raise ``TTLIdentityError`` unless the VM named by ``entry`` is the one it was set on.

    A VM deleted and re-created under the same name, or renamed onto it, would
    otherwise be deleted by a TTL nobody set on it. Raises ``VMNotFoundError`` /
    ``AmbiguousVMError`` from the lookup as ``delete_vm`` would. An entry without
    a recorded UUID is not checked.
    """
    if entry.instance_uuid is None:
        return
    from vmware_aiops.ops.vm_lifecycle import _require_vm

    vm = _require_vm(si, entry.vm_name)
    try:
        actual = vm.config.instanceUuid
    except Exception as exc:  # noqa: BLE001 - unreadable is not a match
        actual = f"unreadable ({type(exc).__name__})"
    if actual != entry.instance_uuid:
        raise TTLIdentityError(
            f"not deleted: the VM named '{entry.vm_name}' now has instance UUID "
            f"{actual}, but the TTL was set on instance UUID {entry.instance_uuid}. It "
            "is a different VM (re-created or renamed); the TTL entry is removed."
        )


def remove_entry(vm_name: str) -> None:
    """Remove a TTL entry after deletion (called by scheduler)."""
    store = _load_ttl_store()
    if vm_name in store:
        del store[vm_name]
        _save_ttl_store(store)


def get_ttl(vm_name: str) -> dict | None:
    """The TTL entry registered for ``vm_name``, or None."""
    entry = _load_ttl_store().get(vm_name)
    return dict(entry) if entry else None


# ---------------------------------------------------------------------------
# MCP gate measurements (HLD §7). The MCP module groups TTL and Clean Slate,
# so both measurements live here; the CLI and the daemon do not call them.
# ---------------------------------------------------------------------------

#: Snapshot names listed in a Clean Slate refusal; the count covers the rest.
MAX_LISTED_SNAPSHOTS = 16


def measure_ttl(si: ServiceInstance, vm_name: str, minutes: int) -> dict:
    """What a TTL schedules: the deletion of this VM, unattended, at ``expires_at``.

    The VM is measured the way ``vm_delete`` measures it. ``vm_delete``'s power
    blockers do not apply: the daemon powers a running VM off before deleting
    it, and a TTL on a running lab VM is the ordinary case. Every other blocker,
    and an unreadable field, refuses.
    """
    from datetime import timedelta

    from vmware_aiops.ops import vm_delete_gate
    from vmware_aiops.ops.vm_lifecycle import _require_vm

    if minutes < 1:
        raise ValueError(f"TTL must be at least 1 minute (got {minutes}).")
    measured = vm_delete_gate.measure_vm_delete(_require_vm(si, vm_name))
    expires_at = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(minutes=minutes)
    existing = get_ttl(vm_name)
    radius = {k: v for k, v in measured.items() if k not in ("blockers", "acknowledge_with")}
    power = set(vm_delete_gate.power_blockers(measured["power_state"]))
    return {
        **radius,
        "minutes": minutes,
        "expires_at": expires_at.isoformat(),
        "replaces_expires_at": existing["expires_at"] if existing else None,
        "deleted_by": "the scheduler daemon, unattended, when the TTL expires, and "
                      "only if the VM still has this instance_uuid; a running VM is "
                      "powered off first. Nothing happens unless the daemon is running.",
        # Only vm_delete's power blockers do not apply: the daemon powers off first.
        "blockers": [b for b in measured["blockers"] if b not in power],
    }


def measure_clean_slate(si: ServiceInstance, vm_name: str, snapshot_name: str) -> dict:
    """What reverting ``vm_name`` to ``snapshot_name`` would discard, and what stands in the way."""
    from pyVmomi import vim, vmodl
    from vmware_policy import sanitize

    from vmware_aiops.ops.vm_lifecycle import _require_vm, matching_snapshots, snapshot_nodes

    read_errors = (vmodl.MethodFault, AttributeError, TypeError)
    vm = _require_vm(si, vm_name)

    def read(fn):
        try:
            return fn()
        except read_errors:
            return None

    instance_uuid = read(lambda: vm.config.instanceUuid)
    power_state = read(lambda: vm.runtime.powerState)
    snaps = read(lambda: snapshot_nodes(vm))
    # Resolved the way clean_slate resolves it, so both mean the same node.
    matches = matching_snapshots(snaps, snapshot_name) if snaps is not None else []

    unmeasured = [
        field for field, value in (
            ("instance_uuid", instance_uuid),
            ("power_state", power_state),
            ("snapshots", snaps),
        ) if value is None
    ]
    blockers = []
    wanted = sanitize(snapshot_name, 200)
    if snaps is not None and not matches:
        names = [sanitize(s.name, 100) for s in snaps][:MAX_LISTED_SNAPSHOTS]
        blockers.append(
            f"No snapshot named '{wanted}' on this VM. Available: "
            f"{', '.join(names) or 'none'}. Pass snapshot_name with one of those, or "
            "create the baseline first with vm_create_snapshot."
        )
    elif len(matches) > 1:
        blockers.append(
            f"{len(matches)} snapshots match '{wanted}' (duplicate names, or names that "
            "differ only by control/invisible characters), and this tool will not "
            "guess which one you meant. Rename or delete the duplicate "
            "(vm_list_snapshots shows them), then preview again."
        )
    chosen = matches[0] if len(matches) == 1 else None
    created = str(read(lambda: chosen.createTime)) if chosen is not None else None
    return {
        "vm": sanitize(read(lambda: vm.name) or "", 200),
        "instance_uuid": instance_uuid,
        "power_state": str(power_state) if power_state is not None else None,
        "powers_off_first": power_state == vim.VirtualMachine.PowerState.poweredOn,
        "snapshot": wanted,
        "snapshot_created": created,
        "snapshot_matches": len(matches),
        "snapshot_count": len(snaps) if snaps is not None else None,
        "discards": (
            f"everything written to the VM since the snapshot was taken ({created})"
            if created else "everything written to the VM since the snapshot was taken"
        ),
        "unmeasured": unmeasured,
        "blockers": blockers,
    }
