"""Node: error_handler — mark the workflow FAILED when a routing error occurs."""

from orchestrator.node_result import WorkPlannerNodeResult
from orchestrator.work_planner.state import ErrorHandlerInputState
from state.workflow_repository import update_status
from state.workflow_status import WorkflowStatus


def error_handler(state: ErrorHandlerInputState) -> WorkPlannerNodeResult:
    workflow_id = state.get("workflow_id")
    error = state.get("error") or "Unknown error"

    if workflow_id:
        update_status(
            workflow_id,
            WorkflowStatus.FAILED,
            actor="dispatcher",
            reason=error,
        )

    return {}
