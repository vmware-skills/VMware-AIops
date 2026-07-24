"""Structured logging for scan results."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path


class ScanLogger:
    """Writes scan issues to a structured log file (JSON Lines format)."""

    def __init__(self, log_file: str) -> None:
        self._path = Path(log_file).expanduser()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._logger = logging.getLogger("vmware-aiops.scan")

    def log_issue(self, issue: dict) -> None:
        """Append a single issue to the log file and emit to console."""
        entry = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            **issue,
        }

        # Append to JSONL file
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        # Also log to console
        level = {
            "critical": logging.CRITICAL,
            "warning": logging.WARNING,
            "info": logging.INFO,
        }.get(issue.get("severity", "info"), logging.INFO)

        self._logger.log(
            level,
            "[%s] %s | %s",
            issue.get("severity", "?").upper(),
            issue.get("source", "?"),
            issue.get("message", ""),
        )
