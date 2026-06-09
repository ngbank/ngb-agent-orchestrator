"""Typed return contracts for graph node functions.

Nodes return partial state updates that are merged into graph state by LangGraph.
These TypedDicts make that contract explicit and safer to evolve.
"""

from typing import Any

from typing_extensions import TypedDict

JsonDict = dict[str, Any]


class CommonNodeResult(TypedDict, total=False):
    """Keys shared across planner/orchestrator node return payloads."""

    error: str | None
    failed_node: str | None


class WorkPlannerNodeResult(CommonNodeResult, total=False):
    """Return contract for ``graph.work_planner.nodes`` functions."""

    workflow_id: str
    ticket: JsonDict
    work_plan_data: JsonDict | None
    clarifications: list[JsonDict]


class OrchestratorNodeResult(CommonNodeResult, total=False):
    """Return contract for ``graph.nodes`` functions."""

    approval_decision: str
    rejection_reason: str | None
    pr_approval_decision: str
    pr_comments: str | None
