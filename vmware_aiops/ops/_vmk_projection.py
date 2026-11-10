"""Read VMkernel configuration and represent unknown service and routing state."""

from __future__ import annotations


from typing import TYPE_CHECKING

from pyVmomi import vim

from vmware_policy import sanitize


if TYPE_CHECKING:
    pass


def _connection_state(host: vim.HostSystem) -> str:
    """``runtime.connectionState`` as a plain string, ``"unknown"`` if unreadable."""
    return str(getattr(getattr(host, "runtime", None), "connectionState", "") or "unknown")


def _vmk_services(host: vim.HostSystem) -> dict[str, list[str]] | None:
    """Map vmk device -> host services it is selected for (management, vmotion, ...).

    Returns ``None`` when the service map CANNOT be read (configManager,
    virtualNicManager or its info unavailable - disconnected host, restricted
    role, API hiccup). Callers MUST treat None as "unverifiable", never as "no
    services": an empty dict here once let remove_host_vmk delete interfaces it
    claimed to protect.
    """
    # configManager itself is None on a host vCenter has lost contact with, so
    # reaching straight through it raised the same bare AttributeError the
    # notResponding hosts produced one function down (VCF 9.1, 2026-08-30).
    cfg_mgr = getattr(host, "configManager", None)
    vnic_mgr = getattr(cfg_mgr, "virtualNicManager", None) if cfg_mgr else None
    if vnic_mgr is None or vnic_mgr.info is None:
        return None
    services: dict[str, list[str]] = {}
    for net_cfg in vnic_mgr.info.netConfig or []:
        for selected in net_cfg.selectedVnic or []:
            for cand in net_cfg.candidateVnic or []:
                if cand.key == selected:
                    services.setdefault(cand.device, []).append(net_cfg.nicType)
    return services


def _vmk_carries_default_route(host: vim.HostSystem, vmk: str) -> bool | None:
    """True if ``vmk`` is the device of any default (prefix-0) route on the host.

    Checks the resolved route table plus every netstack instance's table.
    Returns ``None`` when the routing state cannot be read - callers must
    treat that as unverifiable (fail closed), not as "no gateway".
    """
    try:
        net = host.config.network
        routes = []
        rt = getattr(net, "routeTableInfo", None)
        if rt is not None:
            routes.extend(rt.ipRoute or [])
        for stack in getattr(net, "netStackInstance", None) or []:
            srt = getattr(stack, "routeTableInfo", None)
            if srt is not None:
                routes.extend(srt.ipRoute or [])
        return any(
            getattr(r, "prefixLength", None) == 0 and getattr(r, "deviceName", None) == vmk
            for r in routes
        )
    except Exception:
        return None


_CONNECTED = "connected"


def _unread_note(state: str) -> str:
    """Why this host's adapters are not a measurement, in the row that says so."""
    if state == "unknown":
        # connectionState itself was unreadable: the account cannot read this
        # host (e.g. Read-only on one VM only, lab 2026-10-18). Not "unreachable".
        return (
            "This account cannot read this host's state, so its VMkernel adapters "
            "were not read (reachable=false here means not read, not that vCenter "
            "lost the host). An empty list is NOT a report that it has none - "
            "grant Read-only on the host and re-run."
        )
    if state != _CONNECTED:
        return (
            f"vCenter cannot reach this host (connectionState={sanitize(state, 40)}), "
            "so anything shown for it is vCenter's last cached view rather than "
            "the host's current state. An absent or empty adapter list here is "
            "NOT a report that the host has no VMkernel adapters - reconnect the "
            "host in vCenter and re-run to find out."
        )
    return (
        "vCenter reports this host as connected but returned no network "
        "configuration for it, so its VMkernel adapters were not read. This is "
        "not a report that the host has none."
    )


def _vmk_row(
    host_display: str,
    vnic,
    services: list[str] | None,
    *,
    reachable: bool,
    note: str | None,
) -> dict:
    """One list row. ``vnic=None`` builds the placeholder for an unread host.

    Every unread fact is ``None`` rather than ``[]``/``False``/``0``: those are
    claims about the host, and this row exists precisely because nobody managed
    to make one.
    """
    spec = vnic.spec if vnic is not None else None
    ip = spec.ip if spec is not None else None
    dv_port = getattr(spec, "distributedVirtualPort", None) if spec is not None else None
    netstack = getattr(spec, "netStackInstanceKey", None) if spec is not None else None
    return {
        "host": host_display,
        "device": sanitize(vnic.device, 40) if vnic is not None else None,
        "reachable": reachable,
        "ip": ip.ipAddress if ip else None,
        "netmask": ip.subnetMask if ip else None,
        "dhcp": bool(ip.dhcp) if ip else None,
        "mtu": spec.mtu if spec is not None else None,
        "mac": spec.mac if spec is not None else None,
        "portgroup": (sanitize(vnic.portgroup, 200) or None) if vnic is not None else None,
        "dvs_port": dv_port.portgroupKey if dv_port else None,
        "netstack": sanitize(netstack, 80) if netstack else None,
        "services": services,
        "note": note,
    }
