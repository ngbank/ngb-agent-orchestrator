"""
Code generator subgraph builder.

Compiles the executor as a LangGraph StateGraph that decomposes execute_plan
into focused, single-responsibility nodes.

Graph topology:
    repo_setup
        ↓ (error → persist_results)
    prepare_workspace
        ↓
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

from orchestrator.code_generator.edges import (
    route_after_infer_branch_prefix,
    route_after_prepare_workspace,
    route_after_repo_setup,
)
from orchestrator.code_generator.nodes.cleanup import cleanup
from orchestrator.code_generator.nodes.infer_branch_prefix import infer_branch_prefix
from orchestrator.code_generator.nodes.persist_results import persist_results
from orchestrator.code_generator.nodes.prepare_workspace import prepare_workspace
from orchestrator.code_generator.nodes.process_results import process_results
from orchestrator.code_generator.nodes.push_and_create_pr import push_and_create_pr
from orchestrator.code_generator.nodes.run_goose import run_goose
from orchestrator.code_generator.state import CodeGeneratorState
from orchestrator.shared.repo_setup import build_repo_setup_subgraph


def build_code_generator():
    """Build and compile the code_generator subgraph.

    Returns:
        A compiled LangGraph ``CompiledGraph`` suitable for use as a node in
        the top-level orchestrator graph.
    """
    builder = StateGraph(CodeGeneratorState)
    repo_setup_subgraph = build_repo_setup_subgraph("code_generator")

    builder.add_node("repo_setup", repo_setup_subgraph)
    builder.add_node("prepare_workspace", prepare_workspace)
    builder.add_node("infer_branch_prefix", infer_branch_prefix)
    builder.add_node("run_goose", run_goose)
    builder.add_node("process_results", process_results)
    builder.add_node("push_and_create_pr", push_and_create_pr)
    builder.add_node("persist_results", persist_results)
    builder.add_node("cleanup", cleanup)

    builder.set_entry_point("repo_setup")

    builder.add_conditional_edges(
        "repo_setup",
        route_after_repo_setup,
        {"run_goose": "prepare_workspace", "persist_results": "persist_results"},
    )
    builder.add_conditional_edges(
        "prepare_workspace",
        route_after_prepare_workspace,
        {"infer_branch_prefix": "infer_branch_prefix", "run_goose": "run_goose"},
    )
    builder.add_conditional_edges(
        "infer_branch_prefix",
        route_after_infer_branch_prefix,
        {"run_goose": "run_goose", "persist_results": "persist_results"},
    )
    builder.add_edge("run_goose", "process_results")
    builder.add_edge("process_results", "push_and_create_pr")
    builder.add_edge("push_and_create_pr", "persist_results")
    builder.add_edge("persist_results", "cleanup")
    builder.add_edge("cleanup", END)

    return builder.compile()
