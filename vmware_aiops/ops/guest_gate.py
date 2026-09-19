"""The blast-radius gate in front of the guest-writing MCP tools (HLD §7).

``guest_ops`` stays the executor the CLI calls. This module is what the MCP
tools put in front of it: it reads what a call would do inside the guest and
what stands in the way, without starting anything or transferring anything.

* ``measure_*`` returns the blast radius (L1): the VM (name + instance UUID),
  the guest account (never the password), what would run or be written, and
  ``blockers`` / ``unmeasured``.
* The tool returns that as a preview when ``confirm`` is False (L2).
* With ``confirm=True`` the tool re-measures and calls
  ``gate.refuse_on`` before the ops function (L3).

Every reading of the VM is a property read on objects the lookup already
returned; no new API is called.
"""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pyVmomi import vim, vmodl
from vmware_policy import sanitize

from vmware_aiops.ops import vm_lifecycle
from vmware_aiops.ops.guest_ops import _FAMILY_WINDOWS, _detect_shell

if TYPE_CHECKING:
    from pyVmomi.vim import ServiceInstance

#: Cap for one command string or argument list shown in a blast radius.
MAX_COMMAND_CHARS = 1000

#: Cap for a path shown in a blast radius.
MAX_PATH_CHARS = 500

#: What ``guest_provision`` accepts, and the key each type cannot run without.
STEP_REQUIRED_KEYS: dict[str, tuple[str, ...]] = {
    "exec": ("command",),
    "upload": ("local_path", "guest_path"),
    "service": ("name",),
}

_READ_ERRORS = (vmodl.MethodFault, AttributeError, TypeError)
_TOOLS_RUNNING = "guestToolsRunning"
_UNREAD = object()


def _read(fn: Callable[[], Any]) -> Any:
    try:
        return fn()
    except _READ_ERRORS:
        return _UNREAD


def _text(value: Any, cap: int) -> str:
    return sanitize(str(value), cap)


def _cap_blocker(label: str, value: Any, cap: int) -> str | None:
    """A blocker when ``value`` is longer than the preview can show.

    The preview shows at most ``cap`` characters; what runs is the whole
    string. Approving a command whose end nobody saw is approving something
    unseen, so the call refuses instead of truncating silently.
    """
    length = len(str(value))
    if length <= cap:
        return None
    return (
        f"The {label} is {length} characters; the preview shows only the first {cap}, "
        "so what would run cannot be reviewed in full. Put it in a script, upload it "
        "with vm_guest_upload, and run the script instead."
    )


def _shown(fields: dict[str, tuple[Any, int]]) -> tuple[dict[str, Any], list[str]]:
    """Each field as shown (capped), its full length, whether any was cut, and blockers."""
    shown: dict[str, Any] = {}
    blockers: list[str] = []
    truncated = False
    for name, (value, cap) in fields.items():
        if value is None:
            shown[name] = None
            continue
        shown[name] = _text(value, cap)
        shown[f"{name}_length"] = len(str(value))
        blocker = _cap_blocker(name.replace("_", " "), value, cap)
        if blocker:
            truncated = True
            blockers.append(blocker)
    shown["truncated"] = truncated
    return shown, blockers


def _measure_vm(vm: vim.VirtualMachine, vm_name: str, username: str) -> dict[str, Any]:
    """What every guest tool touches: which VM, as whom, and whether it can run."""
    instance_uuid = _read(lambda: vm.config.instanceUuid)
    power_state = _read(lambda: vm.runtime.powerState)
    tools_status = _read(lambda: vm.guest.toolsRunningStatus if vm.guest else None)
    family = _read(lambda: vm.guest.guestFamily if vm.guest else None)
    name = _read(lambda: vm.name)

    unmeasured = [
        field for field, value in (
            ("instance_uuid", instance_uuid),
            ("power_state", power_state),
            ("tools_running_status", tools_status),
        ) if value is _UNREAD or (field == "instance_uuid" and not value)
    ]
    blockers: list[str] = []
    if power_state is not _UNREAD and power_state != vim.VirtualMachine.PowerState.poweredOn:
        blockers.append(
            f"VM '{_text(vm_name, 200)}' is not powered on ({power_state}). Guest "
            "operations need a running guest: run vm_power_on, wait for the guest "
            "to boot, then preview again."
        )
    elif tools_status is not _UNREAD and tools_status != _TOOLS_RUNNING:
        blockers.append(
            f"VMware Tools is not running in '{_text(vm_name, 200)}' (status: "
            f"{tools_status}). Install or start VMware Tools inside the guest, check "
            "it with vm_info (vmware-monitor), then preview again."
        )

    return {
        "vm": _text(name if name is not _UNREAD else vm_name, 200),
        "instance_uuid": (
            None if instance_uuid is _UNREAD or not instance_uuid else str(instance_uuid)
        ),
        "power_state": None if power_state is _UNREAD else str(power_state),
        "tools_running_status": None if tools_status is _UNREAD else tools_status,
        "os_family": None if family is _UNREAD else family,
        "username": _text(username, 200),
        "blockers": blockers,
        "unmeasured": unmeasured,
    }


def _local_file(path: str) -> tuple[dict[str, Any], str | None]:
    """The local source an upload would read: its size, or why it cannot be read.

    The same two checks, with the same wording, as ``guest_ops.guest_upload`` —
    here they run before anything is transferred.
    """
    local = Path(path).expanduser()
    info: dict[str, Any] = {"local_path": _text(path, MAX_PATH_CHARS), "local_size_bytes": None}
    if not local.is_file():
        return info, (
            f"Local upload source is not an existing regular file: {_text(local, MAX_PATH_CHARS)}. "
            "This path is read on the machine running vmware-aiops, not inside the "
            "guest. Check it exists, then pass an absolute path."
        )
    if not os.access(local, os.R_OK):
        return info, (
            f"Cannot read local upload source: {_text(local, MAX_PATH_CHARS)}. Grant read "
            "permission to the user running vmware-aiops, or pass a readable path."
        )
    try:
        info["local_size_bytes"] = local.stat().st_size
    except OSError:
        return info, (
            f"Cannot stat local upload source: {_text(local, MAX_PATH_CHARS)}. Check the "
            "file is still there and readable, then preview again."
        )
    return info, None


def _vm(si: ServiceInstance, vm_name: str) -> vim.VirtualMachine:
    """Find the VM or raise the teaching ``VMNotFoundError`` / ``AmbiguousVMError``."""
    return vm_lifecycle._require_vm(si, vm_name)


def measure_guest_exec(
    si: ServiceInstance,
    vm_name: str,
    command: str,
    username: str,
    arguments: str = "",
    working_directory: str | None = None,
) -> dict[str, Any]:
    """What ``guest_exec`` would start inside the guest."""
    radius = _measure_vm(_vm(si, vm_name), vm_name, username)
    shown, too_long = _shown({
        "command": (command, MAX_COMMAND_CHARS),
        "arguments": (arguments, MAX_COMMAND_CHARS),
        "working_directory": (working_directory or None, MAX_PATH_CHARS),
    })
    return {**radius, **shown, "blockers": radius["blockers"] + too_long}


def measure_guest_exec_output(
    si: ServiceInstance, vm_name: str, command: str, username: str, timeout: int
) -> dict[str, Any]:
    """What ``guest_exec_with_output`` would run, through which shell."""
    vm = _vm(si, vm_name)
    radius = _measure_vm(vm, vm_name, username)
    shell = _read(lambda: " ".join(_detect_shell(vm)))
    shown, too_long = _shown({"command": (command, MAX_COMMAND_CHARS)})
    return {
        **radius,
        **shown,
        "blockers": radius["blockers"] + too_long,
        "shell": None if shell is _UNREAD else shell,
        "unmeasured": radius["unmeasured"] + (["shell"] if shell is _UNREAD else []),
        "timeout_s": timeout,
        "guest_side_effects": "Writes the output to a temp file in the guest, "
                              "downloads it, then deletes it.",
    }


def measure_guest_upload(
    si: ServiceInstance, vm_name: str, local_path: str, guest_path: str, username: str
) -> dict[str, Any]:
    """What ``guest_upload`` would read locally and write into the guest."""
    radius = _measure_vm(_vm(si, vm_name), vm_name, username)
    info, blocker = _local_file(local_path)
    shown, too_long = _shown({"guest_path": (guest_path, MAX_PATH_CHARS)})
    return {
        **radius,
        **info,
        **shown,
        # guest_upload passes overwrite=True: an existing guest file is replaced.
        "overwrites_existing_guest_file": True,
        "blockers": radius["blockers"] + ([blocker] if blocker else []) + too_long,
    }


def measure_guest_download(
    si: ServiceInstance, vm_name: str, guest_path: str, local_path: str, username: str
) -> dict[str, Any]:
    """What a plan's ``guest_download`` step would read from the guest and write locally."""
    radius = _measure_vm(_vm(si, vm_name), vm_name, username)
    shown, too_long = _shown({
        "guest_path": (guest_path, MAX_PATH_CHARS),
        "local_path": (local_path, MAX_PATH_CHARS),
    })
    return {**radius, **shown, "blockers": radius["blockers"] + too_long}


def _describe_step(number: int, step: Any, family: Any) -> tuple[dict[str, Any], list[str]]:
    """One provisioning step as it would run, plus what stops it from running."""
    if not isinstance(step, dict):
        return (
            {"step": number, "type": None},
            [f"step {number}: not an object — each step is a dict with a 'type' key."],
        )
    kind = step.get("type")
    if kind not in STEP_REQUIRED_KEYS:
        return (
            {"step": number, "type": _text(kind, 50)},
            [f"step {number}: unknown type '{_text(kind, 50)}' — use one of "
             f"{', '.join(STEP_REQUIRED_KEYS)}."],
        )
    missing = [k for k in STEP_REQUIRED_KEYS[kind] if not step.get(k)]
    if missing:
        return (
            {"step": number, "type": kind},
            [f"step {number}: missing '{missing[0]}' for a {kind} step."],
        )
    if kind in ("exec", "service"):
        command = (
            step["command"] if kind == "exec"
            else f"systemctl {step.get('action', 'start')} {step['name']}"
        )
        shown, too_long = _shown({"command": (command, MAX_COMMAND_CHARS)})
        problems = [f"step {number}: {b}" for b in too_long]
        if kind == "service" and family == _FAMILY_WINDOWS:
            problems.append(
                f"step {number}: a service step runs systemctl, which a Windows guest "
                "does not have. Use an exec step with the Windows service command instead."
            )
        return {"step": number, "type": kind, **shown}, problems
    info, blocker = _local_file(str(step["local_path"]))
    described = {"step": number, "type": kind, **info,
                 "guest_path": _text(step["guest_path"], MAX_PATH_CHARS)}
    return described, [f"step {number}: {blocker}"] if blocker else []


def measure_guest_provision(
    si: ServiceInstance, vm_name: str, username: str, steps: list[Any], timeout: int
) -> dict[str, Any]:
    """Every step ``guest_provision`` would run, in order, with counts per kind.

    All steps are listed, not a capped sample: the caller approves the run as a
    whole, and a step hidden past a cap is a step nobody approved.
    """
    radius = _measure_vm(_vm(si, vm_name), vm_name, username)
    steps = list(steps or [])
    described: list[dict[str, Any]] = []
    blockers = list(radius["blockers"])
    if not steps:
        blockers.append(
            "There are no steps to run. Pass at least one exec, upload or service step."
        )
    for number, step in enumerate(steps, start=1):
        entry, problems = _describe_step(number, step, radius["os_family"])
        described.append(entry)
        blockers.extend(problems)
    return {
        **radius,
        "step_count": len(steps),
        "steps_by_type": dict(
            Counter(d["type"] for d in described if d["type"] in STEP_REQUIRED_KEYS)
        ),
        "steps": described,
        "timeout_per_step_s": timeout,
        "stops_on_first_failure": True,
        "blockers": blockers,
    }
