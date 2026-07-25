"""Cluster management commands: create, delete, configure HA/DRS, add/remove hosts."""

from __future__ import annotations

from typing import Annotated

import typer
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
    cli_errors,
    console,
)

cluster_app = typer.Typer(help="Cluster management: create, delete, configure HA/DRS.")


@cluster_app.command("info")
@cli_errors
def cluster_info_cmd(
    name: str,
    target: TargetOption = None,
    config: ConfigOption = None,
) -> None:
    """Show detailed cluster info."""
    from vmware_aiops.ops.cluster_mgmt import get_cluster_info

    si, _ = _get_connection(target, config)
    info = get_cluster_info(si, name)
    console.print(f"\n[bold cyan]Cluster '{name}':[/]")
    for k, v in info.items():
        if k == "hosts":
            console.print(f"  [cyan]hosts:[/]")
            for h in v:
                state_style = "green" if h["connection_state"] == "connected" else "red"
                maint = " [yellow](maintenance)[/]" if h["maintenance_mode"] else ""
                console.print(
                    f"    - {h['name']} [{state_style}]{h['connection_state']}[/]{maint}"
                )
        else:
            console.print(f"  [cyan]{k}:[/] {v}")


@cluster_app.command("create")
@cli_errors
@guarded(risk_level='medium')
def cluster_create_cmd(
    name: str,
    ha: Annotated[bool, typer.Option("--ha", help="Enable HA")] = False,
    drs: Annotated[bool, typer.Option("--drs", help="Enable DRS")] = False,
    drs_behavior: Annotated[
        str, typer.Option("--drs-behavior", help="DRS behavior: fullyAutomated|partiallyAutomated|manual")
    ] = "fullyAutomated",
    datacenter: Annotated[str, typer.Option(help="Datacenter name")] = "",
    target: TargetOption = None,
    config: ConfigOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Create a new cluster."""
    from vmware_aiops.ops.cluster_mgmt import create_cluster

    if dry_run:
        _dry_run_print(
            target=_resolve_target(target), vm_name=name, operation="create_cluster",
            api_call="datacenter.hostFolder.CreateClusterEx()",
            parameters={"ha": ha, "drs": drs, "drs_behavior": drs_behavior},
            resource_label="Cluster",
        )
        return
    si, _ = _get_connection(target, config)
    result = create_cluster(
        si, cluster_name=name, datacenter_name=datacenter or None,
        ha_enabled=ha, drs_enabled=drs, drs_behavior=drs_behavior,
    )
    console.print(f"[green]{result}[/]")
    _audit.log(
        target=_resolve_target(target), operation="create_cluster",
        resource=name, parameters={"ha": ha, "drs": drs, "drs_behavior": drs_behavior},
        result=result,
    )


@cluster_app.command("delete")
@cli_errors
@guarded(risk_level='high')
def cluster_delete_cmd(
    name: str,
    target: TargetOption = None,
    config: ConfigOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Delete an empty cluster (destructive!)."""
    from vmware_aiops.ops.cluster_mgmt import delete_cluster, get_cluster_info

    si, _ = _get_connection(target, config)
    info = get_cluster_info(si, name)
    if dry_run:
        _dry_run_print(
            target=_resolve_target(target), vm_name=name, operation="delete_cluster",
            api_call="cluster.Destroy_Task()",
            before_state={"host_count": info["host_count"], "ha": info["ha_enabled"], "drs": info["drs_enabled"]},
            resource_label="Cluster",
        )
        return
    _double_confirm("删除集群", name, _resolve_target(target), resource_type="Cluster")
    result = delete_cluster(si, name)
    console.print(f"[green]{result}[/]")
    _audit.log(
        target=_resolve_target(target), operation="delete_cluster",
        resource=name, before_state=info, result=result,
    )


@cluster_app.command("add-host")
@cli_errors
@guarded(risk_level='medium')
def cluster_add_host_cmd(
    name: str,
    host: Annotated[str, typer.Option("--host", help="Host name to add")],
    target: TargetOption = None,
    config: ConfigOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Move a host into a cluster."""
    from vmware_aiops.ops.cluster_mgmt import add_host_to_cluster

    if dry_run:
        _dry_run_print(
            target=_resolve_target(target), vm_name=name, operation="cluster_add_host",
            api_call="cluster.MoveInto_Task()",
            parameters={"host": host},
            resource_label="Cluster",
        )
        return
    si, _ = _get_connection(target, config)
    _double_confirm("添加主机到集群", f"{host} → {name}", _resolve_target(target), resource_type="Host")
    result = add_host_to_cluster(si, cluster_name=name, host_name=host)
    console.print(f"[green]{result}[/]")
    _audit.log(
        target=_resolve_target(target), operation="cluster_add_host",
        resource=name, parameters={"host": host}, result=result,
    )


@cluster_app.command("remove-host")
@cli_errors
@guarded(risk_level='medium')
def cluster_remove_host_cmd(
    name: str,
    host: Annotated[str, typer.Option("--host", help="Host name to remove")],
    target: TargetOption = None,
    config: ConfigOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Remove a host from a cluster (host must be in maintenance mode)."""
    from vmware_aiops.ops.cluster_mgmt import remove_host_from_cluster

    if dry_run:
        _dry_run_print(
            target=_resolve_target(target), vm_name=name, operation="cluster_remove_host",
            api_call="datacenter.hostFolder.MoveIntoFolder_Task(list=[host])",
            parameters={"host": host},
            resource_label="Cluster",
        )
        return
    si, _ = _get_connection(target, config)
    _double_confirm("从集群移除主机", f"{host} ← {name}", _resolve_target(target), resource_type="Host")
    result = remove_host_from_cluster(si, cluster_name=name, host_name=host)
    console.print(f"[green]{result}[/]")
    _audit.log(
        target=_resolve_target(target), operation="cluster_remove_host",
        resource=name, parameters={"host": host}, result=result,
    )


@cluster_app.command("configure")
@cli_errors
@guarded(risk_level='medium')
def cluster_configure_cmd(
    name: str,
    ha: Annotated[bool | None, typer.Option("--ha/--no-ha", help="Enable/disable HA")] = None,
    drs: Annotated[bool | None, typer.Option("--drs/--no-drs", help="Enable/disable DRS")] = None,
    drs_behavior: Annotated[
        str, typer.Option("--drs-behavior", help="DRS behavior: fullyAutomated|partiallyAutomated|manual")
    ] = "",
    target: TargetOption = None,
    config: ConfigOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Configure cluster HA/DRS settings."""
    from vmware_aiops.ops.cluster_mgmt import configure_cluster, get_cluster_info

    params = {}
    if ha is not None:
        params["ha_enabled"] = ha
    if drs is not None:
        params["drs_enabled"] = drs
    if drs_behavior:
        params["drs_behavior"] = drs_behavior

    si, _ = _get_connection(target, config)
    if dry_run:
        before = get_cluster_info(si, name)
        _dry_run_print(
            target=_resolve_target(target), vm_name=name, operation="configure_cluster",
            api_call="cluster.ReconfigureComputeResource_Task()",
            parameters=params,
            before_state={"ha": before["ha_enabled"], "drs": before["drs_enabled"], "drs_behavior": before["drs_behavior"]},
            resource_label="Cluster",
        )
        return
    _double_confirm("重新配置集群", name, _resolve_target(target), resource_type="Cluster")
    result = configure_cluster(si, cluster_name=name, **params)
    console.print(f"[green]{result}[/]")
    _audit.log(
        target=_resolve_target(target), operation="configure_cluster",
        resource=name, parameters=params, result=result,
    )


# ─── DRS affinity / anti-affinity rules ──────────────────────────────────────


def _print_rule(rule: dict) -> None:
    """Render one rule summary from list_drs_rules / *_drs_rule ops output."""
    flags = "enabled" if rule.get("enabled") else "disabled"
    if rule.get("mandatory"):
        flags += ", mandatory"
    console.print(
        f"  [cyan]{rule['name']}[/] "
        f"[dim]({rule['type']}, {flags}, key={rule['key']})[/]"
    )
    if "vms" in rule:
        console.print(f"    vms: {', '.join(rule['vms']) or '(none)'}")
    if "vm_group" in rule:
        console.print(
            f"    vm_group: {rule['vm_group']}  "
            f"affine: {rule['affine_host_group'] or '-'}  "
            f"anti: {rule['anti_affine_host_group'] or '-'}"
        )


@cluster_app.command("drs-rules")
@cli_errors
def cluster_drs_rules_cmd(
    name: str,
    target: TargetOption = None,
    config: ConfigOption = None,
) -> None:
    """List a cluster's DRS rules (VM-VM affinity/anti-affinity and VM-Host)."""
    from vmware_aiops.ops.cluster_mgmt import list_drs_rules

    si, _ = _get_connection(target, config)
    out = list_drs_rules(si, name)
    console.print(f"\n[bold cyan]DRS rules on '{out['cluster']}' ({out['count']}):[/]")
    for rule in out["rules"]:
        _print_rule(rule)
    if not out["rules"]:
        console.print("  [dim](none)[/]")


@cluster_app.command("drs-rule-set")
@cli_errors
@guarded(risk_level='medium')
def cluster_drs_rule_set_cmd(
    name: str,
    rule_name: Annotated[str, typer.Option("--rule", help="Exact DRS rule name")],
    enabled: Annotated[bool, typer.Option("--enable/--disable", help="Enable or disable the rule")],
    target: TargetOption = None,
    config: ConfigOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Enable or disable an existing DRS rule (idempotent)."""
    from vmware_aiops.ops.cluster_mgmt import set_drs_rule_enabled

    si, _ = _get_connection(target, config)
    if dry_run:
        out = set_drs_rule_enabled(si, name, rule_name=rule_name, enabled=enabled, confirm=False)
        console.print(f"[magenta][DRY-RUN] {out['action']}: {out.get('hint', '')}[/]")
        return
    _double_confirm(
        "启用/禁用 DRS 规则", f"{rule_name} → {'enable' if enabled else 'disable'}",
        _resolve_target(target), resource_type="DRS rule",
    )
    out = set_drs_rule_enabled(si, name, rule_name=rule_name, enabled=enabled, confirm=True)
    console.print(f"[green]{out['action']}[/]: {out.get('hint', rule_name)}")
    if out.get("rule_now"):
        _print_rule(out["rule_now"])
    _audit.log(
        target=_resolve_target(target), operation="set_drs_rule_enabled",
        resource=f"{name}/{rule_name}", parameters={"enabled": enabled}, result=out["action"],
    )


@cluster_app.command("drs-rule-create")
@cli_errors
@guarded(risk_level='medium')
def cluster_drs_rule_create_cmd(
    name: str,
    rule_name: Annotated[str, typer.Option("--rule", help="Name for the new rule (unique on the cluster)")],
    rule_type: Annotated[str, typer.Option("--type", help="affinity | antiAffinity")],
    vms: Annotated[list[str], typer.Option("--vm", help="VM name (repeat for each; >=2, all in the cluster)")],
    disabled: Annotated[bool, typer.Option("--disabled", help="Create the rule disabled")] = False,
    target: TargetOption = None,
    config: ConfigOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Create a VM-VM DRS rule (affinity keeps VMs together, antiAffinity apart)."""
    from vmware_aiops.ops.cluster_mgmt import create_drs_rule

    si, _ = _get_connection(target, config)
    if dry_run:
        out = create_drs_rule(
            si, name, rule_name=rule_name, rule_type=rule_type,
            vm_names=vms, enabled=not disabled, confirm=False,
        )
        console.print(f"[magenta][DRY-RUN] {out['action']}: would create {out['would_create']}[/]")
        return
    _double_confirm(
        "创建 DRS 规则", f"{rule_name} ({rule_type})",
        _resolve_target(target), resource_type="DRS rule",
    )
    out = create_drs_rule(
        si, name, rule_name=rule_name, rule_type=rule_type,
        vm_names=vms, enabled=not disabled, confirm=True,
    )
    console.print(f"[green]{out['action']}[/]")
    _print_rule(out["created"])
    _audit.log(
        target=_resolve_target(target), operation="create_drs_rule",
        resource=f"{name}/{rule_name}",
        parameters={"type": rule_type, "vms": vms, "enabled": not disabled},
        result=out["action"],
    )


@cluster_app.command("drs-rule-delete")
@cli_errors
@guarded(risk_level='high')
def cluster_drs_rule_delete_cmd(
    name: str,
    rule_name: Annotated[str, typer.Option("--rule", help="Exact DRS rule name (VM-VM only)")],
    target: TargetOption = None,
    config: ConfigOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Delete a VM-VM DRS rule (refuses VM-Host rules; definition recorded for recreate)."""
    from vmware_aiops.ops.cluster_mgmt import delete_drs_rule

    si, _ = _get_connection(target, config)
    if dry_run:
        out = delete_drs_rule(si, name, rule_name=rule_name, confirm=False)
        console.print(f"[magenta][DRY-RUN] {out['action']}: would delete {out['would_delete']['rule']}[/]")
        return
    _double_confirm(
        "删除 DRS 规则", rule_name, _resolve_target(target), resource_type="DRS rule",
    )
    out = delete_drs_rule(si, name, rule_name=rule_name, confirm=True)
    console.print(f"[green]{out['action']}[/]: {rule_name}")
    _audit.log(
        target=_resolve_target(target), operation="delete_drs_rule",
        resource=f"{name}/{rule_name}",
        before_state=out["deleted"]["rule"], result=out["action"],
    )
