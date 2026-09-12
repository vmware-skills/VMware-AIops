"""Guest operations must say which guest account they run as.

Until 2026-09-11 every guest tool defaulted ``username`` to ``root`` — on the
MCP surface (vm_guest_exec, vm_guest_exec_output, vm_guest_upload,
vm_guest_download) and the CLI (guest-exec / guest-upload / guest-download).
An agent that omitted the argument ran a command as root without anyone
having chosen root. ClawHub's review flagged guest exec as the highest-impact
capability of the skill; the maintainer decided the account must be explicit.

The fix is a breaking change on purpose: a call that relied on the default now
fails fast and says so, instead of silently acting as root.
"""

from __future__ import annotations

import asyncio

import pytest
from typer.testing import CliRunner

GUEST_TOOLS = ("vm_guest_exec", "vm_guest_exec_output", "vm_guest_upload", "vm_guest_download")


def _schemas():
    from vmware_aiops.mcp_server.server import mcp

    return {t.name: t.inputSchema for t in asyncio.run(mcp.list_tools())}


@pytest.mark.parametrize("tool", GUEST_TOOLS)
def test_mcp_guest_tool_requires_username(tool):
    schema = _schemas()[tool]
    assert "username" in schema.get("required", []), f"{tool}: username is optional"
    assert "default" not in schema["properties"]["username"], (
        f"{tool}: username still has a default "
        f"({schema['properties']['username'].get('default')!r})"
    )


@pytest.mark.parametrize(
    "argv",
    [
        ["vm", "guest-exec", "web-01", "--cmd", "/bin/ls"],
        ["vm", "guest-upload", "web-01", "--local", "/tmp/a", "--guest", "/tmp/a"],
        ["vm", "guest-download", "web-01", "--guest", "/tmp/a", "--local", "/tmp/b"],
    ],
)
def test_cli_guest_command_requires_user(argv):
    from vmware_aiops.cli import app

    result = CliRunner().invoke(app, argv, input="secret\nsecret\n")
    assert result.exit_code != 0, f"{argv[1]} ran without --user"
    assert "--user" in result.output, result.output
