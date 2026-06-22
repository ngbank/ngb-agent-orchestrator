"""REST routes for the orchestrator HTTP server.

All routes depend on:

* ``get_service`` — supplies the :class:`WorkflowService` to call.
* ``require_bearer_token`` — enforces the auth stub (no-op when the
  ``ORCHESTRATOR_API_TOKEN`` env var is unset).

The router only translates between HTTP and ``WorkflowService`` — every
behavioural detail (status updates, post-processing) lives in the service.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse

from orchestrator.workflow_service import WorkflowService

from .auth import require_admin_token, require_bearer_token
from .deps import get_service
from .schemas import (
    CancelWorkflowRequest,
    ClearDbResponse,
    CommentPrRequest,
    HealthResponse,
    MarkInterruptedRequest,
    RejectPlanRequest,
    RejectPrRequest,
    StartWorkflowRequest,
    SubmitClarificationRequest,
    WorkflowAuditEntryResponse,
    WorkflowDetailResponse,
    WorkflowHistoryEntryResponse,
    WorkflowRunResponse,
    WorkflowSummaryResponse,
    parse_status,
)
from .sse import parse_last_event_id, stream_events_sse, stream_logs_sse

# Shared headers for all SSE responses — disables proxy buffering so events
# reach the client immediately.
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}
_SSE_MEDIA_TYPE = "text/event-stream"

# ---------------------------------------------------------------------------
# Unauthenticated routes (health + version-style probes)
# ---------------------------------------------------------------------------

health_router = APIRouter(tags=["health"])


@health_router.get("/healthz", response_model=HealthResponse)
def healthz() -> HealthResponse:
    """Liveness probe — always returns 200 when the process is up."""
    return HealthResponse()


# ---------------------------------------------------------------------------
# Workflow routes (auth-gated)
# ---------------------------------------------------------------------------

workflow_router = APIRouter(
    prefix="/workflows",
    tags=["workflows"],
    dependencies=[Depends(require_bearer_token)],
)


@workflow_router.post(
    "",
    response_model=WorkflowRunResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start a new workflow",
)
def start_workflow(
    body: StartWorkflowRequest,
    service: WorkflowService = Depends(get_service),
) -> WorkflowRunResponse:
    result = service.start(body.to_dto())
    return WorkflowRunResponse.from_dto(result)


@workflow_router.get(
    "",
    response_model=List[WorkflowSummaryResponse],
    summary="List workflows",
)
def list_workflows(
    ticket_key: Optional[str] = Query(default=None),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=500),
    service: WorkflowService = Depends(get_service),
) -> List[WorkflowSummaryResponse]:
    try:
        parsed = parse_status(status_filter)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    summaries = service.list(ticket_key=ticket_key, status=parsed, limit=limit)
    return [WorkflowSummaryResponse.from_dto(s) for s in summaries]


@workflow_router.get(
    "/{workflow_id}",
    response_model=WorkflowDetailResponse,
    summary="Get a workflow by id",
)
def get_workflow(
    workflow_id: str,
    service: WorkflowService = Depends(get_service),
) -> WorkflowDetailResponse:
    detail = service.get(workflow_id)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow not found: {workflow_id}",
        )
    return WorkflowDetailResponse.from_dto(detail)


@workflow_router.post(
    "/{workflow_id}/cancel",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cancel an in-flight workflow",
    responses={
        404: {"description": "Workflow not found"},
        409: {"description": "Workflow already terminal"},
    },
)
def cancel_workflow(
    workflow_id: str,
    body: Optional[CancelWorkflowRequest] = None,
    service: WorkflowService = Depends(get_service),
) -> Response:
    detail = service.get(workflow_id)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow not found: {workflow_id}",
        )
    if detail.status.is_terminal():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Workflow {workflow_id} is already terminal " f"(status: {detail.status.value})"
            ),
        )
    payload = body if body is not None else CancelWorkflowRequest.model_validate({})
    service.cancel(workflow_id, reason=payload.reason, actor=payload.actor)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Mutating routes: approval / clarification / retry
# ---------------------------------------------------------------------------


def _require_workflow(service: WorkflowService, workflow_id: str) -> None:
    """Raise 404 when ``workflow_id`` is unknown."""
    if service.get(workflow_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow not found: {workflow_id}",
        )


def _service_value_error_to_409(exc: ValueError) -> HTTPException:
    """Map a ``ValueError`` raised by a graph-running service method to 409.

    The local service raises ``ValueError`` for invalid state transitions
    (e.g. retry on a non-retryable workflow); surface those as 409 Conflict
    so the HTTP client can distinguish them from transport/auth errors.
    """
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


_MUTATION_RESPONSES: Dict[int | str, Dict[str, Any]] = {
    404: {"description": "Workflow not found"},
    409: {"description": "Workflow is in an incompatible state for this action"},
}


@workflow_router.post(
    "/{workflow_id}/approve-plan",
    response_model=WorkflowRunResponse,
    summary="Approve a paused WorkPlan and resume the workflow",
    responses=_MUTATION_RESPONSES,
)
def approve_plan(
    workflow_id: str,
    service: WorkflowService = Depends(get_service),
) -> WorkflowRunResponse:
    _require_workflow(service, workflow_id)
    try:
        result = service.approve_plan(workflow_id)
    except ValueError as exc:
        raise _service_value_error_to_409(exc) from exc
    return WorkflowRunResponse.from_dto(result)


@workflow_router.post(
    "/{workflow_id}/reject-plan",
    response_model=WorkflowRunResponse,
    summary="Reject a paused WorkPlan and resume the workflow",
    responses=_MUTATION_RESPONSES,
)
def reject_plan(
    workflow_id: str,
    body: Optional[RejectPlanRequest] = None,
    service: WorkflowService = Depends(get_service),
) -> WorkflowRunResponse:
    _require_workflow(service, workflow_id)
    reason = body.reason if body is not None else None
    try:
        result = service.reject_plan(workflow_id, reason)
    except ValueError as exc:
        raise _service_value_error_to_409(exc) from exc
    return WorkflowRunResponse.from_dto(result)


@workflow_router.post(
    "/{workflow_id}/clarification",
    response_model=WorkflowRunResponse,
    summary="Submit clarification answers and resume the workflow",
    responses=_MUTATION_RESPONSES,
)
def submit_clarification(
    workflow_id: str,
    body: SubmitClarificationRequest,
    service: WorkflowService = Depends(get_service),
) -> WorkflowRunResponse:
    _require_workflow(service, workflow_id)
    answers = [a.model_dump() for a in body.answers]
    try:
        result = service.submit_clarification(workflow_id, answers)
    except ValueError as exc:
        raise _service_value_error_to_409(exc) from exc
    return WorkflowRunResponse.from_dto(result)


@workflow_router.post(
    "/{workflow_id}/retry",
    response_model=WorkflowRunResponse,
    summary="Retry a failed / interrupted workflow from its failed_node",
    responses=_MUTATION_RESPONSES,
)
def retry_workflow(
    workflow_id: str,
    service: WorkflowService = Depends(get_service),
) -> WorkflowRunResponse:
    _require_workflow(service, workflow_id)
    try:
        result = service.retry(workflow_id)
    except ValueError as exc:
        raise _service_value_error_to_409(exc) from exc
    return WorkflowRunResponse.from_dto(result)


# ---------------------------------------------------------------------------
# Mutating routes: PR review flow
# ---------------------------------------------------------------------------


@workflow_router.post(
    "/{workflow_id}/approve-pr",
    response_model=WorkflowRunResponse,
    summary="Approve the workflow's PR and mark it COMPLETED",
    responses=_MUTATION_RESPONSES,
)
def approve_pr(
    workflow_id: str,
    service: WorkflowService = Depends(get_service),
) -> WorkflowRunResponse:
    _require_workflow(service, workflow_id)
    try:
        result = service.approve_pr(workflow_id)
    except ValueError as exc:
        raise _service_value_error_to_409(exc) from exc
    return WorkflowRunResponse.from_dto(result)


@workflow_router.post(
    "/{workflow_id}/reject-pr",
    response_model=WorkflowRunResponse,
    summary="Reject the workflow's PR and mark it REJECTED",
    responses=_MUTATION_RESPONSES,
)
def reject_pr(
    workflow_id: str,
    body: Optional[RejectPrRequest] = None,
    service: WorkflowService = Depends(get_service),
) -> WorkflowRunResponse:
    _require_workflow(service, workflow_id)
    reason = body.reason if body is not None else None
    try:
        result = service.reject_pr(workflow_id, reason)
    except ValueError as exc:
        raise _service_value_error_to_409(exc) from exc
    return WorkflowRunResponse.from_dto(result)


@workflow_router.post(
    "/{workflow_id}/comment-pr",
    response_model=WorkflowRunResponse,
    summary="Post review comments on the workflow's PR and resume",
    responses=_MUTATION_RESPONSES,
)
def comment_pr(
    workflow_id: str,
    body: CommentPrRequest,
    service: WorkflowService = Depends(get_service),
) -> WorkflowRunResponse:
    _require_workflow(service, workflow_id)
    try:
        result = service.comment_pr(workflow_id, body.comments)
    except ValueError as exc:
        raise _service_value_error_to_409(exc) from exc
    return WorkflowRunResponse.from_dto(result)


# ---------------------------------------------------------------------------
# Read routes: history + audit log
# ---------------------------------------------------------------------------


@workflow_router.get(
    "/{workflow_id}/history",
    response_model=List[WorkflowHistoryEntryResponse],
    summary="Return the node traversal history for a workflow",
    responses={404: {"description": "Workflow not found"}},
)
def get_workflow_history(
    workflow_id: str,
    service: WorkflowService = Depends(get_service),
) -> List[WorkflowHistoryEntryResponse]:
    _require_workflow(service, workflow_id)
    entries = service.get_history(workflow_id)
    return [WorkflowHistoryEntryResponse.from_dto(e) for e in entries]


@workflow_router.get(
    "/{workflow_id}/audit-log",
    response_model=List[WorkflowAuditEntryResponse],
    summary="Return the audit log entries for a workflow",
    responses={404: {"description": "Workflow not found"}},
)
def get_workflow_audit_log(
    workflow_id: str,
    service: WorkflowService = Depends(get_service),
) -> List[WorkflowAuditEntryResponse]:
    _require_workflow(service, workflow_id)
    entries = service.get_audit_log(workflow_id)
    return [WorkflowAuditEntryResponse.from_dto(e) for e in entries]


# ---------------------------------------------------------------------------
# Streaming routes (SSE)
# ---------------------------------------------------------------------------


@workflow_router.get(
    "/{workflow_id}/events",
    summary="Stream workflow lifecycle events (SSE)",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {_SSE_MEDIA_TYPE: {}},
            "description": "SSE stream of workflow events; closes on terminal status.",
        },
        404: {"description": "Workflow not found"},
    },
)
def stream_workflow_events(
    workflow_id: str,
    after_seq: int = Query(default=0, ge=0),
    last_event_id: Optional[str] = Header(default=None, alias="Last-Event-ID"),
    service: WorkflowService = Depends(get_service),
) -> StreamingResponse:
    if service.get(workflow_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow not found: {workflow_id}",
        )
    # ``after_seq`` query param wins; fall back to ``Last-Event-ID`` only when
    # the client did not supply an explicit value.  EventSource clients set
    # the header automatically on reconnect.
    resume_seq = after_seq
    if resume_seq == 0:
        parsed = parse_last_event_id(last_event_id)
        if parsed is not None:
            resume_seq = parsed
    return StreamingResponse(
        stream_events_sse(service, workflow_id, after_seq=resume_seq),
        media_type=_SSE_MEDIA_TYPE,
        headers=_SSE_HEADERS,
    )


@workflow_router.get(
    "/{workflow_id}/logs",
    summary="Stream workflow log content (SSE)",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {_SSE_MEDIA_TYPE: {}},
            "description": "SSE stream of log chunks; closes on terminal status.",
        },
        404: {"description": "Workflow not found"},
    },
)
def stream_workflow_logs(
    workflow_id: str,
    after_offset: int = Query(default=0, ge=0),
    stage: Optional[str] = Query(default=None),
    last_event_id: Optional[str] = Header(default=None, alias="Last-Event-ID"),
    service: WorkflowService = Depends(get_service),
) -> StreamingResponse:
    if service.get(workflow_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow not found: {workflow_id}",
        )
    resume_offset = after_offset
    if resume_offset == 0:
        parsed = parse_last_event_id(last_event_id)
        if parsed is not None:
            resume_offset = parsed
    return StreamingResponse(
        stream_logs_sse(service, workflow_id, stage=stage, after_offset=resume_offset),
        media_type=_SSE_MEDIA_TYPE,
        headers=_SSE_HEADERS,
    )


# ---------------------------------------------------------------------------
# Admin routes
#
# Gated by :func:`require_admin_token` — these refuse to run unless the
# server has an ``ORCHESTRATOR_API_TOKEN`` configured (503 otherwise), and
# require a matching bearer token when it is.  ``mark_interrupted`` lives
# here rather than on the workflow router so the auth posture is uniform.
# ---------------------------------------------------------------------------


admin_router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin_token)],
)


@admin_router.post(
    "/clear-db",
    response_model=ClearDbResponse,
    summary="Wipe all workflows and LangGraph checkpoints",
)
def clear_db(
    service: WorkflowService = Depends(get_service),
) -> ClearDbResponse:
    workflows, checkpoints = service.clear_db()
    return ClearDbResponse(workflows=workflows, checkpoints=checkpoints)


@admin_router.post(
    "/workflows/{workflow_id}/mark-interrupted",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Mark an in-flight workflow as FAILED after an interrupt",
    responses={404: {"description": "Workflow not found"}},
)
def mark_interrupted(
    workflow_id: str,
    body: Optional[MarkInterruptedRequest] = None,
    service: WorkflowService = Depends(get_service),
) -> Response:
    _require_workflow(service, workflow_id)
    payload = body if body is not None else MarkInterruptedRequest.model_validate({})
    service.mark_interrupted(
        workflow_id,
        failed_node=payload.failed_node,
        actor=payload.actor,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
