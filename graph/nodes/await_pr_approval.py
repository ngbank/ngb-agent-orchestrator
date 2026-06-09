"""Node: await_pr_approval — pause the graph and wait for PR review signals."""

import click
from langgraph.types import interrupt

from graph.node_result import OrchestratorNodeResult
from graph.state import OrchestratorState
from graph.utils import _get_actor
from state.workflow_repository import get_workflow, update_pr_comments, update_status
from state.workflow_status import WorkflowStatus


def await_pr_approval(state: OrchestratorState) -> OrchestratorNodeResult:
    """Interrupt the graph until the PR is approved, rejected, or commented on.

    On first entry (no prior decision in state):
      - Marks workflow as PENDING_PR_APPROVAL in the DB
      - Calls interrupt() to pause and serialise graph state to the checkpointer
      - Resumes when run.py calls graph.invoke(Command(resume=...))

    On resume:
      - Reads the decision dict injected by Command(resume=...)
      - Updates workflow status and writes to audit_log
      - Returns pr_approval_decision (and pr_comments) into state so the
        routing edge can direct the graph to END or back to execute_plan
    """
    workflow_id = state.get("workflow_id")
    pr_url = state.get("pr_url")

    # Guard: if already approved (re-invoked run without restart), be a no-op.
    if workflow_id:
        workflow = get_workflow(workflow_id)
        if workflow and workflow["status"] == WorkflowStatus.COMPLETED:
            click.echo("ℹ️  PR already approved — workflow completed.")
            return {"pr_approval_decision": "approved"}

    # Mark as pending PR approval before suspending.
    if workflow_id:
        update_status(
            workflow_id,
            WorkflowStatus.PENDING_PR_APPROVAL,
            actor="dispatcher",
            reason="Awaiting PR review",
        )

    ticket_key = state.get("ticket_key", workflow_id)
    click.echo("")
    click.echo("⏸️  Pull request is ready for review.")
    click.echo(f"   Workflow ID: {workflow_id}")
    if pr_url:
        click.echo(f"   PR URL:      {pr_url}")
    click.echo("")
    click.echo(f"   To approve:  dispatcher --approve-pr --ticket {ticket_key}")
    click.echo(f"   To comment:  dispatcher --comment-pr --ticket {ticket_key}")
    reject_cmd = f"dispatcher --reject-pr --ticket {ticket_key}"
    click.echo(f'   To reject:   {reject_cmd} --reason "your reason"')
    click.echo("")

    # Suspend here — resumes when Command(resume={...}) is passed.
    resume_payload: dict = interrupt({"workflow_id": workflow_id, "pr_url": pr_url})

    decision = resume_payload.get("decision", "")
    comments = resume_payload.get("comments")
    reason = resume_payload.get("reason")
    actor = _get_actor()

    if not workflow_id:
        return {"pr_approval_decision": "rejected", "pr_comments": comments}

    if decision == "approved":
        update_status(
            workflow_id,
            WorkflowStatus.COMPLETED,
            actor=actor,
            reason="PR approved by reviewer",
        )
        click.echo(f"✅ PR approved by {actor}")
        return {"pr_approval_decision": "approved"}

    elif decision == "commented":
        update_status(
            workflow_id,
            WorkflowStatus.PR_COMMENTED,
            actor=actor,
            reason="PR commented on by reviewer",
        )
        if comments:
            update_pr_comments(workflow_id, comments, actor=actor)
        click.echo(f"💬 PR commented on by {actor}")
        return {"pr_approval_decision": "commented", "pr_comments": comments}

    else:  # rejected
        update_status(
            workflow_id,
            WorkflowStatus.REJECTED,
            actor=actor,
            reason=reason or "PR rejected by reviewer",
        )
        click.echo(f"🚫 PR rejected by {actor}" + (f": {reason}" if reason else ""))
        return {"pr_approval_decision": "rejected", "pr_comments": comments}
