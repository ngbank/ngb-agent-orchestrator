"""Conditional edge routing functions for the shared repo_setup subgraph."""

from typing import Literal

from orchestrator.shared.repo_setup.state import RepoSetupState


def route_after_resolve(state: RepoSetupState) -> Literal["fetch_github_token", "end"]:
    """Stop subgraph early when resolve step failed."""
    if state.get("error") or state.get("exec_error"):
        return "end"
    return "fetch_github_token"


def route_after_fetch(state: RepoSetupState) -> Literal["clone_repo", "end"]:
    """Stop subgraph early when token fetch step failed."""
    if state.get("error") or state.get("exec_error"):
        return "end"
    return "clone_repo"
