"""Monitor's missing-object remedies must survive the AIops MCP error wrapper.

Found on real vCenter, 2026-11-08: investigate_vm reduced Monitor's authored
VMNotFoundError to a generic failure and incorrectly suggested checking login.
The host and datastore delegates use different authored exception classes too.
"""
from __future__ import annotations

import asyncio
import importlib
import json

import mcp.types as mcp_types
import pytest
from vmware_policy.policy import reset_policy_engine

from vmware_aiops.mcp_server.server import mcp
from vmware_aiops.mcp_server.tools import summary


@pytest.fixture(autouse=True)
def policy_home(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_HOME", str(tmp_path))
    reset_policy_engine()
    yield
    reset_policy_engine()


def call(tool, arguments):
    handler = mcp._mcp_server.request_handlers[mcp_types.CallToolRequest]
    request = mcp_types.CallToolRequest(
        method="tools/call",
        params=mcp_types.CallToolRequestParams(name=tool, arguments=arguments),
    )
    result = asyncio.run(handler(request)).root
    text = "".join(b.text for b in result.content if isinstance(b, mcp_types.TextContent))
    return result, json.loads(text)


@pytest.mark.parametrize(("kind", "lookup", "listing"), [
    ("vm", "find_vm_by_name", "list_virtual_machines"),
    ("host", "find_host_by_name", "list_esxi_hosts"),
    ("datastore", "find_datastore_by_name", "list_all_datastores"),
])
def test_missing_delegate_object_keeps_its_real_remedy(monkeypatch, kind, lookup, listing):
    # Exercise the real Monitor raise site, not a reconstructed exception.
    module = importlib.import_module(f"vmware_monitor.ops.investigate_{kind}")
    monkeypatch.setattr(module, lookup, lambda *_args: None)
    monkeypatch.setattr(summary, "_get_connection", lambda *_args: object())
    result, payload = call(f"investigate_{kind}", {f"{kind}_name": "missing-\x00object"})
    assert result.isError is True
    assert "not found" in payload["error"]
    assert listing in payload["error"]
    assert "missing-object" in payload["error"]
    assert "\x00" not in payload["error"]
    assert "doctor" not in payload["hint"]


@pytest.mark.parametrize("error_type", [RuntimeError, type("VMNotFoundError", (Exception,), {})])
def test_unplanned_delegate_errors_are_still_reduced(monkeypatch, error_type):
    def fail(*_args, **_kwargs):
        raise error_type("https://svc:secret-example@internal.invalid/api")

    monkeypatch.setattr(summary, "_get_connection", lambda *_args: object())
    monkeypatch.setattr(summary, "get_vm_investigation_bundle", fail)
    result, payload = call("investigate_vm", {"vm_name": "missing-object"})
    assert result.isError is True
    assert payload["error"] == f"{error_type.__name__}: operation failed."
    assert "secret-example" not in str(payload)
    assert "doctor" in payload["hint"]
