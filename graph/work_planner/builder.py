"""
WorkPlanner subgraph builder.

Compiles the WorkPlanner as a LangGraph StateGraph that encapsulates all
current dispatcher stages as discrete nodes connected by conditional edges.

Graph topology:
    validate_input
        ↓ (error → error_handler)
    check_duplicate
        ↓ (error → error_handler)
    fetch_ticket
        ↓ (error → error_handler)
    create_workflow_record
        ↓
    generate_plan
        ↓
    validate_plan
        ↓ (error → error_handler)
    store_plan
        ↓
    post_to_jira
        ↓
    END
    error_handler → END
"""

from langgraph.graph import StateGraph, END

from graph.work_planner.state import WorkPlannerState
from graph.work_planner.nodes.validate_input import validate_input
from graph.work_planner.nodes.check_duplicate import check_duplicate
from graph.work_planner.nodes.fetch_ticket import fetch_ticket
from graph.work_planner.nodes.create_workflow_record import create_workflow_record
from graph.work_planner.nodes.generate_plan import generate_plan
from graph.work_planner.nodes.validate_plan import validate_plan
from graph.work_planner.nodes.store_plan import store_plan
from graph.work_planner.nodes.post_to_jira import post_to_jira
from graph.work_planner.nodes.error_handler import error_handler
from graph.work_planner.edges import (
    route_after_validate_input,
    route_after_check_duplicate,
    route_after_fetch_ticket,
    route_after_validate_plan,
)


def build_work_planner(checkpointer=None):
    """Build and compile the WorkPlanner subgraph.

    Args:
        checkpointer: Optional LangGraph checkpointer (e.g., SqliteSaver) for
            resumable runs.  Defaults to None (stateless execution).

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
    builder.add_node("generate_plan", generate_plan)
    builder.add_node("validate_plan", validate_plan)
    builder.add_node("store_plan", store_plan)
    builder.add_node("post_to_jira", post_to_jira)
    builder.add_node("error_handler", error_handler)

    # --- entry point ---
    builder.set_entry_point("validate_input")

    # --- edges ---
    builder.add_conditional_edges(
        "validate_input",
        route_after_validate_input,
        {"check_duplicate": "check_duplicate", "error_handler": "error_handler"},
    )
    builder.add_conditional_edges(
        "check_duplicate",
        route_after_check_duplicate,
        {"fetch_ticket": "fetch_ticket", "error_handler": "error_handler"},
    )
    builder.add_conditional_edges(
        "fetch_ticket",
        route_after_fetch_ticket,
        {
            "create_workflow_record": "create_workflow_record",
            "error_handler": "error_handler",
        },
    )
    builder.add_edge("create_workflow_record", "generate_plan")
    builder.add_edge("generate_plan", "validate_plan")
    builder.add_conditional_edges(
        "validate_plan",
        route_after_validate_plan,
        {"store_plan": "store_plan", "error_handler": "error_handler"},
    )
    builder.add_edge("store_plan", "post_to_jira")
    builder.add_edge("post_to_jira", END)
    builder.add_edge("error_handler", END)

    return builder.compile(checkpointer=checkpointer)
