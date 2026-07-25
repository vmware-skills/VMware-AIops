"""Read list tools state their own completeness instead of leaving it inferred.

Regression source: VMware-AIops issue #31 (juanpf-ha). Driving the family with
a local Llama 3.3 70B, the operator reported that "with long tool responses, it
may omit existing information or incorrectly state that no data was returned."

A bare ``list[dict]`` gives a model no way to tell a whole answer from page
one, so it guesses — and a guess that reads "no data" is worse than a partial
list, because it looks like a finding. Every read list tool now returns the
family envelope, stating returned / limit / total / truncated outright.

The write ``batch_*`` tools are deliberately excluded and pinned that way at
the bottom of this file: their list return is a per-item result of work already
done, complete by construction, so an envelope would add noise and a
meaningless "truncated".
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pyVmomi import vim

from vmware_aiops.ops import alarm_mgmt, datastore_browser, planner, ttl

ENVELOPE_KEYS = {"items", "returned", "limit", "total", "truncated", "hint"}


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _alarms(n: int) -> list[dict]:
    return [
        {
            "severity": "warning",
            "alarm_name": f"alarm-{i}",
            "entity_name": f"vm-{i}",
            "entity_type": "VirtualMachine",
            "time": "2026-07-18T00:00:00Z",
            "acknowledged": False,
        }
        for i in range(n)
    ]


def _stub_active_alarms(monkeypatch, rows: list[dict]) -> None:
    monkeypatch.setattr(alarm_mgmt, "get_active_alarms", lambda si: rows)


def _stub_browse(monkeypatch, file_names: list[str]) -> None:
    files = [
        SimpleNamespace(path=n, fileSize=1024 * 1024, modification="2026-06-01")
        for n in file_names
    ]
    folder = SimpleNamespace(folderPath="[ds1] ", file=files)

    def fake_search(datastorePath, searchSpec):  # noqa: N803 — pyVmomi API names
        return SimpleNamespace(
            info=SimpleNamespace(
                state=vim.TaskInfo.State.success, result=[folder], error=None
            )
        )

    monkeypatch.setattr(
        datastore_browser,
        "find_datastore_by_name",
        lambda si, name: SimpleNamespace(
            browser=SimpleNamespace(SearchDatastoreSubFolders_Task=fake_search)
        ),
    )


def _stub_plans(monkeypatch, tmp_path: Path, n: int) -> None:
    for i in range(n):
        (tmp_path / f"plan-{i}.json").write_text(
            json.dumps(
                {
                    "plan_id": f"plan-{i}",
                    "created_at": "2026-07-18T00:00:00Z",
                    "status": "pending",
                    "summary": {"total_steps": 1, "vms_affected": ["vm-1"]},
                }
            ),
            encoding="utf-8",
        )
    monkeypatch.setattr(planner, "_PLANS_DIR", tmp_path)
    monkeypatch.setattr(planner, "_cleanup_stale", lambda: None)


def _stub_ttl_store(monkeypatch, tmp_path: Path, vm_names: list[str]) -> None:
    store = {
        name: {
            "vm_name": name,
            "expires_at": "2099-01-01T00:00:00+00:00",
            "target": None,
        }
        for name in vm_names
    }
    store_file = tmp_path / "ttl.json"
    store_file.write_text(json.dumps(store), encoding="utf-8")
    monkeypatch.setattr(ttl, "_TTL_FILE", store_file)


# ---------------------------------------------------------------------------
# Shape — the six keys are the contract
# ---------------------------------------------------------------------------


def test_list_alarms_carries_every_envelope_key(monkeypatch) -> None:
    """Explicit nulls, never missing keys — a missing key invites invention."""
    _stub_active_alarms(monkeypatch, _alarms(3))
    assert ENVELOPE_KEYS <= set(alarm_mgmt.list_alarms(MagicMock()))


def test_browse_datastore_carries_every_envelope_key(monkeypatch) -> None:
    _stub_browse(monkeypatch, ["app.ova"])
    assert ENVELOPE_KEYS <= set(datastore_browser.browse_datastore(object(), "ds1"))


def test_list_plans_carries_every_envelope_key(monkeypatch, tmp_path) -> None:
    _stub_plans(monkeypatch, tmp_path, 2)
    assert ENVELOPE_KEYS <= set(planner.list_plans())


def test_list_ttl_carries_every_envelope_key(monkeypatch, tmp_path) -> None:
    _stub_ttl_store(monkeypatch, tmp_path, ["vm-1"])
    assert ENVELOPE_KEYS <= set(ttl.list_ttl())


# ---------------------------------------------------------------------------
# Truncation — a full page says so, a short page says it is complete
# ---------------------------------------------------------------------------


def test_alarms_full_page_is_flagged_truncated_with_a_hint(monkeypatch) -> None:
    """200 active alarms behind a limit of 50 — the model must be told."""
    _stub_active_alarms(monkeypatch, _alarms(200))
    result = alarm_mgmt.list_alarms(MagicMock(), limit=50)
    assert result["returned"] == 50
    assert result["limit"] == 50
    assert result["total"] == 200
    assert result["truncated"] is True
    assert result["hint"] and "200" in result["hint"]


def test_alarms_short_page_is_complete_with_no_hint(monkeypatch) -> None:
    _stub_active_alarms(monkeypatch, _alarms(3))
    result = alarm_mgmt.list_alarms(MagicMock(), limit=50)
    assert result["returned"] == 3
    assert result["total"] == 3
    assert result["truncated"] is False
    assert result["hint"] is None


def test_alarms_page_exactly_filling_a_known_total_is_not_truncated(
    monkeypatch,
) -> None:
    """A known total is what lets a full page be recognised as complete."""
    _stub_active_alarms(monkeypatch, _alarms(50))
    result = alarm_mgmt.list_alarms(MagicMock(), limit=50)
    assert result["returned"] == 50
    assert result["total"] == 50
    assert result["truncated"] is False


def test_unlimited_alarm_listing_is_never_truncated(monkeypatch) -> None:
    _stub_active_alarms(monkeypatch, _alarms(200))
    result = alarm_mgmt.list_alarms(MagicMock())
    assert result["returned"] == 200
    assert result["total"] == 200
    assert result["limit"] is None
    assert result["truncated"] is False


# ---------------------------------------------------------------------------
# The un-paged tools: "this is complete" is itself the information
# ---------------------------------------------------------------------------


def test_browse_datastore_reports_a_real_total_and_no_truncation(monkeypatch) -> None:
    _stub_browse(monkeypatch, ["app.ova", "boot.iso"])
    result = datastore_browser.browse_datastore(object(), "ds1")
    assert result["returned"] == 2
    assert result["total"] == 2
    assert result["truncated"] is False
    assert result["hint"] is None


def test_list_plans_reports_a_real_total_and_no_truncation(
    monkeypatch, tmp_path
) -> None:
    _stub_plans(monkeypatch, tmp_path, 3)
    result = planner.list_plans()
    assert result["returned"] == 3
    assert result["total"] == 3
    assert result["truncated"] is False


def test_list_ttl_reports_a_real_total_and_no_truncation(monkeypatch, tmp_path) -> None:
    _stub_ttl_store(monkeypatch, tmp_path, ["vm-1", "vm-2"])
    result = ttl.list_ttl()
    assert result["returned"] == 2
    assert result["total"] == 2
    assert result["truncated"] is False


# ---------------------------------------------------------------------------
# Empty is a stated zero, not an absence
# ---------------------------------------------------------------------------


def test_empty_alarm_list_is_an_explicit_zero(monkeypatch) -> None:
    """"No active alarms" must not read the same as "the call failed"."""
    _stub_active_alarms(monkeypatch, [])
    result = alarm_mgmt.list_alarms(MagicMock())
    assert result["items"] == []
    assert result["returned"] == 0
    assert result["total"] == 0
    assert result["truncated"] is False


def test_missing_plan_dir_is_an_explicit_zero(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(planner, "_PLANS_DIR", tmp_path / "absent")
    monkeypatch.setattr(planner, "_cleanup_stale", lambda: None)
    result = planner.list_plans()
    assert result["items"] == []
    assert result["returned"] == 0
    assert result["truncated"] is False


# ---------------------------------------------------------------------------
# The batch write tools stay bare lists — pinned so a later sweep cannot
# quietly envelope them
# ---------------------------------------------------------------------------

BATCH_WRITE_TOOLS = (
    "batch_clone_vms",
    "batch_deploy_from_spec",
    "batch_linked_clone_vms",
)


@pytest.mark.parametrize("tool_name", BATCH_WRITE_TOOLS)
def test_batch_write_tools_still_return_a_bare_list(tool_name) -> None:
    """Their list is a per-item result of work already done, not a page.

    Every requested clone is reported, so the set is complete by construction:
    an envelope would add a ``truncated`` flag that could only ever be false
    and a ``total`` that merely repeats ``returned``. The exclusion is
    deliberate, so it is pinned rather than left to the next refactor's
    judgement.
    """
    from vmware_aiops.mcp_server.tools import deploy

    fn = inspect.unwrap(getattr(deploy, tool_name))
    assert (
        inspect.signature(fn).return_annotation == list[dict]
    ), f"{tool_name} must keep its bare-list return — see this test's docstring"
