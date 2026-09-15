"""Cluster-health summary tool (read-only) — delegates to vmware-monitor.

The aggregation + ranking logic lives in the vmware-monitor package (a declared
dependency). AIops is the family's conversational entry point, so it re-exposes
the same one-glance triage here — using its own vCenter connection — rather than
forcing the user to switch skills. Read-only; no state is changed.
"""

from typing import Optional

from vmware_monitor.ops.attention import get_cross_vcenter_attention
from vmware_monitor.ops.cluster_summary import get_cluster_health_summary
from vmware_monitor.ops.investigate_datastore import get_datastore_investigation_bundle
from vmware_monitor.ops.investigate_host import get_host_investigation_bundle
from vmware_monitor.ops.investigate_vm import get_vm_investigation_bundle
from vmware_policy import vmware_tool

from vmware_aiops.mcp_server._shared import _ensure_conn_mgr, _get_connection, mcp, tool_errors


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
@vmware_tool(risk_level="low")
@tool_errors("dict")
def cluster_health_summary(
    target: Optional[str] = None,
    cluster_filter: Optional[str] = None,
    include_vms: bool = True,
    top_n: int = 10,
) -> dict:
    """[READ] One-glance health rollup for every cluster — "is anything on fire?".

    Aggregates hosts, VM power state, live CPU/memory pressure, triggered alarms and
    datastores thin-provisioned past 100% of capacity per cluster, assigns each a
    status ("ok"/"warn"/"critical"), and flattens the anomalies into a ranked
    ``top_issues`` focus list — the fast triage view for "what's wrong right now?".
    Read-only (delegates to the vmware-monitor library).
    Use it FIRST for a cross-cluster glance, then drill in with
    vm_investigation_bundle or host_investigation_bundle.

    Returns {totals, top_issues, issues_total, clusters, snapshot,
    customization_hint}. Lead with ``top_issues`` (worst first, each with a
    drill-down hint), ``clusters`` as context, ``customization_hint`` last.
    Point-in-time — no trending.

    Args:
        target: Optional vCenter/ESXi target name from config. Uses default if omitted.
        cluster_filter: Case-insensitive substring to show only matching clusters.
        include_vms: Roll up VM power counts (default True). False skips the VM
            pass on very large fleets.
        top_n: Cap the top_issues focus list (default 10; 0 hides it).
    """
    si = _get_connection(target)
    return get_cluster_health_summary(
        si, cluster_filter=cluster_filter, include_vms=include_vms, top_n=top_n
    )


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
@vmware_tool(risk_level="low")
@tool_errors("dict")
def vm_investigation_bundle(
    vm_name: str,
    target: Optional[str] = None,
    hours: int = 24,
) -> dict:
    """[READ] "What is happening around this VM?" — one correlated drill-down.

    Correlates everything around one VM so you don't stitch it together yourself:
    the VM's state, its host, cluster context, backing datastores, snapshots,
    triggered alarms, live performance, and a merged **event timeline** across VM,
    host, cluster and datastores (newest first). Batched, cheap even on large
    fleets. Delegates to the vmware-monitor library (read-only). Explain the result
    in operational language; do not dump it raw.

    Use this AFTER cluster_health_summary points at a problem VM. Point-in-time.

    Args:
        vm_name: Exact VM name. Unknown names return a teaching error (list VMs first).
        target: Optional vCenter/ESXi target name from config (default if omitted).
        hours: Event-timeline look-back window in hours (default 24).
    """
    si = _get_connection(target)
    return get_vm_investigation_bundle(si, vm_name, hours=hours)


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
@vmware_tool(risk_level="low")
@tool_errors("dict")
def host_investigation_bundle(
    host_name: str,
    target: Optional[str] = None,
    hours: int = 24,
) -> dict:
    """[READ] "What is happening around this ESXi host?" — one correlated drill-down.

    Correlates a host's state (connection, CPU/memory, ESXi version, uptime), its
    cluster context, a rollup of the VMs it runs, the datastores it mounts, alarms
    across host/cluster/datastore, live performance, and a merged **event timeline**.
    Delegates to the vmware-monitor library (read-only); do not dump the result raw.

    Use this AFTER cluster_health_summary flags a host. Point-in-time.

    Args:
        host_name: Exact host name. Unknown names return a teaching error.
        target: Optional vCenter/ESXi target name from config (default if omitted).
        hours: Event-timeline look-back window in hours (default 24).
    """
    si = _get_connection(target)
    return get_host_investigation_bundle(si, host_name, hours=hours)


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
@vmware_tool(risk_level="low")
@tool_errors("dict")
def datastore_investigation_bundle(
    datastore_name: str,
    target: Optional[str] = None,
    hours: int = 24,
) -> dict:
    """[READ] "What is happening around this datastore?" — one correlated drill-down.

    Correlates a datastore's capacity/free space/accessibility, the hosts that mount
    it, a rollup of the VMs it backs, alarms across datastore/host, and a merged
    **event timeline**. Delegates to the vmware-monitor library (read-only); do not
    dump the result raw. (Per-datastore latency is a separate perf report.)

    Use this AFTER cluster_health_summary flags storage pressure. Point-in-time.

    Args:
        datastore_name: Exact datastore name. Unknown names return a teaching error.
        target: Optional vCenter/ESXi target name from config (default if omitted).
        hours: Event-timeline look-back window in hours (default 24).
    """
    si = _get_connection(target)
    return get_datastore_investigation_bundle(si, datastore_name, hours=hours)


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
@vmware_tool(risk_level="low")
@tool_errors("dict")
def cross_vcenter_attention(
    cluster_filter: Optional[str] = None,
    top_n: int = 10,
) -> dict:
    """[READ] "What needs attention now?" across EVERY configured vCenter — one list.

    Runs cluster_health_summary against every configured target and returns one
    globally ranked ``top_issues`` list (worst first, each tagged with its
    ``vcenter``) plus a per-target rollup — "where do I look first, anywhere in the
    estate?". Degrades gracefully: an unreachable target is listed under
    ``unreachable`` and the rest still aggregate. Delegates to the vmware-monitor
    library (read-only). Lead with ``top_issues``, then drill in with
    vm_investigation_bundle. Point-in-time.

    Args:
        cluster_filter: Case-insensitive cluster substring applied to every target.
        top_n: Cap the merged top_issues focus list (default 10).
    """
    sessions, unreachable = _ensure_conn_mgr().connect_all()
    return get_cross_vcenter_attention(
        sessions, unreachable=unreachable, cluster_filter=cluster_filter, top_n=top_n
    )
