"""Cluster management: create, delete, configure HA/DRS, add/remove hosts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pyVmomi import vim
from vmware_policy import sanitize

from vmware_aiops.ops.inventory import (
    InventoryError,
    _collect,
    find_cluster_by_name,
    find_host_by_name,
    find_vm_by_name,
    resolve_datacenter,
)
from vmware_aiops.ops.vm_lifecycle import _wait_for_task

if TYPE_CHECKING:
    from pyVmomi.vim import ServiceInstance


class ClusterNotFoundError(Exception):
    """Raised when a cluster is not found by name."""


class ClusterError(Exception):
    """Raised on cluster operation failures."""


_VALID_DRS_BEHAVIORS = {"fullyAutomated", "partiallyAutomated", "manual"}


def _require_cluster(
    si: ServiceInstance, cluster_name: str
) -> vim.ClusterComputeResource:
    """Find a cluster or raise ClusterNotFoundError."""
    cluster = find_cluster_by_name(si, cluster_name)
    if cluster is None:
        raise ClusterNotFoundError(
            f"Cluster '{cluster_name}' not found. Run cluster_health_summary "
            f"(CLI: vmware-aiops summary) to see every cluster name on this target, "
            f"then retry with an exact name."
        )
    return cluster


def _get_datacenter(si: ServiceInstance, datacenter_name: str | None = None) -> vim.Datacenter:
    """Find a datacenter by name, or return the first one."""
    try:
        return resolve_datacenter(si, datacenter_name)
    except InventoryError as e:
        raise ClusterError(str(e)) from e


# ─── Info ─────────────────────────────────────────────────────────────────────


def get_cluster_info(si: ServiceInstance, cluster_name: str) -> dict:
    """Get detailed cluster information."""
    cluster = _require_cluster(si, cluster_name)
    cfg = cluster.configuration

    # Batch the per-host runtime reads: one PropertyCollector call for all hosts,
    # keyed by moRef, instead of a lazy round-trip per host in the loop.
    host_refs = cluster.host or []
    host_props = {
        obj: p
        for obj, p in _collect(
            si,
            [vim.HostSystem],
            [
                "name",
                "runtime.connectionState",
                "runtime.powerState",
                "runtime.inMaintenanceMode",
            ],
        )
    }
    hosts = []
    for host in host_refs:
        p = host_props.get(host, {})
        hosts.append({
            "name": sanitize(p.get("name", "")),
            "connection_state": str(p.get("runtime.connectionState")),
            "power_state": str(p.get("runtime.powerState")),
            "maintenance_mode": p.get("runtime.inMaintenanceMode"),
        })

    return {
        "name": sanitize(cluster.name),
        "host_count": len(host_refs),
        "hosts": hosts,
        "ha_enabled": cfg.dasConfig.enabled if cfg.dasConfig else False,
        "ha_admission_control": cfg.dasConfig.admissionControlEnabled if cfg.dasConfig else False,
        "drs_enabled": cfg.drsConfig.enabled if cfg.drsConfig else False,
        "drs_behavior": str(cfg.drsConfig.defaultVmBehavior) if cfg.drsConfig else "N/A",
        "total_cpu_mhz": cluster.summary.totalCpu if cluster.summary else 0,
        "total_memory_gb": round(
            cluster.summary.totalMemory / (1024**3)
        ) if cluster.summary and cluster.summary.totalMemory else 0,
        "effective_cpu_mhz": cluster.summary.effectiveCpu if cluster.summary else 0,
        "effective_memory_gb": round(
            cluster.summary.effectiveMemory / 1024
        ) if cluster.summary and cluster.summary.effectiveMemory else 0,
    }


# ─── Create / Delete ─────────────────────────────────────────────────────────


def create_cluster(
    si: ServiceInstance,
    cluster_name: str,
    datacenter_name: str | None = None,
    ha_enabled: bool = False,
    drs_enabled: bool = False,
    drs_behavior: str = "fullyAutomated",
) -> str:
    """Create a new cluster in the specified datacenter."""
    if drs_behavior not in _VALID_DRS_BEHAVIORS:
        raise ClusterError(
            f"Invalid DRS behavior '{drs_behavior}'. "
            f"Valid: {sorted(_VALID_DRS_BEHAVIORS)}. "
            f"Pass --drs-behavior (MCP arg: drs_behavior) with one of those exact values."
        )

    # Check if cluster already exists
    existing = find_cluster_by_name(si, cluster_name)
    if existing is not None:
        raise ClusterError(
            f"Cluster '{cluster_name}' already exists. Run cluster_info "
            f"(CLI: vmware-aiops cluster info '{cluster_name}') to inspect it, "
            f"or retry create with a different name."
        )

    dc = _get_datacenter(si, datacenter_name)

    spec = vim.cluster.ConfigSpecEx(
        dasConfig=vim.cluster.DasConfigInfo(
            enabled=ha_enabled,
        ),
        drsConfig=vim.cluster.DrsConfigInfo(
            enabled=drs_enabled,
            defaultVmBehavior=vim.cluster.DrsConfigInfo.DrsBehavior(drs_behavior),
        ),
    )

    dc.hostFolder.CreateClusterEx(name=cluster_name, spec=spec)

    features = []
    if ha_enabled:
        features.append("HA")
    if drs_enabled:
        features.append(f"DRS({drs_behavior})")
    feature_str = f" with {', '.join(features)}" if features else ""

    return f"Cluster '{cluster_name}' created{feature_str}."


def delete_cluster(si: ServiceInstance, cluster_name: str) -> str:
    """Delete an empty cluster."""
    cluster = _require_cluster(si, cluster_name)

    if cluster.host and len(cluster.host) > 0:
        host_names = [h.name for h in cluster.host]
        raise ClusterError(
            f"Cluster '{cluster_name}' still has {len(cluster.host)} host(s): "
            f"{', '.join(host_names)}. Remove each one first with cluster_remove_host "
            f"(CLI: vmware-aiops cluster remove-host), then retry the delete."
        )

    task = cluster.Destroy_Task()
    _wait_for_task(task)
    return f"Cluster '{cluster_name}' deleted."


# ─── Host Management ─────────────────────────────────────────────────────────


def add_host_to_cluster(
    si: ServiceInstance,
    cluster_name: str,
    host_name: str,
) -> str:
    """Move an already-managed host into a cluster.

    The host must already be in vCenter inventory (standalone or in another cluster).
    To add a brand-new host to vCenter, use the vCenter UI or AddHost_Task API.
    """
    cluster = _require_cluster(si, cluster_name)
    host = find_host_by_name(si, host_name)
    if host is None:
        raise ClusterError(
            f"Host '{host_name}' not found in this vCenter's inventory. Run cluster_info "
            f"with name='{cluster_name}' to see that cluster's member hosts, or "
            f"list_esxi_hosts (vmware-monitor skill) for every host, then retry with an "
            f"exact name."
        )

    # Check if already in this cluster
    for h in cluster.host or []:
        if h.name == host_name:
            return f"Host '{host_name}' is already in cluster '{cluster_name}'."

    task = cluster.MoveInto_Task(host=[host])
    _wait_for_task(task, timeout=300)
    return f"Host '{host_name}' moved into cluster '{cluster_name}'."


def remove_host_from_cluster(
    si: ServiceInstance,
    cluster_name: str,
    host_name: str,
) -> str:
    """Remove a host from a cluster by moving it to standalone in the datacenter host folder.

    The host must be in maintenance mode before removal.
    """
    cluster = _require_cluster(si, cluster_name)
    host = find_host_by_name(si, host_name)
    if host is None:
        raise ClusterError(
            f"Host '{host_name}' not found in this vCenter's inventory. Run cluster_info "
            f"with name='{cluster_name}' to see that cluster's member hosts, or "
            f"list_esxi_hosts (vmware-monitor skill) for every host, then retry with an "
            f"exact name."
        )

    # Verify host is in this cluster
    in_cluster = any(h.name == host_name for h in (cluster.host or []))
    if not in_cluster:
        raise ClusterError(
            f"Host '{host_name}' is not in cluster '{cluster_name}'. Run cluster_info "
            f"with name='{cluster_name}' to see its actual members, then retry with a "
            f"host from that list."
        )

    if not host.runtime.inMaintenanceMode:
        raise ClusterError(
            f"Host '{host_name}' must be in maintenance mode before removal. "
            f"Enter it from the vSphere Client (Host > Maintenance Mode), or on the "
            f"host run: esxcli system maintenanceMode set --enable true. Confirm with "
            f"cluster_info, then retry."
        )

    # Walk up from cluster to find its owning datacenter
    parent = cluster.parent
    while parent and not isinstance(parent, vim.Datacenter):
        parent = parent.parent
    if parent is None:
        raise ClusterError(f"Cannot determine datacenter for cluster '{cluster_name}'")
    dc = parent

    # Move host to datacenter's host folder as standalone.
    # vim.Folder's method is MoveIntoFolder_Task (single 'list' param);
    # MoveInto_Task only exists on ClusterComputeResource.
    task = dc.hostFolder.MoveIntoFolder_Task([host])
    _wait_for_task(task, timeout=300)
    return f"Host '{host_name}' removed from cluster '{cluster_name}'."


# ─── Configure ────────────────────────────────────────────────────────────────


def configure_cluster(
    si: ServiceInstance,
    cluster_name: str,
    ha_enabled: bool | None = None,
    drs_enabled: bool | None = None,
    drs_behavior: str | None = None,
) -> str:
    """Reconfigure cluster HA/DRS settings."""
    if ha_enabled is None and drs_enabled is None and drs_behavior is None:
        return "Nothing to change. Specify --ha, --drs, or --drs-behavior."

    if drs_behavior is not None and drs_behavior not in _VALID_DRS_BEHAVIORS:
        raise ClusterError(
            f"Invalid DRS behavior '{drs_behavior}'. "
            f"Valid: {sorted(_VALID_DRS_BEHAVIORS)}. "
            f"Pass --drs-behavior (MCP arg: drs_behavior) with one of those exact values."
        )

    cluster = _require_cluster(si, cluster_name)

    spec = vim.cluster.ConfigSpecEx()
    changes = []

    if ha_enabled is not None:
        spec.dasConfig = vim.cluster.DasConfigInfo(enabled=ha_enabled)
        changes.append(f"HA={'ON' if ha_enabled else 'OFF'}")

    if drs_enabled is not None or drs_behavior is not None:
        drs_cfg = vim.cluster.DrsConfigInfo()
        if drs_enabled is not None:
            drs_cfg.enabled = drs_enabled
            changes.append(f"DRS={'ON' if drs_enabled else 'OFF'}")
        if drs_behavior is not None:
            drs_cfg.defaultVmBehavior = vim.cluster.DrsConfigInfo.DrsBehavior(drs_behavior)
            changes.append(f"DRS behavior={drs_behavior}")
        spec.drsConfig = drs_cfg

    task = cluster.ReconfigureComputeResource_Task(spec=spec, modify=True)
    _wait_for_task(task)
    return f"Cluster '{cluster_name}' reconfigured: {', '.join(changes)}."


# ─── DRS affinity / anti-affinity rules ──────────────────────────────────────

_VM_VM_RULE_CLASSES = {
    vim.cluster.AffinityRuleSpec: "affinity",
    vim.cluster.AntiAffinityRuleSpec: "antiAffinity",
}

_CREATABLE_RULE_TYPES = {
    "affinity": vim.cluster.AffinityRuleSpec,
    "antiAffinity": vim.cluster.AntiAffinityRuleSpec,
}


def _rule_type_label(rule) -> str:
    """Human label for a rule's concrete type. VM-VM types get stable names."""
    for cls, label in _VM_VM_RULE_CLASSES.items():
        if isinstance(rule, cls):
            return label
    if isinstance(rule, vim.cluster.VmHostRuleInfo):
        return "vmHost"
    return type(rule).__name__.rsplit(".", 1)[-1]


def _vm_name(vm) -> str:
    """Server round-trip for a VM ref's name (seam for tests)."""
    return vm.name


def _rule_summary(rule) -> dict:
    """Projection of one rule. VM names are resolved lazily - rule counts are
    small (units, not hundreds), so the per-VM read is acceptable here."""
    out = {
        "key": rule.key,
        "name": sanitize(rule.name, 200),
        "type": _rule_type_label(rule),
        "enabled": bool(rule.enabled),
        "mandatory": bool(rule.mandatory) if rule.mandatory is not None else False,
    }
    if isinstance(rule, tuple(_VM_VM_RULE_CLASSES)):
        out["vms"] = sorted(sanitize(_vm_name(vm), 200) for vm in rule.vm or [])
    elif isinstance(rule, vim.cluster.VmHostRuleInfo):
        out["vm_group"] = sanitize(rule.vmGroupName or "", 200)
        out["affine_host_group"] = sanitize(rule.affineHostGroupName or "", 200)
        out["anti_affine_host_group"] = sanitize(rule.antiAffineHostGroupName or "", 200)
    return out


def _cluster_rules(cluster) -> list:
    cfg = cluster.configurationEx
    return list(cfg.rule or []) if cfg is not None else []


def _require_rule(cluster, rule_name: str):
    """Find a rule by exact name; refuse ambiguity, teach on miss."""
    matches = [r for r in _cluster_rules(cluster) if r.name == rule_name]
    if not matches:
        names = sorted(sanitize(r.name, 100) for r in _cluster_rules(cluster))
        raise ClusterError(
            f"No rule named '{sanitize(rule_name, 100)}' on cluster "
            f"'{sanitize(cluster.name, 100)}'. Existing rules: "
            f"{', '.join(names) or 'none'}. Names are matched exactly."
        )
    if len(matches) > 1:
        raise ClusterError(
            f"Rule name '{sanitize(rule_name, 100)}' is ambiguous - "
            f"{len(matches)} rules share it (keys "
            f"{sorted(r.key for r in matches)}). Rename one in the UI first."
        )
    return matches[0]


def _apply_rule_spec(cluster, operation: str, rule=None, remove_key=None) -> None:
    rule_spec = vim.cluster.RuleSpec(operation=operation)
    if rule is not None:
        rule_spec.info = rule
    if remove_key is not None:
        rule_spec.removeKey = remove_key
    spec = vim.cluster.ConfigSpecEx(rulesSpec=[rule_spec])
    task = cluster.ReconfigureComputeResource_Task(spec=spec, modify=True)
    _wait_for_task(task)


def list_drs_rules(si: ServiceInstance, cluster_name: str) -> dict:
    """All DRS rules on a cluster (VM-VM affinity/anti-affinity, VM-Host)."""
    cluster = _require_cluster(si, cluster_name)
    rules = [_rule_summary(r) for r in _cluster_rules(cluster)]
    return {
        "cluster": sanitize(cluster.name, 200),
        "count": len(rules),
        "rules": sorted(rules, key=lambda r: r["name"]),
    }


def set_drs_rule_enabled(
    si: ServiceInstance,
    cluster_name: str,
    rule_name: str,
    enabled: bool,
    confirm: bool = False,
) -> dict:
    """Flip an existing rule's enabled flag - confirm-gated, idempotent."""
    cluster = _require_cluster(si, cluster_name)
    rule = _require_rule(cluster, rule_name)
    change = {
        "cluster": sanitize(cluster.name, 200),
        "rule": _rule_summary(rule),
        "enabled": enabled,
    }
    if bool(rule.enabled) == enabled:
        return {
            "action": "noop",
            "unchanged": change,
            "hint": f"Rule '{rule.name}' is already "
                    f"{'enabled' if enabled else 'disabled'}.",
        }
    if not confirm:
        return {
            "action": "preview",
            "would_set": change,
            "hint": "Re-run with confirm=True to apply.",
        }
    rule.enabled = enabled
    _apply_rule_spec(cluster, "edit", rule=rule)
    now = _require_rule(cluster, rule_name)
    return {"action": "set", "set": change, "rule_now": _rule_summary(now)}


def _resolve_rule_vms(si: ServiceInstance, cluster, vm_names: list[str]) -> list:
    """Resolve rule-member names to VM refs, requiring cluster membership."""
    vms = []
    for name in vm_names:
        vm = find_vm_by_name(si, name)
        if vm is None:
            raise ClusterError(f"VM '{sanitize(name, 200)}' not found.")
        pool = vm.resourcePool
        if pool is None or pool.owner != cluster:
            raise ClusterError(
                f"VM '{sanitize(name, 200)}' is not in cluster "
                f"'{sanitize(cluster.name, 100)}' (or is a template) - "
                "rule members must belong to the rule's cluster."
            )
        vms.append(vm)
    return vms


def create_drs_rule(
    si: ServiceInstance,
    cluster_name: str,
    rule_name: str,
    rule_type: str,
    vm_names: list[str],
    enabled: bool = True,
    confirm: bool = False,
) -> dict:
    """Create a VM-VM affinity or anti-affinity rule - confirm-gated.

    VM-Host rules are out of scope (they hang off cluster VM/host groups -
    a separate management surface).
    """
    cluster = _require_cluster(si, cluster_name)

    if rule_type not in _CREATABLE_RULE_TYPES:
        raise ClusterError(
            f"rule_type must be one of {sorted(_CREATABLE_RULE_TYPES)} "
            f"(got '{sanitize(rule_type, 60)}'). VM-Host rules are managed "
            "via cluster groups and are not created here."
        )
    if any(r.name == rule_name for r in _cluster_rules(cluster)):
        raise ClusterError(
            f"A rule named '{sanitize(rule_name, 100)}' already exists on "
            f"'{sanitize(cluster.name, 100)}'."
        )
    unique_vms = list(dict.fromkeys(vm_names or []))
    if len(unique_vms) < 2:
        raise ClusterError(
            "A VM-VM rule needs at least 2 distinct VMs "
            f"(got {len(unique_vms)})."
        )

    vms = _resolve_rule_vms(si, cluster, unique_vms)

    would = {
        "cluster": sanitize(cluster.name, 200),
        "name": sanitize(rule_name, 200),
        "type": rule_type,
        "enabled": enabled,
        "vms": [sanitize(n, 200) for n in unique_vms],
    }
    if not confirm:
        return {
            "action": "preview",
            "would_create": would,
            "hint": "Re-run with confirm=True to create.",
        }

    info = _CREATABLE_RULE_TYPES[rule_type](
        name=rule_name, enabled=enabled, userCreated=True, vm=vms
    )
    _apply_rule_spec(cluster, "add", rule=info)
    now = _require_rule(cluster, rule_name)
    return {"action": "created", "created": _rule_summary(now)}


def delete_drs_rule(
    si: ServiceInstance,
    cluster_name: str,
    rule_name: str,
    confirm: bool = False,
) -> dict:
    """Delete a VM-VM affinity/anti-affinity rule - confirm-gated.

    REFUSES non-VM-VM rules (VM-Host rules can carry licensing/compliance
    placement constraints and hang off cluster groups - manage those in the
    UI). The preview and the result both record the full rule definition,
    so a mistaken delete can be recreated from the audit trail.
    """
    cluster = _require_cluster(si, cluster_name)
    rule = _require_rule(cluster, rule_name)
    if not isinstance(rule, tuple(_VM_VM_RULE_CLASSES)):
        raise ClusterError(
            f"REFUSED: '{sanitize(rule_name, 100)}' is a "
            f"{_rule_type_label(rule)} rule, not VM-VM. VM-Host and other "
            "rule types can carry licensing/compliance placement constraints "
            "- manage them in the vSphere UI."
        )
    doomed = {
        "cluster": sanitize(cluster.name, 200),
        "rule": _rule_summary(rule),
    }
    if not confirm:
        return {
            "action": "preview",
            "would_delete": doomed,
            "hint": "Re-run with confirm=True to delete. The definition "
                    "above is what you would recreate it from.",
        }
    _apply_rule_spec(cluster, "remove", remove_key=rule.key)
    remaining = [r.name for r in _cluster_rules(cluster) if r.name == rule_name]
    if remaining:
        raise ClusterError(
            f"Delete task completed but '{sanitize(rule_name, 100)}' is "
            "still present - inspect the cluster in the UI."
        )
    return {"action": "deleted", "deleted": doomed}
