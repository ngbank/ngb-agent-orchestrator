"""Server-Sent Events routes for workflow event / log streaming."""

from __future__ import annotations

from typing import Optional

from fastapi import Depends, Header, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from orchestrator.workflow_service import WorkflowService

from ..deps import get_service
from ..sse import parse_last_event_id, stream_events_sse, stream_logs_sse
from ._shared import _SSE_HEADERS, _SSE_MEDIA_TYPE, workflow_router


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
