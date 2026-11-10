"""Vm Guest callbacks using the existing facade runtime."""

from __future__ import annotations

from typing import Annotated

import typer


from vmware_aiops.cli._common import (
    ConfigOption,
    DryRunOption,
    TargetOption,
)


def register_callbacks(runtime):
    @runtime.vm_app.command("guest-exec")
    @runtime.cli_errors
    @runtime.guarded("run_vm_guest_command", risk_level="critical", sensitive_params=["password"])
    def vm_guest_exec_cmd(
        vm_name: Annotated[str, typer.Argument(help="VM name")],
        command: Annotated[
            str, typer.Option("--cmd", help="Full path to program (e.g. /bin/bash)")
        ],
        username: Annotated[
            str, typer.Option("--user", "-u", help="Guest OS account to run as (required)")
        ],
        arguments: Annotated[str, typer.Option("--args", help="Command arguments")] = "",
        password: Annotated[
            str,
            typer.Option(
                "--password", "-p", help="Guest OS password", prompt=True, hide_input=True
            ),
        ] = "",
        target: TargetOption = None,
        config: ConfigOption = None,
        dry_run: DryRunOption = False,
    ) -> None:
        """Execute a command inside a VM via VMware Tools."""
        from vmware_aiops.ops.guest_ops import guest_exec

        if dry_run:
            # Never echo the password into the preview.
            runtime._dry_run_print(
                target=runtime._resolve_target(target),
                vm_name=vm_name,
                operation="guest_exec",
                api_call="GuestProcessManager.StartProgramInGuest()",
                parameters={"command": command, "arguments": arguments, "username": username},
            )
            return
        # Arbitrary command execution inside a guest OS is the most powerful thing
        # this CLI does, and it was the only destructive command without a
        # confirmation — 25 others had one. `_double_confirm` is where that
        # confirmation now lives.
        runtime._double_confirm(
            f"在客户机执行命令({command})", vm_name, runtime._resolve_target(target)
        )
        si, _ = runtime._get_connection(target, config)
        result = guest_exec(si, vm_name, command, username, password, arguments=arguments)
        runtime._audit.log(
            target=runtime._resolve_target(target),
            operation="guest_exec",
            resource=vm_name,
            result=f"exit_code={result['exit_code']}",
        )
        table = runtime.Table(title=f"Guest Exec: {vm_name}")
        table.add_column("Field", style="cyan")
        table.add_column("Value")
        table.add_row("Command", result["command"])
        table.add_row("PID", str(result["pid"]))
        exit_style = "green" if result["exit_code"] == 0 else "red"
        table.add_row("Exit Code", f"[{exit_style}]{result['exit_code']}[/]")
        table.add_row("Timed Out", str(result["timed_out"]))
        runtime.console.print(table)

    @runtime.vm_app.command("guest-upload")
    @runtime.cli_errors
    @runtime.guarded("upload_vm_guest_file", risk_level="high", sensitive_params=["password"])
    def vm_guest_upload_cmd(
        vm_name: Annotated[str, typer.Argument(help="VM name")],
        local_path: Annotated[str, typer.Option("--local", help="Local file path")],
        guest_path: Annotated[str, typer.Option("--guest", help="Destination path inside VM")],
        username: Annotated[
            str, typer.Option("--user", "-u", help="Guest OS account to run as (required)")
        ],
        password: Annotated[
            str,
            typer.Option(
                "--password", "-p", help="Guest OS password", prompt=True, hide_input=True
            ),
        ] = "",
        target: TargetOption = None,
        config: ConfigOption = None,
        dry_run: DryRunOption = False,
    ) -> None:
        """Upload a file to a VM via VMware Tools."""
        from vmware_aiops.ops.guest_ops import guest_upload

        if dry_run:
            runtime._dry_run_print(
                target=runtime._resolve_target(target),
                vm_name=vm_name,
                operation="guest_upload",
                api_call="GuestFileManager.InitiateFileTransferToGuest()",
                parameters={
                    "local_path": local_path,
                    "guest_path": guest_path,
                    "username": username,
                },
            )
            return
        # Writes a file inside the guest OS — destructive by the same standard as
        # the 25 commands that already confirm.
        runtime._double_confirm(
            f"上传文件到客户机({guest_path})", vm_name, runtime._resolve_target(target)
        )
        si, _ = runtime._get_connection(target, config)
        result = guest_upload(si, vm_name, local_path, guest_path, username, password)
        runtime._audit.log(
            target=runtime._resolve_target(target),
            operation="guest_upload",
            resource=vm_name,
            result=result,
        )
        runtime.console.print(f"[green]✓ {result}[/green]")

    @runtime.vm_app.command("guest-download")
    @runtime.cli_errors
    @runtime.guarded("download_vm_guest_file", risk_level="high", sensitive_params=["password"])
    def vm_guest_download_cmd(
        vm_name: Annotated[str, typer.Argument(help="VM name")],
        guest_path: Annotated[str, typer.Option("--guest", help="File path inside VM")],
        local_path: Annotated[str, typer.Option("--local", help="Local destination path")],
        username: Annotated[
            str, typer.Option("--user", "-u", help="Guest OS account to run as (required)")
        ],
        password: Annotated[
            str,
            typer.Option(
                "--password", "-p", help="Guest OS password", prompt=True, hide_input=True
            ),
        ] = "",
        overwrite: Annotated[
            bool, typer.Option("--overwrite", help="Replace an existing local file")
        ] = False,
        target: TargetOption = None,
        config: ConfigOption = None,
    ) -> None:
        """Download a file from a VM via VMware Tools (writes the local filesystem)."""
        from vmware_aiops.ops.guest_ops import guest_download

        si, _ = runtime._get_connection(target, config)
        result = guest_download(
            si, vm_name, guest_path, local_path, username, password, overwrite=overwrite
        )
        runtime._audit.log(
            target=runtime._resolve_target(target),
            operation="guest_download",
            resource=vm_name,
            result=result,
        )
        runtime.console.print(f"[green]✓ {result}[/green]")

    return (
        vm_guest_exec_cmd,
        vm_guest_upload_cmd,
        vm_guest_download_cmd,
    )
