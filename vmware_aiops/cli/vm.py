"""VM lifecycle commands: power, snapshot, clone, migrate, TTL, guest ops."""

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
    _show_state_preview,
    _validate_vm_params,
    cli_errors,
    console,
)

vm_app = typer.Typer(help="VM lifecycle: power, snapshot, clone, migrate.")


# ─── Power ────────────────────────────────────────────────────────────────────


@vm_app.command("power-on")
@cli_errors
@guarded(risk_level='medium')
def vm_power_on(
    name: str,
    target: TargetOption = None,
    config: ConfigOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Power on a VM."""
    from vmware_aiops.ops.vm_lifecycle import power_on_vm

    si, _ = _get_connection(target, config)
    if dry_run:
        from vmware_aiops.ops.vm_lifecycle import get_vm_info
        before = get_vm_info(si, name)
        _dry_run_print(
            target=_resolve_target(target), vm_name=name, operation="power_on",
            api_call="vim.VirtualMachine.PowerOn()",
            before_state={"power_state": before.get("power_state")},
            expected_after={"power_state": "poweredOn"},
        )
        return
    result = power_on_vm(si, name)
    console.print(f"[green]{result}[/]")
    _audit.log(
        target=_resolve_target(target),
        operation="power_on",
        resource=name,
        after_state={"power_state": "poweredOn"},
        result=result,
    )


@vm_app.command("power-off")
@cli_errors
@guarded(risk_level='medium')
def vm_power_off(
    name: str,
    force: Annotated[bool, typer.Option(help="Force power off")] = False,
    target: TargetOption = None,
    config: ConfigOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Power off a VM (graceful shutdown or force)."""
    from vmware_aiops.ops.vm_lifecycle import get_vm_info, power_off_vm

    si, _ = _get_connection(target, config)
    before = get_vm_info(si, name)
    if dry_run:
        api = "vim.VirtualMachine.PowerOff()" if force else "vim.VirtualMachine.ShutdownGuest()"
        _dry_run_print(
            target=_resolve_target(target), vm_name=name, operation="power_off",
            api_call=api, parameters={"force": force},
            before_state={"power_state": before.get("power_state")},
            expected_after={"power_state": "poweredOff"},
        )
        return
    _show_state_preview(before, "关机", name)
    _double_confirm("关机", name, _resolve_target(target))
    result = power_off_vm(si, name, force=force)
    console.print(f"[green]{result}[/]")
    _audit.log(
        target=_resolve_target(target),
        operation="power_off",
        resource=name,
        parameters={"force": force},
        before_state={"power_state": before.get("power_state")},
        after_state={"power_state": "poweredOff"},
        result=result,
    )


@vm_app.command("create")
@cli_errors
@guarded(risk_level='medium')
def vm_create(
    name: str,
    cpu: Annotated[int, typer.Option(help="Number of CPUs")] = 2,
    memory: Annotated[int, typer.Option(help="Memory in MB")] = 4096,
    disk: Annotated[int, typer.Option(help="Disk size in GB")] = 40,
    network: Annotated[str, typer.Option(help="Network name")] = "VM Network",
    datastore: Annotated[str, typer.Option(help="Datastore name")] = "",
    folder: Annotated[str, typer.Option(help="VM folder path")] = "",
    target: TargetOption = None,
    config: ConfigOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Create a new VM."""
    from vmware_aiops.ops.vm_lifecycle import create_vm

    _validate_vm_params(name=name, cpu=cpu, memory_mb=memory, disk_gb=disk)
    if dry_run:
        _dry_run_print(
            target=_resolve_target(target), vm_name=name, operation="create_vm",
            api_call="vim.Folder.CreateVM_Task()",
            parameters={"cpu": cpu, "memory_mb": memory, "disk_gb": disk, "network": network,
                         "datastore": datastore or "(auto)", "folder": folder or "(root)"},
        )
        return
    si, _ = _get_connection(target, config)
    result = create_vm(
        si,
        vm_name=name,
        cpu=cpu,
        memory_mb=memory,
        disk_gb=disk,
        network_name=network,
        datastore_name=datastore or None,
        folder_path=folder or None,
    )
    console.print(f"[green]{result}[/]")
    _audit.log(
        target=_resolve_target(target),
        operation="create_vm",
        resource=name,
        parameters={"cpu": cpu, "memory_mb": memory, "disk_gb": disk, "network": network},
        result=result,
    )


@vm_app.command("delete")
@cli_errors
@guarded(risk_level='critical')
def vm_delete(
    name: str,
    target: TargetOption = None,
    config: ConfigOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Delete a VM (destructive!)."""
    from vmware_aiops.ops.vm_lifecycle import delete_vm, get_vm_info

    si, _ = _get_connection(target, config)
    before = get_vm_info(si, name)
    if dry_run:
        _dry_run_print(
            target=_resolve_target(target), vm_name=name, operation="delete_vm",
            api_call="vim.VirtualMachine.Destroy_Task()",
            before_state={
                "power_state": before.get("power_state"),
                "cpu": before.get("cpu"),
                "memory_mb": before.get("memory_mb"),
                "snapshot_count": before.get("snapshot_count"),
            },
        )
        return
    _show_state_preview(before, "删除", name)
    _double_confirm("删除", name, _resolve_target(target))
    result = delete_vm(si, name)
    console.print(f"[green]{result}[/]")
    _audit.log(
        target=_resolve_target(target),
        operation="delete_vm",
        resource=name,
        before_state={
            "power_state": before.get("power_state"),
            "cpu": before.get("cpu"),
            "memory_mb": before.get("memory_mb"),
            "snapshot_count": before.get("snapshot_count"),
        },
        result=result,
    )


@vm_app.command("reconfigure")
@cli_errors
@guarded(risk_level='medium')
def vm_reconfigure(
    name: str,
    cpu: Annotated[int | None, typer.Option(help="New CPU count")] = None,
    memory: Annotated[int | None, typer.Option(help="New memory in MB")] = None,
    target: TargetOption = None,
    config: ConfigOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Reconfigure VM CPU/memory."""
    from vmware_aiops.ops.vm_lifecycle import get_vm_info, reconfigure_vm

    _validate_vm_params(cpu=cpu, memory_mb=memory)
    si, _ = _get_connection(target, config)
    before = get_vm_info(si, name)
    if dry_run:
        _dry_run_print(
            target=_resolve_target(target), vm_name=name, operation="reconfigure_vm",
            api_call="vim.VirtualMachine.ReconfigVM_Task()",
            parameters={"cpu": cpu or "(unchanged)", "memory_mb": memory or "(unchanged)"},
            before_state={"cpu": before.get("cpu"), "memory_mb": before.get("memory_mb")},
            expected_after={
                "cpu": cpu or before.get("cpu"),
                "memory_mb": memory or before.get("memory_mb"),
            },
        )
        return
    _show_state_preview(before, "调整配置", name)

    changes = []
    if cpu is not None:
        changes.append(f"CPU→{cpu}")
    if memory is not None:
        changes.append(f"内存→{memory}MB")

    proposed_cpu = cpu or before.get("cpu")
    proposed_mem = memory or before.get("memory_mb")
    console.print(
        f"[bold yellow]  Proposed: CPU={proposed_cpu}, "
        f"Memory={proposed_mem}MB[/]"
    )
    _double_confirm(f"调整配置({', '.join(changes)})", name, _resolve_target(target))
    result = reconfigure_vm(si, name, cpu=cpu, memory_mb=memory)
    console.print(f"[green]{result}[/]")
    _audit.log(
        target=_resolve_target(target),
        operation="reconfigure_vm",
        resource=name,
        parameters={"cpu": cpu, "memory_mb": memory},
        before_state={"cpu": before.get("cpu"), "memory_mb": before.get("memory_mb")},
        after_state={
            "cpu": cpu or before.get("cpu"),
            "memory_mb": memory or before.get("memory_mb"),
        },
        result=result,
    )


# ─── Snapshots ────────────────────────────────────────────────────────────────


@vm_app.command("snapshot-create")
@cli_errors
@guarded(risk_level='medium')
def vm_snapshot_create(
    vm_name: str,
    snap_name: Annotated[str, typer.Option("--name", help="Snapshot name")] = "snapshot",
    description: Annotated[str, typer.Option(help="Snapshot description")] = "",
    memory: Annotated[bool, typer.Option(help="Include memory")] = True,
    quiesce: Annotated[
        bool, typer.Option(help="Quiesce guest filesystem (requires running VMware Tools)")
    ] = False,
    target: TargetOption = None,
    config: ConfigOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Create a VM snapshot."""
    from vmware_aiops.ops.vm_lifecycle import create_snapshot

    if dry_run:
        _dry_run_print(
            target=_resolve_target(target), vm_name=vm_name, operation="snapshot_create",
            api_call="vim.VirtualMachine.CreateSnapshot_Task()",
            parameters={
                "snap_name": snap_name, "description": description,
                "memory": memory, "quiesce": quiesce,
            },
        )
        return
    si, _ = _get_connection(target, config)
    result = create_snapshot(si, vm_name, snap_name, description, memory, quiesce)
    console.print(f"[green]{result}[/]")
    _audit.log(
        target=_resolve_target(target),
        operation="snapshot_create",
        resource=vm_name,
        parameters={
            "snap_name": snap_name, "description": description,
            "memory": memory, "quiesce": quiesce,
        },
        result=result,
    )


@vm_app.command("snapshot-list")
@cli_errors
def vm_snapshot_list(
    vm_name: str,
    target: TargetOption = None,
    config: ConfigOption = None,
) -> None:
    """List VM snapshots."""
    from vmware_aiops.ops.vm_lifecycle import list_snapshots

    si, _ = _get_connection(target, config)
    snaps = list_snapshots(si, vm_name)
    if not snaps:
        console.print("[yellow]No snapshots found.[/]")
        return
    for s in snaps:
        prefix = "  " * s["level"]
        console.print(f"{prefix}[cyan]{s['name']}[/] ({s['created']}) - {s['description']}")


@vm_app.command("snapshot-revert")
@cli_errors
@guarded(risk_level='high')
def vm_snapshot_revert(
    vm_name: str,
    snap_name: Annotated[str, typer.Option("--name", help="Snapshot name to revert to")],
    target: TargetOption = None,
    config: ConfigOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Revert VM to a snapshot."""
    from vmware_aiops.ops.vm_lifecycle import get_vm_info, revert_to_snapshot

    si, _ = _get_connection(target, config)
    before = get_vm_info(si, vm_name)
    if dry_run:
        _dry_run_print(
            target=_resolve_target(target), vm_name=vm_name, operation="snapshot_revert",
            api_call="vim.vm.Snapshot.RevertToSnapshot_Task()",
            parameters={"snap_name": snap_name},
            before_state={"power_state": before.get("power_state")},
        )
        return
    _show_state_preview(before, "恢复快照", vm_name)
    console.print(f"[bold yellow]  Snapshot: {snap_name}[/]")
    _double_confirm(f"恢复快照 '{snap_name}'", vm_name, _resolve_target(target))
    result = revert_to_snapshot(si, vm_name, snap_name)
    console.print(f"[green]{result}[/]")
    _audit.log(
        target=_resolve_target(target),
        operation="snapshot_revert",
        resource=vm_name,
        parameters={"snap_name": snap_name},
        before_state={"power_state": before.get("power_state")},
        result=result,
    )


@vm_app.command("snapshot-delete")
@cli_errors
@guarded(risk_level='high')
def vm_snapshot_delete(
    vm_name: str,
    snap_name: Annotated[str, typer.Option("--name", help="Snapshot name to delete")],
    target: TargetOption = None,
    config: ConfigOption = None,
    dry_run: DryRunOption = False,
    no_wait: Annotated[
        bool,
        typer.Option(
            "--no-wait",
            help="Fire the delete and return the task id immediately instead of "
            "blocking up to 30 min on consolidation. Poll with 'vm task-status'.",
        ),
    ] = False,
    timeout: Annotated[
        int,
        typer.Option(help="Seconds to wait for consolidation before returning the task id."),
    ] = 1800,
    remove_children: Annotated[
        bool,
        typer.Option(
            "--remove-children",
            help="Also delete every snapshot below this one. Off by default: "
            "children are kept and consolidated into their parent.",
        ),
    ] = False,
) -> None:
    """Delete a VM snapshot (waits up to 30 min for delta consolidation)."""
    from vmware_aiops.ops.vm_lifecycle import delete_snapshot

    if dry_run:
        _dry_run_print(
            target=_resolve_target(target), vm_name=vm_name, operation="snapshot_delete",
            api_call="vim.vm.Snapshot.RemoveSnapshot_Task()",
            parameters={"snap_name": snap_name, "remove_children": remove_children},
        )
        return
    si, _ = _get_connection(target, config)
    console.print(f"[bold yellow]⚠️  即将删除 VM '{vm_name}' 的快照 '{snap_name}'[/]")
    _double_confirm(f"删除快照 '{snap_name}'", vm_name, _resolve_target(target))
    result = delete_snapshot(
        si, vm_name, snap_name, wait=not no_wait, timeout=timeout,
        remove_children=remove_children,
    )
    console.print(f"[green]{result}[/]")
    _audit.log(
        target=_resolve_target(target),
        operation="snapshot_delete",
        resource=vm_name,
        parameters={"snap_name": snap_name},
        result=result,
    )


@vm_app.command("task-status")
@cli_errors
def vm_task_status(
    task_id: Annotated[str, typer.Argument(help="Task id from a --no-wait operation")],
    target: TargetOption = None,
    config: ConfigOption = None,
) -> None:
    """Poll a long-running task (e.g. an async snapshot delete) by its id."""
    from vmware_aiops.ops.vm_lifecycle import get_task_status

    si, _ = _get_connection(target, config)
    status = get_task_status(si, task_id)
    state = status.get("state")
    colour = {"success": "green", "error": "red", "gone": "yellow"}.get(state, "cyan")
    console.print(f"[bold {colour}]Task {task_id}: {state}[/]")
    for key in ("operation", "entity", "progress_pct", "task_error", "note"):
        if status.get(key) is not None:
            console.print(f"  {key}: {status[key]}")


# ─── Clone & Migrate ──────────────────────────────────────────────────────────


@vm_app.command("clone")
@cli_errors
@guarded(risk_level='high')
def vm_clone(
    name: str,
    new_name: Annotated[str, typer.Option("--new-name", help="Name for the clone")],
    to_host: Annotated[
        str | None,
        typer.Option("--to-host", help="Target ESXi host (default: source's host)"),
    ] = None,
    to_datastore: Annotated[
        str | None,
        typer.Option("--to-datastore", help="Target datastore (default: source's datastore)"),
    ] = None,
    power_on: Annotated[
        bool, typer.Option("--power-on/--no-power-on", help="Power on after clone")
    ] = False,
    target: TargetOption = None,
    config: ConfigOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Clone a VM. Use --to-host and --to-datastore to land it on a specific host/storage."""
    from vmware_aiops.ops.vm_lifecycle import clone_vm, get_vm_info

    si, _ = _get_connection(target, config)
    before = get_vm_info(si, name)
    params = {"new_name": new_name, "to_host": to_host, "to_datastore": to_datastore, "power_on": power_on}
    if dry_run:
        _dry_run_print(
            target=_resolve_target(target), vm_name=name, operation="clone_vm",
            api_call="vim.VirtualMachine.Clone()",
            parameters=params,
            before_state={"cpu": before.get("cpu"), "memory_mb": before.get("memory_mb")},
        )
        return
    _show_state_preview(before, "克隆", name)
    console.print(f"[bold yellow]  Clone name: {new_name}[/]")
    if to_host:
        console.print(f"[bold yellow]  Target host: {to_host}[/]")
    if to_datastore:
        console.print(f"[bold yellow]  Target datastore: {to_datastore}[/]")
    _double_confirm(f"克隆为 '{new_name}'", name, _resolve_target(target))
    result = clone_vm(
        si, name, new_name,
        target_host=to_host,
        target_datastore=to_datastore,
        power_on=power_on,
    )
    console.print(f"[green]{result}[/]")
    _audit.log(
        target=_resolve_target(target),
        operation="clone_vm",
        resource=name,
        parameters=params,
        before_state={"cpu": before.get("cpu"), "memory_mb": before.get("memory_mb")},
        result=result,
    )


@vm_app.command("migrate")
@cli_errors
@guarded(risk_level='high')
def vm_migrate(
    name: str,
    to_host: Annotated[str, typer.Option("--to-host", help="Target ESXi host name")],
    to_datastore: Annotated[
        str | None,
        typer.Option(
            "--to-datastore",
            help="Target datastore (required when target host has no access to source datastore)",
        ),
    ] = None,
    target: TargetOption = None,
    config: ConfigOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Migrate (vMotion) a VM to another host, optionally with storage vMotion."""
    from vmware_aiops.ops.vm_lifecycle import get_vm_info, migrate_vm

    si, _ = _get_connection(target, config)
    before = get_vm_info(si, name)
    params = {"to_host": to_host, "to_datastore": to_datastore}
    if dry_run:
        _dry_run_print(
            target=_resolve_target(target), vm_name=name, operation="migrate_vm",
            api_call="vim.VirtualMachine.Relocate()",
            parameters=params,
            before_state={"host": before.get("host")},
            expected_after={"host": to_host},
        )
        return
    _show_state_preview(before, "迁移", name)
    console.print(f"[bold yellow]  Target host: {to_host}[/]")
    if to_datastore:
        console.print(f"[bold yellow]  Target datastore: {to_datastore}[/]")
    _double_confirm(f"迁移到 '{to_host}'", name, _resolve_target(target))
    result = migrate_vm(si, name, to_host, target_datastore=to_datastore)
    console.print(f"[green]{result}[/]")
    _audit.log(
        target=_resolve_target(target),
        operation="migrate_vm",
        resource=name,
        parameters=params,
        before_state={"host": before.get("host")},
        after_state={"host": to_host},
        result=result,
    )


# ─── TTL & Clean Slate ────────────────────────────────────────────────────────


@vm_app.command("set-ttl")
@cli_errors
@guarded(risk_level='medium')
def vm_set_ttl(
    vm_name: str,
    minutes: Annotated[int, typer.Option("--minutes", "-m", help="Minutes until auto-deletion")],
    target: TargetOption = None,
    config: ConfigOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Set a TTL for a VM. The daemon will auto-delete it when time expires (destructive!)."""
    from vmware_aiops.ops.ttl import preview_ttl, set_ttl

    if dry_run:
        _dry_run_print(
            target=_resolve_target(target), vm_name=vm_name, operation="vm_set_ttl",
            api_call="scheduler.delete_vm() on TTL expiry",
            parameters={"minutes": minutes, "preview": preview_ttl(vm_name, minutes, target=target)},
        )
        return
    _double_confirm(f"设置 TTL ({minutes} 分钟后自动删除)", vm_name, _resolve_target(target))
    result = set_ttl(vm_name, minutes, target=target)
    console.print(f"[green]{result}[/]")
    _audit.log(
        target=_resolve_target(target),
        operation="vm_set_ttl",
        resource=vm_name,
        parameters={"minutes": minutes},
        result=result,
    )


@vm_app.command("cancel-ttl")
@cli_errors
@guarded(risk_level='medium')
def vm_cancel_ttl(vm_name: str) -> None:
    """Cancel an existing TTL for a VM."""
    from vmware_aiops.ops.ttl import cancel_ttl

    result = cancel_ttl(vm_name)
    console.print(f"[yellow]{result}[/]")


@vm_app.command("list-ttl")
@cli_errors
def vm_list_ttl() -> None:
    """List all VMs with TTLs registered."""
    from vmware_aiops.ops.ttl import list_ttl

    entries = list_ttl()["items"]
    if not entries:
        console.print("[yellow]No TTLs registered.[/]")
        return
    table = Table(title="VM TTL Registry")
    table.add_column("VM Name", style="cyan")
    table.add_column("Expires At (UTC)")
    table.add_column("Remaining (min)", justify="right")
    table.add_column("Target")
    table.add_column("Status")
    for e in entries:
        status = "[red]EXPIRED[/]" if e["expired"] else "[green]active[/]"
        table.add_row(
            e["vm_name"],
            e["expires_at"],
            str(e["remaining_minutes"]),
            e["target"] or "(default)",
            status,
        )
    console.print(table)


@vm_app.command("clean-slate")
@cli_errors
@guarded(risk_level='high')
def vm_clean_slate(
    vm_name: str,
    snapshot: Annotated[str, typer.Option("--snapshot", "-s", help="Snapshot name")] = "baseline",
    target: TargetOption = None,
    config: ConfigOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Revert VM to baseline snapshot (Clean Slate). Powers off first if needed."""
    from vmware_aiops.ops.vm_lifecycle import clean_slate, get_vm_info

    if dry_run:
        _dry_run_print(
            target=_resolve_target(target), vm_name=vm_name, operation="clean_slate",
            api_call="vim.VirtualMachine.PowerOff() + RevertToSnapshot_Task()",
            parameters={"snapshot": snapshot},
        )
        return
    si, _ = _get_connection(target, config)
    before = get_vm_info(si, vm_name)
    _show_state_preview(before, "Clean Slate (恢复基线快照)", vm_name)
    console.print(f"[bold yellow]  Snapshot: {snapshot}[/]")
    _double_confirm(f"恢复基线快照 '{snapshot}'", vm_name, _resolve_target(target))
    result = clean_slate(si, vm_name, snapshot_name=snapshot)
    console.print(f"[green]{result}[/]")
    _audit.log(
        target=_resolve_target(target),
        operation="clean_slate",
        resource=vm_name,
        parameters={"snapshot": snapshot},
        before_state={"power_state": before.get("power_state")},
        result=result,
    )


# ─── Guest Operations ────────────────────────────────────────────────────────


@vm_app.command("guest-exec")
@cli_errors
@guarded(risk_level='medium', sensitive_params=['password'])
def vm_guest_exec_cmd(
    vm_name: Annotated[str, typer.Argument(help="VM name")],
    command: Annotated[str, typer.Option("--cmd", help="Full path to program (e.g. /bin/bash)")],
    arguments: Annotated[str, typer.Option("--args", help="Command arguments")] = "",
    username: Annotated[str, typer.Option("--user", "-u", help="Guest OS username")] = "root",
    password: Annotated[str, typer.Option("--password", "-p", help="Guest OS password", prompt=True, hide_input=True)] = "",
    target: TargetOption = None,
    config: ConfigOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Execute a command inside a VM via VMware Tools."""
    from vmware_aiops.ops.guest_ops import guest_exec

    if dry_run:
        # Never echo the password into the preview.
        _dry_run_print(
            target=_resolve_target(target), vm_name=vm_name, operation="guest_exec",
            api_call="GuestProcessManager.StartProgramInGuest()",
            parameters={"command": command, "arguments": arguments, "username": username},
        )
        return
    # Arbitrary command execution inside a guest OS is the most powerful thing
    # this CLI does, and it was the only destructive command without a
    # confirmation — 25 others had one. `_double_confirm` is where that
    # confirmation now lives.
    _double_confirm(f"在客户机执行命令({command})", vm_name, _resolve_target(target))
    si, _ = _get_connection(target, config)
    result = guest_exec(si, vm_name, command, username, password, arguments=arguments)
    _audit.log(
        target=_resolve_target(target),
        operation="guest_exec",
        resource=vm_name,
        result=f"exit_code={result['exit_code']}",
    )
    table = Table(title=f"Guest Exec: {vm_name}")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Command", result["command"])
    table.add_row("PID", str(result["pid"]))
    exit_style = "green" if result["exit_code"] == 0 else "red"
    table.add_row("Exit Code", f"[{exit_style}]{result['exit_code']}[/]")
    table.add_row("Timed Out", str(result["timed_out"]))
    console.print(table)


@vm_app.command("guest-upload")
@cli_errors
@guarded(risk_level='medium', sensitive_params=['password'])
def vm_guest_upload_cmd(
    vm_name: Annotated[str, typer.Argument(help="VM name")],
    local_path: Annotated[str, typer.Option("--local", help="Local file path")],
    guest_path: Annotated[str, typer.Option("--guest", help="Destination path inside VM")],
    username: Annotated[str, typer.Option("--user", "-u", help="Guest OS username")] = "root",
    password: Annotated[str, typer.Option("--password", "-p", help="Guest OS password", prompt=True, hide_input=True)] = "",
    target: TargetOption = None,
    config: ConfigOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Upload a file to a VM via VMware Tools."""
    from vmware_aiops.ops.guest_ops import guest_upload

    if dry_run:
        _dry_run_print(
            target=_resolve_target(target), vm_name=vm_name, operation="guest_upload",
            api_call="GuestFileManager.InitiateFileTransferToGuest()",
            parameters={"local_path": local_path, "guest_path": guest_path, "username": username},
        )
        return
    # Writes a file inside the guest OS — destructive by the same standard as
    # the 25 commands that already confirm.
    _double_confirm(f"上传文件到客户机({guest_path})", vm_name, _resolve_target(target))
    si, _ = _get_connection(target, config)
    result = guest_upload(si, vm_name, local_path, guest_path, username, password)
    _audit.log(
        target=_resolve_target(target),
        operation="guest_upload",
        resource=vm_name,
        result=result,
    )
    console.print(f"[green]✓ {result}[/green]")


@vm_app.command("guest-download")
@cli_errors
@guarded(risk_level='medium', sensitive_params=['password'])
def vm_guest_download_cmd(
    vm_name: Annotated[str, typer.Argument(help="VM name")],
    guest_path: Annotated[str, typer.Option("--guest", help="File path inside VM")],
    local_path: Annotated[str, typer.Option("--local", help="Local destination path")],
    username: Annotated[str, typer.Option("--user", "-u", help="Guest OS username")] = "root",
    password: Annotated[str, typer.Option("--password", "-p", help="Guest OS password", prompt=True, hide_input=True)] = "",
    overwrite: Annotated[
        bool, typer.Option("--overwrite", help="Replace an existing local file")
    ] = False,
    target: TargetOption = None,
    config: ConfigOption = None,
) -> None:
    """Download a file from a VM via VMware Tools (writes the local filesystem)."""
    from vmware_aiops.ops.guest_ops import guest_download

    si, _ = _get_connection(target, config)
    result = guest_download(
        si, vm_name, guest_path, local_path, username, password, overwrite=overwrite
    )
    _audit.log(
        target=_resolve_target(target),
        operation="guest_download",
        resource=vm_name,
        result=result,
    )
    console.print(f"[green]✓ {result}[/green]")
