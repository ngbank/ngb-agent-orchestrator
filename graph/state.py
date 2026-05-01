"""
Top-level orchestrator state.

OrchestratorState is the parent graph's state TypedDict. It is a superset of
WorkPlannerState so that the compiled WorkPlanner subgraph can read and write
shared keys without an explicit channel mapping.
"""

from typing import Any, Optional
from typing_extensions import TypedDict


class OrchestratorState(TypedDict, total=False):
    # --- inputs ---
    ticket_key: str
    dry_run: bool

    # --- populated by WorkPlanner subgraph ---
    workflow_id: Optional[str]
    ticket: Optional[Any]          # dispatcher.jira_client.JiraTicket
    work_plan_data: Optional[dict]
    error: Optional[str]

    # --- populated by await_approval node ---
    approval_decision: Optional[str]   # "approved" | "rejected"
    rejection_reason: Optional[str]    # only set when rejected
