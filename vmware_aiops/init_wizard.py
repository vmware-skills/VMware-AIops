"""Interactive first-run setup wizard — ``vmware-aiops init``.

Replaces the hand-rolled "mkdir + cp config.example.yaml + edit YAML + remember
chmod 600" dance with guided prompts. Writes config.yaml + .env, sets the
correct per-target password env-var name, obfuscates the password to grep-safe
``b64:`` form immediately (never left plaintext on disk), locks .env to 0600,
and offers to verify the connection.

Only touches local config files — no vSphere mutation.
"""

from __future__ import annotations

import os
from typing import Any

import typer
import yaml
from rich.console import Console

from vmware_aiops.config import (
    CONFIG_DIR,
    CONFIG_FILE,
    ENV_FILE,
    _autoencode_env_file,
)

console = Console()


def _env_key_for(name: str) -> str:
    """The password env-var name the loader expects for a target."""
    return f"VMWARE_{name.upper().replace('-', '_')}_PASSWORD"


def _prompt_target() -> dict[str, Any]:
    """Collect one target's fields interactively."""
    name = typer.prompt("Target name (short id, e.g. prod-vcenter)", default="prod-vcenter")
    host = typer.prompt("vCenter/ESXi host (FQDN or IP)")
    ttype = typer.prompt("Type", default="vcenter")
    while ttype not in ("vcenter", "esxi"):
        console.print("[yellow]Enter 'vcenter' or 'esxi'.[/]")
        ttype = typer.prompt("Type", default="vcenter")
    default_user = "administrator@vsphere.local" if ttype == "vcenter" else "root"
    username = typer.prompt("Username", default=default_user)
    port = typer.prompt("Port", default=443, type=int)
    verify_ssl = typer.confirm(
        "Verify the TLS certificate? (answer No for self-signed lab certs)", default=True
    )
    return {
        "name": name,
        "host": host,
        "type": ttype,
        "username": username,
        "port": port,
        "verify_ssl": verify_ssl,
    }


def _write_env(name: str, password: str) -> str:
    """Write the password to .env (grep-safe b64), 0600, and the live env."""
    from dotenv import set_key

    env_key = _env_key_for(name)
    ENV_FILE.touch(mode=0o600, exist_ok=True)
    os.chmod(ENV_FILE, 0o600)
    set_key(str(ENV_FILE), env_key, password, quote_mode="never")
    # Obfuscate to b64: immediately so the secret is never left plaintext on
    # disk, even before the next load (honours the .env-no-plaintext rule).
    _autoencode_env_file(ENV_FILE)
    os.chmod(ENV_FILE, 0o600)
    # Make it visible to an in-process connection test this session.
    os.environ[env_key] = password
    return env_key


def run_init(force: bool = False, skip_test: bool = False) -> int:
    """Run the interactive setup wizard. Returns a process exit code."""
    console.print("[bold cyan]vmware-aiops init[/] — guided setup\n")

    if CONFIG_FILE.exists() and not force:
        console.print(f"[yellow]Config already exists:[/] {CONFIG_FILE}")
        if not typer.confirm("Overwrite it?", default=False):
            console.print("Kept existing config. Edit it by hand or re-run with --force.")
            return 0

    target = _prompt_target()
    password = typer.prompt("Password", hide_input=True)

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config = {
        "targets": [target],
        "scanner": {"enabled": True, "interval_minutes": 15, "severity_threshold": "warning"},
        "notify": {"webhook_url": ""},
    }
    CONFIG_FILE.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    env_key = _write_env(target["name"], password)

    console.print()
    console.print(f"[green]✓[/] Wrote {CONFIG_FILE}")
    console.print(f"[green]✓[/] Wrote {ENV_FILE} (0600, password stored grep-safe as {env_key})")
    if not target["verify_ssl"]:
        console.print("[yellow]ℹ TLS verification disabled — only safe for self-signed labs.[/]")

    if skip_test:
        console.print("\nNext: [cyan]vmware-aiops doctor[/] to verify the connection.")
        return 0

    if not typer.confirm("\nTest the connection now?", default=True):
        console.print("Next: [cyan]vmware-aiops doctor[/] to verify the connection.")
        return 0

    from vmware_aiops.doctor import run_doctor

    console.print()
    return run_doctor()
