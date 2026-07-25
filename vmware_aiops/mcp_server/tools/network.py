"""Network tools: dvSwitch portgroups + host VMkernel adapters and MTU diagnostics."""

from typing import Optional

from vmware_policy import vmware_tool

from vmware_aiops.mcp_server._shared import _get_connection, mcp, tool_errors
from vmware_aiops.ops import host_network_mgmt, network_mgmt

# ---------------------------------------------------------------------------
# dvSwitch portgroups
# ---------------------------------------------------------------------------


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@vmware_tool(risk_level="low")
@tool_errors("dict")
def list_dvs_portgroups(
    dvs_name: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
    target: Optional[str] = None,
) -> dict:
    """[READ] List distributed virtual portgroups, optionally scoped to one dvSwitch.

    Per portgroup: name, parent dvSwitch, binding type (earlyBinding /
    ephemeral), VLAN setting (id, trunk ranges, or pvlan), configured port
    count, and whether it is the switch's uplink portgroup. Use to verify
    create_dvs_portgroup results or to survey network config before changes.

    Args:
        dvs_name: dvSwitch name to scope to; omit to list across all switches.
        limit: Max portgroups to return (default 200).
        offset: Skip this many portgroups first (paging).
        target: vCenter target name from config.yaml; omit to use the default target.

    Returns:
        Dict with total, returned, and portgroups list. Errors return a
        dict with "error" + hint.
    """
    si = _get_connection(target)
    return network_mgmt.list_dvs_portgroups(
        si, dvs_name=dvs_name, limit=limit, offset=offset
    )


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="medium")
@tool_errors("dict")
def create_dvs_portgroup(
    name: str,
    dvs_name: str,
    vlan_id: int,
    binding: str = "earlyBinding",
    num_ports: int = 8,
    confirm: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE] Create a VLAN-tagged portgroup on a dvSwitch - preview/confirm gated.

    confirm=False (default) validates everything (switch exists, name free,
    binding and VLAN legal) and returns the exact spec that WOULD be created
    without writing anything. confirm=True creates the portgroup and waits
    for the task. Verify afterwards with list_dvs_portgroups. Audited.

    binding="ephemeral" creates a portgroup with no pre-created port pool,
    attachable from the ESXi host client even when vCenter is down - use for
    a self-hosted VCSA's own management portgroup. num_ports is ignored for
    ephemeral. lateBinding is deprecated by vSphere and not offered.

    Args:
        name: Name for the new portgroup; must be unique on the switch.
        dvs_name: Name of the distributed virtual switch to create it on.
        vlan_id: VLAN ID to tag (0-4094; 0 = none).
        binding: "earlyBinding" (default) or "ephemeral".
        num_ports: Port count for earlyBinding portgroups (default 8).
        confirm: False previews; True creates.
        target: vCenter target name from config.yaml; omit to use the default target.

    Returns:
        Preview dict (action="preview", would_create) or result dict
        (action="created", created). Errors return a dict with "error" + hint.
    """
    si = _get_connection(target)
    return network_mgmt.create_dvs_portgroup(
        si, name=name, dvs_name=dvs_name, vlan_id=vlan_id,
        binding=binding, num_ports=num_ports, confirm=confirm,
    )


# ---------------------------------------------------------------------------
# Host VMkernel adapters + MTU diagnostics
# ---------------------------------------------------------------------------


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@vmware_tool(risk_level="low")
@tool_errors("dict")
def list_host_vmks(
    host_name: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    target: Optional[str] = None,
) -> dict:
    """[READ] List VMkernel adapters, optionally scoped to one ESXi host.

    Per vmk: device, IP/netmask/dhcp, MTU, MAC, portgroup (standard or DVS),
    netstack, and which host services it is selected for (management,
    vmotion, vsan, ...). The verify pair for add_host_vmk/remove_host_vmk.

    Args:
        host_name: ESXi host name; omit to list across all hosts.
        limit: Max vmks to return (default 100).
        offset: Skip this many vmks first (paging).
        target: vCenter target name from config.yaml; omit to use the default target.

    Returns:
        Dict with total, returned, and vmks list (services is null when a
        host's service map could not be read). Errors return "error" + hint.
    """
    si = _get_connection(target)
    return host_network_mgmt.list_host_vmks(
        si, host_name=host_name, limit=limit, offset=offset
    )


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="medium")
@tool_errors("dict")
def add_host_vmk(
    host_name: str,
    portgroup: str,
    ip: str,
    netmask: str,
    mtu: int = 1500,
    confirm: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE] Add a static-IP VMkernel adapter on a DVS portgroup - preview/confirm gated.

    Deliberately minimal shape for throwaway test vmks on L2-only segments
    (e.g. a TEP VLAN): static IPv4, NO gateway, NO services enabled. The DVS
    port allocation is handled internally - pass the distributed portgroup
    name. confirm=False validates (host + portgroup exist, IP/netmask/MTU
    legal, IP not already on the host) and returns the exact spec without
    writing; confirm=True creates and returns the assigned device name.
    Verify with list_host_vmks; remove with remove_host_vmk. Audited.

    Args:
        host_name: ESXi host to add the vmk on.
        portgroup: Distributed portgroup name to connect to.
        ip: Static IPv4 address for the vmk.
        netmask: Subnet mask (e.g. 255.255.255.0).
        mtu: MTU for the vmk (default 1500; 9000 for jumbo tests).
        confirm: False previews; True creates.
        target: vCenter target name from config.yaml; omit to use the default target.

    Returns:
        Preview dict (action="preview") or result dict (action="created",
        device=e.g. "vmk2"). Errors return a dict with "error" + hint.
    """
    si = _get_connection(target)
    return host_network_mgmt.add_host_vmk(
        si, host_name=host_name, portgroup=portgroup, ip=ip,
        netmask=netmask, mtu=mtu, confirm=confirm,
    )


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="high")
@tool_errors("dict")
def remove_host_vmk(
    host_name: str,
    vmk: str,
    confirm: bool = False,
    force_unprotected: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE] Remove a VMkernel adapter - confirm-gated, guarded, fail-closed.

    REFUSES (unless force_unprotected=True) when the vmk is selected for any
    host service (management/vmotion/vsan/...), lives on a non-default
    netstack (NSX TEPs on vxlan, dedicated vmotion/provisioning stacks -
    never visible in the service map), carries a default gateway route, or
    when any of that CANNOT be verified - unverifiable is treated as unsafe,
    never as clear. Test vmks created by add_host_vmk trip none of these and
    remove cleanly without force.

    ABSOLUTE, no override: the host's only management-enabled vmk is never
    removable - this call rides the interface it would delete.

    force_unprotected=True (together with confirm=True) overrides the
    non-absolute protections for deliberate teardown; the override and every
    bypassed protection are recorded in the result (and the audit trail).

    Args:
        host_name: ESXi host the vmk lives on.
        vmk: Device name to remove (e.g. "vmk2").
        confirm: False previews; True removes.
        force_unprotected: True bypasses the non-absolute protections above.
        target: vCenter target name from config.yaml; omit to use the default target.

    Returns:
        Preview dict (action="preview") or result dict (action="removed",
        plus forced/protections_bypassed when overridden). Errors return a
        dict with "error" + hint.
    """
    si = _get_connection(target)
    return host_network_mgmt.remove_host_vmk(
        si, host_name=host_name, vmk=vmk, confirm=confirm,
        force_unprotected=force_unprotected,
    )


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@vmware_tool(risk_level="medium")
@tool_errors("dict")
def set_vmk_service(
    host_name: str,
    vmk: str,
    service: str,
    enabled: bool,
    confirm: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE] Enable/disable a host service on an existing vmk - preview/confirm gated.

    Completes the add_host_vmk story: adapters are created serviceless by
    design, then tagged here (e.g. enable "vmotion" on a new vMotion vmk).
    Idempotent - re-applying the current state returns a no-write noop.
    Verify with list_host_vmks (the services field). Audited.

    Valid services are the vSphere nicType names: management, vmotion, vsan,
    vSphereProvisioning, faultToleranceLogging, vSphereReplication,
    vSphereReplicationNFC, vSphereBackupNFC, ptp, and the nvme/vsan variants.
    Note "vSphereProvisioning", not "provisioning".

    FAIL CLOSED: refuses both directions when the host's service map cannot
    be read. ABSOLUTE, no override: disabling management on the host's only
    management-enabled vmk - the call rides the interface it would untag.

    Args:
        host_name: ESXi host the vmk lives on.
        vmk: Device name to change (e.g. "vmk3").
        service: Service/nicType name to enable or disable.
        enabled: True selects the vmk for the service; False deselects.
        confirm: False previews; True applies.
        target: vCenter target name from config.yaml; omit to use the default target.

    Returns:
        Preview dict (action="preview"), noop dict (action="noop") when the
        state already matches, or result dict (action="set", services_now).
        Errors return a dict with "error" + hint.
    """
    si = _get_connection(target)
    return host_network_mgmt.set_vmk_service(
        si, host_name=host_name, vmk=vmk, service=service,
        enabled=enabled, confirm=confirm,
    )


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@vmware_tool(risk_level="medium")
@tool_errors("dict")
def vmk_ping(
    host_name: str,
    source_vmk: str,
    dest_ip: str,
    size: int = 56,
    df: bool = False,
    count: int = 3,
    netstack: Optional[str] = None,
    target: Optional[str] = None,
) -> dict:
    """[READ] DF-bit-capable ping sourced from a host vmk - MTU path validation.

    Runs `esxcli network diag ping` on the ESXi host through the vSphere API
    (no host SSH). df=True sets Don't-Fragment so an oversized packet FAILS
    instead of fragmenting - that failure is the diagnostic:
    - df=True size=1572 proves a >=1600 MTU path (overlay/TEP floor)
    - df=True size=8972 proves full jumbo (9000 minus 28 bytes overhead)
    A too-big result reports 'Message too long' in the fault field rather
    than erroring - read success + fault together.

    Args:
        host_name: ESXi host to source the ping from.
        source_vmk: VMkernel device to source from (e.g. "vmk2").
        dest_ip: IPv4 address to ping.
        size: ICMP payload bytes (default 56). Path proves size+28 MTU.
        df: True sets the Don't-Fragment bit (the MTU probe mode).
        count: Packets to send (default 3, max 60).
        netstack: Optional netstack instance (e.g. "vxlan" for real TEP vmks).
        target: vCenter target name from config.yaml; omit to use the default target.

    Returns:
        Dict with request, success, and summary (transmitted/received/loss/
        rtt) or fault (the esxcli failure text). Errors return "error" + hint.
    """
    si = _get_connection(target)
    return host_network_mgmt.vmk_ping(
        si, host_name=host_name, source_vmk=source_vmk, dest_ip=dest_ip,
        size=size, df=df, count=count, netstack=netstack,
    )
