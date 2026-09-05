"""A dropped connection must not stay alive in the atexit registry.

Every ``_create_connection`` registers a cleanup that closes over the
ServiceInstance, and ``atexit`` holds that closure -- and so the SI -- until the
process exits. Nothing unregistered it, so a long-running MCP server that
reconnects after each session expiry (踩坑 #40) accumulated one dead
ServiceInstance per reconnect, and at exit would run a Disconnect against every
session it had ever opened. The id(si) side stores were correctly down to one
entry the whole time -- the side-store discipline was never the leak.

Reachability is the assertion, not ``atexit._ncallbacks()``. That counter does
not decrease on unregister: a subprocess check shows an unregistered handler is
genuinely never called while ``_ncallbacks()`` still reports it. Asserting on it
would have measured nothing in either direction.

Only ``SmartConnect`` is patched, so the production registration runs. A test
that registered its own handler would pass with the production one deleted.
"""

from __future__ import annotations

import gc
import weakref
from unittest.mock import MagicMock, patch

import pytest

from vmware_aiops.config import TargetConfig
from vmware_aiops.connection import ConnectionManager, _release_si


@pytest.fixture(autouse=True)
def _password(monkeypatch):
    """The connection layer resolves the password before it dials."""
    # Two spellings because the family has two: most skills read
    # VMWARE_<TARGET>_PASSWORD, PrivateAI and VKS read
    # VMWARE_<SKILL>_<TARGET>_PASSWORD. Setting both keeps this test portable.
    monkeypatch.setenv("VMWARE_VCENTER_PROD_PASSWORD", "test-only")
    monkeypatch.setenv("VMWARE_AIOPS_VCENTER_PROD_PASSWORD", "test-only")


def _target(name: str = "vcenter-prod") -> TargetConfig:
    return TargetConfig(name=name, host="vc.example.com", config_username="svc")


def _dead_si(*_a, **_kw):
    """An SI whose liveness probe says dead, so the next connect() evicts it."""
    si = MagicMock()
    si.content.sessionManager.currentSession = None
    return si


@pytest.mark.unit
def test_a_released_connection_is_collectable() -> None:
    """The mechanism: what connect() registers, _release_si takes back."""
    with patch("pyVim.connect.SmartConnect", side_effect=_dead_si):
        si = ConnectionManager._create_connection(_target())
    ref = weakref.ref(si)

    _release_si(si)
    del si
    gc.collect()
    assert ref() is None, "atexit still holds the connection after _release_si"


@pytest.mark.unit
def test_a_connection_not_released_is_pinned() -> None:
    """The negative half. Without this, the test above could pass because
    nothing ever held the object -- and would then keep passing with the whole
    registration deleted."""
    with patch("pyVim.connect.SmartConnect", side_effect=_dead_si):
        si = ConnectionManager._create_connection(_target())
    ref = weakref.ref(si)

    del si
    gc.collect()
    assert ref() is not None, "connect() no longer registers an atexit cleanup at all"
    _release_si(ref())  # leave the registry clean for the rest of the suite


@pytest.mark.unit
def test_repeated_eviction_does_not_pin_every_session() -> None:
    """The shape that actually bites: a session that keeps expiring."""
    cfg = MagicMock()
    cfg.get_target.return_value = _target()
    cfg.default_target = _target()
    mgr = ConnectionManager(cfg)

    seen: list[weakref.ref] = []
    with patch("pyVim.connect.SmartConnect", side_effect=_dead_si), \
         patch("pyVim.connect.Disconnect"):
        for _ in range(20):
            seen.append(weakref.ref(mgr.connect("vcenter-prod")))

    live = mgr._connections.pop("vcenter-prod", None)
    if live is not None:
        _release_si(live)
        del live
    gc.collect()

    pinned = sum(1 for r in seen if r() is not None)
    assert pinned == 0, (
        f"{pinned} of 20 evicted ServiceInstance objects are still reachable — "
        "each reconnect is leaking its session into the atexit registry"
    )
