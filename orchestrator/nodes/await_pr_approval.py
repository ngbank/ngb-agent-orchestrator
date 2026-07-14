"""Node: await_pr_approval — pause the graph and wait for PR review signals."""

import logging

from langgraph.types import interrupt

from orchestrator.state import PRApprovalInputState, PRApprovalOutputState
from orchestrator.utils import _get_actor
from state.workflow_repository import get_workflow, update_pr_comments, update_status
from state.workflow_status import WorkflowStatus

logger = logging.getLogger(__name__)


def await_pr_approval(state: PRApprovalInputState) -> PRApprovalOutputState:
    """Interrupt the graph until the PR is approved, rejected, or commented on.

    On first entry (no prior decision in state):
      - Marks workflow as PENDING_PR_APPROVAL in the DB
      - Calls interrupt() to pause and serialise graph state to the checkpointer
      - Resumes when run.py calls graph.invoke(Command(resume=...))

    On resume:
      - Reads the decision dict injected by Command(resume=...)
      - Updates workflow status and writes to audit_log
      - Returns pr_approval_decision (and pr_comments) into state so the
        routing edge can direct the graph to END or back to generate_code
    """
    workflow_id = state.get("workflow_id")
    pr_url = state.get("pr_url")

    # Guard: if already approved (re-invoked run without restart), be a no-op.
    if workflow_id:
        workflow = get_workflow(workflow_id)
        if workflow and workflow["status"] == WorkflowStatus.COMPLETED:
            logger.info("PR already approved; workflow completed.")
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
    message = "⏸️  Pull request is ready for review.\n   Workflow ID: %s"
    args: tuple[str | None, ...]
    if pr_url:
        message += "\n   PR URL:      %s"
        args = (workflow_id, pr_url)
    else:
        args = (workflow_id,)
    message += (
        "\n   To approve:  dispatcher --approve-pr --ticket %s"
        "\n   To comment:  dispatcher --comment-pr --ticket %s"
        '\n   To reject:   dispatcher --reject-pr --ticket %s --reason "your reason"'
    )
    logger.info(message, *args, ticket_key, ticket_key, ticket_key)

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
            pr_approval_decision="approved",
        )
        logger.info("PR approved by %s", actor)
        return {"pr_approval_decision": "approved"}

    elif decision == "commented":
        update_status(
            workflow_id,
            WorkflowStatus.PR_COMMENTED,
            actor=actor,
            reason="PR commented on by reviewer",
            pr_approval_decision="commented",
        )
        if comments:
            update_pr_comments(workflow_id, comments, actor=actor)
        logger.info("PR commented on by %s", actor)
        return {"pr_approval_decision": "commented", "pr_comments": comments}

    else:  # rejected
        update_status(
            workflow_id,
            WorkflowStatus.REJECTED,
            actor=actor,
            reason=reason or "PR rejected by reviewer",
            pr_approval_decision="rejected",
        )
        if reason:
            logger.warning("PR rejected by %s: %s", actor, reason)
        else:
            logger.warning("PR rejected by %s", actor)
        return {"pr_approval_decision": "rejected", "pr_comments": comments}
