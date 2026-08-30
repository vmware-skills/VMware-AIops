"""A teaching message the agent never sees is not a teaching message.

``_safe_error`` reduces unrecognised exceptions to ``"<Class>: operation
failed."`` so raw vSphere text cannot leak. The allowlist it checks against was
an enumeration, and an enumeration drifts: two exceptions this skill raises
deliberately were missing from it, so their messages were replaced by their
class names on the way to the agent.

The missing-password error mattered most — this family's most common first-run
failure, whose entire remedy is the env var name it names. An MCP agent
received ``OSError: operation failed.`` and had nothing to act on.
``TaskStillRunning`` was the second: it exists to say "this is NOT a failure,
keep polling", and arrived as ``TaskStillRunning: operation failed.``

The defect was invisible from the CLI, which prints these messages in full, and
invisible to the error-quality eval, which reads the message at the raise site
rather than what survives the wrapper.

So the rule is the inverse of an enumeration: every exception this skill raises
on purpose passes through, and only genuinely unplanned ones are reduced.

The first repair over-corrected: it admitted bare ``OSError``, which is not
"the errors this skill authored" but "every OS-level error there is" —
including ``ssl.SSLCertVerificationError`` (certificate subject and hostname),
``socket.gaierror`` (the name that failed to resolve) and connection errors
carrying a full ``scheme://host:port/path``. ``sanitize`` strips control
characters and truncates; it redacts nothing. So the authored error now has an
authored *type*, ``ConfigError``, and the tests below pin both directions: the
remedy still arrives, and the hostnames still do not.
"""

from __future__ import annotations

import socket
import ssl
from unittest.mock import patch

import pytest

from vmware_aiops.config import CONFIG_FILE, ConfigError, TargetConfig
from vmware_aiops.connection import ConnectionManager
from vmware_aiops.mcp_server._shared import _safe_error
from vmware_aiops.ops.cluster_mgmt import ClusterError, ClusterNotFoundError
from vmware_aiops.ops.datastore_browser import DatastoreBrowseError
from vmware_aiops.ops.guest_ops import GuestOpsError
from vmware_aiops.ops.host_network_mgmt import HostNetworkError
from vmware_aiops.ops.inventory import InventoryError
from vmware_aiops.ops.iscsi_config import HostNotFoundError, ISCSIError
from vmware_aiops.ops.network_mgmt import NetworkError
from vmware_aiops.ops.vm_lifecycle import (
    TaskFailedError,
    TaskStillRunning,
    VMNotFoundError,
)

TEACHING = "VM 'web-99' not found on 'vcenter-prod'. Run vm_list to see available VMs."

ENV_KEY = "VMWARE_VCENTER_PROD_PASSWORD"

#: A hostname of the shape a real TLS/DNS failure quotes back.
LEAKY_HOST = "vc-prod-01.corp.internal"


def test_missing_password_keeps_the_env_var_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """The authored config error — and the whole point of it is the env var name.

    Raised from the real property rather than hand-built. Reconstructing the
    exception here would keep passing if ``config.py`` reverted to a bare
    ``OSError``, which is precisely the drift this file exists to catch.
    """
    monkeypatch.delenv(ENV_KEY, raising=False)
    target = TargetConfig(
        name="vcenter-prod", host="vc.example.com", config_username="svc@vsphere.local"
    )

    with pytest.raises(ConfigError) as caught:
        _ = target.password

    out = _safe_error(caught.value, "vm_list")
    assert ENV_KEY in out
    assert "operation failed" not in out


def test_tls_failure_does_not_hand_the_agent_the_certificate_subject() -> None:
    """Bare OSError in the allowlist passed this through verbatim; sanitize redacts nothing."""
    exc = ssl.SSLCertVerificationError(
        f"[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self signed "
        f"certificate (_ssl.c:1006), subject CN={LEAKY_HOST}"
    )
    assert LEAKY_HOST in str(exc), "fixture must actually carry the hostname"

    out = _safe_error(exc, "vm_power_on")
    assert out == "SSLCertVerificationError: operation failed."
    assert LEAKY_HOST not in out


def test_dns_failure_does_not_hand_the_agent_the_hostname() -> None:
    exc = socket.gaierror(-2, f"Name or service not known: {LEAKY_HOST}")
    assert LEAKY_HOST in str(exc), "fixture must actually carry the hostname"

    out = _safe_error(exc, "vm_list")
    assert out == "gaierror: operation failed."
    assert LEAKY_HOST not in out


@pytest.mark.parametrize(
    "exc_type",
    [
        VMNotFoundError,
        GuestOpsError,
        TaskFailedError,
        ClusterNotFoundError,
        ClusterError,
        InventoryError,
        HostNotFoundError,
        HostNetworkError,
        ISCSIError,
        DatastoreBrowseError,
        NetworkError,
    ],
)
def test_domain_exceptions_keep_their_message(exc_type):
    assert _safe_error(exc_type(TEACHING), "vm_list") == TEACHING


def test_failed_browse_task_keeps_its_remedy_through_the_wrapper() -> None:
    """Driven from the raise site: this one was a ``RuntimeError``, so the
    remedy written into it — deliberately capped so it would survive the
    300-char truncation — was replaced by "RuntimeError: operation failed."
    before it ever reached the agent."""
    from unittest.mock import MagicMock

    from vmware_aiops.ops.datastore_browser import _wait_for_task

    task = MagicMock()
    task.info.state = "error"
    task.info.error.msg = "The object or item referred to could not be found."

    with pytest.raises(DatastoreBrowseError) as caught:
        _wait_for_task(task)

    out = _safe_error(caught.value, "browse_datastore")
    assert "list_all_datastores" in out
    assert "operation failed" not in out


def test_task_still_running_keeps_its_not_a_failure_message():
    """It exists to say "keep polling"; reduced to a class name it says the opposite."""
    out = _safe_error(TaskStillRunning("task-42", 300), "vm_clone")
    assert "NOT a failure" in out
    assert "task-42" in out


@pytest.mark.parametrize("exc_type", [ValueError, FileNotFoundError, KeyError, PermissionError])
def test_validation_errors_still_pass_through(exc_type):
    assert "web-99" in _safe_error(exc_type(TEACHING), "t")


def test_dropped_connection_surfaces_its_hint():
    """The CLI path catches OSError and prints the hint; the MCP path must match."""
    assert "retry" in _safe_error(ConnectionError("Connection lost — retry the operation."), "t")


def test_unplanned_exceptions_are_still_reduced():
    """The redaction this allowlist exists for has to keep working."""
    out = _safe_error(RuntimeError("https://admin:hunter2@vc.internal/api/task-42"), "t")
    assert out == "RuntimeError: operation failed."
    assert "hunter2" not in out


def test_message_is_still_truncated():
    """Length capping is the other half of the guard.

    Asserted against the constant, not a repeated literal. The cap moved 300 ->
    500 because 300 governed *only* authored text — an unplanned exception is
    reduced to a bare type name and never reaches it — and was cutting the
    closing remedy off the longest of those messages. The principle it encodes
    is unchanged: an authored message is bounded, because it interpolates
    inventory names.
    """
    from vmware_aiops.mcp_server._shared import AUTHORED_MESSAGE_CAP

    assert len(_safe_error(VMNotFoundError("x" * 900), "t")) <= AUTHORED_MESSAGE_CAP


# ── the diagnostic bare OSError used to carry, put back deliberately ─────────


@pytest.fixture
def target(monkeypatch: pytest.MonkeyPatch) -> TargetConfig:
    monkeypatch.setenv(ENV_KEY, "irrelevant-to-these-cases")
    return TargetConfig(name="vcenter-prod", host="vc.example.com", config_username="svc")


@pytest.mark.parametrize(
    ("raised", "expected_remedy"),
    [
        (
            ssl.SSLCertVerificationError(f"certificate verify failed, subject CN={LEAKY_HOST}"),
            "verify_ssl: false",
        ),
        (socket.gaierror(-2, f"Name or service not known: {LEAKY_HOST}"), "'host' value"),
        (ConnectionRefusedError(61, f"Connection refused to {LEAKY_HOST}:443"), "is up"),
    ],
    ids=["tls", "dns", "unreachable"],
)
def test_connect_failures_are_translated_rather_than_dropped(
    target, raised, expected_remedy
) -> None:
    """Withholding these types costs the operator the diagnostic, and a
    self-signed certificate is this family's most common connection problem.
    The connection layer replaces each with authored text naming the target and
    the setting to change. The raw exception survives on ``__cause__``, which
    reaches the server log only.
    """
    with patch("pyVim.connect.SmartConnect", side_effect=raised):
        with pytest.raises(OSError) as caught:  # noqa: PT011 — type varies by case
            ConnectionManager._create_connection(target)

    out = _safe_error(caught.value, "vm_power_on")
    assert expected_remedy in out
    assert str(CONFIG_FILE) in out
    assert LEAKY_HOST not in out
    assert len(out) <= 300, "the remedy must survive the sanitize cap"
    assert caught.value.__cause__ is raised, "raw detail must stay reachable for the log"


def test_missing_password_is_not_relabelled_as_a_connection_failure(monkeypatch) -> None:
    """``ConfigError`` is an ``OSError`` subclass, so the handlers that translate
    TLS/DNS/unreachable would happily have swallowed the missing-password error
    and answered it with the wrong remedy. The credentials are therefore read
    *before* the try block, and this pins that ordering: SmartConnect is never
    reached, and the env var name still arrives.
    """
    monkeypatch.delenv(ENV_KEY, raising=False)
    unconfigured = TargetConfig(name="vcenter-prod", host="vc.example.com", config_username="svc")

    with patch("pyVim.connect.SmartConnect", side_effect=AssertionError("must not be reached")):
        with pytest.raises(ConfigError) as caught:
            ConnectionManager._create_connection(unconfigured)

    assert ENV_KEY in _safe_error(caught.value, "vm_power_on")
