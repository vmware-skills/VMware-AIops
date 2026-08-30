"""A failed tool call must be a failure *in the MCP frame*, not only in prose.

Found on real hardware, 2026-08-30, in a round that probed the MCP surface
rather than the CLI. ``@tool_errors`` catches every exception and returns an
error payload, so the call returns normally and the lowlevel server builds
``CallToolResult(isError=False)``. Over stdio a client therefore cannot tell
"the VM does not exist" from "the VM was powered on": both arrive as a
successful call, and the only way to learn otherwise is to read the prose.

The catching itself is not the bug and is not being undone — raw exceptions
leak vSphere response bodies and host:port pairs (see ``_safe_error``), and the
teaching messages this family authored are the whole remedy an agent gets. What
was missing is that a failure *also* says so in the protocol frame.

How FastMCP surfaces the flag was established by reading the installed package
and driving it, not from memory (踩坑 #36). ``mcp`` 1.28.1:

* ``Tool.run`` re-raises any exception as ``ToolError``; the lowlevel
  ``call_tool`` handler catches it and returns ``isError=True`` with
  ``str(exc)`` as the only content — which would prefix every message with
  "Error executing tool <name>: " and drop the structured payload;
* ``FuncMetadata.convert_result`` passes a ``types.CallToolResult`` through
  untouched, and the lowlevel handler returns it verbatim, ``isError`` and all.

The second is what this skill uses, at the FastMCP boundary and *after*
FastMCP's own conversion has run, so the content and ``structuredContent`` are
byte-identical to what the same payload produced before — only the flag is new.

Every test here dispatches through the real ``CallToolRequest`` handler. A test
that called the tool function directly would prove nothing: the function's
return value was never the bug.

The controls matter as much as the assertions. A change that marked everything
an error would satisfy every failure test and make the flag useless, so a
successful call is pinned as ``isError=False`` beside each failure; and a change
that threw the message away to set the flag would be worse than the bug, so the
authored text is asserted alongside it every time.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import mcp.types as mcp_types
import pytest
from vmware_policy.budget import reset_budget
from vmware_policy.policy import reset_policy_engine
from vmware_policy.undo import reset_undo_store

import vmware_aiops.mcp_server.tools.cluster as cluster_tools
import vmware_aiops.mcp_server.tools.deploy as deploy_tools
import vmware_aiops.mcp_server.tools.plan as plan_tools
import vmware_aiops.mcp_server.tools.vm as vm_tools
from vmware_aiops.config import TargetConfig
from vmware_aiops.mcp_server._shared import _DOCTOR_HINT
from vmware_aiops.mcp_server.server import mcp

#: What a connection failure looks like on the way into a tool body.
DROPPED = "Connection to vcenter-prod dropped. Run 'vmware-aiops doctor'."


@pytest.fixture(autouse=True)
def harness(tmp_path, monkeypatch):
    """Point policy, budget and the undo store at a tmp dir, not the real ~/.vmware."""
    monkeypatch.setenv("OPS_HOME", str(tmp_path))
    reset_policy_engine()
    reset_budget()
    reset_undo_store()
    yield
    reset_policy_engine()
    reset_budget()
    reset_undo_store()


def call(tool: str, /, **arguments: Any) -> mcp_types.CallToolResult:
    """Dispatch through FastMCP's own request handler — the real wire path.

    Input validation, argument coercion, result conversion and frame
    construction all run here, which is the whole point: the defect lives in the
    frame, and nothing below this handler can observe it.
    """
    handler = mcp._mcp_server.request_handlers[mcp_types.CallToolRequest]
    request = mcp_types.CallToolRequest(
        method="tools/call",
        params=mcp_types.CallToolRequestParams(name=tool, arguments=arguments),
    )
    result = asyncio.run(handler(request)).root
    assert isinstance(result, mcp_types.CallToolResult), f"not a tool result: {result!r}"
    return result


def text_of(result: mcp_types.CallToolResult) -> str:
    """The text a client actually reads out of the frame."""
    return "\n".join(
        block.text for block in result.content if isinstance(block, mcp_types.TextContent)
    )


def _connection_fails(*_args: Any, **_kwargs: Any):
    raise ConnectionError(DROPPED)


def _connection_works(*_args: Any, **_kwargs: Any):
    return MagicMock()


# ── the three payload shapes ────────────────────────────────────────────────


def test_str_shaped_failure_is_flagged_and_keeps_its_teaching_text(monkeypatch) -> None:
    """``@tool_errors("str")`` covers every undo-bearing write in this skill."""
    monkeypatch.setattr(vm_tools, "_get_connection", _connection_fails)

    result = call("vm_power_on", vm_name="web-01", target="vcenter-prod")

    assert result.isError is True, "a failed power-on arrived as a successful call"
    body = text_of(result)
    assert DROPPED in body, "the teaching message was dropped setting the flag"
    assert _DOCTOR_HINT in body, "the remedy the payload carries must survive too"


def test_str_shaped_success_is_not_flagged(monkeypatch) -> None:
    """Control: marking everything an error would pass every test above."""
    monkeypatch.setattr(vm_tools, "_get_connection", _connection_works)
    monkeypatch.setattr(vm_tools, "power_on_vm", lambda si, name: f"VM '{name}' powered on.")

    result = call("vm_power_on", vm_name="web-01", target="vcenter-prod")

    assert result.isError is False
    assert "powered on" in text_of(result)


def test_dict_shaped_failure_is_flagged_and_keeps_its_payload(monkeypatch) -> None:
    monkeypatch.setattr(cluster_tools, "_get_connection", _connection_fails)

    result = call("cluster_info", name="prod-cluster", target="vcenter-prod")

    assert result.isError is True
    assert DROPPED in text_of(result)
    assert result.structuredContent is None or result.structuredContent.get("error")


def test_dict_shaped_success_is_not_flagged(monkeypatch) -> None:
    """Control for the dict shape."""
    monkeypatch.setattr(cluster_tools, "_get_connection", _connection_works)
    monkeypatch.setattr(
        "vmware_aiops.ops.cluster_mgmt.get_cluster_info",
        lambda si, name: {"name": name, "host_count": 3},
    )

    result = call("cluster_info", name="prod-cluster", target="vcenter-prod")

    assert result.isError is False
    assert "host_count" in text_of(result)


def test_list_shaped_failure_is_flagged_and_keeps_its_payload(monkeypatch) -> None:
    monkeypatch.setattr(deploy_tools, "_get_connection", _connection_fails)

    result = call(
        "batch_clone_vms",
        source_vm_name="golden",
        vm_names=["node-1", "node-2"],
        target="vcenter-prod",
    )

    assert result.isError is True
    assert DROPPED in text_of(result)


def test_list_shaped_success_is_not_flagged(monkeypatch) -> None:
    """Control for the list shape."""
    monkeypatch.setattr(deploy_tools, "_get_connection", _connection_works)
    monkeypatch.setattr(
        deploy_tools.vm_deploy,
        "batch_clone",
        lambda *a, **k: [{"name": "node-1", "status": "ok"}],
    )

    result = call(
        "batch_clone_vms",
        source_vm_name="golden",
        vm_names=["node-1"],
        target="vcenter-prod",
    )

    assert result.isError is False
    assert "node-1" in text_of(result)


# ── a failure that never raised ─────────────────────────────────────────────


def test_returned_error_envelope_is_flagged(monkeypatch) -> None:
    """Not every failure travels as an exception.

    ``apply_plan`` returns ``{"error": "Plan 'x' not found"}`` outright — no
    exception is raised, so ``@tool_errors`` never sees one. vmware-policy
    already treats a truthy top-level ``error`` as this family's documented
    failure envelope when it audits; the frame must agree with the audit row.
    """
    monkeypatch.setattr(plan_tools, "_get_connection", _connection_works)

    result = call("vm_apply_plan", plan_id="no-such-plan", target="vcenter-prod")

    assert result.isError is True, "a returned failure envelope still read as success"
    assert "no-such-plan" in text_of(result)


def test_falsy_error_key_is_not_a_failure(monkeypatch) -> None:
    """Control: ``{"error": None}`` is a result reporting that nothing broke.

    ``guest_provision`` returns exactly that on a clean run, so a rule keyed on
    the key's *presence* would mark every successful provision a failure.
    """
    monkeypatch.setattr(
        "vmware_aiops.mcp_server.tools.guest._get_connection", _connection_works
    )
    monkeypatch.setattr(
        "vmware_aiops.mcp_server.tools.guest.guest_provision",
        lambda *a, **k: {"success": True, "completed_steps": 1, "error": None},
    )

    result = call(
        "vm_guest_provision",
        vm_name="web-01",
        username="root",
        password="pw",
        steps=[{"type": "exec", "command": "true"}],
    )

    assert result.isError is False


# ── the message an earlier release deliberately authored ────────────────────


def test_the_missing_password_remedy_reaches_the_frame_unchanged(monkeypatch) -> None:
    """The family's most common first-run failure, worded on purpose.

    ``ConfigError`` names the env var, the ``.env`` path and the two commands
    that fix it. v1.8.4 was spent getting this text past ``_safe_error``; if
    flagging the frame costs it, the fix is a regression rather than a repair.

    The expected text is not typed out here — it is obtained by provoking the
    real ``TargetConfig.password`` failure, so a reworded remedy cannot pass by
    matching a stale copy of itself.
    """
    target = TargetConfig(
        name="vcenter-prod", host="vcenter.example.com", config_username="svc"
    )
    monkeypatch.delenv("VMWARE_VCENTER_PROD_PASSWORD", raising=False)
    with pytest.raises(OSError) as caught:  # noqa: PT011 — ConfigError is an OSError
        _ = target.password
    authored = str(caught.value)

    def _raise_authored(*_args: Any, **_kwargs: Any):
        raise caught.value

    monkeypatch.setattr(vm_tools, "_get_connection", _raise_authored)

    result = call("vm_power_on", vm_name="web-01", target="vcenter-prod")

    assert result.isError is True
    assert authored in text_of(result), "the authored remedy did not survive the frame"


# ── the rule itself agrees with the one vmware-policy audits by ─────────────


@pytest.mark.parametrize(
    "payload",
    [
        {"error": "boom"},
        {"error": "boom", "hint": "do the thing"},
        {"error": None},
        {"error": ""},
        {"ok": True},
        [{"error": "boom"}],
        [{"error": "boom"}, {"error": "boom"}],
        [{"name": "node-1", "status": "ok"}],
        [],
        "Error: boom",
        None,
        42,
    ],
)
def test_failure_envelope_rule_matches_vmware_policys(payload) -> None:
    """One convention, two enforcement points — kept honest mechanically.

    The audit status and the protocol flag must not be able to disagree about
    what a failure is. vmware-policy owns the rule and does not export it, so
    this skill carries its own copy; this test is the link that stops the two
    drifting (形态 #6). If the rule moves, it goes red here rather than silently
    producing frames that contradict the audit trail.
    """
    from vmware_policy.decorators import _returned_failure

    from vmware_aiops.mcp_server._shared import _is_error_envelope

    assert _is_error_envelope(payload) is _returned_failure(payload)
