"""Regression — alarm / health / log-scan scale via PropertyCollector.

Same bug class as GitHub issue #31 (inventory), audited across the ops layer:
functions that walked a ``CreateContainerView`` and then touched pyVmomi *lazy*
properties per object (``entity.name``, ``entity.triggeredAlarmState``,
``host.runtime.healthSystemRuntime``, ``host.configManager.*`` …). Each lazy read
is a separate SOAP round-trip, so on inventories with thousands of hosts/VMs the
walk is tens of thousands of round-trips.

Fix locked here: every enumeration fetches its properties in one batched
``PropertyCollector.RetrievePropertiesEx`` call (paged via continuation tokens).
The fake managed objects raise on ANY attribute access, so a regression to
per-object lazy reads fails loudly instead of silently going slow.

Covers (at minimum, per the audit):
  * alarm_mgmt._find_triggered_alarm
  * health.get_active_alarms
plus health.get_host_hardware_status / get_host_services and
scanner.log_scanner.scan_host_logs.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pyVmomi import vim

from tests.eval.regression._diag_fakes import LogStub, diag_manager, host
from tests.eval.regression._pc_fakes import NoLazyMO, make_si
from vmware_aiops.ops import alarm_mgmt
from vmware_aiops.ops.health import (
    get_active_alarms,
    get_host_hardware_status,
    get_host_services,
)
from vmware_aiops.scanner.log_scanner import scan_host_logs


def _alarm_state(alarm_name: str, entity_name: str, status: str = "red"):
    entity = SimpleNamespace(name=entity_name)
    return SimpleNamespace(
        overallStatus=status,
        alarm=SimpleNamespace(info=SimpleNamespace(name=alarm_name)),
        entity=entity,
        time="2026-07-02 00:00:00",
        acknowledged=False,
    )


# ---------------------------------------------------------------------------
# alarm_mgmt._find_triggered_alarm
# ---------------------------------------------------------------------------


def test_find_triggered_alarm_batched_no_lazy_reads():
    """Match is found via batched props; entity objects are never read lazily."""
    match_state = _alarm_state("Host CPU usage", "esxi-7")
    fixtures = {
        vim.VirtualMachine: [
            (NoLazyMO("vm:web-01"), {"name": "web-01", "triggeredAlarmState": []}),
        ],
        vim.HostSystem: [
            (NoLazyMO("host:esxi-1"), {"name": "esxi-1", "triggeredAlarmState": []}),
            (
                NoLazyMO("host:esxi-7"),
                {"name": "esxi-7", "triggeredAlarmState": [match_state]},
            ),
        ],
        vim.ClusterComputeResource: [],
        vim.Datacenter: [],
        vim.Datastore: [],
    }
    si = make_si(fixtures)
    entity, state = alarm_mgmt._find_triggered_alarm(si, "esxi-7", "Host CPU usage")
    assert state is match_state
    assert object.__getattribute__(entity, "_label") == "host:esxi-7"


def test_find_triggered_alarm_not_found_raises_and_destroys_each_view_once():
    """No match -> all five types collected, each container view destroyed once."""
    fixtures = {
        t: [(NoLazyMO(f"{t.__name__}:a"), {"name": "a", "triggeredAlarmState": []})]
        for t in (
            vim.VirtualMachine,
            vim.HostSystem,
            vim.ClusterComputeResource,
            vim.Datacenter,
            vim.Datastore,
        )
    }
    si = make_si(fixtures)
    with pytest.raises(ValueError, match="not found"):
        alarm_mgmt._find_triggered_alarm(si, "a", "Nonexistent Alarm")
    assert len(si.views) == 5, "one container view per searched type"
    for stub in si.views:
        assert stub.calls == 1, f"view destroyed {stub.calls}x, expected exactly 1"


# ---------------------------------------------------------------------------
# health.get_active_alarms
# ---------------------------------------------------------------------------


def test_get_active_alarms_batched_and_deduped():
    """Root-folder aggregation + host propagation collapse to one alarm; the
    per-type walk is batched and host objects are never read lazily."""
    state = _alarm_state("Host CPU usage", "esxi-1", status="red")
    fixtures = {
        vim.Folder: [(NoLazyMO("root"), {"triggeredAlarmState": [state]})],
        vim.Datacenter: [],
        vim.ClusterComputeResource: [],
        vim.HostSystem: [
            (NoLazyMO("host:esxi-1"), {"triggeredAlarmState": [state]}),
        ],
    }
    alarms = get_active_alarms(make_si(fixtures))
    assert len(alarms) == 1, "propagated + aggregated alarm must be deduplicated"
    assert alarms[0]["alarm_name"] == "Host CPU usage"
    assert alarms[0]["entity_name"] == "esxi-1"
    assert alarms[0]["severity"] == "critical"


def test_get_active_alarms_severity_mapping_and_ordering():
    warn = _alarm_state("Datastore usage", "ds-01", status="yellow")
    crit = _alarm_state("Host memory", "esxi-2", status="red")
    fixtures = {
        vim.Folder: [(NoLazyMO("root"), {"triggeredAlarmState": [warn, crit]})],
        vim.Datacenter: [],
        vim.ClusterComputeResource: [],
        vim.HostSystem: [],
    }
    alarms = get_active_alarms(make_si(fixtures))
    assert [a["severity"] for a in alarms] == ["critical", "warning"]


# ---------------------------------------------------------------------------
# health.get_host_hardware_status
# ---------------------------------------------------------------------------


def test_get_host_hardware_status_batched():
    sensor = SimpleNamespace(
        name="CPU1 Temp",
        sensorType="temperature",
        healthState=SimpleNamespace(key="green"),
        currentReading=4500,
        baseUnits="C",
    )
    runtime_health = SimpleNamespace(
        systemHealthInfo=SimpleNamespace(numericSensorInfo=[sensor])
    )
    fixtures = {
        vim.HostSystem: [
            (
                NoLazyMO("host:esxi-1"),
                {"name": "esxi-1", "runtime.healthSystemRuntime": runtime_health},
            )
        ]
    }
    rows = get_host_hardware_status(make_si(fixtures))
    assert len(rows) == 1
    assert rows[0]["host"] == "esxi-1"
    assert rows[0]["status"] == "green"       # from healthState.key
    assert rows[0]["type"] == "temperature"   # sensorType kept as 'type'


# ---------------------------------------------------------------------------
# health.get_host_services — filter applied before touching serviceSystem
# ---------------------------------------------------------------------------


def test_get_host_services_filter_before_touching_service_system():
    svc = SimpleNamespace(
        key="TSM-SSH", label="SSH", running=True, policy="on"
    )
    svc_system = SimpleNamespace(
        serviceInfo=SimpleNamespace(service=[svc])
    )
    fixtures = {
        vim.HostSystem: [
            # Filtered-out host: serviceSystem is a NoLazyMO -> reading it (i.e.
            # not honoring the host_name filter first) would blow up.
            (
                NoLazyMO("host:other"),
                {"name": "other", "configManager.serviceSystem": NoLazyMO("svc:other")},
            ),
            (
                NoLazyMO("host:esxi-1"),
                {"name": "esxi-1", "configManager.serviceSystem": svc_system},
            ),
        ]
    }
    rows = get_host_services(make_si(fixtures), host_name="esxi-1")
    assert len(rows) == 1
    assert rows[0]["host"] == "esxi-1"
    assert rows[0]["service"] == "TSM-SSH"
    assert rows[0]["running"] is True


# ---------------------------------------------------------------------------
# scanner.log_scanner.scan_host_logs — narrow to host_name before RPCs
# ---------------------------------------------------------------------------


def test_scan_host_logs_narrows_to_host_before_browsing():
    # content.diagnosticManager is a real vim.DiagnosticManager, so pyVmomi
    # checks every BrowseDiagnosticLog argument (an earlier fake here took the
    # arguments it happened to be given, and before that put the method on the
    # host's diagnosticSystem, which has none — see
    # test_host_log_scan_reads_real_logs.py). The wanted host is a real
    # vim.HostSystem on the same stub, which raises on any lazy property read.
    stub = LogStub({"hostd": ["all good", "ERROR: disk failure detected"]})
    wanted = host("host-esxi-1", stub)
    fixtures = {
        vim.HostSystem: [
            # Filtered-out host: never named in a BrowseDiagnosticLog call.
            (NoLazyMO("host:other"), {"name": "other"}),
            (wanted, {"name": "esxi-1"}),
        ]
    }
    si = make_si(fixtures)
    content = si.RetrieveContent()
    content.diagnosticManager = diag_manager(stub)
    content.about = SimpleNamespace(apiType="VirtualCenter")
    issues = scan_host_logs(si, host_name="esxi-1", log_keys=("hostd",))
    hosts = [c["host"] for c in stub.calls]
    assert hosts and all(h is wanted for h in hosts), "a filtered-out host was browsed"
    assert len(issues) == 1
    assert issues[0]["entity"] == "esxi-1"
    assert "esxi-1" in issues[0]["message"]
    assert issues[0]["severity"] == "warning"
