"""Node: fetch_github_token — generate short-lived GitHub App installation token."""

import click

from orchestrator.code_generator.state import (
    FetchGithubTokenInputState,
    FetchGithubTokenOutputState,
)
from orchestrator.shared.repo_setup import fetch_token_for_repo


def _failure_summary(ticket_key: str, error: str) -> dict:
    """Return a standard failed execution summary dict."""
    return {
        "ticket_key": ticket_key,
        "branch": "",
        "build": "fail",
        "tests": "skipped",
        "files_changed": [],
        "commit_sha": "",
        "pr_url": "",
        "status": "failed",
        "error": error,
    }


def fetch_github_token(
    state: FetchGithubTokenInputState,
) -> FetchGithubTokenOutputState:
    """Fetch a GitHub App installation access token and store in state.

    Reads:  ticket_key
    Writes: github_token
    On failure: additionally sets execution_summary, exec_error, failed_node.
    """
    ticket_key = state.get("ticket_key", "")
    repo_url = state.get("repo_url", "")

    try:
        token = fetch_token_for_repo(ticket_key, repo_url)
        click.echo("✓ Fetched GitHub App installation token")
        return {"github_token": token} if token else {}
    except Exception as e:  # noqa: BLE001
        click.echo(f"❌ Failed to fetch GitHub token: {e}", err=True)
        error_msg = f"GitHub token fetch failed: {e}"
        return {
            "execution_summary": _failure_summary(ticket_key, error_msg),
            "exec_error": error_msg,
            "failed_node": "execute_plan",
        }
