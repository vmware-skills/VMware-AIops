"""dvSwitch portgroup ops: validation, preview/confirm gate, spec correctness.

Probes input shapes, not just values: the preview leg must never write, the
confirm leg must send exactly the validated spec, and every validation error
must fire BEFORE any task is issued.
"""
import types

import pytest
from pyVmomi import vim

from vmware_aiops.ops import network_mgmt
from vmware_aiops.ops.network_mgmt import (
    DvsNotFoundError,
    NetworkError,
    create_dvs_portgroup,
    list_dvs_portgroups,
)


class FakePG:
    def __init__(self, name, binding="earlyBinding", vlan=None, num_ports=8, uplink=False):
        self.name = name
        cfg = types.SimpleNamespace()
        cfg.type = binding
        cfg.numPorts = num_ports
        cfg.uplink = uplink
        cfg.defaultPortConfig = types.SimpleNamespace(vlan=vlan)
        self.config = cfg


class FakeDVS:
    def __init__(self, name, portgroups=()):
        self.name = name
        self.portgroup = list(portgroups)
        self.created_specs = []

    def CreateDVPortgroup_Task(self, spec):  # noqa: N802 - pyVmomi API name
        self.created_specs.append(spec)
        return "fake-task"


@pytest.fixture
def env(monkeypatch):
    """One switch with one existing portgroup; task-wait is a no-op recorder."""
    vlan = vim.dvs.VmwareDistributedVirtualSwitch.VlanIdSpec()
    vlan.vlanId = 100
    dvs = FakeDVS("DSwitch", [FakePG("existing-pg", vlan=vlan)])
    monkeypatch.setattr(network_mgmt, "_get_objects", lambda si, types_: [dvs])
    waited = []
    monkeypatch.setattr(network_mgmt, "_wait_for_task", lambda t: waited.append(t))
    return types.SimpleNamespace(dvs=dvs, waited=waited, si=object())


# --- validation (must all fire before any task) -------------------------------

def test_rejects_bad_binding_including_deprecated_latebinding(env):
    for bad in ("lateBinding", "static", "", "EPHEMERAL"):
        with pytest.raises(NetworkError, match="binding"):
            create_dvs_portgroup(env.si, "pg", "DSwitch", 100, binding=bad, confirm=True)
    assert env.dvs.created_specs == []


def test_rejects_vlan_out_of_range(env):
    for bad in (-1, 4095, 99999):
        with pytest.raises(NetworkError, match="vlan_id"):
            create_dvs_portgroup(env.si, "pg", "DSwitch", bad, confirm=True)
    assert env.dvs.created_specs == []


def test_rejects_duplicate_name(env):
    with pytest.raises(NetworkError, match="already exists"):
        create_dvs_portgroup(env.si, "existing-pg", "DSwitch", 100, confirm=True)
    assert env.dvs.created_specs == []


def test_unknown_dvs_raises_with_available_list(env):
    with pytest.raises(DvsNotFoundError, match="DSwitch"):
        create_dvs_portgroup(env.si, "pg", "nope-vds", 100, confirm=True)
    assert env.dvs.created_specs == []


# --- preview/confirm gate ------------------------------------------------------

def test_preview_validates_but_never_writes(env):
    out = create_dvs_portgroup(env.si, "pg-app", "DSwitch", 100, binding="ephemeral")
    assert out["action"] == "preview"
    assert out["would_create"]["vlan_id"] == 100
    assert out["would_create"]["binding"] == "ephemeral"
    assert out["would_create"]["num_ports"] == 0  # ephemeral has no port pool
    assert env.dvs.created_specs == []
    assert env.waited == []


def test_confirm_creates_with_exact_spec(env):
    out = create_dvs_portgroup(
        env.si, "pg-app", "DSwitch", 100, binding="earlyBinding",
        num_ports=16, confirm=True,
    )
    assert out["action"] == "created"
    assert len(env.dvs.created_specs) == 1
    spec = env.dvs.created_specs[0]
    assert spec.name == "pg-app"
    assert spec.type == "earlyBinding"
    assert spec.numPorts == 16
    assert spec.defaultPortConfig.vlan.vlanId == 100
    assert spec.defaultPortConfig.vlan.inherited is False
    assert env.waited == ["fake-task"]


def test_confirm_ephemeral_leaves_numports_unset(env):
    create_dvs_portgroup(env.si, "pg-mgmt", "DSwitch", 100, binding="ephemeral", confirm=True)
    spec = env.dvs.created_specs[0]
    assert spec.type == "ephemeral"
    assert not spec.numPorts  # vCenter ignores it; we never send a pool size


# --- list ----------------------------------------------------------------------

def _make_fake_collect(dvs_list):
    """Stand in for inventory._collect over a set of FakeDVS objects, dispatching
    on requested obj_type as PropertyCollector would: [(obj, {path: value})]."""
    def fake(si, obj_type, paths):
        if obj_type[0] is vim.DistributedVirtualSwitch:
            return [(d, {"name": d.name}) for d in dvs_list]
        rows = []
        for d in dvs_list:
            for pg in d.portgroup:
                rows.append((pg, {
                    "name": pg.name,
                    "config.type": pg.config.type,
                    "config.numPorts": pg.config.numPorts,
                    "config.defaultPortConfig": pg.config.defaultPortConfig,
                    "config.uplink": pg.config.uplink,
                    "config.distributedVirtualSwitch": d,
                }))
        return rows
    return fake


def test_list_reports_vlan_shapes(env, monkeypatch):
    trunk = vim.dvs.VmwareDistributedVirtualSwitch.TrunkVlanSpec()
    r = vim.NumericRange()
    r.start, r.end = 100, 200
    trunk.vlanId = [r]
    pvlan = vim.dvs.VmwareDistributedVirtualSwitch.PvlanSpec()
    pvlan.pvlanId = 42
    vlan_id = vim.dvs.VmwareDistributedVirtualSwitch.VlanIdSpec()
    vlan_id.vlanId = 100
    dvs = FakeDVS("vds2", [
        FakePG("a", vlan=vlan_id),
        FakePG("b", vlan=trunk),
        FakePG("c", vlan=pvlan),
        FakePG("d", vlan=None),
    ])
    monkeypatch.setattr(network_mgmt, "_collect", _make_fake_collect([dvs]))
    out = list_dvs_portgroups(object())
    vlans = {p["name"]: p["vlan"] for p in out["portgroups"]}
    assert vlans == {"a": "100", "b": "trunk 100-200", "c": "pvlan 42", "d": "unset"}


def test_list_scoped_to_named_dvs(env, monkeypatch):
    # A second switch whose portgroups must be filtered OUT by the dvs_name scope.
    other = FakeDVS("OtherSwitch", [FakePG("other-pg")])
    monkeypatch.setattr(
        network_mgmt, "_collect", _make_fake_collect([env.dvs, other])
    )
    out = list_dvs_portgroups(env.si, dvs_name="DSwitch")
    assert out["total"] == 1
    assert out["portgroups"][0]["name"] == "existing-pg"
    assert out["portgroups"][0]["vlan"] == "100"


def test_list_unknown_dvs_scope_raises_with_available(env, monkeypatch):
    monkeypatch.setattr(network_mgmt, "_collect", _make_fake_collect([env.dvs]))
    with pytest.raises(NetworkError, match="not found"):
        list_dvs_portgroups(env.si, dvs_name="nope-vds")


def test_preview_carries_blockers_and_unmeasured(env):
    """Review L2: the portgroup preview says what stands in the way (nothing)."""
    out = create_dvs_portgroup(env.si, "pg-app", "DSwitch", 100)
    assert out["blast_radius"]["blockers"] == []
    assert out["blast_radius"]["unmeasured"] == []
