"""A real ``vim.DiagnosticManager`` over a stub that only answers.

pyVmomi checks every argument of a managed-method call before handing it to
the stub: the keyword must be one of BrowseDiagnosticLog's parameters (host,
key, start, lines) and each value must have the VMODL type. The fakes these
tests used before took ``**kwargs``, so a misspelled keyword (``line=`` for
``lines=``) or a float line number would have passed. Here the checking is
pyVmomi's own; the stub records what reached it and plays a log file back.

The stub doubles as a lazy-read guard: a ``vim.HostSystem`` built on it raises
if anything reads one of its properties (every host property must come from
the batched PropertyCollector call).
"""

from __future__ import annotations

from pyVmomi import vim

PROBE_START = 999999999


class LogStub:
    """Plays back ``logs[key]`` (a list of lines; line N is ``logs[key][N-1]``).

    Tests change ``logs`` between cycles: append lines to make a log grow,
    replace the list with a shorter one to make it rotate. ``fail`` maps a log
    key to the exception every read of that log raises.
    """

    def __init__(
        self, logs: dict[str, list[str]], fail: dict[str, Exception] | None = None
    ) -> None:
        self.logs = logs
        self.fail = dict(fail or {})
        self.calls: list[dict] = []

    def InvokeMethod(self, mo, info, args):  # noqa: N802 - pyVmomi stub contract
        call = {param.name: arg for param, arg in zip(info.params, args)}
        self.calls.append(call)
        if call["key"] in self.fail:
            raise self.fail[call["key"]]
        text = self.logs[call["key"]]
        total = len(text)
        start = call["start"] or 1
        count = call["lines"] if call["lines"] is not None else total
        chunk = text[start - 1:start - 1 + count] if start <= total else []
        line_end = start + len(chunk) - 1 if chunk else total
        return vim.DiagnosticManager.LogHeader(lineStart=start, lineEnd=line_end, lineText=chunk)

    def InvokeAccessor(self, mo, info):  # noqa: N802 - pyVmomi stub contract
        raise AssertionError(f"lazy property read '{info.name}' on {mo}")

    def reads(self, key: str | None = None) -> list[dict]:
        """The calls that read text (not the end-of-log probes)."""
        return [c for c in self.calls
                if c["start"] != PROBE_START and (key is None or c["key"] == key)]


def diag_manager(stub: LogStub) -> vim.DiagnosticManager:
    return vim.DiagnosticManager("DiagMgr", stub)


def host(moid: str, stub: LogStub) -> vim.HostSystem:
    return vim.HostSystem(moid, stub)


def numbered(count: int, first: int = 1, text: str = "routine line") -> list[str]:
    """``count`` harmless lines, each carrying its line number."""
    return [f"{text} {n}" for n in range(first, first + count)]
