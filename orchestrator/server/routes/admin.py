"""Admin routes: DB wipe + mark-interrupted.

Gated by :func:`require_admin_token` (applied at the router level in
:mod:`._shared`): these refuse to run unless the server has an
``ORCHESTRATOR_API_TOKEN`` configured (503 otherwise), and require a
matching bearer token when it is.  ``mark_interrupted`` lives here
rather than on ``workflow_router`` so the auth posture is uniform.
"""

from __future__ import annotations

from typing import Optional

from fastapi import Depends, Response, status

from orchestrator.workflow_service import WorkflowService

from ..background import BackgroundDispatcherProtocol
from ..deps import get_background_dispatcher, get_service
from ..schemas import ClearDbResponse, MarkInterruptedRequest
from ._shared import _require_workflow, admin_router


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
