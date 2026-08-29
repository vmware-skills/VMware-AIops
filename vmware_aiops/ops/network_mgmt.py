"""dvSwitch portgroup management: list + preview/confirm-gated create.

First network-authoring surface in aiops. Create is gated per house
convention: confirm=False returns a preview of exactly what would be
created (and validates everything it can without writing); confirm=True
executes. Ephemeral binding is first-class - an ephemeral portgroup is
attachable from the ESXi host client even with vCenter down, which is
the self-hosted-VCSA use case.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pyVmomi import vim
from vmware_policy import paginated, sanitize

from vmware_aiops.ops.inventory import _collect
from vmware_aiops.ops.vm_lifecycle import _wait_for_task

if TYPE_CHECKING:
    from pyVmomi.vim import ServiceInstance


def _get_objects(si: ServiceInstance, obj_type: list, recursive: bool = True) -> list:
    """Container-view walk returning raw managed objects.

    Local on purpose: the module enumerates small sets (dvSwitches) and then
    reads nested config off each object, which the batched PropertyCollector
    helpers in ``inventory`` don't cover; keeping the helper here also keeps
    the module free of private cross-module imports.
    """
    content = si.RetrieveContent()
    container = content.viewManager.CreateContainerView(
        content.rootFolder, obj_type, recursive
    )
    try:
        return list(container.view)
    finally:
        container.Destroy()


class NetworkError(Exception):
    """Raised on network operation failures."""


class DvsNotFoundError(NetworkError):
    """Raised when a distributed virtual switch is not found by name."""


# lateBinding is deprecated by vSphere; do not offer it.
_VALID_BINDINGS = {"earlyBinding", "ephemeral"}
_VLAN_MIN, _VLAN_MAX = 0, 4094


def _find_dvs_by_name(si: ServiceInstance, name: str) -> vim.DistributedVirtualSwitch:
    for dvs in _get_objects(si, [vim.DistributedVirtualSwitch]):
        if dvs.name == name:
            return dvs
    available = sorted(d.name for d in _get_objects(si, [vim.DistributedVirtualSwitch]))
    raise DvsNotFoundError(
        f"Distributed switch '{name}' not found. Available: {available or 'none'}"
    )


def _vlan_description(port_config) -> str:
    """Human-readable VLAN setting of a portgroup's defaultPortConfig."""
    vlan = getattr(port_config, "vlan", None)
    if vlan is None:
        return "unset"
    if isinstance(vlan, vim.dvs.VmwareDistributedVirtualSwitch.TrunkVlanSpec):
        ranges = ",".join(
            f"{r.start}-{r.end}" if r.start != r.end else str(r.start)
            for r in (vlan.vlanId or [])
        )
        return f"trunk {ranges or 'all'}"
    if isinstance(vlan, vim.dvs.VmwareDistributedVirtualSwitch.PvlanSpec):
        return f"pvlan {vlan.pvlanId}"
    if isinstance(vlan, vim.dvs.VmwareDistributedVirtualSwitch.VlanIdSpec):
        return str(vlan.vlanId)
    return type(vlan).__name__


def list_dvs_portgroups(
    si: ServiceInstance,
    dvs_name: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> dict:
    """List distributed portgroups, optionally scoped to one dvSwitch.

    Batched via PropertyCollector (the inventory ``_collect`` path) so a large
    estate is one server-side call rather than a container-view walk with a
    per-portgroup ``.config`` round-trip. Parent-switch names resolve through a
    single companion ``_collect`` (the ``list_virtual_machines`` host-name
    pattern).
    """
    dvs_names = {
        obj: p.get("name")
        for obj, p in _collect(si, [vim.DistributedVirtualSwitch], ["name"])
    }
    target_refs = None
    if dvs_name is not None:
        target_refs = {ref for ref, nm in dvs_names.items() if nm == dvs_name}
        if not target_refs:
            available = sorted(n for n in dvs_names.values() if n)
            raise DvsNotFoundError(
                f"Distributed switch '{dvs_name}' not found. "
                f"Available: {available or 'none'}"
            )
    out = []
    for _obj, p in _collect(
        si,
        [vim.dvs.DistributedVirtualPortgroup],
        [
            "name",
            "config.type",
            "config.numPorts",
            "config.defaultPortConfig",
            "config.uplink",
            "config.distributedVirtualSwitch",
        ],
    ):
        parent = p.get("config.distributedVirtualSwitch")
        if target_refs is not None and parent not in target_refs:
            continue
        out.append({
            "name": sanitize(p.get("name", ""), 200),
            "dvs": sanitize(dvs_names.get(parent) or "N/A", 200),
            "binding": str(p.get("config.type", "N/A")),
            "vlan": _vlan_description(p.get("config.defaultPortConfig")),
            "num_ports": p.get("config.numPorts") or 0,
            "uplink": bool(p.get("config.uplink") or False),
        })
    total = len(out)
    window = out[offset : offset + limit] if limit > 0 else out[offset:]
    # The family envelope, not a hand-rolled near-miss. The previous shape —
    # {total, returned, portgroups} — was self-consistent and still wrong: an
    # agent that knows this family reads `items` and got nothing, and there was
    # no `truncated`/`hint`, which is precisely what issue #31 showed a model
    # cannot infer for itself. `portgroups` is kept as a deprecated alias so
    # nothing that reads it breaks.
    result = paginated(window, limit=limit if limit > 0 else None, total=total)
    result["portgroups"] = result["items"]
    if offset:
        result["offset"] = offset
    return result


def create_dvs_portgroup(
    si: ServiceInstance,
    name: str,
    dvs_name: str,
    vlan_id: int,
    binding: str = "earlyBinding",
    num_ports: int = 8,
    confirm: bool = False,
) -> dict:
    """Create a VLAN-tagged portgroup on a dvSwitch, preview/confirm gated.

    confirm=False validates (switch exists, name free on that switch, binding
    and VLAN legal) and returns the exact spec that WOULD be created, without
    writing. confirm=True creates it and waits for the task.
    """
    if binding not in _VALID_BINDINGS:
        raise NetworkError(
            f"binding must be one of {sorted(_VALID_BINDINGS)}, got '{binding}' "
            "(lateBinding is deprecated by vSphere and not offered)"
        )
    if not (_VLAN_MIN <= vlan_id <= _VLAN_MAX):
        raise NetworkError(f"vlan_id must be {_VLAN_MIN}-{_VLAN_MAX}, got {vlan_id}")
    if binding == "earlyBinding" and num_ports < 1:
        raise NetworkError(f"num_ports must be >= 1 for earlyBinding, got {num_ports}")

    dvs = _find_dvs_by_name(si, dvs_name)
    existing = {pg.name for pg in dvs.portgroup or []}
    if name in existing:
        raise NetworkError(f"Portgroup '{name}' already exists on '{dvs_name}'")

    # Ephemeral portgroups have no pre-created port pool; vCenter ignores
    # numPorts for them - report 0 to keep the preview honest.
    effective_ports = num_ports if binding == "earlyBinding" else 0
    planned = {
        "name": name,
        "dvs": dvs.name,
        "vlan_id": vlan_id,
        "binding": binding,
        "num_ports": effective_ports,
    }
    if not confirm:
        return {
            "action": "preview",
            "would_create": planned,
            "hint": "Re-run with confirm=True to create.",
        }

    spec = vim.dvs.DistributedVirtualPortgroup.ConfigSpec()
    spec.name = name
    spec.type = binding
    if binding == "earlyBinding":
        spec.numPorts = num_ports
    port_config = vim.dvs.VmwareDistributedVirtualSwitch.VmwarePortConfigPolicy()
    vlan_spec = vim.dvs.VmwareDistributedVirtualSwitch.VlanIdSpec()
    vlan_spec.vlanId = vlan_id
    vlan_spec.inherited = False
    port_config.vlan = vlan_spec
    spec.defaultPortConfig = port_config

    _wait_for_task(dvs.CreateDVPortgroup_Task(spec))
    return {"action": "created", "created": planned}
