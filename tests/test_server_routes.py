"""HTTP route tests for :mod:`orchestrator.server`.

Use a :class:`FakeWorkflowService` so the tests exercise the full FastAPI
stack — auth, routing, schema validation, OpenAPI — without spinning up
LangGraph or SQLite.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

import pytest
from fastapi.testclient import TestClient

from orchestrator.server.app import create_app
from orchestrator.server.auth import API_TOKEN_ENV
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
        self.cancel_calls: List[Dict[str, Any]] = []
        self.list_calls: List[Dict[str, Any]] = []
        self.workflows: Dict[str, WorkflowDetail] = {}
        self.start_result: Optional[WorkflowRunResult] = None
        self.start_exc: Optional[BaseException] = None

    # ------------------------- helpers --------------------------------
    def seed(self, detail: WorkflowDetail) -> None:
        self.workflows[detail.id] = detail

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
        return []

    def get_audit_log(self, workflow_id: str) -> List[WorkflowAuditEntry]:
        return []

    def read_logs(
        self,
        workflow_id: str,
        stage: Optional[str] = None,
    ) -> List[WorkflowLogChunk]:
        return []

    def stream_events(
        self,
        workflow_id: str,
        after_seq: int = 0,
    ) -> Iterable[WorkflowEvent]:
        return iter(())

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

    def mark_interrupted(self, *args, **kwargs) -> None:  # pragma: no cover - unused
        return None

    def clear_db(self) -> tuple[int, int]:  # pragma: no cover - unused
        return (0, 0)

    # ------------------------- graph ops ------------------------------
    def start(self, request: WorkflowStartRequest) -> WorkflowRunResult:
        self.start_calls.append(request)
        if self.start_exc is not None:
            raise self.start_exc
        if self.start_result is not None:
            return self.start_result
        return WorkflowRunResult(
            workflow_id=request.workflow_id or "wf-generated",
            ticket_key=request.ticket_key,
            final_status=WorkflowStatus.PENDING_APPROVAL,
            interrupted=True,
        )

    def approve_plan(self, workflow_id: str) -> WorkflowRunResult:  # pragma: no cover - unused
        raise NotImplementedError

    def reject_plan(self, *args, **kwargs) -> WorkflowRunResult:  # pragma: no cover - unused
        raise NotImplementedError

    def submit_clarification(self, *args, **kwargs) -> WorkflowRunResult:  # pragma: no cover
        raise NotImplementedError

    def retry(self, workflow_id: str) -> WorkflowRunResult:  # pragma: no cover - unused
        raise NotImplementedError

    def approve_pr(self, workflow_id: str) -> WorkflowRunResult:  # pragma: no cover - unused
        raise NotImplementedError

    def comment_pr(self, *args, **kwargs) -> WorkflowRunResult:  # pragma: no cover - unused
        raise NotImplementedError

    def reject_pr(self, *args, **kwargs) -> WorkflowRunResult:  # pragma: no cover - unused
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_service() -> FakeWorkflowService:
    return FakeWorkflowService()


@pytest.fixture
def client(monkeypatch, fake_service: FakeWorkflowService) -> TestClient:
    # Default: auth disabled.  Tests that exercise auth opt in via monkeypatch.
    monkeypatch.delenv(API_TOKEN_ENV, raising=False)
    app = create_app(service=fake_service)
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
        execution_summary=None,
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
    assert response.status_code == 201
    body = response.json()
    assert body["workflow_id"] == "wf-1"
    assert body["ticket_key"] == "AOS-141"
    assert body["final_status"] == "pending_approval"
    assert body["interrupted"] is True
    assert len(fake_service.start_calls) == 1
    assert fake_service.start_calls[0].ticket_key == "AOS-141"
    assert fake_service.start_calls[0].dry_run is False


def test_start_workflow_passes_optional_fields(
    client: TestClient, fake_service: FakeWorkflowService
) -> None:
    response = client.post(
        "/workflows",
        json={"ticket_key": "AOS-141", "dry_run": True, "workflow_id": "custom-id"},
    )
    assert response.status_code == 201
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
