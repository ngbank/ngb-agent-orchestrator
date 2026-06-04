"""Node: clone_repo — create workspace and clone the target repository."""

import json
import os
import tempfile

import click

from graph.executor.nodes.resolve_repo import _failure_summary
from graph.executor.state import ExecutionState
from graph.utils import log_path, run_and_tee


def clone_repo(state: ExecutionState) -> dict:
    """Create temp workspace files and clone the target repository.

    Reads:  workflow_id, ticket_key, repo_url, work_plan_data
    Writes: working_dir, work_plan_path, summary_path, reasoning_path, exec_log_path
    On failure: additionally sets execution_summary, exec_error, failed_node.
    """
    workflow_id = state.get("workflow_id")
    ticket_key = state.get("ticket_key", "")
    repo_url = state.get("repo_url", "")
    work_plan_data = state.get("work_plan_data")

    # Create temp workspace — done before the clone so paths are always in state
    # for cleanup even if the clone fails.
    working_dir = tempfile.mkdtemp(prefix=f"ngb-execute-{workflow_id}-")
    lp = log_path(workflow_id or "unknown", "execute", ticket_key=ticket_key)

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

    workspace = {
        "working_dir": working_dir,
        "work_plan_path": work_plan_path,
        "summary_path": summary_path,
        "reasoning_path": reasoning_path,
        "exec_log_path": str(lp),
    }

    # Attempt git clone
    click.echo(f"📂 Cloning {repo_url} into {working_dir}... (log: {lp})")
    try:
        with open(lp, "w") as log_file:
            log_file.write(f"=== git clone {repo_url} ===\n")
            clone_result = run_and_tee(
                ["git", "clone", repo_url, working_dir],
                log_file,
            )
        if clone_result.returncode != 0:
            raise RuntimeError(f"git clone exited with code {clone_result.returncode}")
    except Exception as e:
        click.echo(f"❌ Failed to clone repository: {e}", err=True)
        error_msg = f"Failed to clone {repo_url}: {e}"
        return {
            **workspace,
            "execution_summary": _failure_summary(ticket_key, error_msg),
            "exec_error": error_msg,
            "failed_node": "execute_plan",
        }

    return workspace
