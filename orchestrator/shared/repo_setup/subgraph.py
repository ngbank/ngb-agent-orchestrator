"""Shared repo setup subgraph used by planner and executor flows."""

from typing import Literal, Optional

import click
from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from orchestrator.shared.repo_setup.primitives import (
    clone_repository,
    fetch_token_for_repo,
    resolve_repository_url,
)
from orchestrator.utils import log_path


class RepoSetupState(TypedDict, total=False):
    """Minimal state contract for the shared repo setup subgraph."""

    ticket_key: str
    workflow_id: Optional[str]
    repo_url: Optional[str]
    github_token: Optional[str]
    working_dir: Optional[str]
    error: Optional[str]
    exec_error: Optional[str]
    failed_node: Optional[str]
    execution_summary: Optional[dict]


def _execution_failure_summary(ticket_key: str, error: str) -> dict:
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


def _failure_update(state: RepoSetupState, message: str, mode: str) -> dict:
    if mode == "code_generator":
        return {
            "execution_summary": _execution_failure_summary(state.get("ticket_key", ""), message),
            "exec_error": message,
            "failed_node": "execute_plan",
        }

    return {
        "error": message,
        "failed_node": "repo_setup",
    }


def _resolve_repo_node(mode: str):
    def _node(state: RepoSetupState) -> dict:
        ticket_key = state.get("ticket_key", "")
        existing_repo_url = (state.get("repo_url") or "").strip()

        try:
            repo_url = resolve_repository_url(ticket_key, existing_repo_url)
            return {"repo_url": repo_url}
        except ValueError as exc:
            error_msg = str(exc)
            click.echo(f"❌ {error_msg}", err=True)
            return _failure_update(state, error_msg, mode)

    return _node


def _fetch_token_node(mode: str):
    def _node(state: RepoSetupState) -> dict:
        ticket_key = state.get("ticket_key", "")
        repo_url = (state.get("repo_url") or "").strip()

        if not repo_url:
            error_msg = "Repository URL is required before fetching GitHub token"
            click.echo(f"❌ {error_msg}", err=True)
            return _failure_update(state, error_msg, mode)

        if repo_url.startswith("git@"):
            click.echo("🔑 Skipping GitHub token fetch for SSH repository URL")
            return {}

        try:
            token = fetch_token_for_repo(ticket_key, repo_url)
            click.echo("✓ Fetched GitHub App installation token")
            return {"github_token": token} if token else {}
        except Exception as exc:  # noqa: BLE001
            error_msg = str(exc)
            click.echo(f"❌ {error_msg}", err=True)
            return _failure_update(state, error_msg, mode)

    return _node


def _clone_repo_node(mode: str):
    def _node(state: RepoSetupState) -> dict:
        workflow_id = state.get("workflow_id") or "unknown"
        ticket_key = state.get("ticket_key", "")
        repo_url = (state.get("repo_url") or "").strip()
        github_token = (state.get("github_token") or "").strip()

        if not repo_url:
            error_msg = "Repository URL is required before cloning"
            click.echo(f"❌ {error_msg}", err=True)
            return _failure_update(state, error_msg, mode)

        stage = "execute" if mode == "code_generator" else "plan"
        prefix = f"ngb-{'execute' if mode == 'code_generator' else 'plan'}-{workflow_id}-"
        lp = log_path(workflow_id, stage, ticket_key=ticket_key)

        try:
            with open(lp, "a") as log_file:
                log_file.write(f"\n=== git clone {repo_url} ===\n")
                working_dir = clone_repository(
                    repo_url,
                    github_token,
                    temp_prefix=prefix,
                    log_file=log_file,
                )
            click.echo(f"📂 Cloning {repo_url} into {working_dir}... (log: {lp})")
            return {"working_dir": working_dir}
        except Exception as exc:  # noqa: BLE001
            error_msg = f"Failed to clone {repo_url}: {exc}"
            click.echo(f"❌ {error_msg}", err=True)
            return _failure_update(state, error_msg, mode)

    return _node


def _route_after_resolve(state: RepoSetupState) -> Literal["fetch_github_token", "end"]:
    if state.get("error") or state.get("exec_error"):
        return "end"
    return "fetch_github_token"


def _route_after_fetch(state: RepoSetupState) -> Literal["clone_repo", "end"]:
    if state.get("error") or state.get("exec_error"):
        return "end"
    return "clone_repo"


def build_repo_setup_subgraph(mode: Literal["work_planner", "code_generator"]):
    """Build a shared repo-setup subgraph.

    Args:
        mode: Selects failure field mapping for the parent subgraph.
    """
    builder = StateGraph(RepoSetupState)

    builder.add_node("resolve_repo", _resolve_repo_node(mode))
    builder.add_node("fetch_github_token", _fetch_token_node(mode))
    builder.add_node("clone_repo", _clone_repo_node(mode))

    builder.set_entry_point("resolve_repo")
    builder.add_conditional_edges(
        "resolve_repo",
        _route_after_resolve,
        {"fetch_github_token": "fetch_github_token", "end": END},
    )
    builder.add_conditional_edges(
        "fetch_github_token",
        _route_after_fetch,
        {"clone_repo": "clone_repo", "end": END},
    )
    builder.add_edge("clone_repo", END)

    return builder.compile()
