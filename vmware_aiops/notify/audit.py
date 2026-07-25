"""Audit logging for all operations (Plan -> Confirm -> Execute -> Log).

DEPRECATED: This module is superseded by vmware_policy.audit.AuditEngine.
The @vmware_tool decorator now handles audit logging automatically.
This file is retained only because the CLI still imports it directly.
Remove once the CLI migrates to vmware-policy.
"""

from __future__ import annotations

import getpass
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AuditLogger:
    """Writes operation audit entries to a structured log file (JSON Lines format).

    Logs to ``~/.vmware-aiops/audit.log`` by default.  Each entry records
    *what* was done, *where*, *before/after* state, and *who* initiated it.
    """

    def __init__(self, log_file: str = "~/.vmware-aiops/audit.log") -> None:
        self._path = Path(log_file).expanduser()
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # mkdir mode is masked by umask; enforce owner-only explicitly.
        try:
            os.chmod(self._path.parent, 0o700)
        except OSError:
            pass
        self._logger = logging.getLogger("vmware-aiops.audit")

    def log(
        self,
        *,
        target: str,
        operation: str,
        resource: str,
        skill: str = "aiops",
        parameters: dict[str, Any] | None = None,
        before_state: dict[str, Any] | None = None,
        after_state: dict[str, Any] | None = None,
        result: str = "",
        user: str | None = None,
    ) -> None:
        """Append a single audit entry to the log file and emit to console."""
        entry: dict[str, Any] = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "target": target,
            "operation": operation,
            "resource": resource,
            "skill": skill,
            "parameters": parameters or {},
            "before_state": before_state or {},
            "after_state": after_state or {},
            "result": result,
            "user": user or _current_user(),
        }

        existed = self._path.exists()
        with open(self._path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        if not existed:
            try:
                os.chmod(self._path, 0o600)
            except OSError:
                pass

        self._logger.info(
            "[AUDIT] %s %s on %s (%s) -> %s",
            operation,
            resource,
            target,
            skill,
            result,
        )

    def log_query(
        self,
        *,
        target: str,
        resource: str,
        query_type: str,
        skill: str = "monitor",
    ) -> None:
        """Shorthand for read-only query audit (vmware-monitor)."""
        self.log(
            target=target,
            operation="query",
            resource=resource,
            skill=skill,
            parameters={"query_type": query_type},
            result="ok",
        )


def _current_user() -> str:
    """Return the current OS username."""
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"
