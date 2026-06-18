"""Shared repo setup subgraph builder used by planner and executor flows."""

from typing import Literal

from langgraph.graph import END, StateGraph

from orchestrator.shared.repo_setup.nodes import (
    build_clone_repo_node,
    build_fetch_github_token_node,
    build_resolve_repo_node,
)
from orchestrator.shared.repo_setup.state import RepoSetupState


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

    builder.add_node("resolve_repo", build_resolve_repo_node(mode))
    builder.add_node("fetch_github_token", build_fetch_github_token_node(mode))
    builder.add_node("clone_repo", build_clone_repo_node(mode))

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
