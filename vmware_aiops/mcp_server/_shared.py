"""Shared MCP server primitives: the FastMCP instance, connection helper,
error sanitisation, and the ``@tool_errors`` decorator.

Tool modules under ``vmware_aiops/mcp_server/tools/`` import ``mcp`` from here and register
their ``@mcp.tool()`` functions onto it. ``vmware_aiops/mcp_server/server.py`` then imports
those modules and runs the server.

Keep ``Optional[X]`` (never PEP 604 ``X | None``) in any FastMCP-reflected
tool signature — on Python 3.10 with older mcp/pydantic the union is eval'd to
``types.UnionType`` and FastMCP's ``issubclass`` check crashes (踩坑 #33).
"""

import contextvars
import functools
import logging
import ssl
from typing import Any, Callable, Optional

from mcp import types as mcp_types
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


# ---------------------------------------------------------------------------
# Protocol-level failure reporting
# ---------------------------------------------------------------------------

#: Set by :class:`_FrameErrorFastMCP` for the tool call it is dispatching, and
#: appended to by :func:`_mark_call_failed` from inside the tool body. A list is
#: used rather than a plain value because it is the binding, not the variable,
#: that has to survive: the marker is written deep inside the call and read
#: after it returns, and mutating an object both sides already hold works
#: whether FastMCP calls the tool inline (it does today) or moves it to a
#: worker thread with a copied context (it would not carry a rebind back).
#: One binding per dispatch, so concurrent calls cannot mark each other.
_call_failed: contextvars.ContextVar[list[bool] | None] = contextvars.ContextVar(
    "vmware_aiops_mcp_call_failed", default=None
)


def _mark_call_failed() -> None:
    """Declare that the in-flight tool call failed, though it will *return*."""
    sink = _call_failed.get()
    if sink is not None:
        sink.append(True)


def _is_error_envelope(result: Any) -> bool:
    """True if ``result`` is the family's documented error envelope.

    Not every failure travels as an exception: ``apply_plan`` answers an unknown
    plan id with ``{"error": "Plan 'x' not found"}`` and never raises, so
    ``@tool_errors`` sees a perfectly ordinary return.

    A truthy top-level ``error`` key is this family's convention for "the call
    failed", and vmware-policy already audits by exactly that rule — a falsy
    ``error`` is a result reporting that nothing went wrong (``guest_provision``
    returns ``{"error": None}`` on a clean run), and a multi-element list is a
    batch with partial results, which is a successful call.

    This duplicates ``vmware_policy.decorators._returned_failure``, which is
    private and not exported. The duplication is deliberate but not left to
    trust: ``test_failure_envelope_rule_matches_vmware_policys`` pins the two
    against each other over a table of shapes, so they cannot drift into a state
    where the audit row and the protocol frame disagree about the same call
    (形态 #6). The right home for the rule is a public export from vmware-policy,
    which every skill's boundary could then share.
    """
    if isinstance(result, dict):
        return bool(result.get("error"))
    if isinstance(result, list) and len(result) == 1 and isinstance(result[0], dict):
        return bool(result[0].get("error"))
    return False


def _error_frame(converted: Any) -> mcp_types.CallToolResult:
    """Re-wrap an already-converted tool result as an error frame.

    ``converted`` is whatever ``FuncMetadata.convert_result`` produced — a list
    of content blocks, or an ``(unstructured, structured)`` pair for a tool with
    an output schema. Re-wrapping *after* that conversion rather than building a
    ``CallToolResult`` inside the tool is the whole point: the content and
    ``structuredContent`` are then byte-identical to what the same payload
    produced before this change, and nothing here has to know FastMCP's
    ``{"result": ...}`` wrapping convention or which of the 60 tools it applies
    to. Only ``isError`` is new.
    """
    if isinstance(converted, mcp_types.CallToolResult):
        return converted.model_copy(update={"isError": True})
    if isinstance(converted, tuple) and len(converted) == 2:
        unstructured, structured = converted
    else:
        unstructured, structured = converted, None
    return mcp_types.CallToolResult(
        content=list(unstructured), structuredContent=structured, isError=True
    )


class _FrameErrorFastMCP(FastMCP):
    """FastMCP that reports a caught tool failure as ``isError`` on the wire.

    ``@tool_errors`` catches every exception and returns an error payload, so
    the lowlevel server saw an ordinary return and built
    ``CallToolResult(isError=False)``. A client over stdio could not tell a
    missing VM from a powered-on one without parsing prose.

    Raising instead would set the flag — the lowlevel handler turns any
    exception into an error result — but at the cost of the payload: it keeps
    only ``str(exc)``, prefixed with "Error executing tool <name>: ", and drops
    ``structuredContent`` entirely. Returning a ``CallToolResult`` is the other
    shape ``mcp`` 1.28.1 accepts (``convert_result`` passes it through and the
    lowlevel handler returns it verbatim), and it is the one that keeps the
    authored message exactly as it was.

    Both paths were established by reading the installed package and driving it,
    not from memory (踩坑 #36).
    """

    async def call_tool(self, name: str, arguments: dict) -> Any:
        sink: list[bool] = []
        token = _call_failed.set(sink)
        try:
            converted = await super().call_tool(name, arguments)
        finally:
            _call_failed.reset(token)
        if not sink:
            return converted
        return _error_frame(converted)


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
                result = func(*args, **kwargs)
            except Exception as e:  # noqa: BLE001 — sanitised below
                msg = _safe_error(e, name)
                # This wrapper swallows the exception, so @vmware_tool above it
                # sees an ordinary return and would record the call as ``ok``.
                # Declare the failure explicitly — unconditionally, because a
                # single call is easier to keep true than one per shape.
                report_tool_failure(msg)
                # The same declaration, aimed at the protocol frame instead of
                # the audit row. Both have to hear it: the audit trail is for
                # the operator afterwards, ``isError`` is for the agent now.
                _mark_call_failed()
                if shape == "dict":
                    return {"error": msg, "hint": _DOCTOR_HINT}
                if shape == "list":
                    return [{"error": msg, "hint": _DOCTOR_HINT}]
                return f"Error: {msg} {_DOCTOR_HINT}"
            # A tool can also fail by *returning* the family's error envelope
            # without ever raising — the plan guards do exactly that. Policy
            # already reads that envelope when it audits; the frame agrees.
            if _is_error_envelope(result):
                _mark_call_failed()
            return result

        return wrapper

    return decorator


mcp = _FrameErrorFastMCP(
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
        # No env-var read here: load_config resolves the path (explicit arg,
        # then the environment, then the default). This copy was the reason the
        # server and the CLI opened different files — load_config did not look
        # at the variable at all, so only this path honoured it (形态 #6).
        config = load_config()
        _conn_mgr = ConnectionManager(config)
    return _conn_mgr


def _get_connection(target: Optional[str] = None) -> Any:
    """Return a pyVmomi ServiceInstance, lazily initialising the manager."""
    return _ensure_conn_mgr().connect(target)
