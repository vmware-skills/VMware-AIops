"""`vm_list_plans` claims [READ] — it must not delete anything.

From the 2026-07-19 pre-release review. The tool carried `[READ]` plus
`readOnlyHint: True, destructiveHint: False`, so the read-only gate let it
through, while `list_plans()` opened with `_cleanup_stale()` and unlinked every
plan file older than 24h.

The damage is specific to read-only mode. A failed apply leaves `plan-*.json` as
the only on-disk record of which steps landed. Open a read-only server the next
day to review the incident, ask the model to list plans, and the record is gone
— unrecoverable in that session by construction, because `vm_create_plan`,
`vm_apply_plan` and `vm_rollback_plan` were all withheld by the gate. The one
plan tool still visible was the one that deletes.

Expiry now runs only on the write path (`create_plan`), so plans are still swept
on every write.
"""

from __future__ import annotations

import json
import time

import pytest

from vmware_aiops.ops import planner


@pytest.fixture
def plans_dir(tmp_path, monkeypatch):
    d = tmp_path / "plans"
    d.mkdir()
    monkeypatch.setattr(planner, "_PLANS_DIR", d)
    return d


def _write_plan(directory, plan_id: str, *, age_seconds: float, status: str = "failed"):
    p = directory / f"plan-{plan_id}.json"
    p.write_text(
        json.dumps(
            {
                "plan_id": plan_id,
                "created_at": "2026-07-18T00:00:00Z",
                "target": "vc01",
                "status": status,
                "summary": {"total_steps": 3, "vms_affected": 2},
            }
        ),
        encoding="utf-8",
    )
    stamp = time.time() - age_seconds
    import os

    os.utime(p, (stamp, stamp))
    return p


def test_list_plans_does_not_delete_stale_plans(plans_dir):
    """The regression itself: listing must leave every file on disk."""
    stale = _write_plan(plans_dir, "STALE", age_seconds=planner._STALE_SECONDS + 3600)
    fresh = _write_plan(plans_dir, "FRESH", age_seconds=60)

    planner.list_plans()

    assert stale.exists(), "listing deleted a stale plan — the read-only record is gone"
    assert fresh.exists()


def test_list_plans_still_reports_stale_plans(plans_dir):
    """Not deleting is only useful if the plan is still visible. A failed plan
    past the expiry window is exactly what an incident review needs to see."""
    _write_plan(plans_dir, "STALE", age_seconds=planner._STALE_SECONDS + 3600, status="failed")

    result = planner.list_plans()

    ids = [item["plan_id"] for item in result["items"]]
    assert "STALE" in ids
    assert result["total"] == 1


def test_expiry_still_runs_on_the_write_path(plans_dir, monkeypatch):
    """Moving the sweep must not disable it. `create_plan` still expires."""
    called = {"n": 0}
    monkeypatch.setattr(planner, "_cleanup_stale", lambda: called.__setitem__("n", called["n"] + 1))
    monkeypatch.setattr(planner, "validate_operations", lambda ops: ["stop here"])

    planner.create_plan(si=None, operations=[{"op": "noop"}])

    assert called["n"] == 1, "create_plan no longer sweeps stale plans"


def test_list_plans_calls_no_cleanup(plans_dir, monkeypatch):
    """Pin the absence directly, so a future refactor cannot quietly restore the
    call by re-adding a convenience sweep to the read path."""
    monkeypatch.setattr(
        planner,
        "_cleanup_stale",
        lambda: pytest.fail("list_plans must not sweep — it is the read-only survivor"),
    )
    planner.list_plans()
