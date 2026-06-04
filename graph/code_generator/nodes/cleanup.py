"""Node: cleanup — delete temp files and the cloned working directory.

This node always runs, regardless of success or failure, ensuring no temp
directories are leaked.
"""

import os
import shutil

import click

from graph.code_generator.state import CodeGeneratorState


def cleanup(state: CodeGeneratorState) -> dict:
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
    if working_dir and os.path.isdir(working_dir):
        shutil.rmtree(working_dir, ignore_errors=True)
        click.echo(f"🧹 Cleaned up working directory: {working_dir}")

    return {}
