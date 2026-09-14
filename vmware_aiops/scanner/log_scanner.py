"""Log scanner: queries vCenter/ESXi events and classifies issues.

Security: All vSphere-sourced content (event messages, host log lines) is
sanitized before output to prevent prompt injection attacks.  Sanitization
includes truncation, control-character removal, and explicit boundary markers
so that downstream consumers (including LLM agents) can distinguish trusted
output from untrusted vSphere data.
"""

from __future__ import annotations

import http.client
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from xml.parsers.expat import ExpatError

from pyVmomi import vim, vmodl
from vmware_monitor.ops.health import query_events
from vmware_policy import sanitize

from vmware_aiops.config import ScannerConfig
from vmware_aiops.ops.health import CRITICAL_EVENTS, WARNING_EVENTS
from vmware_aiops.ops.inventory import _collect

if TYPE_CHECKING:
    from pyVmomi.vim import ServiceInstance

_log = logging.getLogger("vmware-aiops.log-scanner")

_ERROR_PATTERNS = (
    "error", "fail", "critical", "panic", "lost access",
    "cannot", "timeout", "refused", "corrupt",
)
_CRITICAL_PATTERNS = ("critical", "panic", "corrupt")

# Asking for a line past the end returns no text and the log's last line
# number in ``lineEnd`` — how the scan learns where the end is.
_PAST_THE_END = 999999999

# What a log read may raise that means "this log could not be read": any
# vSphere fault (NoPermission, CannotAccessFile, InvalidRequest, …) and the
# transport failures pyVmomi's SOAP adapter lets through (socket/SSL errors
# are OSError; a non-200/500 reply is HTTPException; a garbled body is
# ExpatError). Deliberately NOT ``Exception``: AttributeError / TypeError /
# NameError are bugs in this code, and catching them is exactly how the scan
# read nothing for years while reporting every log as merely unreadable.
_UNREADABLE = (vmodl.MethodFault, OSError, http.client.HTTPException, ExpatError)


@dataclass(frozen=True)
class HostLogPass:
    """One pass over the host logs, and how far each log was read.

    ``issues`` holds the findings plus an ``info`` row for every log that
    could not be read (source ``host_log:unavailable``) or was not read in full
    (source ``host_log:skipped``). ``positions`` maps ``(host name, log key)``
    to the last line number read, carried forward from the ``since`` the pass
    was given for any log it could not read this time.
    """

    issues: tuple[dict, ...]
    positions: Mapping[tuple[str, str], int]


@dataclass(frozen=True)
class _LogRead:
    text: tuple[str, ...]
    position: int
    skipped: int | None


def scan_logs(
    si: ServiceInstance,
    scanner_config: ScannerConfig,
) -> list[dict]:
    """Scan recent events/logs and return issues above severity threshold.

    Returns a list of issue dicts with keys: severity, source, message, time.
    """
    content = si.RetrieveContent()
    event_mgr = content.eventManager

    now = datetime.now(tz=timezone.utc)
    begin = now - timedelta(hours=scanner_config.lookback_hours)

    filter_spec = vim.event.EventFilterSpec(
        time=vim.event.EventFilterSpec.ByTime(beginTime=begin, endTime=now)
    )

    # Not QueryEvents, which hands back only the oldest 1000 events in the window
    # on vCenter; the shared read goes newest first (see ops/health.py).
    events = query_events(event_mgr, filter_spec)
    threshold = scanner_config.severity_threshold
    severity_rank = {"critical": 0, "warning": 1, "info": 2}
    min_rank = severity_rank.get(threshold, 1)

    issues: list[dict] = []
    for event in events:
        event_type = type(event).__name__

        if event_type in CRITICAL_EVENTS:
            severity = "critical"
        elif event_type in WARNING_EVENTS:
            severity = "warning"
        else:
            continue  # Skip info-level for scanner

        if severity_rank.get(severity, 2) > min_rank:
            continue

        # Sanitize event message: truncate, strip ALL control characters,
        # and wrap in boundary markers to prevent prompt injection from
        # attacker-controlled vSphere event content.
        raw_msg = event.fullFormattedMessage or str(event)
        safe_msg = sanitize(raw_msg, 500)

        issues.append({
            "severity": severity,
            "source": "event",
            "event_type": event_type,
            "message": f"[VSPHERE_EVENT]{safe_msg}[/VSPHERE_EVENT]",
            "time": str(event.createdTime),
            "entity": _safe_entity_name(event),
        })

    return issues


def scan_host_logs(
    si: ServiceInstance,
    host_name: str | None = None,
    log_keys: tuple[str, ...] = ("hostd", "vmkernel", "vpxa"),
    lines: int = 500,
) -> list[dict]:
    """Scan the last ``lines`` lines of each ESXi host log for error patterns.

    Every call reads the tail of each log, remembering nothing between calls;
    the daemon uses :func:`scan_host_logs_since`, which reads only what is new.

    BrowseDiagnosticLog lives on ``content.diagnosticManager``, not on the
    host's ``configManager.diagnosticSystem`` (a vim.host.DiagnosticSystem,
    which has no such method — calling it there raised AttributeError on every
    host and the pass reported nothing). Through vCenter the host is named;
    a standalone ESXi rejects ``host=`` with ``vmodl.fault.InvalidRequest``
    (pyVmomi has no ``vim.fault.InvalidRequest``).

    A log that cannot be read is returned as an ``info`` issue with source
    ``host_log:unavailable`` — never dropped, or a pass that read nothing
    would look like a clean one.
    """
    return list(_scan_pass(si, host_name, log_keys, lines, since=None).issues)


def scan_host_logs_since(
    si: ServiceInstance,
    since: Mapping[tuple[str, str], int],
    log_keys: tuple[str, ...] = ("hostd", "vmkernel", "vpxa"),
    lines: int = 500,
) -> HostLogPass:
    """Read only the host-log lines written since the previous pass (the daemon).

    ``since`` is the previous pass's ``positions``. A log with no entry is
    read from its last ``lines`` lines. A log whose line count went down has
    rotated: its last ``lines`` lines are read, and a ``host_log:skipped`` row
    says whatever was written between the previous read and the rotation was
    not scanned. More than ``lines`` new lines: the newest ``lines`` are read
    and a ``host_log:skipped`` row carries the count. A rotation that has
    already grown past the previous position looks like growth, not rotation
    — the line count is all this API reports.
    """
    return _scan_pass(si, None, log_keys, lines, since=since)


def _scan_pass(
    si: ServiceInstance,
    host_name: str | None,
    log_keys: tuple[str, ...],
    lines: int,
    since: Mapping[tuple[str, str], int] | None,
) -> HostLogPass:
    content = si.RetrieveContent()
    diag_mgr = content.diagnosticManager
    on_vcenter = getattr(getattr(content, "about", None), "apiType", "") == "VirtualCenter"

    issues: list[dict] = []
    positions = dict(since or {})
    # Enumerate hosts + name in one batched call, then narrow to host_name
    # before issuing the (inherent) BrowseDiagnosticLog RPCs.
    for host_ref, props in _collect(si, [vim.HostSystem], ["name"]):
        raw_name = props.get("name", "")
        if host_name and raw_name != host_name:
            continue
        # The host name is vSphere text too, and it goes into every message.
        name = sanitize(raw_name, 200)
        scope = {"host": host_ref} if on_vcenter else {}
        for log_key in log_keys:
            prior = None if since is None else since.get((raw_name, log_key))
            try:
                read = _read_log(diag_mgr, log_key, scope, lines, prior)
            except _UNREADABLE as exc:
                _log_read_failure(name, log_key, exc)
                issues.append(_info("host_log:unavailable", name,
                                    f"{name}: {log_key} log could not be read — "
                                    f"{_read_failure(exc)}"))
                continue
            positions[(raw_name, log_key)] = read.position
            if read.skipped != 0:
                issues.append(_info("host_log:skipped", name,
                                    _skip_message(name, log_key, read.skipped)))
            issues.extend(_findings(read.text, name, log_key))

    return HostLogPass(issues=tuple(issues), positions=positions)


def _window(total: int, prior: int | None, lines: int) -> tuple[int, int, int | None]:
    """Which lines to read: ``(start, count, skipped)``; skipped None = unknown."""
    tail_start = max(1, total - lines + 1)
    if prior is None:
        return tail_start, lines, 0
    if total < prior:
        return tail_start, lines, None  # rotated
    new = total - prior
    if new > lines:
        return tail_start, lines, new - lines
    return prior + 1, new, 0


def _read_log(
    diag_mgr: object,
    log_key: str,
    scope: dict,
    lines: int,
    prior: int | None,
) -> _LogRead:
    """Probe for the last line number, then read the window after ``prior``."""
    probe = diag_mgr.BrowseDiagnosticLog(key=log_key, start=_PAST_THE_END, **scope)
    total = getattr(probe, "lineEnd", 0) or 0
    start, count, skipped = _window(total, prior, lines)
    if count <= 0:
        return _LogRead(text=(), position=total, skipped=skipped)
    data = diag_mgr.BrowseDiagnosticLog(key=log_key, start=start, lines=count, **scope)
    text = tuple(data.lineText or ()) if data else ()
    position = start + len(text) - 1 if text else total
    return _LogRead(text=text, position=position, skipped=skipped)


def _findings(text: tuple[str, ...], name: str, log_key: str) -> list[dict]:
    """The lines matching a trouble pattern, as issue rows."""
    rows: list[dict] = []
    for line in text:
        line_lower = line.lower()
        if not any(pattern in line_lower for pattern in _ERROR_PATTERNS):
            continue
        severity = "critical" if any(p in line_lower for p in _CRITICAL_PATTERNS) else "warning"
        # Sanitize host log lines: truncate, strip ALL control characters,
        # and wrap in boundary markers to prevent prompt injection from
        # attacker-controlled content.
        safe_line = sanitize(line.strip(), 200)
        rows.append({
            "severity": severity,
            "source": f"host_log:{log_key}",
            "message": f"[VSPHERE_HOST_LOG]{name}: {safe_line}[/VSPHERE_HOST_LOG]",
            "time": str(datetime.now(tz=timezone.utc)),
            "entity": name,
        })
    return rows


def _info(source: str, name: str, message: str) -> dict:
    return {"severity": "info", "source": source, "message": message,
            "time": str(datetime.now(tz=timezone.utc)), "entity": name}


def _skip_message(name: str, log_key: str, skipped: int | None) -> str:
    if skipped is None:
        return (f"{name}: {log_key} log rotated since the last read — lines written "
                "between that read and the rotation were not scanned")
    return (f"{name}: {log_key} log grew by more than one read — {skipped} line(s) "
            "were not scanned; the newest were")


def _log_read_failure(name: str, log_key: str, exc: Exception) -> None:
    """Server-log record of an unreadable log, at a level the daemon actually
    prints (it runs at INFO) — _read_failure points here."""
    detail = sanitize(str(exc), 300).replace("\n", " ")
    _log.warning(
        "Could not read the %s log on host %s: %s: %s",
        log_key, name, type(exc).__name__, detail,
    )
    _log.debug("Traceback for the %s log on %s", log_key, name, exc_info=True)


def _read_failure(exc: Exception) -> str:
    """Why a log could not be read, in words an operator can act on.

    Authored, not quoted: the fault text can carry host names and paths, and
    this reaches the scan log and any agent reading it verbatim.
    """
    if isinstance(exc, vim.fault.NoPermission):
        return ("NoPermission — reading host logs needs the Global.Diagnostics "
                "privilege, which vCenter's Read-Only role does not include")
    return (f"{type(exc).__name__} — the log could not be read; the daemon log "
            "has a warning with the fault detail")


def _safe_entity_name(event) -> str:
    """Safely extract entity name from event."""
    try:
        if hasattr(event, "vm") and event.vm:
            return event.vm.name
        if hasattr(event, "host") and event.host:
            return event.host.name
        if hasattr(event, "ds") and event.ds:
            return event.ds.name
    except Exception:
        _log.debug("Failed to extract entity name from event", exc_info=True)
    return "N/A"
