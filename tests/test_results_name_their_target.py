"""Every result must say which target answered, as vmware-monitor's do since 1.15.0.

2026-09-16 scenario tests: asked for a VM's investigation bundle and to say where
the data came from, this server's answer named `home-vcenter` — but the tool
result carried no target at all. The model had read the name off the argument it
had chosen itself, so a call that omits `target` (the common case) gives an
answer whose source cannot be told from the payload: a standalone ESXi host and
a vCenter look identical.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from vmware_aiops.config import AppConfig, TargetConfig
from vmware_aiops.mcp_server import _shared

# Importing the server is what registers the tools onto the shared instance;
# _shared alone builds an empty registry, and the two checks below would then
# pass over nothing.
from vmware_aiops.mcp_server import server as _server  # noqa: F401

ESXI = TargetConfig(name="home-esxi", host="192.0.2.15", config_username="root", type="esxi")
VC = TargetConfig(name="home-vcenter", host="192.0.2.16", config_username="admin", type="vcenter")


@pytest.fixture
def config(monkeypatch):
    cfg = AppConfig(targets=(VC, ESXI))

    class _Mgr:
        _config = cfg

    monkeypatch.setattr(_shared, "_ensure_conn_mgr", lambda: _Mgr())
    return cfg


class TestStamping:
    def test_a_named_target_is_reported(self, config):
        tool = _shared._with_target(lambda target=None: {"items": []})
        assert tool(target="home-esxi")["target"] == {"name": "home-esxi", "type": "esxi"}

    def test_an_omitted_target_reports_the_default(self, config):
        tool = _shared._with_target(lambda target=None: {"items": []})
        assert tool()["target"] == {"name": "home-vcenter", "type": "vcenter"}

    def test_a_tool_without_a_target_parameter_is_untouched(self, config):
        def fn(limit=None):
            return {"items": []}

        assert _shared._with_target(fn) is fn

    def test_a_non_dict_result_passes_through(self, config):
        tool = _shared._with_target(lambda target=None: ["a", "b"])
        assert tool(target="home-esxi") == ["a", "b"]

    def test_a_raw_string_under_that_key_is_the_argument_echoed_back(self, config):
        """So it is replaced, not preserved.

        This test asserted the opposite until an independent review pointed out
        what the preserved value actually was: `create_plan` and `apply_plan`
        return the raw `target` argument — `None` when the call omitted it — so
        one key held a string, a null and a `{name, type}` across sibling tools,
        and a call that did reach a target answered `target: null`.
        """
        tool = _shared._with_target(lambda target=None: {"target": "home-esxi"})
        assert tool(target="home-esxi")["target"] == {"name": "home-esxi", "type": "esxi"}

    def test_naming_the_target_never_breaks_the_answer(self, monkeypatch):
        """A broken config must cost the label, not the result."""

        def boom():
            raise FileNotFoundError("no config")

        monkeypatch.setattr(_shared, "_ensure_conn_mgr", boom)
        tool = _shared._with_target(lambda target=None: {"items": []})
        assert tool(target="home-esxi") == {"items": []}

    def test_an_error_payload_still_says_which_target_was_tried(self, config):
        tool = _shared._with_target(lambda target=None: {"error": "connect failed"})
        result = tool(target="home-esxi")
        assert result["error"] == "connect failed"
        assert result["target"]["name"] == "home-esxi"


class TestShapesBeyondAPlainDict:
    """Independent review, 2026-09-16: the first version stamped dicts only."""

    def test_a_list_of_dicts_is_stamped_element_by_element(self, config):
        tool = _shared._with_target(lambda target=None: [{"vm": "a"}, {"vm": "b"}])
        result = tool(target="home-esxi")
        assert [row["vm"] for row in result] == ["a", "b"]
        assert all(row["target"] == {"name": "home-esxi", "type": "esxi"} for row in result)

    def test_a_list_of_non_dicts_passes_through(self, config):
        tool = _shared._with_target(lambda target=None: ["vm-a", "vm-b"])
        assert tool(target="home-esxi") == ["vm-a", "vm-b"]

    def test_a_string_result_is_left_exactly_as_it_was(self, config):
        """The write tools answer prose. Rewriting it to carry a label would
        change 24 tools' output contract; the server instructions carry the
        rule for those instead, and the release notes say so."""
        tool = _shared._with_target(lambda target=None: "Powered on VM 'web-01'.")
        assert tool(target="home-esxi") == "Powered on VM 'web-01'."

    def test_a_raw_target_string_is_replaced_by_the_resolved_one(self, config):
        """`create_plan`/`apply_plan` already return a top-level `target`: the raw
        argument. Leaving it made one key hold two types across sibling tools."""
        tool = _shared._with_target(lambda target=None: {"plan_id": "p1", "target": "home-esxi"})
        result = tool(target="home-esxi")
        assert result["target"] == {"name": "home-esxi", "type": "esxi"}
        assert result["plan_id"] == "p1"

    def test_a_null_target_is_replaced_by_the_default(self, config):
        """`target: None` reads as "no target", which is never true of an answer."""
        tool = _shared._with_target(lambda target=None: {"plan_id": "p1", "target": None})
        assert tool()["target"] == {"name": "home-vcenter", "type": "vcenter"}

    def test_an_already_resolved_target_is_not_overwritten(self, config):
        tool = _shared._with_target(
            lambda target=None: {"target": {"name": "spelled-by-the-tool", "type": "vcenter"}}
        )
        assert tool(target="home-esxi")["target"]["name"] == "spelled-by-the-tool"

    def test_an_async_tool_is_stamped_too(self, config):
        async def fn(target=None):
            return {"items": []}

        tool = _shared._with_target(fn)
        assert inspect.iscoroutinefunction(tool)
        assert asyncio.run(tool(target="home-esxi"))["target"]["name"] == "home-esxi"


class TestTheInstructionsCarryTheRule:
    """The payload now holds the fact; something has to tell the model to use it.

    vmware-monitor 1.15.0 shipped both halves. Only the payload half was copied
    here at first, and the failure being fixed was a *reporting* failure.
    """

    def test_the_configured_targets_and_the_rule_are_listed(self, monkeypatch):
        cfg = AppConfig(targets=(VC, ESXI))
        monkeypatch.setattr(_shared, "load_config", lambda: cfg)
        text = _shared._target_instructions()
        assert "home-vcenter (vcenter, 192.0.2.16, default)" in text
        assert "home-esxi (esxi, 192.0.2.15)" in text
        assert "ask the user" in text

    def test_a_broken_config_still_yields_instructions(self, monkeypatch):
        def boom():
            raise FileNotFoundError("no config")

        monkeypatch.setattr(_shared, "load_config", boom)
        assert "Choosing a target" in _shared._target_instructions()

    def test_the_server_uses_them(self):
        assert "Choosing a target" in (_shared.mcp.instructions or "")


class TestEveryRegisteredToolIsWrapped:
    def test_every_target_taking_tool_names_its_target(self):
        tools = _shared.mcp._tool_manager._tools
        taking_target = [
            name for name, t in tools.items() if "target" in inspect.signature(t.fn).parameters
        ]
        assert taking_target, "no registered tool takes target — the check would be vacuous"
        unwrapped = [
            name for name in taking_target if not getattr(tools[name].fn, "_names_target", False)
        ]
        assert unwrapped == []

    def test_the_wrapper_does_not_change_any_tool_schema(self):
        """The label is added to results, never to the call signature."""
        tools = asyncio.run(_shared.mcp.list_tools())
        assert len(tools) >= 60
        for tool in tools:
            params = (tool.inputSchema or {}).get("properties", {})
            signature = inspect.signature(_shared.mcp._tool_manager._tools[tool.name].fn)
            assert set(params) == {
                p
                for p in signature.parameters
                if not p.startswith("_") and p not in ("args", "kwargs")
            }, tool.name
