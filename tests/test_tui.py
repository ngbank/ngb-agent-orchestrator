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
        # ``log_scripts[workflow_id][stream]`` is a list of byte-string
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

    async def test_quit_closes_service_with_close_method(
        self, sample_summaries: List[WorkflowSummary], sample_details: Dict[str, WorkflowDetail]
    ):
        """``action_quit`` must close the underlying service so workers
        blocked in an HTTP stream get unblocked and the process can exit.
        See ``WorkflowTUI.action_quit`` docstring for context.
        """

        class ClosableService(FakeWorkflowService):
            def __init__(self, *args: object, **kwargs: object) -> None:
                super().__init__(*args, **kwargs)  # type: ignore[arg-type]
                self.close_calls = 0

            def close(self) -> None:
                self.close_calls += 1

        service = ClosableService(summaries=sample_summaries, details=sample_details)
        app = WorkflowTUI(service)
        async with app.run_test() as pilot:
            await pilot.press("q")
            assert not app.is_running
        assert service.close_calls == 1

    async def test_quit_tolerates_service_close_errors(
        self, sample_summaries: List[WorkflowSummary], sample_details: Dict[str, WorkflowDetail]
    ):
        """A failure inside ``service.close`` must not block quit."""

        class ExplodingService(FakeWorkflowService):
            def close(self) -> None:
                raise RuntimeError("boom")

        service = ExplodingService(summaries=sample_summaries, details=sample_details)
        app = WorkflowTUI(service)
        async with app.run_test() as pilot:
            await pilot.press("q")
            assert not app.is_running

    async def test_quit_stops_refresh_and_tail_timers(
        self, sample_summaries: List[WorkflowSummary], sample_details: Dict[str, WorkflowDetail]
    ):
        """Quitting cancels the periodic timers so no fresh workers spawn
        during shutdown."""
        service = FakeWorkflowService(summaries=sample_summaries, details=sample_details)
        app = WorkflowTUI(service)
        async with app.run_test() as pilot:
            # Sanity: at least one timer is active.
            assert app._refresh_timer is not None
            await pilot.press("q")
            assert not app.is_running
        assert app._refresh_timer is None
        assert app._tail_timer is None

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

    async def test_check_action_hides_inapplicable_workflow_actions(
        self,
        sample_summaries: List[WorkflowSummary],
        sample_details: Dict[str, WorkflowDetail],
    ):
        """``check_action`` consults the registry so the footer reshapes
        per-row instead of advertising every binding everywhere."""
        service = FakeWorkflowService(summaries=sample_summaries, details=sample_details)
        app = WorkflowTUI(service)
        async with app.run_test():
            # First row is PENDING — only globals + retry/cancel apply, not approve.
            assert app.check_action("approve", ()) is False
            assert app.check_action("approve_pr", ()) is False
            assert app.check_action("refresh", ()) is True
            # ``quit`` is owned by the framework default (not registry).
            assert app.check_action("quit", ()) is True

    async def test_check_action_enables_approve_when_pending_approval(
        self,
        sample_summaries: List[WorkflowSummary],
        sample_details: Dict[str, WorkflowDetail],
    ):
        # Force the first workflow into PENDING_APPROVAL.
        wf_id = sample_summaries[0].id
        service = FakeWorkflowService(summaries=sample_summaries, details=sample_details)
        service.set_status(wf_id, WorkflowStatus.PENDING_APPROVAL)
        app = WorkflowTUI(service)
        async with app.run_test():
            assert app.check_action("approve", ()) is True
            assert app.check_action("reject", ()) is True
            # Clarify needs concerns in the work plan — not present here.
            assert app.check_action("clarify", ()) is False
            assert app.check_action("retry", ()) is False
            # PR actions stay hidden until PENDING_PR_APPROVAL.
            assert app.check_action("approve_pr", ()) is False

    async def test_check_action_pr_visible_on_status_alone(
        self,
        sample_summaries: List[WorkflowSummary],
    ):
        """PR actions must surface as soon as the row is in
        ``PENDING_PR_APPROVAL`` regardless of whether the ``pr_url`` column
        is populated.

        ``code_generator/persist_results`` writes the status without writing
        the dedicated ``pr_url`` column (the URL lives in
        ``code_generation_summary.pr_url``), so requiring ``d.pr_url`` here would
        hide the actions for every PR-awaiting workflow in practice. The
        CLI handlers (``_handle_*_pr``) gate on status only, so the TUI
        must match.
        """
        wf_id = "wf-pr"
        summary = _make_summary(wf_id, "AOS-9", WorkflowStatus.PENDING_PR_APPROVAL)
        detail_no_url = _make_detail(wf_id, "AOS-9", WorkflowStatus.PENDING_PR_APPROVAL)
        service = FakeWorkflowService(summaries=[summary], details={wf_id: detail_no_url})
        app = WorkflowTUI(service)
        async with app.run_test():
            assert app.check_action("approve_pr", ()) is True
            assert app.check_action("comment_pr", ()) is True
            assert app.check_action("reject_pr", ()) is True

        detail_with_url = _make_detail(
            wf_id,
            "AOS-9",
            WorkflowStatus.PENDING_PR_APPROVAL,
            pr_url="https://github.com/org/repo/pull/42",
        )
        service2 = FakeWorkflowService(summaries=[summary], details={wf_id: detail_with_url})
        app2 = WorkflowTUI(service2)
        async with app2.run_test():
            assert app2.check_action("approve_pr", ()) is True
            assert app2.check_action("comment_pr", ()) is True
            assert app2.check_action("reject_pr", ()) is True


# ---------------------------------------------------------------------------
# Stage C — live log tailing for in-progress workflows
# ---------------------------------------------------------------------------


def _running_setup(
    workflow_chunks: Optional[List[str]] = None,
) -> tuple[FakeWorkflowService, str]:
    """Build a FakeWorkflowService with a single IN_PROGRESS workflow and a
    scripted log stream for tailing tests."""
    wf_id = "wf-running"
    summary = _make_summary(wf_id, "AOS-145", WorkflowStatus.IN_PROGRESS)
    detail = _make_detail(wf_id, "AOS-145", WorkflowStatus.IN_PROGRESS)
    scripts: Dict[str, List[str]] = {}
    if workflow_chunks:
        scripts["workflow"] = workflow_chunks
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
            workflow_chunks=["workflow line 1\n", "workflow line 2\n"],
        )
        app = WorkflowTUI(service)
        async with app.run_test():
            detail = app.query_one(DetailPane)
            # Tail widget is visible because the selected workflow is IN_PROGRESS.
            assert detail.is_tail_visible() is True
            # First poll happens synchronously inside _start_tail; trigger one
            # more cycle so the second scripted workflow chunk is consumed.
            app._poll_tail()
            tail_log = detail.query_one("#tail_log")
            # ``Log.lines`` returns the rendered lines; expect both chunks.
            rendered = "\n".join(str(line) for line in tail_log.lines)
            assert "workflow line 1" in rendered
            assert "workflow line 2" in rendered

    async def test_tail_advances_offset_so_lines_are_not_repeated(self):
        service, wf_id = _running_setup(workflow_chunks=["alpha\n", "beta\n", "gamma\n"])
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
            # Offset advanced past every emitted byte.
            assert app._tail_offset == sum(
                len(s.encode("utf-8")) for s in ["alpha\n", "beta\n", "gamma\n"]
            )

    async def test_tail_hides_when_workflow_finishes_mid_view(self):
        service, wf_id = _running_setup(workflow_chunks=["running...\n"])
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
        service, _ = _running_setup(workflow_chunks=["x\n"])
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


# ---------------------------------------------------------------------------
# Async tail polling \u2014 ``service.read_logs`` must not block the main loop
# when the user navigates onto a running workflow.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAsyncTailPolling:
    async def test_schedule_tail_poll_returns_immediately_when_read_logs_blocks(self):
        """Navigating onto an IN_PROGRESS workflow must not freeze the UI
        while ``read_logs`` is slow (e.g. opening an SSE stream in remote
        mode). ``_schedule_tail_poll`` parks the work on a worker thread and
        returns to the main loop right away.
        """
        wf_id = "wf-slow-tail"
        summary = _make_summary(wf_id, "AOS-145", WorkflowStatus.IN_PROGRESS)
        detail = _make_detail(wf_id, "AOS-145", WorkflowStatus.IN_PROGRESS)

        blocker = threading.Event()
        read_started = threading.Event()
        read_returned = threading.Event()

        class BlockingService(FakeWorkflowService):
            def read_logs(self, workflow_id, stage=None, after_offset=0):
                # Mark that the worker thread reached the service call, then
                # park so the test can prove the main loop never blocks here.
                read_started.set()
                blocker.wait(timeout=5)
                try:
                    return super().read_logs(workflow_id, stage=stage, after_offset=after_offset)
                finally:
                    read_returned.set()

        service = BlockingService(
            summaries=[summary],
            details={wf_id: detail},
            log_scripts={wf_id: {"workflow": ["streamed-line\n"]}},
        )
        app = WorkflowTUI(service)
        async with app.run_test() as pilot:
            # Wait for the worker spawned by ``_start_tail`` to actually call
            # ``read_logs``; if dispatch is genuinely non-blocking, the main
            # loop reaches this point even though ``read_logs`` is parked.
            for _ in range(50):
                if read_started.is_set():
                    break
                await pilot.pause()
            assert read_started.is_set(), "tail worker never reached read_logs"
            assert not read_returned.is_set(), "read_logs unexpectedly returned"

            # Main loop is still responsive: an unrelated synchronous TUI
            # operation completes instantly while the worker is parked.
            t0 = time.monotonic()
            app.query_one(DetailPane)
            assert time.monotonic() - t0 < 0.2

            # Release the worker; the scheduled chunk eventually lands in
            # the tail widget via ``call_from_thread`` \u2192 ``_apply_tail_chunk``.
            blocker.set()
            for _ in range(50):
                await pilot.pause()
                tail_log = app.query_one(DetailPane).query_one("#tail_log")
                rendered = "\n".join(str(line) for line in tail_log.lines)
                if "streamed-line" in rendered:
                    break
            assert "streamed-line" in rendered

    async def test_overlapping_polls_are_debounced(self):
        """If a previous tail poll hasn't returned, the next timer tick must
        skip rather than stack workers \u2014 otherwise a slow ``read_logs`` would
        spawn a thread per tick.
        """
        service, _wf = _running_setup(workflow_chunks=["x\n"])
        app = WorkflowTUI(service)
        async with app.run_test() as pilot:
            # Drain the initial poll worker spawned by ``_start_tail`` and its
            # pending ``_mark_tail_idle`` callback before tampering with the
            # in-flight flag.
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert app._tail_in_flight is False

            # Simulate a previous poll still in flight, then trigger another
            # scheduled tick. ``_schedule_tail_poll`` must bail out instead of
            # spawning a second worker.
            app._tail_in_flight = True
            app._schedule_tail_poll()
            await pilot.pause()
            assert app._tail_in_flight is True


# ---------------------------------------------------------------------------
# Modal semantics \u2014 the buttons (Submit / Cancel) decide whether the action
# runs, not the contents of the text field. A previous bug coerced an empty
# input to ``None`` on Submit, which made ``action_cancel`` / ``action_reject``
# silently drop the request when the user pressed Submit/Enter without typing.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestInputModal:
    async def _push_and_dismiss(
        self,
        *,
        action: str,
        typed: str,
    ) -> object:
        """Push an ``InputModal`` and trigger either Submit or Cancel.

        ``action`` is ``"submit"``, ``"enter"`` (pressing Enter inside the
        input), or ``"cancel"``. Returns whatever the modal dismissed with.
        """
        from textual.app import App
        from textual.widgets import Input

        from dispatcher.tui.modals import InputModal

        captured: Dict[str, object] = {}

        class _Host(App):
            def on_mount(self) -> None:
                def _done(value: object) -> None:
                    captured["value"] = value

                self.push_screen(InputModal("title", placeholder="..."), _done)

        host = _Host()
        async with host.run_test() as pilot:
            await pilot.pause()
            # ``host.screen`` is the modal that ``push_screen`` activated;
            # query through it so we don't accidentally hit the default
            # screen before the modal has mounted.
            input_widget = host.screen.query_one("#input_field", Input)
            if typed:
                input_widget.value = typed
            await pilot.pause()
            if action == "submit":
                await pilot.click("#submit")
            elif action == "enter":
                input_widget.focus()
                await pilot.press("enter")
            elif action == "cancel":
                await pilot.click("#cancel")
            else:  # pragma: no cover - defensive
                raise ValueError(action)
            # Give the dismiss callback a chance to run.
            for _ in range(20):
                await pilot.pause()
                if "value" in captured:
                    break
        return captured.get("value", "<never-dismissed>")

    async def test_submit_with_empty_input_returns_empty_string(self):
        """Submit must dispatch \u2014 the field's emptiness is irrelevant. This
        is the bug the user hit: pressing Submit on the cancel-reason modal
        without typing a reason previously dropped the cancel silently."""
        value = await self._push_and_dismiss(action="submit", typed="")
        assert value == ""

    async def test_enter_with_empty_input_returns_empty_string(self):
        value = await self._push_and_dismiss(action="enter", typed="")
        assert value == ""

    async def test_submit_with_text_returns_text(self):
        value = await self._push_and_dismiss(action="submit", typed="because reasons")
        assert value == "because reasons"

    async def test_cancel_button_returns_none(self):
        value = await self._push_and_dismiss(action="cancel", typed="ignored")
        assert value is None


# ---------------------------------------------------------------------------
# Editor handoff — clarify and comment-PR both shell out to ``$EDITOR``. With
# the TUI active, Textual must release the terminal (``App.suspend``) for the
# editor subprocess; otherwise the editor's output is overdrawn and keys are
# captured by Textual bindings. The TUI provides this via a worker-friendly
# editor runner that bounces the suspended call onto the main thread.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestEditorHandoff:
    async def test_run_editor_suspended_wraps_subprocess_in_app_suspend(
        self, fake_service: WorkflowService
    ):
        """``_run_editor_suspended`` must enter ``app.suspend()`` *before* it
        spawns the editor and exit it *after* the subprocess returns —
        otherwise Textual will still own the TTY when the editor draws."""
        from contextlib import contextmanager
        from unittest.mock import patch

        events: List[str] = []

        @contextmanager
        def fake_suspend(_self):
            events.append("suspend_enter")
            try:
                yield
            finally:
                events.append("suspend_exit")

        def fake_run(cmd, **kwargs):
            events.append(f"run:{cmd[0]}")

            class _CP:
                returncode = 0

            return _CP()

        app = WorkflowTUI(fake_service)
        async with app.run_test():
            with (
                patch.object(WorkflowTUI, "suspend", fake_suspend),
                patch("dispatcher.tui.app.subprocess.run", side_effect=fake_run),
            ):
                app._run_editor_suspended(["nano", "/tmp/x"])

        assert events == ["suspend_enter", "run:nano", "suspend_exit"]

    async def test_make_editor_runner_bridges_through_call_from_thread(
        self, fake_service: WorkflowService
    ):
        """The runner handed to the worker must dispatch the suspended call
        onto the Textual main thread via ``call_from_thread`` — never run
        the editor directly from the worker (which would race the driver).
        """
        from unittest.mock import patch

        app = WorkflowTUI(fake_service)
        async with app.run_test():
            with patch.object(app, "call_from_thread") as mock_call:
                runner = app._make_editor_runner()
                runner(["nano", "/tmp/x"])

            mock_call.assert_called_once_with(app._run_editor_suspended, ["nano", "/tmp/x"])

    async def test_action_clarify_forwards_editor_runner_to_wrapper(
        self, fake_service: WorkflowService
    ):
        """``action_clarify`` must build an editor runner and pass it to the
        ``clarify_workflow`` wrapper so it can reach the underlying handler.
        """
        from unittest.mock import patch

        captured: Dict[str, object] = {}

        def fake_clarify_workflow(service, ticket_key, workflow_id, *, editor_runner):
            captured["editor_runner"] = editor_runner
            return "Clarification submitted."

        summary = _make_summary("wf-c", "AOS-9", WorkflowStatus.PENDING_WORKPLAN_CLARIFICATION)
        detail = _make_detail("wf-c", "AOS-9", WorkflowStatus.PENDING_WORKPLAN_CLARIFICATION)
        service = FakeWorkflowService(summaries=[summary], details={"wf-c": detail})

        app = WorkflowTUI(service)
        async with app.run_test() as pilot:
            with patch("dispatcher.tui.app.clarify_workflow", side_effect=fake_clarify_workflow):
                await pilot.pause()
                # Select the workflow row before triggering the action.
                wf_list = app.query_one(WorkflowList)
                table = wf_list.query_one("DataTable")
                table.focus()
                table.move_cursor(row=0)
                await pilot.pause()
                app.action_clarify()
                await app.workers.wait_for_complete()

        assert callable(captured.get("editor_runner"))

    async def test_action_comment_pr_forwards_editor_runner_to_wrapper(
        self, fake_service: WorkflowService
    ):
        from unittest.mock import patch

        captured: Dict[str, object] = {}

        def fake_comment_pr(service, ticket_key, workflow_id, *, editor_runner):
            captured["editor_runner"] = editor_runner
            return "PR comment submitted."

        summary = _make_summary("wf-p", "AOS-10", WorkflowStatus.PENDING_PR_APPROVAL)
        detail = _make_detail("wf-p", "AOS-10", WorkflowStatus.PENDING_PR_APPROVAL)
        service = FakeWorkflowService(summaries=[summary], details={"wf-p": detail})

        app = WorkflowTUI(service)
        async with app.run_test() as pilot:
            with patch("dispatcher.tui.app.comment_pr", side_effect=fake_comment_pr):
                await pilot.pause()
                wf_list = app.query_one(WorkflowList)
                table = wf_list.query_one("DataTable")
                table.focus()
                table.move_cursor(row=0)
                await pilot.pause()
                app.action_comment_pr()
                await app.workers.wait_for_complete()

        assert callable(captured.get("editor_runner"))
