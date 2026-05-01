"""Node: await_approval — pause the graph and wait for a CLI approve/reject signal."""

import getpass

import click
from langgraph.types import interrupt

from state.state_store import update_status, get_workflow, _create_audit_log, get_connection
from state.workflow_status import WorkflowStatus
from graph.state import OrchestratorState


def await_approval(state: OrchestratorState) -> dict:
    """Interrupt the graph until the developer explicitly approves or rejects.

    On first entry (no prior decision in state):
      - Marks workflow as PENDING_APPROVAL in the DB
      - Calls interrupt() to pause and serialise graph state to the checkpointer
      - Resumes when run.py calls graph.invoke(Command(resume=...))

    On resume:
      - Reads the decision dict injected by Command(resume=...)
      - Updates workflow status and writes to audit_log
      - Returns approval_decision (and rejection_reason) into state so the
        routing edge can direct the graph to execute_plan or END
    """
    workflow_id = state.get("workflow_id")

    # Guard: if already approved (re-invoked run without restart), be a no-op.
    if workflow_id:
        workflow = get_workflow(workflow_id)
        if workflow and workflow["status"] == WorkflowStatus.APPROVED:
            click.echo("ℹ️  WorkPlan already approved — continuing to execution.")
            return {"approval_decision": "approved"}

    # Mark as pending approval before suspending.
    if workflow_id:
        update_status(
            workflow_id,
            WorkflowStatus.PENDING_APPROVAL,
            actor="dispatcher",
            reason="Awaiting developer approval",
        )

    ticket_key = state.get("ticket_key", workflow_id)
    click.echo("")
    click.echo("⏸️  WorkPlan is ready for review.")
    click.echo(f"   Workflow ID: {workflow_id}")
    click.echo("")
    click.echo(f"   To approve:  python -m dispatcher.run --approve --ticket {ticket_key}")
    click.echo(f"   To reject:   python -m dispatcher.run --reject --ticket {ticket_key}" + ' --reason "your reason"')
    click.echo("")

    # Suspend here — resumes when Command(resume={"decision": ..., "reason": ...}) is passed.
    resume_payload: dict = interrupt({"workflow_id": workflow_id})

    decision = resume_payload.get("decision", "")
    reason = resume_payload.get("reason")
    actor = _get_actor()

    if decision == "approved":
        update_status(
            workflow_id,
            WorkflowStatus.APPROVED,
            actor=actor,
            reason="WorkPlan approved by developer",
        )
        click.echo(f"✅ WorkPlan approved by {actor}")
        return {"approval_decision": "approved"}

    else:  # rejected
        _write_rejection_audit(workflow_id, actor, reason)
        update_status(
            workflow_id,
            WorkflowStatus.REJECTED,
            actor=actor,
            reason=reason or "WorkPlan rejected by developer",
        )
        click.echo(f"🚫 WorkPlan rejected by {actor}" + (f": {reason}" if reason else ""))
        return {"approval_decision": "rejected", "rejection_reason": reason}


def _get_actor() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


def _write_rejection_audit(workflow_id: str, actor: str, reason: str | None) -> None:
    conn = get_connection()
    try:
        _create_audit_log(
            conn,
            workflow_id=workflow_id,
            actor=actor,
            action="workflow_rejected",
            reason=reason or "No reason provided",
        )
        conn.commit()
    finally:
        conn.close()
