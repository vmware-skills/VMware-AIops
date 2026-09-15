"""The scanner daemon's calls are audited — the TTL delete above all.

A family survey on 2026-09-15 found that ``scanner/scheduler.py`` deleted VMs whose
TTL had expired with no row in either audit trail: not on success, not on
failure. ``daemon start`` was marked ``@cli_local``, so the I-9 gate saw nothing,
and the scheduler called ``delete_vm`` directly, bypassing the ``guard()`` that
stops the same deletion over MCP (``vm_delete``) or the CLI. The scan cycle and the
webhook send reached vCenter and an external endpoint without a row either.

HLD §8.1 / I-8: every call that reaches a remote system or sends data out writes
one row, a failed one included. These tests read the rows back from the sandbox
database the engine is bound to.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from vmware_policy import get_engine
from vmware_policy.audit import reset_engine
from vmware_policy.policy import PolicyDenied, PolicyResult


@pytest.fixture(autouse=True)
def isolated_audit(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_HOME", str(tmp_path / "vmware"))
    reset_engine()
    yield
    reset_engine()


def rows(tool: str | None = None) -> list[dict]:
    db = Path(get_engine()._path)
    if not db.exists():
        return []
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        query = "SELECT skill, tool, status, params, result, risk_level FROM audit_log"
        args: tuple = ()
        if tool is not None:
            query += " WHERE tool = ?"
            args = (tool,)
        return [dict(r) for r in con.execute(query + " ORDER BY id", args)]


def _ttl_entry(vm_name: str = "ttl-vm", target: str | None = "lab-vc"):
    from vmware_aiops.ops.ttl import TTLEntry

    return TTLEntry(vm_name=vm_name, expires_at="2026-01-01T00:00:00+00:00", target=target)


@pytest.fixture
def ttl(monkeypatch):
    from vmware_aiops.scanner import scheduler

    removed: list[str] = []
    monkeypatch.setattr(scheduler, "get_expired_entries", lambda: [_ttl_entry()])
    monkeypatch.setattr(scheduler, "remove_entry", removed.append)
    return scheduler, removed


# ── TTL delete ───────────────────────────────────────────────────────────────


def test_a_ttl_delete_writes_one_vm_delete_row(ttl, monkeypatch):
    scheduler, removed = ttl
    monkeypatch.setattr(scheduler, "delete_vm", lambda si, name: f"VM '{name}' deleted")

    scheduler._run_ttl_check(MagicMock())

    found = rows("vm_delete")
    assert len(found) == 1, f"a TTL delete left no vm_delete row: {rows()}"
    row = found[0]
    assert row["skill"] == "aiops"
    assert row["status"] == "ok"
    assert row["risk_level"] == "critical"
    assert '"ttl-vm"' in row["params"] and "ttl_expiry" in row["params"]
    assert removed == ["ttl-vm"]


def test_a_failed_ttl_delete_is_recorded_as_error_and_the_entry_kept(ttl, monkeypatch):
    scheduler, removed = ttl

    def boom(si, name):
        raise RuntimeError("vCenter task timeout")

    monkeypatch.setattr(scheduler, "delete_vm", boom)

    scheduler._run_ttl_check(MagicMock())

    found = rows("vm_delete")
    assert len(found) == 1 and found[0]["status"] == "error", rows()
    assert "vCenter task timeout" in found[0]["result"]
    assert removed == []


def test_a_ttl_vm_already_gone_is_recorded_and_the_stale_entry_dropped(ttl, monkeypatch):
    from vmware_aiops.ops.vm_lifecycle import VMNotFoundError

    scheduler, removed = ttl

    def gone(si, name):
        raise VMNotFoundError(f"VM '{name}' not found")

    monkeypatch.setattr(scheduler, "delete_vm", gone)

    scheduler._run_ttl_check(MagicMock())

    found = rows("vm_delete")
    assert len(found) == 1 and found[0]["status"] == "error", rows()
    assert "not found" in found[0]["result"]
    assert removed == ["ttl-vm"]


def test_a_ttl_delete_refused_by_policy_never_runs(ttl, monkeypatch):
    scheduler, removed = ttl
    deleted: list[str] = []
    monkeypatch.setattr(scheduler, "delete_vm", lambda si, name: deleted.append(name))

    def deny(skill, tool, params=None, *, risk_level="low", target=""):
        raise PolicyDenied(
            PolicyResult(allowed=False, rule="no-deletes", reason="deletes are frozen")
        )

    monkeypatch.setattr(scheduler, "guard", deny)

    scheduler._run_ttl_check(MagicMock())

    assert deleted == [], "a denied TTL delete must not reach vCenter"
    found = rows("vm_delete")
    assert len(found) == 1 and found[0]["status"] == "denied", rows()
    assert removed == [], "a denied delete keeps the entry for when the rule is lifted"


# ── Scan cycle and webhook ───────────────────────────────────────────────────


class _StubLogger:
    def __init__(self, *args, **kwargs):
        self.logged: list[dict] = []

    def log_issue(self, issue: dict) -> None:
        self.logged.append(issue)


def _config(webhook_url: str | None = None):
    return SimpleNamespace(
        notify=SimpleNamespace(log_file="unused.log", webhook_url=webhook_url, webhook_timeout=5),
        scanner=SimpleNamespace(),
    )


@pytest.fixture
def quiet_scan(monkeypatch):
    from vmware_aiops.scanner import scheduler

    monkeypatch.setattr(scheduler, "ScanLogger", _StubLogger)
    monkeypatch.setattr(scheduler, "scan_alarms", lambda si: [])
    monkeypatch.setattr(scheduler, "scan_logs", lambda si, cfg: [])
    monkeypatch.setattr(scheduler, "_host_log_issues", lambda si, name, cursors: [])
    return scheduler


def test_a_clean_scan_cycle_writes_one_ok_row(quiet_scan):
    conn = MagicMock()
    conn.list_targets.return_value = ["lab-vc"]

    quiet_scan._run_scan(_config(), conn)

    found = rows("daemon_scan")
    assert len(found) == 1 and found[0]["status"] == "ok", rows()


def test_a_scan_cycle_that_cannot_connect_is_recorded_as_error(quiet_scan):
    conn = MagicMock()
    conn.list_targets.return_value = ["lab-vc"]
    conn.connect.side_effect = ConnectionError("lab-vc unreachable")

    quiet_scan._run_scan(_config(), conn)

    found = rows("daemon_scan")
    assert len(found) == 1 and found[0]["status"] == "error", rows()
    assert "lab-vc" in found[0]["result"]


def test_a_failed_webhook_send_is_recorded_as_error(quiet_scan, monkeypatch):
    class _FailingWebhook:
        def __init__(self, url=None, timeout=None):
            self.url = url

        def send(self, issues):
            return False

    monkeypatch.setattr(quiet_scan, "WebhookNotifier", _FailingWebhook)
    conn = MagicMock()
    conn.list_targets.return_value = ["lab-vc"]
    conn.connect.side_effect = ConnectionError("lab-vc unreachable")  # a critical issue to page

    quiet_scan._run_scan(_config(webhook_url="https://hooks.example.invalid/T0K3N"), conn)

    found = rows("webhook_send")
    assert len(found) == 1 and found[0]["status"] == "error", rows()
    assert "T0K3N" not in found[0]["params"], (
        "the webhook URL can carry a token; it must not be recorded"
    )


# ── daemon start ─────────────────────────────────────────────────────────────


def test_daemon_start_is_audited_not_local():
    from vmware_aiops.cli.scan import daemon_start

    chain, fn = [], daemon_start
    while fn is not None and fn not in chain:
        chain.append(fn)
        fn = getattr(fn, "__wrapped__", None)
    assert any(getattr(f, "_is_audited", False) for f in chain), (
        "daemon start reaches vCenter; it must be @audited"
    )
    assert not any(getattr(f, "_is_local", False) for f in chain), (
        "daemon start is not a local command"
    )
