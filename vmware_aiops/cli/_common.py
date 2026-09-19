"""Shared helpers for all CLI sub-modules."""

from __future__ import annotations

import functools
import ssl
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from vmware_policy import PolicyDenied

from vmware_aiops.notify.audit import AuditLogger

_audit = AuditLogger()
console = Console()

# ─── Shared Option types ──────────────────────────────────────────────────────

TargetOption = Annotated[str | None, typer.Option("--target", "-t", help="Target name from config")]
ConfigOption = Annotated[Path | None, typer.Option("--config", "-c", help="Config file path")]
DryRunOption = Annotated[bool, typer.Option("--dry-run", help="Print API calls without executing")]


# ─── Shared helpers ───────────────────────────────────────────────────────────


def _cli_error_types() -> tuple[type[BaseException], ...]:
    """Exception types translated to a one-line teaching error (踩坑 #37).

    Domain exceptions carry teaching messages already; infra exceptions
    (missing config, missing password env, unreachable vCenter, timeouts)
    are common user mistakes that must not surface as raw tracebacks.
    OSError covers FileNotFoundError / ConnectionError / TimeoutError.
    """
    from pyVmomi import vim

    from vmware_aiops.ops.cluster_mgmt import ClusterError, ClusterNotFoundError
    from vmware_aiops.ops.guest_ops import GuestOpsError
    from vmware_aiops.ops.gate import GateRefusedError
    from vmware_aiops.ops.inventory import AmbiguousVMError
    from vmware_aiops.ops.vm_lifecycle import (
        TaskFailedError,
        TaskStillRunning,
        VMNotFoundError,
    )

    return (
        VMNotFoundError,
        AmbiguousVMError,
        GateRefusedError,
        GuestOpsError,
        TaskFailedError,
        TaskStillRunning,
        ClusterNotFoundError,
        ClusterError,
        KeyError,
        OSError,
        vim.fault.InvalidLogin,
    )


def cli_errors(fn):
    """Decorator: translate known exceptions into one red line + exit code 1.

    Applied to CLI command functions so bad VM names, missing config/password
    env vars, and unreachable vCenters print a single teaching message instead
    of a Python traceback. typer.Exit / typer.Abort pass through untouched.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (typer.Exit, typer.Abort):
            raise
        except PolicyDenied as e:
            # A deny rule or maintenance window refused this write — @guarded ran
            # guard() before the body and already wrote the status="denied" audit
            # row. Teach the operator which rule fired instead of a traceback.
            rule = f" [dim](rule: {e.result.rule})[/]" if e.result.rule else ""
            console.print(f"[red]Denied by policy: {e.result.reason}[/]{rule}")
            raise typer.Exit(1) from e
        except _cli_error_types() as e:
            from pyVmomi import vim

            message = getattr(e, "msg", None) or str(e)
            if isinstance(e, KeyError):
                message = f"Missing required key or environment variable: {message}"
            elif isinstance(e, vim.fault.InvalidLogin):
                # The SDK message ("incorrect user name or password") doesn't
                # say WHERE to fix it — point at the exact file + env var. Use a
                # clean base so an unset SDK msg doesn't leak the raw fault repr.
                message = (
                    "Login failed (incorrect username or password). → Check the "
                    "password in ~/.vmware-aiops/.env (env var "
                    "VMWARE_<TARGET>_PASSWORD) and the username in "
                    "~/.vmware-aiops/config.yaml. Re-run 'vmware-aiops init' to reset both."
                )
            elif isinstance(e, ssl.SSLError):
                # Self-signed lab certs are the usual cause; teach the toggle.
                message = (
                    f"TLS certificate verification failed: {message} → For a "
                    "self-signed lab, set verify_ssl: false on the target in "
                    "~/.vmware-aiops/config.yaml (or re-run 'vmware-aiops init')."
                )
            console.print(f"[red]Error: {message}[/]")
            raise typer.Exit(1) from e

    return wrapper


def _dry_run_print(
    *,
    target: str,
    vm_name: str,
    operation: str,
    api_call: str,
    parameters: dict | None = None,
    before_state: dict | None = None,
    expected_after: dict | None = None,
    resource_label: str = "VM",
) -> None:
    """Print a dry-run preview of the API call that would be made."""
    console.print("\n[bold magenta][DRY-RUN] No changes will be made.[/]")
    console.print(f"[magenta]  Target:    {target}[/]")
    console.print(f"[magenta]  {resource_label}:        {vm_name}[/]")
    console.print(f"[magenta]  Operation: {operation}[/]")
    console.print(f"[magenta]  API Call:  {api_call}[/]")
    if parameters:
        for k, v in parameters.items():
            console.print(f"[magenta]  Param:     {k} = {v}[/]")
    if before_state:
        console.print(f"[magenta]  Current:   {before_state}[/]")
    if expected_after:
        console.print(f"[magenta]  Expected:  {expected_after}[/]")
    console.print("[magenta]  Run without --dry-run to execute.[/]\n")
    _audit.log(
        target=target,
        operation=operation,
        resource=vm_name,
        parameters={"dry_run": True, **(parameters or {})},
        before_state=before_state or {},
        result="dry-run",
    )


def _get_connection(target: str | None, config_path: Path | None = None):
    """Helper to get a pyVmomi connection."""
    from vmware_aiops.config import load_config
    from vmware_aiops.connection import ConnectionManager

    cfg = load_config(config_path)
    mgr = ConnectionManager(cfg)
    return mgr.connect(target), cfg


def _get_all_connections(config_path: Path | None = None):
    """Connect to every configured target, tolerating per-target failures.

    Returns ``(sessions, unreachable)`` — see ``ConnectionManager.connect_all`` —
    for the cross-vCenter attention view. One dead vCenter never fails the command.
    """
    from vmware_aiops.config import load_config
    from vmware_aiops.connection import ConnectionManager

    cfg = load_config(config_path)
    return ConnectionManager(cfg).connect_all()


def _resolve_target(target: str | None) -> str:
    """Return a display name for the target (used in audit logs)."""
    return target or "default"


def _show_state_preview(info: dict, action: str, vm_name: str) -> None:
    """Display current VM state before a destructive operation."""
    console.print(f"\n[bold cyan]📋 Current state of VM '{vm_name}':[/]")
    state_keys = (
        "power_state",
        "cpu",
        "memory_mb",
        "guest_os",
        "host",
        "ip_address",
        "snapshot_count",
    )
    for key in state_keys:
        if key in info:
            console.print(f"  [cyan]{key}:[/] {info[key]}")
    console.print()


def _validate_vm_params(
    *,
    name: str | None = None,
    cpu: int | None = None,
    memory_mb: int | None = None,
    disk_gb: int | None = None,
) -> None:
    """Validate VM parameter ranges. Raises typer.BadParameter on invalid input."""
    if name is not None:
        if not name or len(name) > 80:
            raise typer.BadParameter(
                f"VM name must be 1-80 characters, got {len(name or '')}. "
                f"Pass a shorter NAME argument and re-run the vmware-aiops command."
            )
        if name.startswith("-") or name.startswith("."):
            raise typer.BadParameter(
                f"VM name '{name}' must not start with '-' or '.'. "
                f"Rename it to start with a letter or digit, then re-run the "
                f"vmware-aiops command."
            )
    if cpu is not None and not (1 <= cpu <= 128):
        raise typer.BadParameter(f"CPU count must be 1-128, got {cpu}. Pass --cpu in that range.")
    if memory_mb is not None and not (128 <= memory_mb <= 1_048_576):
        raise typer.BadParameter(
            f"Memory must be 128-1048576 MB, got {memory_mb}. "
            f"Pass --memory in that range (MB, not GB)."
        )
    if disk_gb is not None and not (1 <= disk_gb <= 65_536):
        raise typer.BadParameter(
            f"Disk size must be 1-65536 GB, got {disk_gb}. Pass --disk in that range (GB)."
        )


def _double_confirm(
    action: str,
    vm_name: str,
    target: str = "default",
    resource_type: str = "VM",
) -> None:
    """Require two confirmations for destructive operations.

    Logs a 'rejected' audit entry if the user declines at either step.
    """
    console.print(f"[bold yellow]⚠️  即将执行: {action} {resource_type} '{vm_name}'[/]")
    try:
        typer.confirm(f"第 1 次确认: 确定要{action} '{vm_name}'?", abort=True)
        typer.confirm(f"第 2 次确认: 再次确认{action} '{vm_name}'，此操作不可撤销?", abort=True)
    except typer.Abort:
        _audit.log(
            target=target,
            operation=action,
            resource=vm_name,
            result="rejected",
        )
        raise
