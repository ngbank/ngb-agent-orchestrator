"""
Top-level orchestrator graph builder.

Creates a pipeline with a human-in-the-loop approval gate:

    START → work_planner (subgraph) → await_approval → execute_plan → END
                                            ↓ rejected
                                           END

The ``work_planner`` subgraph handles all planning stages (fetch, generate,
validate, store, post to Jira).  ``await_approval`` calls interrupt() so the
graph suspends until the developer explicitly approves or rejects via CLI.
The ``execute_plan`` node is a stub for future code-execution work.
"""

import sqlite3
from typing import Literal

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver

from graph.state import OrchestratorState
from graph.work_planner.builder import build_work_planner
from graph.nodes.await_approval import await_approval
from state.state_store import get_db_path


def _execute_plan_stub(state: OrchestratorState) -> dict:
    """Stub for future code-execution stage (ticket TBD)."""
    return {}


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
        checkpointer = SqliteSaver(conn)

    work_planner = build_work_planner()

    builder = StateGraph(OrchestratorState)
    builder.add_node("work_planner", work_planner)
    builder.add_node("await_approval", await_approval)
    builder.add_node("execute_plan", _execute_plan_stub)

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
    builder.add_edge("execute_plan", END)

    return builder.compile(checkpointer=checkpointer)
