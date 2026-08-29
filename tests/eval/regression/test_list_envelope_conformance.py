"""Every list tool must answer in the family envelope.

Found live against a real vCenter, 2026-08-29: `list_dvs_portgroups` returned
`{total, returned, portgroups}` and `list_host_vmks` returned
`{total, returned, vmks}`. Both are self-consistent and both are wrong, because
an agent that has learned this family's contract reads `items` — and gets
nothing.

The missing keys are the ones that carry the meaning. `truncated` and `hint`
exist because of VMware-AIops issue #31, where a model handed a bare list
"incorrectly states that no data was returned". A list tool without them puts
the model back in the position of guessing, which is what the envelope was
introduced to stop.

`scan_datastore_images` was worse: `{images, last_scan}`, with no count at all.

The old keys are kept as deprecated aliases so nothing that reads them breaks.
"""

from __future__ import annotations

import pytest

from vmware_policy.envelope import ENVELOPE_KEYS

from vmware_aiops.ops import datastore_browser, host_network_mgmt, network_mgmt


def _assert_envelope(result: dict, alias: str):
    missing = set(ENVELOPE_KEYS) - set(result)
    assert not missing, f"not the family envelope, missing {sorted(missing)}"
    assert alias in result, f"the {alias!r} alias was dropped — that breaks readers"
    assert result[alias] == result["items"], f"{alias} and items disagree"


class TestPortgroups:
    def test_returns_the_envelope(self, monkeypatch):
        monkeypatch.setattr(network_mgmt, "_collect", lambda *a, **k: [])
        _assert_envelope(network_mgmt.list_dvs_portgroups(object()), "portgroups")

    def test_an_empty_result_is_not_truncated(self, monkeypatch):
        monkeypatch.setattr(network_mgmt, "_collect", lambda *a, **k: [])
        r = network_mgmt.list_dvs_portgroups(object())
        assert r["truncated"] is False and r["hint"] is None
        assert r["total"] == 0


class TestVmks:
    def test_returns_the_envelope(self, monkeypatch):
        monkeypatch.setattr(host_network_mgmt, "_collect", lambda *a, **k: [])
        _assert_envelope(host_network_mgmt.list_host_vmks(object()), "vmks")


class TestScannedImages:
    """The registry dict is also the on-disk format, so the envelope is applied
    at the tool layer rather than at the source."""

    def _tool(self, monkeypatch, registry):
        from vmware_aiops.mcp_server.tools import datastore as dt

        monkeypatch.setattr(dt, "_get_connection", lambda target: object())
        monkeypatch.setattr(dt.datastore_browser, "update_registry", lambda si: registry)
        return dt.scan_datastore_images.__wrapped__(target=None)

    def test_returns_the_envelope_when_nothing_was_found(self, monkeypatch):
        """The empty path reached the agent as {images: [], last_scan: None} —
        no count, no truncation flag, nothing to reason about."""
        r = self._tool(monkeypatch, {"images": [], "last_scan": None})
        _assert_envelope(r, "images")
        assert r["total"] == 0

    def test_keeps_last_scan(self, monkeypatch):
        r = self._tool(monkeypatch, {"images": [], "last_scan": "2026-08-29T00:00:00Z"})
        assert r["last_scan"] == "2026-08-29T00:00:00Z"


def test_the_envelope_contract_is_read_from_policy_not_re_declared():
    """Six keys, defined once. A hand-copied tuple here would go stale the day
    a seventh is added — which is why vmware_policy exports ENVELOPE_KEYS."""
    assert set(ENVELOPE_KEYS) == {
        "items", "returned", "limit", "total", "truncated", "hint",
    }
