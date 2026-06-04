"""Conditional edge routing functions for the executor subgraph."""

from typing import Literal

from graph.executor.state import ExecutionState


def route_after_resolve(
    state: ExecutionState,
) -> Literal["clone_repo", "persist_results"]:
    """Skip to persist_results if resolve_repo failed."""
    if state.get("exec_error"):
        return "persist_results"
    return "clone_repo"


def route_after_clone(
    state: ExecutionState,
) -> Literal["run_goose", "persist_results"]:
    """Skip to persist_results if clone_repo failed."""
    if state.get("exec_error"):
        return "persist_results"
    return "run_goose"
