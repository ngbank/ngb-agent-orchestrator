"""Node: clone_repo — create workspace and clone the target repository."""

import json
import os
import tempfile

import click

from graph.code_generator.nodes.resolve_repo import _failure_summary
from graph.code_generator.state import CloneRepoInputState, CloneRepoOutputState
from graph.utils import log_path, run_and_tee


def clone_repo(state: CloneRepoInputState) -> CloneRepoOutputState:
    """Create temp workspace files and clone the target repository.

    Reads:  workflow_id, ticket_key, repo_url, work_plan_data, github_token
    Writes: working_dir, work_plan_path, summary_path, reasoning_path, exec_log_path
    On failure: additionally sets execution_summary, exec_error, failed_node.
    """
    workflow_id = state.get("workflow_id")
    ticket_key = state.get("ticket_key", "")
    repo_url = state.get("repo_url", "")
    github_token = state.get("github_token", "")
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

    # Inject GitHub token into HTTPS URL for authentication
    # Format: https://x-access-token:{token}@github.com/owner/repo.git
    if not repo_url.startswith("https://"):
        error_msg = f"Repository URL must be HTTPS format (got {repo_url})"
        click.echo(f"❌ {error_msg}", err=True)
        return {
            "working_dir": working_dir,
            "work_plan_path": work_plan_path,
            "summary_path": summary_path,
            "reasoning_path": reasoning_path,
            "exec_log_path": str(lp),
            "execution_summary": _failure_summary(ticket_key, error_msg),
            "exec_error": error_msg,
            "failed_node": "execute_plan",
        }

    # Extract github.com URL and inject token
    # https://github.com/owner/repo.git → https://x-access-token:{token}@github.com/owner/repo.git
    if github_token:
        clone_url = repo_url.replace(
            "https://github.com/", f"https://x-access-token:{github_token}@github.com/"
        )
    else:
        clone_url = repo_url

    # Attempt git clone
    click.echo(f"📂 Cloning {repo_url} into {working_dir}... (log: {lp})")
    try:
        with open(lp, "w") as log_file:
            log_file.write(f"=== git clone {repo_url} ===\n")
            clone_result = run_and_tee(
                ["git", "clone", clone_url, working_dir],
                log_file,
            )
        if clone_result.returncode != 0:
            raise RuntimeError(f"git clone exited with code {clone_result.returncode}")
    except Exception as e:
        click.echo(f"❌ Failed to clone repository: {e}", err=True)
        error_msg = f"Failed to clone {repo_url}: {e}"
        return {
            "working_dir": working_dir,
            "work_plan_path": work_plan_path,
            "summary_path": summary_path,
            "reasoning_path": reasoning_path,
            "exec_log_path": str(lp),
            "execution_summary": _failure_summary(ticket_key, error_msg),
            "exec_error": error_msg,
            "failed_node": "execute_plan",
        }

    return {
        "working_dir": working_dir,
        "work_plan_path": work_plan_path,
        "summary_path": summary_path,
        "reasoning_path": reasoning_path,
        "exec_log_path": str(lp),
    }
