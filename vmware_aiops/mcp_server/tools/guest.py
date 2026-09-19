"""Guest Operations tools: exec, exec-with-output, upload, download, provision."""

from typing import Optional

from vmware_policy import vmware_tool

from vmware_aiops.mcp_server._shared import _get_connection, mcp, tool_errors
from vmware_aiops.ops.gate import preview, refuse_on
from vmware_aiops.ops.guest_gate import (
    measure_guest_exec,
    measure_guest_exec_output,
    measure_guest_provision,
    measure_guest_upload,
)
from vmware_aiops.ops.guest_ops import (
    guest_download,
    guest_exec,
    guest_exec_with_output,
    guest_provision,
    guest_upload,
)

# The four tools below hand caller-supplied content to the guest OS — a program
# to run, or a file to place at a chosen path — and every one of them advertised
# `destructiveHint: false`. The command string is unbounded and runs with the
# credentials passed in, which the example here makes root; `rm -rf /` is a
# well-formed argument. `destructiveHint` is the field a client consults before
# deciding whether to ask its user, so the label was disarming the one prompt
# that mattered most. `vm_guest_download` below keeps `false`: it reads the
# guest and writes the *caller's* disk, which is a different hazard with its own
# guard, and a label applied to everything tells a client nothing.
#
# The same four sit behind the HLD §7 gate: a bare call returns the blast
# radius (which VM, as whom, what would run or be written) and does nothing;
# confirm=True re-measures, refuses on a blocker or an unreadable field, and
# only then calls the same ops function the CLI calls.

_HINT = (
    "Nothing was run or written in the guest. Show blast_radius to the user; "
    "re-run with confirm=True only after they agree."
)
@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="critical", sensitive_params=['password'])
@tool_errors("dict")
def vm_guest_exec(
    vm_name: str,
    command: str,
    username: str,
    arguments: str = "",
    password: str = "",
    working_directory: Optional[str] = None,
    confirm: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE] Execute a command inside a VM via VMware Tools.

    Requires VMware Tools running in the guest OS. Returns exit_code, stdout,
    stderr, timed_out, and blast_radius.

    Without confirm=True this only previews: blast_radius names the VM (name,
    instance UUID), the guest account, the exact command, arguments and working
    directory, VMware Tools status and any blockers; nothing runs. Show it to
    the user. Do not set confirm=True on your own because the user asked
    earlier: they have not seen the preview yet. Refused outright: VM not
    powered on, VMware Tools not running, an identity or status that cannot be
    read, or a name that matches more than one VM.

    Note: the Guest Ops API does not capture stdout/stderr directly, so use this
    only for fire-and-forget commands — prefer vm_guest_exec_output whenever you
    need the output.

    Args:
        vm_name: Target VM name.
        command: Full path to program (e.g. "/bin/bash", "C:\\Windows\\System32\\cmd.exe").
        arguments: Command arguments (e.g. "-c 'whoami'").
        username: Guest OS account to run as. Required — there is no default, so a
            call can never act as root without choosing root.
        password: Guest OS password.
        working_directory: Working directory inside guest (optional).
        confirm: False (default) returns the blast radius and changes nothing. True applies it.
        target: Optional vCenter/ESXi target name from config.
    """
    si = _get_connection(target)
    radius = measure_guest_exec(
        si, vm_name, command, username,
        arguments=arguments, working_directory=working_directory,
    )
    if not confirm:
        return preview(radius, _HINT)
    refuse_on(radius, "vm_guest_exec")
    result = guest_exec(
        si, vm_name, command, username, password,
        arguments=arguments,
        working_directory=working_directory,
    )
    return {**result, "action": "executed", "blast_radius": radius}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="critical", sensitive_params=['password'])
@tool_errors("dict")
def vm_guest_exec_output(
    vm_name: str,
    command: str,
    username: str,
    password: str = "",
    timeout: int = 300,
    confirm: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE] Execute a shell command inside a VM and capture stdout + stderr.

    Automatically detects guest OS (Linux/Windows) and selects the correct
    shell. Output is captured by redirecting to a temp file, downloading it,
    then cleaning up — no manual redirection needed. Prefer this over
    vm_guest_exec whenever you need the output. Requires VMware Tools running
    and a writable temp directory in the guest.

    Returns exit_code, stdout, stderr, timed_out, os_family, and blast_radius.

    Without confirm=True this only previews: blast_radius names the VM (name,
    instance UUID), the guest account, the exact command and the shell it runs
    through, VMware Tools status and any blockers; nothing runs. Show it to the
    user. Do not set confirm=True on your own because the user asked earlier:
    they have not seen the preview yet. Refused outright: VM not powered on,
    VMware Tools not running, an unreadable identity or status, or a name that
    matches more than one VM.

    Args:
        vm_name: Target VM name.
        command: Shell command (e.g. "df -h", "ls /etc", "ipconfig").
        username: Guest OS account to run as. Required — there is no default, so a
            call can never act as root without choosing root.
        password: Guest OS password.
        timeout: Max wait seconds (default 300).
        confirm: False (default) returns the blast radius and changes nothing. True applies it.
        target: Optional vCenter/ESXi target name from config.
    """
    si = _get_connection(target)
    radius = measure_guest_exec_output(si, vm_name, command, username, timeout)
    if not confirm:
        return preview(radius, _HINT)
    refuse_on(radius, "vm_guest_exec_output")
    result = guest_exec_with_output(si, vm_name, command, username, password, timeout=timeout)
    return {**result, "action": "executed", "blast_radius": radius}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="high", sensitive_params=['password'])
@tool_errors("dict")
def vm_guest_upload(
    vm_name: str,
    local_path: str,
    guest_path: str,
    username: str,
    password: str = "",
    confirm: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE] Upload a file from local machine to a VM via VMware Tools.

    Returns a dict with action, message and blast_radius (a status string
    before the confirmation gate). Requires VMware Tools running in the guest
    OS. An existing file at guest_path is replaced. Use vm_guest_download for
    the reverse direction, and vm_guest_provision instead when uploads and
    commands belong to one ordered provisioning run.

    Without confirm=True this only previews: blast_radius names the VM (name,
    instance UUID), the guest account, the local path and size, the guest path,
    VMware Tools status and any blockers; nothing is transferred. Show it to the
    user. Do not set confirm=True on your own because the user asked earlier:
    they have not seen the preview yet. Refused outright: VM not powered on,
    VMware Tools not running, a local file that is missing or unreadable, an
    unreadable identity or status, or a name that matches more than one VM.

    Args:
        vm_name: Target VM name.
        local_path: Local file path to upload.
        guest_path: Destination path inside the guest.
        username: Guest OS account to run as. Required — there is no default, so a
            call can never act as root without choosing root.
        password: Guest OS password.
        confirm: False (default) returns the blast radius and changes nothing. True applies it.
        target: Optional vCenter/ESXi target name from config.
    """
    si = _get_connection(target)
    radius = measure_guest_upload(si, vm_name, local_path, guest_path, username)
    if not confirm:
        return preview(radius, _HINT)
    refuse_on(radius, "vm_guest_upload")
    message = guest_upload(si, vm_name, local_path, guest_path, username, password)
    return {"action": "uploaded", "message": message, "blast_radius": radius}


# Reading a file out of the VM is a read of the VM; writing it to local_path is
# not, and readOnlyHint is what a client consults before deciding whether to ask
# the user. Annotated exactly like its mirror vm_guest_upload — same operation,
# opposite direction.
@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="high", sensitive_params=['password'])
@tool_errors("str")
def vm_guest_download(
    vm_name: str,
    guest_path: str,
    local_path: str,
    username: str,
    password: str = "",
    overwrite: bool = False,
    target: Optional[str] = None,
) -> str:
    """[WRITE] Download a file from a VM and write it to a local path.

    Reads from the guest, writes the local filesystem — the write is why this is
    not a read tool. Returns a status string. Requires VMware Tools running in
    the guest OS. Use vm_guest_upload for the reverse direction; to capture
    command output use vm_guest_exec_output instead — it redirects and
    downloads for you.

    Refuses a destination that already exists unless overwrite=True, and never
    writes through a symlink or over a directory. Pick a path that does not
    exist yet rather than passing overwrite=True by default.

    Args:
        vm_name: Target VM name.
        guest_path: File path inside the guest to download.
        local_path: Local destination path, including the file name.
        username: Guest OS account to run as. Required — there is no default, so a
            call can never act as root without choosing root.
        password: Guest OS password.
        overwrite: True replaces an existing file at local_path (default False).
        target: Optional vCenter/ESXi target name from config.
    """
    si = _get_connection(target)
    return guest_download(
        si, vm_name, guest_path, local_path, username, password, overwrite=overwrite
    )


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="critical", sensitive_params=['password'])
@tool_errors("dict")
def vm_guest_provision(
    vm_name: str,
    username: str,
    password: str,
    steps: list[dict],
    timeout: int = 300,
    confirm: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE] Provision a VM by running an ordered sequence of guest operations.

    Prefer this over repeated vm_guest_exec / vm_guest_upload calls when the steps
    form one provisioning run. Steps stop on the first failure, so a partial run
    leaves the guest half-configured. Requires VMware Tools running in the guest.

    Without confirm=True this only previews: blast_radius names the VM (name,
    instance UUID), the guest account and every step it would run, in order,
    with counts per type, local file sizes and any blockers; nothing runs. Show
    it to the user. Do not set confirm=True on your own because the user asked
    earlier: they have not seen the preview yet. Refused outright: VM not
    powered on, VMware Tools not running, an empty step list, a step with an
    unknown type or a missing key, an upload whose local file is missing or
    unreadable, a service step on a Windows guest, an unreadable identity or
    status, or a name that matches more than one VM.

    Step types:
      - exec:    {"type": "exec", "command": "apt-get install -y nginx"}
      - upload:  {"type": "upload", "local_path": "...", "guest_path": "..."}
      - service: {"type": "service", "name": "nginx", "action": "start"}

    Args:
        vm_name: Target VM name.
        username: Guest OS username.
        password: Guest OS password.
        steps: Ordered list of step dicts (see Step types).
        timeout: Per-step timeout in seconds (default 300).
        confirm: False (default) returns the blast radius and changes nothing. True applies it.
        target: Optional vCenter/ESXi target name from config.

    Returns:
        dict with success, completed_steps, total_steps, results, error, and
        blast_radius.
    """
    si = _get_connection(target)
    radius = measure_guest_provision(si, vm_name, username, steps, timeout)
    if not confirm:
        return preview(radius, _HINT)
    refuse_on(radius, "vm_guest_provision")
    result = guest_provision(si, vm_name, username, password, steps, timeout=timeout)
    return {**result, "action": "provisioned", "blast_radius": radius}
