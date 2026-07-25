"""Host VMkernel adapter management + MTU-path diagnostic.

add/remove/list vmks ride the standard vSphere API (HostNetworkSystem).
vmk_ping rides esxcli-over-API: HostSystem.RetrieveManagedMethodExecuter()
-> ExecuteSoap on the host's "network diag ping" CLI handler - the same
mechanism PowerCLI's Get-EsxCli uses, works through a vCenter connection,
no host SSH required.

Design notes:
- add_host_vmk sets NO gateway (vSphere vmk IpConfig has no gateway field;
  default routes are per-netstack) and enables NO services - both deliberate:
  the use case is throwaway test vmks on L2-only segments (e.g. TEP VLANs).
- remove_host_vmk FAILS CLOSED. It refuses when the vmk is selected for ANY
  host service (management/vMotion/vSAN/...), when it lives on a non-default
  netstack (vxlan/NSX TEPs, dedicated vmotion/provisioning stacks - those
  vmks NEVER appear in the service map, so the map alone cannot protect
  them), when it carries a default gateway route, or when any of that CANNOT
  be verified. ``force_unprotected=True`` (with ``confirm=True``) overrides
  every protection except one absolute: the host's only management-enabled
  vmk is never removable - the call rides the interface it would delete, and
  after it succeeds nothing can reach the host to undo it.
"""

from __future__ import annotations

import ipaddress
import re
from typing import TYPE_CHECKING

from pyVmomi import vim
from vmware_policy import sanitize

from vmware_aiops.connection import get_verify_ssl
from vmware_aiops.ops.inventory import _collect, find_host_by_name

if TYPE_CHECKING:
    from pyVmomi.vim import ServiceInstance


def _get_objects(si: ServiceInstance, obj_type: list, recursive: bool = True) -> list:
    """Container-view walk returning raw managed objects.

    Local on purpose: the module enumerates small scoped sets (portgroups,
    hosts) and reads nested config off each object, which the batched
    PropertyCollector helpers in ``inventory`` don't cover; keeping the
    helper here also keeps the module free of private cross-module imports.
    """
    content = si.RetrieveContent()
    container = content.viewManager.CreateContainerView(
        content.rootFolder, obj_type, recursive
    )
    try:
        return list(container.view)
    finally:
        container.Destroy()


class HostNetworkError(Exception):
    """Raised on host-network operation failures."""


class HostNotFoundError(HostNetworkError):
    """Raised when an ESXi host is not found by name."""


_MTU_MIN, _MTU_MAX = 68, 9190


def _require_host(si: ServiceInstance, host_name: str) -> vim.HostSystem:
    host = find_host_by_name(si, host_name)
    if host is None:
        raise HostNotFoundError(f"Host '{host_name}' not found")
    return host


def _find_dv_portgroup(si: ServiceInstance, name: str) -> vim.dvs.DistributedVirtualPortgroup:
    for pg in _get_objects(si, [vim.dvs.DistributedVirtualPortgroup]):
        if pg.name == name:
            return pg
    raise HostNetworkError(f"Distributed portgroup '{name}' not found")


def _vmk_services(host: vim.HostSystem) -> dict[str, list[str]] | None:
    """Map vmk device -> host services it is selected for (management, vmotion, ...).

    Returns ``None`` when the service map CANNOT be read (virtualNicManager or
    its info unavailable - disconnected host, restricted role, API hiccup).
    Callers MUST treat None as "unverifiable", never as "no services": an empty
    dict here once let remove_host_vmk delete interfaces it claimed to protect.
    """
    vnic_mgr = host.configManager.virtualNicManager
    if vnic_mgr is None or vnic_mgr.info is None:
        return None
    services: dict[str, list[str]] = {}
    for net_cfg in vnic_mgr.info.netConfig or []:
        for selected in net_cfg.selectedVnic or []:
            for cand in net_cfg.candidateVnic or []:
                if cand.key == selected:
                    services.setdefault(cand.device, []).append(net_cfg.nicType)
    return services


_DEFAULT_NETSTACK = "defaultTcpipStack"


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
            getattr(r, "prefixLength", None) == 0
            and getattr(r, "deviceName", None) == vmk
            for r in routes
        )
    except Exception:
        return None


def list_host_vmks(
    si: ServiceInstance,
    host_name: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """List VMkernel adapters, optionally scoped to one host.

    ``services`` is ``None`` (not ``[]``) for vmks on a host whose service
    map could not be read - unknown is reported as unknown.

    The all-hosts path batches name + vnic list through PropertyCollector
    (the inventory ``_collect`` path) so a large estate is one server-side
    call, not a container-view walk with a per-host ``.config`` round-trip.
    The service map still reads per host - it lives on a separate managed
    object (virtualNicManager) the collector traversal doesn't cover.
    """
    if host_name:
        host = _require_host(si, host_name)
        host_rows = [(host, sanitize(host.name, 200), host.config.network.vnic or [])]
    else:
        host_rows = [
            (obj, sanitize(p.get("name", ""), 200), p.get("config.network.vnic") or [])
            for obj, p in _collect(si, [vim.HostSystem], ["name", "config.network.vnic"])
        ]
    out = []
    for host_obj, host_display, vnics in host_rows:
        services = _vmk_services(host_obj)
        for vnic in vnics:
            ip = vnic.spec.ip
            dv_port = getattr(vnic.spec, "distributedVirtualPort", None)
            netstack = getattr(vnic.spec, "netStackInstanceKey", None)
            out.append({
                "host": host_display,
                "device": sanitize(vnic.device, 40),
                "ip": ip.ipAddress if ip else None,
                "netmask": ip.subnetMask if ip else None,
                "dhcp": bool(ip.dhcp) if ip else None,
                "mtu": vnic.spec.mtu,
                "mac": vnic.spec.mac,
                "portgroup": sanitize(vnic.portgroup, 200) or None,
                "dvs_port": dv_port.portgroupKey if dv_port else None,
                "netstack": sanitize(netstack, 80) if netstack else None,
                "services": (
                    services.get(vnic.device, []) if services is not None else None
                ),
            })
    total = len(out)
    window = out[offset : offset + limit] if limit > 0 else out[offset:]
    result = {"total": total, "returned": len(window), "vmks": window}
    if offset or len(window) < total:
        result["offset"] = offset
        result["hint"] = "Use limit/offset to page through the remainder."
    return result


def add_host_vmk(
    si: ServiceInstance,
    host_name: str,
    portgroup: str,
    ip: str,
    netmask: str,
    mtu: int = 1500,
    confirm: bool = False,
) -> dict:
    """Add a static-IP VMkernel adapter on a DVS portgroup, preview/confirm gated.

    No gateway is set and no services are enabled - throwaway-test-vmk shape.
    confirm=False validates and returns the exact spec; confirm=True creates
    and returns the assigned device name (e.g. "vmk2").
    """
    try:
        ipaddress.IPv4Address(ip)
    except ValueError as e:
        raise HostNetworkError(f"invalid IPv4 address {ip!r}: {e}") from e
    try:
        # Netmask must be a valid mask, not just any dotted quad.
        ipaddress.IPv4Network(f"0.0.0.0/{netmask}")
    except ValueError as e:
        raise HostNetworkError(f"invalid netmask {netmask!r}: {e}") from e
    if not (_MTU_MIN <= mtu <= _MTU_MAX):
        raise HostNetworkError(f"mtu must be {_MTU_MIN}-{_MTU_MAX}, got {mtu}")

    host = _require_host(si, host_name)
    pg = _find_dv_portgroup(si, portgroup)

    for vnic in host.config.network.vnic or []:
        if vnic.spec.ip and vnic.spec.ip.ipAddress == ip:
            raise HostNetworkError(
                f"Host '{host_name}' already has {vnic.device} at {ip}"
            )

    planned = {
        "host": host.name,
        "portgroup": pg.name,
        "ip": ip,
        "netmask": netmask,
        "mtu": mtu,
        "gateway": None,
        "services": [],
    }
    if not confirm:
        return {
            "action": "preview",
            "would_create": planned,
            "hint": "Re-run with confirm=True to create.",
        }

    spec = vim.host.VirtualNic.Specification()
    spec.ip = vim.host.IpConfig(dhcp=False, ipAddress=ip, subnetMask=netmask)
    spec.mtu = mtu
    spec.distributedVirtualPort = vim.dvs.PortConnection(
        portgroupKey=pg.key,
        switchUuid=pg.config.distributedVirtualSwitch.uuid,
    )
    # portgroup="" is the API contract for DVS-connected vmks.
    device = host.configManager.networkSystem.AddVirtualNic(portgroup="", nic=spec)
    return {"action": "created", "device": device, "created": planned}


def remove_host_vmk(
    si: ServiceInstance,
    host_name: str,
    vmk: str,
    confirm: bool = False,
    force_unprotected: bool = False,
) -> dict:
    """Remove a VMkernel adapter - confirm-gated, guarded, FAIL CLOSED.

    Protections (each refuses unless ``force_unprotected=True``):
    - vmk is selected for any host service (management/vMotion/vSAN/...)
    - the host's service map cannot be read (unverifiable != safe)
    - vmk lives on a non-default netstack (vxlan/NSX TEP, dedicated
      vmotion/provisioning stacks) - those never appear in the service map
    - vmk is the device of a default (prefix-0) route, or the routing
      table cannot be read

    ABSOLUTE (no override): the host's only management-enabled vmk. The
    call rides the interface it would delete; after it succeeded, nothing
    could reach the host to undo it.
    """
    host = _require_host(si, host_name)

    existing = {v.device: v for v in host.config.network.vnic or []}
    if vmk not in existing:
        raise HostNetworkError(
            f"'{vmk}' not found on '{host_name}'. "
            f"Present: {sanitize(str(sorted(existing)), 300) or 'none'}"
        )
    vnic = existing[vmk]

    protections: list[str] = []

    services = _vmk_services(host)
    if services is None:
        protections.append(
            "the host's service map cannot be read (virtualNicManager "
            "unavailable) - this vmk may carry management/vMotion/other "
            "critical services and that cannot be ruled out right now"
        )
    else:
        in_use = services.get(vmk, [])
        if "management" in in_use:
            mgmt_vmks = [d for d, svcs in services.items() if "management" in svcs]
            if len(mgmt_vmks) <= 1:
                raise HostNetworkError(
                    f"REFUSED (no override): {vmk} is the ONLY "
                    f"management-enabled vmk on '{host_name}'. Removing it "
                    "severs the API path this call rides on; after it "
                    "succeeded, nothing could reach the host to undo it."
                )
        if in_use:
            protections.append(f"selected for host services {in_use}")

    netstack = getattr(vnic.spec, "netStackInstanceKey", None)
    if netstack and netstack != _DEFAULT_NETSTACK:
        protections.append(
            f"lives on netstack '{sanitize(netstack, 80)}' - non-default "
            "netstacks (NSX TEPs on vxlan, dedicated vmotion/provisioning "
            "stacks) are system-owned and never appear in the service map"
        )

    carries_gw = _vmk_carries_default_route(host, vmk)
    if carries_gw is None:
        protections.append(
            "the host routing table cannot be read - this vmk may carry "
            "a default gateway route and that cannot be ruled out right now"
        )
    elif carries_gw:
        protections.append("carries a default (prefix-0) gateway route")

    if protections and not force_unprotected:
        raise HostNetworkError(
            f"REFUSED: removing {vmk} on '{host_name}' is blocked: "
            + "; ".join(protections)
            + ". If you are certain this interface is safe to remove, re-run "
            "with force_unprotected=True AND confirm=True - the override and "
            "every bypassed protection are recorded in the result."
        )

    target_ip = vnic.spec.ip
    doomed = {
        "host": sanitize(host.name, 200),
        "device": vmk,
        "ip": target_ip.ipAddress if target_ip else None,
    }
    if not confirm:
        preview: dict = {
            "action": "preview",
            "would_remove": doomed,
            "hint": "Re-run with confirm=True to remove.",
        }
        if protections:
            preview["protections_bypassed_by_force"] = protections
        return preview

    host.configManager.networkSystem.RemoveVirtualNic(vmk)
    result: dict = {"action": "removed", "removed": doomed}
    if protections:
        result["forced"] = True
        result["protections_bypassed"] = protections
    return result


# vim.host.VirtualNicManager.NicType enum values (pyVmomi 8/9). Kept as a
# static allowlist so an unknown/mistyped service fails loudly at validation
# instead of surfacing as an opaque vCenter fault at call time.
_NIC_TYPES = frozenset({
    "management", "vmotion", "vsan", "vSphereProvisioning",
    "faultToleranceLogging", "vSphereReplication", "vSphereReplicationNFC",
    "vSphereBackupNFC", "ptp", "nvmeRdma", "nvmeTcp", "vnetworking",
    "vsanExternal", "vsanReplication", "vsanWitness",
})


def set_vmk_service(
    si: ServiceInstance,
    host_name: str,
    vmk: str,
    service: str,
    enabled: bool,
    confirm: bool = False,
) -> dict:
    """Enable/disable a host service (nicType) on an existing vmk - confirm-gated.

    FAIL CLOSED: when the host's service map cannot be read, BOTH directions
    refuse - idempotence checks and the management guard below each need the
    verified current state, and unverifiable is never treated as safe.

    ABSOLUTE (no override): disabling ``management`` on the host's only
    management-enabled vmk. The call rides the interface it would untag;
    after it succeeded, nothing could reach the host to undo it.
    """
    host = _require_host(si, host_name)

    if service not in _NIC_TYPES:
        raise HostNetworkError(
            f"Unknown service '{sanitize(service, 60)}'. "
            f"Valid services: {', '.join(sorted(_NIC_TYPES))}"
        )

    existing = {v.device for v in host.config.network.vnic or []}
    if vmk not in existing:
        raise HostNetworkError(
            f"'{vmk}' not found on '{host_name}'. "
            f"Present: {sanitize(str(sorted(existing)), 300) or 'none'}"
        )

    services = _vmk_services(host)
    if services is None:
        raise HostNetworkError(
            "REFUSED: the host's service map cannot be read (virtualNicManager "
            "unavailable), so the current selections cannot be verified and "
            "this change cannot be made safely. Retry when the host is "
            "reachable/healthy."
        )
    current = sorted(services.get(vmk, []))

    if not enabled and service == "management" and "management" in current:
        mgmt_vmks = [d for d, svcs in services.items() if "management" in svcs]
        if len(mgmt_vmks) <= 1:
            raise HostNetworkError(
                f"REFUSED (no override): {vmk} is the ONLY management-enabled "
                f"vmk on '{host_name}'. Untagging management severs the API "
                "path this call rides on; after it succeeded, nothing could "
                "reach the host to undo it."
            )

    change = {
        "host": sanitize(host.name, 200),
        "device": vmk,
        "service": service,
        "enabled": enabled,
        "current_services": current,
    }

    if (service in current) == enabled:
        return {
            "action": "noop",
            "unchanged": change,
            "hint": f"{vmk} already has '{service}' "
                    f"{'enabled' if enabled else 'disabled'}.",
        }

    if not confirm:
        return {
            "action": "preview",
            "would_set": change,
            "hint": "Re-run with confirm=True to apply.",
        }

    mgr = host.configManager.virtualNicManager
    if enabled:
        mgr.SelectVnicForNicType(service, vmk)
    else:
        mgr.DeselectVnicForNicType(service, vmk)

    after = _vmk_services(host)
    return {
        "action": "set",
        "set": change,
        "services_now": sorted(after.get(vmk, [])) if after is not None else None,
    }


# --- vmk_ping (esxcli over API, raw SOAP) ---------------------------------------
#
# The ManagedMethodExecuter types are internal VMODL that modern pyVmomi does
# not ship (this is how PowerCLI's Get-EsxCli works under the hood). Rather
# than fragile dynamic type registration, we speak the two SOAP calls
# directly against the SAME authenticated vCenter session pyVmomi already
# holds (session cookie reuse) - no extra auth, no host SSH:
#   1. RetrieveManagedMethodExecuter on the HostSystem -> per-host MME moid
#   2. ExecuteSoap on that MME -> esxcli network diag ping

_ESXCLI_PING_MOID = "ha-cli-handler-network-diag"
_ESXCLI_PING_METHOD = "vim.EsxCLI.network.diag.ping"
_ESXCLI_VERSION = "urn:vim25/5.0"

_SOAP_ENVELOPE = (
    '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
    "<soapenv:Body>{body}</soapenv:Body></soapenv:Envelope>"
)


def _soap_post(si: ServiceInstance, body: str) -> str:
    """POST one SOAP body to the connected vCenter's /sdk, reusing the live
    pyVmomi session cookie. Returns the raw response XML; raises
    HostNetworkError on transport errors or SOAP faults."""
    import ssl

    import httpx

    stub = si._stub
    hostport = stub.host if ":" in stub.host else f"{stub.host}:443"
    # Match the pyVmomi session's TLS posture via the module-level verify_ssl
    # registry, NOT by probing si._stub for an sslContext attribute -
    # SoapStubAdapter keeps its context in schemeArgs, so that getattr is
    # always None and the verify=False branch would never execute (found in
    # upstream review of PR #34; targets with verify_ssl: false failed with
    # CERTIFICATE_VERIFY_FAILED).
    if not get_verify_ssl(si):
        verify = False
    else:
        # Verify against the SYSTEM CA store (where hub-installed roots like
        # the v9 VMCA live). httpx's default is the bundled certifi store,
        # which does NOT see system-added roots - pyVmomi verifies via the
        # system store, and this SOAP call must trust exactly what it trusts.
        verify = ssl.create_default_context()
    try:
        r = httpx.post(
            f"https://{hostport}/sdk",
            content=_SOAP_ENVELOPE.format(body=body),
            headers={
                "Content-Type": "text/xml; charset=utf-8",
                "SOAPAction": "urn:vim25/5.0",
                "Cookie": stub.cookie or "",
            },
            verify=verify,
            timeout=90.0,
        )
    except Exception as e:
        raise HostNetworkError(f"SOAP transport failed: {e}") from e
    fault = re.search(r"<faultstring>\s*(.*?)\s*</faultstring>", r.text, re.DOTALL)
    if fault:
        raise HostNetworkError(f"SOAP fault: {sanitize(fault.group(1), 300)}")
    if r.status_code != 200:
        raise HostNetworkError(f"SOAP HTTP {r.status_code}: {sanitize(r.text, 200)}")
    return r.text


def _retrieve_mme_moid(si: ServiceInstance, host: vim.HostSystem) -> str:
    body = (
        '<RetrieveManagedMethodExecuter xmlns="urn:vim25">'
        f'<_this type="HostSystem">{host._moId}</_this>'
        "</RetrieveManagedMethodExecuter>"
    )
    xml = _soap_post(si, body)
    m = re.search(r"<returnval[^>]*>\s*([^<\s]+)\s*</returnval>", xml)
    if not m:
        raise HostNetworkError(
            f"could not locate ManagedMethodExecuter moid in response: {sanitize(xml, 200)}"
        )
    return m.group(1)

# PingOutput field tags of interest. ESXi's schema genuinely misspells
# "Recieved" and uses "PacketLost"; accept correct spellings too.
_PING_FIELDS = {
    "transmitted": ("Transmitted",),
    "received": ("Recieved", "Received"),
    "packet_lost_pct": ("PacketLost",),
    "roundtrip_avg_ms": ("RoundtripAvgMS", "RoundtripAvg"),
    "host_addr": ("HostAddr",),
}


def _xml_tag(name: str, value) -> str:
    return f"<{name}>{value}</{name}>"


def _parse_ping_xml(xml: str) -> dict:
    """Pull the summary fields out of the esxcli PingOutput XML, defensively."""
    out: dict = {}
    for field, tags in _PING_FIELDS.items():
        for tag in tags:
            m = re.search(rf"<{tag}>\s*([^<]*?)\s*</{tag}>", xml)
            if m:
                val = m.group(1)
                try:
                    out[field] = float(val) if "." in val else int(val)
                except ValueError:
                    out[field] = val
                break
    return out


def vmk_ping(
    si: ServiceInstance,
    host_name: str,
    source_vmk: str,
    dest_ip: str,
    size: int = 56,
    df: bool = False,
    count: int = 3,
    netstack: str | None = None,
) -> dict:
    """DF-bit-capable ping sourced from a host vmk - the MTU path validator.

    Runs `esxcli network diag ping` on the host through the vSphere API
    (ManagedMethodExecuter), no SSH. df=True + size probes path MTU:
    e.g. size=1572 proves a >=1600 overlay floor, size=8972 proves full
    jumbo (9000 minus 28 bytes ICMP/IP overhead). A too-big DF'd packet
    fails with "sendto() failed (Message too long)" - that failure IS the
    diagnostic signal, reported per-packet, not raised.
    """
    try:
        ipaddress.IPv4Address(dest_ip)
    except ValueError as e:
        raise HostNetworkError(f"invalid IPv4 address {dest_ip!r}: {e}") from e
    if not (0 < size <= 65507):
        raise HostNetworkError(f"size must be 1-65507, got {size}")
    if not (1 <= count <= 60):
        raise HostNetworkError(f"count must be 1-60, got {count}")

    host = _require_host(si, host_name)
    devices = {v.device for v in host.config.network.vnic or []}
    if source_vmk not in devices:
        present = sanitize(str(sorted(devices)), 300)
        raise HostNetworkError(
            f"'{source_vmk}' not found on '{host_name}'. Present: {present}"
        )

    from xml.sax.saxutils import escape

    args = [
        ("count", count),
        ("host", dest_ip),
        ("interface", source_vmk),
        ("size", size),
    ]
    if df:
        args.append(("df", "true"))
    if netstack:
        args.append(("netstack", netstack))
    # The CLI handler validates the argument array against the method's
    # declared parameter order, which follows the esxcli metadata's
    # alphabetical convention - an out-of-order array faults with
    # "A specified parameter was not correct: argument[N]" (govmomi solves
    # this by fetching the param metadata; sorted names match it for ping).
    args.sort(key=lambda kv: kv[0])

    mme_moid = _retrieve_mme_moid(si, host)
    argument_xml = "".join(
        f"<argument><name>{n}</name><val>{escape(_xml_tag(n, v))}</val></argument>"
        for n, v in args
    )
    body = (
        '<ExecuteSoap xmlns="urn:vim25">'
        f'<_this type="ReflectManagedMethodExecuter">{mme_moid}</_this>'
        f"<moid>{_ESXCLI_PING_MOID}</moid>"
        f"<version>{_ESXCLI_VERSION}</version>"
        f"<method>{_ESXCLI_PING_METHOD}</method>"
        f"{argument_xml}"
        "</ExecuteSoap>"
    )
    xml = _soap_post(si, body)

    request = {
        "host": host.name,
        "source_vmk": source_vmk,
        "dest_ip": dest_ip,
        "size": size,
        "df": df,
        "count": count,
        "netstack": netstack,
    }
    # esxcli-level failure comes back inside the result as <fault><faultMsg>
    # (e.g. every DF'd packet oversized, bad netstack). For MTU probing this
    # IS the answer - return it structured, not as an error. vCenter
    # serializes the reflect types WITH a namespace prefix
    # (<reflect:response>, live-verified 2026-07-16) - match both.
    fault_msgs = re.findall(
        r"<(?:\w+:)?faultMsg>\s*(.*?)\s*</(?:\w+:)?faultMsg>", xml, re.DOTALL
    )
    if fault_msgs:
        return {
            "request": request,
            "success": False,
            "fault": sanitize("; ".join(fault_msgs), 500),
            "hint": "For df=True, 'Message too long' means the path MTU is below size+28.",
        }
    # The PingOutput arrives XML-escaped inside <response> (possibly
    # namespace-prefixed); unescape once.
    resp = re.search(
        r"<(?:\w+:)?response[^>]*>(.*)</(?:\w+:)?response>", xml, re.DOTALL
    )
    from xml.sax.saxutils import unescape

    summary = _parse_ping_xml(unescape(resp.group(1)) if resp else xml)
    received = summary.get("received", 0)
    out = {
        "request": request,
        "success": isinstance(received, int) and received > 0,
        "summary": summary,
    }
    if not summary:
        # Unrecognized response shape - never swallow it silently; the raw
        # excerpt is the only way to adapt the parser to a new ACOS/ESXi
        # schema without SSHing anywhere.
        out["raw_response"] = sanitize(xml[-1200:], 1200)
    return out
