"""Inventory queries for vCenter/ESXi resources."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pyVmomi import vim, vmodl
from vmware_policy import sanitize

if TYPE_CHECKING:
    from pyVmomi.vim import ServiceInstance


class InventoryError(Exception):
    """Raised when a required inventory object cannot be resolved."""


class AmbiguousVMError(InventoryError):
    """Raised when a VM name matches more than one VM.

    vSphere allows duplicate VM names across folders. Every caller of
    ``find_vm_by_name`` in this skill goes on to change the VM it gets back,
    and a write that lands on the wrong one of two same-named VMs looks
    exactly like a write that landed on the right one.
    """


# Server-side page size for PropertyCollector. Large inventories are streamed in
# batches of this many objects; the helper transparently follows continuation
# tokens, so the caller always gets the full result set.
_PC_PAGE_SIZE = 1000


def _collect(
    si: ServiceInstance, obj_type: list, paths: list[str]
) -> list[tuple[object, dict]]:
    """Batch-retrieve ``paths`` for every ``obj_type`` object in one operation.

    Uses ``PropertyCollector.RetrievePropertiesEx`` so all requested properties
    for all matching objects are fetched in a single server-side call (paged via
    continuation tokens), instead of one lazy SOAP round-trip per property per
    object. This is the difference between seconds and minutes on inventories
    with thousands of VMs/hosts (GitHub issue #31).

    Args:
        si: vSphere ServiceInstance.
        obj_type: Single-element list with the managed-object type to collect,
            e.g. ``[vim.VirtualMachine]``.
        paths: Property paths to fetch, e.g. ``["name", "runtime.powerState"]``.
            Array properties (e.g. ``vm``) come back as lists; unset properties
            are simply absent from the returned dict.

    Returns:
        List of ``(managed_object, {path: value})`` tuples in server order.
    """
    content = si.RetrieveContent()
    view = content.viewManager.CreateContainerView(
        content.rootFolder, obj_type, True
    )
    try:
        traversal = vmodl.query.PropertyCollector.TraversalSpec(
            name="traverseView", type=vim.view.ContainerView, path="view", skip=False
        )
        obj_spec = vmodl.query.PropertyCollector.ObjectSpec(
            obj=view, skip=True, selectSet=[traversal]
        )
        prop_spec = vmodl.query.PropertyCollector.PropertySpec(
            type=obj_type[0], pathSet=list(paths), all=False
        )
        filter_spec = vmodl.query.PropertyCollector.FilterSpec(
            objectSet=[obj_spec], propSet=[prop_spec]
        )
        options = vmodl.query.PropertyCollector.RetrieveOptions(
            maxObjects=_PC_PAGE_SIZE
        )
        pc = content.propertyCollector
        results: list[tuple[object, dict]] = []
        batch = pc.RetrievePropertiesEx([filter_spec], options)
        while batch is not None:
            for obj_content in batch.objects:
                props = {p.name: p.val for p in (obj_content.propSet or [])}
                results.append((obj_content.obj, props))
            token = getattr(batch, "token", None)
            if not token:
                break
            batch = pc.ContinueRetrievePropertiesEx(token)
        return results
    finally:
        view.Destroy()


def _collect_object(
    si: ServiceInstance, obj: object, obj_type: type, paths: list[str]
) -> dict:
    """Batch-retrieve ``paths`` for a single already-known managed object.

    Unlike :func:`_collect` (which walks a container view), this targets one
    specific object via a direct ``ObjectSpec``. Used when the object is already
    in hand — e.g. the inventory root folder, whose ``triggeredAlarmState``
    aggregates every alarm in the inventory — so its properties are fetched in a
    single ``RetrievePropertiesEx`` call instead of a lazy per-property
    round-trip.

    Args:
        si: vSphere ServiceInstance.
        obj: The managed object to inspect.
        obj_type: The managed-object type of ``obj`` (e.g. ``vim.Folder``).
        paths: Property paths to fetch.

    Returns:
        ``{path: value}`` for the object (empty dict if nothing came back).
    """
    content = si.RetrieveContent()
    obj_spec = vmodl.query.PropertyCollector.ObjectSpec(obj=obj, skip=False)
    prop_spec = vmodl.query.PropertyCollector.PropertySpec(
        type=obj_type, pathSet=list(paths), all=False
    )
    filter_spec = vmodl.query.PropertyCollector.FilterSpec(
        objectSet=[obj_spec], propSet=[prop_spec]
    )
    options = vmodl.query.PropertyCollector.RetrieveOptions()
    batch = content.propertyCollector.RetrievePropertiesEx([filter_spec], options)
    if batch is None or not batch.objects:
        return {}
    return {p.name: p.val for p in (batch.objects[0].propSet or [])}


_VM_SORT_KEYS = {"name", "cpu", "memory_mb", "power_state"}
_COMPACT_FIELDS = ("name", "power_state", "cpu", "memory_mb")
_VM_PROPS = [
    "name",
    "runtime.powerState",
    "runtime.host",
    "config.hardware.numCPU",
    "config.hardware.memoryMB",
    "config.guestFullName",
    "config.uuid",
    "guest.ipAddress",
    "guest.toolsRunningStatus",
]


def list_vms(
    si: ServiceInstance,
    limit: int | None = None,
    sort_by: str = "name",
    power_state: str | None = None,
    fields: list[str] | None = None,
    compact_threshold: int = 50,
) -> dict:
    """List virtual machines with optional filtering, sorting, and field selection.

    Returns a dict with keys:
        total   - total VMs after filtering
        mode    - "full" or "compact" (auto-selected when total > compact_threshold)
        vms     - list of VM dicts
        hint    - optional suggestion when compact mode is auto-selected

    Auto-compact: when no explicit limit/fields are set and total VMs exceed
    compact_threshold (default 50), only compact fields are returned to keep
    context manageable. Use limit or fields to override.

    Args:
        si: vSphere ServiceInstance.
        limit: Max number of VMs to return (None = all).
        sort_by: Sort field: "name" | "cpu" | "memory_mb" | "power_state".
        power_state: Filter by power state: "poweredOn" | "poweredOff" | "suspended".
        fields: Return only these fields (None = auto).
            Available: name, power_state, cpu, memory_mb, guest_os, ip_address,
                       host, uuid, tools_status.
        compact_threshold: Auto-compact when VM count exceeds this (default 50).
    """
    # Resolve host moRef -> name in one batched call so per-VM host lookups
    # don't each trigger a round-trip.
    host_names = {obj: p.get("name") for obj, p in _collect(si, [vim.HostSystem], ["name"])}

    results = []
    for _obj, p in _collect(si, [vim.VirtualMachine], _VM_PROPS):
        host_ref = p.get("runtime.host")
        guest_os = p.get("config.guestFullName")
        tools = p.get("guest.toolsRunningStatus")
        entry = {
            "name": sanitize(p.get("name", "")),
            "power_state": str(p.get("runtime.powerState", "N/A")),
            "cpu": p.get("config.hardware.numCPU") or 0,
            "memory_mb": p.get("config.hardware.memoryMB") or 0,
            "guest_os": sanitize(guest_os) if guest_os else "N/A",
            "ip_address": p.get("guest.ipAddress"),
            "host": sanitize(host_names.get(host_ref) or "N/A") if host_ref else "N/A",
            "uuid": p.get("config.uuid") or "N/A",
            "tools_status": str(tools) if tools else "N/A",
        }
        results.append(entry)

    # Filter by power state
    if power_state:
        results = [r for r in results if power_state.lower() in r["power_state"].lower()]

    # Sort
    sort_key = sort_by if sort_by in _VM_SORT_KEYS else "name"
    results = sorted(results, key=lambda x: x[sort_key])

    total = len(results)

    # Limit
    if limit is not None and limit > 0:
        results = results[:limit]

    # Determine mode and field selection
    explicit_fields = bool(fields)
    explicit_limit = limit is not None and limit > 0

    if not explicit_fields and not explicit_limit and total > compact_threshold:
        # Auto-compact: large inventory, no explicit constraints
        mode = "compact"
        results = [{k: r[k] for k in _COMPACT_FIELDS if k in r} for r in results]
        hint = (
            f"Large inventory ({total} VMs): showing compact fields only. "
            "Use --limit N or --fields to get full details."
        )
    else:
        mode = "full"
        hint = None
        if fields:
            valid = {"name", "power_state", "cpu", "memory_mb", "guest_os",
                     "ip_address", "host", "uuid", "tools_status"}
            keep = [f for f in fields if f in valid]
            if keep:
                results = [{k: r[k] for k in keep if k in r} for r in results]

    return {"total": total, "mode": mode, "vms": results, "hint": hint}


_HOST_PROPS = [
    "name",
    "runtime.connectionState",
    "runtime.powerState",
    "hardware.cpuInfo.numCpuCores",
    "hardware.cpuInfo.numCpuThreads",
    "hardware.memorySize",
    "config.product.version",
    "config.product.build",
    "vm",
    "summary.quickStats.uptime",
]
_DS_PROPS = [
    "name",
    "summary.type",
    "summary.freeSpace",
    "summary.capacity",
    "summary.accessible",
    "summary.url",
    "vm",
]
_CLUSTER_PROPS = [
    "name",
    "host",
    "configuration.drsConfig.enabled",
    "configuration.drsConfig.defaultVmBehavior",
    "configuration.dasConfig.enabled",
    "summary.totalCpu",
    "summary.totalMemory",
]
_NET_PROPS = ["name", "vm", "summary.accessible"]


def list_hosts(si: ServiceInstance) -> list[dict]:
    """List all ESXi hosts with basic info."""
    results = []
    for _obj, p in _collect(si, [vim.HostSystem], _HOST_PROPS):
        mem = p.get("hardware.memorySize")
        results.append({
            "name": p.get("name", ""),
            "connection_state": str(p.get("runtime.connectionState", "N/A")),
            "power_state": str(p.get("runtime.powerState", "N/A")),
            "cpu_cores": p.get("hardware.cpuInfo.numCpuCores") or 0,
            "cpu_threads": p.get("hardware.cpuInfo.numCpuThreads") or 0,
            "memory_gb": round(mem / (1024**3)) if mem else 0,
            "esxi_version": p.get("config.product.version") or "N/A",
            "esxi_build": p.get("config.product.build") or "N/A",
            "vm_count": len(p.get("vm") or []),
            "uptime_seconds": p.get("summary.quickStats.uptime") or 0,
        })
    return sorted(results, key=lambda x: x["name"])


def list_datastores(si: ServiceInstance) -> list[dict]:
    """List all datastores with capacity info."""
    results = []
    for _obj, p in _collect(si, [vim.Datastore], _DS_PROPS):
        free = p.get("summary.freeSpace")
        cap = p.get("summary.capacity")
        results.append({
            "name": p.get("name", ""),
            "type": p.get("summary.type"),
            "free_gb": round(free / (1024**3), 1) if free else 0,
            "total_gb": round(cap / (1024**3), 1) if cap else 0,
            "accessible": p.get("summary.accessible"),
            "url": p.get("summary.url"),
            "vm_count": len(p.get("vm") or []),
        })
    return sorted(results, key=lambda x: x["name"])


def list_clusters(si: ServiceInstance) -> list[dict]:
    """List all clusters with configuration info."""
    results = []
    for _obj, p in _collect(si, [vim.ClusterComputeResource], _CLUSTER_PROPS):
        total_mem = p.get("summary.totalMemory")
        drs_behavior = p.get("configuration.drsConfig.defaultVmBehavior")
        results.append({
            "name": p.get("name", ""),
            "host_count": len(p.get("host") or []),
            "drs_enabled": bool(p.get("configuration.drsConfig.enabled")),
            "drs_behavior": str(drs_behavior) if drs_behavior else "N/A",
            "ha_enabled": bool(p.get("configuration.dasConfig.enabled")),
            "total_cpu_mhz": p.get("summary.totalCpu") or 0,
            "total_memory_gb": round(total_mem / (1024**3)) if total_mem else 0,
        })
    return sorted(results, key=lambda x: x["name"])


def list_networks(si: ServiceInstance) -> list[dict]:
    """List all networks."""
    results = []
    for _obj, p in _collect(si, [vim.Network], _NET_PROPS):
        accessible = p.get("summary.accessible")
        results.append({
            "name": p.get("name", ""),
            "vm_count": len(p.get("vm") or []),
            "accessible": accessible if accessible is not None else True,
        })
    return sorted(results, key=lambda x: x["name"])


def _find_by_name(si: ServiceInstance, obj_type: list, name: str):
    """Return the first managed object of ``obj_type`` whose name matches.

    Fetches every object's ``name`` in one batched call rather than touching
    ``obj.name`` per object (each of which would be a round-trip).
    """
    for obj, p in _collect(si, obj_type, ["name"]):
        if p.get("name") == name:
            return obj
    return None


def find_vm_by_name(si: ServiceInstance, vm_name: str) -> vim.VirtualMachine | None:
    """Find a VM by exact name. Returns None if not found.

    Raises AmbiguousVMError when the name matches more than one VM, rather
    than returning whichever the property collector listed first.
    """
    matches = [
        obj for obj, p in _collect(si, [vim.VirtualMachine], ["name"])
        if p.get("name") == vm_name
    ]
    if len(matches) > 1:
        raise AmbiguousVMError(
            f"{len(matches)} VMs are named '{sanitize(vm_name, 200)}'. This skill "
            "refuses to guess which one you meant, because every operation that "
            "looks a VM up by name goes on to change it. Run vm_investigation_bundle "
            "or list_virtual_machines (vmware-monitor) to tell them apart, then "
            "retry once the duplicate is renamed or removed."
        )
    return matches[0] if matches else None


def find_host_by_name(si: ServiceInstance, host_name: str) -> vim.HostSystem | None:
    """Find a host by name. Returns None if not found."""
    return _find_by_name(si, [vim.HostSystem], host_name)


def find_datastore_by_name(
    si: ServiceInstance, ds_name: str
) -> vim.Datastore | None:
    """Find a datastore by name. Returns None if not found."""
    return _find_by_name(si, [vim.Datastore], ds_name)


def find_cluster_by_name(
    si: ServiceInstance, cluster_name: str
) -> vim.ClusterComputeResource | None:
    """Find a cluster by exact name. Returns None if not found."""
    return _find_by_name(si, [vim.ClusterComputeResource], cluster_name)


def find_datacenter_by_name(
    si: ServiceInstance, dc_name: str
) -> vim.Datacenter | None:
    """Find a datacenter by exact name. Returns None if not found."""
    return _find_by_name(si, [vim.Datacenter], dc_name)


def resolve_datacenter(
    si: ServiceInstance, datacenter_name: str | None = None
) -> vim.Datacenter:
    """Resolve a datacenter by name, or return the first one in inventory.

    Searches explicitly for vim.Datacenter objects rather than assuming
    ``rootFolder.childEntity[0]`` is a datacenter — that assumption breaks on
    multi-DC inventories (wrong DC), top-level folders (wrong type), and empty
    inventories (IndexError). Raises InventoryError instead.
    """
    if datacenter_name:
        dc = find_datacenter_by_name(si, datacenter_name)
        if dc is None:
            raise InventoryError(
                f"Datacenter '{datacenter_name}' not found on this target. "
                f"Omit --datacenter (MCP arg: datacenter_name) to use the first "
                f"datacenter, or copy an exact name from the vSphere Client inventory "
                f"tree. Check you are on the right vCenter with 'vmware-aiops doctor'."
            )
        return dc
    content = si.RetrieveContent()
    for child in content.rootFolder.childEntity:
        if isinstance(child, vim.Datacenter):
            return child
    raise InventoryError(
        "No datacenter found in this vCenter's inventory — nothing can be deployed "
        "until one exists. Create a datacenter in the vSphere Client, or check that "
        "--target points at the intended vCenter: run 'vmware-aiops doctor'."
    )


def find_compute_resource(
    dc: vim.Datacenter, cluster_name: str | None = None
) -> vim.ComputeResource:
    """Find a ComputeResource (cluster or standalone host) in a datacenter.

    Searches the datacenter's host folder explicitly for a ComputeResource
    rather than assuming ``hostFolder.childEntity[0]`` is one — nested folders
    and empty host folders break that assumption. When ``cluster_name`` is
    given, matches by name; otherwise returns the first ComputeResource found.
    """
    def _walk(folder) -> vim.ComputeResource | None:
        for child in getattr(folder, "childEntity", []) or []:
            if isinstance(child, vim.ComputeResource):
                if cluster_name is None or child.name == cluster_name:
                    return child
            elif isinstance(child, vim.Folder):
                found = _walk(child)
                if found is not None:
                    return found
        return None

    cr = _walk(dc.hostFolder)
    if cr is None:
        if cluster_name:
            raise InventoryError(
                f"Compute resource '{cluster_name}' not found in datacenter "
                f"'{dc.name}'. Run cluster_health_summary (CLI: vmware-aiops summary) "
                f"to see every cluster on this target, then retry with an exact name."
            )
        raise InventoryError(
            f"No compute resource (cluster or host) found in datacenter '{dc.name}' — "
            f"there is nowhere to place a VM. Add a host or cluster to that datacenter "
            f"in the vSphere Client, or check --target/--datacenter point at the right "
            f"place: run 'vmware-aiops doctor'."
        )
    return cr
