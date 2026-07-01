"""Node: await_approval — pause the graph and wait for a CLI approve/reject signal."""

import logging

from langgraph.types import interrupt

from orchestrator.state import ApprovalInputState, ApprovalOutputState
from orchestrator.utils import _get_actor
from state.workflow_repository import get_workflow, update_status
from state.workflow_status import WorkflowStatus

logger = logging.getLogger(__name__)


def await_approval(state: ApprovalInputState) -> ApprovalOutputState:
    """Interrupt the graph until the developer explicitly approves or rejects.

    On first entry (no prior decision in state):
      - Marks workflow as PENDING_APPROVAL in the DB
      - Calls interrupt() to pause and serialise graph state to the checkpointer
      - Resumes when run.py calls graph.invoke(Command(resume=...))

    On resume:
      - Reads the decision dict injected by Command(resume=...)
      - Updates workflow status and writes to audit_log
      - Returns approval_decision (and rejection_reason) into state so the
        routing edge can direct the graph to generate_code or END
    """
    workflow_id = state.get("workflow_id")

    # Guard: if already approved (re-invoked run without restart), be a no-op.
    if workflow_id:
        workflow = get_workflow(workflow_id)
        if workflow and workflow["status"] == WorkflowStatus.APPROVED:
            logger.info("WorkPlan already approved; continuing to execution.")
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
    logger.info(
        "⏸️  WorkPlan is ready for review.\n"
        "   Workflow ID: %s\n"
        "   To approve:  dispatcher --approve-plan --ticket %s\n"
        '   To reject:   dispatcher --reject --ticket %s --reason "your reason"',
        workflow_id,
        ticket_key,
        ticket_key,
    )

    # Suspend here — resumes when Command(resume={"decision": ..., "reason": ...}) is passed.
    resume_payload: dict = interrupt({"workflow_id": workflow_id})

    decision = resume_payload.get("decision", "")
    reason = resume_payload.get("reason")
    actor = _get_actor()

    if not workflow_id:
        return {"approval_decision": "rejected", "rejection_reason": "missing workflow_id"}

    if decision == "approved":
        update_status(
            workflow_id,
            WorkflowStatus.APPROVED,
            actor=actor,
            reason="WorkPlan approved by developer",
        )
        logger.info("WorkPlan approved by %s", actor)
        return {"approval_decision": "approved"}

    else:  # rejected
        update_status(
            workflow_id,
            WorkflowStatus.REJECTED,
            actor=actor,
            reason=reason or "WorkPlan rejected by developer",
        )
        if reason:
            logger.warning("WorkPlan rejected by %s: %s", actor, reason)
        else:
            logger.warning("WorkPlan rejected by %s", actor)
        return {"approval_decision": "rejected", "rejection_reason": reason}
