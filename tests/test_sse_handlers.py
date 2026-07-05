"""Regression tests for the async SSE handlers in ``orchestrator/server/sse.py``.

These tests exist specifically to guard against a class of bug where the
SSE async generators call the synchronous ``WorkflowService`` API directly
on the asyncio event loop. When that happens, every slow service call
(SQLite query, file read, etc.) blocks the FastAPI event loop and takes
``/healthz`` — plus every other route — down with it until the workflow
reaches a terminal state.

The fix is to wrap each service call in ``asyncio.to_thread(...)`` so it
runs on the anyio worker pool. These tests prove that contract by
asserting the sync methods executed on a *different* thread than the one
running the event loop.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Iterable, List, Optional

import pytest

from orchestrator.server.sse import stream_events_sse, stream_logs_sse
from orchestrator.workflow_service.dtos import (
    WorkflowDetail,
    WorkflowEvent,
    WorkflowLogChunk,
)
from state.workflow_status import WorkflowStatus

# ---------------------------------------------------------------------------
# Test double
# ---------------------------------------------------------------------------


class _ThreadRecordingService:
    """Minimal ``WorkflowService`` stand-in that records which thread each
    sync call ran on.

    The SSE generators call ``get``, ``read_logs`` and ``stream_events``.
    Each recording set contains the ``threading.Thread`` instance that
    executed the call.
    """

    def __init__(self, *, terminal_after_calls: int = 2) -> None:
        self._get_calls = 0
        self._terminal_after_calls = terminal_after_calls
        self.get_threads: set[threading.Thread] = set()
        self.read_logs_threads: set[threading.Thread] = set()
        self.stream_events_threads: set[threading.Thread] = set()

    def _detail(self, status: WorkflowStatus) -> WorkflowDetail:
        return WorkflowDetail(
            id="wf-1",
            ticket_key="AOS-1",
            status=status,
            created_at="2025-01-01T00:00:00+00:00",
            updated_at="2025-01-01T00:00:00+00:00",
        )

    def get(self, workflow_id: str) -> Optional[WorkflowDetail]:
        self.get_threads.add(threading.current_thread())
        self._get_calls += 1
        if self._get_calls >= self._terminal_after_calls:
            return self._detail(WorkflowStatus.COMPLETED)
        return self._detail(WorkflowStatus.IN_PROGRESS)

    def read_logs(
        self,
        workflow_id: str,
        stage: Optional[str] = None,
        after_offset: int = 0,
    ) -> List[WorkflowLogChunk]:
        self.read_logs_threads.add(threading.current_thread())
        return []

    def stream_events(
        self,
        workflow_id: str,
        after_seq: int = 0,
    ) -> Iterable[WorkflowEvent]:
        self.stream_events_threads.add(threading.current_thread())
        return iter(())


# ---------------------------------------------------------------------------
# Regression guards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_logs_sse_runs_service_calls_off_event_loop(monkeypatch):
    """``stream_logs_sse`` must call ``read_logs`` and ``get`` on a worker
    thread, never on the event loop thread. Otherwise the event loop is
    blocked for the duration of each service call and ``/healthz`` starts
    to time out under any real workflow load.
    """
    from orchestrator.server import sse

    # Shorten the poll interval so the test does not sleep for real.
    monkeypatch.setattr(sse, "LOG_POLL_INTERVAL_S", 0.0)

    loop_thread = threading.current_thread()
    service = _ThreadRecordingService(terminal_after_calls=2)

    async for _ in stream_logs_sse(service, "wf-1"):
        pass

    assert service.read_logs_threads, "read_logs was never called"
    assert service.get_threads, "get was never called"
    assert loop_thread not in service.read_logs_threads, (
        "read_logs ran on the event loop thread — it must be offloaded " "via asyncio.to_thread"
    )
    assert loop_thread not in service.get_threads, (
        "get ran on the event loop thread — it must be offloaded via " "asyncio.to_thread"
    )


@pytest.mark.asyncio
async def test_stream_events_sse_runs_service_calls_off_event_loop(monkeypatch):
    """Same contract as :func:`test_stream_logs_sse_runs_service_calls_off_event_loop`
    but for the events stream: ``stream_events`` and ``get`` must run on a
    worker thread.
    """
    from orchestrator.server import sse

    monkeypatch.setattr(sse, "EVENT_POLL_INTERVAL_S", 0.0)

    loop_thread = threading.current_thread()
    service = _ThreadRecordingService(terminal_after_calls=2)

    async for _ in stream_events_sse(service, "wf-1"):
        pass

    assert service.stream_events_threads, "stream_events was never called"
    assert service.get_threads, "get was never called"
    assert loop_thread not in service.stream_events_threads, (
        "stream_events ran on the event loop thread — it must be " "offloaded via asyncio.to_thread"
    )
    assert loop_thread not in service.get_threads, (
        "get ran on the event loop thread — it must be offloaded via " "asyncio.to_thread"
    )


@pytest.mark.asyncio
async def test_stream_logs_sse_keeps_event_loop_responsive(monkeypatch):
    """Direct behavioural test: while the SSE generator is running against
    a service whose ``read_logs`` sleeps for 100 ms per call, another
    coroutine scheduled on the same event loop must still make progress
    quickly. If the SSE handler were calling ``read_logs`` on the event
    loop, this concurrent coroutine would starve.
    """
    from orchestrator.server import sse

    monkeypatch.setattr(sse, "LOG_POLL_INTERVAL_S", 0.0)

    import time as _time

    class _SlowService(_ThreadRecordingService):
        def read_logs(self, workflow_id, stage=None, after_offset=0):
            _time.sleep(0.1)  # sync sleep — would block the loop if run on it
            return super().read_logs(workflow_id, stage=stage, after_offset=after_offset)

    service = _SlowService(terminal_after_calls=3)

    ticks = 0

    async def _tick():
        nonlocal ticks
        while ticks < 10:
            await asyncio.sleep(0.01)
            ticks += 1

    async def _consume():
        async for _ in stream_logs_sse(service, "wf-1"):
            pass

    start = _time.monotonic()
    await asyncio.gather(_consume(), _tick())
    elapsed = _time.monotonic() - start

    # With the loop free, ``_tick`` completes 10 iterations of 10 ms = ~100 ms
    # while ``_consume`` runs alongside. If ``read_logs`` blocked the loop,
    # ``_tick`` would only tick between ``read_logs`` calls (once per 100 ms)
    # and the total would be ~1s.
    assert ticks == 10
    assert elapsed < 0.6, (
        f"event loop was starved by SSE handler ({elapsed:.2f}s elapsed "
        f"for what should be ~0.3s of work)"
    )
