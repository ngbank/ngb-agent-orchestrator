"""Node: cleanup — remove temporary work planner clone directories."""

import os
import shutil

import click

from orchestrator.work_planner.state import CleanupInputState


def cleanup(state: CleanupInputState) -> dict:
    """Remove the temporary working directory created by clone_repo."""
    working_dir = state.get("working_dir")
    if working_dir and os.path.isdir(working_dir):
        shutil.rmtree(working_dir, ignore_errors=True)
        click.echo(f"🧹 Cleaned up working directory: {working_dir}")

    return {}
