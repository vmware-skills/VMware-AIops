"""The confirmation gate (HLD §7, revised 2026-09-16) on six more destructive tools.

``cluster_delete``, ``cluster_remove_host``, ``vm_set_ttl``, ``vm_clean_slate``,
``vm_apply_plan`` and ``vm_rollback_plan`` each take ``confirm: bool = False``:

* L2 — a bare call previews and calls no write API.
* L1 — the preview and the acting response both carry ``blast_radius``.
* L3 — ``confirm=True`` refuses on a blocker or on anything it could not read.

None of them takes ``acknowledge_blast_radius``: Pilot must be able to drive
them with ``confirm=True`` after its own human approval step.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from pyVmomi import vim
from vmware_policy.budget import reset_budget
from vmware_policy.policy import reset_policy_engine
from vmware_policy.undo import get_undo_store, reset_undo_store

import vmware_aiops.mcp_server.tools.cluster as cluster_tools
import vmware_aiops.mcp_server.tools.plan as plan_tools
import vmware_aiops.mcp_server.tools.ttl as ttl_tools
from vmware_aiops.ops import cluster_mgmt, inventory, plan_executor, planner, ttl, vm_lifecycle

ON = vim.VirtualMachine.PowerState.poweredOn
OFF = vim.VirtualMachine.PowerState.poweredOff
GATED = (
    "cluster_delete", "cluster_remove_host", "vm_set_ttl",
    "vm_clean_slate", "vm_apply_plan", "vm_rollback_plan",
)


def _unreadable(_self):
    # Not AttributeError: MagicMock would answer that with a fresh mock.
    raise TypeError("unreadable")


@pytest.fixture(autouse=True)
def harness(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_HOME", str(tmp_path))
    reset_policy_engine()
    reset_budget()
    reset_undo_store()
    for mod in (cluster_tools, ttl_tools, plan_tools):
        monkeypatch.setattr(mod, "_get_connection", lambda _t=None: MagicMock())
    monkeypatch.setattr(cluster_mgmt, "_wait_for_task", lambda _t, **_k: None)
    monkeypatch.setattr(vm_lifecycle, "_wait_for_task", lambda _t, **_k: None)
    monkeypatch.setattr(ttl, "_TTL_FILE", tmp_path / "ttl.json")
    monkeypatch.setattr(planner, "_PLANS_DIR", tmp_path / "plans")
    yield
    reset_policy_engine()
    reset_budget()
    reset_undo_store()


# ─── the schema and the wording ───────────────────────────────────────────────


def _tool(name):
    from vmware_aiops.mcp_server.server import mcp

    return next(t for t in asyncio.run(mcp.list_tools()) if t.name == name)


@pytest.mark.parametrize("name", GATED)
def test_schema_defaults_confirm_to_false(name):
    props = _tool(name).inputSchema["properties"]
    assert props["confirm"]["default"] is False
    assert "acknowledge_blast_radius" not in props


_MODULES = {
    "cluster_delete": cluster_tools, "cluster_remove_host": cluster_tools,
    "vm_set_ttl": ttl_tools, "vm_clean_slate": ttl_tools,
    "vm_apply_plan": plan_tools, "vm_rollback_plan": plan_tools,
}


@pytest.mark.parametrize("name", GATED)
def test_docstring_carries_the_normative_wording(name):
    doc = " ".join(getattr(_MODULES[name], name).__doc__.split())
    assert doc.startswith("[WRITE]")
    assert "confirm: False (default) returns the blast radius and changes nothing. " \
           "True applies it." in doc
    assert "Do not set confirm=True on your own" in doc
    assert "Do not set confirm=True on your own" in " ".join(_tool(name).description.split())


@pytest.mark.parametrize("name", GATED)
def test_every_gated_tool_is_advertised_destructive(name):
    assert _tool(name).annotations.destructiveHint is True


# ─── cluster fixtures ─────────────────────────────────────────────────────────


def _vm(name, power=OFF):
    vm = MagicMock(name=f"vm:{name}")
    vm.name = name
    vm.runtime.powerState = power
    return vm


def _host(name="esx-01", maintenance=True, vms=()):
    host = MagicMock(name=f"host:{name}")
    host.name = name
    host._moId = f"host-{name}"
    host.runtime.inMaintenanceMode = maintenance
    host.vm = list(vms)
    return host


def _cluster(name="lab", hosts=(), datastores=("ds1", "ds2")):
    cluster = MagicMock(name=f"cluster:{name}")
    cluster.name = name
    cluster._moId = "domain-c7"
    cluster.host = list(hosts)
    cluster.datastore = [MagicMock(name=f"ds:{d}") for d in datastores]
    for mock, d in zip(cluster.datastore, datastores):
        mock.name = d
    cluster.parent = MagicMock(spec=vim.Datacenter)
    return cluster


@pytest.fixture
def world(monkeypatch):
    """Serve one cluster and its hosts by name."""
    class World:
        cluster = None
        hosts: dict = {}

        def install(self, cluster, extra_hosts=()):
            self.cluster = cluster
            self.hosts = {h.name: h for h in list(cluster.host) + list(extra_hosts)}
            monkeypatch.setattr(
                cluster_mgmt, "find_cluster_by_name",
                lambda _si, n: cluster if n == cluster.name else None,
            )
            monkeypatch.setattr(
                cluster_mgmt, "find_host_by_name", lambda _si, n: self.hosts.get(n),
            )
            return cluster

    return World()


# ─── cluster_delete ───────────────────────────────────────────────────────────


def test_cluster_delete_bare_call_previews_and_destroys_nothing(world):
    c = world.install(_cluster())
    out = cluster_tools.cluster_delete("lab")
    c.Destroy_Task.assert_not_called()
    assert out["action"] == "preview"


def test_cluster_delete_preview_states_the_blast_radius(world):
    world.install(_cluster(hosts=[_host("esx-01", vms=[_vm("a", ON), _vm("b")]),
                                  _host("esx-02", vms=[_vm("c")])]))
    br = cluster_tools.cluster_delete("lab")["blast_radius"]
    assert br["cluster"] == "lab" and br["cluster_id"] == "domain-c7"
    assert br["host_count"] == 2 and br["hosts"] == ["esx-01", "esx-02"]
    assert br["vm_count"] == 3 and br["vms"] == ["a", "b", "c"]
    assert br["datastore_count"] == 2 and br["datastores"] == ["ds1", "ds2"]
    assert br["unmeasured"] == []


def test_cluster_delete_confirm_destroys_once_and_reports(world):
    c = world.install(_cluster())
    out = cluster_tools.cluster_delete("lab", confirm=True)
    c.Destroy_Task.assert_called_once()
    assert out["action"] == "deleted"
    assert out["blast_radius"]["host_count"] == 0


def test_cluster_delete_with_hosts_is_a_blocker_and_refuses(world):
    c = world.install(_cluster(hosts=[_host("esx-01")]))
    br = cluster_tools.cluster_delete("lab")["blast_radius"]
    assert any("cluster_remove_host" in b for b in br["blockers"])
    out = cluster_tools.cluster_delete("lab", confirm=True)
    c.Destroy_Task.assert_not_called()
    assert "esx-01" in out["error"] and "cluster_remove_host" in out["error"]


def test_cluster_delete_with_vms_is_a_blocker(world):
    world.install(_cluster(hosts=[_host("esx-01", vms=[_vm("a")])]))
    br = cluster_tools.cluster_delete("lab")["blast_radius"]
    assert any("1 VM" in b for b in br["blockers"])


def test_cluster_delete_unreadable_hosts_refuses(world):
    c = world.install(_cluster())
    type(c).host = property(_unreadable)
    preview = cluster_tools.cluster_delete("lab")
    assert "hosts" in preview["blast_radius"]["unmeasured"]
    out = cluster_tools.cluster_delete("lab", confirm=True)
    c.Destroy_Task.assert_not_called()
    assert "could not read hosts" in out["error"]


def test_cluster_delete_unreadable_vm_list_refuses(world):
    h = _host("esx-01")
    type(h).vm = property(_unreadable)
    c = world.install(_cluster(hosts=[h]))
    out = cluster_tools.cluster_delete("lab", confirm=True)
    c.Destroy_Task.assert_not_called()
    assert "error" in out


def test_cluster_delete_unknown_cluster_teaches(world):
    world.install(_cluster())
    out = cluster_tools.cluster_delete("nope")
    assert "not found" in out["error"]


# ─── cluster_remove_host ──────────────────────────────────────────────────────


def test_remove_host_bare_call_previews_and_moves_nothing(world):
    c = world.install(_cluster(hosts=[_host()]))
    out = cluster_tools.cluster_remove_host("lab", "esx-01")
    c.parent.hostFolder.MoveIntoFolder_Task.assert_not_called()
    assert out["action"] == "preview"


def test_remove_host_preview_states_the_blast_radius(world):
    world.install(_cluster(hosts=[_host(vms=[_vm("a"), _vm("b")])]))
    br = cluster_tools.cluster_remove_host("lab", "esx-01")["blast_radius"]
    assert br["host"] == "esx-01" and br["host_id"] == "host-esx-01"
    assert br["cluster"] == "lab"
    assert br["maintenance_mode"] is True
    assert br["vm_count"] == 2 and br["powered_on_vm_count"] == 0
    assert br["vms"] == ["a", "b"]
    assert br["blockers"] == [] and br["unmeasured"] == []


def test_remove_host_confirm_moves_once_and_reports(world):
    c = world.install(_cluster(hosts=[_host()]))
    out = cluster_tools.cluster_remove_host("lab", "esx-01", confirm=True)
    c.parent.hostFolder.MoveIntoFolder_Task.assert_called_once()
    assert out["action"] == "removed"
    assert out["blast_radius"]["host"] == "esx-01"


def test_remove_host_not_in_maintenance_is_a_blocker(world):
    c = world.install(_cluster(hosts=[_host(maintenance=False)]))
    assert cluster_tools.cluster_remove_host("lab", "esx-01")["blast_radius"]["blockers"]
    out = cluster_tools.cluster_remove_host("lab", "esx-01", confirm=True)
    c.parent.hostFolder.MoveIntoFolder_Task.assert_not_called()
    assert "maintenance mode" in out["error"]


def test_remove_host_with_powered_on_vms_is_a_blocker(world):
    c = world.install(_cluster(hosts=[_host(vms=[_vm("a", ON), _vm("b")])]))
    br = cluster_tools.cluster_remove_host("lab", "esx-01")["blast_radius"]
    assert br["powered_on_vm_count"] == 1
    out = cluster_tools.cluster_remove_host("lab", "esx-01", confirm=True)
    c.parent.hostFolder.MoveIntoFolder_Task.assert_not_called()
    assert "powered on" in out["error"]


def test_remove_host_unreadable_maintenance_mode_refuses(world):
    h = _host()
    type(h.runtime).inMaintenanceMode = property(_unreadable)
    c = world.install(_cluster(hosts=[h]))
    out = cluster_tools.cluster_remove_host("lab", "esx-01", confirm=True)
    c.parent.hostFolder.MoveIntoFolder_Task.assert_not_called()
    assert "maintenance_mode" in out["error"]


def test_remove_host_unreadable_vm_power_refuses(world):
    vm = _vm("a")
    type(vm.runtime).powerState = property(_unreadable)
    c = world.install(_cluster(hosts=[_host(vms=[vm])]))
    out = cluster_tools.cluster_remove_host("lab", "esx-01", confirm=True)
    c.parent.hostFolder.MoveIntoFolder_Task.assert_not_called()
    assert "vm_power_states" in out["error"]


def test_remove_host_not_a_member_keeps_its_refusal(world):
    world.install(_cluster(hosts=[_host()]), extra_hosts=[_host("esx-09")])
    out = cluster_tools.cluster_remove_host("lab", "esx-09")
    assert "is not in cluster 'lab'" in out["error"]


def test_remove_host_caps_the_vm_list(world):
    vms = [_vm(f"vm-{i:02d}") for i in range(20)]
    world.install(_cluster(hosts=[_host(vms=vms)]))
    br = cluster_tools.cluster_remove_host("lab", "esx-01")["blast_radius"]
    assert br["vm_count"] == 20 and len(br["vms"]) == 16


def test_cli_remove_host_ops_is_unchanged_for_powered_on_vms(world):
    """The powered-on blocker lives in the MCP gate; the CLI keeps its contract."""
    c = world.install(_cluster(hosts=[_host(vms=[_vm("a", ON)])]))
    cluster_mgmt.remove_host_from_cluster(MagicMock(), "lab", "esx-01")
    c.parent.hostFolder.MoveIntoFolder_Task.assert_called_once()


# ─── VM fixtures for TTL / clean slate ────────────────────────────────────────


def _snap(name, children=(), created="2026-09-01 10:00:00+00:00"):
    s = MagicMock(name=f"snap:{name}")
    s.name = name
    s.description = ""
    s.createTime = created
    s.state = "poweredOff"
    s.childSnapshotList = list(children)
    return s


def _full_vm(name="lab-01", power=OFF, snaps=(), instance_uuid="5012-aaaa"):
    vm = MagicMock(name=f"vm:{name}")
    vm.name = name
    vm.runtime.powerState = power
    vm.runtime.host.name = "esx-01"
    vm.config.instanceUuid = instance_uuid
    vm.config.hardware.device = []
    if snaps:
        vm.snapshot.rootSnapshotList = list(snaps)
    else:
        vm.snapshot = None
    return vm


@pytest.fixture
def vms(monkeypatch):
    def install(*served):
        monkeypatch.setattr(
            inventory, "_collect",
            lambda _si, _types, _props: [(v, {"name": v.name}) for v in served],
        )
        return served
    return install


# ─── vm_set_ttl ───────────────────────────────────────────────────────────────


def test_ttl_bare_call_previews_and_schedules_nothing(vms):
    vms(_full_vm())
    out = ttl_tools.vm_set_ttl("lab-01", 30)
    assert out["action"] == "preview"
    assert ttl.list_ttl()["items"] == []


def test_ttl_preview_states_the_vm_to_be_deleted_and_when(vms):
    vms(_full_vm(power=ON, snaps=[_snap("a"), _snap("b")]))
    br = ttl_tools.vm_set_ttl("lab-01", 30)["blast_radius"]
    assert br["vm"] == "lab-01" and br["instance_uuid"] == "5012-aaaa"
    assert br["snapshot_count"] == 2
    assert br["minutes"] == 30
    expires = datetime.fromisoformat(br["expires_at"])
    delta = (expires - datetime.now(timezone.utc)).total_seconds()
    assert 29 * 60 < delta <= 30 * 60 + 5
    # A running lab VM is the normal case: the daemon powers it off first.
    assert br["blockers"] == []
    assert "acknowledge_with" not in br


def test_ttl_preview_shows_the_ttl_it_would_replace(vms):
    vms(_full_vm())
    ttl.set_ttl("lab-01", 600)
    br = ttl_tools.vm_set_ttl("lab-01", 30)["blast_radius"]
    assert br["replaces_expires_at"] is not None


def test_ttl_confirm_schedules_once_and_reports(vms):
    vms(_full_vm())
    out = ttl_tools.vm_set_ttl("lab-01", 30, confirm=True)
    assert out["action"] == "scheduled"
    assert [e["vm_name"] for e in ttl.list_ttl()["items"]] == ["lab-01"]
    assert out["blast_radius"]["vm"] == "lab-01"


def test_ttl_unreadable_identity_refuses(vms):
    vm = _full_vm()
    vm.config = None
    vms(vm)
    out = ttl_tools.vm_set_ttl("lab-01", 30, confirm=True)
    assert "instance_uuid" in out["error"]
    assert ttl.list_ttl()["items"] == []


def test_ttl_missing_vm_teaches_and_schedules_nothing(vms):
    vms()
    out = ttl_tools.vm_set_ttl("ghost", 30, confirm=True)
    assert "not found" in out["error"]
    assert ttl.list_ttl()["items"] == []


def test_ttl_minutes_below_one_is_an_error(vms):
    vms(_full_vm())
    out = ttl_tools.vm_set_ttl("lab-01", 0, confirm=True)
    assert "at least 1 minute" in out["error"]
    assert ttl.list_ttl()["items"] == []


def test_ttl_preview_records_no_undo_token_and_confirm_records_one(vms):
    vms(_full_vm())
    preview = ttl_tools.vm_set_ttl("lab-01", 30)
    assert "_undo_id" not in preview
    assert get_undo_store().list() == []
    done = ttl_tools.vm_set_ttl("lab-01", 30, confirm=True)
    assert "_undo_id" in done
    assert len(get_undo_store().list()) == 1


def test_ttl_refusal_records_no_undo_token(vms):
    vm = _full_vm()
    vm.config = None
    vms(vm)
    ttl_tools.vm_set_ttl("lab-01", 30, confirm=True)
    assert get_undo_store().list() == []


# ─── vm_clean_slate ───────────────────────────────────────────────────────────


def test_clean_slate_bare_call_previews_and_changes_nothing(vms):
    base = _snap("baseline")
    (vm,) = vms(_full_vm(power=ON, snaps=[base]))
    out = ttl_tools.vm_clean_slate("lab-01")
    assert out["action"] == "preview"
    vm.PowerOff.assert_not_called()
    base.snapshot.RevertToSnapshot_Task.assert_not_called()


def test_clean_slate_preview_states_the_blast_radius(vms):
    vms(_full_vm(power=ON, snaps=[_snap("baseline", children=[_snap("later")])]))
    br = ttl_tools.vm_clean_slate("lab-01")["blast_radius"]
    assert br["vm"] == "lab-01" and br["instance_uuid"] == "5012-aaaa"
    assert br["snapshot"] == "baseline"
    assert br["snapshot_created"] == "2026-09-01 10:00:00+00:00"
    assert br["snapshot_count"] == 2
    assert br["power_state"] == "poweredOn" and br["powers_off_first"] is True
    assert br["blockers"] == [] and br["unmeasured"] == []


def test_clean_slate_confirm_reverts_once_and_reports(vms):
    base = _snap("baseline")
    (vm,) = vms(_full_vm(power=ON, snaps=[base]))
    out = ttl_tools.vm_clean_slate("lab-01", confirm=True)
    vm.PowerOff.assert_called_once()
    base.snapshot.RevertToSnapshot_Task.assert_called_once()
    assert out["action"] == "reverted"
    assert out["blast_radius"]["snapshot"] == "baseline"


def test_clean_slate_missing_baseline_refuses_without_powering_off(vms):
    """The ops function powers the VM off before finding out the snapshot is missing."""
    (vm,) = vms(_full_vm(power=ON, snaps=[_snap("other")]))
    assert ttl_tools.vm_clean_slate("lab-01")["blast_radius"]["blockers"]
    out = ttl_tools.vm_clean_slate("lab-01", confirm=True)
    vm.PowerOff.assert_not_called()
    assert "No snapshot named 'baseline'" in out["error"] and "other" in out["error"]


def test_clean_slate_no_snapshots_at_all_refuses(vms):
    (vm,) = vms(_full_vm(power=ON))
    out = ttl_tools.vm_clean_slate("lab-01", confirm=True)
    vm.PowerOff.assert_not_called()
    assert "No snapshot named 'baseline'" in out["error"]


def test_clean_slate_ambiguous_baseline_refuses(vms):
    first, second = _snap("baseline"), _snap("baseline")
    vms(_full_vm(snaps=[first, _snap("x", children=[second])]))
    br = ttl_tools.vm_clean_slate("lab-01")["blast_radius"]
    assert br["snapshot_matches"] == 2
    out = ttl_tools.vm_clean_slate("lab-01", confirm=True)
    first.snapshot.RevertToSnapshot_Task.assert_not_called()
    second.snapshot.RevertToSnapshot_Task.assert_not_called()
    assert "2 snapshots match 'baseline'" in out["error"]


def test_clean_slate_unreadable_power_state_refuses(vms):
    base = _snap("baseline")
    vm = _full_vm(snaps=[base])
    type(vm.runtime).powerState = property(_unreadable)
    vms(vm)
    out = ttl_tools.vm_clean_slate("lab-01", confirm=True)
    base.snapshot.RevertToSnapshot_Task.assert_not_called()
    assert "power_state" in out["error"]


def test_clean_slate_unreadable_snapshot_tree_refuses(vms):
    vm = _full_vm()
    type(vm).snapshot = property(_unreadable)
    vms(vm)
    out = ttl_tools.vm_clean_slate("lab-01", confirm=True)
    vm.PowerOff.assert_not_called()
    assert "snapshots" in out["error"]


# ─── plan fixtures ────────────────────────────────────────────────────────────


def _write_plan(steps, status="pending", target="vc01", plan_id="plan-t-0001"):
    planner._PLANS_DIR.mkdir(parents=True, exist_ok=True)
    plan = {
        "plan_id": plan_id, "created_at": "2026-09-19T00:00:00+00:00",
        "target": target, "status": status,
        "steps": steps, "summary": {"total_steps": len(steps)},
    }
    (planner._PLANS_DIR / f"{plan_id}.json").write_text(json.dumps(plan), encoding="utf-8")
    return plan_id


def _step(index, action, params, status="pending"):
    rb_action, rb_params = planner._build_rollback(action, params)
    return {
        "index": index, "action": action, "params": params,
        "rollback_action": rb_action, "rollback_params": rb_params,
        "status": status, "result": None, "executed_at": None,
    }


@pytest.fixture
def dispatched(monkeypatch, vms):
    # Destructive steps are measured the way their own tools measure them, so
    # the VMs the plans below name have to exist.
    web = _full_vm("web-01", power=ON)
    web.guest.toolsRunningStatus = "guestToolsRunning"
    # web-02 is what _failed_plan's rollback powers off; rollback steps are
    # measured too, so it has to exist.
    web2 = _full_vm("web-02", power=ON)
    web2.guest.toolsRunningStatus = "guestToolsRunning"
    vms(web, web2, _full_vm("old-01", instance_uuid="x"))
    calls: list = []
    monkeypatch.setattr(plan_executor, "_dispatch",
                        lambda _si, action, params: calls.append(("do", action, params)) or "ok")
    monkeypatch.setattr(plan_executor, "_rollback_dispatch",
                        lambda _si, action, params: calls.append(("undo", action, params)) or "ok")
    return calls


def _mixed_plan():
    return [
        _step(0, "create_vm", {"vm_name": "new-01"}),
        _step(1, "power_off", {"vm_name": "web-01"}),
        _step(2, "guest_exec", {"vm_name": "web-01", "command": "/bin/rm",
                                "username": "root", "password": "s3cret-pw"}),
        _step(3, "delete_vm", {"vm_name": "old-01",
                               "acknowledge_blast_radius": {"instance_uuid": "x", "disk_count": 0,
                                                            "snapshot_count": 0}}),
    ]


# ─── vm_apply_plan ────────────────────────────────────────────────────────────


def test_apply_bare_call_previews_and_runs_nothing(dispatched):
    pid = _write_plan(_mixed_plan())
    before = (planner._PLANS_DIR / f"{pid}.json").read_text(encoding="utf-8")
    out = plan_tools.vm_apply_plan(pid, target="vc01")
    assert out["action"] == "preview"
    assert dispatched == []
    assert (planner._PLANS_DIR / f"{pid}.json").read_text(encoding="utf-8") == before


def test_apply_preview_lists_every_step_and_counts_destructive(dispatched):
    pid = _write_plan(_mixed_plan())
    br = plan_tools.vm_apply_plan(pid, target="vc01")["blast_radius"]
    assert [(s["index"], s["action"]) for s in br["steps"]] == [
        (0, "create_vm"), (1, "power_off"), (2, "guest_exec"), (3, "delete_vm"),
    ]
    assert br["steps"][3]["target"] == {"vm_name": "old-01"}
    assert br["step_count"] == 4
    assert br["destructive_step_count"] == 3
    assert br["destructive_steps"] == [1, 2, 3]
    assert br["plan_target"] == "vc01"
    assert br["blockers"] == []


def test_apply_preview_never_carries_step_credentials(dispatched):
    pid = _write_plan(_mixed_plan())
    out = plan_tools.vm_apply_plan(pid, target="vc01")
    assert "s3cret-pw" not in repr(out)


def test_apply_preview_lists_every_step_past_sixteen(dispatched):
    pid = _write_plan([_step(i, "power_on", {"vm_name": f"vm-{i}"}) for i in range(20)])
    br = plan_tools.vm_apply_plan(pid, target="vc01")["blast_radius"]
    assert len(br["steps"]) == 20 and br["destructive_step_count"] == 0


def test_apply_confirm_runs_every_step_once_and_reports(dispatched):
    pid = _write_plan(_mixed_plan())
    out = plan_tools.vm_apply_plan(pid, confirm=True, target="vc01")
    assert [c[1] for c in dispatched] == ["create_vm", "power_off", "guest_exec", "delete_vm"]
    assert out["status"] == "completed"
    assert out["blast_radius"]["destructive_step_count"] == 3


def test_apply_delete_step_without_acknowledgement_is_a_blocker(dispatched):
    """Refused before step 0 runs, not at step 3 after the others landed."""
    steps = _mixed_plan()
    steps[3]["params"].pop("acknowledge_blast_radius")
    pid = _write_plan(steps)
    assert plan_tools.vm_apply_plan(pid, target="vc01")["blast_radius"]["blockers"]
    out = plan_tools.vm_apply_plan(pid, confirm=True, target="vc01")
    assert dispatched == []
    assert "Step 3" in out["error"] and "vm_delete" in out["error"]


def test_apply_on_a_different_target_than_the_plan_was_checked_on_refuses(dispatched):
    pid = _write_plan(_mixed_plan(), target="vc01")
    out = plan_tools.vm_apply_plan(pid, confirm=True, target="vc02")
    assert dispatched == []
    assert "target='vc01'" in out["error"]
    out = plan_tools.vm_apply_plan(pid, confirm=True)
    assert dispatched == []
    assert "target='vc01'" in out["error"]


def test_apply_unknown_step_action_refuses(dispatched):
    pid = _write_plan([_step(0, "power_on", {"vm_name": "a"}) | {"action": "format_disk"}])
    out = plan_tools.vm_apply_plan(pid, confirm=True, target="vc01")
    assert dispatched == []
    assert "format_disk" in out["error"]


def test_apply_unknown_plan_is_an_error_on_both_paths(dispatched):
    assert "no-such-plan" in plan_tools.vm_apply_plan("no-such-plan")["error"]
    assert "no-such-plan" in plan_tools.vm_apply_plan("no-such-plan", confirm=True)["error"]


def test_apply_non_pending_plan_is_an_error(dispatched):
    pid = _write_plan(_mixed_plan(), status="failed")
    out = plan_tools.vm_apply_plan(pid, target="vc01")
    assert "expected 'pending'" in out["error"]


def test_every_plan_action_is_classified():
    """A new plan action must be put on one side of the gated line on purpose."""
    from vmware_aiops.ops.plan_gate import (
        DESTRUCTIVE_ACTIONS,
        GATED_ACTIONS,
        NON_DESTRUCTIVE_ACTIONS,
    )

    assert not GATED_ACTIONS & set(NON_DESTRUCTIVE_ACTIONS)
    assert not DESTRUCTIVE_ACTIONS & set(NON_DESTRUCTIVE_ACTIONS)
    assert GATED_ACTIONS | set(NON_DESTRUCTIVE_ACTIONS) == set(planner._ACTION_SCHEMA)


# ─── vm_rollback_plan ─────────────────────────────────────────────────────────


def _failed_plan():
    return [
        _step(0, "create_vm", {"vm_name": "new-01"}, status="success"),
        _step(1, "revert_snapshot", {"vm_name": "web-01", "snapshot_name": "s"}, status="success"),
        _step(2, "power_on", {"vm_name": "web-02"}, status="success"),
        _step(3, "power_off", {"vm_name": "web-03"}, status="failed"),
    ]


def test_rollback_bare_call_previews_and_runs_nothing(dispatched):
    pid = _write_plan(_failed_plan(), status="failed")
    before = (planner._PLANS_DIR / f"{pid}.json").read_text(encoding="utf-8")
    out = plan_tools.vm_rollback_plan(pid, target="vc01")
    assert out["action"] == "preview"
    assert dispatched == []
    assert (planner._PLANS_DIR / f"{pid}.json").read_text(encoding="utf-8") == before


def test_rollback_preview_lists_what_runs_and_what_is_skipped(dispatched):
    pid = _write_plan(_failed_plan(), status="failed")
    br = plan_tools.vm_rollback_plan(pid, target="vc01")["blast_radius"]
    assert [(s["step_index"], s["rollback_action"]) for s in br["would_run"]] == [
        (2, "power_off"), (0, "delete_vm"),
    ]
    assert [(s["step_index"], s["action"]) for s in br["skipped_irreversible"]] == [
        (1, "revert_snapshot"),
    ]
    assert br["would_run_count"] == 2
    assert br["destructive_rollback_count"] == 2
    assert br["deletes_vms"] == ["new-01"]
    assert br["blockers"] == []


def test_rollback_confirm_runs_once_and_reports(dispatched):
    pid = _write_plan(_failed_plan(), status="failed")
    out = plan_tools.vm_rollback_plan(pid, confirm=True, target="vc01")
    assert [c[1] for c in dispatched] == ["power_off", "delete_vm"]
    assert out["status"] == "rolled_back"
    assert out["blast_radius"]["would_run_count"] == 2


def test_rollback_on_a_different_target_refuses(dispatched):
    pid = _write_plan(_failed_plan(), status="failed", target="vc01")
    out = plan_tools.vm_rollback_plan(pid, confirm=True, target="vc02")
    assert dispatched == []
    assert "target='vc01'" in out["error"]


def test_rollback_of_a_pending_plan_is_an_error(dispatched):
    pid = _write_plan(_failed_plan(), status="pending")
    out = plan_tools.vm_rollback_plan(pid, target="vc01")
    assert "rollback only available for 'failed' plans" in out["error"]


# ─── refusals are audited as failures ─────────────────────────────────────────


@pytest.fixture
def audit_rows(monkeypatch):
    rows: list[dict] = []

    class _Recorder:
        def log(self, **kw):
            rows.append(kw)

    monkeypatch.setattr("vmware_policy.guard.get_engine", lambda: _Recorder())
    return rows


def test_refusal_is_audited_as_a_failure(world, audit_rows):
    world.install(_cluster(hosts=[_host("esx-01")]))
    cluster_tools.cluster_delete("lab", confirm=True)
    assert audit_rows and audit_rows[0]["status"] == "error"


def test_preview_is_audited_as_dry_run(world, audit_rows):
    world.install(_cluster())
    cluster_tools.cluster_delete("lab")
    # vmware-policy >= 1.17.0 records a confirm=False preview as "dry_run".
    assert audit_rows and audit_rows[0]["status"] == "dry_run"
