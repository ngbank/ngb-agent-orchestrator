"""Node: clone_repo — clone the planning target repository into a temp workspace."""

import tempfile

import click

from orchestrator.utils import log_path, run_and_tee
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

    working_dir = tempfile.mkdtemp(prefix=f"ngb-plan-{workflow_id}-")
    lp = log_path(workflow_id, "plan", ticket_key=ticket_key)

    clone_url = repo_url
    if repo_url.startswith("https://github.com/") and github_token:
        clone_url = repo_url.replace(
            "https://github.com/",
            f"https://x-access-token:{github_token}@github.com/",
        )

    click.echo(f"📂 Cloning {repo_url} into {working_dir}... (log: {lp})")
    try:
        with open(lp, "a") as log_file:
            log_file.write(f"\n=== git clone {repo_url} ===\n")
            result = run_and_tee(["git", "clone", clone_url, working_dir], log_file)
        if result.returncode != 0:
            raise RuntimeError(f"git clone exited with code {result.returncode}")
    except Exception as exc:  # noqa: BLE001
        error_msg = f"Failed to clone {repo_url}: {exc}"
        click.echo(f"❌ {error_msg}", err=True)
        return {
            "working_dir": working_dir,
            "error": error_msg,
            "failed_node": "clone_repo",
        }

    return {"working_dir": working_dir}
