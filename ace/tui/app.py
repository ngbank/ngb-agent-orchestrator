"""
ACE TUI entrypoint.

Thin scaffold mirroring ``dispatcher/tui/app.py``. The staging-queue screen
and review actions land starting in Epic 3, tickets 3.4-3.5.
"""

import click


def run_tui() -> None:
    """Entry point for the ``ace-tui`` app; screens land in Epic 3."""
    click.echo("ace-tui: no screens implemented yet")
