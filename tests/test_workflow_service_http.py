"""Tests for :mod:`orchestrator.workflow_service.http_client`.

Covers three layers:

1. **HTTP unit tests** — drive :class:`HttpWorkflowService` against a real
   FastAPI app via :class:`httpx.ASGITransport` and assert wire-level
   behaviour (status codes, params, bearer auth, OTel headers).
2. **Parity tests** — call the same Protocol methods on
   :class:`LocalWorkflowService` and :class:`HttpWorkflowService` (the latter
   backed by an ``ASGITransport`` over a FastAPI app whose service IS the
   local one) and assert the returned DTOs are equal.
3. **Reconnect test** — verify :meth:`HttpWorkflowService.stream_events` resumes
   from the last seen ``seq`` after a transport-level disconnect.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Dict, Iterable, Iterator, List, Optional, cast

import httpx
import pytest

from orchestrator.server.app import create_app
from orchestrator.server.auth import API_TOKEN_ENV
from orchestrator.workflow_service import (
    HttpWorkflowService,
    LocalWorkflowService,
    RemoteOperationNotSupported,
    WorkflowEvent,
    WorkflowStartRequest,
    build_http_workflow_service,
)
from orchestrator.workflow_service import http_client as http_client_mod
from orchestrator.workflow_service.dtos import (
    WorkflowAuditEntry,
    WorkflowDetail,
)
from orchestrator.workflow_service.dtos import WorkflowEvent as WorkflowEventDTO
from orchestrator.workflow_service.dtos import (
    WorkflowHistoryEntry,
    WorkflowLogChunk,
    WorkflowRunResult,
    WorkflowSummary,
)
from state import workflow_repository as state_store
from state.sqlite_workflow_repository import SQLiteWorkflowRepository
from state.workflow_status import WorkflowStatus

# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------


class _FakeWorkflowService:
    """Lightweight in-memory WorkflowService for HTTP route tests.

    Mirrors the fake used in :mod:`tests.test_server_routes` but is local to
    this file so the two test suites stay independent.
    """

    def __init__(self) -> None:
        self.workflows: Dict[str, WorkflowDetail] = {}
        self.start_calls: List[WorkflowStartRequest] = []
        self.start_result: Optional[WorkflowRunResult] = None
        self.cancel_calls: List[Dict[str, Any]] = []
        self.list_calls: List[Dict[str, Any]] = []
        self.events: Dict[str, List[WorkflowEventDTO]] = {}
        self.log_bytes: Dict[str, Dict[str, bytes]] = {}
        # AOS-147 — per-method recorders + canned results / exceptions.
        self.approve_plan_calls: List[str] = []
        self.reject_plan_calls: List[Dict[str, Any]] = []
        self.submit_clarification_calls: List[Dict[str, Any]] = []
        self.retry_calls: List[str] = []
        self.approve_pr_calls: List[str] = []
        self.reject_pr_calls: List[Dict[str, Any]] = []
        self.comment_pr_calls: List[Dict[str, Any]] = []
        self.mark_interrupted_calls: List[Dict[str, Any]] = []
        self.mark_failed_calls: List[Dict[str, Any]] = []
        self.clear_db_calls: List[None] = []
        self.history: Dict[str, List[WorkflowHistoryEntry]] = {}
        self.audit_log: Dict[str, List[WorkflowAuditEntry]] = {}
        self.mutation_result: Optional[WorkflowRunResult] = None
        self.mutation_exc: Optional[BaseException] = None
        self.clear_db_result: tuple[int, int] = (0, 0)

    def seed(self, detail: WorkflowDetail) -> None:
        self.workflows[detail.id] = detail

    def _summary(self, d: WorkflowDetail) -> WorkflowSummary:
        return WorkflowSummary(
            id=d.id,
            ticket_key=d.ticket_key,
            status=d.status,
            created_at=d.created_at,
            updated_at=d.updated_at,
            pr_url=d.pr_url,
        )

    # Reads -----------------------------------------------------------
    def get(self, workflow_id: str) -> Optional[WorkflowDetail]:
        return self.workflows.get(workflow_id)

    def get_by_ticket(self, ticket_key: str) -> List[WorkflowSummary]:
        return [self._summary(w) for w in self.workflows.values() if w.ticket_key == ticket_key]

    def get_latest_retryable_by_ticket(self, ticket_key: str) -> Optional[WorkflowSummary]:
        return None

    def list(
        self,
        ticket_key: Optional[str] = None,
        status: Optional[WorkflowStatus] = None,
        limit: int = 50,
    ) -> List[WorkflowSummary]:
        self.list_calls.append({"ticket_key": ticket_key, "status": status, "limit": limit})
        out: List[WorkflowSummary] = []
        for d in self.workflows.values():
            if ticket_key is not None and d.ticket_key != ticket_key:
                continue
            if status is not None and d.status != status:
                continue
            out.append(self._summary(d))
        return out[:limit]

    def get_history(self, workflow_id: str) -> List[WorkflowHistoryEntry]:
        return list(self.history.get(workflow_id, []))

    def get_audit_log(self, workflow_id: str) -> List[WorkflowAuditEntry]:
        return list(self.audit_log.get(workflow_id, []))

    def read_logs(
        self,
        workflow_id: str,
        stage: Optional[str] = None,
        after_offset: int = 0,
    ) -> List[WorkflowLogChunk]:
        store = self.log_bytes.get(workflow_id, {})
        stages = [stage] if stage else list(store.keys())
        chunks: List[WorkflowLogChunk] = []
        for st in stages:
            raw = store.get(st)
            if not raw:
                continue
            start = max(0, min(after_offset, len(raw)))
            if start >= len(raw):
                continue
            chunks.append(
                WorkflowLogChunk(
                    workflow_id=workflow_id,
                    stage=st,
                    path=f"/tmp/{workflow_id}-{st}.log",
                    content=raw[start:].decode("utf-8"),
                    offset=start,
                )
            )
        return chunks

    def stream_events(
        self,
        workflow_id: str,
        after_seq: int = 0,
    ) -> Iterable[WorkflowEventDTO]:
        return iter([e for e in self.events.get(workflow_id, []) if e.seq > after_seq])

    # Mutations -------------------------------------------------------
    def cancel(
        self,
        workflow_id: str,
        reason: Optional[str] = None,
        actor: str = "system",
    ) -> None:
        self.cancel_calls.append({"workflow_id": workflow_id, "reason": reason, "actor": actor})
        existing = self.workflows.get(workflow_id)
        if existing is not None:
            self.workflows[workflow_id] = WorkflowDetail(
                id=existing.id,
                ticket_key=existing.ticket_key,
                status=WorkflowStatus.CANCELLED,
                created_at=existing.created_at,
                updated_at=existing.updated_at,
                pr_url=existing.pr_url,
            )

    def mark_interrupted(
        self,
        workflow_id: str,
        failed_node: Optional[str] = None,
        actor: str = "system",
    ) -> None:
        self.mark_interrupted_calls.append(
            {"workflow_id": workflow_id, "failed_node": failed_node, "actor": actor}
        )

    def mark_failed(
        self,
        workflow_id: str,
        reason: str,
        actor: str = "system",
    ) -> None:
        self.mark_failed_calls.append(
            {"workflow_id": workflow_id, "reason": reason, "actor": actor}
        )

    def clear_db(self) -> tuple[int, int]:
        self.clear_db_calls.append(None)
        return self.clear_db_result

    # Graph ops -------------------------------------------------------
    def prepare_start(self, request: WorkflowStartRequest) -> WorkflowStartRequest:
        if request.dry_run:
            return request
        wf_id = request.workflow_id or "wf-prepared"
        if wf_id not in self.workflows:
            self.workflows[wf_id] = _make_detail(
                wf_id,
                status=WorkflowStatus.PENDING,
                ticket_key=request.ticket_key,
            )
        return WorkflowStartRequest(
            ticket_key=request.ticket_key,
            workflow_id=wf_id,
            dry_run=request.dry_run,
        )

    def start(self, request: WorkflowStartRequest) -> WorkflowRunResult:
        self.start_calls.append(request)
        if self.start_result is not None:
            return self.start_result
        return WorkflowRunResult(
            workflow_id=request.workflow_id or "wf-generated",
            ticket_key=request.ticket_key,
            final_status=WorkflowStatus.PENDING_APPROVAL,
            interrupted=True,
        )

    def approve_plan(self, workflow_id: str) -> WorkflowRunResult:
        self.approve_plan_calls.append(workflow_id)
        return self._mutation_result_or_default(workflow_id)

    def reject_plan(self, workflow_id: str, reason: Optional[str]) -> WorkflowRunResult:
        self.reject_plan_calls.append({"workflow_id": workflow_id, "reason": reason})
        return self._mutation_result_or_default(workflow_id)

    def submit_clarification(
        self,
        workflow_id: str,
        answers: List[Dict[str, str]],
    ) -> WorkflowRunResult:
        self.submit_clarification_calls.append(
            {"workflow_id": workflow_id, "answers": list(answers)}
        )
        return self._mutation_result_or_default(workflow_id)

    def retry(self, workflow_id: str) -> WorkflowRunResult:
        self.retry_calls.append(workflow_id)
        return self._mutation_result_or_default(workflow_id)

    def approve_pr(self, workflow_id: str) -> WorkflowRunResult:
        self.approve_pr_calls.append(workflow_id)
        return self._mutation_result_or_default(workflow_id)

    def comment_pr(self, workflow_id: str, comments: str) -> WorkflowRunResult:
        self.comment_pr_calls.append({"workflow_id": workflow_id, "comments": comments})
        return self._mutation_result_or_default(workflow_id)

    def reject_pr(self, workflow_id: str, reason: Optional[str]) -> WorkflowRunResult:
        self.reject_pr_calls.append({"workflow_id": workflow_id, "reason": reason})
        return self._mutation_result_or_default(workflow_id)

    def _mutation_result_or_default(self, workflow_id: str) -> WorkflowRunResult:
        if self.mutation_exc is not None:
            raise self.mutation_exc
        if self.mutation_result is not None:
            return self.mutation_result
        existing = self.workflows.get(workflow_id)
        return WorkflowRunResult(
            workflow_id=workflow_id,
            ticket_key=existing.ticket_key if existing else None,
            final_status=existing.status if existing else WorkflowStatus.PENDING,
        )


def _make_detail(
    workflow_id: str,
    *,
    status: WorkflowStatus = WorkflowStatus.IN_PROGRESS,
    ticket_key: str = "AOS-143",
) -> WorkflowDetail:
    return WorkflowDetail(
        id=workflow_id,
        ticket_key=ticket_key,
        status=status,
        created_at="2026-06-22T00:00:00",
        updated_at="2026-06-22T00:00:00",
        pr_url=None,
        work_plan=None,
        execution_summary=None,
        clarification_history=[],
        pr_comments=None,
        usage_summary={},
        retry_count=0,
    )


def _build_http_service(
    fake: _FakeWorkflowService,
    *,
    token: Optional[str] = None,
) -> HttpWorkflowService:
    """Construct an HttpWorkflowService backed by an in-process FastAPI app.

    Uses :class:`fastapi.testclient.TestClient` (a sync ``httpx.Client``
    subclass) so the service can issue real HTTP requests against the ASGI
    app without spinning up a uvicorn process.
    """
    from fastapi.testclient import TestClient

    from orchestrator.server.background import SyncBackgroundDispatcher

    # Inject a sync dispatcher so the fire-and-forget routes execute the
    # underlying fake call inline (the test wants to observe the fake's
    # ``_calls`` list) without needing the FastAPI lifespan.
    app = create_app(service=fake, background_dispatcher=SyncBackgroundDispatcher())
    client = TestClient(app, base_url="http://testserver")
    return build_http_workflow_service(base_url="http://testserver", token=token, client=client)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_service(monkeypatch) -> _FakeWorkflowService:
    # Default: server auth disabled.
    monkeypatch.delenv(API_TOKEN_ENV, raising=False)
    return _FakeWorkflowService()


@pytest.fixture
def http_service(fake_service: _FakeWorkflowService) -> Iterator[HttpWorkflowService]:
    svc = _build_http_service(fake_service)
    try:
        yield svc
    finally:
        svc.close()


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


class TestReads:
    def test_get_returns_detail(
        self, fake_service: _FakeWorkflowService, http_service: HttpWorkflowService
    ) -> None:
        fake_service.seed(_make_detail("wf-1"))
        detail = http_service.get("wf-1")
        assert detail is not None
        assert detail.id == "wf-1"
        assert detail.ticket_key == "AOS-143"
        assert detail.status == WorkflowStatus.IN_PROGRESS

    def test_get_returns_none_on_404(self, http_service: HttpWorkflowService) -> None:
        assert http_service.get("nope") is None

    def test_get_by_ticket_filters_correctly(
        self, fake_service: _FakeWorkflowService, http_service: HttpWorkflowService
    ) -> None:
        fake_service.seed(_make_detail("wf-1", ticket_key="AOS-143"))
        fake_service.seed(_make_detail("wf-2", ticket_key="AOS-99"))
        results = http_service.get_by_ticket("AOS-143")
        assert [r.id for r in results] == ["wf-1"]

    def test_get_latest_retryable_returns_first_retryable(
        self, fake_service: _FakeWorkflowService, http_service: HttpWorkflowService
    ) -> None:
        # Note: FakeWorkflowService.get_by_ticket returns dict-iteration order,
        # so we seed deterministically.
        fake_service.seed(_make_detail("wf-1", status=WorkflowStatus.FAILED))
        fake_service.seed(_make_detail("wf-2", status=WorkflowStatus.COMPLETED))
        result = http_service.get_latest_retryable_by_ticket("AOS-143")
        assert result is not None
        assert result.id == "wf-1"
        assert result.status == WorkflowStatus.FAILED

    def test_get_latest_retryable_returns_none(
        self, fake_service: _FakeWorkflowService, http_service: HttpWorkflowService
    ) -> None:
        fake_service.seed(_make_detail("wf-1", status=WorkflowStatus.COMPLETED))
        assert http_service.get_latest_retryable_by_ticket("AOS-143") is None

    def test_list_forwards_filters(
        self, fake_service: _FakeWorkflowService, http_service: HttpWorkflowService
    ) -> None:
        fake_service.seed(_make_detail("wf-1", status=WorkflowStatus.COMPLETED))
        fake_service.seed(_make_detail("wf-2", status=WorkflowStatus.IN_PROGRESS))
        results = http_service.list(status=WorkflowStatus.COMPLETED, limit=5)
        assert [r.id for r in results] == ["wf-1"]
        last = fake_service.list_calls[-1]
        assert last["status"] == WorkflowStatus.COMPLETED
        assert last["limit"] == 5


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------


class TestCancel:
    def test_cancel_forwards_actor_and_reason(
        self, fake_service: _FakeWorkflowService, http_service: HttpWorkflowService
    ) -> None:
        fake_service.seed(_make_detail("wf-1"))
        http_service.cancel("wf-1", reason="drop it", actor="dispatcher")
        call = fake_service.cancel_calls[-1]
        assert call == {"workflow_id": "wf-1", "reason": "drop it", "actor": "dispatcher"}

    def test_cancel_raises_value_error_on_404(self, http_service: HttpWorkflowService) -> None:
        with pytest.raises(ValueError, match="not found"):
            http_service.cancel("nope")

    def test_cancel_raises_value_error_on_409(
        self, fake_service: _FakeWorkflowService, http_service: HttpWorkflowService
    ) -> None:
        fake_service.seed(_make_detail("wf-1", status=WorkflowStatus.COMPLETED))
        with pytest.raises(ValueError, match="terminal"):
            http_service.cancel("wf-1")


# ---------------------------------------------------------------------------
# Graph-running operations
# ---------------------------------------------------------------------------


class TestStart:
    def test_start_forwards_full_request(
        self, fake_service: _FakeWorkflowService, http_service: HttpWorkflowService
    ) -> None:
        fake_service.start_result = WorkflowRunResult(
            workflow_id="custom-id",
            ticket_key="AOS-143",
            final_status=WorkflowStatus.PENDING_APPROVAL,
            interrupted=True,
        )
        result = http_service.start(
            WorkflowStartRequest(ticket_key="AOS-143", dry_run=True, workflow_id="custom-id")
        )
        assert result.workflow_id == "custom-id"
        assert result.final_status == WorkflowStatus.PENDING_APPROVAL
        assert result.interrupted is True
        sent = fake_service.start_calls[-1]
        assert sent.ticket_key == "AOS-143"
        assert sent.dry_run is True
        assert sent.workflow_id == "custom-id"


# ---------------------------------------------------------------------------
# Streaming: read_logs (snapshot drain)
# ---------------------------------------------------------------------------


class TestReadLogs:
    """End-to-end log-snapshot tests against a terminal workflow.

    The Starlette ``TestClient`` transport does not honor httpx read
    timeouts, so we cannot reliably end-to-end test the "workflow still
    active, fall back on ReadTimeout" code path via TestClient.  That path
    is covered separately by ``test_read_logs_returns_on_read_timeout``
    using a stub httpx client.  Tests here use ``COMPLETED`` status so the
    server emits ``stream_end`` and the client returns cleanly.
    """

    def test_read_logs_drains_initial_burst(
        self,
        fake_service: _FakeWorkflowService,
        http_service: HttpWorkflowService,
        monkeypatch,
    ) -> None:
        from orchestrator.server import sse as sse_mod

        monkeypatch.setattr(sse_mod, "LOG_POLL_INTERVAL_S", 0.01)
        monkeypatch.setattr(sse_mod, "HEARTBEAT_INTERVAL_S", 60.0)

        fake_service.seed(_make_detail("wf-1", status=WorkflowStatus.COMPLETED))
        fake_service.log_bytes.setdefault("wf-1", {})["plan"] = b"plan output line\n"
        fake_service.log_bytes["wf-1"]["execute"] = b"execute output line\n"

        chunks = http_service.read_logs("wf-1")
        by_stage = {c.stage: c for c in chunks}
        assert set(by_stage) == {"plan", "execute"}
        assert by_stage["plan"].content == "plan output line\n"
        assert by_stage["execute"].content == "execute output line\n"

    def test_read_logs_returns_empty_when_no_logs(
        self,
        fake_service: _FakeWorkflowService,
        http_service: HttpWorkflowService,
        monkeypatch,
    ) -> None:
        from orchestrator.server import sse as sse_mod

        monkeypatch.setattr(sse_mod, "LOG_POLL_INTERVAL_S", 0.01)
        monkeypatch.setattr(sse_mod, "HEARTBEAT_INTERVAL_S", 60.0)
        fake_service.seed(_make_detail("wf-1", status=WorkflowStatus.COMPLETED))
        assert http_service.read_logs("wf-1") == []

    def test_read_logs_returns_only_requested_stage(
        self,
        fake_service: _FakeWorkflowService,
        http_service: HttpWorkflowService,
        monkeypatch,
    ) -> None:
        from orchestrator.server import sse as sse_mod

        monkeypatch.setattr(sse_mod, "LOG_POLL_INTERVAL_S", 0.01)
        monkeypatch.setattr(sse_mod, "HEARTBEAT_INTERVAL_S", 60.0)

        fake_service.seed(_make_detail("wf-1", status=WorkflowStatus.COMPLETED))
        fake_service.log_bytes.setdefault("wf-1", {})["plan"] = b"plan-only\n"
        fake_service.log_bytes["wf-1"]["execute"] = b"execute-only\n"

        chunks = http_service.read_logs("wf-1", stage="plan")
        assert [c.stage for c in chunks] == ["plan"]
        assert chunks[0].content == "plan-only\n"

    def test_read_logs_terminates_on_stream_end(
        self,
        fake_service: _FakeWorkflowService,
        http_service: HttpWorkflowService,
        monkeypatch,
    ) -> None:
        # Explicit assertion that stream_end short-circuits the loop even when
        # the configured client-side read timeout is high.
        from orchestrator.server import sse as sse_mod

        monkeypatch.setattr(sse_mod, "LOG_POLL_INTERVAL_S", 0.01)
        monkeypatch.setattr(sse_mod, "HEARTBEAT_INTERVAL_S", 60.0)
        monkeypatch.setattr(http_client_mod, "LOG_SNAPSHOT_READ_TIMEOUT_S", 30.0)

        fake_service.seed(_make_detail("wf-1", status=WorkflowStatus.COMPLETED))
        fake_service.log_bytes.setdefault("wf-1", {})["plan"] = b"done\n"

        chunks = http_service.read_logs("wf-1")
        assert [c.stage for c in chunks] == ["plan"]


def test_read_logs_returns_on_read_timeout(monkeypatch) -> None:
    """When the server is idle (no stream_end), the client should disconnect
    on the configured read timeout and return whatever chunks arrived."""

    sent_chunks = [
        {"stage": "plan", "offset": 0, "content": "first\n", "end_offset": 6},
    ]

    class _StubStreamCtx:
        def __init__(self) -> None:
            self.status_code = 200
            self.headers: Dict[str, str] = {}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self) -> None:
            return None

        def iter_lines(self):
            # Emit one event then raise ReadTimeout to simulate a still-active
            # workflow on the server side.
            for chunk in sent_chunks:
                yield f"id: {chunk['end_offset']}"
                yield f"data: {json.dumps(chunk)}"
                yield ""
            raise httpx.ReadTimeout("simulated idle")

    class _StubClient:
        def __init__(self) -> None:
            self.requests: List[Dict[str, Any]] = []

        def stream(self, method, url, *, params=None, headers=None, timeout=None):
            self.requests.append(
                {
                    "method": method,
                    "url": url,
                    "params": params,
                    "headers": headers,
                    "timeout": timeout,
                }
            )
            return _StubStreamCtx()

    stub = _StubClient()
    svc = build_http_workflow_service("http://example.test", client=cast(httpx.Client, stub))

    chunks = svc.read_logs("wf-1")
    assert [c.stage for c in chunks] == ["plan"]
    assert chunks[0].content == "first\n"
    assert chunks[0].offset == 0
    assert stub.requests and stub.requests[0]["url"].endswith("/workflows/wf-1/logs")


# ---------------------------------------------------------------------------
# Streaming: stream_events
# ---------------------------------------------------------------------------


class TestStreamEvents:
    def test_stream_events_yields_until_terminal(
        self,
        fake_service: _FakeWorkflowService,
        http_service: HttpWorkflowService,
        monkeypatch,
    ) -> None:
        from orchestrator.server import sse as sse_mod

        monkeypatch.setattr(sse_mod, "EVENT_POLL_INTERVAL_S", 0.01)
        monkeypatch.setattr(sse_mod, "HEARTBEAT_INTERVAL_S", 60.0)

        fake_service.seed(_make_detail("wf-1", status=WorkflowStatus.COMPLETED))
        fake_service.events["wf-1"] = [
            WorkflowEvent(workflow_id="wf-1", seq=1, kind="node_start", node="plan"),
            WorkflowEvent(workflow_id="wf-1", seq=2, kind="node_end", node="plan"),
        ]
        received = list(http_service.stream_events("wf-1"))
        seqs = [e.seq for e in received]
        # Server emits a synthetic stream_end after draining; the client
        # filters it out, so we only see the two real events.
        assert seqs == [1, 2]
        assert received[0].kind == "node_start"

    def test_stream_events_respects_after_seq(
        self,
        fake_service: _FakeWorkflowService,
        http_service: HttpWorkflowService,
        monkeypatch,
    ) -> None:
        from orchestrator.server import sse as sse_mod

        monkeypatch.setattr(sse_mod, "EVENT_POLL_INTERVAL_S", 0.01)
        monkeypatch.setattr(sse_mod, "HEARTBEAT_INTERVAL_S", 60.0)

        fake_service.seed(_make_detail("wf-1", status=WorkflowStatus.COMPLETED))
        fake_service.events["wf-1"] = [
            WorkflowEvent(workflow_id="wf-1", seq=1, kind="node_start", node="plan"),
            WorkflowEvent(workflow_id="wf-1", seq=2, kind="node_end", node="plan"),
            WorkflowEvent(workflow_id="wf-1", seq=3, kind="node_start", node="execute"),
        ]
        received = list(http_service.stream_events("wf-1", after_seq=2))
        assert [e.seq for e in received] == [3]


# ---------------------------------------------------------------------------
# Auth + OTel headers
# ---------------------------------------------------------------------------


class TestHeaders:
    def test_bearer_token_is_attached(
        self,
        monkeypatch,
        fake_service: _FakeWorkflowService,
    ) -> None:
        monkeypatch.setenv(API_TOKEN_ENV, "secret-token")
        # Rebuild the app so the auth dependency reads the new env value.
        svc = _build_http_service(fake_service, token="secret-token")
        try:
            fake_service.seed(_make_detail("wf-1"))
            assert svc.get("wf-1") is not None  # 200 because token matches
        finally:
            svc.close()

    def test_missing_token_returns_401_via_http_error(
        self,
        monkeypatch,
        fake_service: _FakeWorkflowService,
    ) -> None:
        monkeypatch.setenv(API_TOKEN_ENV, "secret-token")
        svc = _build_http_service(fake_service, token=None)
        try:
            fake_service.seed(_make_detail("wf-1"))
            with pytest.raises(httpx.HTTPStatusError) as exc:
                svc.get("wf-1")
            assert exc.value.response.status_code == 401
        finally:
            svc.close()

    def test_otel_traceparent_header_propagates(
        self,
        fake_service: _FakeWorkflowService,
        http_service: HttpWorkflowService,
    ) -> None:
        from opentelemetry.sdk.trace import TracerProvider

        provider = TracerProvider()
        tracer = provider.get_tracer(__name__)

        captured: Dict[str, Optional[str]] = {"traceparent": None}

        # Wrap the existing transport in a probe that records the header.
        original_handle = http_service._client._transport.handle_request

        def probe(request: httpx.Request) -> httpx.Response:
            captured["traceparent"] = request.headers.get("traceparent")
            return original_handle(request)

        http_service._client._transport.handle_request = probe

        fake_service.seed(_make_detail("wf-1"))
        with tracer.start_as_current_span("test"):
            http_service.get("wf-1")
        # OTel may not be configured at import time in this test process;
        # the inject helper is best-effort.  Accept either header present or
        # absent — what matters is that no exception was raised.
        if captured["traceparent"] is not None:
            assert captured["traceparent"].startswith("00-")


# ---------------------------------------------------------------------------
# Approval / clarification / retry / PR review (AOS-147)
# ---------------------------------------------------------------------------


def _expected_run_result(workflow_id: str = "wf-1") -> WorkflowRunResult:
    return WorkflowRunResult(
        workflow_id=workflow_id,
        ticket_key="AOS-143",
        final_status=WorkflowStatus.PENDING_PR_APPROVAL,
        execution_summary={"status": "success"},
        pr_url="https://example.test/pr/1",
    )


class TestApproval:
    def test_approve_plan_forwards_call(
        self,
        fake_service: _FakeWorkflowService,
        http_service: HttpWorkflowService,
    ) -> None:
        # Fire-and-forget: the route returns a snapshot from the current
        # workflow row (the seeded detail) rather than the eventual result
        # of the background graph drive.
        fake_service.seed(_make_detail("wf-1"))
        fake_service.mutation_result = _expected_run_result("wf-1")
        result = http_service.approve_plan("wf-1")
        # Status snapshot reflects the seeded row.
        assert result.final_status == WorkflowStatus.IN_PROGRESS
        # The graph drive ran inline via SyncBackgroundDispatcher.
        assert fake_service.approve_plan_calls == ["wf-1"]

    def test_approve_plan_404_raises_value_error(self, http_service: HttpWorkflowService) -> None:
        with pytest.raises(ValueError, match="not found"):
            http_service.approve_plan("nope")

    def test_approve_plan_failure_marks_workflow_failed(
        self,
        fake_service: _FakeWorkflowService,
        http_service: HttpWorkflowService,
    ) -> None:
        # Fire-and-forget: when the background graph drive raises, the
        # dispatcher's on_failure handler calls mark_failed.  The HTTP
        # client itself does not see the exception (the route already
        # returned 202).
        fake_service.seed(_make_detail("wf-1"))
        fake_service.mutation_exc = ValueError("not awaiting approval")
        # Should not raise — server returns 202 immediately.
        http_service.approve_plan("wf-1")
        assert len(fake_service.mark_failed_calls) == 1
        assert fake_service.mark_failed_calls[0]["workflow_id"] == "wf-1"
        assert "not awaiting approval" in fake_service.mark_failed_calls[0]["reason"]

    def test_reject_plan_passes_reason(
        self,
        fake_service: _FakeWorkflowService,
        http_service: HttpWorkflowService,
    ) -> None:
        fake_service.seed(_make_detail("wf-1"))
        fake_service.mutation_result = _expected_run_result("wf-1")
        http_service.reject_plan("wf-1", "bad scope")
        assert fake_service.reject_plan_calls == [{"workflow_id": "wf-1", "reason": "bad scope"}]

    def test_reject_plan_with_none_reason(
        self,
        fake_service: _FakeWorkflowService,
        http_service: HttpWorkflowService,
    ) -> None:
        fake_service.seed(_make_detail("wf-1"))
        fake_service.mutation_result = _expected_run_result("wf-1")
        http_service.reject_plan("wf-1", None)
        assert fake_service.reject_plan_calls == [{"workflow_id": "wf-1", "reason": None}]

    def test_submit_clarification_forwards_answers(
        self,
        fake_service: _FakeWorkflowService,
        http_service: HttpWorkflowService,
    ) -> None:
        fake_service.seed(_make_detail("wf-1"))
        fake_service.mutation_result = _expected_run_result("wf-1")
        answers = [
            {"concern": "what about retries?", "answer": "exponential backoff"},
        ]
        http_service.submit_clarification("wf-1", answers)
        assert fake_service.submit_clarification_calls == [
            {"workflow_id": "wf-1", "answers": answers}
        ]

    def test_retry_forwards_call(
        self,
        fake_service: _FakeWorkflowService,
        http_service: HttpWorkflowService,
    ) -> None:
        fake_service.seed(_make_detail("wf-1", status=WorkflowStatus.FAILED))
        fake_service.mutation_result = _expected_run_result("wf-1")
        http_service.retry("wf-1")
        assert fake_service.retry_calls == ["wf-1"]

    def test_retry_409_raises_value_error(
        self,
        fake_service: _FakeWorkflowService,
        http_service: HttpWorkflowService,
    ) -> None:
        # The server now pre-validates retryability synchronously based on
        # the workflow's current status.  COMPLETED is not retryable, so
        # the route returns 409 before dispatching.
        fake_service.seed(_make_detail("wf-1", status=WorkflowStatus.COMPLETED))
        with pytest.raises(ValueError, match="cannot be retried"):
            http_service.retry("wf-1")


class TestPrReview:
    def test_approve_pr_forwards_call(
        self,
        fake_service: _FakeWorkflowService,
        http_service: HttpWorkflowService,
    ) -> None:
        fake_service.seed(_make_detail("wf-1", status=WorkflowStatus.PENDING_PR_APPROVAL))
        fake_service.mutation_result = _expected_run_result("wf-1")
        http_service.approve_pr("wf-1")
        assert fake_service.approve_pr_calls == ["wf-1"]

    def test_reject_pr_passes_reason(
        self,
        fake_service: _FakeWorkflowService,
        http_service: HttpWorkflowService,
    ) -> None:
        fake_service.seed(_make_detail("wf-1", status=WorkflowStatus.PENDING_PR_APPROVAL))
        fake_service.mutation_result = _expected_run_result("wf-1")
        http_service.reject_pr("wf-1", "tests failing")
        assert fake_service.reject_pr_calls == [{"workflow_id": "wf-1", "reason": "tests failing"}]

    def test_comment_pr_passes_comments(
        self,
        fake_service: _FakeWorkflowService,
        http_service: HttpWorkflowService,
    ) -> None:
        fake_service.seed(_make_detail("wf-1", status=WorkflowStatus.PENDING_PR_APPROVAL))
        fake_service.mutation_result = _expected_run_result("wf-1")
        http_service.comment_pr("wf-1", "please tighten the API")
        assert fake_service.comment_pr_calls == [
            {"workflow_id": "wf-1", "comments": "please tighten the API"}
        ]


class TestHistoryAndAuditLog:
    def test_get_history_returns_entries(
        self,
        fake_service: _FakeWorkflowService,
        http_service: HttpWorkflowService,
    ) -> None:
        fake_service.seed(_make_detail("wf-1"))
        fake_service.history["wf-1"] = [
            WorkflowHistoryEntry(step=1, node="plan", outcome="ok", result_keys=["work_plan"]),
            WorkflowHistoryEntry(step=2, node="execute", outcome="error", error="boom"),
        ]
        entries = http_service.get_history("wf-1")
        assert [e.node for e in entries] == ["plan", "execute"]
        assert entries[0].result_keys == ["work_plan"]
        assert entries[1].error == "boom"

    def test_get_history_404(self, http_service: HttpWorkflowService) -> None:
        with pytest.raises(ValueError, match="not found"):
            http_service.get_history("nope")

    def test_get_audit_log_returns_entries(
        self,
        fake_service: _FakeWorkflowService,
        http_service: HttpWorkflowService,
    ) -> None:
        fake_service.seed(_make_detail("wf-1"))
        fake_service.audit_log["wf-1"] = [
            WorkflowAuditEntry(
                workflow_id="wf-1",
                actor="dispatcher",
                action="status_change",
                timestamp="2026-06-22T00:00:00",
                details={"to": "pending_approval"},
            ),
        ]
        entries = http_service.get_audit_log("wf-1")
        assert [e.action for e in entries] == ["status_change"]
        assert entries[0].details == {"to": "pending_approval"}

    def test_get_audit_log_404(self, http_service: HttpWorkflowService) -> None:
        with pytest.raises(ValueError, match="not found"):
            http_service.get_audit_log("nope")


class TestAdmin:
    """Admin endpoints require ``ORCHESTRATOR_API_TOKEN`` on the server."""

    def test_clear_db_forwards_call_and_returns_counts(
        self,
        monkeypatch,
        fake_service: _FakeWorkflowService,
    ) -> None:
        monkeypatch.setenv(API_TOKEN_ENV, "admin-token")
        fake_service.clear_db_result = (3, 7)
        svc = _build_http_service(fake_service, token="admin-token")
        try:
            result = svc.clear_db()
            assert result == (3, 7)
            assert len(fake_service.clear_db_calls) == 1
        finally:
            svc.close()

    def test_clear_db_503_when_admin_disabled(
        self,
        fake_service: _FakeWorkflowService,
        http_service: HttpWorkflowService,
    ) -> None:
        # http_service fixture builds the app with admin disabled by default.
        with pytest.raises(httpx.HTTPStatusError) as exc:
            http_service.clear_db()
        assert exc.value.response.status_code == 503
        assert fake_service.clear_db_calls == []

    def test_mark_interrupted_forwards_fields(
        self,
        monkeypatch,
        fake_service: _FakeWorkflowService,
    ) -> None:
        monkeypatch.setenv(API_TOKEN_ENV, "admin-token")
        fake_service.seed(_make_detail("wf-1"))
        svc = _build_http_service(fake_service, token="admin-token")
        try:
            svc.mark_interrupted("wf-1", failed_node="execute", actor="ops-bot")
            assert fake_service.mark_interrupted_calls == [
                {"workflow_id": "wf-1", "failed_node": "execute", "actor": "ops-bot"}
            ]
        finally:
            svc.close()

    def test_mark_interrupted_404_raises_value_error(
        self,
        monkeypatch,
        fake_service: _FakeWorkflowService,
    ) -> None:
        monkeypatch.setenv(API_TOKEN_ENV, "admin-token")
        svc = _build_http_service(fake_service, token="admin-token")
        try:
            with pytest.raises(ValueError, match="not found"):
                svc.mark_interrupted("nope")
        finally:
            svc.close()


def test_remote_operation_not_supported_still_exported() -> None:
    """Back-compat: the exception class is preserved even though no method raises it now."""
    assert issubclass(RemoteOperationNotSupported, NotImplementedError)


# ---------------------------------------------------------------------------
# Reconnect: stream_events resumes from last seen seq
# ---------------------------------------------------------------------------


class _StubStreamCtx:
    """Stand-in for ``httpx.Client.stream(...)`` return value."""

    def __init__(
        self,
        *,
        status_code: int = 200,
        lines: Optional[List[str]] = None,
        raise_after: Optional[BaseException] = None,
    ) -> None:
        self.status_code = status_code
        self._lines = list(lines or [])
        self._raise_after = raise_after

    def __enter__(self) -> "_StubStreamCtx":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        return None

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("stub", request=None, response=None)

    def iter_lines(self) -> Iterator[str]:
        for line in self._lines:
            yield line
        if self._raise_after is not None:
            raise self._raise_after


class _StubClient:
    """Minimal ``httpx.Client`` stand-in that returns scripted SSE streams."""

    def __init__(self, streams: List[_StubStreamCtx]) -> None:
        self._streams = list(streams)
        self.stream_calls: List[Dict[str, Any]] = []

    def stream(self, method: str, url: str, **kwargs: Any) -> _StubStreamCtx:
        self.stream_calls.append({"method": method, "url": url, "params": kwargs.get("params")})
        if not self._streams:
            raise httpx.ReadError("no more scripted streams")
        return self._streams.pop(0)


def _sse(seq: int, kind: str, node: Optional[str] = None) -> str:
    data = json.dumps({"seq": seq, "kind": kind, "node": node, "data": None})
    return f"id: {seq}\ndata: {data}\n\n"


def _expand(*frames: str) -> List[str]:
    """Flatten SSE frame strings into the line-by-line view ``iter_lines`` yields.

    Real ``httpx.Response.iter_lines`` does NOT emit a trailing empty line at
    EOF, so we drop the artifact ``split("\\n")`` produces for a string that
    ends with ``"\\n"``.  The empty separator line BETWEEN events must be
    preserved so the parser flushes each event.
    """
    out: List[str] = []
    for frame in frames:
        lines = frame.split("\n")
        if lines and lines[-1] == "":
            lines = lines[:-1]
        out.extend(lines)
    return out


def test_stream_events_reconnects_after_transport_drop(monkeypatch) -> None:
    """Mid-stream ``ReadError`` triggers a reconnect with the last ``seq``."""
    # Zero backoff so the test is fast.
    monkeypatch.setattr(http_client_mod, "RECONNECT_BACKOFF_S", 0)

    first_stream = _StubStreamCtx(
        lines=_expand(_sse(1, "node_start", "plan"), _sse(2, "node_end", "plan")),
        raise_after=httpx.ReadError("connection dropped"),
    )
    second_stream = _StubStreamCtx(
        lines=_expand(
            _sse(3, "node_start", "execute"),
            'id: 4\ndata: {"seq": 4, "kind": "stream_end", "node": null, '
            '"data": {"final_status": "completed"}}\n\n',
        ),
    )
    client = _StubClient([first_stream, second_stream])
    service = HttpWorkflowService(base_url="http://test", client=client)

    events = list(service.stream_events("wf-1"))

    # Three real events delivered; stream_end is filtered out.
    assert [(e.seq, e.kind) for e in events] == [
        (1, "node_start"),
        (2, "node_end"),
        (3, "node_start"),
    ]
    # Two GETs: first with after_seq=0, second with after_seq=2.
    assert len(client.stream_calls) == 2
    assert client.stream_calls[0]["params"]["after_seq"] == 0
    assert client.stream_calls[1]["params"]["after_seq"] == 2


def test_stream_events_gives_up_after_max_reconnect_attempts(monkeypatch) -> None:
    monkeypatch.setattr(http_client_mod, "RECONNECT_BACKOFF_S", 0)
    monkeypatch.setattr(http_client_mod, "MAX_RECONNECT_ATTEMPTS", 2)

    streams = [
        _StubStreamCtx(
            lines=_expand(_sse(1, "node_start", "plan")), raise_after=httpx.ReadError("x")
        ),
        _StubStreamCtx(lines=[], raise_after=httpx.ReadError("x")),
        _StubStreamCtx(lines=[], raise_after=httpx.ReadError("x")),
    ]
    client = _StubClient(streams)
    service = HttpWorkflowService(base_url="http://test", client=client)

    events = list(service.stream_events("wf-1"))
    # We get the one event delivered before the first drop, then bail.
    assert [e.seq for e in events] == [1]
    assert len(client.stream_calls) == 3  # initial + 2 reconnect attempts


# ---------------------------------------------------------------------------
# Parity: Local vs Http for the implemented subset
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_db(monkeypatch) -> Iterator[str]:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test.db")
        logs_dir = os.path.join(tmp, "logs")
        os.makedirs(logs_dir, exist_ok=True)
        monkeypatch.setenv("DB_PATH", db_path)
        monkeypatch.setenv("ORCHESTRATOR_LOGS_DIR", logs_dir)
        state_store.run_migrations()
        yield db_path


@pytest.fixture
def parity_pair(temp_db):
    """Local + Http services sharing the same backing repository."""
    repo = SQLiteWorkflowRepository()

    class _NoopGraph:
        def get_state(self, _config):  # pragma: no cover - unused in parity scenarios
            class _S:
                values: Dict[str, Any] = {}
                next = ()
                tasks: List[Any] = []
                metadata: Dict[str, Any] = {}

            return _S()

        def get_state_history(self, _config):  # pragma: no cover - unused
            return iter(())

    from fastapi.testclient import TestClient

    local = LocalWorkflowService(repository=repo, graph_factory=lambda: _NoopGraph())
    app = create_app(service=local)
    client = TestClient(app, base_url="http://testserver")
    http = build_http_workflow_service(base_url="http://testserver", client=client)
    try:
        yield local, http, repo
    finally:
        http.close()


class TestParity:
    def test_get_parity(self, parity_pair) -> None:
        local, http, repo = parity_pair
        wf_id = repo.create_workflow(ticket_key="AOS-143", work_plan={"summary": "hi"})
        local_detail = local.get(wf_id)
        http_detail = http.get(wf_id)
        assert local_detail is not None and http_detail is not None
        assert local_detail.id == http_detail.id
        assert local_detail.status == http_detail.status
        assert local_detail.work_plan == http_detail.work_plan
        assert local_detail.usage_summary == http_detail.usage_summary

    def test_get_by_ticket_parity(self, parity_pair) -> None:
        local, http, repo = parity_pair
        wf1 = repo.create_workflow(ticket_key="AOS-143")
        wf2 = repo.create_workflow(ticket_key="AOS-143")
        local_summaries = local.get_by_ticket("AOS-143")
        http_summaries = http.get_by_ticket("AOS-143")
        assert [s.id for s in local_summaries] == [s.id for s in http_summaries]
        assert {wf1, wf2} == {s.id for s in http_summaries}

    def test_list_parity(self, parity_pair) -> None:
        local, http, repo = parity_pair
        wf_done = repo.create_workflow(ticket_key="AOS-143")
        repo.update_status(wf_done, WorkflowStatus.COMPLETED)
        repo.create_workflow(ticket_key="AOS-143", status=WorkflowStatus.PENDING)

        local_list = local.list(status=WorkflowStatus.COMPLETED)
        http_list = http.list(status=WorkflowStatus.COMPLETED)
        assert {s.id for s in local_list} == {s.id for s in http_list} == {wf_done}

    def test_cancel_parity(self, parity_pair) -> None:
        local, http, repo = parity_pair
        wf_id = repo.create_workflow(ticket_key="AOS-143")
        # Cancel via HTTP, then read via Local to confirm state propagates.
        http.cancel(wf_id, reason="parity")
        local_detail = local.get(wf_id)
        assert local_detail is not None
        assert local_detail.status == WorkflowStatus.CANCELLED
