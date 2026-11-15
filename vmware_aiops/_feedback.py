"""Point to the public setup-feedback form after a first useful result.

Prints a link only. Nothing is collected or sent by the CLI.
"""

from __future__ import annotations

from rich.console import Console

SETUP_FEEDBACK_URL = (
    "https://github.com/vmware-skills/marketplace/issues/new?template=setup-feedback.yml"
)


def print_feedback_hint(console: Console, next_step: str | None = None) -> None:
    """Print an optional next step and the feedback form link, dimmed."""
    if next_step:
        console.print(f"[dim]Next: {next_step}[/]", soft_wrap=True)
    console.print(
        "[dim]Useful result, or stuck? Tell us (nothing is sent automatically): "
        f"{SETUP_FEEDBACK_URL}[/]",
        soft_wrap=True,
        highlight=False,
    )


def reached_a_target(data: dict) -> bool:
    """True when the attention view read at least one configured target."""
    return bool(data.get("targets"))
