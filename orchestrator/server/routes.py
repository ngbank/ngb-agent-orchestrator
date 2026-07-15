"""REST routes for the orchestrator HTTP server.

All routes depend on:

* ``get_service`` — supplies the :class:`WorkflowService` to call.
* ``get_background_dispatcher`` — supplies the worker pool that runs
  graph-running operations off the request thread (fire-and-forget).
* ``require_bearer_token`` — enforces the auth stub (no-op when the
  ``ORCHESTRATOR_API_TOKEN`` env var is unset).

Graph-running mutating routes (``start``, ``approve_plan``, ``comment_pr``
…) return ``202 Accepted`` immediately and dispatch the actual graph drive
to the background dispatcher.  Clients observe progress via
``/workflows/{id}/events`` (SSE) and ``/workflows/{id}`` (snapshot).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse

from orchestrator.workflow_service import WorkflowService
from orchestrator.workflow_service.dtos import WorkflowRunResult
from state.workflow_status import WorkflowStatus

from .auth import require_admin_token, require_bearer_token
from .background import BackgroundDispatcherProtocol
from .deps import get_background_dispatcher, get_service
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

logger = logging.getLogger(__name__)

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
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start a new workflow (fire-and-forget)",
    responses={
        409: {
            "description": (
                "A graph drive is already in flight for this workflow id, "
                "or the dispatcher is shut down."
            )
        },
    },
)
def start_workflow(
    body: StartWorkflowRequest,
    service: WorkflowService = Depends(get_service),
    dispatcher: BackgroundDispatcherProtocol = Depends(get_background_dispatcher),
) -> WorkflowRunResponse:
    request = body.to_dto()

    # Dry-run is a no-op at the service layer and the test path; return a
    # synchronous placeholder so callers can verify routing without
    # spinning up a graph.
    if request.dry_run:
        return WorkflowRunResponse.from_dto(service.start(request))

    # Reserve the workflow id + create the PENDING row synchronously so
    # ``GET /workflows/{id}`` works immediately after this returns.
    prepared = service.prepare_start(request)  # type: ignore[attr-defined]
    workflow_id = prepared.workflow_id or ""

    _submit_graph_drive(
        dispatcher=dispatcher,
        service=service,
        workflow_id=workflow_id,
        op_name="start",
        fn=service.start,
        args=(prepared,),
    )

    detail = service.get(workflow_id)
    snapshot_status = detail.status if detail else WorkflowStatus.PENDING
    return WorkflowRunResponse.from_dto(
        WorkflowRunResult(
            workflow_id=workflow_id,
            ticket_key=request.ticket_key,
            final_status=snapshot_status,
        )
    )


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
    dispatcher: BackgroundDispatcherProtocol = Depends(get_background_dispatcher),
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
    dispatcher.cancel(workflow_id)
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


def _submit_graph_drive(
    *,
    dispatcher: BackgroundDispatcherProtocol,
    service: WorkflowService,
    workflow_id: str,
    op_name: str,
    fn: Any,
    args: tuple = (),
    kwargs: Optional[Dict[str, Any]] = None,
) -> None:
    """Submit ``fn(*args, **kwargs)`` to the background dispatcher.

    Wraps the call in a failure callback that marks the workflow ``FAILED``
    if the graph drive raises uncaught.  Raises ``HTTPException(409)`` if
    the dispatcher is already running a job for ``workflow_id`` or has
    been shut down.
    """

    call_kwargs = kwargs or {}

    def _on_failure(exc: BaseException) -> None:
        reason = f"{op_name} raised: {type(exc).__name__}: {exc}"
        try:
            service.mark_failed(
                workflow_id,
                reason,
                actor="background-dispatcher",
            )
        except Exception:  # pragma: no cover - defensive
            logger.exception(
                "Failed to mark workflow %s as FAILED after %s error",
                workflow_id,
                op_name,
            )

    try:
        accepted = dispatcher.submit(
            workflow_id,
            fn,
            *args,
            on_failure=_on_failure,
            **call_kwargs,
        )
    except RuntimeError as exc:
        # Dispatcher already shut down.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    if not accepted:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Workflow {workflow_id} already has a graph drive in flight; "
                "wait for it to complete (or pause at an interrupt) before "
                "submitting another operation."
            ),
        )


def _snapshot_response(
    service: WorkflowService,
    workflow_id: str,
) -> WorkflowRunResponse:
    """Return a ``WorkflowRunResponse`` based on the current DB row."""
    detail = service.get(workflow_id)
    snapshot_status = detail.status if detail else WorkflowStatus.PENDING
    ticket_key = detail.ticket_key if detail else ""
    return WorkflowRunResponse.from_dto(
        WorkflowRunResult(
            workflow_id=workflow_id,
            ticket_key=ticket_key,
            final_status=snapshot_status,
        )
    )


_MUTATION_RESPONSES: Dict[int | str, Dict[str, Any]] = {
    404: {"description": "Workflow not found"},
    409: {"description": "Workflow is in an incompatible state for this action"},
}


# Maps a human-decision gate status to the REST endpoint that resumes it.
# Used by ``retry_workflow`` to give callers a concrete recovery hint when
# they mistakenly POST /retry on a gate-paused workflow (AOS-280).
_GATE_RESUME_ENDPOINT: Dict[WorkflowStatus, str] = {
    WorkflowStatus.PENDING_WORKPLAN_CLARIFICATION: "POST /workflows/{id}/clarification",
    WorkflowStatus.PENDING_APPROVAL: "POST /workflows/{id}/approve-plan or /reject-plan",
    WorkflowStatus.PENDING_PR_APPROVAL: (
        "POST /workflows/{id}/approve-pr, /comment-pr, or /reject-pr"
    ),
}


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
    _require_workflow(service, workflow_id)
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
    _require_workflow(service, workflow_id)
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
    _require_workflow(service, workflow_id)
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
# Mutating routes: PR review flow
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
    _require_workflow(service, workflow_id)
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
    _require_workflow(service, workflow_id)
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
    _require_workflow(service, workflow_id)
    _submit_graph_drive(
        dispatcher=dispatcher,
        service=service,
        workflow_id=workflow_id,
        op_name="comment_pr",
        fn=service.comment_pr,
        args=(workflow_id, body.comments),
    )
    return _snapshot_response(service, workflow_id)


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
    dispatcher: BackgroundDispatcherProtocol = Depends(get_background_dispatcher),
) -> Response:
    _require_workflow(service, workflow_id)
    payload = body if body is not None else MarkInterruptedRequest.model_validate({})
    service.mark_interrupted(
        workflow_id,
        failed_node=payload.failed_node,
        actor=payload.actor,
    )
    dispatcher.cancel(workflow_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
