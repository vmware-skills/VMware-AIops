"""Stopping the real MCP server must run atexit — that is the session logout.

Claude Code stops a stdio MCP server with SIGINT and then SIGTERM about a
millisecond later, while stdin is still open (measured 2026-09-15). With Python's
default handlers the process died before atexit, so every conversation left a
vSphere session open. A first fix — raise SystemExit from the handler — passed a
test that replaced ``mcp.run`` with a sleep, but against the real stdio loop the
interpreter then waited forever on anyio's stdin reader thread and atexit still
never ran (independent review). So this test starts the real server with stdin
held open, completes the MCP handshake, and sends the same signals the client does.

The second fix ignored further stop signals while the logout ran, with no limit.
pyVmomi connects with ``httpConnectionTimeout=None``, so a logout to a vCenter
that stopped answering waits indefinitely, and the server could then only be
ended with SIGKILL (second independent review, 2026-09-15). The logout now has a
deadline, and the last test here holds a logout open for ten minutes.
"""

from __future__ import annotations

import json
import os
import select
import signal
import subprocess
import sys
import textwrap
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal delivery")

MODULE = "vmware_aiops.mcp_server"

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "stop-signal-test", "version": "0"},
    },
}

STOPPED = (128 + signal.SIGINT, 128 + signal.SIGTERM)


def _read_reply(proc: subprocess.Popen, timeout: float) -> bytes:
    deadline = time.monotonic() + timeout
    buf = b""
    while b"\n" not in buf and time.monotonic() < deadline:
        ready, _, _ = select.select([proc.stdout], [], [], max(0.0, deadline - time.monotonic()))
        if not ready:
            break
        chunk = os.read(proc.stdout.fileno(), 65536)
        if not chunk:
            break
        buf += chunk
    return buf


def _stop_real_server(tmp_path, code: str, signals: tuple[str, ...], wait_s: float):
    """Start the server, finish the handshake, send ``signals``; return (rc, log, seconds)."""
    with open(tmp_path / "stderr.log", "wb") as stderr:
        proc = subprocess.Popen(
            [sys.executable, "-c", code],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
            env=dict(os.environ),
        )
        try:
            proc.stdin.write((json.dumps(INITIALIZE) + "\n").encode("utf-8"))
            proc.stdin.flush()
            reply = _read_reply(proc, timeout=60)
            assert b'"id":1' in reply.replace(b" ", b""), f"no initialize reply: {reply[:300]!r}"
            started = time.monotonic()
            for name in signals:
                proc.send_signal(getattr(signal, name))
                time.sleep(0.001)
            try:
                proc.wait(timeout=wait_s)
            except subprocess.TimeoutExpired:
                pytest.fail(f"server still running {wait_s:.0f}s after the stop signals")
            seconds = time.monotonic() - started
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            proc.stdin.close()
    log = (tmp_path / "stderr.log").read_text(encoding="utf-8", errors="replace")[-2000:]
    return proc.returncode, log, seconds


@pytest.mark.parametrize(
    "signals", [("SIGINT", "SIGTERM"), ("SIGTERM",)], ids=["int-then-term", "term"]
)
def test_stop_signals_run_atexit_on_the_real_server(tmp_path, signals):
    marker = tmp_path / "atexit-ran"
    code = textwrap.dedent(
        f"""
        import atexit
        from {MODULE} import server
        atexit.register(lambda: open({str(marker)!r}, "w", encoding="utf-8").write("ok"))
        server.main()
        """
    )
    rc, log, _seconds = _stop_real_server(tmp_path, code, signals, wait_s=20)
    assert marker.exists(), f"atexit did not run (rc={rc}): {log}"
    assert rc in STOPPED, log


def test_a_hung_logout_cannot_keep_the_server_alive(tmp_path):
    """A logout to a vCenter that stopped answering must not outlive the deadline."""
    started = tmp_path / "logout-started"
    code = textwrap.dedent(
        f"""
        import atexit
        import time
        from {MODULE} import server

        def _logout_to_a_vcenter_that_stopped_answering():
            open({str(started)!r}, "w", encoding="utf-8").write("ok")
            time.sleep(600)

        atexit.register(_logout_to_a_vcenter_that_stopped_answering)
        server.main()
        """
    )
    rc, log, seconds = _stop_real_server(tmp_path, code, ("SIGINT", "SIGTERM"), wait_s=30)
    assert started.exists(), f"the logout never started (rc={rc}): {log}"
    assert rc in STOPPED, log
    # The deadline is 5 s; the margin absorbs a slow CI host, not a longer deadline.
    assert seconds < 15, f"exited only after {seconds:.1f}s"
    assert "did not finish within" in log, log
