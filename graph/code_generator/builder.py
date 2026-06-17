"""
Code generator subgraph builder.

Compiles the executor as a LangGraph StateGraph that decomposes execute_plan
into focused, single-responsibility nodes.

Graph topology:
    resolve_repo
        ↓ (error → persist_results)
    fetch_github_token
        ↓ (error → persist_results)
    clone_repo
        ↓ (error → persist_results)
    run_goose
        ↓
    process_results
        ↓
    push_and_create_pr
        ↓
    persist_results
        ↓
    cleanup
        ↓
    END

All error paths route through persist_results → cleanup so that failure
summaries are always persisted and temp files are always removed.
"""

from langgraph.graph import END, StateGraph

from graph.code_generator.edges import (
    route_after_clone,
    route_after_fetch_token,
    route_after_resolve,
)
from graph.code_generator.nodes.cleanup import cleanup
from graph.code_generator.nodes.clone_repo import clone_repo
from graph.code_generator.nodes.fetch_github_token import fetch_github_token
from graph.code_generator.nodes.persist_results import persist_results
from graph.code_generator.nodes.process_results import process_results
from graph.code_generator.nodes.push_and_create_pr import push_and_create_pr
from graph.code_generator.nodes.resolve_repo import resolve_repo
from graph.code_generator.nodes.run_goose import run_goose
from graph.code_generator.state import CodeGeneratorState


def build_code_generator():
    """Build and compile the code_generator subgraph.

    Returns:
        A compiled LangGraph ``CompiledGraph`` suitable for use as a node in
        the top-level orchestrator graph.
    """
    builder = StateGraph(CodeGeneratorState)

    builder.add_node("resolve_repo", resolve_repo)
    builder.add_node("fetch_github_token", fetch_github_token)
    builder.add_node("clone_repo", clone_repo)
    builder.add_node("run_goose", run_goose)
    builder.add_node("process_results", process_results)
    builder.add_node("push_and_create_pr", push_and_create_pr)
    builder.add_node("persist_results", persist_results)
    builder.add_node("cleanup", cleanup)

    builder.set_entry_point("resolve_repo")

    builder.add_conditional_edges(
        "resolve_repo",
        route_after_resolve,
        {"fetch_github_token": "fetch_github_token", "persist_results": "persist_results"},
    )
    builder.add_conditional_edges(
        "fetch_github_token",
        route_after_fetch_token,
        {"clone_repo": "clone_repo", "persist_results": "persist_results"},
    )
    builder.add_conditional_edges(
        "clone_repo",
        route_after_clone,
        {"run_goose": "run_goose", "persist_results": "persist_results"},
    )
    builder.add_edge("run_goose", "process_results")
    builder.add_edge("process_results", "push_and_create_pr")
    builder.add_edge("push_and_create_pr", "persist_results")
    builder.add_edge("persist_results", "cleanup")
    builder.add_edge("cleanup", END)

    return builder.compile()
