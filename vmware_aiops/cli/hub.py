"""Hub commands: VMware skill family management."""

from __future__ import annotations

import typer
from rich.table import Table

from vmware_aiops.cli._common import cli_errors, console
from vmware_policy import cli_local

hub_app = typer.Typer(help="VMware skill family management.")


@hub_app.command("status")
@cli_errors
@cli_local("lists which family skills are installed on this machine")
def hub_status() -> None:
    """Show installed VMware skill family members and available modules."""
    import shutil

    FAMILY: list[tuple[str, str, str]] = [
        ("vmware-aiops", "vmware-aiops", "VM lifecycle, deploy, guest ops, cluster"),
        ("vmware-monitor", "vmware-monitor", "Read-only inventory, alarms, events"),
        ("vmware-storage", "vmware-storage", "iSCSI, vSAN, datastore management"),
        ("vmware-vks", "vmware-vks", "Tanzu Kubernetes (vSphere 8.x+)"),
        ("vmware-nsx", "vmware-nsx-mgmt", "NSX networking: segments, gateways, NAT"),
        ("vmware-nsx-security", "vmware-nsx-security", "DFW microsegmentation, security groups"),
        ("vmware-aria", "vmware-aria", "Aria Ops metrics, alerts, capacity"),
    ]

    table = Table(title="VMware Skill Family", show_header=True, header_style="bold")
    table.add_column("Skill", style="cyan", min_width=22)
    table.add_column("Status", min_width=12)
    table.add_column("Capabilities")
    table.add_column("Install", style="dim")

    for skill, package, desc in FAMILY:
        installed = shutil.which(skill) is not None
        status = "[green]✓ installed[/green]" if installed else "[dim]─ not installed[/dim]"
        install_cmd = "" if installed else f"uv tool install {package}"
        table.add_row(skill, status, desc, install_cmd)

    console.print(table)
    console.print()

    installed_count = sum(1 for skill, _, _ in FAMILY if shutil.which(skill))
    console.print(f"[bold]{installed_count}/{len(FAMILY)}[/bold] family members installed.")
    if installed_count < len(FAMILY):
        console.print("[dim]Run the install commands above to add more capabilities.[/dim]")
