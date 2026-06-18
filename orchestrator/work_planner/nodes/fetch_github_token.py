"""Node: fetch_github_token — fetch GitHub token for repo cloning when needed."""

import click

from orchestrator.shared.repo_setup import fetch_token_for_repo
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

    try:
        token = fetch_token_for_repo(state.get("ticket_key", ""), repo_url)
        click.echo("✓ Fetched GitHub App installation token")
        return {"github_token": token} if token else {}
    except Exception as exc:  # noqa: BLE001
        error_msg = str(exc)
        click.echo(f"❌ {error_msg}", err=True)
        return {"error": error_msg, "failed_node": "fetch_github_token"}
