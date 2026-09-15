"""Top-level Typer app: assembles all sub-apps and top-level commands."""

from __future__ import annotations

from typing import Annotated

import typer

from vmware_aiops.cli._common import cli_errors
from vmware_aiops.cli.alarm import alarm_app
from vmware_aiops.cli.cluster import cluster_app
from vmware_aiops.cli.deploy import datastore_app, deploy_app
from vmware_aiops.cli.doctor import doctor_cmd
from vmware_aiops.cli.hub import hub_app
from vmware_aiops.cli.investigate import attention_cmd, investigate_app
from vmware_aiops.cli.mcp_config import mcp_config_app
from vmware_aiops.cli.plan import plan_app
from vmware_aiops.cli.scan import daemon_app, scan_app
from vmware_aiops.cli.summary import cluster_summary_cmd
from vmware_aiops.cli.vm import vm_app
from vmware_policy import audited, cli_local

app = typer.Typer(
    name="vmware-aiops",
    help="VMware vCenter/ESXi AI-powered monitoring and operations.",
    no_args_is_help=True,
)

# Register sub-apps
app.add_typer(vm_app, name="vm")
app.add_typer(deploy_app, name="deploy")
app.add_typer(datastore_app, name="datastore")
app.add_typer(cluster_app, name="cluster")
app.add_typer(scan_app, name="scan")
app.add_typer(daemon_app, name="daemon")
app.add_typer(plan_app, name="plan")
app.add_typer(mcp_config_app, name="mcp-config")
app.add_typer(alarm_app, name="alarm")
app.add_typer(hub_app, name="hub")

# Register top-level commands
app.command("summary")(cluster_summary_cmd)
app.command("attention")(attention_cmd)
app.add_typer(investigate_app, name="investigate")
app.command("doctor")(doctor_cmd)


@app.command("init")
@audited("init")
def init_cmd(
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite an existing config without asking")
    ] = False,
    skip_test: Annotated[
        bool, typer.Option("--skip-test", help="Don't run a connection test after writing config")
    ] = False,
) -> None:
    """Interactive first-run setup: write config.yaml + .env, then verify."""
    from vmware_aiops.init_wizard import run_init

    raise typer.Exit(run_init(force=force, skip_test=skip_test))


@app.command("mcp")
@cli_errors
@cli_local("starts the MCP server; its tools audit themselves")
def mcp_cmd() -> None:
    """Start the MCP server (stdio transport).

    Single-command entry point for MCP clients (Claude Desktop, Cursor, etc.):
        vmware-aiops mcp

    Equivalent to the legacy `vmware-aiops-mcp` console script.
    """
    import sys

    if sys.version_info < (3, 10):
        typer.echo(
            f"ERROR: vmware-aiops MCP server requires Python >= 3.10 "
            f"(got {sys.version_info.major}.{sys.version_info.minor}).\n"
            f"Interpreter: {sys.executable}\n"
            f"Fix: uv python install 3.12 && "
            f"uv tool install --python 3.12 --force vmware-aiops",
            err=True,
        )
        raise typer.Exit(2)

    from vmware_aiops.mcp_server.server import main as _mcp_main

    _mcp_main()


if __name__ == "__main__":
    app()
