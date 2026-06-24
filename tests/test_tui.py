"""Unit tests for TUI presentation helpers and Textual Pilot interaction tests."""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from typing import Dict, Iterable, List, Optional

import pytest

from dispatcher.tui.app import WorkflowTUI
from dispatcher.tui.widgets import DetailPane, LogTail, WorkflowList
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
        log_scripts: Optional[Dict[str, Dict[str, List[str]]]] = None,
    ) -> None:
        self._summaries = summaries or []
        self._details = details or {}
        # ``log_scripts[workflow_id][stage]`` is a list of byte-string
        # snippets the tail loop will receive across successive
        # ``read_logs`` calls.  Each call pops one snippet per stage so
        # tests can simulate a streamed sequence.
        self._log_scripts: Dict[str, Dict[str, List[bytes]]] = {
            wf_id: {st: [s.encode("utf-8") for s in chunks] for st, chunks in stages.items()}
            for wf_id, stages in (log_scripts or {}).items()
        }
        self._log_cursor: Dict[str, Dict[str, int]] = {}

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

    def read_logs(
        self,
        workflow_id: str,
        stage: Optional[str] = None,
        after_offset: int = 0,
    ) -> List[WorkflowLogChunk]:
        scripts = self._log_scripts.get(workflow_id)
        if not scripts:
            return []
        stages = [stage] if stage else list(scripts.keys())
        chunks: List[WorkflowLogChunk] = []
        cursor_map = self._log_cursor.setdefault(workflow_id, {})
        for st in stages:
            queue = scripts.get(st)
            if not queue:
                continue
            idx = cursor_map.get(st, 0)
            if idx >= len(queue):
                continue
            payload = queue[idx]
            cursor_map[st] = idx + 1
            chunks.append(
                WorkflowLogChunk(
                    workflow_id=workflow_id,
                    stage=st,
                    path=f"<fake:{st}>",
                    content=payload.decode("utf-8"),
                    offset=after_offset,
                )
            )
        return chunks

    def set_status(self, workflow_id: str, status: WorkflowStatus) -> None:
        """Test helper: update both summary and detail status in-place."""
        self._summaries = [
            replace(s, status=status) if s.id == workflow_id else s for s in self._summaries
        ]
        detail = self._details.get(workflow_id)
        if detail is not None:
            self._details[workflow_id] = replace(detail, status=status)

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

    def mark_failed(
        self,
        workflow_id: str,
        reason: str,
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


# ---------------------------------------------------------------------------
# Stage C — live log tailing for in-progress workflows
# ---------------------------------------------------------------------------


def _running_setup(
    plan_chunks: Optional[List[str]] = None,
    execute_chunks: Optional[List[str]] = None,
) -> tuple[FakeWorkflowService, str]:
    """Build a FakeWorkflowService with a single IN_PROGRESS workflow and a
    scripted log stream for tailing tests."""
    wf_id = "wf-running"
    summary = _make_summary(wf_id, "AOS-145", WorkflowStatus.IN_PROGRESS)
    detail = _make_detail(wf_id, "AOS-145", WorkflowStatus.IN_PROGRESS)
    scripts: Dict[str, List[str]] = {}
    if plan_chunks:
        scripts["plan"] = plan_chunks
    if execute_chunks:
        scripts["execute"] = execute_chunks
    service = FakeWorkflowService(
        summaries=[summary],
        details={wf_id: detail},
        log_scripts={wf_id: scripts} if scripts else None,
    )
    return service, wf_id


@pytest.mark.asyncio
class TestLiveLogTailing:
    async def test_tail_appears_for_in_progress_workflow(self):
        service, wf_id = _running_setup(
            plan_chunks=["plan line 1\n", "plan line 2\n"],
            execute_chunks=["exec line 1\n"],
        )
        app = WorkflowTUI(service)
        async with app.run_test():
            detail = app.query_one(DetailPane)
            # Tail widget is visible because the selected workflow is IN_PROGRESS.
            assert detail.is_tail_visible() is True
            # First poll happens synchronously inside _start_tail; trigger one
            # more cycle so the second scripted plan chunk is consumed.
            app._poll_tail()
            tail_log = detail.query_one("#tail_log")
            # ``Log.lines`` returns the rendered lines; expect both plan chunks
            # plus the single execute chunk plus the stage header lines.
            rendered = "\n".join(str(line) for line in tail_log.lines)
            assert "plan line 1" in rendered
            assert "plan line 2" in rendered
            assert "exec line 1" in rendered
            assert "[plan]" in rendered
            assert "[execute]" in rendered

    async def test_tail_advances_offset_so_lines_are_not_repeated(self):
        service, wf_id = _running_setup(plan_chunks=["alpha\n", "beta\n", "gamma\n"])
        app = WorkflowTUI(service)
        async with app.run_test():
            # Three poll cycles consume one scripted chunk each; fourth poll
            # finds nothing and must not duplicate previously-streamed bytes.
            app._poll_tail()
            app._poll_tail()
            app._poll_tail()
            detail = app.query_one(DetailPane)
            tail_log = detail.query_one("#tail_log")
            rendered = "\n".join(str(line) for line in tail_log.lines)
            assert rendered.count("alpha") == 1
            assert rendered.count("beta") == 1
            assert rendered.count("gamma") == 1
            # Offsets advanced past every emitted byte.
            assert app._tail_offsets["plan"] == sum(
                len(s.encode("utf-8")) for s in ["alpha\n", "beta\n", "gamma\n"]
            )

    async def test_tail_hides_when_workflow_finishes_mid_view(self):
        service, wf_id = _running_setup(plan_chunks=["running...\n"])
        app = WorkflowTUI(service)
        async with app.run_test():
            detail = app.query_one(DetailPane)
            assert detail.is_tail_visible() is True
            assert app._tail_workflow_id == wf_id

            # Workflow transitions to COMPLETED while the TUI is open.
            service.set_status(wf_id, WorkflowStatus.COMPLETED)
            app._refresh_workflows()

            assert detail.is_tail_visible() is False
            assert app._tail_workflow_id is None
            assert app._tail_timer is None

    async def test_pause_binding_toggles_auto_scroll(self):
        service, _ = _running_setup(plan_chunks=["x\n"])
        app = WorkflowTUI(service)
        async with app.run_test() as pilot:
            detail = app.query_one(DetailPane)
            tail = detail.query_one(LogTail)
            tail_log = tail.query_one("#tail_log")
            assert tail_log.auto_scroll is True

            await pilot.press("space")
            assert tail.is_paused is True
            assert tail_log.auto_scroll is False

            await pilot.press("space")
            assert tail.is_paused is False
            assert tail_log.auto_scroll is True

    async def test_tail_does_not_start_for_non_running_workflow(
        self, fake_service: WorkflowService
    ):
        # ``fake_service`` only has PENDING and COMPLETED workflows.
        app = WorkflowTUI(fake_service)
        async with app.run_test():
            detail = app.query_one(DetailPane)
            assert detail.is_tail_visible() is False
            assert app._tail_workflow_id is None
            assert app._tail_timer is None


# ---------------------------------------------------------------------------
# Async action plumbing — service calls must not block Textual's main loop.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAsyncActions:
    async def test_run_action_async_returns_immediately(self, fake_service: WorkflowService):
        """``_run_action_async`` dispatches the callable to a worker thread
        and must return to the main loop without waiting for the work to
        finish, so the TUI stays responsive while ``service.start`` /
        ``service.approve_plan`` / ... are driving a graph.
        """
        blocker = threading.Event()
        finished = threading.Event()

        def slow_fn() -> str:
            # Simulate a long-running service call that would otherwise
            # freeze the UI if it ran on the main loop.
            blocker.wait(timeout=5)
            finished.set()
            return "slow done"

        app = WorkflowTUI(fake_service)
        async with app.run_test() as pilot:
            t0 = time.monotonic()
            app._run_action_async("slow op", slow_fn)
            elapsed = time.monotonic() - t0

            # The dispatch call returned well before ``slow_fn`` could possibly
            # complete; the worker is still parked on ``blocker``.
            assert elapsed < 0.5
            assert not finished.is_set()

            # Release the worker and let Textual's event loop process the
            # ``call_from_thread`` callbacks the worker schedules.
            blocker.set()
            for _ in range(50):
                await pilot.pause()
                if finished.is_set():
                    break
            assert finished.is_set()
