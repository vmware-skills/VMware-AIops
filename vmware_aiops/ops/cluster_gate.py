"""The blast-radius gate in front of cluster deletion and host removal (HLD §7).

``cluster_mgmt.delete_cluster`` and ``remove_host_from_cluster`` stay the
executors the CLI and plans call. The MCP tools measure first with the functions
here, refuse on a blocker or an unreadable field (``gate.refuse_on``), and only
then call the executor.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pyVmomi import vim, vmodl
from vmware_policy import sanitize

from vmware_aiops.ops import cluster_mgmt

if TYPE_CHECKING:
    from pyVmomi.vim import ServiceInstance

#: Names listed individually in a blast radius; the counts cover the rest.
MAX_LISTED = 16

_READ_ERRORS = (vmodl.MethodFault, AttributeError, TypeError)
_UNREAD = object()


def _read(fn):
    try:
        return fn()
    except _READ_ERRORS:
        return _UNREAD


def _names(objs) -> list[str]:
    return [sanitize(o.name, 200) for o in objs][:MAX_LISTED]


def _vms_on(hosts) -> list | object:
    """Every VM registered on ``hosts``; ``_UNREAD`` if any host's list is unreadable."""
    vms: list = []
    for host in hosts:
        on_host = _read(lambda h=host: list(h.vm or []))
        if on_host is _UNREAD:
            return _UNREAD
        vms.extend(on_host)
    return vms


def measure_cluster_delete(si: ServiceInstance, cluster_name: str) -> dict[str, Any]:
    """What deleting the cluster would remove, and what stands in the way."""
    cluster = cluster_mgmt._require_cluster(si, cluster_name)
    hosts = _read(lambda: list(cluster.host or []))
    vms = _vms_on(hosts) if hosts is not _UNREAD else _UNREAD
    datastores = _read(lambda: list(cluster.datastore or []))

    unmeasured = [
        field for field, value in (("hosts", hosts), ("vms", vms), ("datastores", datastores))
        if value is _UNREAD
    ]
    blockers = []
    if hosts is not _UNREAD and hosts:
        blockers.append(
            f"The cluster still has {len(hosts)} host(s): {', '.join(_names(hosts))}. "
            "Remove each one first with cluster_remove_host (it must be in maintenance "
            "mode), then preview the deletion again."
        )
    if vms is not _UNREAD and vms:
        blockers.append(
            f"{len(vms)} VM(s) still run in the cluster. Migrate them off (vm_migrate) "
            "before removing the hosts that carry them."
        )
    cluster_id = _read(lambda: cluster._moId)
    return {
        "cluster": sanitize(cluster.name, 200),
        "cluster_id": cluster_id if cluster_id is not _UNREAD else None,
        "host_count": len(hosts) if hosts is not _UNREAD else None,
        "hosts": _names(hosts) if hosts is not _UNREAD else [],
        "vm_count": len(vms) if vms is not _UNREAD else None,
        "vms": _names(vms) if vms is not _UNREAD else [],
        "datastore_count": len(datastores) if datastores is not _UNREAD else None,
        "datastores": _names(datastores) if datastores is not _UNREAD else [],
        "note": "Datastores are not deleted; they stay mounted on their hosts.",
        "unmeasured": unmeasured,
        "blockers": blockers,
    }


def measure_host_removal(
    si: ServiceInstance, cluster_name: str, host_name: str
) -> dict[str, Any]:
    """What removing the host from the cluster would take out, and what stands in the way.

    Raises ``ClusterError`` exactly where ``remove_host_from_cluster`` does for
    an unknown or non-member host: there is nothing to measure.
    """
    cluster, host = cluster_mgmt.require_member_host(si, cluster_name, host_name)
    maintenance = _read(lambda: host.runtime.inMaintenanceMode)
    vms = _vms_on([host])
    powers = (
        [_read(lambda v=vm: v.runtime.powerState) for vm in vms]
        if vms is not _UNREAD else _UNREAD
    )
    powers_read = powers is not _UNREAD and _UNREAD not in powers
    powered_on = (
        sum(1 for p in powers if p == vim.VirtualMachine.PowerState.poweredOn)
        if powers_read else None
    )

    unmeasured = [
        field for field, ok in (
            ("maintenance_mode", maintenance is not _UNREAD and maintenance is not None),
            ("vms", vms is not _UNREAD),
            ("vm_power_states", vms is _UNREAD or powers_read),
        ) if not ok
    ]
    blockers = []
    if maintenance is False:
        blockers.append(
            f"Host '{sanitize(host_name, 200)}' must be in maintenance mode before removal. "
            "Enter it from the vSphere Client (Host > Maintenance Mode), confirm with "
            "cluster_info, then preview the removal again."
        )
    if powered_on:
        blockers.append(
            f"{powered_on} VM(s) on the host are powered on. Migrate them (vm_migrate) or "
            "power them off before removing the host from the cluster."
        )
    host_id = _read(lambda: host._moId)
    return {
        "cluster": sanitize(cluster.name, 200),
        "host": sanitize(host.name, 200),
        "host_id": host_id if host_id is not _UNREAD else None,
        "maintenance_mode": maintenance if maintenance is not _UNREAD else None,
        "vm_count": len(vms) if vms is not _UNREAD else None,
        "powered_on_vm_count": powered_on,
        "vms": _names(vms) if vms is not _UNREAD else [],
        "note": "The host is not deleted: it stays in vCenter as a standalone host "
                "(cluster_add_host moves it back). It leaves the cluster's HA/DRS.",
        "unmeasured": unmeasured,
        "blockers": blockers,
    }
