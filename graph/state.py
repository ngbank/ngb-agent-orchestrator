"""
Top-level orchestrator state with per-stage focused TypedDicts.

Following the Interface Segregation Principle, each stage of the orchestration
workflow has a focused TypedDict that declares only the keys it reads and writes.
OrchestratorState is a composition of all these TypedDicts for the top-level
LangGraph graph (which requires a single state type).
"""

from typing import Any, Optional

from typing_extensions import TypedDict


class OrchestratorInputState(TypedDict):
    """Top-level inputs to the orchestrator graph."""

    ticket_key: str
    dry_run: bool


class WorkPlanningInputState(TypedDict, total=False):
    """Input required by WorkPlanner subgraph."""

    ticket_key: str
    dry_run: bool
    workflow_id: Optional[str]  # optional for fresh runs


class WorkPlanningOutputState(TypedDict, total=False):
    """Output produced by WorkPlanner subgraph."""

    workflow_id: Optional[str]
    ticket: Optional[Any]  # dispatcher.jira_client.JiraTicket
    work_plan_data: Optional[dict]
    error: Optional[str]
    failed_node: Optional[str]
    clarifications: Optional[list]  # accumulated Q&A rounds from reviewer


class ApprovalInputState(TypedDict, total=False):
    """Input required by await_approval node."""

    workflow_id: Optional[str]
    ticket_key: str
    work_plan_data: Optional[dict]


class ApprovalOutputState(TypedDict, total=False):
    """Output produced by await_approval node."""

    approval_decision: Optional[str]  # "approved" | "rejected"
    rejection_reason: Optional[str]


class CodeGenerationInputState(TypedDict, total=False):
    """Input required by code_generator subgraph."""

    ticket_key: str
    workflow_id: str
    work_plan_data: dict


class CodeGenerationOutputState(TypedDict, total=False):
    """Output produced by code_generator subgraph."""

    pr_url: Optional[str]
    execution_summary: Optional[dict]
    failed_node: Optional[str]


class PRApprovalInputState(TypedDict, total=False):
    """Input required by await_pr_approval node."""

    workflow_id: Optional[str]
    ticket_key: str
    pr_url: Optional[str]


class PRApprovalOutputState(TypedDict, total=False):
    """Output produced by await_pr_approval node."""

    pr_approval_decision: Optional[str]  # "approved" | "rejected" | "commented"
    pr_comments: Optional[str]


class OrchestratorState(TypedDict, total=False):
    """
    Top-level orchestrator state.

    Composition of all per-stage TypedDicts for the LangGraph graph. Each node
    is annotated with its narrower input/output types (OrchestratorInputState,
    WorkPlanningInputState, etc.) for IDE and mypy visibility, even though the
    runtime type remains OrchestratorState.

    This is the superset of WorkPlannerState so that the compiled WorkPlanner
    subgraph can read and write shared keys without an explicit channel mapping.
    """

    # --- top-level inputs ---
    ticket_key: str
    dry_run: bool

    # --- populated by WorkPlanner subgraph ---
    workflow_id: Optional[str]
    ticket: Optional[Any]  # dispatcher.jira_client.JiraTicket
    work_plan_data: Optional[dict]
    error: Optional[str]
    failed_node: Optional[str]  # set when a node fails so --retry can resume
    clarifications: Optional[list]  # accumulated Q&A rounds from reviewer

    # --- populated by await_approval node ---
    approval_decision: Optional[str]  # "approved" | "rejected"
    rejection_reason: Optional[str]  # only set when rejected

    # --- populated by PR approval loop ---
    pr_url: Optional[str]
    pr_comments: Optional[str]
    pr_approval_decision: Optional[str]  # "approved" | "rejected" | "commented"

    # --- populated by code_generator subgraph ---
    execution_summary: Optional[dict]  # summary written by code generator
