"""Notification failures cannot publish opaque endpoint tokens or remote bodies."""

from types import SimpleNamespace

import httpx
import pytest

from vmware_aiops.notify.webhook import WebhookNotifier
from vmware_aiops.scanner import scheduler


@pytest.mark.parametrize("failure", ["transport", "status"])
def test_notifier_logs_only_error_class_or_status(monkeypatch, caplog, failure):
    def post(*args, **kwargs):
        if failure == "transport":
            raise httpx.ConnectError("https://hook.example/opaque-private-token")
        return httpx.Response(500, text="opaque-private-token echoed remotely")

    monkeypatch.setattr(httpx, "post", post)
    assert not WebhookNotifier("https://hook.example/opaque-private-token", timeout=1).send(
        [{"severity": "critical", "message": "incident text"}]
    )
    assert "opaque-private-token" not in caplog.text
    assert "incident text" not in caplog.text


def test_unexpected_sender_exception_is_not_copied_into_audit(monkeypatch):
    rows = []
    monkeypatch.setattr(scheduler, "_audit", lambda *args: rows.append(args))

    def fail(issues):
        raise RuntimeError("https://hook.example/opaque-private-token")

    with pytest.raises(RuntimeError):
        scheduler._send_webhook(SimpleNamespace(send=fail), [])
    assert rows and rows[0][2] == "error"
    assert "opaque-private-token" not in str(rows)


def test_connection_exception_is_not_sent_to_webhook(monkeypatch):
    sent = []
    monkeypatch.setattr(
        scheduler, "ScanLogger", lambda *a: SimpleNamespace(log_issue=lambda i: None)
    )
    monkeypatch.setattr(
        scheduler,
        "WebhookNotifier",
        lambda **k: SimpleNamespace(send=lambda issues: sent.extend(issues) or True),
    )
    monkeypatch.setattr(scheduler, "_audit", lambda *a: None)

    def fail(name):
        raise ConnectionError("https://user:password@host/opaque-private-token")

    conn = SimpleNamespace(list_targets=lambda: ["vc"], connect=fail)
    config = SimpleNamespace(
        scanner=SimpleNamespace(),
        notify=SimpleNamespace(
            log_file="unused",
            webhook_url="https://hook.example/opaque-private-token",
            webhook_timeout=1,
        ),
    )
    scheduler._run_scan(config, conn)
    assert sent and "ConnectionError" in str(sent)
    assert "opaque-private-token" not in str(sent) and "password" not in str(sent)
