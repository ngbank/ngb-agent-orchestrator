"""Node: fetch_github_token — fetch GitHub token for repo cloning when needed."""

import click

from dispatcher.github_client import GitHubAuthError, get_installation_token
from orchestrator.work_planner.state import (
    FetchGithubTokenInputState,
    FetchGithubTokenOutputState,
)


def fetch_github_token(state: FetchGithubTokenInputState) -> FetchGithubTokenOutputState:
    """Fetch a GitHub App token for HTTPS GitHub clone URLs.

    For SSH URLs (git@github.com:...) token auth is not required.
    """
    repo_url = (state.get("repo_url") or "").strip()
    if not repo_url:
        return {
            "error": "Repository URL is required before fetching GitHub token",
            "failed_node": "fetch_github_token",
        }

    if repo_url.startswith("git@"):
        click.echo("🔑 Skipping GitHub token fetch for SSH repository URL")
        return {}

    ticket_key = state.get("ticket_key", "")
    project_key = ticket_key.split("-")[0].upper() if ticket_key else ""

    try:
        token = get_installation_token(project_key)
        click.echo("✓ Fetched GitHub App installation token")
        return {"github_token": token}
    except GitHubAuthError as exc:
        error_msg = f"GitHub token fetch failed: {exc}"
        click.echo(f"❌ {error_msg}", err=True)
        return {"error": error_msg, "failed_node": "fetch_github_token"}
