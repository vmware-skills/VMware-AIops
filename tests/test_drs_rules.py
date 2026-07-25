"""DRS rule ops: listing projections, exact-name matching, preview/confirm
gates, idempotent noops, VM-VM-only guards, and spec application shapes."""
import types

import pytest
from pyVmomi import vim

from vmware_aiops.ops import cluster_mgmt as cm
from vmware_aiops.ops.cluster_mgmt import (
    ClusterError,
    create_drs_rule,
    delete_drs_rule,
    list_drs_rules,
    set_drs_rule_enabled,
)

VM_NAMES = {"vm-101": "cppm1", "vm-102": "amm-2"}


def _vm_ref(mo_id):
    return vim.VirtualMachine(mo_id)


def _anti_rule(key=42, name="Aruba_Wirless_Servers", enabled=True):
    return vim.cluster.AntiAffinityRuleSpec(
        key=key, name=name, enabled=enabled,
        vm=[_vm_ref("vm-101"), _vm_ref("vm-102")],
    )


def _vmhost_rule(key=77, name="oracle-must-run"):
    return vim.cluster.VmHostRuleInfo(
        key=key, name=name, enabled=True, mandatory=True,
        vmGroupName="oracle-vms", affineHostGroupName="licensed-hosts",
    )


class FakeCluster:
    """Cluster whose ReconfigureComputeResource_Task applies rulesSpec ops."""

    def __init__(self, rules):
        self.name = "ANC-UCS-PROD"
        self._rules = list(rules)
        self.applied_specs = []
        self._next_key = 1000

    @property
    def configurationEx(self):  # noqa: N802 - pyVmomi property name
        return types.SimpleNamespace(rule=list(self._rules))

    def ReconfigureComputeResource_Task(self, spec, modify):  # noqa: N802
        assert modify is True
        self.applied_specs.append(spec)
        for rs in spec.rulesSpec:
            if rs.operation == "edit":
                self._rules = [
                    rs.info if r.key == rs.info.key else r for r in self._rules
                ]
            elif rs.operation == "add":
                rs.info.key = self._next_key
                self._rules.append(rs.info)
            elif rs.operation == "remove":
                self._rules = [r for r in self._rules if r.key != rs.removeKey]
        return object()  # task sentinel; _wait_for_task is patched out


@pytest.fixture
def env(monkeypatch):
    cluster = FakeCluster([_anti_rule(), _vmhost_rule()])
    monkeypatch.setattr(cm, "find_cluster_by_name",
                        lambda si, n: cluster if n == cluster.name else None)
    monkeypatch.setattr(cm, "_wait_for_task", lambda task: None)
    monkeypatch.setattr(cm, "_vm_name", lambda vm: VM_NAMES[vm._moId])
    return types.SimpleNamespace(cluster=cluster, si=object())


# --- list -------------------------------------------------------------------

def test_list_projects_both_rule_shapes(env):
    out = list_drs_rules(env.si, "ANC-UCS-PROD")
    assert out["count"] == 2
    anti = next(r for r in out["rules"] if r["type"] == "antiAffinity")
    assert anti["name"] == "Aruba_Wirless_Servers"
    assert anti["enabled"] is True
    assert anti["vms"] == ["amm-2", "cppm1"]
    vmhost = next(r for r in out["rules"] if r["type"] == "vmHost")
    assert vmhost["mandatory"] is True
    assert vmhost["affine_host_group"] == "licensed-hosts"
    assert "vms" not in vmhost


def test_unknown_cluster_and_rule_teach(env):
    with pytest.raises(cm.ClusterNotFoundError):
        list_drs_rules(env.si, "nope")
    with pytest.raises(ClusterError, match="Existing rules"):
        set_drs_rule_enabled(env.si, "ANC-UCS-PROD", "nope-rule", False)


def test_ambiguous_rule_name_refuses(env):
    env.cluster._rules.append(_anti_rule(key=43))  # duplicate name
    with pytest.raises(ClusterError, match="ambiguous"):
        set_drs_rule_enabled(env.si, "ANC-UCS-PROD", "Aruba_Wirless_Servers",
                             False, confirm=True)
    assert env.cluster.applied_specs == []


# --- enable/disable ----------------------------------------------------------

def test_set_enabled_noop_and_preview_never_write(env):
    out = set_drs_rule_enabled(env.si, "ANC-UCS-PROD",
                               "Aruba_Wirless_Servers", True, confirm=True)
    assert out["action"] == "noop"
    out = set_drs_rule_enabled(env.si, "ANC-UCS-PROD",
                               "Aruba_Wirless_Servers", False)
    assert out["action"] == "preview"
    assert out["would_set"]["enabled"] is False
    assert env.cluster.applied_specs == []


def test_set_enabled_confirm_roundtrip(env):
    out = set_drs_rule_enabled(env.si, "ANC-UCS-PROD",
                               "Aruba_Wirless_Servers", False, confirm=True)
    assert out["action"] == "set"
    assert out["rule_now"]["enabled"] is False
    out = set_drs_rule_enabled(env.si, "ANC-UCS-PROD",
                               "Aruba_Wirless_Servers", True, confirm=True)
    assert out["rule_now"]["enabled"] is True
    ops = [s.rulesSpec[0].operation for s in env.cluster.applied_specs]
    assert ops == ["edit", "edit"]


# --- create -------------------------------------------------------------------

def _patch_resolve(monkeypatch, refs):
    monkeypatch.setattr(cm, "_resolve_rule_vms", lambda si, c, names: refs)


def test_create_validates_type_name_and_members(env, monkeypatch):
    with pytest.raises(ClusterError, match="rule_type"):
        create_drs_rule(env.si, "ANC-UCS-PROD", "r", "vmHost",
                        ["a", "b"], confirm=True)
    with pytest.raises(ClusterError, match="already exists"):
        create_drs_rule(env.si, "ANC-UCS-PROD", "Aruba_Wirless_Servers",
                        "antiAffinity", ["a", "b"], confirm=True)
    with pytest.raises(ClusterError, match="at least 2 distinct"):
        create_drs_rule(env.si, "ANC-UCS-PROD", "r", "antiAffinity",
                        ["a", "a"], confirm=True)
    assert env.cluster.applied_specs == []


def test_resolve_rule_vms_membership_guard(env, monkeypatch):
    other_cluster = object()
    vms = {
        "in": types.SimpleNamespace(
            resourcePool=types.SimpleNamespace(owner=env.cluster)),
        "outside": types.SimpleNamespace(
            resourcePool=types.SimpleNamespace(owner=other_cluster)),
        "template": types.SimpleNamespace(resourcePool=None),
    }
    monkeypatch.setattr(cm, "find_vm_by_name", lambda si, n: vms.get(n))
    with pytest.raises(ClusterError, match="not found"):
        cm._resolve_rule_vms(env.si, env.cluster, ["missing"])
    with pytest.raises(ClusterError, match="not in cluster"):
        cm._resolve_rule_vms(env.si, env.cluster, ["outside"])
    with pytest.raises(ClusterError, match="not in cluster"):
        cm._resolve_rule_vms(env.si, env.cluster, ["template"])
    assert cm._resolve_rule_vms(env.si, env.cluster, ["in"]) == [vms["in"]]


def test_create_preview_then_confirm(env, monkeypatch):
    _patch_resolve(monkeypatch, [_vm_ref("vm-101"), _vm_ref("vm-102")])
    out = create_drs_rule(env.si, "ANC-UCS-PROD", "keep-apart",
                          "antiAffinity", ["cppm1", "amm-2"])
    assert out["action"] == "preview"
    assert out["would_create"]["vms"] == ["cppm1", "amm-2"]
    assert env.cluster.applied_specs == []

    out = create_drs_rule(env.si, "ANC-UCS-PROD", "keep-apart",
                          "antiAffinity", ["cppm1", "amm-2"], confirm=True)
    assert out["action"] == "created"
    assert out["created"]["type"] == "antiAffinity"
    assert out["created"]["key"] == 1000
    spec = env.cluster.applied_specs[-1].rulesSpec[0]
    assert spec.operation == "add"
    assert isinstance(spec.info, vim.cluster.AntiAffinityRuleSpec)
    assert spec.info.userCreated is True


def test_create_affinity_type_maps(env, monkeypatch):
    _patch_resolve(monkeypatch, [_vm_ref("vm-101"), _vm_ref("vm-102")])
    out = create_drs_rule(env.si, "ANC-UCS-PROD", "keep-together",
                          "affinity", ["cppm1", "amm-2"], confirm=True)
    assert out["created"]["type"] == "affinity"


# --- delete -------------------------------------------------------------------

def test_delete_refuses_vmhost_rules(env):
    with pytest.raises(ClusterError, match="REFUSED.*vmHost"):
        delete_drs_rule(env.si, "ANC-UCS-PROD", "oracle-must-run", confirm=True)
    assert env.cluster.applied_specs == []


def test_delete_preview_records_definition_then_confirm(env):
    out = delete_drs_rule(env.si, "ANC-UCS-PROD", "Aruba_Wirless_Servers")
    assert out["action"] == "preview"
    assert out["would_delete"]["rule"]["vms"] == ["amm-2", "cppm1"]
    assert env.cluster.applied_specs == []

    out = delete_drs_rule(env.si, "ANC-UCS-PROD", "Aruba_Wirless_Servers",
                          confirm=True)
    assert out["action"] == "deleted"
    assert out["deleted"]["rule"]["key"] == 42
    spec = env.cluster.applied_specs[-1].rulesSpec[0]
    assert spec.operation == "remove"
    assert spec.removeKey == 42
    assert list_drs_rules(env.si, "ANC-UCS-PROD")["count"] == 1
