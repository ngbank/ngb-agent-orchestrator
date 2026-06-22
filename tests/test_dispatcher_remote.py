"""Integration tests for the dispatcher's remote (HTTP) transport mode.

These tests verify two things:

1. ``build_workflow_service_from_env`` correctly routes between the local and
   remote ``WorkflowService`` implementations based on environment variables.
2. The dispatcher CLI works end-to-end when wired to an
   ``HttpWorkflowService`` that talks to an in-process FastAPI app via
   :class:`fastapi.testclient.TestClient`.

We deliberately keep the surface narrow: any in-depth HTTP behaviour is
already covered by :mod:`tests.test_workflow_service_http`.  These tests are
the "the dispatcher sees the remote service" smoke layer.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

import pytest
from click.testing import CliRunner

from dispatcher.run import run
from orchestrator.server.app import create_app
from orchestrator.server.auth import API_TOKEN_ENV
from orchestrator.workflow_service import (
    HttpWorkflowService,
    build_http_workflow_service,
    build_workflow_service_from_env,
)
from orchestrator.workflow_service.dtos import (
    WorkflowAuditEntry,
    WorkflowDetail,
)
from orchestrator.workflow_service.dtos import WorkflowEvent as WorkflowEventDTO
from orchestrator.workflow_service.dtos import (
    WorkflowHistoryEntry,
    WorkflowLogChunk,
    WorkflowRunResult,
    WorkflowStartRequest,
    WorkflowSummary,
)
from orchestrator.workflow_service.factory import (
    MODE_ENV,
    MODE_LOCAL,
    MODE_REMOTE,
    TOKEN_ENV,
    URL_ENV,
)
from orchestrator.workflow_service.local import LocalWorkflowService
from state.workflow_status import WorkflowStatus

# ---------------------------------------------------------------------------
# Minimal in-memory fake (mirror of the one in test_workflow_service_http.py
# but pared down to what the dispatcher exercises here).
# ---------------------------------------------------------------------------


class _FakeService:
    def __init__(self) -> None:
        self.workflows: Dict[str, WorkflowDetail] = {}
        self.list_calls: List[Dict[str, Any]] = []

    def seed(self, detail: WorkflowDetail) -> None:
        self.workflows[detail.id] = detail

    # Reads -----------------------------------------------------------
    def get(self, workflow_id: str) -> Optional[WorkflowDetail]:
        return self.workflows.get(workflow_id)

    def get_by_ticket(self, ticket_key: str) -> List[WorkflowSummary]:
        return [self._summary(d) for d in self.workflows.values() if d.ticket_key == ticket_key]

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

    def get_history(self, workflow_id: str) -> List[WorkflowHistoryEntry]:  # pragma: no cover
        return []

    def get_audit_log(self, workflow_id: str) -> List[WorkflowAuditEntry]:  # pragma: no cover
        return []

    def read_logs(
        self,
        workflow_id: str,
        stage: Optional[str] = None,
        after_offset: int = 0,
    ) -> List[WorkflowLogChunk]:
        return []

    def stream_events(
        self,
        workflow_id: str,
        after_seq: int = 0,
    ) -> Iterable[WorkflowEventDTO]:
        return iter([])

    # Mutations / graph ops -----------------------------------------
    def cancel(self, *a, **k) -> None:  # pragma: no cover
        return None

    def mark_interrupted(self, *a, **k) -> None:  # pragma: no cover
        return None

    def clear_db(self) -> tuple[int, int]:  # pragma: no cover
        return (0, 0)

    def start(self, request: WorkflowStartRequest) -> WorkflowRunResult:  # pragma: no cover
        return WorkflowRunResult(
            workflow_id=request.workflow_id or "wf-x",
            ticket_key=request.ticket_key,
            final_status=WorkflowStatus.PENDING_APPROVAL,
            interrupted=True,
        )

    def approve_plan(self, *a, **k):  # pragma: no cover
        raise NotImplementedError

    def reject_plan(self, *a, **k):  # pragma: no cover
        raise NotImplementedError

    def submit_clarification(self, *a, **k):  # pragma: no cover
        raise NotImplementedError

    def retry(self, *a, **k):  # pragma: no cover
        raise NotImplementedError

    def approve_pr(self, *a, **k):  # pragma: no cover
        raise NotImplementedError

    def comment_pr(self, *a, **k):  # pragma: no cover
        raise NotImplementedError

    def reject_pr(self, *a, **k):  # pragma: no cover
        raise NotImplementedError

    # Helpers --------------------------------------------------------
    def _summary(self, d: WorkflowDetail) -> WorkflowSummary:
        return WorkflowSummary(
            id=d.id,
            ticket_key=d.ticket_key,
            status=d.status,
            created_at=d.created_at,
            updated_at=d.updated_at,
            pr_url=d.pr_url,
        )


def _make_detail(
    workflow_id: str,
    *,
    ticket_key: str = "AOS-143",
    status: WorkflowStatus = WorkflowStatus.COMPLETED,
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


# ---------------------------------------------------------------------------
# Factory selection
# ---------------------------------------------------------------------------


class TestFactoryEnvSelection:
    def test_unset_mode_defaults_to_local(self, monkeypatch) -> None:
        monkeypatch.delenv(MODE_ENV, raising=False)
        monkeypatch.delenv(URL_ENV, raising=False)
        monkeypatch.delenv(TOKEN_ENV, raising=False)
        svc = build_workflow_service_from_env()
        assert isinstance(svc, LocalWorkflowService)

    def test_explicit_local_mode_returns_local(self, monkeypatch) -> None:
        monkeypatch.setenv(MODE_ENV, MODE_LOCAL)
        monkeypatch.delenv(URL_ENV, raising=False)
        svc = build_workflow_service_from_env()
        assert isinstance(svc, LocalWorkflowService)

    def test_mode_case_insensitive(self, monkeypatch) -> None:
        monkeypatch.setenv(MODE_ENV, "LOCAL")
        svc = build_workflow_service_from_env()
        assert isinstance(svc, LocalWorkflowService)

    def test_remote_mode_returns_http_service(self, monkeypatch) -> None:
        monkeypatch.setenv(MODE_ENV, MODE_REMOTE)
        monkeypatch.setenv(URL_ENV, "http://orchestrator.test:8080")
        monkeypatch.delenv(TOKEN_ENV, raising=False)
        svc = build_workflow_service_from_env()
        try:
            assert isinstance(svc, HttpWorkflowService)
        finally:
            svc.close()  # type: ignore[attr-defined]

    def test_remote_mode_without_url_raises(self, monkeypatch) -> None:
        monkeypatch.setenv(MODE_ENV, MODE_REMOTE)
        monkeypatch.delenv(URL_ENV, raising=False)
        with pytest.raises(ValueError, match=URL_ENV):
            build_workflow_service_from_env()

    def test_unknown_mode_raises(self, monkeypatch) -> None:
        monkeypatch.setenv(MODE_ENV, "weird")
        with pytest.raises(ValueError, match="invalid"):
            build_workflow_service_from_env()


# ---------------------------------------------------------------------------
# Dispatcher CLI end-to-end via HttpWorkflowService
# ---------------------------------------------------------------------------


def _build_http_service_with_fake(
    fake: _FakeService, *, token: Optional[str] = None
) -> HttpWorkflowService:
    from fastapi.testclient import TestClient

    app = create_app(service=fake)
    client = TestClient(app, base_url="http://testserver")
    return build_http_workflow_service("http://testserver", token=token, client=client)


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def fake_service(monkeypatch) -> _FakeService:
    monkeypatch.delenv(API_TOKEN_ENV, raising=False)
    return _FakeService()


@pytest.fixture
def http_service(fake_service: _FakeService):
    svc = _build_http_service_with_fake(fake_service)
    try:
        yield svc
    finally:
        svc.close()


class TestDispatcherCLIOverHttp:
    def test_list_renders_summaries_from_remote(
        self,
        cli_runner: CliRunner,
        fake_service: _FakeService,
        http_service: HttpWorkflowService,
    ) -> None:
        fake_service.seed(_make_detail("wf-1", ticket_key="AOS-143"))
        fake_service.seed(_make_detail("wf-2", ticket_key="AOS-99"))

        result = cli_runner.invoke(run, ["--list"], obj=http_service)
        assert result.exit_code == 0, result.output
        # Both workflows appear in the listing.
        assert "AOS-143" in result.output
        assert "AOS-99" in result.output
        # The CLI hit the remote `list` endpoint.
        assert fake_service.list_calls and fake_service.list_calls[0]["limit"] == 50

    def test_list_with_ticket_filter_forwards_to_remote(
        self,
        cli_runner: CliRunner,
        fake_service: _FakeService,
        http_service: HttpWorkflowService,
    ) -> None:
        fake_service.seed(_make_detail("wf-1", ticket_key="AOS-143"))
        fake_service.seed(_make_detail("wf-2", ticket_key="AOS-99"))

        result = cli_runner.invoke(run, ["--list", "--ticket", "AOS-143"], obj=http_service)
        assert result.exit_code == 0, result.output
        assert "AOS-143" in result.output
        assert "AOS-99" not in result.output
        # The ticket filter was propagated over HTTP.
        assert fake_service.list_calls[-1]["ticket_key"] == "AOS-143"

    def test_list_with_no_workflows_prints_friendly_message(
        self,
        cli_runner: CliRunner,
        fake_service: _FakeService,
        http_service: HttpWorkflowService,
    ) -> None:
        result = cli_runner.invoke(run, ["--list"], obj=http_service)
        assert result.exit_code == 0, result.output
        assert "No workflows found" in result.output


class TestDispatcherFactoryWiring:
    """The CLI must surface ``ValueError`` from the factory as a clean exit."""

    def test_remote_mode_without_url_exits_nonzero(
        self, cli_runner: CliRunner, monkeypatch
    ) -> None:
        monkeypatch.setenv(MODE_ENV, MODE_REMOTE)
        monkeypatch.delenv(URL_ENV, raising=False)
        # ``obj`` left as None so the CLI invokes the factory.
        result = cli_invoke_with_no_obj(cli_runner, ["--list"])
        assert result.exit_code == 2
        assert URL_ENV in result.output

    def test_unknown_mode_exits_nonzero(self, cli_runner: CliRunner, monkeypatch) -> None:
        monkeypatch.setenv(MODE_ENV, "weird")
        result = cli_invoke_with_no_obj(cli_runner, ["--list"])
        assert result.exit_code == 2
        assert "invalid" in result.output.lower()


def cli_invoke_with_no_obj(runner: CliRunner, args: List[str]):
    """``runner.invoke`` defaults to ``obj=None`` so the dispatcher exercises
    the env factory path.  Keeping the helper explicit so the intent is clear.
    """
    return runner.invoke(run, args)
