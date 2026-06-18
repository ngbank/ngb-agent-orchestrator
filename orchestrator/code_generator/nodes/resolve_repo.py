"""Node: resolve_repo — resolve the target repository URL for the ticket's project."""

import click

from orchestrator.code_generator.state import ResolveRepoInputState, ResolveRepoOutputState
from orchestrator.shared.repo_setup import resolve_repository_url


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


def resolve_repo(state: ResolveRepoInputState) -> ResolveRepoOutputState:
    """Resolve the git repository URL for the ticket's project key.

    Reads:  ticket_key
    Writes: repo_url
    On failure: sets execution_summary, exec_error, failed_node and routes to persist_results.
    """
    ticket_key = state.get("ticket_key", "")
    try:
        repo_url = resolve_repository_url(ticket_key)
        return {"repo_url": repo_url}
    except ValueError as e:
        click.echo(f"❌ {e}", err=True)
        return {
            "execution_summary": _failure_summary(ticket_key, str(e)),
            "exec_error": str(e),
            "failed_node": "execute_plan",
        }
