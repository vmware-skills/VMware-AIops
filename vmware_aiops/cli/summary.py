"""Cluster-health summary command (read-only) — delegates to vmware-monitor.

AIops is the family's conversational entry point, so it re-exposes the same
one-glance triage the vmware-monitor CLI offers. The aggregation and both
renderers (terminal + offline HTML) live in the vmware-monitor library, so this
command is a thin adapter over AIops's own vCenter connection — no logic or
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
    _get_connection,
    cli_errors,
)
from vmware_policy import audited


@cli_errors
@audited("cluster_health_summary")
def cluster_summary_cmd(
    cluster: Annotated[
        str | None,
        typer.Option("--cluster", help="Show only clusters matching this substring"),
    ] = None,
    no_vms: Annotated[
        bool,
        typer.Option("--no-vms", help="Skip the VM rollup pass (faster on huge fleets)"),
    ] = False,
    top: Annotated[
        int,
        typer.Option("--top", help="Size of the top-issues focus list (0 to hide)"),
    ] = 10,
    html: Annotated[
        bool,
        typer.Option(
            "--html", help="Write an offline HTML snapshot to ~/vmware-health/ (timestamped)"
        ),
    ] = False,
    html_path: Annotated[
        Path | None,
        typer.Option(
            "--html-path", help="Write the HTML snapshot to this exact path (implies --html)"
        ),
    ] = None,
    target: TargetOption = None,
    config: ConfigOption = None,
) -> None:
    """One-glance cluster health: is anything on fire?

    Leads with the top-N individual anomalies, then an opinionated per-cluster
    table. Same view as `vmware-monitor summary` — the logic is shared. Pass
    --html for an offline, timestamped snapshot file.
    """
    from vmware_monitor.cli_observability import render_summary_console, write_html_snapshot
    from vmware_monitor.ops.cluster_summary import get_cluster_health_summary

    si, cfg = _get_connection(target, config)
    tgt = target or getattr(getattr(cfg, "default_target", None), "name", "default")
    data = get_cluster_health_summary(si, cluster_filter=cluster, include_vms=not no_vms, top_n=top)
    _audit.log_query(
        target=tgt, resource="clusters", query_type="cluster_health_summary", skill="aiops"
    )

    if html or html_path is not None:
        write_html_snapshot(data, tgt, html_path)
        return
    render_summary_console(data, top)
