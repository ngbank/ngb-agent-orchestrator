"""Cleanup node factory shared by planner and executor subgraphs."""

import os
from typing import Any, Sequence

import click

from orchestrator.shared.repo_setup.repo_operations import cleanup_working_dir


def build_cleanup_node(*, temp_file_keys: Sequence[str] = ()):
    """Build a cleanup node.

    Args:
        temp_file_keys: Optional state keys containing temp file paths to unlink
            before working_dir cleanup.
    """

    def _node(state: Any) -> dict:
        for key in temp_file_keys:
            path = state.get(key)
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass

        working_dir = state.get("working_dir")
        if cleanup_working_dir(working_dir):
            click.echo(f"🧹 Cleaned up working directory: {working_dir}")

        return {}

    return _node
