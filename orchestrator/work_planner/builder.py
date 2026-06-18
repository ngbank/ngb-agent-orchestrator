"""
WorkPlanner subgraph builder.

Compiles the WorkPlanner as a LangGraph StateGraph that encapsulates all
current dispatcher stages as discrete nodes connected by conditional edges.

Graph topology:
    validate_input
        ↓ (error → cleanup → error_handler)
    check_duplicate
        ↓ (error → cleanup → error_handler)
    fetch_ticket
        ↓ (error → cleanup → error_handler)
    create_workflow_record
        ↓
    resolve_repo
        ↓ (error → cleanup → error_handler)
    fetch_github_token
        ↓ (error → cleanup → error_handler)
    clone_repo
        ↓ (error → cleanup → error_handler)
    generate_plan
        ↓ (error or empty work_plan_data → cleanup → error_handler)
    validate_plan
        ↓ (error → cleanup → error_handler)
        ↓ (concerns/blocked/questions → await_workplan_clarification)
    await_workplan_clarification [interrupt()]
        ↓ (on resume → generate_plan)  ← loop
    store_plan
        ↓
    post_to_jira
        ↓
    cleanup
        ↓ (error? → error_handler)
    END
"""

from langgraph.graph import END, StateGraph

from orchestrator.work_planner.edges import (
    route_after_check_duplicate,
    route_after_cleanup,
    route_after_clone_repo,
    route_after_fetch_github_token,
    route_after_fetch_ticket,
    route_after_generate_plan,
    route_after_resolve_repo,
    route_after_validate_input,
    route_after_validate_plan,
    route_after_workplan_clarification,
)
from orchestrator.work_planner.nodes.await_workplan_clarification import (
    await_workplan_clarification,
)
from orchestrator.work_planner.nodes.check_duplicate import check_duplicate
from orchestrator.work_planner.nodes.cleanup import cleanup
from orchestrator.work_planner.nodes.clone_repo import clone_repo
from orchestrator.work_planner.nodes.create_workflow_record import create_workflow_record
from orchestrator.work_planner.nodes.error_handler import error_handler
from orchestrator.work_planner.nodes.fetch_github_token import fetch_github_token
from orchestrator.work_planner.nodes.fetch_ticket import fetch_ticket
from orchestrator.work_planner.nodes.generate_plan import generate_plan
from orchestrator.work_planner.nodes.post_to_jira import post_to_jira
from orchestrator.work_planner.nodes.resolve_repo import resolve_repo
from orchestrator.work_planner.nodes.store_plan import store_plan
from orchestrator.work_planner.nodes.validate_input import validate_input
from orchestrator.work_planner.nodes.validate_plan import validate_plan
from orchestrator.work_planner.state import WorkPlannerState


def build_work_planner(checkpointer=None):
    """Build and compile the WorkPlanner subgraph.

    Args:
        checkpointer: Optional LangGraph checkpointer (e.g., SqliteSaver) for
            resumable runs. Defaults to None (stateless execution).

    Returns:
        A compiled LangGraph ``CompiledGraph`` ready to be invoked directly or
        embedded as a node inside the top-level orchestrator graph.
    """
    builder = StateGraph(WorkPlannerState)

    # --- nodes ---
    builder.add_node("validate_input", validate_input)
    builder.add_node("check_duplicate", check_duplicate)
    builder.add_node("fetch_ticket", fetch_ticket)
    builder.add_node("create_workflow_record", create_workflow_record)
    builder.add_node("resolve_repo", resolve_repo)
    builder.add_node("fetch_github_token", fetch_github_token)
    builder.add_node("clone_repo", clone_repo)
    builder.add_node("generate_plan", generate_plan)
    builder.add_node("validate_plan", validate_plan)
    builder.add_node("await_workplan_clarification", await_workplan_clarification)
    builder.add_node("store_plan", store_plan)
    builder.add_node("post_to_jira", post_to_jira)
    builder.add_node("cleanup", cleanup)
    builder.add_node("error_handler", error_handler)

    # --- entry point ---
    builder.set_entry_point("validate_input")

    # --- edges ---
    builder.add_conditional_edges(
        "validate_input",
        route_after_validate_input,
        {"check_duplicate": "check_duplicate", "cleanup": "cleanup"},
    )
    builder.add_conditional_edges(
        "check_duplicate",
        route_after_check_duplicate,
        {"fetch_ticket": "fetch_ticket", "cleanup": "cleanup"},
    )
    builder.add_conditional_edges(
        "fetch_ticket",
        route_after_fetch_ticket,
        {
            "create_workflow_record": "create_workflow_record",
            "cleanup": "cleanup",
        },
    )
    builder.add_edge("create_workflow_record", "resolve_repo")
    builder.add_conditional_edges(
        "resolve_repo",
        route_after_resolve_repo,
        {
            "fetch_github_token": "fetch_github_token",
            "cleanup": "cleanup",
        },
    )
    builder.add_conditional_edges(
        "fetch_github_token",
        route_after_fetch_github_token,
        {
            "clone_repo": "clone_repo",
            "cleanup": "cleanup",
        },
    )
    builder.add_conditional_edges(
        "clone_repo",
        route_after_clone_repo,
        {
            "generate_plan": "generate_plan",
            "cleanup": "cleanup",
        },
    )
    builder.add_conditional_edges(
        "generate_plan",
        route_after_generate_plan,
        {"validate_plan": "validate_plan", "cleanup": "cleanup"},
    )
    builder.add_conditional_edges(
        "validate_plan",
        route_after_validate_plan,
        {
            "store_plan": "store_plan",
            "await_workplan_clarification": "await_workplan_clarification",
            "cleanup": "cleanup",
        },
    )
    builder.add_conditional_edges(
        "await_workplan_clarification",
        route_after_workplan_clarification,
        {"generate_plan": "generate_plan", "cleanup": "cleanup"},
    )
    builder.add_edge("store_plan", "post_to_jira")
    builder.add_edge("post_to_jira", "cleanup")
    builder.add_conditional_edges(
        "cleanup",
        route_after_cleanup,
        {"error_handler": "error_handler", "end": END},
    )
    builder.add_edge("error_handler", END)

    return builder.compile(checkpointer=checkpointer)
