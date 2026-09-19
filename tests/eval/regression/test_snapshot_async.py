"""Regression — snapshot delete timeout/async behaviour (2026-06-22 incident).

A user deleted an old, large snapshot (EVE-NG Large image, ~3-year delta) and
burned ~26k tokens over 30 min. Root cause chain:

- delete_snapshot waited synchronously with the 300s default meant for metadata
  ops, while clone/migrate already used 600s. Snapshot consolidation is the
  slowest write op, so it always blew the 300s budget.
- On timeout _wait_for_task raised (a bare TimeoutError), so the agent thought
  the delete FAILED and improvised foreground polling in its own context — that
  is what actually cost the tokens.

Fixes locked here:
1. snapshot delete uses a generous (1800s) default, not 300s.
2. async mode (wait=False) returns a task id immediately without waiting.
3. timeout is honest: _wait_for_task raises TaskStillRunning carrying the task
   id, and delete_snapshot(wait=True) translates it into a "still running, NOT
   failed" string with poll guidance instead of raising.
4. get_task_status polls a task id and degrades a gc'd task to state 'gone'.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from vmware_aiops.ops import vm_lifecycle
from vmware_aiops.ops.vm_lifecycle import (
    TaskStillRunning,
    _wait_for_task,
    delete_snapshot,
    get_task_status,
)


def _fake_target(task) -> SimpleNamespace:
    """The snapshot node ``resolve_snapshot`` would hand the executor."""
    ref = MagicMock()
    ref.RemoveSnapshot_Task.return_value = task
    return SimpleNamespace(name="baseline", snapshot=ref)


def _resolved(target):
    """Patch the lookup: the VM, and the one snapshot the name resolves to."""
    return (
        patch.object(vm_lifecycle, "_require_vm", return_value=MagicMock()),
        patch.object(vm_lifecycle, "resolve_snapshot", return_value=(target, None)),
    )


# ── Fix 1: generous default timeout, not the 300s metadata default ──


def test_delete_snapshot_default_timeout_is_generous() -> None:
    sig = inspect.signature(delete_snapshot)
    assert sig.parameters["timeout"].default >= 1800, (
        "snapshot consolidation is the slowest write op — its default wait must "
        "be far larger than the 300s used for metadata tasks"
    )


# ── Fix 2: async mode returns a task id without ever waiting ──


def test_delete_snapshot_no_wait_returns_task_id_without_waiting() -> None:
    task = SimpleNamespace(_moId="task-9001", info=SimpleNamespace(state="running"))
    target = _fake_target(task)

    vm_patch, snap_patch = _resolved(target)
    with vm_patch, snap_patch, patch.object(vm_lifecycle, "_wait_for_task") as waited:
        out = delete_snapshot(MagicMock(), "vm1", "baseline", wait=False)

    waited.assert_not_called()
    assert "task-9001" in out
    assert "task-status" in out


# ── Fix 3a: _wait_for_task raises TaskStillRunning (not bare TimeoutError) ──


def test_wait_for_task_raises_task_still_running_with_id() -> None:
    task = SimpleNamespace(
        _moId="task-7777",
        info=SimpleNamespace(state=vm_lifecycle.vim.TaskInfo.State.running),
    )
    with pytest.raises(TaskStillRunning) as ei:
        _wait_for_task(task, timeout=0)
    assert ei.value.task_id == "task-7777"
    assert "task-status" in str(ei.value)


# ── Fix 3b: wait=True timeout does NOT raise — returns honest "still running" ──


def test_delete_snapshot_wait_timeout_returns_not_failed_string() -> None:
    task = SimpleNamespace(_moId="task-5555", info=SimpleNamespace(state="running"))
    target = _fake_target(task)

    vm_patch, snap_patch = _resolved(target)
    with vm_patch, snap_patch, patch.object(
                vm_lifecycle, "_wait_for_task",
                side_effect=TaskStillRunning("task-5555", 1800),
            ):
        out = delete_snapshot(MagicMock(), "vm1", "baseline", wait=True)

    assert "task-5555" in out
    assert "NOT failed" in out
    assert "task-status" in out


# ── Fix 4: get_task_status degrades a garbage-collected task to 'gone' ──


def test_get_task_status_gone_task_is_not_an_exception() -> None:
    si = MagicMock()
    broken = MagicMock()
    type(broken).info = property(lambda self: (_ for _ in ()).throw(Exception("gc")))
    with patch.object(vm_lifecycle.vim, "Task", return_value=broken):
        status = get_task_status(si, "task-dead")
    assert status["state"] == "gone"
    assert "task_id" in status
