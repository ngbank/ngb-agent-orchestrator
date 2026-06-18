"""Node: resolve_repo — resolve repository URL for work planning."""

import click

from mcp_server.server import get_repo_for_project
from orchestrator.work_planner.state import ResolveRepoInputState, ResolveRepoOutputState


def resolve_repo(state: ResolveRepoInputState) -> ResolveRepoOutputState:
    """Resolve the repository URL for this workflow.

    Priority order:
    1. Explicit repo_url already present in state.
    2. Project mapping lookup via ticket key prefix.
    """
    existing_repo_url = (state.get("repo_url") or "").strip()
    if existing_repo_url:
        click.echo(f"🔗 Using provided repository URL: {existing_repo_url}")
        return {"repo_url": existing_repo_url}

    ticket_key = state.get("ticket_key", "")
    project_key = ticket_key.split("-")[0].upper() if ticket_key else ""
    try:
        repo_url = get_repo_for_project(project_key)
        click.echo(f"🔗 Resolved repository URL for {project_key}: {repo_url}")
        return {"repo_url": repo_url}
    except ValueError as exc:
        error_msg = str(exc)
        click.echo(f"❌ {error_msg}", err=True)
        return {"error": error_msg, "failed_node": "resolve_repo"}
