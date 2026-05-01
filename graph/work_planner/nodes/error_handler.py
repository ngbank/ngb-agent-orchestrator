"""Node: error_handler — mark the workflow FAILED when a routing error occurs."""

from state.state_store import update_status
from state.workflow_status import WorkflowStatus
from graph.work_planner.state import WorkPlannerState


def error_handler(state: WorkPlannerState) -> dict:
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
