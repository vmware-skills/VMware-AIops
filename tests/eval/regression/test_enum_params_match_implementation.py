"""Closed-value parameters must carry their values in the MCP schema.

A tool parameter with a fixed set of valid values used to be typed `str` with
the values named only in prose. An MCP client sees the schema, not the prose, so
a model had to guess the spelling -- and guessing wrong did not raise. It
silently did something else:

  * vmware-monitor ranked an unrecognised severity with `SEVERITY_ORDER.get(sev, 1)`,
    so a typo filtered at warning level and returned a plausible, wrong list.
  * vmware-nsx chose a gateway with `"tier-0s" if gateway_type == "tier0" else "tier-1s"`,
    so anything not spelled exactly `tier0` silently addressed **tier1** --
    including on `delete_static_route`.

`Literal[...]` puts the values in the schema, where the client rejects a bad one
before it reaches any of that.

This gate pins each enum against the set the implementation actually accepts, so
the two cannot drift. The NSX case is why that matters: `create_nat_rule` accepts
six actions and its docstring named three, so an enum copied from the prose would
have turned a documentation gap into a hard rejection of three working values.
"""

from __future__ import annotations

import asyncio

import pytest

from vmware_aiops.mcp_server.server import mcp

from vmware_aiops.ops.cluster_mgmt import _CREATABLE_RULE_TYPES, _VALID_DRS_BEHAVIORS
from vmware_aiops.ops.network_mgmt import _VALID_BINDINGS

EXPECTED = [
    ("cluster_create", "drs_behavior", sorted(_VALID_DRS_BEHAVIORS)),
    ("cluster_configure", "drs_behavior", sorted(_VALID_DRS_BEHAVIORS)),
    ("create_drs_rule", "rule_type", sorted(_CREATABLE_RULE_TYPES)),
    ("create_dvs_portgroup", "binding", sorted(_VALID_BINDINGS)),
]


@pytest.mark.unit
def test_every_closed_value_parameter_declares_its_values() -> None:
    tools = {t.name: t for t in asyncio.run(mcp.list_tools())}
    problems = []
    for tool_name, param, expected in EXPECTED:
        tool = tools.get(tool_name)
        if tool is None:
            problems.append(f"{tool_name}: tool is gone; delete or update this entry")
            continue
        spec = (tool.inputSchema or {}).get("properties", {}).get(param)
        if spec is None:
            problems.append(f"{tool_name}.{param}: parameter is gone")
            continue
        got = spec.get("enum") or next(
            (b.get("enum") for b in spec.get("anyOf", []) if b.get("enum")), None
        )
        if got is None:
            problems.append(
                f"{tool_name}.{param}: no enum in the schema -- an agent has to guess "
                f"the spelling, and guessing wrong does not raise here"
            )
        elif set(got) != set(expected):
            problems.append(
                f"{tool_name}.{param}: schema enum {sorted(got)} != what the "
                f"implementation accepts {sorted(expected)}"
            )
    assert not problems, "\n".join(problems)


@pytest.mark.unit
def test_the_gate_is_wired_to_something() -> None:
    """A gate over an empty list passes forever while checking nothing."""
    assert EXPECTED, "no parameters listed -- this gate would be vacuous"
