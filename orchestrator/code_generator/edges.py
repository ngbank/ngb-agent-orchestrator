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


def route_after_infer_branch_prefix(
    state: CodeGeneratorState,
) -> Literal["run_goose", "persist_results"]:
    """Skip to persist_results if infer_branch_prefix failed."""
    if state.get("exec_error"):
        return "persist_results"
    return "run_goose"


def route_after_prepare_workspace(
    state: CodeGeneratorState,
) -> Literal["infer_branch_prefix", "run_goose"]:
    """Skip infer_branch_prefix on comment-pr re-executions."""
    if state.get("pr_approval_decision") == "commented":
        return "run_goose"
    return "infer_branch_prefix"


def route_after_repo_setup(
    state: CodeGeneratorState,
) -> Literal["run_goose", "persist_results"]:
    """Skip to persist_results if repo_setup subgraph failed."""
    if state.get("exec_error"):
        return "persist_results"
    if not state.get("working_dir"):
        return "persist_results"
    return "run_goose"
