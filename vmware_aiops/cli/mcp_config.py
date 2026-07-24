"""MCP config generator commands: generate, list, install."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from vmware_aiops.cli._common import cli_errors, console

mcp_config_app = typer.Typer(help="Generate MCP server config for local AI agents.")

_AGENT_TEMPLATES = {
    "goose": "goose.json",
    "cursor": "cursor.json",
    "claude-code": "claude-code.json",
    "continue": "continue.yaml",
    "vscode-copilot": "vscode-copilot.json",
    "localcowork": "localcowork.json",
    "mcp-agent": "mcp-agent.yaml",
}

_TEMPLATES_DIR = Path(__file__).parent.parent.parent / "examples" / "mcp-configs"

# Default install destinations for each agent
_AGENT_INSTALL_PATHS: dict[str, Path] = {
    "claude-code": Path.home() / ".claude" / "settings.json",
    "cursor": Path.home() / ".cursor" / "mcp.json",
    "goose": Path.home() / ".config" / "goose" / "config.yaml",
    "vscode-copilot": Path(".vscode") / "mcp.json",
    "continue": Path.home() / ".continue" / "config.json",
    "localcowork": Path.home() / ".localcowork" / "mcp.json",
    "mcp-agent": Path("mcp_agent.config.yaml"),
}


@mcp_config_app.command("generate")
@cli_errors
def mcp_config_generate(
    agent: Annotated[
        str,
        typer.Option(
            "--agent",
            "-a",
            help=(
                "Target agent: goose, cursor, claude-code, continue, "
                "vscode-copilot, localcowork, mcp-agent"
            ),
        ),
    ],
    install_path: Annotated[
        str | None,
        typer.Option("--path", help="Absolute path to VMware-AIops install dir"),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Write config to this file path"),
    ] = None,
) -> None:
    """Generate MCP server config for a local AI agent.

    Prints the ready-to-use config to stdout (or writes to --output file).
    Replace /path/to/VMware-AIops with your actual installation directory.

    Example:
        vmware-aiops mcp-config generate --agent goose
    """
    agent_lower = agent.lower()
    if agent_lower not in _AGENT_TEMPLATES:
        available = ", ".join(sorted(_AGENT_TEMPLATES.keys()))
        console.print(f"[red]Unknown agent '{agent}'. Available: {available}[/]")
        raise typer.Exit(1)

    template_file = _TEMPLATES_DIR / _AGENT_TEMPLATES[agent_lower]
    if not template_file.exists():
        console.print(f"[red]Template file not found: {template_file}[/]")
        raise typer.Exit(1)

    content = template_file.read_text(encoding="utf-8")

    # Replace placeholder with actual path if provided
    if install_path:
        abs_path = str(Path(install_path).resolve())
        content = content.replace("/path/to/VMware-AIops", abs_path)
    else:
        # Try to resolve from package location
        pkg_dir = Path(__file__).parent.parent.parent.resolve()
        # Only substitute if it looks like a real install (has pyproject.toml)
        if (pkg_dir / "pyproject.toml").exists():
            content = content.replace("/path/to/VMware-AIops", str(pkg_dir))

    if output:
        output.write_text(content, encoding="utf-8")
        console.print(f"[green]Config written to: {output}[/]")
    else:
        console.print(content)


@mcp_config_app.command("list")
@cli_errors
def mcp_config_list() -> None:
    """List all supported agents."""
    table = Table(title="Supported Agents")
    table.add_column("Agent", style="cyan")
    table.add_column("Template File")
    for agent_name, template in sorted(_AGENT_TEMPLATES.items()):
        table.add_row(agent_name, template)
    console.print(table)


@mcp_config_app.command("install")
@cli_errors
def mcp_config_install(
    agent: Annotated[
        str,
        typer.Option(
            "--agent", "-a",
            help="Target agent: goose, cursor, claude-code, continue, "
                 "vscode-copilot, localcowork, mcp-agent",
        ),
    ],
    install_path: Annotated[
        str | None,
        typer.Option("--path", help="Absolute path to VMware-AIops install dir"),
    ] = None,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip confirmation prompt"),
    ] = False,
) -> None:
    """Install MCP config directly into a local AI agent's config file.

    Writes the vmware-aiops MCP server entry into the agent's config file.
    For agents with JSON configs, merges into the mcpServers section.
    Creates the config file if it doesn't exist.

    Example:
        vmware-aiops mcp-config install --agent cursor
        vmware-aiops mcp-config install --agent claude-code --yes
    """
    agent_lower = agent.lower()
    if agent_lower not in _AGENT_TEMPLATES:
        available = ", ".join(sorted(_AGENT_TEMPLATES.keys()))
        console.print(f"[red]Unknown agent '{agent}'. Available: {available}[/]")
        raise typer.Exit(1)

    # Get the generated config content
    template_file = _TEMPLATES_DIR / _AGENT_TEMPLATES[agent_lower]
    if not template_file.exists():
        console.print(f"[red]Template file not found: {template_file}[/]")
        raise typer.Exit(1)

    content = template_file.read_text(encoding="utf-8")
    if install_path:
        abs_path = str(Path(install_path).resolve())
        content = content.replace("/path/to/VMware-AIops", abs_path)
    else:
        pkg_dir = Path(__file__).parent.parent.parent.resolve()
        if (pkg_dir / "pyproject.toml").exists():
            content = content.replace("/path/to/VMware-AIops", str(pkg_dir))

    dest = _AGENT_INSTALL_PATHS.get(agent_lower)
    if dest is None:
        console.print(
            f"[yellow]No default install path for '{agent_lower}'. "
            f"Use 'generate' and install manually.[/]"
        )
        raise typer.Exit(1)

    console.print(f"[bold]Agent:[/] {agent_lower}")
    console.print(f"[bold]Install path:[/] {dest}")

    if not yes:
        confirmed = typer.confirm("Write config to this path?")
        if not confirmed:
            console.print("[yellow]Cancelled.[/]")
            raise typer.Exit(0)

    dest.parent.mkdir(parents=True, exist_ok=True)

    # For JSON configs: merge mcpServers entry if file exists
    if dest.suffix == ".json" and dest.exists():
        try:
            existing = json.loads(dest.read_text(encoding="utf-8"))
            new_entry = json.loads(content)
            # Merge: support both {mcpServers: {...}} and flat formats
            if "mcpServers" in new_entry:
                existing.setdefault("mcpServers", {}).update(new_entry["mcpServers"])
            else:
                existing.update(new_entry)
            dest.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
            console.print(f"[green]✓ Merged vmware-aiops into: {dest}[/]")
        except json.JSONDecodeError as e:
            console.print(f"[red]Existing config is not valid JSON: {e}[/]")
            console.print("[yellow]Writing new config (backup original first).[/]")
            dest.with_suffix(".bak").write_text(dest.read_text(encoding="utf-8"), encoding="utf-8")
            dest.write_text(content, encoding="utf-8")
            console.print(f"[green]✓ Written: {dest} (backup: {dest.with_suffix('.bak')})[/]")
        except OSError as e:
            console.print(f"[red]Failed to read/write config: {e}[/]")
            raise typer.Exit(1) from e
    else:
        dest.write_text(content, encoding="utf-8")
        console.print(f"[green]✓ Written: {dest}[/]")

    console.print("\n[dim]Run 'vmware-aiops doctor' to verify your setup.[/]")
