"""Node: prepare_workspace — materialize temp files needed by the execute recipe.

The shared repo_setup subgraph only clones the repository; everything else the
Goose execute recipe needs on disk (the work plan JSON, the output paths it
writes to, and the per-stage log file) is created here so each node stays
single-responsibility and the cleanup node can find the paths via state.
"""

import json
import os
import tempfile

from orchestrator.code_generator.state import (
    PrepareWorkspaceInputState,
    PrepareWorkspaceOutputState,
)
from orchestrator.utils import log_path


def prepare_workspace(state: PrepareWorkspaceInputState) -> PrepareWorkspaceOutputState:
    """Write the work plan to a temp file and reserve summary/reasoning/log paths.

    Reads:  workflow_id, ticket_key, work_plan_data
    Writes: work_plan_path, summary_path, reasoning_path, exec_log_path
    """
    workflow_id = state.get("workflow_id") or "unknown"
    ticket_key = state.get("ticket_key", "")
    work_plan_data = state.get("work_plan_data")

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix="_workplan.json",
        prefix=f"{workflow_id}_",
        delete=False,
    ) as wp_file:
        json.dump(work_plan_data, wp_file, indent=2)
        work_plan_path = wp_file.name

    summary_fd, summary_path = tempfile.mkstemp(
        suffix="_exec_summary.json",
        prefix=f"{workflow_id}_",
    )
    os.close(summary_fd)

    reasoning_fd, reasoning_path = tempfile.mkstemp(
        suffix="_reasoning.txt",
        prefix=f"{workflow_id}_",
    )
    os.close(reasoning_fd)

    exec_log_path = log_path(workflow_id, "execute", ticket_key=ticket_key)

    return {
        "work_plan_path": work_plan_path,
        "summary_path": summary_path,
        "reasoning_path": reasoning_path,
        "exec_log_path": str(exec_log_path),
    }
