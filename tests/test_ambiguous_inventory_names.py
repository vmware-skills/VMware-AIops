"""Host, cluster, datastore and datacenter lookups refuse a duplicated name.

VMs already do (``AmbiguousVMError``, 2026-09-16). The other finders still
returned whichever object the property collector listed first. Cluster and
datastore names are unique only within a datacenter, so on a vCenter with two
datacenters ``add_cluster_host("Cluster01", …)`` could move a host into the
other datacenter's cluster, and the result reads exactly like a success.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pyVmomi import vim
from vmware_policy.budget import reset_budget
from vmware_policy.policy import reset_policy_engine
from vmware_policy.undo import reset_undo_store

import vmware_aiops.mcp_server.tools.cluster as cluster_tools
from vmware_aiops.ops import inventory
from vmware_aiops.ops.inventory import AmbiguousNameError, AmbiguousVMError, InventoryError


@pytest.fixture(autouse=True)
def harness(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_HOME", str(tmp_path))
    reset_policy_engine()
    reset_budget()
    reset_undo_store()
    yield
    reset_policy_engine()
    reset_budget()
    reset_undo_store()


def _obj(name: str, moid: str) -> MagicMock:
    obj = MagicMock(name=f"obj:{name}:{moid}")
    obj.name = name
    obj._moId = moid
    return obj


@pytest.fixture
def inventory_of(monkeypatch):
    """Serve ``_collect`` per managed-object type from a fixed inventory."""
    def install(by_type: dict):
        monkeypatch.setattr(
            inventory, "_collect",
            lambda _si, types, _props: [(o, {"name": o.name}) for o in by_type.get(types[0], [])],
        )
        return by_type
    return install


FINDERS = [
    (inventory.find_host_by_name, vim.HostSystem, "host"),
    (inventory.find_cluster_by_name, vim.ClusterComputeResource, "cluster"),
    (inventory.find_datastore_by_name, vim.Datastore, "datastore"),
    (inventory.find_datacenter_by_name, vim.Datacenter, "datacenter"),
]


@pytest.mark.parametrize(("finder", "kind", "label"), FINDERS)
def test_a_duplicated_name_is_refused_and_names_both_objects(inventory_of, finder, kind, label):
    inventory_of({kind: [_obj("X", "obj-1"), _obj("X", "obj-2"), _obj("Y", "obj-3")]})

    with pytest.raises(AmbiguousNameError) as raised:
        finder(MagicMock(), "X")

    message = str(raised.value)
    assert f"2 {label}s are named 'X'" in message
    assert "obj-1" in message and "obj-2" in message


@pytest.mark.parametrize(("finder", "kind", "label"), FINDERS)
def test_a_unique_name_still_resolves_and_a_missing_one_is_none(inventory_of, finder, kind, label):
    only = _obj("X", "obj-1")
    inventory_of({kind: [only, _obj("Y", "obj-2")]})

    assert finder(MagicMock(), "X") is only
    assert finder(MagicMock(), "nope") is None


def test_the_vm_error_is_one_kind_of_ambiguous_name():
    # Callers that catch AmbiguousVMError keep working; new ones catch the base.
    assert issubclass(AmbiguousVMError, AmbiguousNameError)
    assert issubclass(AmbiguousNameError, InventoryError)


def test_add_cluster_host_refuses_a_duplicated_cluster_and_moves_nothing(inventory_of, monkeypatch):
    dc1, dc2 = _obj("Cluster01", "domain-c1"), _obj("Cluster01", "domain-c2")
    inventory_of({vim.ClusterComputeResource: [dc1, dc2], vim.HostSystem: [_obj("esx-01", "host-1")]})
    monkeypatch.setattr(cluster_tools, "_get_connection", lambda _t=None: MagicMock())

    out = cluster_tools.add_cluster_host("Cluster01", "esx-01")

    assert "2 clusters are named 'Cluster01'" in out
    dc1.MoveInto_Task.assert_not_called()
    dc2.MoveInto_Task.assert_not_called()


def test_a_plan_that_migrates_to_a_duplicated_host_fails_validation_not_the_planner(inventory_of):
    from vmware_aiops.ops.planner import precheck_targets

    inventory_of({
        vim.VirtualMachine: [_obj("web-01", "vm-1")],
        vim.HostSystem: [_obj("esx-01", "host-1"), _obj("esx-01", "host-2")],
    })

    errors = precheck_targets(
        MagicMock(), [{"action": "migrate", "vm_name": "web-01", "target_host": "esx-01"}]
    )

    assert any("2 hosts are named 'esx-01'" in e for e in errors), errors


def test_many_matches_list_a_few_ids_and_count_the_rest(inventory_of):
    inventory_of({vim.Datastore: [_obj("ds", f"datastore-{i}") for i in range(8)]})

    with pytest.raises(AmbiguousNameError) as raised:
        inventory.find_datastore_by_name(MagicMock(), "ds")

    message = str(raised.value)
    assert "8 datastores are named 'ds'" in message
    assert "datastore-4" in message and "datastore-5" not in message
    assert "and 3 more" in message
