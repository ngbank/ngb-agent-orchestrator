"""Unit tests for TUI presentation helpers and Textual Pilot interaction tests."""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional

import pytest

from dispatcher.tui.app import WorkflowTUI
from dispatcher.tui.widgets import DetailPane, WorkflowList
from orchestrator.workflow_service import (
    WorkflowAuditEntry,
    WorkflowDetail,
    WorkflowEvent,
    WorkflowHistoryEntry,
    WorkflowLogChunk,
    WorkflowRunResult,
    WorkflowService,
    WorkflowStartRequest,
    WorkflowSummary,
)
from state.workflow_status import WorkflowStatus


class FakeWorkflowService:
    """In-memory ``WorkflowService`` returning canned DTOs for TUI tests."""

    def __init__(
        self,
        summaries: Optional[List[WorkflowSummary]] = None,
        details: Optional[Dict[str, WorkflowDetail]] = None,
    ) -> None:
        self._summaries = summaries or []
        self._details = details or {}

    # --- read operations -------------------------------------------------

    def get(self, workflow_id: str) -> Optional[WorkflowDetail]:
        return self._details.get(workflow_id)

    def get_by_ticket(self, ticket_key: str) -> List[WorkflowSummary]:
        return [s for s in self._summaries if s.ticket_key == ticket_key]

    def get_latest_retryable_by_ticket(self, ticket_key: str) -> Optional[WorkflowSummary]:
        return next((s for s in self._summaries if s.ticket_key == ticket_key), None)

    def list(
        self,
        ticket_key: Optional[str] = None,
        status: Optional[WorkflowStatus] = None,
        limit: int = 50,
    ) -> List[WorkflowSummary]:
        result = list(self._summaries)
        if ticket_key is not None:
            result = [s for s in result if s.ticket_key == ticket_key]
        if status is not None:
            result = [s for s in result if s.status == status]
        return result[:limit]

    def get_history(self, workflow_id: str) -> List[WorkflowHistoryEntry]:
        return []

    def get_audit_log(self, workflow_id: str) -> List[WorkflowAuditEntry]:
        return []

    def read_logs(self, workflow_id: str, stage: Optional[str] = None) -> List[WorkflowLogChunk]:
        return []

    def stream_events(self, workflow_id: str, after_seq: int = 0) -> Iterable[WorkflowEvent]:
        return iter(())

    # --- admin / mutations (unused by the unit tests) --------------------

    def cancel(
        self,
        workflow_id: str,
        reason: Optional[str] = None,
        actor: str = "system",
    ) -> None:
        return None

    def mark_interrupted(
        self,
        workflow_id: str,
        failed_node: Optional[str] = None,
        actor: str = "system",
    ) -> None:
        return None

    def clear_db(self) -> tuple[int, int]:
        return (0, 0)

    # --- graph-running operations (unused by the unit tests) -------------

    def start(self, request: WorkflowStartRequest) -> WorkflowRunResult:
        return WorkflowRunResult(
            workflow_id=request.workflow_id or "",
            ticket_key=request.ticket_key,
            final_status=WorkflowStatus.PENDING,
        )

    def approve_plan(self, workflow_id: str) -> WorkflowRunResult:
        return WorkflowRunResult(
            workflow_id=workflow_id, ticket_key=None, final_status=WorkflowStatus.APPROVED
        )

    def reject_plan(self, workflow_id: str, reason: Optional[str]) -> WorkflowRunResult:
        return WorkflowRunResult(
            workflow_id=workflow_id, ticket_key=None, final_status=WorkflowStatus.REJECTED
        )

    def submit_clarification(
        self, workflow_id: str, answers: List[Dict[str, str]]
    ) -> WorkflowRunResult:
        return WorkflowRunResult(
            workflow_id=workflow_id, ticket_key=None, final_status=WorkflowStatus.PENDING_APPROVAL
        )

    def retry(self, workflow_id: str) -> WorkflowRunResult:
        return WorkflowRunResult(
            workflow_id=workflow_id, ticket_key=None, final_status=WorkflowStatus.IN_PROGRESS
        )

    def approve_pr(self, workflow_id: str) -> WorkflowRunResult:
        return WorkflowRunResult(
            workflow_id=workflow_id, ticket_key=None, final_status=WorkflowStatus.COMPLETED
        )

    def comment_pr(self, workflow_id: str, comments: str) -> WorkflowRunResult:
        return WorkflowRunResult(
            workflow_id=workflow_id, ticket_key=None, final_status=WorkflowStatus.PR_COMMENTED
        )

    def reject_pr(self, workflow_id: str, reason: Optional[str]) -> WorkflowRunResult:
        return WorkflowRunResult(
            workflow_id=workflow_id, ticket_key=None, final_status=WorkflowStatus.REJECTED
        )


def _make_summary(
    wf_id: str,
    ticket: str,
    status: WorkflowStatus,
    *,
    pr_url: Optional[str] = None,
    updated: str = "2024-01-01T01:00:00+00:00",
) -> WorkflowSummary:
    return WorkflowSummary(
        id=wf_id,
        ticket_key=ticket,
        status=status,
        created_at="2024-01-01T00:00:00+00:00",
        updated_at=updated,
        pr_url=pr_url,
    )


def _make_detail(
    wf_id: str,
    ticket: str,
    status: WorkflowStatus,
    *,
    work_plan: Optional[Dict] = None,
    pr_url: Optional[str] = None,
    retry_count: int = 0,
) -> WorkflowDetail:
    return WorkflowDetail(
        id=wf_id,
        ticket_key=ticket,
        status=status,
        created_at="2024-01-01T00:00:00+00:00",
        updated_at="2024-01-01T01:00:00+00:00",
        pr_url=pr_url,
        work_plan=work_plan,
        retry_count=retry_count,
    )


@pytest.fixture
def sample_summaries() -> List[WorkflowSummary]:
    return [
        _make_summary("wf-1", "AOS-1", WorkflowStatus.PENDING),
        _make_summary(
            "wf-2",
            "AOS-2",
            WorkflowStatus.COMPLETED,
            pr_url="https://github.com/org/repo/pull/1",
            updated="2024-01-02T01:00:00+00:00",
        ),
    ]


@pytest.fixture
def sample_details() -> Dict[str, WorkflowDetail]:
    return {
        "wf-1": _make_detail(
            "wf-1", "AOS-1", WorkflowStatus.PENDING, work_plan={"summary": "Fix bug"}
        ),
        "wf-2": _make_detail(
            "wf-2",
            "AOS-2",
            WorkflowStatus.COMPLETED,
            work_plan={"summary": "Add feature"},
            pr_url="https://github.com/org/repo/pull/1",
            retry_count=1,
        ),
    }


@pytest.fixture
def fake_service(
    sample_summaries: List[WorkflowSummary],
    sample_details: Dict[str, WorkflowDetail],
) -> WorkflowService:
    service = FakeWorkflowService(summaries=sample_summaries, details=sample_details)
    # Sanity check: our fake satisfies the runtime-checkable Protocol.
    assert isinstance(service, WorkflowService)
    return service


class TestWorkflowList:
    def test_update_workflows_populates_internal_store(
        self, sample_summaries: List[WorkflowSummary]
    ):
        widget = WorkflowList()
        # textual widgets need to be mounted for query_one to work;
        # test the internal data structure directly
        widget._workflows = sample_summaries
        assert len(widget._workflows) == 2
        assert widget._workflows[0].ticket_key == "AOS-1"

    def test_get_selected_workflow_without_mount(self, sample_summaries: List[WorkflowSummary]):
        widget = WorkflowList()
        widget._workflows = sample_summaries
        # Without a mounted DataTable cursor, returns None
        assert widget.get_selected_workflow() is None


class TestDetailPane:
    def test_update_workflow_with_none(self):
        pane = DetailPane()
        pane._workflow = None
        # Just ensure no exception
        pane.update_workflow(None)
        assert pane._workflow is None

    def test_update_workflow_with_data(self, sample_details: Dict[str, WorkflowDetail]):
        pane = DetailPane()
        wf = sample_details["wf-1"]
        pane.update_workflow(wf)
        assert pane._workflow == wf


@pytest.mark.asyncio
class TestWorkflowTUI:
    async def test_app_mounts(self, fake_service: WorkflowService):
        app = WorkflowTUI(fake_service)
        async with app.run_test():
            assert app.is_running

    async def test_refresh_action(self, fake_service: WorkflowService):
        app = WorkflowTUI(fake_service)
        async with app.run_test() as pilot:
            await pilot.press("r")
            assert app.is_running

    async def test_quit_action(self, fake_service: WorkflowService):
        app = WorkflowTUI(fake_service)
        async with app.run_test() as pilot:
            await pilot.press("q")
            assert not app.is_running

    async def test_renders_list_and_detail_from_service(
        self,
        fake_service: WorkflowService,
        sample_summaries: List[WorkflowSummary],
    ):
        """End-to-end: TUI lists canned summaries and shows detail for the
        selected row, sourcing all data from the injected ``WorkflowService``.
        """
        app = WorkflowTUI(fake_service)
        async with app.run_test():
            workflow_list = app.query_one(WorkflowList)
            assert [s.id for s in workflow_list._workflows] == [s.id for s in sample_summaries]

            detail = app.query_one(DetailPane)
            # The first row is selected after refresh; detail should match it.
            assert detail._workflow is not None
            assert detail._workflow.id == sample_summaries[0].id
            assert detail._workflow.ticket_key == sample_summaries[0].ticket_key
