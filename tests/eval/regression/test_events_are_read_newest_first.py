"""Regression — AIops event reads return the newest events.

AIops called ``EventManager.QueryEvents`` directly in two places: the event
sweep (``ops/health.get_recent_events``) and the scanner (``scanner/log_scanner``).
Measured on a lab vCenter 8.0.3 (2026-09-14), QueryEvents returns at most 1000
events and they are the **oldest** in the window — a 24 h sweep missed the latest
16 hours. vmware-monitor fixed its reads by walking an event history collector
newest-first (``vmware_monitor.ops.health.read_events``); AIops now goes through
the same function rather than keeping a second copy of the bug.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from vmware_aiops.ops import health
from vmware_aiops.scanner import log_scanner

_T0 = datetime(2026, 9, 13, 14, 0, tzinfo=timezone.utc)


class _Collector:
    """vCenter's collector as measured: newest page first, older pages oldest-first."""

    def __init__(self, events):
        self._events, self._size, self._cursor = list(events), 10, None

    def SetCollectorPageSize(self, maxCount):  # noqa: N802,N803
        self._size = maxCount

    @property
    def latestPage(self):  # noqa: N802
        return list(reversed(self._events[-self._size:]))

    def ResetCollector(self):  # noqa: N802
        self._cursor = max(0, len(self._events) - self._size)

    def ReadPreviousEvents(self, maxCount):  # noqa: N802,N803
        start = max(0, self._cursor - maxCount)
        page, self._cursor = self._events[start:self._cursor], start
        return page

    def DestroyCollector(self):  # noqa: N802
        pass


class _EventManager:
    def __init__(self, events):
        self._events = events

    def CreateCollectorForEvents(self, filter):  # noqa: N802,A002
        return _Collector(self._events)

    def QueryEvents(self, _spec):  # noqa: N802
        return self._events[:1000]  # what vCenter really returns: the oldest 1000


def _events(n, cls="UserLoginSessionEvent"):
    kind = type(cls, (SimpleNamespace,), {})
    return [kind(key=i, createdTime=_T0 + timedelta(seconds=30 * i),
                 fullFormattedMessage=f"event {i}", userName="root") for i in range(1, n + 1)]


@pytest.mark.unit
def test_the_sweep_includes_a_critical_event_after_a_thousand_routine_ones():
    events = _events(1500)
    late = _events(1, "HostConnectionLostEvent")[0]
    late.key, late.createdTime = 99999, _T0 + timedelta(days=1)
    mgr = _EventManager(events + [late])
    si = SimpleNamespace(RetrieveContent=lambda: SimpleNamespace(eventManager=mgr))
    rows = health.get_recent_events(si, hours=48, severity="warning")
    assert any(r["event_type"] == "HostConnectionLostEvent" for r in rows)


@pytest.mark.unit
@pytest.mark.parametrize("module", [health, log_scanner])
def test_no_direct_query_events_call_remains(module):
    assert "QueryEvents(" not in inspect.getsource(module), f"{module.__name__} calls QueryEvents"
