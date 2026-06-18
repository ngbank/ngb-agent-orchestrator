"""
WorkPlanner subgraph state with per-node focused TypedDicts.

Following the Interface Segregation Principle, each node within the work planner
has a focused TypedDict that declares only the keys it reads and writes.
WorkPlannerState is the composition of all these TypedDicts for the subgraph.
"""

from typing import Any, Optional

from typing_extensions import TypedDict


class ValidateInputInputState(TypedDict):
    """Input required by validate_input node."""

    ticket_key: str


class ValidateInputOutputState(TypedDict, total=False):
    """Output produced by validate_input node (on error)."""

    error: Optional[str]
    failed_node: Optional[str]


class CheckDuplicateInputState(TypedDict):
    """Input required by check_duplicate node."""

    ticket_key: str
    workflow_id: Optional[str]


class CheckDuplicateOutputState(TypedDict, total=False):
    """Output produced by check_duplicate node (on error)."""

    error: Optional[str]
    failed_node: Optional[str]


class CreateWorkflowRecordInputState(TypedDict):
    """Input required by create_workflow_record node."""

    ticket_key: str
    workflow_id: Optional[str]


class CreateWorkflowRecordOutputState(TypedDict, total=False):
    """Output produced by create_workflow_record node."""

    workflow_id: Optional[str]


class FetchTicketInputState(TypedDict):
    """Input required by fetch_ticket node."""

    ticket_key: str


class FetchTicketOutputState(TypedDict, total=False):
    """Output produced by fetch_ticket node."""

    ticket: Optional[Any]  # dispatcher.jira_client.JiraTicket


class GeneratePlanInputState(TypedDict, total=False):
    """Input required by generate_plan node."""

    ticket_key: str
    workflow_id: Optional[str]
    clarifications: Optional[list]


class GeneratePlanOutputState(TypedDict, total=False):
    """Output produced by generate_plan node."""

    work_plan_data: Optional[dict]
    error: Optional[str]
    failed_node: Optional[str]


class ValidatePlanInputState(TypedDict, total=False):
    """Input required by validate_plan node."""

    work_plan_data: Optional[dict]


class ValidatePlanOutputState(TypedDict, total=False):
    """Output produced by validate_plan node (on error)."""

    error: Optional[str]
    failed_node: Optional[str]


class AwaitClarificationInputState(TypedDict, total=False):
    """Input required by await_workplan_clarification node."""

    workflow_id: Optional[str]
    work_plan_data: Optional[dict]
    clarifications: Optional[list]
    ticket_key: str


class AwaitClarificationOutputState(TypedDict, total=False):
    """Output produced by await_workplan_clarification node (on error)."""

    clarifications: Optional[list]
    work_plan_data: Optional[dict]
    error: Optional[str]


class StorePlanInputState(TypedDict, total=False):
    """Input required by store_plan node."""

    work_plan_data: Optional[dict]
    workflow_id: Optional[str]
    ticket_key: str


class PostToJiraInputState(TypedDict, total=False):
    """Input required by post_to_jira node."""

    work_plan_data: Optional[dict]
    ticket_key: str


class ErrorHandlerInputState(TypedDict, total=False):
    """Input required by error_handler node."""

    workflow_id: Optional[str]
    error: Optional[str]


class WorkPlannerState(TypedDict, total=False):
    """
    WorkPlanner subgraph state.

    Composition of all per-node TypedDicts for the subgraph. Each node is
    annotated with its narrower input/output types for IDE and mypy visibility,
    even though the runtime type remains WorkPlannerState.

    WorkPlannerState is a subset of OrchestratorState. All keys exist on both
    TypedDicts so that LangGraph can route state through the subgraph boundary
    without an explicit input/output channel mapping.
    """

    # --- shared with OrchestratorState ---
    ticket_key: str
    dry_run: bool
    workflow_id: Optional[str]
    ticket: Optional[Any]  # dispatcher.jira_client.JiraTicket
    work_plan_data: Optional[dict]
    error: Optional[str]
    failed_node: Optional[str]  # set when a node fails so --retry can resume
    clarifications: Optional[list]  # accumulated Q&A rounds from reviewer
