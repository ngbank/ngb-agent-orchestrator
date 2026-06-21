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
        # Streaming hooks: tests append WorkflowEvent / WorkflowLogChunk values
        # here at any time; the SSE generators pick them up on the next poll.
        # Use lists keyed by workflow_id so multiple workflows can be streamed
        # in the same test without crosstalk.
        self.events: Dict[str, List[WorkflowEvent]] = {}
        self.log_bytes: Dict[str, Dict[str, bytes]] = {}
        self.read_logs_calls: List[Dict[str, Any]] = []
        self.stream_events_calls: List[Dict[str, Any]] = []

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
            execution_summary=existing.execution_summary,
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
        return []

    def get_audit_log(self, workflow_id: str) -> List[WorkflowAuditEntry]:
        return []

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


def test_logs_stream_replays_all_stages_and_closes_on_terminal(
    client: TestClient, fake_service: FakeWorkflowService
) -> None:
    fake_service.seed(_make_detail("wf-1", status=WorkflowStatus.COMPLETED))
    fake_service.append_log("wf-1", "plan", "plan-line-1\nplan-line-2\n")
    fake_service.append_log("wf-1", "execute", "exec-line-1\n")

    with client.stream("GET", "/workflows/wf-1/logs") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = b"".join(response.iter_bytes())

    frames = _parse_sse(body)
    # Two log chunks + stream_end
    assert len(frames) >= 3
    log_frames = [f for f in frames if f["id"] is not None]
    payloads = [json.loads(_data(f)) for f in log_frames]
    stages = {p["stage"] for p in payloads}
    assert stages == {"plan", "execute"}
    plan_payload = next(p for p in payloads if p["stage"] == "plan")
    assert plan_payload["offset"] == 0
    assert plan_payload["content"] == "plan-line-1\nplan-line-2\n"
    assert plan_payload["end_offset"] == len(plan_payload["content"].encode("utf-8"))
    # Final frame: stream_end, no id
    final = frames[-1]
    assert final["id"] is None
    assert json.loads(_data(final))["kind"] == "stream_end"
    assert json.loads(_data(final))["final_status"] == "completed"


def test_logs_stream_respects_after_offset(
    client: TestClient, fake_service: FakeWorkflowService
) -> None:
    fake_service.seed(_make_detail("wf-1", status=WorkflowStatus.COMPLETED))
    fake_service.append_log("wf-1", "plan", "hello world")

    with client.stream(
        "GET",
        "/workflows/wf-1/logs?stage=plan&after_offset=6",
    ) as response:
        body = b"".join(response.iter_bytes())

    frames = _parse_sse(body)
    log_frames = [f for f in frames if f["id"] is not None]
    assert len(log_frames) == 1
    payload = json.loads(_data(log_frames[0]))
    assert payload["stage"] == "plan"
    assert payload["offset"] == 6
    assert payload["content"] == "world"
    assert payload["end_offset"] == 11


def test_logs_stream_respects_last_event_id_header(
    client: TestClient, fake_service: FakeWorkflowService
) -> None:
    fake_service.seed(_make_detail("wf-1", status=WorkflowStatus.COMPLETED))
    fake_service.append_log("wf-1", "plan", "abcdef")

    with client.stream(
        "GET",
        "/workflows/wf-1/logs?stage=plan",
        headers={"Last-Event-ID": "3"},
    ) as response:
        body = b"".join(response.iter_bytes())

    log_frames = [f for f in _parse_sse(body) if f["id"] is not None]
    payload = json.loads(_data(log_frames[0]))
    assert payload["offset"] == 3
    assert payload["content"] == "def"


def test_logs_stream_stage_filter(client: TestClient, fake_service: FakeWorkflowService) -> None:
    fake_service.seed(_make_detail("wf-1", status=WorkflowStatus.COMPLETED))
    fake_service.append_log("wf-1", "plan", "PLAN")
    fake_service.append_log("wf-1", "execute", "EXEC")

    with client.stream("GET", "/workflows/wf-1/logs?stage=execute") as response:
        body = b"".join(response.iter_bytes())

    log_frames = [f for f in _parse_sse(body) if f["id"] is not None]
    assert len(log_frames) == 1
    payload = json.loads(_data(log_frames[0]))
    assert payload["stage"] == "execute"
    assert payload["content"] == "EXEC"


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
