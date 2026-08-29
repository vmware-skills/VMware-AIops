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


class TestOffsetPaging:
    """Caught in review of the change above, not by its own tests.

    `paginated()` derives truncation from `returned < total`, which is right
    when a page starts at zero and wrong the moment `offset` is involved: the
    final page of five items reached with offset=4 returns one row, and
    `1 < 5` made it claim more data was being withheld — with a hint advising
    "raise limit", which cannot help someone already at the end.

    The old hand-rolled shape was vague here too. This is the first version that
    has to be exact, because `truncated` is a flag an agent acts on rather than
    prose it reads.
    """

    def _rows(self, n):
        return [
            {
                "name": f"pg-{i}", "dvs": "dvs", "binding": "earlyBinding",
                "vlan": "none", "num_ports": 8, "uplink": False,
            }
            for i in range(n)
        ]

    def _list(self, monkeypatch, n, **kw):
        rows = self._rows(n)
        monkeypatch.setattr(
            network_mgmt, "_collect",
            lambda si, t, p: [(object(), {
                "name": r["name"], "config.type": "earlyBinding",
                "config.numPorts": 8, "config.uplink": False,
                "config.defaultPortConfig": None,
            }) for r in rows],
        )
        monkeypatch.setattr(network_mgmt, "_dvs_names", lambda si: {}, raising=False)
        return network_mgmt.list_dvs_portgroups(object(), **kw)

    def test_the_last_page_is_not_reported_as_truncated(self, monkeypatch):
        r = self._list(monkeypatch, 5, limit=100, offset=4)
        assert r["returned"] == 1 and r["total"] == 5
        assert r["truncated"] is False, "claimed more data at the end of the list"
        assert r["hint"] is None

    def test_a_middle_page_is_still_truncated(self, monkeypatch):
        r = self._list(monkeypatch, 5, limit=2, offset=1)
        assert r["returned"] == 2 and r["truncated"] is True
        assert r["hint"]

    def test_the_offset_is_reported_so_the_page_can_be_placed(self, monkeypatch):
        assert self._list(monkeypatch, 5, limit=2, offset=1)["offset"] == 1
