"""Node: create_workflow_record — persist a PENDING workflow and mark it IN_PROGRESS."""

import click

from state.state_store import create_workflow, update_status
from state.workflow_status import WorkflowStatus
from graph.work_planner.state import WorkPlannerState


def create_workflow_record(state: WorkPlannerState) -> dict:
    ticket_key = state.get("ticket_key", "")
    click.echo("📝 Creating workflow record...")
    workflow_id = create_workflow(
        ticket_key=ticket_key,
        work_plan=None,
        status=WorkflowStatus.PENDING,
    )
    update_status(
        workflow_id,
        WorkflowStatus.IN_PROGRESS,
        actor="dispatcher",
        reason="Starting workflow execution",
    )
    click.echo(f"✅ Workflow created: {workflow_id}")
    return {"workflow_id": workflow_id}
