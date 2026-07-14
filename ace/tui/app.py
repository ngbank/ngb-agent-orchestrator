"""
ACE TUI entrypoint.

Thin scaffold mirroring ``dispatcher/tui/app.py``. The staging-queue screen
and review actions will land as the review pipeline is implemented.
"""

import click


def run_tui() -> None:
    """Entry point for the ``ace-tui`` app; screens not yet implemented."""
    click.echo("ace-tui: no screens implemented yet")
