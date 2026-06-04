"""Node: resolve_repo — resolve the target repository URL for the ticket's project."""

import click

from graph.code_generator.state import CodeGeneratorState
from mcp_server.server import get_repo_for_project


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


def resolve_repo(state: CodeGeneratorState) -> dict:
    """Resolve the git repository URL for the ticket's project key.

    Reads:  ticket_key
    Writes: repo_url
    On failure: sets execution_summary, exec_error, failed_node and routes to persist_results.
    """
    ticket_key = state.get("ticket_key", "")
    project_key = ticket_key.split("-")[0].upper()
    try:
        repo_url = get_repo_for_project(project_key)
        return {"repo_url": repo_url}
    except ValueError as e:
        click.echo(f"❌ {e}", err=True)
        return {
            "execution_summary": _failure_summary(ticket_key, str(e)),
            "exec_error": str(e),
            "failed_node": "execute_plan",
        }
