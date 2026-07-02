"""Shared helpers for repo_setup subgraph nodes."""

from orchestrator.shared.repo_setup.state import RepoSetupState


def code_generation_failure_summary(ticket_key: str, error: str) -> dict:
    """Return a standard failed code generation summary dict."""
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


def failure_update(state: RepoSetupState, message: str, mode: str) -> dict:
    """Map failures to mode-specific fields expected by parent subgraphs."""
    if mode == "code_generator":
        return {
            "code_generation_summary": code_generation_failure_summary(
                state.get("ticket_key", ""), message
            ),
            "exec_error": message,
            "failed_node": "generate_code",
        }

    return {
        "error": message,
        "failed_node": "repo_setup",
    }
