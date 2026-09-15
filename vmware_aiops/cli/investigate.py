"""Object-centered investigation + cross-vCenter attention commands (read-only).

AIops is the family's conversational entry point, so it re-exposes the same
object-investigation drill-downs and cross-vCenter "what needs attention now?"
view the vmware-monitor CLI offers. The aggregation, correlation, and both
renderers (terminal + offline HTML) live in the vmware-monitor library, so these
commands are thin adapters over AIops's own vCenter connection — no logic or
rendering is duplicated.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from vmware_aiops.cli._common import (
    ConfigOption,
    TargetOption,
    _audit,
    _get_all_connections,
    _get_connection,
    cli_errors,
)
from vmware_policy import audited

investigate_app = typer.Typer(help="Object-centered investigation bundles (read-only).")

_Hours = Annotated[int, typer.Option("--hours", help="Event-timeline look-back window")]
_Html = Annotated[
    bool, typer.Option("--html", help="Write an offline HTML snapshot to ~/vmware-health/")
]
_HtmlPath = Annotated[
    Path | None,
    typer.Option("--html-path", help="Write the HTML snapshot to this exact path (implies --html)"),
]


def _tgt(target: str | None, cfg: object) -> str:
    return target or getattr(getattr(cfg, "default_target", None), "name", "default")


@investigate_app.command("vm")
@cli_errors
@audited("vm_investigation_bundle")
def investigate_vm_cmd(
    vm_name: Annotated[str, typer.Argument(help="Exact VM name to investigate")],
    hours: _Hours = 24,
    html: _Html = False,
    html_path: _HtmlPath = None,
    target: TargetOption = None,
    config: ConfigOption = None,
) -> None:
    """ "What is happening around this VM?" — correlated drill-down (via vmware-monitor)."""
    from vmware_monitor.cli_observability import render_bundle_console, write_bundle_html_snapshot
    from vmware_monitor.ops.investigate_vm import get_vm_investigation_bundle

    si, cfg = _get_connection(target, config)
    tgt = _tgt(target, cfg)
    bundle = get_vm_investigation_bundle(si, vm_name, hours=hours)
    _audit.log_query(
        target=tgt, resource=vm_name, query_type="vm_investigation_bundle", skill="aiops"
    )
    if html or html_path is not None:
        write_bundle_html_snapshot(bundle, "vm", tgt, html_path)
        return
    render_bundle_console(bundle, "vm")


@investigate_app.command("host")
@cli_errors
@audited("host_investigation_bundle")
def investigate_host_cmd(
    host_name: Annotated[str, typer.Argument(help="Exact ESXi host name to investigate")],
    hours: _Hours = 24,
    html: _Html = False,
    html_path: _HtmlPath = None,
    target: TargetOption = None,
    config: ConfigOption = None,
) -> None:
    """ "What is happening around this ESXi host?" — correlated drill-down (via vmware-monitor)."""
    from vmware_monitor.cli_observability import render_bundle_console, write_bundle_html_snapshot
    from vmware_monitor.ops.investigate_host import get_host_investigation_bundle

    si, cfg = _get_connection(target, config)
    tgt = _tgt(target, cfg)
    bundle = get_host_investigation_bundle(si, host_name, hours=hours)
    _audit.log_query(
        target=tgt, resource=host_name, query_type="host_investigation_bundle", skill="aiops"
    )
    if html or html_path is not None:
        write_bundle_html_snapshot(bundle, "host", tgt, html_path)
        return
    render_bundle_console(bundle, "host")


@investigate_app.command("datastore")
@cli_errors
@audited("datastore_investigation_bundle")
def investigate_datastore_cmd(
    datastore_name: Annotated[str, typer.Argument(help="Exact datastore name to investigate")],
    hours: _Hours = 24,
    html: _Html = False,
    html_path: _HtmlPath = None,
    target: TargetOption = None,
    config: ConfigOption = None,
) -> None:
    """ "What is happening around this datastore?" — correlated drill-down (via vmware-monitor)."""
    from vmware_monitor.cli_observability import render_bundle_console, write_bundle_html_snapshot
    from vmware_monitor.ops.investigate_datastore import get_datastore_investigation_bundle

    si, cfg = _get_connection(target, config)
    tgt = _tgt(target, cfg)
    bundle = get_datastore_investigation_bundle(si, datastore_name, hours=hours)
    _audit.log_query(
        target=tgt,
        resource=datastore_name,
        query_type="datastore_investigation_bundle",
        skill="aiops",
    )
    if html or html_path is not None:
        write_bundle_html_snapshot(bundle, "datastore", tgt, html_path)
        return
    render_bundle_console(bundle, "datastore")


@cli_errors
@audited("cross_vcenter_attention")
def attention_cmd(
    cluster: Annotated[
        str | None, typer.Option("--cluster", help="Show only clusters matching this substring")
    ] = None,
    top: Annotated[int, typer.Option("--top", help="Size of the merged top-issues list")] = 10,
    html: _Html = False,
    html_path: _HtmlPath = None,
    config: ConfigOption = None,
) -> None:
    """Cross-vCenter "what needs attention now?" — one ranked list across all targets."""
    from vmware_monitor.cli_observability import (
        render_attention_console,
        write_attention_html_snapshot,
    )
    from vmware_monitor.ops.attention import get_cross_vcenter_attention

    sessions, unreachable = _get_all_connections(config)
    data = get_cross_vcenter_attention(
        sessions, unreachable=unreachable, cluster_filter=cluster, top_n=top
    )
    _audit.log_query(
        target="*", resource="all-vcenters", query_type="cross_vcenter_attention", skill="aiops"
    )
    if html or html_path is not None:
        write_attention_html_snapshot(data, html_path)
        return
    render_attention_console(data)
