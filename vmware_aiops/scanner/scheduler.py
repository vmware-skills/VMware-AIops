"""APScheduler-based daemon for periodic scanning."""

from __future__ import annotations

import logging
import os
import signal
import sys
import time
from collections.abc import Mapping
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from vmware_policy.guard import audit_call, guard
from vmware_policy.policy import PolicyDenied
from vmware_policy.sanitize import sanitize

from vmware_aiops.config import AppConfig, load_config
from vmware_aiops.connection import ConnectionManager
from vmware_aiops.notify.logger import ScanLogger
from vmware_aiops.notify.webhook import WebhookNotifier
from vmware_aiops.ops.ttl import get_expired_entries, remove_entry
from vmware_aiops.ops.vm_lifecycle import VMNotFoundError, delete_vm
from vmware_aiops.scanner.alarm_scanner import scan_alarms
from vmware_aiops.scanner.log_scanner import scan_host_logs_since, scan_logs

logger = logging.getLogger("vmware-aiops.scheduler")

PID_FILE = Path.home() / ".vmware-aiops" / "daemon.pid"

_SKILL = "aiops"

_UNREADABLE_SOURCE = "host_log:unavailable"
_SKIPPED_SOURCE = "host_log:skipped"


class HostLogCursors:
    """Where the daemon last read each host log: (target, host, log) -> line.

    Process memory only: a restarted daemon reads the tail of every log once
    more, then continues from there. Without this every cycle re-read the last
    500 lines and reported the same lines again, every 15 minutes.
    """

    def __init__(self) -> None:
        self._by_target: dict[str, Mapping[tuple[str, str], int]] = {}

    def since(self, target: str) -> Mapping[tuple[str, str], int]:
        return dict(self._by_target.get(target, {}))

    def advance(self, target: str, positions: Mapping[tuple[str, str], int]) -> None:
        self._by_target = {**self._by_target, target: dict(positions)}


def _pages(issue: dict) -> bool:
    """Whether an issue goes to the webhook.

    Critical always does. A warning does unless it is a host-log line: a
    busy host writes routine lines that match "fail"/"timeout" by the
    hundred (live: 135 of 228 matches in one cycle were one hostd line), so
    host-log warnings go to the scan log only. Alarm and event warnings page
    exactly as before.
    """
    severity = issue.get("severity")
    if severity == "critical":
        return True
    if severity == "warning":
        return not str(issue.get("source", "")).startswith("host_log")
    return False


def _host_log_issues(si: object, target_name: str, cursors: HostLogCursors) -> list[dict]:
    """New host-log lines as issues (plus the scanner's unread/skipped rows)."""
    result = scan_host_logs_since(si, cursors.since(target_name))
    cursors.advance(target_name, result.positions)
    return list(result.issues)


def _scan_target(
    si: object, target_name: str, config: AppConfig, cursors: HostLogCursors
) -> tuple[list[dict], list[str]]:
    """Run the three passes on one target: (issues, names of failed passes)."""
    passes = (
        ("Alarm scan", lambda: scan_alarms(si)),
        ("Log scan", lambda: scan_logs(si, config.scanner)),
        ("Host log scan", lambda: _host_log_issues(si, target_name, cursors)),
    )
    issues: list[dict] = []
    failed: list[str] = []
    for label, run in passes:
        try:
            issues.extend(run())
        except Exception as e:
            # With the traceback: a failed pass may be a bug in this code, and
            # "Scan complete" below will say the cycle was incomplete.
            logger.error("%s failed for %s: %s", label, target_name, e, exc_info=True)
            failed.append(f"{target_name}: {label.lower()}")
    return issues, failed


def _log_summary(all_issues: list[dict], paged: list[dict], failed: list[str]) -> None:
    """One line that never claims "all clear" about a cycle that did not run."""
    unreadable = sum(1 for i in all_issues if i.get("source") == _UNREADABLE_SOURCE)
    skipped = sum(1 for i in all_issues if i.get("source") == _SKIPPED_SOURCE)
    findings = len(all_issues) - unreadable - skipped
    if not all_issues and not failed:
        logger.info("Scan complete: all clear")
        return
    summary = (f"{findings} finding(s) ({len(paged)} sent to the webhook), "
               f"{unreadable} unreadable host log(s), {skipped} host log(s) with "
               f"unscanned lines, {len(failed)} failed pass(es)")
    if failed:
        logger.warning("Scan INCOMPLETE: %s: %s", summary, "; ".join(failed))
    else:
        logger.info("Scan complete: %s", summary)


def _audit(
    tool: str, params: dict, status: str, result: object, started: float, risk_level: str = "low"
) -> None:
    """Write one audit row for a call the daemon made (HLD §8.1 / I-8).

    The daemon is neither an MCP tool nor a CLI command, so no decorator writes
    its rows. Until 2026-09-15 nothing did: a TTL delete, a scan cycle against
    vCenter and a webhook send left no row in either audit trail. The status is
    passed explicitly, so this needs nothing newer than vmware-policy 1.15.0.
    """
    audit_call(
        _SKILL,
        tool,
        params=params,
        result=result,
        status=status,
        duration_ms=int((time.time() - started) * 1000),
        risk_level=risk_level,
    )


#: The last TTL outcome recorded per VM, for the life of the daemon. The TTL
#: check runs every minute, so a refused or failing entry would otherwise write
#: the same row 1,440 times a day (review, 2026-09-15; HLD §8.1: volume is part of
#: the contract). Replaced, never mutated in place.
_last_ttl_outcome: dict[str, str] = {}


def _audit_ttl(
    vm_name: str, params: dict, status: str, result: object, started: float,
    risk_level: str = "critical",
) -> None:
    """Record a TTL delete attempt when its outcome changes; every success is recorded.

    The outcome is the status plus the refusing rule or the error's class, so a
    different reason for the same status is recorded again.
    """
    global _last_ttl_outcome
    detail = ""
    if isinstance(result, dict):
        detail = str(result.get("rule") or result.get("error") or "")
    signature = f"{status}:{detail.split(':', 1)[0][:80]}"
    if status != "ok" and _last_ttl_outcome.get(vm_name) == signature:
        return
    if status == "ok":
        _last_ttl_outcome = {k: v for k, v in _last_ttl_outcome.items() if k != vm_name}
    else:
        _last_ttl_outcome = {**_last_ttl_outcome, vm_name: signature}
    _audit("vm_delete", params, status, result, started, risk_level)


def _send_webhook(webhook: WebhookNotifier, paged: list[dict]) -> None:
    """Send the paged issues and record the send.

    The URL is never recorded: it can carry a token.
    """
    started = time.time()
    params = {"issues": len(paged)}
    status, result = "ok", {"issues": len(paged)}
    try:
        if not webhook.send(paged):
            status = "error"
            result = {
                "error": "webhook send failed; the daemon log has the HTTP status or exception"
            }
    except Exception as exc:
        status, result = "error", {"error": sanitize(f"{type(exc).__name__}: {exc}", 500)}
        raise
    except BaseException as exc:
        # Ctrl+C / shutdown mid-send: not delivered, and not a plain failure.
        status, result = "interrupted", {"error": f"send interrupted ({type(exc).__name__})"}
        raise
    finally:
        _audit("webhook_send", params, status, result, started)


def _run_scan(
    config: AppConfig,
    conn_mgr: ConnectionManager,
    cursors: HostLogCursors | None = None,
) -> None:
    """Execute a single scan cycle across all targets.

    ``cursors`` is the daemon's memory of how far each host log was read; the
    scheduler passes the same one to every cycle. Without it (a one-off cycle)
    every log's last lines are read.
    """
    cursors = cursors if cursors is not None else HostLogCursors()
    started = time.time()
    targets: list[str] = []
    status, outcome = "ok", {}
    try:
        scan_logger = ScanLogger(config.notify.log_file)
        webhook = WebhookNotifier(
            url=config.notify.webhook_url,
            timeout=config.notify.webhook_timeout,
        )

        all_issues: list[dict] = []
        failed: list[str] = []
        targets = list(conn_mgr.list_targets())

        for target_name in targets:
            try:
                si = conn_mgr.connect(target_name)
            except Exception as e:
                issue = {
                    "severity": "critical",
                    "source": "connection",
                    "message": f"Failed to connect to {target_name}: {e}",
                    "time": "",
                    "entity": target_name,
                }
                all_issues.append(issue)
                failed.append(f"{target_name}: connect")
                continue
            issues, target_failed = _scan_target(si, target_name, config, cursors)
            all_issues.extend(issues)
            failed.extend(target_failed)

        # Log all issues
        for issue in all_issues:
            scan_logger.log_issue(issue)

        paged = [i for i in all_issues if _pages(i)]
        if paged and config.notify.webhook_url:
            _send_webhook(webhook, paged)

        _log_summary(all_issues, paged, failed)
        outcome = {"findings": len(all_issues), "paged": len(paged), "failed": failed}
        if failed:
            # A cycle with a pass that did not run is not a clean cycle.
            status = "error"
            outcome["error"] = sanitize("incomplete scan: " + "; ".join(failed), 500)
    except Exception as exc:
        status, outcome = "error", {"error": sanitize(f"{type(exc).__name__}: {exc}", 500)}
        raise
    except BaseException as exc:
        # Ctrl+C during the first scan (before signal handlers are installed) or a
        # shutdown mid-cycle used to leave an `ok` row (review, 2026-09-15).
        status, outcome = "interrupted", {"error": f"scan interrupted ({type(exc).__name__})"}
        raise
    finally:
        _audit("daemon_scan", {"targets": targets}, status, outcome, started)


def _run_ttl_check(conn_mgr: ConnectionManager) -> None:
    """Check for expired VM TTLs and delete them."""
    global _last_ttl_outcome
    expired = get_expired_entries()
    if not expired:
        return

    for entry in expired:
        target = entry.target
        vm_name = entry.vm_name
        params = {"vm_name": vm_name, "target": target, "trigger": "ttl_expiry"}
        started = time.time()
        # The same authorization as the vm_delete MCP tool and CLI command
        # (HLD I-3): a deny rule on vm_delete must stop an expiry too.
        try:
            guard(_SKILL, "vm_delete", params, risk_level="critical", target=target or "")
        except PolicyDenied as exc:
            _audit_ttl(vm_name, params, "denied",
                   {"error": exc.result.reason, "rule": exc.result.rule}, started, "critical")
            logger.warning("TTL deletion of VM '%s' refused by policy (%s); keeping entry",
                           vm_name, exc.result.reason)
            continue
        except Exception as exc:
            # An unreadable rule set must not turn into an unchecked delete.
            _audit_ttl(vm_name, params, "error",
                   {"error": sanitize(f"policy check failed: {exc}", 500)}, started, "critical")
            logger.warning("TTL deletion of VM '%s' skipped: policy check failed: %s", vm_name, exc)
            continue
        try:
            si = conn_mgr.connect(target)
            result = delete_vm(si, vm_name)
            logger.info("TTL expired: %s", result)
        except VMNotFoundError as exc:
            # VM already gone — entry is stale, safe to drop. The delete itself
            # did not happen, so the row says error with why.
            reason = f"not deleted, already gone; stale TTL entry removed: {exc}"
            _audit_ttl(vm_name, params, "error",
                   {"error": sanitize(reason, 500)}, started, "critical")
            logger.info("TTL VM '%s' no longer exists; removing entry", vm_name)
        except Exception as e:
            # Transient failure (connection, task error): keep the entry so
            # the next cycle retries instead of silently orphaning the VM.
            _audit_ttl(vm_name, params, "error",
                   {"error": sanitize(f"{type(e).__name__}: {e}", 500)}, started, "critical")
            logger.warning(
                "TTL deletion failed for VM '%s'; keeping entry for retry: %s",
                vm_name, e,
            )
            continue
        else:
            _audit_ttl(vm_name, params, "ok",
                   {"result": sanitize(str(result), 500)}, started, "critical")
        remove_entry(vm_name)
        _last_ttl_outcome = {k: v for k, v in _last_ttl_outcome.items() if k != vm_name}


def start_scheduler(config_path: Path | None = None) -> None:
    """Start the blocking scheduler daemon."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    config = load_config(config_path)
    conn_mgr = ConnectionManager(config)

    if not config.scanner.enabled:
        logger.warning("Scanner is disabled in config. Exiting.")
        return

    # Write PID file
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")

    # One cursor store for the life of the process, shared by the first scan
    # and every scheduled one — that is what makes each host-log line appear
    # in one cycle rather than in every cycle it stays within the last 500.
    cursors = HostLogCursors()
    scheduler = BlockingScheduler()
    scheduler.add_job(
        _run_scan,
        trigger=IntervalTrigger(minutes=config.scanner.interval_minutes),
        args=[config, conn_mgr, cursors],
        id="vmware_scan",
        name="VMware AIops Scanner",
        max_instances=1,
        next_run_time=None,  # Scheduler interval starts after manual first run below
    )
    scheduler.add_job(
        _run_ttl_check,
        trigger=IntervalTrigger(minutes=1),
        args=[conn_mgr],
        id="vmware_ttl",
        name="VMware AIops TTL Check",
        max_instances=1,
    )

    # Run first scan immediately, then scheduler takes over
    logger.info(
        "Scanner starting. Interval: %dm. Targets: %s",
        config.scanner.interval_minutes,
        ", ".join(conn_mgr.list_targets()),
    )
    _run_scan(config, conn_mgr, cursors)

    def _shutdown(signum, frame):
        logger.info("Shutting down scanner...")
        scheduler.shutdown(wait=False)
        PID_FILE.unlink(missing_ok=True)
        conn_mgr.disconnect_all()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        scheduler.start()
    finally:
        PID_FILE.unlink(missing_ok=True)
        conn_mgr.disconnect_all()
