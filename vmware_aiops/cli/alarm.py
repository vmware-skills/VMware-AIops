"""Alarm management commands: list, acknowledge, reset."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table
from vmware_policy import guarded

from vmware_aiops.cli._common import (
    ConfigOption,
    DryRunOption,
    TargetOption,
    _audit,
    _double_confirm,
    _dry_run_print,
    _get_connection,
    _resolve_target,
    cli_errors,
    console,
)

alarm_app = typer.Typer(help="vCenter alarm management: list, acknowledge, reset.")


@alarm_app.command("list")
@cli_errors
def alarm_list(
    target: TargetOption = None,
    config: ConfigOption = None,
) -> None:
    """List all active/triggered alarms across the vCenter inventory."""
    from vmware_aiops.ops.alarm_mgmt import list_alarms

    si, _ = _get_connection(target, config)
    alarms = list_alarms(si)["items"]
    if not alarms:
        console.print("[green]No active alarms.[/]")
        return
    table = Table(title="Active vCenter Alarms")
    table.add_column("Severity", style="bold")
    table.add_column("Entity")
    table.add_column("Type")
    table.add_column("Alarm Name")
    table.add_column("Acknowledged")
    table.add_column("Time")
    for a in alarms:
        sev = a["severity"]
        sev_style = {"critical": "red", "warning": "yellow", "info": "cyan"}.get(sev, "white")
        ack = "[green]✓[/]" if a.get("acknowledged") else "[dim]-[/]"
        table.add_row(
            f"[{sev_style}]{sev.upper()}[/]",
            a["entity_name"],
            a["entity_type"],
            a["alarm_name"],
            ack,
            a["time"],
        )
    console.print(table)


@alarm_app.command("acknowledge")
@cli_errors
@guarded('acknowledge_vcenter_alarm', risk_level='medium')
def alarm_acknowledge(
    entity_name: Annotated[str, typer.Argument(help="Entity name (VM/host/cluster)")],
    alarm_name: Annotated[str, typer.Argument(help="Alarm definition name")],
    target: TargetOption = None,
    config: ConfigOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Acknowledge a triggered vCenter alarm (marks as seen, does not clear it)."""
    from vmware_aiops.ops.alarm_mgmt import acknowledge_alarm

    if dry_run:
        _dry_run_print(
            target=_resolve_target(target),
            vm_name=entity_name,
            operation="acknowledge_alarm",
            api_call="alarmManager.AcknowledgeAlarm(alarm, entity)",
            parameters={"alarm_name": alarm_name},
            resource_label="Entity",
        )
        return
    si, _ = _get_connection(target, config)
    result = acknowledge_alarm(si, entity_name, alarm_name, _audit, _resolve_target(target))
    console.print(f"[green]✓ Acknowledged alarm '{alarm_name}' on '{entity_name}'[/]")
    _audit.log(
        target=_resolve_target(target),
        operation="acknowledge_alarm",
        resource=f"alarm/{entity_name}/{alarm_name}",
        result=str(result),
    )


@alarm_app.command("reset")
@cli_errors
@guarded('reset_vcenter_alarm', risk_level='medium')
def alarm_reset(
    entity_name: Annotated[str, typer.Argument(help="Entity name (VM/host/cluster)")],
    alarm_name: Annotated[str, typer.Argument(help="Alarm definition name")],
    target: TargetOption = None,
    config: ConfigOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Clear triggered alarms back to normal state (removes from active list).

    Note: vSphere has no per-alarm clear — this clears all triggered alarms
    matching the named alarm's entity type and current status.
    """
    from vmware_aiops.ops.alarm_mgmt import reset_alarm

    if dry_run:
        _dry_run_print(
            target=_resolve_target(target),
            vm_name=entity_name,
            operation="reset_alarm",
            api_call="alarmManager.ClearTriggeredAlarms(filter=AlarmFilterSpec(status, typeEntity))",
            parameters={"alarm_name": alarm_name},
            resource_label="Entity",
        )
        return
    # Clearing affects ALL triggered alarms matching entity type + status —
    # broader blast radius than a single alarm, so require double confirm.
    _double_confirm("清除告警", f"{entity_name}/{alarm_name}", _resolve_target(target), resource_type="Alarm")
    si, _ = _get_connection(target, config)
    result = reset_alarm(si, entity_name, alarm_name, _audit, _resolve_target(target))
    console.print(f"[green]✓ Cleared triggered alarm '{alarm_name}' on '{entity_name}'[/]")
    if result.get("scope"):
        console.print(f"[dim]Scope: {result['scope']}[/]")
    _audit.log(
        target=_resolve_target(target),
        operation="reset_alarm",
        resource=f"alarm/{entity_name}/{alarm_name}",
        result=str(result),
    )
