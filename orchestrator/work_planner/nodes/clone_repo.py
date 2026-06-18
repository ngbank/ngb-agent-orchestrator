"""Node: clone_repo — clone the planning target repository into a temp workspace."""

import click

from orchestrator.shared.repo_setup import clone_repository
from orchestrator.utils import log_path
from orchestrator.work_planner.state import CloneRepoInputState, CloneRepoOutputState


def clone_repo(state: CloneRepoInputState) -> CloneRepoOutputState:
    """Clone the target repository and persist working_dir in state."""
    workflow_id = state.get("workflow_id") or "unknown"
    ticket_key = state.get("ticket_key", "")
    repo_url = (state.get("repo_url") or "").strip()
    github_token = (state.get("github_token") or "").strip()

    if not repo_url:
        error_msg = "Repository URL is required before cloning"
        click.echo(f"❌ {error_msg}", err=True)
        return {"error": error_msg, "failed_node": "clone_repo"}

    working_dir = None
    lp = log_path(workflow_id, "plan", ticket_key=ticket_key)

    try:
        with open(lp, "a") as log_file:
            log_file.write(f"\n=== git clone {repo_url} ===\n")
            working_dir = clone_repository(
                repo_url,
                github_token,
                temp_prefix=f"ngb-plan-{workflow_id}-",
                log_file=log_file,
            )
        click.echo(f"📂 Cloning {repo_url} into {working_dir}... (log: {lp})")
    except Exception as exc:  # noqa: BLE001
        error_msg = f"Failed to clone {repo_url}: {exc}"
        click.echo(f"❌ {error_msg}", err=True)
        return {
            "working_dir": working_dir,
            "error": error_msg,
            "failed_node": "clone_repo",
        }

    return {"working_dir": working_dir}
