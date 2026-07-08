"""HTTP route tests for :mod:`orchestrator.server`.

Use a :class:`FakeWorkflowService` so the tests exercise the full FastAPI
stack — auth, routing, schema validation, OpenAPI — without spinning up
LangGraph or SQLite.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional

import pytest
from fastapi.testclient import TestClient

from orchestrator.server.app import create_app
from orchestrator.server.auth import ADMIN_ALLOW_UNAUTHENTICATED_ENV, API_TOKEN_ENV
from orchestrator.server.background import SyncBackgroundDispatcher
from orchestrator.workflow_service.dtos import (
    WorkflowAuditEntry,
    WorkflowDetail,
    WorkflowEvent,
    WorkflowHistoryEntry,
    WorkflowLogChunk,
    WorkflowRunResult,
    WorkflowStartRequest,
    WorkflowSummary,
)
from state.workflow_status import WorkflowStatus

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeWorkflowService:
    """In-memory WorkflowService stand-in for HTTP route tests."""

    def __init__(self) -> None:
        self.start_calls: List[WorkflowStartRequest] = []
        self.prepare_start_calls: List[WorkflowStartRequest] = []
        self.cancel_calls: List[Dict[str, Any]] = []
        self.list_calls: List[Dict[str, Any]] = []
        self.workflows: Dict[str, WorkflowDetail] = {}
        self.start_result: Optional[WorkflowRunResult] = None
        self.start_exc: Optional[BaseException] = None
        # Streaming hooks: tests append WorkflowEvent / WorkflowLogChunk values
        # here at any time; the SSE generators pick them up on the next poll.
        # Use lists keyed by workflow_id so multiple workflows can be streamed
        # in the same test without crosstalk.
        self.events: Dict[str, List[WorkflowEvent]] = {}
        self.log_bytes: Dict[str, Dict[str, bytes]] = {}
        self.read_logs_calls: List[Dict[str, Any]] = []
        self.stream_events_calls: List[Dict[str, Any]] = []
        # New in AOS-147: per-method call recorders, canned results, and
        # canned exceptions so tests can drive the route layer without a
        # real LocalWorkflowService.
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

    # ------------------------- helpers --------------------------------
    def seed(self, detail: WorkflowDetail) -> None:
        self.workflows[detail.id] = detail

    def set_status(self, workflow_id: str, new_status: WorkflowStatus) -> None:
        """Helper for streaming tests to flip a seeded workflow to terminal."""
        existing = self.workflows[workflow_id]
        self.workflows[workflow_id] = WorkflowDetail(
            id=existing.id,
            ticket_key=existing.ticket_key,
            status=new_status,
            created_at=existing.created_at,
            updated_at=existing.updated_at,
            pr_url=existing.pr_url,
            work_plan=existing.work_plan,
            code_generation_summary=existing.code_generation_summary,
            clarification_history=list(existing.clarification_history),
            pr_comments=existing.pr_comments,
            usage_summary=dict(existing.usage_summary),
            retry_count=existing.retry_count,
        )

    def append_log(self, workflow_id: str, stage: str, text: str) -> None:
        """Append bytes to the simulated stage log buffer."""
        self.log_bytes.setdefault(workflow_id, {}).setdefault(stage, b"")
        self.log_bytes[workflow_id][stage] += text.encode("utf-8")

    def add_event(self, event: WorkflowEvent) -> None:
        """Append a WorkflowEvent to the queue for ``event.workflow_id``."""
        self.events.setdefault(event.workflow_id, []).append(event)

    def _to_summary(self, detail: WorkflowDetail) -> WorkflowSummary:
        return WorkflowSummary(
            id=detail.id,
            ticket_key=detail.ticket_key,
            status=detail.status,
            created_at=detail.created_at,
            updated_at=detail.updated_at,
            pr_url=detail.pr_url,
        )

    # ------------------------- reads ----------------------------------
    def get(self, workflow_id: str) -> Optional[WorkflowDetail]:
        return self.workflows.get(workflow_id)

    def get_by_ticket(self, ticket_key: str) -> List[WorkflowSummary]:
        return [self._to_summary(w) for w in self.workflows.values() if w.ticket_key == ticket_key]

    def get_latest_retryable_by_ticket(self, ticket_key: str) -> Optional[WorkflowSummary]:
        return None

    def list(
        self,
        ticket_key: Optional[str] = None,
        status: Optional[WorkflowStatus] = None,
        limit: int = 50,
    ) -> List[WorkflowSummary]:
        self.list_calls.append({"ticket_key": ticket_key, "status": status, "limit": limit})
        results: List[WorkflowSummary] = []
        for detail in self.workflows.values():
            if ticket_key is not None and detail.ticket_key != ticket_key:
                continue
            if status is not None and detail.status != status:
                continue
            results.append(self._to_summary(detail))
        return results[:limit]

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
        self.read_logs_calls.append(
            {"workflow_id": workflow_id, "stage": stage, "after_offset": after_offset}
        )
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
    ) -> Iterable[WorkflowEvent]:
        self.stream_events_calls.append({"workflow_id": workflow_id, "after_seq": after_seq})
        return iter([e for e in self.events.get(workflow_id, []) if e.seq > after_seq])

    # ------------------------- mutations ------------------------------
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

    # ------------------------- graph ops ------------------------------
    def prepare_start(self, request: WorkflowStartRequest) -> WorkflowStartRequest:
        self.prepare_start_calls.append(request)
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
        if self.start_exc is not None:
            raise self.start_exc
        if self.start_result is not None:
            result = self.start_result
        else:
            result = WorkflowRunResult(
                workflow_id=request.workflow_id or "wf-generated",
                ticket_key=request.ticket_key,
                final_status=WorkflowStatus.PENDING_APPROVAL,
                interrupted=True,
            )
        # Mirror the post-run status into the prepared row (or create one
        # for backward-compat callers that bypass prepare_start) so that
        # ``service.get`` returns the post-run state when run inline.
        wf_id = request.workflow_id or result.workflow_id or "wf-generated"
        if wf_id in self.workflows:
            self._mirror_result(wf_id, result)
        else:
            self.workflows[wf_id] = _make_detail(
                wf_id,
                status=result.final_status,
                ticket_key=result.ticket_key or request.ticket_key,
            )
        return result

    def _mutation_result_or_default(self, workflow_id: str) -> WorkflowRunResult:
        if self.mutation_exc is not None:
            raise self.mutation_exc
        if self.mutation_result is not None:
            result = self.mutation_result
        else:
            existing = self.workflows.get(workflow_id)
            result = WorkflowRunResult(
                workflow_id=workflow_id,
                ticket_key=existing.ticket_key if existing else None,
                final_status=existing.status if existing else WorkflowStatus.PENDING,
            )
        # Mirror the terminal status into the fake workflow row so that
        # ``service.get(workflow_id)`` (called by the route's
        # ``_snapshot_response`` helper) reflects the post-run state.
        self._mirror_result(workflow_id, result)
        return result

    def _mirror_result(self, workflow_id: str, result: WorkflowRunResult) -> None:
        existing = self.workflows.get(workflow_id)
        if existing is not None:
            self.workflows[workflow_id] = WorkflowDetail(
                id=existing.id,
                ticket_key=existing.ticket_key,
                status=result.final_status,
                created_at=existing.created_at,
                updated_at=existing.updated_at,
                pr_url=existing.pr_url,
                work_plan=existing.work_plan,
                code_generation_summary=existing.code_generation_summary,
                clarification_history=existing.clarification_history,
                pr_comments=existing.pr_comments,
                usage_summary=existing.usage_summary,
                retry_count=existing.retry_count,
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_service() -> FakeWorkflowService:
    return FakeWorkflowService()


@pytest.fixture
def sync_dispatcher() -> SyncBackgroundDispatcher:
    """A sync dispatcher so route tests observe the terminal state inline."""
    return SyncBackgroundDispatcher()


@pytest.fixture
def client(
    monkeypatch,
    fake_service: FakeWorkflowService,
    sync_dispatcher: SyncBackgroundDispatcher,
) -> TestClient:
    # Default: auth disabled.  Tests that exercise auth opt in via monkeypatch.
    monkeypatch.delenv(API_TOKEN_ENV, raising=False)
    monkeypatch.delenv(ADMIN_ALLOW_UNAUTHENTICATED_ENV, raising=False)
    app = create_app(service=fake_service, background_dispatcher=sync_dispatcher)
    return TestClient(app)


def _make_detail(
    workflow_id: str,
    *,
    status: WorkflowStatus = WorkflowStatus.IN_PROGRESS,
    ticket_key: str = "AOS-141",
) -> WorkflowDetail:
    return WorkflowDetail(
        id=workflow_id,
        ticket_key=ticket_key,
        status=status,
        created_at="2026-06-21T00:00:00",
        updated_at="2026-06-21T00:00:00",
        pr_url=None,
        work_plan=None,
        code_generation_summary=None,
        clarification_history=[],
        pr_comments=None,
        usage_summary={},
        retry_count=0,
    )


# ---------------------------------------------------------------------------
# /healthz + OpenAPI
# ---------------------------------------------------------------------------


def test_healthz_returns_ok(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_schema_exposes_all_routes(client: TestClient) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/healthz" in paths
    assert "/workflows" in paths
    assert "/workflows/{workflow_id}" in paths
    assert "/workflows/{workflow_id}/cancel" in paths


# ---------------------------------------------------------------------------
# POST /workflows
# ---------------------------------------------------------------------------


def test_start_workflow_happy_path(client: TestClient, fake_service: FakeWorkflowService) -> None:
    fake_service.start_result = WorkflowRunResult(
        workflow_id="wf-1",
        ticket_key="AOS-141",
        final_status=WorkflowStatus.PENDING_APPROVAL,
        interrupted=True,
    )
    response = client.post("/workflows", json={"ticket_key": "AOS-141"})
    # Fire-and-forget: 202 Accepted with a snapshot of the reserved row.
    assert response.status_code == 202
    body = response.json()
    # The route allocates the workflow id via prepare_start and returns it
    # in the snapshot — the start_result.workflow_id is informational.
    assert body["workflow_id"] == "wf-prepared"
    assert body["ticket_key"] == "AOS-141"
    # SyncBackgroundDispatcher ran the start inline so the mirrored row
    # already reflects the post-run status.
    assert body["final_status"] == "pending_approval"
    assert len(fake_service.start_calls) == 1
    assert fake_service.start_calls[0].ticket_key == "AOS-141"
    assert fake_service.start_calls[0].dry_run is False
    assert fake_service.start_calls[0].workflow_id == "wf-prepared"


def test_start_workflow_passes_optional_fields(
    client: TestClient, fake_service: FakeWorkflowService
) -> None:
    response = client.post(
        "/workflows",
        json={"ticket_key": "AOS-141", "dry_run": True, "workflow_id": "custom-id"},
    )
    # Dry-run still uses 202 (same response_model and status code).
    assert response.status_code == 202
    req = fake_service.start_calls[0]
    assert req.dry_run is True
    assert req.workflow_id == "custom-id"


def test_start_workflow_rejects_missing_ticket_key(client: TestClient) -> None:
    response = client.post("/workflows", json={})
    assert response.status_code == 422  # Pydantic validation error


def test_start_workflow_rejects_empty_ticket_key(client: TestClient) -> None:
    response = client.post("/workflows", json={"ticket_key": ""})
    assert response.status_code == 422


def test_start_workflow_rejects_unknown_field(client: TestClient) -> None:
    response = client.post("/workflows", json={"ticket_key": "AOS-1", "bogus": True})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /workflows
# ---------------------------------------------------------------------------


def test_list_workflows_returns_all(client: TestClient, fake_service: FakeWorkflowService) -> None:
    fake_service.seed(_make_detail("wf-1"))
    fake_service.seed(_make_detail("wf-2", ticket_key="AOS-99"))
    response = client.get("/workflows")
    assert response.status_code == 200
    ids = {row["id"] for row in response.json()}
    assert ids == {"wf-1", "wf-2"}


def test_list_workflows_filters_by_ticket(
    client: TestClient, fake_service: FakeWorkflowService
) -> None:
    fake_service.seed(_make_detail("wf-1", ticket_key="AOS-141"))
    fake_service.seed(_make_detail("wf-2", ticket_key="AOS-99"))
    response = client.get("/workflows", params={"ticket_key": "AOS-141"})
    assert response.status_code == 200
    body = response.json()
    assert [row["id"] for row in body] == ["wf-1"]
    assert fake_service.list_calls[-1]["ticket_key"] == "AOS-141"


def test_list_workflows_filters_by_status(
    client: TestClient, fake_service: FakeWorkflowService
) -> None:
    fake_service.seed(_make_detail("wf-1", status=WorkflowStatus.COMPLETED))
    fake_service.seed(_make_detail("wf-2", status=WorkflowStatus.IN_PROGRESS))
    response = client.get("/workflows", params={"status": "completed"})
    assert response.status_code == 200
    assert [row["id"] for row in response.json()] == ["wf-1"]
    assert fake_service.list_calls[-1]["status"] == WorkflowStatus.COMPLETED


def test_list_workflows_rejects_invalid_status(client: TestClient) -> None:
    response = client.get("/workflows", params={"status": "not-a-status"})
    assert response.status_code == 400
    assert "Unknown status" in response.json()["detail"]


def test_list_workflows_rejects_invalid_limit(client: TestClient) -> None:
    response = client.get("/workflows", params={"limit": "0"})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /workflows/{id}
# ---------------------------------------------------------------------------


def test_get_workflow_returns_detail(client: TestClient, fake_service: FakeWorkflowService) -> None:
    fake_service.seed(_make_detail("wf-1"))
    response = client.get("/workflows/wf-1")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "wf-1"
    assert body["status"] == "in_progress"
    assert body["clarification_history"] == []
    assert body["usage_summary"] == {}
    assert body["retry_count"] == 0


def test_get_workflow_404_when_missing(client: TestClient) -> None:
    response = client.get("/workflows/nope")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# POST /workflows/{id}/cancel
# ---------------------------------------------------------------------------


def test_cancel_workflow_happy_path(client: TestClient, fake_service: FakeWorkflowService) -> None:
    fake_service.seed(_make_detail("wf-1"))
    response = client.post("/workflows/wf-1/cancel", json={"reason": "drop it"})
    assert response.status_code == 204
    assert response.content == b""
    call = fake_service.cancel_calls[-1]
    assert call["workflow_id"] == "wf-1"
    assert call["reason"] == "drop it"
    assert call["actor"] == "api"


def test_cancel_workflow_with_empty_body(
    client: TestClient, fake_service: FakeWorkflowService
) -> None:
    fake_service.seed(_make_detail("wf-1"))
    response = client.post("/workflows/wf-1/cancel")
    assert response.status_code == 204
    call = fake_service.cancel_calls[-1]
    assert call["reason"] is None
    assert call["actor"] == "api"


def test_cancel_workflow_custom_actor(
    client: TestClient, fake_service: FakeWorkflowService
) -> None:
    fake_service.seed(_make_detail("wf-1"))
    response = client.post("/workflows/wf-1/cancel", json={"actor": "ops-bot", "reason": "cleanup"})
    assert response.status_code == 204
    call = fake_service.cancel_calls[-1]
    assert call["actor"] == "ops-bot"


def test_cancel_workflow_404_when_missing(
    client: TestClient, fake_service: FakeWorkflowService
) -> None:
    response = client.post("/workflows/nope/cancel")
    assert response.status_code == 404
    assert fake_service.cancel_calls == []


def test_cancel_workflow_409_when_terminal(
    client: TestClient, fake_service: FakeWorkflowService
) -> None:
    fake_service.seed(_make_detail("wf-1", status=WorkflowStatus.COMPLETED))
    response = client.post("/workflows/wf-1/cancel")
    assert response.status_code == 409
    assert "terminal" in response.json()["detail"].lower()
    assert fake_service.cancel_calls == []


class _SpyDispatcher(SyncBackgroundDispatcher):
    """SyncBackgroundDispatcher that records cancel() invocations."""

    def __init__(self) -> None:
        super().__init__()
        self.cancel_calls: List[str] = []

    def cancel(self, workflow_id: str) -> None:
        self.cancel_calls.append(workflow_id)


def test_cancel_workflow_invokes_dispatcher_cancel(
    fake_service: FakeWorkflowService,
) -> None:
    fake_service.seed(_make_detail("wf-1"))
    dispatcher = _SpyDispatcher()
    app = create_app(service=fake_service, background_dispatcher=dispatcher)
    with TestClient(app) as spy_client:
        response = spy_client.post("/workflows/wf-1/cancel")
    assert response.status_code == 204
    assert dispatcher.cancel_calls == ["wf-1"]


def test_cancel_workflow_does_not_cancel_on_404(
    fake_service: FakeWorkflowService,
) -> None:
    dispatcher = _SpyDispatcher()
    app = create_app(service=fake_service, background_dispatcher=dispatcher)
    with TestClient(app) as spy_client:
        response = spy_client.post("/workflows/nope/cancel")
    assert response.status_code == 404
    assert dispatcher.cancel_calls == []


def test_cancel_workflow_does_not_cancel_on_409_terminal(
    fake_service: FakeWorkflowService,
) -> None:
    fake_service.seed(_make_detail("wf-1", status=WorkflowStatus.COMPLETED))
    dispatcher = _SpyDispatcher()
    app = create_app(service=fake_service, background_dispatcher=dispatcher)
    with TestClient(app) as spy_client:
        response = spy_client.post("/workflows/wf-1/cancel")
    assert response.status_code == 409
    assert dispatcher.cancel_calls == []


# ---------------------------------------------------------------------------
# Auth stub
# ---------------------------------------------------------------------------


def test_auth_disabled_when_env_unset(
    client: TestClient, fake_service: FakeWorkflowService
) -> None:
    # Sanity: no token, but request without Authorization succeeds.
    fake_service.seed(_make_detail("wf-1"))
    response = client.get("/workflows/wf-1")
    assert response.status_code == 200


def test_auth_required_when_token_configured(
    monkeypatch, fake_service: FakeWorkflowService
) -> None:
    monkeypatch.setenv(API_TOKEN_ENV, "secret-token")
    app = create_app(service=fake_service)
    fake_service.seed(_make_detail("wf-1"))
    with TestClient(app) as authed_client:
        # No Authorization header → 401
        unauth = authed_client.get("/workflows/wf-1")
        assert unauth.status_code == 401

        # Wrong token → 401
        wrong = authed_client.get("/workflows/wf-1", headers={"Authorization": "Bearer nope"})
        assert wrong.status_code == 401

        # Malformed header → 401
        bad_scheme = authed_client.get(
            "/workflows/wf-1", headers={"Authorization": "Basic secret-token"}
        )
        assert bad_scheme.status_code == 401

        # Correct token → 200
        ok = authed_client.get("/workflows/wf-1", headers={"Authorization": "Bearer secret-token"})
        assert ok.status_code == 200


def test_healthz_remains_open_when_auth_enabled(
    monkeypatch, fake_service: FakeWorkflowService
) -> None:
    monkeypatch.setenv(API_TOKEN_ENV, "secret-token")
    app = create_app(service=fake_service)
    with TestClient(app) as authed_client:
        response = authed_client.get("/healthz")
        assert response.status_code == 200


def test_empty_token_value_disables_auth(monkeypatch, fake_service: FakeWorkflowService) -> None:
    monkeypatch.setenv(API_TOKEN_ENV, "   ")
    app = create_app(service=fake_service)
    fake_service.seed(_make_detail("wf-1"))
    with TestClient(app) as client_:
        response = client_.get("/workflows/wf-1")
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# SSE streaming — events
# ---------------------------------------------------------------------------


def _parse_sse(raw: bytes) -> List[Dict[str, Optional[str]]]:
    """Minimal SSE parser — splits on blank lines, captures id + data lines.

    Returns a list of ``{"id": <str or None>, "data": <joined str — empty
    when the frame has no data lines>, "comment": <str or None>}`` dicts in
    the order they appeared.  ``data`` is always a ``str`` (never ``None``)
    so callers can pass it straight to ``json.loads`` without narrowing.
    """
    text = raw.decode("utf-8")
    frames: List[Dict[str, Optional[str]]] = []
    for block in text.split("\n\n"):
        block = block.strip("\n")
        if not block:
            continue
        event_id: Optional[str] = None
        comment: Optional[str] = None
        data_lines: List[str] = []
        for line in block.split("\n"):
            if line.startswith(":"):
                comment = line[1:].strip()
            elif line.startswith("id:"):
                event_id = line[len("id:") :].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].lstrip())
        frames.append(
            {
                "id": event_id,
                "data": "\n".join(data_lines),
                "comment": comment,
            }
        )
    return frames


def _data(frame: Dict[str, Optional[str]]) -> str:
    """Narrow ``frame['data']`` to ``str`` for mypy."""
    value = frame["data"]
    assert value is not None
    return value


def _make_event(workflow_id: str, seq: int, kind: str, node: Optional[str] = None) -> WorkflowEvent:
    return WorkflowEvent(workflow_id=workflow_id, seq=seq, kind=kind, node=node)


def test_events_stream_replays_history_and_closes_on_terminal(
    client: TestClient, fake_service: FakeWorkflowService
) -> None:
    fake_service.seed(_make_detail("wf-1", status=WorkflowStatus.COMPLETED))
    fake_service.add_event(_make_event("wf-1", 1, "node_start", "plan"))
    fake_service.add_event(_make_event("wf-1", 2, "node_end", "plan"))
    fake_service.add_event(_make_event("wf-1", 3, "node_end", "execute"))

    with client.stream("GET", "/workflows/wf-1/events") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-cache"
        body = b"".join(response.iter_bytes())

    frames = _parse_sse(body)
    # 3 events + final stream_end
    assert len(frames) == 4
    assert [f["id"] for f in frames] == ["1", "2", "3", "3"]
    parsed = [json.loads(_data(f)) for f in frames]
    assert parsed[0]["kind"] == "node_start"
    assert parsed[0]["node"] == "plan"
    assert parsed[2]["kind"] == "node_end"
    assert parsed[3]["kind"] == "stream_end"
    assert parsed[3]["data"]["final_status"] == "completed"


def test_events_stream_respects_after_seq_query(
    client: TestClient, fake_service: FakeWorkflowService
) -> None:
    fake_service.seed(_make_detail("wf-1", status=WorkflowStatus.COMPLETED))
    for i in (1, 2, 3):
        fake_service.add_event(_make_event("wf-1", i, "node_end", f"n{i}"))

    with client.stream("GET", "/workflows/wf-1/events?after_seq=2") as response:
        body = b"".join(response.iter_bytes())

    frames = _parse_sse(body)
    # Only event seq=3 + stream_end
    assert len(frames) == 2
    payload = json.loads(_data(frames[0]))
    assert payload["seq"] == 3
    assert payload["node"] == "n3"
    assert json.loads(_data(frames[1]))["kind"] == "stream_end"


def test_events_stream_respects_last_event_id_header(
    client: TestClient, fake_service: FakeWorkflowService
) -> None:
    fake_service.seed(_make_detail("wf-1", status=WorkflowStatus.COMPLETED))
    for i in (1, 2, 3):
        fake_service.add_event(_make_event("wf-1", i, "node_end", f"n{i}"))

    with client.stream(
        "GET",
        "/workflows/wf-1/events",
        headers={"Last-Event-ID": "1"},
    ) as response:
        body = b"".join(response.iter_bytes())

    frames = _parse_sse(body)
    seqs = [
        json.loads(_data(f))["seq"]
        for f in frames
        if json.loads(_data(f)).get("kind") != "stream_end"
    ]
    assert seqs == [2, 3]


def test_events_stream_query_param_wins_over_header(
    client: TestClient, fake_service: FakeWorkflowService
) -> None:
    fake_service.seed(_make_detail("wf-1", status=WorkflowStatus.COMPLETED))
    for i in (1, 2, 3):
        fake_service.add_event(_make_event("wf-1", i, "node_end", f"n{i}"))

    with client.stream(
        "GET",
        "/workflows/wf-1/events?after_seq=2",
        headers={"Last-Event-ID": "0"},
    ) as response:
        body = b"".join(response.iter_bytes())

    frames = _parse_sse(body)
    seqs = [
        json.loads(_data(f))["seq"]
        for f in frames
        if json.loads(_data(f)).get("kind") != "stream_end"
    ]
    assert seqs == [3]


def test_events_stream_404_when_workflow_missing(
    client: TestClient, fake_service: FakeWorkflowService
) -> None:
    response = client.get("/workflows/nope/events")
    assert response.status_code == 404


def test_events_stream_heartbeat_when_idle(
    monkeypatch, client: TestClient, fake_service: FakeWorkflowService
) -> None:
    from orchestrator.server import sse

    monkeypatch.setattr(sse, "HEARTBEAT_INTERVAL_S", 0.0)
    monkeypatch.setattr(sse, "EVENT_POLL_INTERVAL_S", 0.001)

    fake_service.seed(_make_detail("wf-1", status=WorkflowStatus.IN_PROGRESS))

    # Schedule a transition to terminal on a background thread so the
    # generator exits and the test does not hang.  The fake's set_status is
    # invoked from a non-asyncio thread; that is safe because TestClient runs
    # the request in its own thread and dict assignment is GIL-protected.
    import threading
    import time as _time

    def _terminate() -> None:
        _time.sleep(0.05)
        fake_service.set_status("wf-1", WorkflowStatus.COMPLETED)

    threading.Thread(target=_terminate, daemon=True).start()

    with client.stream("GET", "/workflows/wf-1/events") as response:
        body = b"".join(response.iter_bytes())

    assert b": ping" in body
    frames = _parse_sse(body)
    # The final frame must be stream_end so the generator exited cleanly.
    assert json.loads(_data(frames[-1]))["kind"] == "stream_end"


# ---------------------------------------------------------------------------
# SSE streaming — logs
# ---------------------------------------------------------------------------


def test_logs_stream_replays_workflow_log_and_closes_on_terminal(
    client: TestClient, fake_service: FakeWorkflowService
) -> None:
    fake_service.seed(_make_detail("wf-1", status=WorkflowStatus.COMPLETED))
    fake_service.append_log("wf-1", "workflow", "workflow-line-1\nworkflow-line-2\n")

    with client.stream("GET", "/workflows/wf-1/logs") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = b"".join(response.iter_bytes())

    frames = _parse_sse(body)
    # One log chunk + stream_end
    assert len(frames) >= 2
    log_frames = [f for f in frames if f["id"] is not None]
    payloads = [json.loads(_data(f)) for f in log_frames]
    stages = {p["stage"] for p in payloads}
    assert stages == {"workflow"}
    workflow_payload = next(p for p in payloads if p["stage"] == "workflow")
    assert workflow_payload["offset"] == 0
    assert workflow_payload["content"] == "workflow-line-1\nworkflow-line-2\n"
    assert workflow_payload["end_offset"] == len(workflow_payload["content"].encode("utf-8"))
    # Final frame: stream_end, no id
    final = frames[-1]
    assert final["id"] is None
    assert json.loads(_data(final))["kind"] == "stream_end"
    assert json.loads(_data(final))["final_status"] == "completed"


def test_logs_stream_respects_after_offset(
    client: TestClient, fake_service: FakeWorkflowService
) -> None:
    fake_service.seed(_make_detail("wf-1", status=WorkflowStatus.COMPLETED))
    fake_service.append_log("wf-1", "workflow", "hello world")

    with client.stream(
        "GET",
        "/workflows/wf-1/logs?stage=workflow&after_offset=6",
    ) as response:
        body = b"".join(response.iter_bytes())

    frames = _parse_sse(body)
    log_frames = [f for f in frames if f["id"] is not None]
    assert len(log_frames) == 1
    payload = json.loads(_data(log_frames[0]))
    assert payload["stage"] == "workflow"
    assert payload["offset"] == 6
    assert payload["content"] == "world"
    assert payload["end_offset"] == 11


def test_logs_stream_respects_last_event_id_header(
    client: TestClient, fake_service: FakeWorkflowService
) -> None:
    fake_service.seed(_make_detail("wf-1", status=WorkflowStatus.COMPLETED))
    fake_service.append_log("wf-1", "workflow", "abcdef")

    with client.stream(
        "GET",
        "/workflows/wf-1/logs?stage=workflow",
        headers={"Last-Event-ID": "3"},
    ) as response:
        body = b"".join(response.iter_bytes())

    log_frames = [f for f in _parse_sse(body) if f["id"] is not None]
    payload = json.loads(_data(log_frames[0]))
    assert payload["offset"] == 3
    assert payload["content"] == "def"


def test_logs_stream_workflow_stage_filter(
    client: TestClient,
    fake_service: FakeWorkflowService,
) -> None:
    fake_service.seed(_make_detail("wf-1", status=WorkflowStatus.COMPLETED))
    fake_service.append_log("wf-1", "workflow", "WORKFLOW")

    with client.stream("GET", "/workflows/wf-1/logs?stage=workflow") as response:
        body = b"".join(response.iter_bytes())

    log_frames = [f for f in _parse_sse(body) if f["id"] is not None]
    assert len(log_frames) == 1
    payload = json.loads(_data(log_frames[0]))
    assert payload["stage"] == "workflow"
    assert payload["content"] == "WORKFLOW"


def test_logs_stream_404_when_workflow_missing(client: TestClient) -> None:
    response = client.get("/workflows/nope/logs")
    assert response.status_code == 404


def test_logs_stream_terminal_with_no_logs_yields_only_stream_end(
    client: TestClient, fake_service: FakeWorkflowService
) -> None:
    fake_service.seed(_make_detail("wf-1", status=WorkflowStatus.COMPLETED))
    with client.stream("GET", "/workflows/wf-1/logs") as response:
        body = b"".join(response.iter_bytes())
    frames = _parse_sse(body)
    assert len(frames) == 1
    payload = json.loads(_data(frames[0]))
    assert payload["kind"] == "stream_end"


def test_openapi_schema_exposes_streaming_routes(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    assert "/workflows/{workflow_id}/events" in paths
    assert "/workflows/{workflow_id}/logs" in paths


# ---------------------------------------------------------------------------
# Mutating routes — approval / clarification / retry (AOS-147)
# ---------------------------------------------------------------------------


def _make_run_result(workflow_id: str = "wf-1") -> WorkflowRunResult:
    return WorkflowRunResult(
        workflow_id=workflow_id,
        ticket_key="AOS-141",
        final_status=WorkflowStatus.PENDING_PR_APPROVAL,
        code_generation_summary={"status": "success"},
        pr_url="https://example.test/pr/1",
    )


def test_approve_plan_happy_path(client: TestClient, fake_service: FakeWorkflowService) -> None:
    fake_service.seed(_make_detail("wf-1"))
    fake_service.mutation_result = _make_run_result("wf-1")
    response = client.post("/workflows/wf-1/approve-plan")
    # Fire-and-forget: 202 Accepted with the post-run snapshot when the
    # sync dispatcher ran the mirror inline.
    assert response.status_code == 202
    body = response.json()
    assert body["workflow_id"] == "wf-1"
    assert body["final_status"] == "pending_pr_approval"
    assert fake_service.approve_plan_calls == ["wf-1"]


def test_approve_plan_404_when_missing(
    client: TestClient, fake_service: FakeWorkflowService
) -> None:
    response = client.post("/workflows/nope/approve-plan")
    assert response.status_code == 404
    assert fake_service.approve_plan_calls == []


def test_approve_plan_marks_failed_on_value_error(
    client: TestClient, fake_service: FakeWorkflowService
) -> None:
    # With fire-and-forget the route returns 202 immediately and any
    # exception raised by the graph drive is captured by the dispatcher's
    # on_failure handler, which marks the workflow FAILED.
    fake_service.seed(_make_detail("wf-1"))
    fake_service.mutation_exc = ValueError("not awaiting approval")
    response = client.post("/workflows/wf-1/approve-plan")
    assert response.status_code == 202
    assert len(fake_service.mark_failed_calls) == 1
    call = fake_service.mark_failed_calls[0]
    assert call["workflow_id"] == "wf-1"
    assert call["actor"] == "background-dispatcher"
    assert "not awaiting approval" in call["reason"]


def test_reject_plan_passes_reason(client: TestClient, fake_service: FakeWorkflowService) -> None:
    fake_service.seed(_make_detail("wf-1"))
    fake_service.mutation_result = _make_run_result("wf-1")
    response = client.post("/workflows/wf-1/reject-plan", json={"reason": "bad scope"})
    assert response.status_code == 202
    assert fake_service.reject_plan_calls == [{"workflow_id": "wf-1", "reason": "bad scope"}]


def test_reject_plan_without_body(client: TestClient, fake_service: FakeWorkflowService) -> None:
    fake_service.seed(_make_detail("wf-1"))
    fake_service.mutation_result = _make_run_result("wf-1")
    response = client.post("/workflows/wf-1/reject-plan")
    assert response.status_code == 202
    assert fake_service.reject_plan_calls == [{"workflow_id": "wf-1", "reason": None}]


def test_reject_plan_404_when_missing(client: TestClient) -> None:
    response = client.post("/workflows/nope/reject-plan", json={"reason": "x"})
    assert response.status_code == 404


def test_submit_clarification_happy_path(
    client: TestClient, fake_service: FakeWorkflowService
) -> None:
    fake_service.seed(_make_detail("wf-1"))
    fake_service.mutation_result = _make_run_result("wf-1")
    answers = [
        {"concern": "what about retries?", "answer": "use exponential backoff"},
        {"concern": "schema?", "answer": "v1 only"},
    ]
    response = client.post("/workflows/wf-1/clarification", json={"answers": answers})
    assert response.status_code == 202
    assert fake_service.submit_clarification_calls == [{"workflow_id": "wf-1", "answers": answers}]


def test_submit_clarification_rejects_empty_answer_text(client: TestClient) -> None:
    # ``answer`` may legitimately be empty per the protocol, but ``concern`` must
    # not be — verifies the schema's min_length constraint on concern.
    response = client.post(
        "/workflows/wf-1/clarification",
        json={"answers": [{"concern": "", "answer": "x"}]},
    )
    assert response.status_code == 422


def test_submit_clarification_rejects_missing_field(client: TestClient) -> None:
    response = client.post(
        "/workflows/wf-1/clarification",
        json={"answers": [{"concern": "a"}]},
    )
    assert response.status_code == 422


def test_submit_clarification_404_when_missing(client: TestClient) -> None:
    response = client.post(
        "/workflows/nope/clarification",
        json={"answers": [{"concern": "a", "answer": "b"}]},
    )
    assert response.status_code == 404


def test_retry_happy_path(client: TestClient, fake_service: FakeWorkflowService) -> None:
    fake_service.seed(_make_detail("wf-1", status=WorkflowStatus.FAILED))
    fake_service.mutation_result = _make_run_result("wf-1")
    response = client.post("/workflows/wf-1/retry")
    assert response.status_code == 202
    assert fake_service.retry_calls == ["wf-1"]


def test_retry_409_when_not_retryable(
    client: TestClient, fake_service: FakeWorkflowService
) -> None:
    # The route now pre-validates retryability synchronously (before
    # dispatching) using the workflow's current status, so the fake's
    # ``retry`` method never runs.
    fake_service.seed(_make_detail("wf-1", status=WorkflowStatus.COMPLETED))
    response = client.post("/workflows/wf-1/retry")
    assert response.status_code == 409
    assert "cannot be retried" in response.json()["detail"]
    assert fake_service.retry_calls == []


def test_retry_404_when_missing(client: TestClient) -> None:
    response = client.post("/workflows/nope/retry")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Mutating routes — PR review flow (AOS-147)
# ---------------------------------------------------------------------------


def test_approve_pr_happy_path(client: TestClient, fake_service: FakeWorkflowService) -> None:
    fake_service.seed(_make_detail("wf-1", status=WorkflowStatus.PENDING_PR_APPROVAL))
    fake_service.mutation_result = _make_run_result("wf-1")
    response = client.post("/workflows/wf-1/approve-pr")
    assert response.status_code == 202
    assert fake_service.approve_pr_calls == ["wf-1"]


def test_approve_pr_404_when_missing(client: TestClient) -> None:
    response = client.post("/workflows/nope/approve-pr")
    assert response.status_code == 404


def test_reject_pr_passes_reason(client: TestClient, fake_service: FakeWorkflowService) -> None:
    fake_service.seed(_make_detail("wf-1", status=WorkflowStatus.PENDING_PR_APPROVAL))
    fake_service.mutation_result = _make_run_result("wf-1")
    response = client.post("/workflows/wf-1/reject-pr", json={"reason": "tests failing"})
    assert response.status_code == 202
    assert fake_service.reject_pr_calls == [{"workflow_id": "wf-1", "reason": "tests failing"}]


def test_comment_pr_happy_path(client: TestClient, fake_service: FakeWorkflowService) -> None:
    fake_service.seed(_make_detail("wf-1", status=WorkflowStatus.PENDING_PR_APPROVAL))
    fake_service.mutation_result = _make_run_result("wf-1")
    response = client.post(
        "/workflows/wf-1/comment-pr", json={"comments": "please tighten the API"}
    )
    assert response.status_code == 202
    assert fake_service.comment_pr_calls == [
        {"workflow_id": "wf-1", "comments": "please tighten the API"}
    ]


def test_comment_pr_rejects_empty_comments(client: TestClient) -> None:
    response = client.post("/workflows/wf-1/comment-pr", json={"comments": ""})
    assert response.status_code == 422


def test_comment_pr_404_when_missing(client: TestClient) -> None:
    response = client.post("/workflows/nope/comment-pr", json={"comments": "hi"})
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Read routes — history / audit log (AOS-147)
# ---------------------------------------------------------------------------


def test_get_history_returns_entries(client: TestClient, fake_service: FakeWorkflowService) -> None:
    fake_service.seed(_make_detail("wf-1"))
    fake_service.history["wf-1"] = [
        WorkflowHistoryEntry(step=1, node="plan", outcome="ok", result_keys=["work_plan"]),
        WorkflowHistoryEntry(step=2, node="execute", outcome="error", error="boom"),
    ]
    response = client.get("/workflows/wf-1/history")
    assert response.status_code == 200
    body = response.json()
    assert [row["node"] for row in body] == ["plan", "execute"]
    assert body[0]["result_keys"] == ["work_plan"]
    assert body[1]["error"] == "boom"


def test_get_history_returns_empty_list_when_no_entries(
    client: TestClient, fake_service: FakeWorkflowService
) -> None:
    fake_service.seed(_make_detail("wf-1"))
    response = client.get("/workflows/wf-1/history")
    assert response.status_code == 200
    assert response.json() == []


def test_get_history_404_when_missing(client: TestClient) -> None:
    response = client.get("/workflows/nope/history")
    assert response.status_code == 404


def test_get_audit_log_returns_entries(
    client: TestClient, fake_service: FakeWorkflowService
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
    response = client.get("/workflows/wf-1/audit-log")
    assert response.status_code == 200
    body = response.json()
    assert body[0]["actor"] == "dispatcher"
    assert body[0]["action"] == "status_change"
    assert body[0]["details"] == {"to": "pending_approval"}


def test_get_audit_log_404_when_missing(client: TestClient) -> None:
    response = client.get("/workflows/nope/audit-log")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Admin routes — clear_db / mark_interrupted (AOS-147)
# ---------------------------------------------------------------------------


def test_admin_clear_db_503_when_token_unset(
    client: TestClient, fake_service: FakeWorkflowService
) -> None:
    # Default client has API_TOKEN_ENV deleted, so admin is disabled.
    response = client.post("/admin/clear-db")
    assert response.status_code == 503
    assert "disabled" in response.json()["detail"].lower()
    assert fake_service.clear_db_calls == []


def test_admin_clear_db_401_when_token_set_but_missing(
    monkeypatch, fake_service: FakeWorkflowService
) -> None:
    monkeypatch.setenv(API_TOKEN_ENV, "admin-token")
    app = create_app(service=fake_service)
    with TestClient(app) as authed_client:
        response = authed_client.post("/admin/clear-db")
        assert response.status_code == 401


def test_admin_clear_db_happy_path(monkeypatch, fake_service: FakeWorkflowService) -> None:
    monkeypatch.setenv(API_TOKEN_ENV, "admin-token")
    fake_service.clear_db_result = (7, 12)
    app = create_app(service=fake_service)
    with TestClient(app) as authed_client:
        response = authed_client.post(
            "/admin/clear-db", headers={"Authorization": "Bearer admin-token"}
        )
        assert response.status_code == 200
        assert response.json() == {"workflows": 7, "checkpoints": 12}
        assert len(fake_service.clear_db_calls) == 1


def test_admin_mark_interrupted_happy_path(monkeypatch, fake_service: FakeWorkflowService) -> None:
    monkeypatch.setenv(API_TOKEN_ENV, "admin-token")
    fake_service.seed(_make_detail("wf-1"))
    app = create_app(service=fake_service)
    with TestClient(app) as authed_client:
        response = authed_client.post(
            "/admin/workflows/wf-1/mark-interrupted",
            headers={"Authorization": "Bearer admin-token"},
            json={"failed_node": "execute", "actor": "ops-bot"},
        )
        assert response.status_code == 204
        assert fake_service.mark_interrupted_calls == [
            {"workflow_id": "wf-1", "failed_node": "execute", "actor": "ops-bot"}
        ]


def test_admin_mark_interrupted_without_body(
    monkeypatch, fake_service: FakeWorkflowService
) -> None:
    monkeypatch.setenv(API_TOKEN_ENV, "admin-token")
    fake_service.seed(_make_detail("wf-1"))
    app = create_app(service=fake_service)
    with TestClient(app) as authed_client:
        response = authed_client.post(
            "/admin/workflows/wf-1/mark-interrupted",
            headers={"Authorization": "Bearer admin-token"},
        )
        assert response.status_code == 204
        call = fake_service.mark_interrupted_calls[-1]
        assert call["failed_node"] is None
        assert call["actor"] == "api"


def test_admin_mark_interrupted_404_when_missing(
    monkeypatch, fake_service: FakeWorkflowService
) -> None:
    monkeypatch.setenv(API_TOKEN_ENV, "admin-token")
    app = create_app(service=fake_service)
    with TestClient(app) as authed_client:
        response = authed_client.post(
            "/admin/workflows/nope/mark-interrupted",
            headers={"Authorization": "Bearer admin-token"},
        )
        assert response.status_code == 404


def test_admin_mark_interrupted_503_when_admin_disabled(
    client: TestClient, fake_service: FakeWorkflowService
) -> None:
    fake_service.seed(_make_detail("wf-1"))
    response = client.post("/admin/workflows/wf-1/mark-interrupted")
    assert response.status_code == 503
    assert fake_service.mark_interrupted_calls == []


def test_admin_mark_interrupted_invokes_dispatcher_cancel(
    monkeypatch, fake_service: FakeWorkflowService
) -> None:
    monkeypatch.setenv(API_TOKEN_ENV, "admin-token")
    fake_service.seed(_make_detail("wf-1"))
    dispatcher = _SpyDispatcher()
    app = create_app(service=fake_service, background_dispatcher=dispatcher)
    with TestClient(app) as authed_client:
        response = authed_client.post(
            "/admin/workflows/wf-1/mark-interrupted",
            headers={"Authorization": "Bearer admin-token"},
        )
    assert response.status_code == 204
    assert dispatcher.cancel_calls == ["wf-1"]


# ---------------------------------------------------------------------------
# Admin escape hatch — ORCHESTRATOR_ALLOW_UNAUTHENTICATED_ADMIN (AOS-197)
# ---------------------------------------------------------------------------


def test_admin_clear_db_allowed_anon_when_escape_hatch_enabled(
    monkeypatch, fake_service: FakeWorkflowService
) -> None:
    """Escape hatch: no API token + flag truthy → admin accepts anon requests."""
    monkeypatch.delenv(API_TOKEN_ENV, raising=False)
    monkeypatch.setenv(ADMIN_ALLOW_UNAUTHENTICATED_ENV, "1")
    fake_service.clear_db_result = (3, 5)
    app = create_app(service=fake_service)
    with TestClient(app) as anon_client:
        response = anon_client.post("/admin/clear-db")
        assert response.status_code == 200
        assert response.json() == {"workflows": 3, "checkpoints": 5}
        assert len(fake_service.clear_db_calls) == 1


def test_admin_mark_interrupted_allowed_anon_when_escape_hatch_enabled(
    monkeypatch, fake_service: FakeWorkflowService
) -> None:
    monkeypatch.delenv(API_TOKEN_ENV, raising=False)
    monkeypatch.setenv(ADMIN_ALLOW_UNAUTHENTICATED_ENV, "true")
    fake_service.seed(_make_detail("wf-1"))
    app = create_app(service=fake_service)
    with TestClient(app) as anon_client:
        response = anon_client.post("/admin/workflows/wf-1/mark-interrupted")
        assert response.status_code == 204
        assert fake_service.mark_interrupted_calls == [
            {"workflow_id": "wf-1", "failed_node": None, "actor": "api"}
        ]


@pytest.mark.parametrize("falsy", ["", "0", "false", "no", "off", " "])
def test_admin_still_503_when_escape_hatch_falsy(
    monkeypatch, fake_service: FakeWorkflowService, falsy: str
) -> None:
    """Falsy escape-hatch values must not unlock admin."""
    monkeypatch.delenv(API_TOKEN_ENV, raising=False)
    monkeypatch.setenv(ADMIN_ALLOW_UNAUTHENTICATED_ENV, falsy)
    app = create_app(service=fake_service)
    with TestClient(app) as anon_client:
        response = anon_client.post("/admin/clear-db")
        assert response.status_code == 503
        assert fake_service.clear_db_calls == []


def test_admin_escape_hatch_ignored_when_api_token_set(
    monkeypatch, fake_service: FakeWorkflowService
) -> None:
    """When API_TOKEN is set, the escape hatch is ignored — bearer still required."""
    monkeypatch.setenv(API_TOKEN_ENV, "admin-token")
    monkeypatch.setenv(ADMIN_ALLOW_UNAUTHENTICATED_ENV, "1")
    app = create_app(service=fake_service)
    with TestClient(app) as gated_client:
        # Anonymous request rejected with 401 (not 200 via escape hatch).
        response = gated_client.post("/admin/clear-db")
        assert response.status_code == 401
        # Bearer token still works.
        response = gated_client.post(
            "/admin/clear-db", headers={"Authorization": "Bearer admin-token"}
        )
        assert response.status_code == 200


def test_openapi_schema_exposes_new_routes(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    assert "/workflows/{workflow_id}/approve-plan" in paths
    assert "/workflows/{workflow_id}/reject-plan" in paths
    assert "/workflows/{workflow_id}/clarification" in paths
    assert "/workflows/{workflow_id}/retry" in paths
    assert "/workflows/{workflow_id}/approve-pr" in paths
    assert "/workflows/{workflow_id}/reject-pr" in paths
    assert "/workflows/{workflow_id}/comment-pr" in paths
    assert "/workflows/{workflow_id}/history" in paths
    assert "/workflows/{workflow_id}/audit-log" in paths
    assert "/admin/clear-db" in paths
    assert "/admin/workflows/{workflow_id}/mark-interrupted" in paths


def test_create_app_configures_root_logger_for_workflow_file_handler(
    fake_service: FakeWorkflowService,
) -> None:
    """Regression: without ``setup_logging()`` the server's root logger stays
    at WARNING (Python default), which drops the ``subprocess.goose - INFO``
    records the per-workflow ``WorkflowFileHandler`` was designed to capture
    — leaving ``workflow.log`` empty and the TUI's live tail pane blank in
    remote mode.
    """
    import logging

    root = logging.getLogger()
    original_level = root.level
    try:
        # Simulate a pristine interpreter where setup_logging() hasn't run.
        root.setLevel(logging.WARNING)
        create_app(service=fake_service)
        assert root.level <= logging.INFO, (
            f"Root logger level {root.level} would drop INFO records — "
            "workflow.log will be empty in the container."
        )
    finally:
        root.setLevel(original_level)
