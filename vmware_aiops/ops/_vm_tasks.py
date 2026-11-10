"""vSphere task polling, faults and indeterminate completion."""

from __future__ import annotations

import time


from typing import TYPE_CHECKING

from pyVmomi import vim


if TYPE_CHECKING:
    pass

from vmware_aiops.ops._snapshot_tree import (
    _count_children as _count_children,
    _count_snapshots as _count_snapshots,
    _require_snapshot_id as _require_snapshot_id,
    matching_snapshots as matching_snapshots,
    resolve_snapshot as resolve_snapshot,
    snapshot_nodes as snapshot_nodes,
)


class TaskFailedError(Exception):
    """Raised when a vSphere task fails."""


class TaskStillRunning(Exception):
    """Raised when a task outlives its wait budget but is still progressing.

    Carries the task moref id so callers can keep polling instead of treating
    the timeout as a failure (the vCenter task is NOT cancelled).
    """

    def __init__(self, task_id: str, timeout: int) -> None:
        self.task_id = task_id
        self.timeout = timeout
        super().__init__(
            f"Task still running after {timeout}s — this is NOT a failure. "
            f"The vCenter task is still progressing (large snapshot consolidation "
            f"or clone can take many minutes). Task id: {task_id}. "
            f"Poll with get_vm_task_status(task_id='{task_id}') "
            f"(CLI: vm task-status {task_id} --target <target>)."
        )


def _task_moid(task) -> str:
    """Return the managed-object id of a task for later reconnection."""
    return task._moId


def _wait_for_task(task, timeout: int = 300) -> object:
    """Wait for a vSphere task to complete.

    Raises TaskStillRunning (not TimeoutError) when the budget is exceeded so
    callers can surface the task id and keep polling — a slow snapshot
    consolidation is not a failure.
    """
    start = time.time()
    while task.info.state in (vim.TaskInfo.State.running, vim.TaskInfo.State.queued):
        if time.time() - start > timeout:
            raise TaskStillRunning(_task_moid(task), timeout)
        time.sleep(2)

    if task.info.state == vim.TaskInfo.State.success:
        return task.info.result

    err = task.info.error
    if err is None:
        raise TaskFailedError(
            f"Task {_task_moid(task)} failed but vCenter attached no fault detail. "
            f"Run get_vm_task_status with task_id='{_task_moid(task)}' for its final state, "
            f"and check get_events (vmware-monitor skill) around now for the underlying "
            f"cause before retrying."
        )

    parts: list[str] = []
    primary = getattr(err, "msg", None) or type(err).__name__
    parts.append(str(primary))
    fault_type = type(err).__name__
    if fault_type and fault_type not in primary:
        parts.append(f"fault={fault_type}")
    cause = getattr(err, "faultCause", None)
    if cause is not None and getattr(cause, "msg", None):
        parts.append(f"caused_by={cause.msg}")
    fault_msgs = getattr(err, "faultMessage", None) or []
    for fm in fault_msgs:
        m = getattr(fm, "message", None)
        if m:
            parts.append(f"detail={m}")

    raise TaskFailedError("Task failed: " + " | ".join(parts))
