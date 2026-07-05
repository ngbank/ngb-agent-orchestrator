"""Server-Sent Events (SSE) helpers for the orchestrator HTTP server.

This module implements the streaming primitives used by the
``GET /workflows/{id}/events`` and ``GET /workflows/{id}/logs`` endpoints.
Both endpoints follow the SSE wire format (``text/event-stream``) so any
browser ``EventSource`` or ``curl -N`` client can consume them.

Design rules:

* Pure async generators — no threads, no executor — so client disconnects
  surface as ``asyncio.CancelledError`` and tear the stream down cleanly.
* All polling cadences are module-level constants so tests can monkeypatch
  them to near-zero without changing the production defaults.
* Streams call back into the existing ``WorkflowService`` (``stream_events``
  / ``read_logs``) on each poll and yield only the *delta*.  Reconnect
  resume is therefore a function-call away: the caller passes the last
  ``after_seq`` / ``after_offset`` it received and the next stream replays
  starting from that point.
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, Dict, Optional

from orchestrator.workflow_service import WorkflowService

# Production cadences.  Tests override via ``monkeypatch.setattr``.
EVENT_POLL_INTERVAL_S: float = 0.25
LOG_POLL_INTERVAL_S: float = 0.25
HEARTBEAT_INTERVAL_S: float = 15.0


def _sse_frame(event_id: Optional[str], data: str) -> bytes:
    """Encode one SSE event.

    ``data`` may contain newlines; SSE requires each line to be prefixed
    with ``data: `` so multi-line payloads are split before encoding.
    """
    lines: list[str] = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    for line in data.split("\n"):
        lines.append(f"data: {line}")
    lines.append("")  # trailing blank line terminates the event
    lines.append("")
    return "\n".join(lines).encode("utf-8")


def _heartbeat() -> bytes:
    """SSE comment frame — keeps idle connections from being closed by proxies."""
    return b": ping\n\n"


def parse_last_event_id(value: Optional[str]) -> Optional[int]:
    """Parse a ``Last-Event-ID`` header into an int; return ``None`` if invalid."""
    if value is None:
        return None
    try:
        parsed = int(value.strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


async def stream_events_sse(
    service: WorkflowService,
    workflow_id: str,
    *,
    after_seq: int = 0,
) -> AsyncIterator[bytes]:
    """Yield SSE-framed workflow events until the workflow reaches a terminal state.

    Each call to ``service.stream_events`` yields the *new* events since the
    last delivered ``seq``.  Between polls the generator waits up to
    ``EVENT_POLL_INTERVAL_S`` for new data and emits a heartbeat every
    ``HEARTBEAT_INTERVAL_S`` of idle time so proxies do not close the
    connection.

    ``WorkflowService`` is a synchronous API; every call into it happens on
    a worker thread via :func:`asyncio.to_thread` so the FastAPI event loop
    stays free to service ``/healthz`` and other endpoints while the stream
    is being consumed.
    """
    last_seq = max(0, after_seq)
    idle_elapsed = 0.0

    while True:
        emitted_any = False
        events = await asyncio.to_thread(_drain_events, service, workflow_id, last_seq)
        for event in events:
            payload = {
                "seq": event.seq,
                "kind": event.kind,
                "node": event.node,
                "data": event.data,
            }
            last_seq = event.seq
            emitted_any = True
            idle_elapsed = 0.0
            yield _sse_frame(str(event.seq), json.dumps(payload))

        detail = await asyncio.to_thread(service.get, workflow_id)
        if detail is not None and detail.status.is_terminal():
            # One last drain after the terminal transition in case the service
            # produced trailing events between the loop above and the status
            # check.
            events = await asyncio.to_thread(_drain_events, service, workflow_id, last_seq)
            for event in events:
                payload = {
                    "seq": event.seq,
                    "kind": event.kind,
                    "node": event.node,
                    "data": event.data,
                }
                last_seq = event.seq
                yield _sse_frame(str(event.seq), json.dumps(payload))
            # Final marker so clients can distinguish "stream closed because
            # workflow finished" from "transport disconnect".
            final_payload = {
                "seq": last_seq,
                "kind": "stream_end",
                "node": None,
                "data": {"final_status": detail.status.value},
            }
            yield _sse_frame(str(last_seq), json.dumps(final_payload))
            return

        if not emitted_any:
            if idle_elapsed >= HEARTBEAT_INTERVAL_S:
                idle_elapsed = 0.0
                yield _heartbeat()
            await asyncio.sleep(EVENT_POLL_INTERVAL_S)
            idle_elapsed += EVENT_POLL_INTERVAL_S


def _drain_events(service: WorkflowService, workflow_id: str, after_seq: int) -> list:
    """Materialise the ``stream_events`` iterator on the calling thread.

    ``WorkflowService.stream_events`` returns a synchronous iterator that
    performs I/O (SQLite reads, langgraph state history walks) as it is
    consumed.  When called from an async context that iteration must happen
    on a worker thread; wrapping it in a helper keeps the ``to_thread``
    call sites concise.
    """
    return list(service.stream_events(workflow_id, after_seq=after_seq))


async def stream_logs_sse(
    service: WorkflowService,
    workflow_id: str,
    *,
    stage: Optional[str] = None,
    after_offset: int = 0,
) -> AsyncIterator[bytes]:
    """Yield SSE-framed log chunks until the workflow reaches a terminal state.

    The wire format uses one SSE event per log chunk.  ``id:`` carries the
    byte offset of the *end* of the chunk within the underlying log file —
    clients can pass this value back as ``after_offset`` (or via
    ``Last-Event-ID``) to resume after reconnect.

    When ``stage`` is ``None`` the canonical ``"workflow"`` stream is followed.

    ``WorkflowService`` is a synchronous API; every call into it happens on
    a worker thread via :func:`asyncio.to_thread` so the FastAPI event loop
    stays free to service ``/healthz`` and other endpoints while the stream
    is being consumed.
    """
    offsets: Dict[str, int] = {}
    if stage is not None:
        offsets[stage] = max(0, after_offset)
    else:
        offsets["workflow"] = max(0, after_offset)

    idle_elapsed = 0.0
    while True:
        emitted_any = False
        for st, offset in list(offsets.items()):
            chunks = await asyncio.to_thread(
                service.read_logs, workflow_id, stage=st, after_offset=offset
            )
            for chunk in chunks:
                content_bytes = chunk.content.encode("utf-8")
                end_offset = chunk.offset + len(content_bytes)
                offsets[st] = end_offset
                payload = {
                    "stage": chunk.stage,
                    "offset": chunk.offset,
                    "end_offset": end_offset,
                    "content": chunk.content,
                }
                emitted_any = True
                idle_elapsed = 0.0
                yield _sse_frame(str(end_offset), json.dumps(payload))

        detail = await asyncio.to_thread(service.get, workflow_id)
        if detail is not None and detail.status.is_terminal():
            # Drain once more to capture any bytes flushed between the loop
            # above and the terminal-status read.
            for st, offset in list(offsets.items()):
                chunks = await asyncio.to_thread(
                    service.read_logs, workflow_id, stage=st, after_offset=offset
                )
                for chunk in chunks:
                    content_bytes = chunk.content.encode("utf-8")
                    end_offset = chunk.offset + len(content_bytes)
                    offsets[st] = end_offset
                    payload = {
                        "stage": chunk.stage,
                        "offset": chunk.offset,
                        "end_offset": end_offset,
                        "content": chunk.content,
                    }
                    yield _sse_frame(str(end_offset), json.dumps(payload))
            final_payload = {
                "stage": stage,
                "kind": "stream_end",
                "final_status": detail.status.value,
            }
            yield _sse_frame(None, json.dumps(final_payload))
            return

        if not emitted_any:
            if idle_elapsed >= HEARTBEAT_INTERVAL_S:
                idle_elapsed = 0.0
                yield _heartbeat()
            await asyncio.sleep(LOG_POLL_INTERVAL_S)
            idle_elapsed += LOG_POLL_INTERVAL_S


__all__ = [
    "EVENT_POLL_INTERVAL_S",
    "LOG_POLL_INTERVAL_S",
    "HEARTBEAT_INTERVAL_S",
    "parse_last_event_id",
    "stream_events_sse",
    "stream_logs_sse",
]
