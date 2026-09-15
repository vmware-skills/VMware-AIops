"""Deploy and datastore commands: OVA, template, linked clone, batch, datastore browsing."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table
from vmware_policy import audited, guarded

from vmware_aiops.cli._common import (
    ConfigOption,
    DryRunOption,
    TargetOption,
    _audit,
    _double_confirm,
    _dry_run_print,
    _get_connection,
    _resolve_target,
    cli_errors,
    console,
)

deploy_app = typer.Typer(help="VM deployment: OVA, template, linked clone, batch.")
datastore_app = typer.Typer(help="Datastore browsing and image discovery.")


# ─── Datastore ───────────────────────────────────────────────────────────────


@datastore_app.command("browse")
@cli_errors
@audited("browse_datastore")
def ds_browse(
    name: Annotated[str, typer.Argument(help="Datastore name")],
    path: Annotated[str, typer.Option(help="Subdirectory path")] = "",
    pattern: Annotated[str, typer.Option(help="File pattern (e.g. *.ova)")] = "*",
    target: TargetOption = None,
    config: ConfigOption = None,
) -> None:
    """Browse files in a datastore."""
    from vmware_aiops.ops.datastore_browser import browse_datastore

    si, _ = _get_connection(target, config)
    files = browse_datastore(si, name, path=path, pattern=pattern)["items"]
    if not files:
        console.print("[yellow]No files found.[/]")
        return
    table = Table(title=f"Datastore: {name}")
    table.add_column("Name", style="cyan")
    table.add_column("Size (MB)", justify="right")
    table.add_column("Type")
    table.add_column("Modified")
    table.add_column("Path")
    for f in files:
        table.add_row(f["name"], str(f["size_mb"]), f["type"], f["modified"][:19], f["ds_path"])
    console.print(table)


@datastore_app.command("scan-images")
@cli_errors
@audited("scan_datastore_images")
def ds_scan_images(
    target: TargetOption = None,
    config: ConfigOption = None,
) -> None:
    """Scan all datastores for deployable images (OVA/ISO/OVF) and update local registry."""
    from vmware_aiops.ops.datastore_browser import update_registry

    si, _ = _get_connection(target, config)
    console.print("[bold]Scanning all datastores for images...[/]")
    registry = update_registry(si)
    images = registry.get("images", [])
    if not images:
        console.print("[yellow]No deployable images found.[/]")
        return
    table = Table(title=f"Image Registry ({len(images)} images)")
    table.add_column("Datastore", style="cyan")
    table.add_column("Name")
    table.add_column("Size (MB)", justify="right")
    table.add_column("Type")
    table.add_column("Path")
    for img in images:
        table.add_row(img["datastore"], img["name"], str(img["size_mb"]),
                       img["type"], img["ds_path"])
    console.print(table)
    console.print(f"[green]Registry saved. Last scan: {registry['last_scan']}[/]")


# ─── Deploy ──────────────────────────────────────────────────────────────────


@deploy_app.command("ova")
@cli_errors
@guarded('deploy_vm_from_ova', risk_level='medium')
def deploy_ova_cmd(
    ova_path: Annotated[str, typer.Argument(help="Local path to .ova file")],
    name: Annotated[str, typer.Option(help="VM name")],
    datastore: Annotated[str, typer.Option(help="Target datastore")],
    network: Annotated[str, typer.Option(help="Network name")] = "VM Network",
    folder: Annotated[str, typer.Option(help="VM folder path")] = "",
    power_on: Annotated[bool, typer.Option("--power-on", help="Power on after deploy")] = False,
    snapshot: Annotated[str, typer.Option(help="Create baseline snapshot")] = "",
    target: TargetOption = None,
    config: ConfigOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Deploy a VM from an OVA file."""
    from vmware_aiops.ops.vm_deploy import deploy_ova

    if dry_run:
        _dry_run_print(
            target=_resolve_target(target), vm_name=name, operation="deploy_ova",
            api_call="OvfManager.CreateImportSpec() + ImportVApp()",
            parameters={"ova": ova_path, "datastore": datastore, "network": network},
        )
        return
    si, _ = _get_connection(target, config)
    _double_confirm("部署 OVA", name, _resolve_target(target))
    result = deploy_ova(
        si, ova_path=ova_path, vm_name=name,
        datastore_name=datastore, network_name=network,
        folder_path=folder or None,
        power_on=power_on, snapshot_name=snapshot or None,
    )
    console.print(f"[green]{result}[/]")
    _audit.log(
        target=_resolve_target(target), operation="deploy_ova",
        resource=name, parameters={"ova": ova_path, "datastore": datastore},
        result=result,
    )


@deploy_app.command("template")
@cli_errors
@guarded('deploy_vm_from_template', risk_level='medium')
def deploy_template_cmd(
    template_name: Annotated[str, typer.Argument(help="Source template name")],
    name: Annotated[str, typer.Option(help="New VM name")],
    datastore: Annotated[str, typer.Option(help="Target datastore")] = "",
    cpu: Annotated[int | None, typer.Option(help="CPU count override")] = None,
    memory: Annotated[int | None, typer.Option(help="Memory (MB) override")] = None,
    power_on: Annotated[bool, typer.Option("--power-on")] = False,
    snapshot: Annotated[str, typer.Option(help="Create baseline snapshot")] = "",
    target: TargetOption = None,
    config: ConfigOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Deploy a VM from a vSphere template."""
    from vmware_aiops.ops.vm_deploy import deploy_from_template

    if dry_run:
        _dry_run_print(
            target=_resolve_target(target), vm_name=name, operation="deploy_template",
            api_call="vim.VirtualMachine.Clone()",
            parameters={"template": template_name, "datastore": datastore or "(template default)"},
        )
        return
    si, _ = _get_connection(target, config)
    _double_confirm(f"从模板 '{template_name}' 部署", name, _resolve_target(target))
    result = deploy_from_template(
        si, template_name=template_name, new_name=name,
        datastore_name=datastore or None, cpu=cpu, memory_mb=memory,
        power_on=power_on, snapshot_name=snapshot or None,
    )
    console.print(f"[green]{result}[/]")
    _audit.log(
        target=_resolve_target(target), operation="deploy_template",
        resource=name, parameters={"template": template_name},
        result=result,
    )


@deploy_app.command("linked-clone")
@cli_errors
@guarded('deploy_linked_clone', risk_level='medium')
def deploy_linked_clone_cmd(
    source: Annotated[str, typer.Option(help="Source VM name")],
    snap: Annotated[str, typer.Option("--snapshot", help="Source snapshot name")],
    name: Annotated[str, typer.Option(help="New VM name")],
    cpu: Annotated[int | None, typer.Option(help="CPU count")] = None,
    memory: Annotated[int | None, typer.Option(help="Memory (MB)")] = None,
    power_on: Annotated[bool, typer.Option("--power-on")] = False,
    baseline: Annotated[str, typer.Option(help="Create baseline snapshot on clone")] = "",
    target: TargetOption = None,
    config: ConfigOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Create a linked clone from a VM snapshot (instant, minimal disk)."""
    from vmware_aiops.ops.vm_deploy import linked_clone

    if dry_run:
        _dry_run_print(
            target=_resolve_target(target), vm_name=name, operation="linked_clone",
            api_call="vim.VirtualMachine.Clone(diskMoveType=createNewChildDiskBacking)",
            parameters={"source": source, "snapshot": snap},
        )
        return
    si, _ = _get_connection(target, config)
    _double_confirm(f"从 '{source}@{snap}' 创建链接克隆", name, _resolve_target(target))
    result = linked_clone(
        si, source_vm_name=source, new_name=name, snapshot_name=snap,
        cpu=cpu, memory_mb=memory, power_on=power_on,
        baseline_snapshot=baseline or None,
    )
    console.print(f"[green]{result}[/]")
    _audit.log(
        target=_resolve_target(target), operation="linked_clone",
        resource=name, parameters={"source": source, "snapshot": snap},
        result=result,
    )


@deploy_app.command("batch")
@cli_errors
@guarded('batch_deploy_from_spec', risk_level='high')
def deploy_batch_cmd(
    spec: Annotated[str, typer.Argument(help="Path to deploy.yaml spec file")],
    target: TargetOption = None,
    config: ConfigOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Batch deploy VMs from a YAML specification file."""
    from vmware_aiops.ops.vm_deploy import batch_deploy, load_deploy_spec

    if dry_run:
        deploy_spec = load_deploy_spec(spec)
        vm_names = [v["name"] for v in deploy_spec["vms"]]
        _dry_run_print(
            target=_resolve_target(target), vm_name=", ".join(vm_names),
            operation="batch_deploy",
            api_call="Multiple VM operations per spec",
            parameters={"spec_file": spec, "vm_count": len(vm_names)},
        )
        return
    si, _ = _get_connection(target, config)
    deploy_spec = load_deploy_spec(spec)
    vm_names = [v["name"] for v in deploy_spec["vms"]]
    console.print(f"[bold yellow]批量部署 {len(vm_names)} 台 VM: {', '.join(vm_names)}[/]")
    _double_confirm(f"批量部署 {len(vm_names)} 台 VM", ", ".join(vm_names), _resolve_target(target))
    results = batch_deploy(si, spec)

    # Display results
    table = Table(title="Batch Deploy Results")
    table.add_column("VM", style="cyan")
    table.add_column("Status")
    table.add_column("Details")
    for r in results:
        status_style = "green" if r["status"] == "ok" else "red"
        table.add_row(
            r["name"],
            f"[{status_style}]{r['status']}[/]",
            " | ".join(r.get("messages", [])),
        )
    console.print(table)

    ok_count = sum(1 for r in results if r["status"] == "ok")
    console.print(f"[bold]Result: {ok_count}/{len(results)} VMs deployed successfully.[/]")
    _audit.log(
        target=_resolve_target(target), operation="batch_deploy",
        resource=spec,
        parameters={"vm_count": len(results), "ok_count": ok_count},
        result=f"{ok_count}/{len(results)} OK",
    )


@deploy_app.command("batch-clone")
@cli_errors
@guarded('batch_clone_vms', risk_level='medium')
def deploy_batch_clone_cmd(
    source: Annotated[str, typer.Option(help="Source VM name")],
    prefix: Annotated[str, typer.Option(help="VM name prefix")] = "vm",
    count: Annotated[int, typer.Option(help="Number of clones")] = 1,
    cpu: Annotated[int | None, typer.Option(help="CPU count")] = None,
    memory: Annotated[int | None, typer.Option(help="Memory (MB)")] = None,
    snapshot: Annotated[str, typer.Option(help="Create baseline snapshot")] = "",
    power_on: Annotated[bool, typer.Option("--power-on")] = False,
    target: TargetOption = None,
    config: ConfigOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Batch clone VMs from a source VM (gold image)."""
    from vmware_aiops.ops.vm_deploy import batch_clone

    vm_names = [f"{prefix}-{i:02d}" for i in range(1, count + 1)]
    if dry_run:
        _dry_run_print(
            target=_resolve_target(target), vm_name=", ".join(vm_names),
            operation="batch_clone",
            api_call="vim.VirtualMachine.Clone() x N",
            parameters={"source": source, "count": count, "prefix": prefix},
        )
        return
    si, _ = _get_connection(target, config)
    console.print(f"[bold yellow]批量克隆 {count} 台: {', '.join(vm_names)}[/]")
    _double_confirm(f"从 '{source}' 批量克隆 {count} 台", source, _resolve_target(target))
    results = batch_clone(
        si, source_vm_name=source, vm_names=vm_names,
        cpu=cpu, memory_mb=memory,
        snapshot_name=snapshot or None, power_on=power_on,
    )

    table = Table(title="Batch Clone Results")
    table.add_column("VM", style="cyan")
    table.add_column("Status")
    table.add_column("Details")
    for r in results:
        status_style = "green" if r["status"] == "ok" else "red"
        table.add_row(r["name"], f"[{status_style}]{r['status']}[/]",
                       " | ".join(r.get("messages", [])))
    console.print(table)
    _audit.log(
        target=_resolve_target(target), operation="batch_clone",
        resource=source, parameters={"count": count, "prefix": prefix},
        result=f"{sum(1 for r in results if r['status'] == 'ok')}/{len(results)} OK",
    )


@deploy_app.command("mark-template")
@cli_errors
@guarded('convert_vm_to_template', risk_level='medium')
def deploy_mark_template(
    name: str,
    target: TargetOption = None,
    config: ConfigOption = None,
) -> None:
    """Convert a powered-off VM to a vSphere template."""
    from vmware_aiops.ops.vm_deploy import convert_to_template

    si, _ = _get_connection(target, config)
    _double_confirm("转换为模板", name, _resolve_target(target))
    result = convert_to_template(si, name)
    console.print(f"[green]{result}[/]")
    _audit.log(
        target=_resolve_target(target), operation="mark_template",
        resource=name, result=result,
    )


@deploy_app.command("iso")
@cli_errors
@guarded('attach_iso_to_vm', risk_level='medium')
def deploy_iso_cmd(
    vm_name: Annotated[str, typer.Argument(help="VM name")],
    iso: Annotated[str, typer.Option(help="ISO datastore path, e.g. '[ds1] iso/ubuntu.iso'")],
    target: TargetOption = None,
    config: ConfigOption = None,
) -> None:
    """Attach an ISO to a VM's CD-ROM drive."""
    from vmware_aiops.ops.vm_deploy import attach_iso

    si, _ = _get_connection(target, config)
    result = attach_iso(si, vm_name, iso)
    console.print(f"[green]{result}[/]")
    _audit.log(
        target=_resolve_target(target), operation="attach_iso",
        resource=vm_name, parameters={"iso": iso}, result=result,
    )
