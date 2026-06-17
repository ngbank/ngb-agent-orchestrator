"""Node: create_workflow_record — persist a PENDING workflow and mark it IN_PROGRESS."""

import click

from orchestrator.work_planner.state import (
    CreateWorkflowRecordInputState,
    CreateWorkflowRecordOutputState,
)
from state.workflow_repository import create_workflow, get_workflow, update_status
from state.workflow_status import WorkflowStatus


def create_workflow_record(
    state: CreateWorkflowRecordInputState,
) -> CreateWorkflowRecordOutputState:
    ticket_key = state.get("ticket_key", "")
    # Use the pre-seeded workflow_id from run.py (which also serves as the
    # LangGraph thread_id) so both systems share a single identifier.
    pre_seeded_id = state.get("workflow_id")

    # Idempotent on retry: if a workflow row already exists for this id
    # (e.g., a previous run created it and is now being resumed), reuse it
    # and just ensure status is IN_PROGRESS.
    if pre_seeded_id and get_workflow(pre_seeded_id) is not None:
        click.echo(f"♻️  Reusing existing workflow record: {pre_seeded_id}")
        update_status(
            pre_seeded_id,
            WorkflowStatus.IN_PROGRESS,
            actor="dispatcher",
            reason="Resuming workflow execution",
        )
        return {"workflow_id": pre_seeded_id}

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
