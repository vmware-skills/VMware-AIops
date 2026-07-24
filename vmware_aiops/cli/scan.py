"""Scan and daemon commands: one-time scan, daemon start/stop/status."""

from __future__ import annotations

import signal

import typer

from vmware_aiops.cli._common import (
    ConfigOption,
    TargetOption,
    _get_connection,
    cli_errors,
    console,
)
from vmware_aiops.config import CONFIG_DIR

scan_app = typer.Typer(help="Log and alarm scanning.")
daemon_app = typer.Typer(help="Scanner daemon management.")


# ─── Scan ─────────────────────────────────────────────────────────────────────


@scan_app.command("now")
@cli_errors
def scan_now(target: TargetOption = None, config: ConfigOption = None) -> None:
    """Run a one-time scan of alarms and events."""
    from vmware_aiops.scanner.alarm_scanner import scan_alarms
    from vmware_aiops.scanner.log_scanner import scan_logs

    si, cfg = _get_connection(target, config)
    console.print("[bold]Running scan...[/]")
    alarm_results = scan_alarms(si)
    log_results = scan_logs(si, cfg.scanner)
    total = len(alarm_results) + len(log_results)
    if total == 0:
        console.print("[green]All clear. No issues found.[/]")
    else:
        console.print(f"[yellow]Found {total} issue(s).[/]")
        for r in alarm_results + log_results:
            sev_style = {"critical": "red", "warning": "yellow"}.get(
                r["severity"], "white"
            )
            console.print(
                f"  [{sev_style}][{r['severity'].upper()}][/] {r['message']}"
            )


# ─── Daemon ───────────────────────────────────────────────────────────────────


@daemon_app.command("start")
@cli_errors
def daemon_start(config: ConfigOption = None) -> None:
    """Start the scanner daemon."""
    from vmware_aiops.scanner.scheduler import start_scheduler

    console.print("[bold]Starting scanner daemon...[/]")
    start_scheduler(config)


@daemon_app.command("status")
@cli_errors
def daemon_status() -> None:
    """Check scanner daemon status."""
    pid_file = CONFIG_DIR / "daemon.pid"
    if pid_file.exists():
        pid = pid_file.read_text(encoding="utf-8").strip()
        console.print(f"[green]Daemon running (PID: {pid})[/]")
    else:
        console.print("[yellow]Daemon not running.[/]")


@daemon_app.command("stop")
@cli_errors
def daemon_stop() -> None:
    """Stop the scanner daemon."""
    import os as _os

    pid_file = CONFIG_DIR / "daemon.pid"
    if not pid_file.exists():
        console.print("[yellow]Daemon not running.[/]")
        return

    pid = int(pid_file.read_text(encoding="utf-8").strip())
    try:
        _os.kill(pid, signal.SIGTERM)
        console.print(f"[green]Daemon (PID: {pid}) stopped.[/]")
    except ProcessLookupError:
        console.print(f"[yellow]Daemon process (PID: {pid}) not found. Cleaning up.[/]")
    except OSError as e:
        console.print(f"[red]Failed to stop daemon: {e}[/]")
        return
    pid_file.unlink(missing_ok=True)
