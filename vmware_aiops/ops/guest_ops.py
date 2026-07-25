"""Guest Operations: execute commands and transfer files inside VMs.

Requires VMware Tools running inside the guest OS.
Uses the GuestOperationsManager API (VIX-like, over SOAP).
"""

from __future__ import annotations

import logging
import os
import shlex
import tempfile
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from pyVmomi import vim

from vmware_policy import sanitize

from vmware_aiops.connection import get_verify_ssl
from vmware_aiops.ops.inventory import find_vm_by_name
from vmware_aiops.ops.vm_lifecycle import VMNotFoundError

if TYPE_CHECKING:
    from pyVmomi.vim import ServiceInstance

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 2  # seconds
_EXEC_TIMEOUT = 300  # seconds
_HTTP_TIMEOUT = 300  # seconds — file-transfer urlopen must never hang the MCP server

# Guest OS family constants (vm.guest.guestFamily)
_FAMILY_WINDOWS = "windowsGuest"
_FAMILY_LINUX = "linuxGuest"


class GuestOpsError(Exception):
    """Raised when a guest operation fails."""


def _require_vm_with_tools(
    si: ServiceInstance, vm_name: str
) -> vim.VirtualMachine:
    """Find VM and verify VMware Tools is running."""
    vm = find_vm_by_name(si, vm_name)
    if vm is None:
        raise VMNotFoundError(
            f"VM '{vm_name}' not found. Run list_virtual_machines (vmware-monitor skill, "
            f"filter by name e.g. '{vm_name[:3]}*') to see available VMs and copy an "
            f"exact name — vSphere VM names are case-sensitive."
        )
    if vm.runtime.powerState != vim.VirtualMachine.PowerState.poweredOn:
        raise GuestOpsError(
            f"VM '{vm_name}' is not powered on. Guest operations need a running guest: "
            f"run vm_power_on (CLI: vmware-aiops vm power-on '{vm_name}'), wait for the "
            f"guest to boot, then retry."
        )
    tools_status = vm.guest.toolsRunningStatus if vm.guest else None
    if tools_status != "guestToolsRunning":
        raise GuestOpsError(
            f"VMware Tools not running on '{vm_name}' (status: {tools_status}). "
            f"Install or start VMware Tools inside the guest, then check the new status "
            f"with vm_info (CLI: vmware-monitor vm info '{vm_name}') before retrying."
        )
    return vm


def _detect_shell(vm: vim.VirtualMachine) -> tuple[str, str]:
    """Detect guest OS shell from guestFamily. Returns (program_path, shell_flag)."""
    family = vm.guest.guestFamily if vm.guest else None
    if family == _FAMILY_WINDOWS:
        return ("C:\\Windows\\System32\\cmd.exe", "/c")
    return ("/bin/sh", "-c")


def _guest_auth(username: str, password: str) -> vim.vm.guest.NamePasswordAuthentication:
    """Build guest authentication spec."""
    auth = vim.vm.guest.NamePasswordAuthentication()
    auth.username = username
    auth.password = password
    auth.interactiveSession = False
    return auth


def guest_exec(
    si: ServiceInstance,
    vm_name: str,
    command: str,
    username: str,
    password: str,
    arguments: str = "",
    working_directory: str | None = None,
    timeout: int = _EXEC_TIMEOUT,
) -> dict:
    """Execute a command inside a VM via VMware Tools.

    Args:
        si: vSphere ServiceInstance.
        vm_name: Target VM name.
        command: Full path to the program (e.g. "/bin/bash", "C:\\Windows\\System32\\cmd.exe").
        username: Guest OS username.
        password: Guest OS password.
        arguments: Command arguments (e.g. "-c 'ls -la /tmp'").
        working_directory: Working directory inside the guest (optional).
        timeout: Max wait time in seconds (default 300).

    Returns:
        dict with keys: exit_code, stdout, stderr, timed_out.
    """
    vm = _require_vm_with_tools(si, vm_name)
    content = si.RetrieveContent()
    gom = content.guestOperationsManager
    pm = gom.processManager

    auth = _guest_auth(username, password)

    # Build process spec
    spec = vim.vm.guest.ProcessManager.ProgramSpec()
    spec.programPath = command
    spec.arguments = arguments
    if working_directory:
        spec.workingDirectory = working_directory

    # Start the process
    pid = pm.StartProgramInGuest(vm, auth, spec)
    logger.info("Started process PID %d in VM '%s': %s %s", pid, vm_name, command, arguments)

    # Poll for completion
    start = time.time()
    timed_out = False
    while True:
        elapsed = time.time() - start
        if elapsed > timeout:
            timed_out = True
            break

        processes = pm.ListProcessesInGuest(vm, auth, pids=[pid])
        if not processes:
            break
        proc = processes[0]
        if proc.exitCode is not None:
            # Process finished
            return {
                "exit_code": proc.exitCode,
                "stdout": "",
                "stderr": "",
                "timed_out": False,
                "command": f"{command} {arguments}".strip(),
                "pid": pid,
            }
        time.sleep(_POLL_INTERVAL)

    if timed_out:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Process timed out after {timeout}s",
            "timed_out": True,
            "command": f"{command} {arguments}".strip(),
            "pid": pid,
        }

    return {
        "exit_code": -1,
        "stdout": "",
        "stderr": "Process disappeared unexpectedly",
        "timed_out": False,
        "command": f"{command} {arguments}".strip(),
        "pid": pid,
    }


def guest_exec_with_output(
    si: ServiceInstance,
    vm_name: str,
    command: str,
    username: str,
    password: str,
    timeout: int = _EXEC_TIMEOUT,
) -> dict:
    """Execute a shell command inside a VM and capture stdout + stderr.

    Automatically detects guest OS (Linux/Windows) and uses the appropriate
    shell. Captures output by redirecting to a temp file, downloading it,
    then cleaning up.

    Args:
        si: vSphere ServiceInstance.
        vm_name: Target VM name.
        command: Shell command to run (e.g. "df -h" or "dir C:\\").
        username: Guest OS username.
        password: Guest OS password.
        timeout: Max wait time in seconds (default 300).

    Returns:
        dict with keys: exit_code, stdout, stderr, timed_out, command, os_family.
    """
    vm = _require_vm_with_tools(si, vm_name)
    family = vm.guest.guestFamily if vm.guest else None
    program, flag = _detect_shell(vm)

    # Temp file paths inside the guest
    run_id = uuid.uuid4().hex[:8]
    if family == _FAMILY_WINDOWS:
        tmp_out = f"C:\\Windows\\Temp\\vmops_{run_id}.txt"
        # Windows cmd.exe: redirect target doesn't need POSIX quoting.
        # The entire /c argument is passed as a single double-quoted string.
        wrapped = f"{command} > {tmp_out} 2>&1"
        shell_arg = f"{flag} \"{wrapped}\""
    else:
        tmp_out = f"/tmp/.vmops_{run_id}.txt"  # nosec B108 — path is inside the remote guest VM, not the local FS; name is uuid-randomized
        # POSIX: quote the redirect destination to guard against special chars,
        # then quote the whole wrapped string as a single shell argument.
        wrapped = f"{command} > {shlex.quote(tmp_out)} 2>&1"
        shell_arg = f"{flag} {shlex.quote(wrapped)}"

    # Run command with output redirection
    result = guest_exec(
        si, vm_name, program, username, password,
        arguments=shell_arg,
        timeout=timeout,
    )
    exit_code = result["exit_code"]
    timed_out = result["timed_out"]

    # Download the output file
    stdout = ""
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".txt", delete=False) as tf:
        local_tmp = tf.name

    try:
        guest_download(si, vm_name, tmp_out, local_tmp, username, password)
        with open(local_tmp, "r", encoding="utf-8", errors="replace") as f:
            stdout = f.read()
    except Exception as e:
        logger.warning("Could not retrieve output file from guest: %s", e)
    finally:
        import os as _os
        try:
            _os.unlink(local_tmp)
        except OSError:
            pass
        # Best-effort cleanup of temp file in guest
        try:
            if family == _FAMILY_WINDOWS:
                guest_exec(si, vm_name, "C:\\Windows\\System32\\cmd.exe",
                           username, password, arguments=f"/c del {tmp_out}")
            else:
                guest_exec(si, vm_name, "/bin/sh", username, password,
                           arguments=f"-c 'rm -f {tmp_out}'")
        except Exception:
            pass

    return {
        "exit_code": exit_code,
        "stdout": sanitize(stdout.strip(), max_len=5000),
        "stderr": "",
        "timed_out": timed_out,
        "command": command,
        "os_family": family or "unknown",
    }


def guest_provision(
    si: "ServiceInstance",
    vm_name: str,
    username: str,
    password: str,
    steps: list[dict],
    timeout: int = 300,
) -> dict:
    """Provision a VM by running a sequence of guest operations.

    Each step is a dict with a ``type`` key:

    - ``{"type": "exec", "command": "apt-get install -y nginx"}``
      Run a shell command (uses guest_exec_with_output).

    - ``{"type": "upload", "local_path": "/tmp/id_rsa.pub", "guest_path": "/root/.ssh/authorized_keys"}``
      Upload a local file into the guest.

    - ``{"type": "service", "name": "nginx", "action": "start"}``
      Start/stop/restart/enable a systemd service (Linux only).

    Steps are executed in order. Execution stops on the first failure
    (non-zero exit code or exception).

    Args:
        si: vSphere ServiceInstance.
        vm_name: Target VM name.
        username: Guest OS username.
        password: Guest OS password.
        steps: List of step dicts (see above).
        timeout: Per-step timeout in seconds (default 300).

    Returns:
        dict with keys:
          - success (bool)
          - completed_steps (int)
          - total_steps (int)
          - results (list of per-step dicts)
          - error (str or None)
    """
    results = []
    for i, step in enumerate(steps):
        step_type = step.get("type")
        step_result: dict = {"step": i + 1, "type": step_type, "success": False}
        try:
            if step_type == "exec":
                command = step["command"]
                step_result["command"] = command
                out = guest_exec_with_output(si, vm_name, command, username, password, timeout=timeout)
                step_result["exit_code"] = out["exit_code"]
                step_result["stdout"] = out["stdout"]
                step_result["timed_out"] = out["timed_out"]
                step_result["success"] = out["exit_code"] == 0 and not out["timed_out"]

            elif step_type == "upload":
                local_path = step["local_path"]
                guest_path = step["guest_path"]
                step_result["local_path"] = local_path
                step_result["guest_path"] = guest_path
                msg = guest_upload(si, vm_name, local_path, guest_path, username, password)
                step_result["message"] = msg
                step_result["success"] = True

            elif step_type == "service":
                name = step["name"]
                action = step.get("action", "start")
                step_result["service"] = name
                step_result["action"] = action
                command = f"systemctl {action} {name}"
                out = guest_exec_with_output(si, vm_name, command, username, password, timeout=timeout)
                step_result["exit_code"] = out["exit_code"]
                step_result["stdout"] = out["stdout"]
                step_result["success"] = out["exit_code"] == 0

            else:
                step_result["error"] = f"Unknown step type: '{step_type}'"
                results.append(step_result)
                return {
                    "success": False,
                    "completed_steps": i,
                    "total_steps": len(steps),
                    "results": results,
                    "error": f"Step {i + 1}: unknown type '{step_type}'",
                }

        except Exception as exc:
            step_result["error"] = str(exc)
            results.append(step_result)
            return {
                "success": False,
                "completed_steps": i,
                "total_steps": len(steps),
                "results": results,
                "error": f"Step {i + 1} ({step_type}) failed: {exc}",
            }

        results.append(step_result)
        if not step_result["success"]:
            return {
                "success": False,
                "completed_steps": i,
                "total_steps": len(steps),
                "results": results,
                "error": f"Step {i + 1} ({step_type}) failed with exit_code={step_result.get('exit_code')}",
            }

    return {
        "success": True,
        "completed_steps": len(steps),
        "total_steps": len(steps),
        "results": results,
        "error": None,
    }


def guest_upload(
    si: ServiceInstance,
    vm_name: str,
    local_path: str,
    guest_path: str,
    username: str,
    password: str,
    overwrite: bool = True,
) -> str:
    """Upload a file from the local machine to a VM via VMware Tools.

    Args:
        si: vSphere ServiceInstance.
        vm_name: Target VM name.
        local_path: Local file path to upload.
        guest_path: Destination path inside the guest.
        username: Guest OS username.
        password: Guest OS password.
        overwrite: Overwrite if file exists (default True).

    Returns:
        Success message string.
    """
    import urllib.request
    import ssl

    vm = _require_vm_with_tools(si, vm_name)
    content = si.RetrieveContent()
    gom = content.guestOperationsManager
    fm = gom.fileManager

    auth = _guest_auth(username, password)

    # Read local file — must be an existing regular file (not a dir/device/fifo).
    local = Path(local_path).expanduser()
    if not local.is_file():
        raise ValueError(
            f"Local upload source is not an existing regular file: {local}. "
            f"This path is read on the machine running vmware-aiops, not inside the "
            f"guest. Check it exists, then pass an absolute path to --local."
        )
    if not os.access(local, os.R_OK):
        raise PermissionError(
            f"Cannot read local upload source: {local}. Grant read permission "
            f"(chmod +r) to the user running vmware-aiops, or pass a readable "
            f"path to --local."
        )
    with open(local, "rb") as f:
        file_data = f.read()

    file_size = len(file_data)

    # Create file attributes
    file_attr = vim.vm.guest.FileManager.FileAttributes()

    # Initiate file transfer (get upload URL)
    transfer_url = fm.InitiateFileTransferToGuest(
        vm, auth, guest_path, file_attr, file_size, overwrite
    )

    # Upload via HTTPS PUT
    # The URL returned may use the vCenter/ESXi hostname; honour target SSL config.
    verify_ssl = get_verify_ssl(si)
    ctx = ssl.create_default_context()
    if not verify_ssl:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # nosec B501 — only when target.verify_ssl=false

    req = urllib.request.Request(transfer_url, data=file_data, method="PUT")
    req.add_header("Content-Type", "application/octet-stream")
    req.add_header("Content-Length", str(file_size))

    with urllib.request.urlopen(  # nosec B310 — URL from vSphere API
        req, context=ctx, timeout=_HTTP_TIMEOUT
    ):
        pass

    logger.info(
        "Uploaded %d bytes to '%s:%s'", file_size, vm_name, guest_path
    )
    return f"Uploaded {file_size} bytes to {guest_path} on VM '{vm_name}'"


def guest_download(
    si: ServiceInstance,
    vm_name: str,
    guest_path: str,
    local_path: str,
    username: str,
    password: str,
) -> str:
    """Download a file from a VM to the local machine via VMware Tools.

    Args:
        si: vSphere ServiceInstance.
        vm_name: Target VM name.
        guest_path: File path inside the guest to download.
        local_path: Local destination path.
        username: Guest OS username.
        password: Guest OS password.

    Returns:
        Success message string.
    """
    import urllib.request
    import ssl

    vm = _require_vm_with_tools(si, vm_name)
    content = si.RetrieveContent()
    gom = content.guestOperationsManager
    fm = gom.fileManager

    auth = _guest_auth(username, password)

    # Initiate file transfer from guest (get download URL)
    transfer_info = fm.InitiateFileTransferFromGuest(vm, auth, guest_path)

    # Download via HTTPS GET
    # Honour target SSL config carried on the ServiceInstance.
    verify_ssl = get_verify_ssl(si)
    ctx = ssl.create_default_context()
    if not verify_ssl:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # nosec B501 — only when target.verify_ssl=false

    with urllib.request.urlopen(  # nosec B310 — URL from vSphere API
        transfer_info.url, context=ctx, timeout=_HTTP_TIMEOUT
    ) as resp:
        file_data = resp.read()

    # Write to local file. Refuse to follow a symlink at the destination so the
    # download can't be redirected to clobber a file elsewhere; create the
    # parent dir if needed.
    local = Path(local_path).expanduser()
    if local.is_symlink():
        raise ValueError(
            f"Refusing to write download through a symlink: {local_path}. "
            f"Pass --local a path that is a regular file or does not exist yet, "
            f"or remove the symlink first, then retry."
        )
    local.parent.mkdir(parents=True, exist_ok=True)
    with open(local, "wb") as f:
        f.write(file_data)

    logger.info(
        "Downloaded %d bytes from '%s:%s' to '%s'",
        len(file_data), vm_name, guest_path, local_path,
    )
    return (
        f"Downloaded {len(file_data)} bytes from {guest_path} "
        f"on VM '{vm_name}' to {local_path}"
    )
