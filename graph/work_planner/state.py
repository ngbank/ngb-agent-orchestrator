"""
WorkPlanner subgraph state.

WorkPlannerState is a subset of OrchestratorState. All keys exist on both
TypedDicts so that LangGraph can route state through the subgraph boundary
without an explicit input/output channel mapping.
"""

from typing import Any, Optional

from typing_extensions import TypedDict


class WorkPlannerState(TypedDict, total=False):
    # --- shared with OrchestratorState ---
    ticket_key: str
    dry_run: bool
    workflow_id: Optional[str]
    ticket: Optional[Any]  # dispatcher.jira_client.JiraTicket
    work_plan_data: Optional[dict]
    error: Optional[str]
    clarifications: Optional[list]  # accumulated Q&A rounds from reviewer
