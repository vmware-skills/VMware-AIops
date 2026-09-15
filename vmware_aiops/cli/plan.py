"""Plan commands: view and manage operation plans."""

from __future__ import annotations

import typer
from rich.table import Table

from vmware_aiops.cli._common import cli_errors, console
from vmware_policy import audited

plan_app = typer.Typer(help="Plan → Apply: view and manage operation plans.")


@plan_app.command("list")
@cli_errors
@audited("vm_list_plans")
def plan_list() -> None:
    """List all pending/failed operation plans."""
    from vmware_aiops.ops.planner import list_plans

    plans = list_plans()["items"]
    if not plans:
        console.print("[dim]No plans found.[/dim]")
        return
    table = Table(title="Operation Plans")
    table.add_column("Plan ID", style="cyan")
    table.add_column("Created", style="dim")
    table.add_column("Status")
    table.add_column("Steps", justify="right")
    table.add_column("VMs Affected")
    for p in plans:
        status_style = "green" if p["status"] == "pending" else "red"
        table.add_row(
            p["plan_id"],
            p["created_at"],
            f"[{status_style}]{p['status']}[/]",
            str(p["total_steps"]),
            ", ".join(p["vms_affected"]),
        )
    console.print(table)
