"""A TTL must not delete the VM that replaces its name after the first lookup."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from vmware_aiops.ops import vm_lifecycle
from vmware_aiops.ops.ttl import TTLEntry
from vmware_aiops.scanner import scheduler


@pytest.mark.parametrize("replacement", [False, True])
def test_ttl_checks_the_vm_used_for_the_delete(monkeypatch, replacement):
    original = MagicMock()
    original.config.instanceUuid = "original-uuid"
    original.runtime.powerState = "poweredOff"
    second = MagicMock() if replacement else original
    second.config.instanceUuid = "replacement-uuid" if replacement else "original-uuid"
    second.runtime.powerState = "poweredOn"
    lookup = MagicMock(side_effect=[original, second])
    monkeypatch.setattr(vm_lifecycle, "find_vm_by_name", lookup)
    monkeypatch.setattr(vm_lifecycle, "_wait_for_task", lambda task: None)
    entry = TTLEntry(vm_name="ttl-vm", expires_at="2026-01-01T00:00:00+00:00",
                     target="lab-vc", instance_uuid="original-uuid")
    monkeypatch.setattr(scheduler, "get_expired_entries", lambda: [entry])
    monkeypatch.setattr(scheduler, "guard", lambda *args, **kwargs: None)
    monkeypatch.setattr(scheduler, "_last_ttl_outcome", {})
    removed = []
    monkeypatch.setattr(scheduler, "remove_entry", removed.append)
    audits = []
    monkeypatch.setattr(scheduler, "_audit_ttl", lambda *args: audits.append(args))

    scheduler._run_ttl_check(SimpleNamespace(connect=lambda target: object()))

    assert lookup.call_count == 2
    if replacement:
        original.PowerOff.assert_not_called()
        original.Destroy_Task.assert_not_called()
        second.PowerOff.assert_not_called()
        second.Destroy_Task.assert_not_called()
        assert audits[0][2] == "error"
    else:
        original.PowerOff.assert_called_once()
        original.Destroy_Task.assert_called_once()
        assert audits[0][2] == "ok"
    assert removed == ["ttl-vm"]
