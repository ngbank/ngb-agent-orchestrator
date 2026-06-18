"""Node: cleanup — remove temporary work planner clone directories."""

import click

from orchestrator.shared.repo_setup import cleanup_working_dir
from orchestrator.work_planner.state import CleanupInputState


def cleanup(state: CleanupInputState) -> dict:
    """Remove the temporary working directory created by clone_repo."""
    working_dir = state.get("working_dir")
    if cleanup_working_dir(working_dir):
        click.echo(f"🧹 Cleaned up working directory: {working_dir}")

    return {}
