"""Shared plumbing for all orchestrator server route modules.

This module is *only* imported by sibling route modules and by the
package's ``__init__.py``; it must not import from any route module to
avoid circular imports. It owns:

* the three ``APIRouter`` instances (``health_router``,
  ``workflow_router``, ``admin_router``) that every handler hangs off,
* the response-code helper tables (``_MUTATION_RESPONSES``,
  ``_GATE_RESUME_ENDPOINT``, ``_SSE_HEADERS``, ``_SSE_MEDIA_TYPE``),
* guard/plumbing helpers (``_require_workflow``,
  ``_require_paused_at_gate``, ``_submit_graph_drive``,
  ``_snapshot_response``, ``_service_value_error_to_409``).

Handlers register onto the routers imported from here by decorator; the
package's ``__init__.py`` imports every sibling module for side-effect
registration and re-exports the three routers so ``app.py`` continues
to say ``from .routes import health_router, workflow_router,
admin_router``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status

from orchestrator.workflow_service import WorkflowService
from orchestrator.workflow_service.dtos import WorkflowRunResult
from state.workflow_status import WorkflowStatus

from ..auth import require_admin_token, require_bearer_token
from ..background import BackgroundDispatcherProtocol
from ..schemas import WorkflowRunResponse

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Router instances
# ---------------------------------------------------------------------------

health_router = APIRouter(tags=["health"])

workflow_router = APIRouter(
    prefix="/workflows",
    tags=["workflows"],
    dependencies=[Depends(require_bearer_token)],
)

# Admin router is gated by ``require_admin_token`` — refuses to run
# unless ``ORCHESTRATOR_API_TOKEN`` is configured (503 otherwise), and
# requires a matching bearer token when it is.
admin_router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin_token)],
)


# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

# Headers for all SSE responses — disables proxy buffering so events
# reach the client immediately.
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}
_SSE_MEDIA_TYPE = "text/event-stream"


_MUTATION_RESPONSES: Dict[int | str, Dict[str, Any]] = {
    404: {"description": "Workflow not found"},
    409: {"description": "Workflow is in an incompatible state for this action"},
}


# Maps a human-decision gate status to the REST endpoint that resumes it.
# Used by the ``/retry`` handler and ``_require_paused_at_gate`` to give
# callers a concrete recovery hint when they POST the wrong verb on a
# gate-paused workflow.
_GATE_RESUME_ENDPOINT: Dict[WorkflowStatus, str] = {
    WorkflowStatus.PENDING_WORKPLAN_CLARIFICATION: "POST /workflows/{id}/clarification",
    WorkflowStatus.PENDING_APPROVAL: "POST /workflows/{id}/approve-plan or /reject-plan",
    WorkflowStatus.PENDING_PR_APPROVAL: (
        "POST /workflows/{id}/approve-pr, /comment-pr, or /reject-pr"
    ),
}


# ---------------------------------------------------------------------------
# Guard helpers
# ---------------------------------------------------------------------------


def _require_workflow(service: WorkflowService, workflow_id: str) -> None:
    """Raise 404 when ``workflow_id`` is unknown."""
    if service.get(workflow_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow not found: {workflow_id}",
        )


def _require_paused_at_gate(
    service: WorkflowService,
    workflow_id: str,
    expected_gate: WorkflowStatus,
) -> None:
    """Raise 404/409 unless ``workflow_id`` is paused at ``expected_gate``.

    Mirrors the ``/retry`` route's guard so wrong-verb misuse is caught at
    the transport layer. Without this check, POST ``/approve-plan`` on a
    workflow paused at ``await_pr_approval`` would still dispatch
    ``Command(resume={"decision": "approved"})`` to the wrong ``interrupt()``
    — the payload shape coincides with the PR-approve payload and the
    wrong gate silently accepts the wrong decision. Rejecting here means
    the misuse never reaches the graph.

    Uses ``_GATE_RESUME_ENDPOINT`` for the hint so mismatched requests
    are pointed at the correct verb.
    """
    detail = service.get(workflow_id)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow not found: {workflow_id}",
        )
    if detail.status == expected_gate:
        return
    if detail.status.is_paused_at_gate():
        # Wrong gate — point at the correct resume verb for the gate the
        # workflow is actually paused at.
        resume_hint = _GATE_RESUME_ENDPOINT.get(
            detail.status,
            "the matching decision endpoint",
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Workflow {workflow_id} is paused at {detail.status.value}, "
                f"not {expected_gate.value}. Use {resume_hint} instead."
            ),
        )
    # Not paused at any gate — refuse without a resume-verb hint (the
    # workflow isn't waiting for a human decision at all).
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            f"Workflow {workflow_id} is in status {detail.status.value} "
            f"and is not paused at {expected_gate.value}; nothing to resume."
        ),
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
