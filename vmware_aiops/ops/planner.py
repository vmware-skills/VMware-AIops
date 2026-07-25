"""Plan → Apply: plan creation, validation, and storage."""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pyVmomi import vim
from vmware_policy import paginated

from vmware_aiops.ops.inventory import find_vm_by_name

if TYPE_CHECKING:
    from pyVmomi.vim import ServiceInstance

logger = logging.getLogger(__name__)

_PLANS_DIR = Path.home() / ".vmware-aiops" / "plans"
_STALE_SECONDS = 24 * 3600  # 24 hours

# ---------------------------------------------------------------------------
# Allowed actions and their required/optional params + rollback mapping
# ---------------------------------------------------------------------------

_ACTION_SCHEMA: dict[str, dict[str, Any]] = {
    "power_on": {
        "required": ["vm_name"],
        "optional": [],
        "rollback": "power_off",
    },
    "power_off": {
        "required": ["vm_name"],
        "optional": ["force"],
        "rollback": "power_on",
    },
    "reset": {
        "required": ["vm_name"],
        "optional": [],
        "rollback": None,
    },
    "suspend": {
        "required": ["vm_name"],
        "optional": [],
        "rollback": "power_on",
    },
    "create_vm": {
        "required": ["vm_name"],
        "optional": ["cpu", "memory_mb", "disk_gb", "network_name", "datastore_name", "folder_path", "guest_id"],
        "rollback": "delete_vm",
    },
    "delete_vm": {
        "required": ["vm_name"],
        "optional": [],
        "rollback": None,
    },
    "reconfigure": {
        "required": ["vm_name"],
        "optional": ["cpu", "memory_mb"],
        "rollback": None,
    },
    "create_snapshot": {
        "required": ["vm_name", "snapshot_name"],
        "optional": ["description", "memory"],
        "rollback": "delete_snapshot",
    },
    "delete_snapshot": {
        "required": ["vm_name", "snapshot_name"],
        "optional": ["remove_children"],
        "rollback": None,
    },
    "revert_snapshot": {
        "required": ["vm_name", "snapshot_name"],
        "optional": [],
        "rollback": None,
    },
    "clone": {
        "required": ["vm_name", "new_name"],
        "optional": [],
        "rollback": "delete_vm",
        "rollback_vm_key": "new_name",
    },
    "migrate": {
        "required": ["vm_name", "target_host"],
        "optional": [],
        "rollback": None,
    },
    "deploy_ova": {
        "required": ["ova_path", "vm_name", "datastore_name", "network_name"],
        "optional": ["folder_path", "power_on", "snapshot_name"],
        "rollback": "delete_vm",
    },
    "deploy_template": {
        "required": ["template_name", "new_name"],
        "optional": ["datastore_name", "cpu", "memory_mb", "power_on", "snapshot_name"],
        "rollback": "delete_vm",
        "rollback_vm_key": "new_name",
    },
    "linked_clone": {
        "required": ["source_vm_name", "snapshot_name", "new_name"],
        "optional": ["cpu", "memory_mb", "power_on", "baseline_snapshot"],
        "rollback": "delete_vm",
        "rollback_vm_key": "new_name",
    },
    "attach_iso": {
        "required": ["vm_name", "iso_ds_path"],
        "optional": [],
        "rollback": None,
    },
    "convert_to_template": {
        "required": ["vm_name"],
        "optional": [],
        "rollback": None,
    },
    "guest_exec": {
        "required": ["vm_name", "command", "username", "password"],
        "optional": ["arguments", "working_directory"],
        "rollback": None,
    },
    "guest_upload": {
        "required": ["vm_name", "local_path", "guest_path", "username", "password"],
        "optional": [],
        "rollback": None,
    },
    "guest_download": {
        "required": ["vm_name", "guest_path", "local_path", "username", "password"],
        "optional": [],
        "rollback": None,
    },
    # Cluster operations
    "create_cluster": {
        "required": ["cluster_name"],
        "optional": ["datacenter_name", "ha_enabled", "drs_enabled", "drs_behavior"],
        "rollback": "delete_cluster",
        "rollback_vm_key": "cluster_name",
    },
    "delete_cluster": {
        "required": ["cluster_name"],
        "optional": [],
        "rollback": None,
    },
    "configure_cluster": {
        "required": ["cluster_name"],
        "optional": ["ha_enabled", "drs_enabled", "drs_behavior"],
        "rollback": None,
    },
    "cluster_add_host": {
        "required": ["cluster_name", "host_name"],
        "optional": [],
        "rollback": "cluster_remove_host",
    },
    "cluster_remove_host": {
        "required": ["cluster_name", "host_name"],
        "optional": [],
        "rollback": "cluster_add_host",
    },
    # iSCSI / Storage operations
    "iscsi_enable": {
        "required": ["host_name"],
        "optional": [],
        "rollback": None,
    },
    "iscsi_add_target": {
        "required": ["host_name", "address"],
        "optional": ["port"],
        "rollback": "iscsi_remove_target",
    },
    "iscsi_remove_target": {
        "required": ["host_name", "address"],
        "optional": ["port"],
        "rollback": "iscsi_add_target",
    },
    "storage_rescan": {
        "required": ["host_name"],
        "optional": [],
        "rollback": None,
    },
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class PlanStep:
    index: int
    action: str
    params: dict[str, Any]
    rollback_action: str | None
    rollback_params: dict[str, Any] | None
    status: str = "pending"  # pending | success | failed | skipped | rolled_back
    result: str | None = None
    executed_at: str | None = None


@dataclass
class Plan:
    plan_id: str
    created_at: str
    target: str | None
    status: str  # pending | executing | completed | failed | rolled_back
    steps: list[PlanStep]
    summary: dict[str, Any]

    def to_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "created_at": self.created_at,
            "target": self.target,
            "status": self.status,
            "steps": [asdict(s) for s in self.steps],
            "summary": self.summary,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _generate_plan_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    short = uuid.uuid4().hex[:4]
    return f"plan-{ts}-{short}"


def _build_rollback(action: str, params: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    """Build rollback action and params for a given action."""
    schema = _ACTION_SCHEMA[action]
    rollback_action = schema.get("rollback")
    if rollback_action is None:
        return None, None

    # Determine which VM name to use for rollback
    rollback_vm_key = schema.get("rollback_vm_key", "vm_name")
    vm_name = params.get(rollback_vm_key, params.get("vm_name"))

    if rollback_action == "delete_vm":
        return rollback_action, {"vm_name": vm_name}
    elif rollback_action == "power_on":
        return rollback_action, {"vm_name": vm_name}
    elif rollback_action == "power_off":
        return rollback_action, {"vm_name": vm_name}
    elif rollback_action == "delete_snapshot":
        return rollback_action, {
            "vm_name": params["vm_name"],
            "snapshot_name": params["snapshot_name"],
        }
    elif rollback_action == "delete_cluster":
        return rollback_action, {"cluster_name": params["cluster_name"]}
    elif rollback_action == "cluster_remove_host":
        return rollback_action, {
            "cluster_name": params["cluster_name"],
            "host_name": params["host_name"],
        }
    elif rollback_action == "cluster_add_host":
        return rollback_action, {
            "cluster_name": params["cluster_name"],
            "host_name": params["host_name"],
        }
    elif rollback_action == "iscsi_remove_target":
        return rollback_action, {
            "host_name": params["host_name"],
            "address": params["address"],
            "port": params.get("port", 3260),
        }
    elif rollback_action == "iscsi_add_target":
        return rollback_action, {
            "host_name": params["host_name"],
            "address": params["address"],
            "port": params.get("port", 3260),
        }
    return rollback_action, {"vm_name": vm_name}


def _cleanup_stale() -> None:
    """Remove plan files older than 24 hours."""
    if not _PLANS_DIR.exists():
        return
    now = time.time()
    for p in _PLANS_DIR.glob("plan-*.json"):
        if now - p.stat().st_mtime > _STALE_SECONDS:
            p.unlink(missing_ok=True)
            logger.info("Cleaned up stale plan: %s", p.name)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_operations(operations: list[dict[str, Any]]) -> list[str]:
    """Validate operation list format. Returns list of error strings (empty = valid)."""
    errors: list[str] = []
    for i, op in enumerate(operations):
        action = op.get("action")
        if action not in _ACTION_SCHEMA:
            errors.append(f"Step {i}: unknown action '{action}'. Allowed: {sorted(_ACTION_SCHEMA)}")
            continue
        schema = _ACTION_SCHEMA[action]
        for req in schema["required"]:
            if req not in op:
                errors.append(f"Step {i} ({action}): missing required param '{req}'")
    return errors


def precheck_targets(si: ServiceInstance, operations: list[dict[str, Any]]) -> list[str]:
    """Check that VMs, snapshots, hosts referenced in operations exist.

    Returns list of warning/error strings (empty = all good).
    """
    errors: list[str] = []
    for i, op in enumerate(operations):
        action = op["action"]
        vm_name = op.get("vm_name")

        # Skip existence check for create operations and non-VM operations
        if action in ("create_vm", "deploy_ova", "create_cluster", "delete_cluster",
                       "configure_cluster", "cluster_add_host", "cluster_remove_host",
                       "iscsi_enable", "iscsi_add_target", "iscsi_remove_target",
                       "storage_rescan"):
            continue

        # Check VM existence
        if vm_name:
            vm = find_vm_by_name(si, vm_name)
            if vm is None:
                errors.append(f"Step {i} ({action}): VM '{vm_name}' not found")
                continue

            # Check snapshot existence
            snap_name = op.get("snapshot_name") or op.get("snapshot")
            if snap_name and action in ("revert_snapshot", "delete_snapshot", "linked_clone"):
                if not _find_snapshot(vm, snap_name):
                    errors.append(f"Step {i} ({action}): snapshot '{snap_name}' not found on VM '{vm_name}'")

        # Check source VM for clone/template operations
        source = op.get("source_vm_name") or op.get("template_name")
        if source and action in ("clone", "linked_clone", "deploy_template"):
            if find_vm_by_name(si, source) is None:
                errors.append(f"Step {i} ({action}): source '{source}' not found")

        # Check target host for migrate
        target_host = op.get("target_host")
        if target_host and action == "migrate":
            from vmware_aiops.ops.inventory import find_host_by_name
            if find_host_by_name(si, target_host) is None:
                errors.append(f"Step {i} ({action}): host '{target_host}' not found")

    return errors


def _find_snapshot(
    vm: vim.VirtualMachine, snap_name: str
) -> vim.vm.Snapshot | None:
    """Recursively find a snapshot by name."""
    if not vm.snapshot or not vm.snapshot.rootSnapshotList:
        return None

    def _walk(snap_list: list) -> vim.vm.Snapshot | None:
        for snap_info in snap_list:
            if snap_info.name == snap_name:
                return snap_info.snapshot
            found = _walk(snap_info.childSnapshotList)
            if found:
                return found
        return None

    return _walk(vm.snapshot.rootSnapshotList)


def create_plan(
    si: ServiceInstance,
    operations: list[dict[str, Any]],
    target: str | None = None,
) -> dict:
    """Create and persist a plan.

    Returns plan dict on success, or dict with "errors" key on validation failure.
    """
    _cleanup_stale()

    # 1. Format validation
    errors = validate_operations(operations)
    if errors:
        return {"errors": errors}

    # 2. Pre-check targets in vSphere
    precheck_errors = precheck_targets(si, operations)
    if precheck_errors:
        return {"errors": precheck_errors}

    # 3. Build plan
    plan_id = _generate_plan_id()
    steps: list[PlanStep] = []
    vms_affected: set[str] = set()
    irreversible_steps: list[int] = []

    for i, op in enumerate(operations):
        action = op["action"]
        params = {k: v for k, v in op.items() if k != "action"}
        rollback_action, rollback_params = _build_rollback(action, params)

        steps.append(PlanStep(
            index=i,
            action=action,
            params=params,
            rollback_action=rollback_action,
            rollback_params=rollback_params,
        ))

        # Track affected resources (VMs, clusters, hosts)
        for key in ("vm_name", "new_name", "source_vm_name", "template_name",
                     "cluster_name", "host_name"):
            if key in params:
                vms_affected.add(params[key])

        if rollback_action is None:
            irreversible_steps.append(i)

    plan = Plan(
        plan_id=plan_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        target=target,
        status="pending",
        steps=steps,
        summary={
            "total_steps": len(steps),
            "vms_affected": sorted(vms_affected),
            "irreversible_steps": irreversible_steps,
            "rollback_available": len(irreversible_steps) < len(steps),
        },
    )

    # 4. Persist (owner-only — plans carry VM names + operations)
    from vmware_aiops._fsutil import secure_chmod_file, secure_mkdir

    secure_mkdir(_PLANS_DIR)
    plan_path = _PLANS_DIR / f"{plan_id}.json"
    plan_path.write_text(json.dumps(plan.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    secure_chmod_file(plan_path)
    logger.info("Plan created: %s (%d steps)", plan_id, len(steps))

    return plan.to_dict()


def load_plan(plan_id: str) -> dict | None:
    """Load a plan from disk. Returns None if not found."""
    plan_path = _PLANS_DIR / f"{plan_id}.json"
    if not plan_path.exists():
        return None
    return json.loads(plan_path.read_text(encoding="utf-8"))


def save_plan(plan: dict) -> None:
    """Write updated plan back to disk (owner-only)."""
    from vmware_aiops._fsutil import secure_chmod_file

    plan_path = _PLANS_DIR / f"{plan['plan_id']}.json"
    plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    secure_chmod_file(plan_path)


def delete_plan(plan_id: str) -> None:
    """Delete a plan file."""
    plan_path = _PLANS_DIR / f"{plan_id}.json"
    plan_path.unlink(missing_ok=True)


def list_plans() -> dict:
    """List all pending plans.

    Returns the family list envelope; every plan file on disk is read, so
    ``total`` is the real plan count and nothing is truncated.

    Listing does NOT expire stale plans. A failed plan is the only on-disk
    record of which steps landed, so expiring it during a plain listing would
    destroy exactly what the operator opened the session to read. Expiry runs
    on ``create_plan`` instead, so plans are still swept on every write.
    """
    if not _PLANS_DIR.exists():
        return paginated([])
    result: list[dict] = []
    for p in sorted(_PLANS_DIR.glob("plan-*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            result.append({
                "plan_id": data["plan_id"],
                "created_at": data["created_at"],
                "target": data.get("target"),
                "status": data["status"],
                "total_steps": data["summary"]["total_steps"],
                "vms_affected": data["summary"]["vms_affected"],
            })
        except (json.JSONDecodeError, KeyError):
            logger.warning("Skipping invalid plan file: %s", p.name)
    return paginated(result, total=len(result))
