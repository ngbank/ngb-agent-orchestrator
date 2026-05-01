"""
Top-level orchestrator graph builder.

Creates a two-node pipeline:

    START → work_planner (subgraph) → execute_plan (stub) → END

The ``work_planner`` subgraph handles all planning stages.
The ``execute_plan`` node is a stub for future code-execution work.
"""

from langgraph.graph import StateGraph, END

from graph.state import OrchestratorState
from graph.work_planner.builder import build_work_planner


def _execute_plan_stub(state: OrchestratorState) -> dict:
    """Stub for future code-execution stage (ticket TBD)."""
    return {}


def build_orchestrator(checkpointer=None):
    """Build and compile the top-level orchestrator graph.

    Args:
        checkpointer: Optional LangGraph checkpointer for resumable runs.

    Returns:
        A compiled LangGraph ``CompiledGraph``.
    """
    work_planner = build_work_planner()

    builder = StateGraph(OrchestratorState)
    builder.add_node("work_planner", work_planner)
    builder.add_node("execute_plan", _execute_plan_stub)

    builder.set_entry_point("work_planner")
    builder.add_edge("work_planner", "execute_plan")
    builder.add_edge("execute_plan", END)

    return builder.compile(checkpointer=checkpointer)
