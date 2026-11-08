"""Webhook notification sender."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import httpx

logger = logging.getLogger("vmware-aiops.webhook")


class WebhookNotifier:
    """Sends scan issues to a generic webhook endpoint.

    Compatible with Slack incoming webhooks, Discord webhooks,
    or any HTTP endpoint accepting JSON POST.
    """

    def __init__(self, url: str, timeout: int = 10) -> None:
        self._url = url
        self._timeout = timeout

    def send(self, issues: list[dict]) -> bool:
        """Send issues to webhook. Returns True on success."""
        if not self._url:
            return False

        critical = [i for i in issues if i["severity"] == "critical"]
        warning = [i for i in issues if i["severity"] == "warning"]

        payload = {
            "source": "vmware-aiops",
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "summary": (
                f"VMware AIops: {len(critical)} critical, "
                f"{len(warning)} warning issue(s)"
            ),
            "issues": issues,
            # Slack-compatible text field
            "text": _format_slack_text(issues),
        }

        try:
            response = httpx.post(
                self._url,
                content=json.dumps(payload, ensure_ascii=False),
                headers={"Content-Type": "application/json"},
                timeout=self._timeout,
            )
            if response.status_code < 300:
                logger.info("Webhook sent successfully (%d issues)", len(issues))
                return True
            logger.warning("Webhook returned HTTP %d", response.status_code)
            return False
        except httpx.HTTPError as e:
            logger.error("Webhook failed (%s)", type(e).__name__)
            return False


def _format_slack_text(issues: list[dict]) -> str:
    """Format issues as Slack-compatible text."""
    lines = ["*VMware AIops Scanner Alert*\n"]
    for issue in issues[:20]:  # Cap at 20 to avoid message limits
        icon = ":red_circle:" if issue["severity"] == "critical" else ":warning:"
        lines.append(f"{icon} `{issue.get('entity', 'N/A')}` {issue['message']}")
    if len(issues) > 20:
        lines.append(f"\n... and {len(issues) - 20} more")
    return "\n".join(lines)
