"""Conditional edge routing functions for the code_generator subgraph."""

from typing import Literal

from orchestrator.code_generator.state import CodeGeneratorState


def route_after_resolve(
    state: CodeGeneratorState,
) -> Literal["fetch_github_token", "persist_results"]:
    """Skip to persist_results if resolve_repo failed."""
    if state.get("exec_error"):
        return "persist_results"
    return "fetch_github_token"


def route_after_fetch_token(
    state: CodeGeneratorState,
) -> Literal["clone_repo", "persist_results"]:
    """Skip to persist_results if fetch_github_token failed."""
    if state.get("exec_error"):
        return "persist_results"
    return "clone_repo"


def route_after_clone(
    state: CodeGeneratorState,
) -> Literal["run_goose", "persist_results"]:
    """Skip to persist_results if clone_repo failed."""
    if state.get("exec_error"):
        return "persist_results"
    return "run_goose"
