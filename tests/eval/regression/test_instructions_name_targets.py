"""Regression: the MCP instructions name the configured targets — in every branch.

``initialize`` hands the client the server's ``instructions``, and for a skill
whose every tool takes ``target`` that string is the only place the client learns
which targets exist. While it was static, the model called tools with no target,
got whatever the default happened to be, and answered confidently about the wrong
system: on 2026-09-15 the sibling read skill's default was a standalone ESXi host, so "how
many VMs does the vCenter have" was answered from that host, and an estate with a
vCenter was told it had none.

The listing was added then. What this file pins is the part that was still
missing on 2026-09-20: **the branch where the config cannot be read**. It
returned the base text with no listing at all — and a client shown no listing
cannot tell "this skill has no targets worth naming" from "this skill could not
read them". The first reading is the one that produces a confident answer about a
system nobody chose, and it is the state every customer is in on day one, before
they run ``init``. Silence is not an answer.
"""

from __future__ import annotations

import pytest

from vmware_aiops.config import AppConfig, TargetConfig
from vmware_aiops.mcp_server import _shared as server

LISTING_MARKER = "Configured targets:"
RULE_MARKER = "Choosing a target:"


def _two_targets() -> AppConfig:
    """A vCenter and a standalone host — the mix the 2026-09-15 defect needed."""
    return AppConfig(
        targets=[
            TargetConfig(
                name="prod-vcenter",
                host="vcenter.example.test",
                config_username="svc@vsphere.local",
                type="vcenter",
            ),
            TargetConfig(
                name="lab-esxi",
                host="esxi.example.test",
                config_username="root",
                type="esxi",
            ),
        ]
    )


@pytest.mark.unit
def test_the_listing_is_the_operators_own_targets(monkeypatch):
    monkeypatch.setattr(server, "load_config", _two_targets)
    text = server._target_instructions()

    assert "prod-vcenter (vcenter, vcenter.example.test, default)" in text
    assert "lab-esxi (esxi, esxi.example.test)" in text
    assert text.count(", default)") == 1, "only the default target is labelled default"
    assert LISTING_MARKER in text and RULE_MARKER in text


@pytest.mark.unit
def test_an_unreadable_config_is_said_out_loud_not_dropped(monkeypatch):
    """The branch a freshly installed machine actually takes."""

    def _no_config():
        raise FileNotFoundError("/Users/someone/.vmware-aiops/config.yaml")

    monkeypatch.setattr(server, "load_config", _no_config)
    text = server._target_instructions()

    assert LISTING_MARKER in text, "an absent listing reads as 'no targets', not 'unread'"
    assert "could not be read (FileNotFoundError)" in text
    assert "doctor" in text, "the reader needs the remedy, not just the fact"
    assert RULE_MARKER in text
    # The exception's text quotes the config path — the client is not shown it.
    assert "/Users/someone" not in text


@pytest.mark.unit
def test_a_config_with_no_targets_says_none_yet(monkeypatch):
    monkeypatch.setattr(server, "load_config", lambda: AppConfig(targets=[]))
    text = server._target_instructions()

    assert LISTING_MARKER in text
    assert "none yet" in text
    assert "Configured targets: ." not in text, "an empty listing must not render blank"


@pytest.mark.unit
def test_building_the_server_never_raises_on_a_broken_config(monkeypatch):
    """Instructions are advisory; startup is not. The tools report config errors."""

    def _broken():
        raise ValueError("config.yaml: mapping values are not allowed here")

    monkeypatch.setattr(server, "load_config", _broken)
    text = server._target_instructions()  # must not raise

    assert text and RULE_MARKER in text
