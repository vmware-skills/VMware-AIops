"""Regression — AIops's event sets match what pyVmomi really hands back.

AIops ranks events with CRITICAL_EVENTS / WARNING_EVENTS / INFO_EVENTS written as
bare class names ("HostConnectionLostEvent"), compared with
``type(event).__name__`` — which for a real pyVmomi event is
``vim.event.HostConnectionLostEvent``. Found live on a vCenter 8.0.3
(2026-09-14): nothing ever matched, so the event sweep ranked everything
"info" and the daemon's event scan, which keeps only critical and warning,
reported a quiet estate. The tests used stand-ins named with the bare name.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pyVmomi import vim

from vmware_aiops.ops import health
from vmware_aiops.scanner import log_scanner

_T = datetime(2026, 9, 3, 14, 8, 46, tzinfo=timezone.utc)


def _real(cls, key):
    return cls(
        key=key, createdTime=_T, fullFormattedMessage=f"{cls.__name__} {key}", userName="root"
    )


def _si():
    return SimpleNamespace(RetrieveContent=lambda: SimpleNamespace(eventManager=object()))


@pytest.mark.unit
def test_the_sweep_ranks_a_real_lost_host_as_critical(monkeypatch):
    events = [_real(vim.event.HostConnectionLostEvent, 1), _real(vim.event.VmPoweredOnEvent, 2)]
    monkeypatch.setattr(health, "query_events", lambda mgr, spec: list(events))
    rows = health.get_recent_events(_si(), hours=1, severity="info")
    sev = {r["event_type"]: r["severity"] for r in rows}
    assert sev["vim.event.HostConnectionLostEvent"] == "critical"


@pytest.mark.unit
def test_the_daemon_event_scan_finds_a_real_critical_event(monkeypatch):
    events = [
        _real(vim.event.HostConnectionLostEvent, 1),
        _real(vim.event.UserLoginSessionEvent, 2),
    ]
    monkeypatch.setattr(log_scanner, "query_events", lambda mgr, spec: list(events))
    cfg = SimpleNamespace(lookback_hours=1, severity_threshold="warning")
    issues = log_scanner.scan_logs(_si(), cfg)
    assert [i["severity"] for i in issues] == ["critical"], issues
