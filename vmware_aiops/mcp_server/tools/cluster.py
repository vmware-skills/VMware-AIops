"""Cluster tools: create/delete clusters, add/remove hosts, HA/DRS config, info."""

from typing import Optional

from vmware_policy import vmware_tool

from vmware_aiops.mcp_server._shared import _get_connection, mcp, tool_errors


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="medium")
@tool_errors("str")
def cluster_create(
    name: str,
    datacenter: Optional[str] = None,
    ha: bool = False,
    drs: bool = False,
    drs_behavior: str = "fullyAutomated",
    target: Optional[str] = None,
) -> str:
    """[WRITE] Create a new empty cluster in a datacenter, optionally enabling HA and DRS.

    Fails with a clear error (no partial state) if the name already exists or
    drs_behavior is invalid. Then add hosts with cluster_add_host, change HA/DRS
    later with cluster_configure, and verify with cluster_info. Returns a status
    string naming the features enabled.

    Args:
        name: Name for the new cluster; must be unique in the datacenter.
        datacenter: Datacenter name; omit for the first one on the target.
        ha: True enables vSphere HA (default False).
        drs: True enables DRS (default False).
        drs_behavior: "fullyAutomated" (default), "partiallyAutomated", or
            "manual". Only takes effect when drs=True.
        target: vCenter target from config.yaml; omit for the default target.
    """
    from vmware_aiops.ops.cluster_mgmt import create_cluster
    si = _get_connection(target)
    return create_cluster(
        si, cluster_name=name, datacenter_name=datacenter,
        ha_enabled=ha, drs_enabled=drs, drs_behavior=drs_behavior,
    )


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="high")
@tool_errors("str")
def cluster_delete(name: str, target: Optional[str] = None) -> str:
    """[WRITE] Delete an empty cluster (no hosts must remain).

    Returns a status string. Check cluster_info first and evacuate any members
    with cluster_remove_host, or vCenter rejects the call.

    Args:
        name: Name of the cluster to delete.
        target: Optional vCenter target name from config.
    """
    from vmware_aiops.ops.cluster_mgmt import delete_cluster
    si = _get_connection(target)
    return delete_cluster(si, name)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="medium")
@tool_errors("str")
def cluster_add_host(
    cluster_name: str,
    host_name: str,
    target: Optional[str] = None,
) -> str:
    """[WRITE] Move an ESXi host that vCenter already manages into a cluster.

    The host must already be in vCenter inventory (standalone or in another cluster) —
    this does NOT register brand-new hosts and takes no host credentials; use the
    vCenter UI for first-time registration. Idempotent: a host already in the cluster
    returns success without change. Maintenance mode is not required to join (it IS
    required by cluster_remove_host). Check membership first with cluster_info.
    Returns a status string: moved, already-in-cluster, or a not-found error.

    Args:
        cluster_name: Destination cluster (create with cluster_create).
        host_name: Host name as shown in vCenter inventory, usually the FQDN,
            e.g. "esxi-01.lab.local".
        target: vCenter target from config.yaml; omit for the default target.
    """
    from vmware_aiops.ops.cluster_mgmt import add_host_to_cluster
    si = _get_connection(target)
    return add_host_to_cluster(si, cluster_name=cluster_name, host_name=host_name)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="medium")
@tool_errors("str")
def cluster_remove_host(
    cluster_name: str,
    host_name: str,
    target: Optional[str] = None,
) -> str:
    """[WRITE] Remove a host from a cluster (host must be in maintenance mode).

    Returns a status string. Run cluster_info first for the exact member host
    names and their maintenance_mode state. The host is not deleted — it stays
    in vCenter inventory standalone; use cluster_add_host to move it back.

    Args:
        cluster_name: Cluster to remove the host from.
        host_name: ESXi host name to remove (from cluster_info output).
        target: Optional vCenter target name from config.
    """
    from vmware_aiops.ops.cluster_mgmt import remove_host_from_cluster
    si = _get_connection(target)
    return remove_host_from_cluster(si, cluster_name=cluster_name, host_name=host_name)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="medium")
@tool_errors("str")
def cluster_configure(
    name: str,
    ha: Optional[bool] = None,
    drs: Optional[bool] = None,
    drs_behavior: Optional[str] = None,
    target: Optional[str] = None,
) -> str:
    """[WRITE] Reconfigure cluster HA/DRS settings.

    Returns a status string naming what changed. Pass only the fields to change;
    None leaves a setting untouched — then verify with cluster_info. drs_behavior
    applies only when DRS is enabled.

    Args:
        name: Cluster name.
        ha: Enable (True) or disable (False) HA, or None to leave unchanged.
        drs: Enable (True) or disable (False) DRS, or None to leave unchanged.
        drs_behavior: DRS behavior: "fullyAutomated", "partiallyAutomated", or "manual".
        target: Optional vCenter target name from config.
    """
    from vmware_aiops.ops.cluster_mgmt import configure_cluster
    si = _get_connection(target)
    return configure_cluster(
        si, cluster_name=name,
        ha_enabled=ha, drs_enabled=drs, drs_behavior=drs_behavior,
    )


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@vmware_tool(risk_level="low")
@tool_errors("dict")
def cluster_info(name: str, target: Optional[str] = None) -> dict:
    """[READ] Get detailed cluster information: member hosts, HA/DRS config, resource capacity.

    Read-only, no side effects. Use before cluster_add_host / cluster_remove_host (shows
    membership and per-host maintenance mode) and to verify cluster_configure changes.

    Args:
        name: Exact cluster name.
        target: vCenter target name from config.yaml; omit to use the default target.

    Returns:
        Dict with name, host_count, hosts (each: name, connection_state, power_state,
        maintenance_mode), ha_enabled, ha_admission_control, drs_enabled, drs_behavior,
        total/effective CPU (MHz) and memory (GB). Errors return a dict with "error" + hint.
    """
    from vmware_aiops.ops.cluster_mgmt import get_cluster_info
    si = _get_connection(target)
    return get_cluster_info(si, name)


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@vmware_tool(risk_level="low")
@tool_errors("dict")
def list_drs_rules(cluster: str, target: Optional[str] = None) -> dict:
    """[READ] List a cluster's DRS rules: VM-VM affinity/anti-affinity and VM-Host.

    Per rule: key, name, type (affinity / antiAffinity / vmHost), enabled,
    mandatory, and the member VM names (VM-VM) or group names (VM-Host).
    The verify pair for create/delete/set_drs_rule_enabled.

    Args:
        cluster: Exact cluster name.
        target: vCenter target name from config.yaml; omit to use the default target.

    Returns:
        Dict with cluster, count, and rules sorted by name. Errors return a
        dict with "error" + hint.
    """
    from vmware_aiops.ops.cluster_mgmt import list_drs_rules as _list
    si = _get_connection(target)
    return _list(si, cluster)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@vmware_tool(risk_level="medium")
@tool_errors("dict")
def set_drs_rule_enabled(
    cluster: str,
    rule_name: str,
    enabled: bool,
    confirm: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE] Enable or disable an existing DRS rule - preview/confirm gated.

    The day-2 toggle: anti-affinity rules often must be disabled while a
    cluster is temporarily too small to satisfy them, then re-enabled when
    hosts return. Idempotent - matching state returns a noop, no write.
    Names are matched exactly; ambiguous names refuse. Audited.

    Args:
        cluster: Exact cluster name.
        rule_name: Exact rule name (see list_drs_rules).
        enabled: True enables the rule; False disables it.
        confirm: False previews; True applies.
        target: vCenter target name from config.yaml; omit to use the default target.

    Returns:
        Preview dict (action="preview"), noop dict (action="noop"), or
        result dict (action="set", rule_now). Errors return "error" + hint.
    """
    from vmware_aiops.ops.cluster_mgmt import set_drs_rule_enabled as _set
    si = _get_connection(target)
    return _set(si, cluster, rule_name=rule_name, enabled=enabled, confirm=confirm)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="medium")
@tool_errors("dict")
def create_drs_rule(
    cluster: str,
    rule_name: str,
    rule_type: str,
    vm_names: list[str],
    enabled: bool = True,
    confirm: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE] Create a VM-VM DRS rule (affinity or anti-affinity) - preview/confirm gated.

    rule_type "affinity" keeps the listed VMs together; "antiAffinity"
    keeps them apart (e.g. redundant appliance pairs on separate hosts).
    Requires >=2 distinct VMs, all members of the cluster. VM-Host rules
    hang off cluster VM/host groups and are not created here. Verify with
    list_drs_rules. Audited.

    Args:
        cluster: Exact cluster name.
        rule_name: Name for the new rule; must be unique on the cluster.
        rule_type: "affinity" or "antiAffinity".
        vm_names: VM names the rule governs (>=2, all in the cluster).
        enabled: Create the rule enabled (default) or disabled.
        confirm: False previews; True creates.
        target: vCenter target name from config.yaml; omit to use the default target.

    Returns:
        Preview dict (action="preview", would_create) or result dict
        (action="created", created incl. the assigned key). Errors return
        a dict with "error" + hint.
    """
    from vmware_aiops.ops.cluster_mgmt import create_drs_rule as _create
    si = _get_connection(target)
    return _create(
        si, cluster, rule_name=rule_name, rule_type=rule_type,
        vm_names=vm_names, enabled=enabled, confirm=confirm,
    )


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="high")
@tool_errors("dict")
def delete_drs_rule(
    cluster: str,
    rule_name: str,
    confirm: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE] Delete a VM-VM DRS rule - confirm-gated, guarded.

    REFUSES non-VM-VM rules: VM-Host rules can carry licensing/compliance
    placement constraints (must-run-on licensed hosts) and hang off cluster
    groups - manage those in the vSphere UI. The preview and result both
    record the full rule definition so a mistaken delete can be recreated
    from the audit trail. Audited.

    Args:
        cluster: Exact cluster name.
        rule_name: Exact rule name (see list_drs_rules).
        confirm: False previews; True deletes.
        target: vCenter target name from config.yaml; omit to use the default target.

    Returns:
        Preview dict (action="preview", would_delete) or result dict
        (action="deleted", deleted). Errors return a dict with "error" + hint.
    """
    from vmware_aiops.ops.cluster_mgmt import delete_drs_rule as _delete
    si = _get_connection(target)
    return _delete(si, cluster, rule_name=rule_name, confirm=confirm)
