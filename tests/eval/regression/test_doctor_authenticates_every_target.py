"""The doctor must authenticate every configured target, not just the first.

Real-hardware finding, 2026-08-30. The tester configured five targets and put
the wrong password on three of them. `doctor` printed **✓ All checks passed**,
because `_check_auth` logged into `config.default_target` and stopped. The very
next call failed on authentication, and its error message told the user to run
the doctor — the one that had just cleared them.

Four skills in the family shared this; three others (Aria, NSX, VKS) already
iterated. It is the family's most-repeated shape: a pattern fixed in one repo
and left standing in the rest (CLAUDE.md 形态 #7).

One row is kept rather than one per target, matching the neighbouring
connectivity check's format, so the table shape is unchanged — but the row now
names every target and fails if any of them does.
"""

from __future__ import annotations

import types

import pytest

from vmware_aiops import doctor as doc


def _targets(*names):
    return types.SimpleNamespace(
        targets=tuple(types.SimpleNamespace(name=n) for n in names),
        default_target=types.SimpleNamespace(name=names[0]) if names else None,
    )


def _install(monkeypatch, config, failures=()):
    """Patch config + connection so `connect(name)` raises for names in `failures`."""
    class _Mgr:
        def __init__(self, cfg):
            pass

        def connect(self, name=None):
            if name in failures:
                raise OSError(f"Cannot complete login to {name}")
            return object()

        def disconnect_all(self):
            pass

    monkeypatch.setattr("vmware_aiops.config.load_config", lambda: config)
    monkeypatch.setattr("vmware_aiops.connection.ConnectionManager", _Mgr)


@pytest.mark.unit
def test_a_bad_password_on_a_non_default_target_fails_the_check(monkeypatch):
    monkeypatch.setattr(type(doc.CONFIG_FILE), "exists", lambda self: True)
    _install(monkeypatch, _targets("lab", "prod", "dr"), failures={"prod"})

    ok, message = doc._check_auth()

    assert ok is False, (
        "the doctor cleared an estate with a target it cannot log into; the "
        "next call fails and its error sends the user back here"
    )
    assert "prod" in message
    assert "Cannot complete login" in message


@pytest.mark.unit
def test_every_target_is_named_in_the_result(monkeypatch):
    monkeypatch.setattr(type(doc.CONFIG_FILE), "exists", lambda self: True)
    _install(monkeypatch, _targets("lab", "prod", "dr"))

    ok, message = doc._check_auth()

    assert ok is True
    for name in ("lab", "prod", "dr"):
        assert name in message, (
            f"{name} is not mentioned, so a reader cannot tell it was checked "
            f"from it being skipped"
        )


@pytest.mark.unit
def test_one_failure_does_not_hide_the_others(monkeypatch):
    """Aborting on the first bad target would report one problem and leave the
    operator to discover the rest one call at a time."""
    monkeypatch.setattr(type(doc.CONFIG_FILE), "exists", lambda self: True)
    _install(monkeypatch, _targets("a", "b", "c"), failures={"a", "c"})

    ok, message = doc._check_auth()

    assert ok is False
    assert "a" in message and "b" in message and "c" in message
    assert message.count("Cannot complete login") == 2


@pytest.mark.unit
def test_a_healthy_single_target_still_passes(monkeypatch):
    """The control: the ordinary case must keep working and keep passing."""
    monkeypatch.setattr(type(doc.CONFIG_FILE), "exists", lambda self: True)
    _install(monkeypatch, _targets("lab"))

    ok, message = doc._check_auth()

    assert ok is True
    assert "lab" in message
