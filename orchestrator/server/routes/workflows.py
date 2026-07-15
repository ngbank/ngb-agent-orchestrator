"""Workflow CRUD routes: start, list, get, cancel, history, audit-log.

All routes hang off ``workflow_router`` (prefix ``/workflows``, bearer
auth). Gate-decision routes live in :mod:`.decisions`; SSE streams in
:mod:`.streams`.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import Depends, HTTPException, Query, Response, status

from orchestrator.workflow_service import WorkflowService
from orchestrator.workflow_service.dtos import WorkflowRunResult
from state.workflow_status import WorkflowStatus

from ..background import BackgroundDispatcherProtocol
from ..deps import get_background_dispatcher, get_service
from ..schemas import (
    CancelWorkflowRequest,
    StartWorkflowRequest,
    WorkflowAuditEntryResponse,
    WorkflowDetailResponse,
    WorkflowHistoryEntryResponse,
    WorkflowRunResponse,
    WorkflowSummaryResponse,
    parse_status,
)
from ._shared import (
    _require_workflow,
    _submit_graph_drive,
    workflow_router,
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
