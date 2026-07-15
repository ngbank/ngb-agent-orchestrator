"""Human-decision gate routes and retry.

Every route in this module resumes a paused workflow (or rewinds a
failed one) and dispatches the graph drive to the background dispatcher.
The six gate-resume routes share the ``_require_paused_at_gate`` guard
so a wrong-verb-for-gate request never reaches the service; ``/retry``
uses the same ``_GATE_RESUME_ENDPOINT`` hint table.
"""

from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, status

from orchestrator.workflow_service import WorkflowService
from state.workflow_status import WorkflowStatus

from ..background import BackgroundDispatcherProtocol
from ..deps import get_background_dispatcher, get_service
from ..schemas import (
    CommentPrRequest,
    RejectPlanRequest,
    RejectPrRequest,
    SubmitClarificationRequest,
    WorkflowRunResponse,
)
from ._shared import (
    _GATE_RESUME_ENDPOINT,
    _MUTATION_RESPONSES,
    _require_paused_at_gate,
    _snapshot_response,
    _submit_graph_drive,
    workflow_router,
)

# ---------------------------------------------------------------------------
# WorkPlan approval / clarification
# ---------------------------------------------------------------------------


@workflow_router.post(
    "/{workflow_id}/approve-plan",
    response_model=WorkflowRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Approve a paused WorkPlan and resume the workflow",
    responses=_MUTATION_RESPONSES,
)
def approve_plan(
    workflow_id: str,
    service: WorkflowService = Depends(get_service),
    dispatcher: BackgroundDispatcherProtocol = Depends(get_background_dispatcher),
) -> WorkflowRunResponse:
    _require_paused_at_gate(service, workflow_id, WorkflowStatus.PENDING_APPROVAL)
    _submit_graph_drive(
        dispatcher=dispatcher,
        service=service,
        workflow_id=workflow_id,
        op_name="approve_plan",
        fn=service.approve_plan,
        args=(workflow_id,),
    )
    return _snapshot_response(service, workflow_id)


@workflow_router.post(
    "/{workflow_id}/reject-plan",
    response_model=WorkflowRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Reject a paused WorkPlan and resume the workflow",
    responses=_MUTATION_RESPONSES,
)
def reject_plan(
    workflow_id: str,
    body: Optional[RejectPlanRequest] = None,
    service: WorkflowService = Depends(get_service),
    dispatcher: BackgroundDispatcherProtocol = Depends(get_background_dispatcher),
) -> WorkflowRunResponse:
    _require_paused_at_gate(service, workflow_id, WorkflowStatus.PENDING_APPROVAL)
    reason = body.reason if body is not None else None
    _submit_graph_drive(
        dispatcher=dispatcher,
        service=service,
        workflow_id=workflow_id,
        op_name="reject_plan",
        fn=service.reject_plan,
        args=(workflow_id, reason),
    )
    return _snapshot_response(service, workflow_id)


@workflow_router.post(
    "/{workflow_id}/clarification",
    response_model=WorkflowRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit clarification answers and resume the workflow",
    responses=_MUTATION_RESPONSES,
)
def submit_clarification(
    workflow_id: str,
    body: SubmitClarificationRequest,
    service: WorkflowService = Depends(get_service),
    dispatcher: BackgroundDispatcherProtocol = Depends(get_background_dispatcher),
) -> WorkflowRunResponse:
    _require_paused_at_gate(service, workflow_id, WorkflowStatus.PENDING_WORKPLAN_CLARIFICATION)
    answers = [a.model_dump() for a in body.answers]
    _submit_graph_drive(
        dispatcher=dispatcher,
        service=service,
        workflow_id=workflow_id,
        op_name="submit_clarification",
        fn=service.submit_clarification,
        args=(workflow_id, answers),
    )
    return _snapshot_response(service, workflow_id)


# ---------------------------------------------------------------------------
# Retry
# ---------------------------------------------------------------------------


@workflow_router.post(
    "/{workflow_id}/retry",
    response_model=WorkflowRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Retry a failed / interrupted workflow from its failed_node",
    responses=_MUTATION_RESPONSES,
)
def retry_workflow(
    workflow_id: str,
    service: WorkflowService = Depends(get_service),
    dispatcher: BackgroundDispatcherProtocol = Depends(get_background_dispatcher),
) -> WorkflowRunResponse:
    detail = service.get(workflow_id)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow not found: {workflow_id}",
        )
    if detail.status.is_paused_at_gate():
        # A gate-paused workflow is not stuck — it is waiting for a human
        # decision.  Retrying it would rewind past the gate node and
        # silently skip the decision (this was the AOS-280 root cause).
        # Point the caller at the correct resume endpoint instead.
        resume_hint = _GATE_RESUME_ENDPOINT.get(detail.status, "the matching decision endpoint")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Workflow {workflow_id} is paused at a human-decision gate "
                f"(status: {detail.status.value}) and cannot be retried. "
                f"Use {resume_hint} instead."
            ),
        )
    if not detail.status.is_retryable():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Workflow {workflow_id} is in status {detail.status.value} "
                "and cannot be retried."
            ),
        )
    _submit_graph_drive(
        dispatcher=dispatcher,
        service=service,
        workflow_id=workflow_id,
        op_name="retry",
        fn=service.retry,
        args=(workflow_id,),
    )
    return _snapshot_response(service, workflow_id)


# ---------------------------------------------------------------------------
# PR review flow
# ---------------------------------------------------------------------------


@workflow_router.post(
    "/{workflow_id}/approve-pr",
    response_model=WorkflowRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Approve the workflow's PR and mark it COMPLETED",
    responses=_MUTATION_RESPONSES,
)
def approve_pr(
    workflow_id: str,
    service: WorkflowService = Depends(get_service),
    dispatcher: BackgroundDispatcherProtocol = Depends(get_background_dispatcher),
) -> WorkflowRunResponse:
    _require_paused_at_gate(service, workflow_id, WorkflowStatus.PENDING_PR_APPROVAL)
    _submit_graph_drive(
        dispatcher=dispatcher,
        service=service,
        workflow_id=workflow_id,
        op_name="approve_pr",
        fn=service.approve_pr,
        args=(workflow_id,),
    )
    return _snapshot_response(service, workflow_id)


@workflow_router.post(
    "/{workflow_id}/reject-pr",
    response_model=WorkflowRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Reject the workflow's PR and mark it REJECTED",
    responses=_MUTATION_RESPONSES,
)
def reject_pr(
    workflow_id: str,
    body: Optional[RejectPrRequest] = None,
    service: WorkflowService = Depends(get_service),
    dispatcher: BackgroundDispatcherProtocol = Depends(get_background_dispatcher),
) -> WorkflowRunResponse:
    _require_paused_at_gate(service, workflow_id, WorkflowStatus.PENDING_PR_APPROVAL)
    reason = body.reason if body is not None else None
    _submit_graph_drive(
        dispatcher=dispatcher,
        service=service,
        workflow_id=workflow_id,
        op_name="reject_pr",
        fn=service.reject_pr,
        args=(workflow_id, reason),
    )
    return _snapshot_response(service, workflow_id)


@workflow_router.post(
    "/{workflow_id}/comment-pr",
    response_model=WorkflowRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Post review comments on the workflow's PR and resume",
    responses=_MUTATION_RESPONSES,
)
def comment_pr(
    workflow_id: str,
    body: CommentPrRequest,
    service: WorkflowService = Depends(get_service),
    dispatcher: BackgroundDispatcherProtocol = Depends(get_background_dispatcher),
) -> WorkflowRunResponse:
    _require_paused_at_gate(service, workflow_id, WorkflowStatus.PENDING_PR_APPROVAL)
    _submit_graph_drive(
        dispatcher=dispatcher,
        service=service,
        workflow_id=workflow_id,
        op_name="comment_pr",
        fn=service.comment_pr,
        args=(workflow_id, body.comments),
    )
    return _snapshot_response(service, workflow_id)
