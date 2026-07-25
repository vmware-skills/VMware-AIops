"""Regression — AIops re-exposes cluster-health summary by delegating to monitor.

AIops is the family's conversational entry point, so "what's wrong in my
environment?" must work from an AIops conversation too. The aggregation lives in
vmware-monitor (a dependency); AIops registers a thin ``cluster_health_summary``
MCP tool and ``summary`` CLI command that call it with AIops's own connection —
no logic is duplicated. These tests lock that wiring so a refactor on either side
cannot silently drop it.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from typer.testing import CliRunner

from vmware_aiops.cli import app

_SAMPLE = {
    "totals": {
        "clusters": 1,
        "hosts_total": 2,
        "hosts_connected": 1,
        "vms_total": 10,
        "vms_on": 8,
        "alarms": {"critical": 1, "warning": 0},
        "worst_status": "critical",
    },
    "top_issues": [
        {
            "severity": "critical",
            "kind": "host_down",
            "object": "esx-9",
            "scope": "host",
            "cluster": "prod",
            "detail": "host notResponding",
            "drilldown": "inventory hosts",
        },
    ],
    "issues_total": 1,
    "clusters": [
        {
            "name": "prod",
            "hosts_total": 2,
            "hosts_connected": 1,
            "vms_total": 10,
            "vms_on": 8,
            "cpu_used_pct": 40,
            "mem_used_pct": 50,
            "ha_enabled": True,
            "drs_enabled": True,
            "alarms": {"critical": 1, "warning": 0},
            "status": "critical",
            "attention": ["1 host disconnected"],
        },
    ],
    "snapshot": "point-in-time",
    "customization_hint": "hint",
}


class _Cfg:
    class default_target:  # noqa: N801 - stub matching config shape
        name = "prod-vc"


def test_mcp_tool_registered_and_read_only() -> None:
    """The delegated summary tool is present on the AIops MCP server."""
    from vmware_aiops.mcp_server.server import mcp

    tools = {t.name for t in asyncio.run(mcp.list_tools())}
    assert "cluster_health_summary" in tools, "AIops must re-expose cluster_health_summary"


def test_mcp_tool_delegates_to_monitor() -> None:
    """The tool calls the vmware-monitor aggregation with AIops's connection."""
    from vmware_aiops.mcp_server.tools import summary as tool_mod

    with (
        patch.object(tool_mod, "_get_connection", return_value=object()) as conn,
        patch.object(tool_mod, "get_cluster_health_summary", return_value=_SAMPLE) as agg,
    ):
        out = tool_mod.cluster_health_summary(target="prod-vc", top_n=5)
    assert out is _SAMPLE
    conn.assert_called_once()
    # include_vms + top_n threaded through to the monitor function.
    assert agg.call_args.kwargs["top_n"] == 5
    assert agg.call_args.kwargs["include_vms"] is True


def test_investigation_tools_registered() -> None:
    """AIops re-exposes the object-investigation family + cross-vCenter attention."""
    from vmware_aiops.mcp_server.server import mcp

    tools = {t.name for t in asyncio.run(mcp.list_tools())}
    for name in (
        "vm_investigation_bundle",
        "host_investigation_bundle",
        "datastore_investigation_bundle",
        "cross_vcenter_attention",
    ):
        assert name in tools, f"AIops must re-expose {name}"


def test_vm_bundle_delegates_to_monitor() -> None:
    """vm_investigation_bundle calls the monitor aggregation with AIops's connection."""
    from vmware_aiops.mcp_server.tools import summary as tool_mod

    sentinel = {"object": {"name": "web-01"}}
    with (
        patch.object(tool_mod, "_get_connection", return_value=object()) as conn,
        patch.object(tool_mod, "get_vm_investigation_bundle", return_value=sentinel) as agg,
    ):
        out = tool_mod.vm_investigation_bundle(vm_name="web-01", hours=48)
    assert out is sentinel
    conn.assert_called_once()
    assert agg.call_args.kwargs["hours"] == 48


def test_attention_delegates_via_connect_all() -> None:
    """cross_vcenter_attention resolves all targets and delegates to monitor."""
    from vmware_aiops.mcp_server.tools import summary as tool_mod

    mgr = type("_Mgr", (), {"connect_all": lambda self: ([("prod", object())], [("dr", "TimeoutError")])})()
    sentinel = {"targets": [], "top_issues": []}
    with (
        patch.object(tool_mod, "_ensure_conn_mgr", return_value=mgr),
        patch.object(tool_mod, "get_cross_vcenter_attention", return_value=sentinel) as agg,
    ):
        out = tool_mod.cross_vcenter_attention(top_n=5)
    assert out is sentinel
    # sessions + unreachable from connect_all threaded through to the aggregator.
    assert agg.call_args.kwargs["unreachable"] == [("dr", "TimeoutError")]
    assert agg.call_args.kwargs["top_n"] == 5


def test_cli_investigate_vm_delegates(tmp_path) -> None:
    """`vmware-aiops investigate vm` renders a monitor bundle; --html writes offline."""
    bundle = {
        "object": {"name": "web-01", "status": "yellow"},
        "host": {"name": "esxi-9", "connection": "connected", "cpu_pct": 40, "mem_pct": 55},
        "cluster": None,
        "datastores": [],
        "snapshots": [],
        "alarms": [],
        "performance": {"note": "off"},
        "timeline": [],
        "stats": [{"k": "Power", "v": "poweredOn"}],
        "hours": 24,
        "snapshot": "point-in-time",
        "customization_hint": "hint",
    }
    out = tmp_path / "vm.html"
    with (
        patch("vmware_aiops.cli.investigate._get_connection", return_value=(object(), _Cfg())),
        patch(
            "vmware_monitor.ops.investigate_vm.get_vm_investigation_bundle", return_value=bundle
        ),
        patch("vmware_aiops.cli.investigate._audit"),
    ):
        term = CliRunner().invoke(app, ["investigate", "vm", "web-01"])
        assert term.exit_code == 0, term.output
        assert "web-01" in term.output
        html = CliRunner().invoke(app, ["investigate", "vm", "web-01", "--html-path", str(out)])
        assert html.exit_code == 0, html.output
    assert out.read_text(encoding="utf-8").startswith("<!doctype html>")


def test_cli_attention_delegates(tmp_path) -> None:
    """`vmware-aiops attention` connects to all targets and renders the merged view."""
    data = {
        "targets": [
            {
                "vcenter": "prod",
                "worst_status": "ok",
                "clusters": 1,
                "hosts_connected": 1,
                "hosts_total": 1,
                "alarms": {"critical": 0, "warning": 0},
            }
        ],
        "top_issues": [],
        "issues_total": 0,
        "totals": {
            "vcenters": 1,
            "clusters": 1,
            "hosts_total": 1,
            "hosts_connected": 1,
            "alarms": {"critical": 0, "warning": 0},
            "worst_status": "ok",
        },
        "unreachable": [],
        "snapshot": "point-in-time",
        "customization_hint": "hint",
    }
    with (
        patch(
            "vmware_aiops.cli.investigate._get_all_connections",
            return_value=([("prod", object())], []),
        ),
        patch("vmware_monitor.ops.attention.get_cross_vcenter_attention", return_value=data),
        patch("vmware_aiops.cli.investigate._audit"),
    ):
        term = CliRunner().invoke(app, ["attention"])
        assert term.exit_code == 0, term.output
        assert "prod" in term.output


def test_cli_summary_terminal_and_html(tmp_path) -> None:
    """`vmware-aiops summary` renders via monitor; --html writes an offline file."""
    out = tmp_path / "snap.html"
    with (
        patch("vmware_aiops.cli.summary._get_connection", return_value=(object(), _Cfg())),
        patch(
            "vmware_monitor.ops.cluster_summary.get_cluster_health_summary", return_value=_SAMPLE
        ),
        patch("vmware_aiops.cli.summary._audit"),
    ):
        term = CliRunner().invoke(app, ["summary"])
        assert term.exit_code == 0, term.output
        assert "Top 1 issues" in term.output and "prod" in term.output

        html = CliRunner().invoke(app, ["summary", "--html-path", str(out)])
        assert html.exit_code == 0, html.output
    assert out.exists()
    assert out.read_text(encoding="utf-8").startswith("<!doctype html>")
