"""REST routes for the orchestrator HTTP server.

All routes depend on:

* ``get_service`` — supplies the :class:`WorkflowService` to call.
* ``require_bearer_token`` — enforces the auth stub (no-op when the
  ``ORCHESTRATOR_API_TOKEN`` env var is unset).

The router only translates between HTTP and ``WorkflowService`` — every
behavioural detail (status updates, post-processing) lives in the service.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from orchestrator.workflow_service import WorkflowService

from .auth import require_bearer_token
from .deps import get_service
from .schemas import (
    CancelWorkflowRequest,
    HealthResponse,
    StartWorkflowRequest,
    WorkflowDetailResponse,
    WorkflowRunResponse,
    WorkflowSummaryResponse,
    parse_status,
)

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
    return WorkflowRunResponse(
        workflow_id=result.workflow_id,
        ticket_key=result.ticket_key,
        final_status=result.final_status.value,
        interrupted=result.interrupted,
        error=result.error,
        execution_summary=result.execution_summary,
        pr_url=result.pr_url,
        failed_node=result.failed_node,
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
