"""
Top-level orchestrator graph builder.

Creates a pipeline with a human-in-the-loop approval gate:

    START → work_planner (subgraph) → await_approval → execute_plan → await_pr_approval → END
                                            ↓ rejected                        ↓ comment
                                           END                          execute_plan (loop)

The ``work_planner`` subgraph handles all planning stages (fetch, generate,
validate, store, post to Jira).  ``await_approval`` calls interrupt() so the
graph suspends until the developer explicitly approves or rejects via CLI.
The ``execute_plan`` node invokes the Goose execute recipe to implement the
approved WorkPlan.  ``await_pr_approval`` calls interrupt() so the
graph suspends until the PR is approved, commented on, or rejected via CLI.
"""

import sqlite3
from typing import Literal

from langgraph.graph import END, StateGraph

from orchestrator.code_generator.builder import build_code_generator
from orchestrator.nodes.await_approval import await_approval
from orchestrator.nodes.await_pr_approval import await_pr_approval
from orchestrator.state import OrchestratorState
from orchestrator.work_planner.builder import build_work_planner
from state.observable_sqlite_saver import ObservableSqliteSaver
from state.workflow_repository import get_db_path


def _route_after_work_planner(
    state: OrchestratorState,
) -> Literal["await_approval", "__end__"]:
    """Skip approval gate if the work_planner subgraph ended with an error."""
    if state.get("error"):
        return "__end__"
    return "await_approval"


def _route_after_approval(
    state: OrchestratorState,
) -> Literal["execute_plan", "__end__"]:
    if state.get("approval_decision") == "approved":
        return "execute_plan"
    return "__end__"


def _route_after_pr_approval(
    state: OrchestratorState,
) -> Literal["execute_plan", "__end__"]:
    if state.get("pr_approval_decision") == "commented":
        return "execute_plan"
    return "__end__"


def build_orchestrator(checkpointer=None):
    """Build and compile the top-level orchestrator graph.

    Args:
        checkpointer: Optional LangGraph checkpointer for resumable runs.
            When None a SqliteSaver backed by the application DB is used so
            that interrupted (pending-approval) runs can be resumed.

    Returns:
        A compiled LangGraph ``CompiledGraph``.
    """
    if checkpointer is None:
        conn = sqlite3.connect(get_db_path(), check_same_thread=False)
        checkpointer = ObservableSqliteSaver(conn)

    work_planner = build_work_planner()
    code_generator = build_code_generator()

    builder = StateGraph(OrchestratorState)
    builder.add_node("work_planner", work_planner)
    builder.add_node("await_approval", await_approval)
    builder.add_node("execute_plan", code_generator)
    builder.add_node("await_pr_approval", await_pr_approval)

    builder.set_entry_point("work_planner")
    builder.add_conditional_edges(
        "work_planner",
        _route_after_work_planner,
        {"await_approval": "await_approval", "__end__": END},
    )
    builder.add_conditional_edges(
        "await_approval",
        _route_after_approval,
        {"execute_plan": "execute_plan", "__end__": END},
    )
    builder.add_edge("execute_plan", "await_pr_approval")
    builder.add_conditional_edges(
        "await_pr_approval",
        _route_after_pr_approval,
        {"execute_plan": "execute_plan", "__end__": END},
    )

    return builder.compile(checkpointer=checkpointer)
