"""Node: cleanup — delete temp files and the cloned working directory.

This node always runs, regardless of success or failure, ensuring no temp
directories are leaked.
"""

import os

import click

from orchestrator.code_generator.state import CleanupInputState
from orchestrator.shared.repo_setup import cleanup_working_dir


def cleanup(state: CleanupInputState) -> dict:
    """Remove temp files and the working clone created by clone_repo.

    Reads:  working_dir, work_plan_path, summary_path, reasoning_path
    Writes: nothing
    """
    for path in (
        state.get("work_plan_path"),
        state.get("summary_path"),
        state.get("reasoning_path"),
    ):
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass

    working_dir = state.get("working_dir")
    if cleanup_working_dir(working_dir):
        click.echo(f"🧹 Cleaned up working directory: {working_dir}")

    return {}
