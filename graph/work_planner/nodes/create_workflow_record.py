"""Node: create_workflow_record — persist a PENDING workflow and mark it IN_PROGRESS."""

import click

from state.state_store import create_workflow, update_status
from state.workflow_status import WorkflowStatus
from graph.work_planner.state import WorkPlannerState


def create_workflow_record(state: WorkPlannerState) -> dict:
    ticket_key = state.get("ticket_key", "")
    # Use the pre-seeded workflow_id from run.py (which also serves as the
    # LangGraph thread_id) so both systems share a single identifier.
    pre_seeded_id = state.get("workflow_id")

    click.echo("📝 Creating workflow record...")
    workflow_id = create_workflow(
        ticket_key=ticket_key,
        work_plan=None,
        status=WorkflowStatus.PENDING,
        workflow_id=pre_seeded_id,
    )
    update_status(
        workflow_id,
        WorkflowStatus.IN_PROGRESS,
        actor="dispatcher",
        reason="Starting workflow execution",
    )
    click.echo(f"✅ Workflow created: {workflow_id}")
    return {"workflow_id": workflow_id}
