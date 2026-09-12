"""The daemon's host-log pass: each line once, the right lines paged, no false all-clear.

Once the scanner really read host logs (2026-09-11, live: 228 matching lines per
cycle), two things about the daemon became visible:

* **Each line, every cycle.** Every 15-minute cycle re-read the last 500 lines
  and reported the same lines again. The daemon now remembers, per target,
  host and log, the last line it read, and reads only what came after.
* **Everything paged.** 135 of those 228 lines were one routine hostd line,
  and every critical/warning issue went to the webhook. Host-log warnings now
  go to the scan log only; host-log criticals, and alarm/event warnings, still
  page.

And a pass that raised must not end in "all clear" — it used to: the error was
logged and the summary fell through to the empty-issues branch.

These drive the real scanner over a real ``vim.DiagnosticManager`` (see
``_diag_fakes``); only the alarm and event passes are replaced.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

from pyVmomi import vim

from tests.eval.regression._diag_fakes import LogStub, diag_manager, host, numbered
from vmware_aiops.scanner import log_scanner, scheduler

LOG_KEYS = ("hostd", "vmkernel", "vpxa")


def _daemon(monkeypatch, stub, alarms=()):
    """Wire _run_scan to ``stub`` for host logs; return (logged, sent, run-one-cycle)."""
    logged, sent = [], []
    monkeypatch.setattr(scheduler, "scan_alarms", lambda si: list(alarms))
    monkeypatch.setattr(scheduler, "scan_logs", lambda si, cfg: [])
    monkeypatch.setattr(scheduler, "ScanLogger",
                        lambda path: SimpleNamespace(log_issue=logged.append))
    monkeypatch.setattr(scheduler, "WebhookNotifier",
                        lambda **kw: SimpleNamespace(send=sent.append))
    content = SimpleNamespace(diagnosticManager=diag_manager(stub),
                              about=SimpleNamespace(apiType="VirtualCenter"))
    si = SimpleNamespace(RetrieveContent=lambda: content)
    rows = [(host("host-10", stub), {"name": "esx-01"})]
    monkeypatch.setattr(log_scanner, "_collect", lambda *a, **k: rows)
    notify = SimpleNamespace(log_file="unused", webhook_url="https://hooks.example/x",
                             webhook_timeout=5)
    config = SimpleNamespace(notify=notify, scanner=None)
    conn_mgr = SimpleNamespace(list_targets=lambda: ["vc"], connect=lambda name: si)
    cursors = scheduler.HostLogCursors()
    return logged, sent, lambda: scheduler._run_scan(config, conn_mgr, cursors)


def _messages(issues):
    return [i["message"] for i in issues]


def test_the_daemon_logs_the_gap_and_does_not_page_for_it(monkeypatch, caplog):
    stub = LogStub({}, fail={k: vim.fault.NoPermission(privilegeId="Global.Diagnostics")
                             for k in LOG_KEYS})
    logged, sent, cycle = _daemon(monkeypatch, stub)
    with caplog.at_level(logging.INFO, logger=scheduler.logger.name):
        cycle()

    assert [i["source"] for i in logged] == ["host_log:unavailable"] * 3
    assert all("Global.Diagnostics" in m for m in _messages(logged))
    assert sent == [], "an info-level gap paged the webhook"
    assert "all clear" not in caplog.text
    assert "3 unreadable host log(s)" in caplog.text


def test_the_second_cycle_reports_only_lines_written_since_the_first(monkeypatch):
    logs = {k: numbered(600) for k in LOG_KEYS}
    logs["hostd"][550] = "error: seen in the first cycle"
    logged, _sent, cycle = _daemon(monkeypatch, LogStub(logs))

    cycle()
    assert _messages(logged) == [
        "[VSPHERE_HOST_LOG]esx-01: error: seen in the first cycle[/VSPHERE_HOST_LOG]"
    ]
    logged.clear()

    cycle()
    assert logged == [], "the second cycle reported a line the first already had"

    logs["vmkernel"].append("error: written between the cycles")
    cycle()
    assert _messages(logged) == [
        "[VSPHERE_HOST_LOG]esx-01: error: written between the cycles[/VSPHERE_HOST_LOG]"
    ]


def test_a_rotated_log_is_read_again_from_its_tail(monkeypatch):
    logs = {k: numbered(600) for k in LOG_KEYS}
    logged, _sent, cycle = _daemon(monkeypatch, LogStub(logs))
    cycle()

    logs["hostd"] = ["routine", "error: first line of interest after rotation"]
    cycle()
    assert "[VSPHERE_HOST_LOG]esx-01: error: first line of interest after rotation" \
        "[/VSPHERE_HOST_LOG]" in _messages(logged)
    notes = [i for i in logged if i["source"] == "host_log:skipped"]
    assert len(notes) == 1 and "rotated" in notes[0]["message"]


def test_an_overflow_reads_the_newest_lines_and_says_how_many_were_skipped(monkeypatch):
    logs = {k: numbered(10) for k in LOG_KEYS}
    logged, sent, cycle = _daemon(monkeypatch, LogStub(logs))
    cycle()

    logs["hostd"].extend(numbered(700, first=11))
    logs["hostd"][-1] = "error: the newest line"
    cycle()
    notes = [i for i in logged if i["source"] == "host_log:skipped"]
    assert len(notes) == 1 and "200 line(s)" in notes[0]["message"]
    assert notes[0]["severity"] == "info"
    assert "[VSPHERE_HOST_LOG]esx-01: error: the newest line[/VSPHERE_HOST_LOG]" \
        in _messages(logged)
    assert sent == []


def test_host_log_warnings_are_logged_but_only_criticals_page(monkeypatch):
    alarm_warning = {"severity": "warning", "source": "alarm", "message": "[host:esx-01] CPU",
                     "time": "", "entity": "esx-01"}
    logs = {k: [] for k in LOG_KEYS}
    logs["hostd"] = ["warning-level: operation timeout", "kernel panic: critical fault"]
    logged, sent, cycle = _daemon(monkeypatch, LogStub(logs), alarms=[alarm_warning])
    cycle()

    by_sev = {i["severity"]: i for i in logged if i["source"] == "host_log:hostd"}
    assert set(by_sev) == {"warning", "critical"}, "both host-log lines belong in the scan log"
    (paged,) = sent
    assert by_sev["critical"] in paged, "a critical host-log line was not paged"
    assert by_sev["warning"] not in paged, "a host-log warning paged the webhook"
    assert alarm_warning in paged, "alarm warnings must page exactly as before"


def test_a_failed_pass_is_never_summarised_as_all_clear(monkeypatch, caplog):
    logged, _sent, cycle = _daemon(monkeypatch, LogStub({k: [] for k in LOG_KEYS}))

    def _broken(si):
        raise RuntimeError("alarm manager exploded")

    monkeypatch.setattr(scheduler, "scan_alarms", _broken)
    with caplog.at_level(logging.INFO, logger=scheduler.logger.name):
        cycle()

    assert logged == []
    assert "all clear" not in caplog.text, "a cycle whose alarm pass raised said all clear"
    assert "INCOMPLETE" in caplog.text and "vc: alarm scan" in caplog.text
    summary = [r for r in caplog.records if "INCOMPLETE" in r.getMessage()]
    assert summary[0].levelno == logging.WARNING


def test_a_bug_in_the_host_log_pass_fails_the_pass_loudly(monkeypatch, caplog):
    """A programming error propagates out of the scanner; the daemon marks the
    pass failed, with the traceback, instead of an unreadable-log info row."""
    logged, _sent, cycle = _daemon(monkeypatch, LogStub({k: [] for k in LOG_KEYS}))

    def _buggy_window(*args):
        raise NameError("name 'tail_start' is not defined")

    monkeypatch.setattr(log_scanner, "_window", _buggy_window)
    with caplog.at_level(logging.INFO, logger=scheduler.logger.name):
        cycle()

    assert not [i for i in logged if i["source"] == "host_log:unavailable"]
    assert "vc: host log scan" in caplog.text
    failures = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert failures and failures[0].exc_info, "the failed pass was logged without its traceback"


def test_the_scheduler_shares_one_cursor_store_across_every_cycle(monkeypatch, tmp_path):
    seen = []
    jobs = []

    class _Scheduler:
        def add_job(self, func, **kw):
            if func is scheduler._run_scan:
                jobs.append(kw["args"])

        def start(self):
            pass

    config = SimpleNamespace(scanner=SimpleNamespace(enabled=True, interval_minutes=15),
                             notify=SimpleNamespace())
    monkeypatch.setattr(scheduler, "PID_FILE", tmp_path / "daemon.pid")
    monkeypatch.setattr(scheduler, "load_config", lambda p=None: config)
    monkeypatch.setattr(scheduler, "ConnectionManager", lambda cfg: SimpleNamespace(
        list_targets=lambda: [], disconnect_all=lambda: None))
    monkeypatch.setattr(scheduler, "BlockingScheduler", _Scheduler)
    fake_run = lambda cfg, conn, cursors=None: seen.append(cursors)  # noqa: E731
    monkeypatch.setattr(scheduler, "_run_scan", fake_run)
    monkeypatch.setattr(scheduler.signal, "signal", lambda *a: None)

    scheduler.start_scheduler()

    (job_args,) = jobs
    assert isinstance(seen[0], scheduler.HostLogCursors)
    assert job_args[2] is seen[0], "the scheduled cycles would forget what the first one read"
