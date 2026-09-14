"""Health checks: alarms, events, hardware status, services."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from pyVmomi import vim
from vmware_monitor.ops.health import query_events
from vmware_policy import sanitize

from vmware_aiops.ops.inventory import _collect, _collect_object

if TYPE_CHECKING:
    from pyVmomi.vim import ServiceInstance

# Event types by severity
CRITICAL_EVENTS = {
    "VmFailedToPowerOnEvent",
    "HostConnectionLostEvent",
    "HostShutdownEvent",
    "VmDiskFailedEvent",
    "DasHostFailedEvent",
    "DatastoreRemovedOnHostEvent",
}

WARNING_EVENTS = {
    "VmFailoverFailed",
    "DrsVmMigratedEvent",
    "DrsSoftRuleViolationEvent",
    "VmFailedToRebootGuestEvent",
    "DVPortgroupReconfiguredEvent",
    "VmGuestShutdownEvent",
    "HostIpChangedEvent",
    "BadUsernameSessionEvent",
}

INFO_EVENTS = {
    "VmPoweredOnEvent",
    "VmPoweredOffEvent",
    "VmMigratedEvent",
    "VmReconfiguredEvent",
    "UserLoginSessionEvent",
    "UserLogoutSessionEvent",
    "VmCreatedEvent",
    "VmRemovedEvent",
    "VmClonedEvent",
}

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


def get_active_alarms(si: ServiceInstance) -> list[dict]:
    """Get all active/triggered alarms across the inventory."""
    content = si.RetrieveContent()
    results = []

    def _emit(alarm_states) -> None:
        for alarm_state in alarm_states or []:
            severity = str(alarm_state.overallStatus)
            severity_map = {"red": "critical", "yellow": "warning", "green": "info"}
            results.append({
                "severity": severity_map.get(severity, severity),
                "alarm_name": sanitize(alarm_state.alarm.info.name),
                "entity_name": sanitize(alarm_state.entity.name),
                "entity_type": type(alarm_state.entity).__name__,
                "time": str(alarm_state.time),
                "acknowledged": getattr(alarm_state, "acknowledged", False),
            })

    # Root folder's triggeredAlarmState aggregates every descendant alarm;
    # fetched in one call rather than a lazy read.
    root_props = _collect_object(
        si, content.rootFolder, vim.Folder, ["triggeredAlarmState"]
    )
    _emit(root_props.get("triggeredAlarmState"))

    # Also check datacenters, clusters, hosts — one batched PropertyCollector
    # call per type instead of touching triggeredAlarmState per entity.
    container_types = [vim.Datacenter, vim.ClusterComputeResource, vim.HostSystem]
    for obj_type in container_types:
        for _obj, props in _collect(si, [obj_type], ["triggeredAlarmState"]):
            _emit(props.get("triggeredAlarmState"))

    # Deduplicate by alarm + entity
    seen = set()
    unique = []
    for a in results:
        key = (a["alarm_name"], a["entity_name"])
        if key not in seen:
            seen.add(key)
            unique.append(a)

    return sorted(unique, key=lambda x: SEVERITY_ORDER.get(x["severity"], 9))


def get_recent_events(
    si: ServiceInstance,
    hours: int = 24,
    severity: str = "warning",
) -> list[dict]:
    """Get recent events filtered by severity."""
    content = si.RetrieveContent()
    event_mgr = content.eventManager

    now = datetime.now(tz=timezone.utc)
    begin = now - timedelta(hours=hours)

    filter_spec = vim.event.EventFilterSpec(
        time=vim.event.EventFilterSpec.ByTime(beginTime=begin, endTime=now)
    )

    # Not QueryEvents: on vCenter it returns only the OLDEST 1000 events in the
    # window (measured 2026-09-14), so a busy day hid its latest hours. The shared
    # read walks an event history collector newest first.
    events = query_events(event_mgr, filter_spec)
    min_level = SEVERITY_ORDER.get(severity, 1)

    results = []
    for event in events:
        event_type = type(event).__name__
        if event_type in CRITICAL_EVENTS:
            sev = "critical"
        elif event_type in WARNING_EVENTS:
            sev = "warning"
        elif event_type in INFO_EVENTS:
            sev = "info"
        else:
            sev = "info"

        if SEVERITY_ORDER.get(sev, 2) > min_level:
            continue

        results.append({
            "severity": sev,
            "event_type": event_type,
            "message": sanitize(event.fullFormattedMessage or str(event), max_len=1000),
            "time": str(event.createdTime),
            "username": event.userName if hasattr(event, "userName") else "N/A",
        })

    return sorted(results, key=lambda x: x["time"], reverse=True)


def get_host_hardware_status(si: ServiceInstance) -> list[dict]:
    """Get hardware sensor status for all hosts."""
    results = []
    for _obj, props in _collect(
        si, [vim.HostSystem], ["name", "runtime.healthSystemRuntime"]
    ):
        runtime_health = props.get("runtime.healthSystemRuntime")
        if not runtime_health or not runtime_health.systemHealthInfo:
            continue
        host_name = props.get("name", "")
        for sensor in runtime_health.systemHealthInfo.numericSensorInfo:
            # Health (green/yellow/red) lives in healthState.key;
            # sensorType is the category (temperature/voltage/fan...).
            health = getattr(sensor, "healthState", None)
            status = str(health.key) if health is not None else "unknown"
            results.append({
                "host": sanitize(host_name),
                "sensor_name": sanitize(sensor.name),
                "type": str(getattr(sensor, "sensorType", "unknown")),
                "reading": sensor.currentReading,
                "unit": sensor.baseUnits,
                "status": status,
            })
    return results


def get_host_services(si: ServiceInstance, host_name: str | None = None) -> list[dict]:
    """Get service status for hosts."""
    results = []
    for _obj, props in _collect(
        si, [vim.HostSystem], ["name", "configManager.serviceSystem"]
    ):
        name = props.get("name", "")
        if host_name and name != host_name:
            continue
        svc_system = props.get("configManager.serviceSystem")
        if not svc_system:
            continue
        for svc in svc_system.serviceInfo.service:
            results.append({
                "host": sanitize(name),
                "service": svc.key,
                "label": sanitize(svc.label),
                "running": svc.running,
                "policy": svc.policy,
            })
    return results
