"""Shared MCP server primitives: the FastMCP instance, connection helper,
error sanitisation, and the ``@tool_errors`` decorator.

Tool modules under ``vmware_aiops/mcp_server/tools/`` import ``mcp`` from here and register
their ``@mcp.tool()`` functions onto it. ``vmware_aiops/mcp_server/server.py`` then imports
those modules and runs the server.

Keep ``Optional[X]`` (never PEP 604 ``X | None``) in any FastMCP-reflected
tool signature — on Python 3.10 with older mcp/pydantic the union is eval'd to
``types.UnionType`` and FastMCP's ``issubclass`` check crashes (踩坑 #33).
"""

import functools
import logging
import os
import ssl
from pathlib import Path
from typing import Any, Callable, Optional

from mcp.server.fastmcp import FastMCP
from vmware_policy import report_tool_failure, sanitize

from vmware_aiops import __version__
from vmware_aiops.config import ConfigError, load_config
from vmware_aiops.connection import ConnectionManager
from vmware_aiops.ops.cluster_mgmt import ClusterError, ClusterNotFoundError
from vmware_aiops.ops.datastore_browser import DatastoreBrowseError
from vmware_aiops.ops.guest_ops import GuestOpsError
from vmware_aiops.ops.host_network_mgmt import HostNetworkError
from vmware_aiops.ops.inventory import InventoryError
from vmware_aiops.ops.iscsi_config import HostNotFoundError, ISCSIError
from vmware_aiops.ops.network_mgmt import NetworkError
from vmware_aiops.ops.vm_lifecycle import TaskFailedError, TaskStillRunning, VMNotFoundError

logger = logging.getLogger(__name__)

_DOCTOR_HINT = "Run 'vmware-aiops doctor' to verify connectivity and credentials."


def _safe_error(exc: Exception, tool: str) -> str:
    """Return an agent-safe error string; log full detail server-side only.

    Raw exception text can carry vSphere response bodies, internal paths, or
    host:port pairs. Full traceback goes to the server log; the agent sees only
    a control-char-stripped, length-capped message.

    The rule is a property, not a list: every exception this skill raises on
    purpose passes through, and only genuinely unplanned ones are reduced. The
    enumeration below is the mechanical expression of that rule, and it drifts —
    each domain exception added under ``vmware_aiops.ops`` without a matching
    entry here loses its teaching message on the way to the agent, which is the
    exact dead end those messages exist to remove.

    The missing-password error — this family's most common first-run failure,
    whose entire remedy is the env var name it carries — arrives as
    ``ConfigError``, a narrow ``OSError`` subclass ``config.py`` raises on
    purpose. Bare ``OSError`` is deliberately *not* here: it would also admit
    ``ssl.SSLCertVerificationError`` (certificate subject and hostname),
    ``socket.gaierror`` (the name that failed to resolve) and
    ``requests``-style connection errors (the full ``scheme://host:port/path``),
    none of which are authored text. ``sanitize`` strips control characters and
    truncates; it redacts nothing, so breadth here is exposure. TLS errors are
    rejected ahead of the list because they also subclass ``ValueError`` and an
    allowlist therefore cannot exclude them — see the comment below.
    ``FileNotFoundError``, ``PermissionError``, ``TimeoutError`` and
    ``ConnectionError`` stay because each is raised deliberately somewhere in
    this skill with a remedy in the message.

    Anything else is reduced to its type — an unplanned exception's text was
    written for a developer reading a traceback, not for an agent choosing what
    to do next, and it is the one that can carry credentials.
    """
    logger.error("Tool %s failed", tool, exc_info=True)
    # Checked before the allowlist, not by removal from it: an allowlist cannot
    # express this. ``ssl.SSLCertVerificationError`` inherits from OSError *and*
    # ValueError, so dropping bare OSError does not stop it — it still matches
    # the ValueError entry, which predates that change and carries real
    # authored messages. It quotes the certificate subject and the hostname.
    # (``socket.gaierror`` needs no entry: OSError is its only base, so the
    # allowlist already reduces it. Adding it here would guard nothing.)
    if isinstance(exc, ssl.SSLError):
        return f"{type(exc).__name__}: operation failed."
    _passthrough = (
        ValueError,
        FileNotFoundError,
        KeyError,
        PermissionError,
        TimeoutError,
        ConnectionError,
        ConfigError,
        VMNotFoundError,
        GuestOpsError,
        TaskFailedError,
        TaskStillRunning,
        ClusterNotFoundError,
        ClusterError,
        InventoryError,
        HostNotFoundError,
        HostNetworkError,
        ISCSIError,
        DatastoreBrowseError,
        NetworkError,
    )
    if isinstance(exc, _passthrough):
        return sanitize(str(exc), 300)
    return f"{type(exc).__name__}: operation failed."


def tool_errors(shape: str = "str") -> Callable:
    """Wrap a tool body in the canonical try/except → ``_safe_error`` pattern.

    Collapses the ~41 near-identical error blocks. Behaviour is byte-for-byte
    identical to the inline handlers it replaces — the only difference is the
    error payload shape, selected per the tool's declared return type:

    * ``"str"``  → ``f"Error: {msg} {hint}"``
    * ``"dict"`` → ``{"error": msg, "hint": hint}``
    * ``"list"`` → ``[{"error": msg, "hint": hint}]``

    The decorated tool name passed to ``_safe_error`` is ``func.__name__``,
    matching the literal names used by the original inline blocks.

    Place this *between* ``@vmware_tool`` and the function so the audit
    decorator and FastMCP still see the original signature (preserved via
    ``functools.wraps``); the wrapper catches exceptions exactly where the
    inline ``try/except`` did, so ``@vmware_tool`` never observes them.

    Because it never observes them, the failure has to be *declared*:
    ``report_tool_failure`` runs before the error payload is returned, inside
    the ``@vmware_tool`` call still in flight. Without it a caught failure was
    audited ``status=ok``, told the circuit breaker ``success=True``, and — for
    the writes that carry an ``undo`` descriptor — recorded a token offering to
    reverse a change that never landed.
    """

    def decorator(func: Callable) -> Callable:
        name = func.__name__

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except Exception as e:  # noqa: BLE001 — sanitised below
                msg = _safe_error(e, name)
                # This wrapper swallows the exception, so @vmware_tool above it
                # sees an ordinary return and would record the call as ``ok``.
                # Declare the failure explicitly — unconditionally, because a
                # single call is easier to keep true than one per shape.
                report_tool_failure(msg)
                if shape == "dict":
                    return {"error": msg, "hint": _DOCTOR_HINT}
                if shape == "list":
                    return [{"error": msg, "hint": _DOCTOR_HINT}]
                return f"Error: {msg} {_DOCTOR_HINT}"

        return wrapper

    return decorator


mcp = FastMCP(
    "vmware-aiops",
    instructions=(
        "VMware vCenter/ESXi VM lifecycle and deployment operations. "
        "Manage VM power state, deploy VMs (OVA/template/clone/batch), "
        "browse datastores, manage clusters, execute guest commands, "
        "and plan multi-step operations. "
        "For read-only monitoring (inventory/alarms/events/VM info), "
        "use vmware-monitor. For storage/iSCSI/vSAN, use vmware-storage. "
        "For Tanzu Kubernetes, use vmware-vks."
    ),
)

# FastMCP takes no version argument and leaves the lowlevel server's at
# None, which makes `initialize` answer with the MCP SDK's version rather
# than ours. Set it so a client can tell which release it is talking to.
mcp._mcp_server.version = __version__

# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

_conn_mgr: Optional[ConnectionManager] = None


def _ensure_conn_mgr() -> ConnectionManager:
    """Lazily build the shared ConnectionManager (does not connect anything)."""
    global _conn_mgr  # noqa: PLW0603
    if _conn_mgr is None:
        config_path_str = os.environ.get("VMWARE_AIOPS_CONFIG")
        config_path = Path(config_path_str) if config_path_str else None
        config = load_config(config_path)
        _conn_mgr = ConnectionManager(config)
    return _conn_mgr


def _get_connection(target: Optional[str] = None) -> Any:
    """Return a pyVmomi ServiceInstance, lazily initialising the manager."""
    return _ensure_conn_mgr().connect(target)
