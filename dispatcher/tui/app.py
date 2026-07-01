"""Main Textual App for the Workflow TUI."""

from __future__ import annotations

import os
import subprocess
from typing import Callable, Dict, List, Optional

from dotenv import load_dotenv
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.timer import Timer
from textual.widgets import Footer, Header

from dispatcher.tui.action_registry import REGISTRY, action_for
from dispatcher.tui.actions import (
    ActionError,
    approve_pr,
    approve_workflow,
    cancel_workflow,
    clarify_workflow,
    clear_database,
    comment_pr,
    reject_pr,
    reject_workflow,
    retry_workflow,
    run_workflow,
    show_logs,
)
from dispatcher.tui.modals import ConfirmModal, InputModal
from dispatcher.tui.widgets import DetailPane, WorkflowList
from orchestrator.runtime_secrets import load_runtime_secrets_from_keyvault
from orchestrator.workflow_service import (
    WorkflowDetail,
    WorkflowService,
    WorkflowSummary,
    build_workflow_service_from_env,
)
from state.workflow_status import WorkflowStatus

# Log stream tailed in the live log view.  Matches
# ``WorkflowService.read_logs``' canonical workflow log.
_TAIL_STAGES = ("workflow",)


class WorkflowTUI(App[None]):
    """Textual TUI for keyboard-driven workflow management."""

    CSS = """
    Screen {
        layout: vertical;
    }
    #main {
        height: 1fr;
        width: 100%;
    }
    """

    # ``BINDINGS`` is assembled from the action registry plus the two
    # bindings that are independent of any selected workflow row (``quit``
    # always works; ``toggle_tail_pause`` is UI-state, not workflow-state).
    # The footer is then narrowed per row by ``check_action`` below, so
    # adding or removing actions touches the registry only — no edits here.
    BINDINGS = [
        ("q", "quit", "Quit"),
        *[(a.key, a.action, a.label) for a in REGISTRY],
        ("space", "toggle_tail_pause", "Pause Tail"),
    ]

    def __init__(self, service: WorkflowService) -> None:
        super().__init__()
        self._service = service
        self._refresh_timer: Optional[Timer] = None
        self._poll_interval = float(os.environ.get("DISPATCHER_TUI_POLL", "2"))
        # Cached detail for the currently-highlighted row. ``check_action``
        # consults this on every footer repaint, so we keep it in sync with
        # the DetailPane in ``_refresh_workflows`` and
        # ``on_data_table_row_highlighted`` rather than refetching from
        # the service per repaint.
        self._selected_detail: Optional[WorkflowDetail] = None
        # Live log tailing state (Stage C).  ``_tail_workflow_id`` is the
        # workflow currently being tailed; ``_tail_offsets`` tracks the
        # next byte to fetch per stage so reconnect-after-poll only
        # delivers new bytes.
        self._tail_timer: Optional[Timer] = None
        self._tail_interval = float(os.environ.get("DISPATCHER_TUI_TAIL_POLL", "1"))
        self._tail_workflow_id: Optional[str] = None
        self._tail_offsets: Dict[str, int] = {}
        self._tail_paused: bool = False
        # ``_tail_in_flight`` debounces overlapping polls: if a previous
        # ``read_logs`` is still running (slow SSE connect, blocked server)
        # the next timer tick is skipped instead of stacking workers.
        self._tail_in_flight: bool = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main"):
            yield WorkflowList()
            yield DetailPane()
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Dispatcher TUI"
        self.sub_title = "Workflow Management"
        self._refresh_workflows()
        if self._poll_interval > 0:
            self._refresh_timer = self.set_interval(self._poll_interval, self._refresh_workflows)

    def _refresh_workflows(self) -> None:
        try:
            workflows = self._service.list(limit=100)
        except Exception:
            workflows = []
        workflow_list = self.query_one(WorkflowList)
        workflow_list.update_workflows(workflows)
        detail = self.query_one(DetailPane)
        selected = workflow_list.get_selected_workflow()
        detail_dto = self._fetch_detail(selected)
        detail.update_workflow(detail_dto)
        self._sync_tail(detail_dto)
        self._set_selected_detail(detail_dto)

    def _fetch_detail(self, summary: Optional[WorkflowSummary]) -> Optional[WorkflowDetail]:
        if summary is None:
            return None
        try:
            return self._service.get(summary.id)
        except Exception:
            return None

    def _set_selected_detail(self, detail: Optional[WorkflowDetail]) -> None:
        """Cache the currently-selected detail and trigger a footer repaint.

        ``check_action`` reads ``self._selected_detail`` to decide which
        bindings to show. Calling ``refresh_bindings()`` makes Textual re-ask
        ``check_action`` for every binding so the footer reshapes to match
        the new selection / status.
        """
        self._selected_detail = detail
        # ``refresh_bindings`` is a no-op when the app is not yet mounted
        # (e.g. during construction). Guard the call so unit-test setup
        # doesn't blow up trying to query the screen stack.
        try:
            self.refresh_bindings()
        except Exception:
            pass

    def check_action(self, action: str, parameters: tuple[object, ...]) -> Optional[bool]:
        """Hide footer bindings whose registry predicate rejects the row.

        Returns:
            ``True`` to show the binding (Textual default), ``False`` to hide
            it from the ``Footer``, ``None`` for actions not owned by the
            registry (``quit``, ``toggle_tail_pause``) so the framework's
            default visibility applies.
        """
        entry = action_for(action)
        if entry is None:
            return True
        return entry.applies(self._selected_detail)

    def _get_selected(self) -> Optional[WorkflowSummary]:
        return self.query_one(WorkflowList).get_selected_workflow()

    def _notify(self, message: str, severity: str = "information") -> None:
        # Textual's `severity` parameter uses Literal types; we accept str at the
        # boundary for caller convenience and trust the documented values.
        self.notify(message, severity=severity, timeout=4)  # pyright: ignore[reportArgumentType]

    # ------------------------------------------------------------------
    # Async action plumbing
    # ------------------------------------------------------------------
    #
    # Service calls (start / approve / reject / retry / ...) are blocking:
    # in local mode they drive the LangGraph workflow end-to-end (LLM calls
    # included); in remote mode they fire-and-forget then follow the SSE
    # event stream synchronously. Running them on Textual's main loop would
    # freeze the UI for the duration of the run, so every action that hits
    # the service goes through ``_run_action_async`` which dispatches to a
    # worker thread and posts the result + refresh back on the main loop.

    def _run_action_async(
        self,
        label: str,
        fn: Callable[[], str],
        *,
        refresh_after: bool = True,
    ) -> None:
        """Run a blocking action in a Textual worker thread.

        ``label`` is shown in an immediate "..." notification so the user
        knows the action was accepted. ``fn`` runs off-thread; its return
        value is surfaced as an info notification, ``ActionError`` as an
        error notification. The workflow list is refreshed on completion
        unless ``refresh_after`` is False.
        """
        self._notify(f"{label}\u2026", "information")

        def worker() -> None:
            try:
                msg = fn()
            except ActionError as exc:
                self.call_from_thread(self._notify, str(exc), "error")
            except Exception as exc:  # defensive: never let a worker crash the app
                self.call_from_thread(self._notify, f"{label} failed: {exc}", "error")
            else:
                self.call_from_thread(self._notify, msg, "information")
            finally:
                if refresh_after:
                    self.call_from_thread(self._refresh_workflows)

        self.run_worker(
            worker,
            thread=True,
            group="actions",
            exit_on_error=False,
            description=label,
        )

    # ------------------------------------------------------------------
    # Editor handoff (suspend Textual so an external $EDITOR can use the TTY)
    # ------------------------------------------------------------------
    #
    # The clarify and comment-PR flows shell out to ``$EDITOR``. With the
    # TUI active, Textual keeps owning the alt-screen / mouse capture /
    # render loop, so the editor's output is overdrawn and key input is
    # captured by Textual bindings. ``App.suspend()`` releases the terminal
    # for the duration of the subprocess, then restores application mode
    # when the editor exits.
    #
    # Because actions run in worker threads (see ``_run_action_async``)
    # and ``App.suspend()`` mutates the driver state, the suspended call
    # is bounced to the main thread via ``call_from_thread`` — the worker
    # blocks until the editor exits, then resumes its own work.

    def _run_editor_suspended(self, cmd: List[str]) -> None:
        """Release the terminal to an external editor and restore on exit.

        Must run on the Textual main thread.
        """
        with self.suspend():
            subprocess.run(cmd, check=True)

    def _make_editor_runner(self) -> Callable[[List[str]], None]:
        """Return an editor runner that the worker thread can call safely."""

        def runner(cmd: List[str]) -> None:
            self.call_from_thread(self._run_editor_suspended, cmd)

        return runner

    def action_refresh(self) -> None:
        self._refresh_workflows()

    def action_new_run(self) -> None:
        def on_ticket(ticket: str | None) -> None:
            if ticket is None:
                return

            ticket_key = ticket.strip()
            if not ticket_key:
                self._notify("Ticket key is required.", "warning")
                return

            self._run_action_async(
                f"Starting workflow for {ticket_key}",
                lambda: run_workflow(self._service, ticket_key, dry_run=False),
            )

        self.push_screen(
            InputModal("Enter ticket key to start workflow:", placeholder="AOS-999"),
            on_ticket,
        )

    def action_approve(self) -> None:
        wf = self._get_selected()
        if wf is None:
            self._notify("No workflow selected.", "warning")
            return
        self._run_action_async(
            f"Approving {wf.ticket_key}",
            lambda: approve_workflow(self._service, wf.ticket_key, wf.id),
        )

    def action_reject(self) -> None:
        wf = self._get_selected()
        if wf is None:
            self._notify("No workflow selected.", "warning")
            return

        def on_reason(reason: str | None) -> None:
            if reason is None:
                return
            self._run_action_async(
                f"Rejecting {wf.ticket_key}",
                lambda: reject_workflow(self._service, wf.ticket_key, wf.id, reason),
            )

        self.push_screen(
            InputModal("Enter rejection reason:", placeholder="Reason..."),
            on_reason,
        )

    def action_clarify(self) -> None:
        wf = self._get_selected()
        if wf is None:
            self._notify("No workflow selected.", "warning")
            return
        editor_runner = self._make_editor_runner()
        self._run_action_async(
            f"Clarifying {wf.ticket_key}",
            lambda: clarify_workflow(
                self._service, wf.ticket_key, wf.id, editor_runner=editor_runner
            ),
        )

    def action_retry(self) -> None:
        wf = self._get_selected()
        if wf is None:
            self._notify("No workflow selected.", "warning")
            return
        self._run_action_async(
            f"Retrying {wf.ticket_key}",
            lambda: retry_workflow(self._service, wf.ticket_key, wf.id),
        )

    def action_cancel(self) -> None:
        wf = self._get_selected()
        if wf is None:
            self._notify("No workflow selected.", "warning")
            return

        def on_reason(reason: str | None) -> None:
            if reason is None:
                return
            self._run_action_async(
                f"Cancelling {wf.ticket_key}",
                lambda: cancel_workflow(self._service, wf.ticket_key, wf.id, reason),
            )

        self.push_screen(
            InputModal("Enter cancellation reason (optional):", placeholder="Reason..."),
            on_reason,
        )

    def action_approve_pr(self) -> None:
        wf = self._get_selected()
        if wf is None:
            self._notify("No workflow selected.", "warning")
            return
        self._run_action_async(
            f"Approving PR for {wf.ticket_key}",
            lambda: approve_pr(self._service, wf.ticket_key, wf.id),
        )

    def action_comment_pr(self) -> None:
        wf = self._get_selected()
        if wf is None:
            self._notify("No workflow selected.", "warning")
            return
        editor_runner = self._make_editor_runner()
        self._run_action_async(
            f"Commenting on PR for {wf.ticket_key}",
            lambda: comment_pr(self._service, wf.ticket_key, wf.id, editor_runner=editor_runner),
        )

    def action_reject_pr(self) -> None:
        wf = self._get_selected()
        if wf is None:
            self._notify("No workflow selected.", "warning")
            return

        def on_reason(reason: str | None) -> None:
            if reason is None:
                return
            self._run_action_async(
                f"Rejecting PR for {wf.ticket_key}",
                lambda: reject_pr(self._service, wf.ticket_key, wf.id, reason),
            )

        self.push_screen(
            InputModal("Enter PR rejection reason:", placeholder="Reason..."),
            on_reason,
        )

    def action_logs(self) -> None:
        wf = self._get_selected()
        if wf is None:
            self._notify("No workflow selected.", "warning")
            return
        self._run_action_async(
            f"Fetching logs for {wf.ticket_key}",
            lambda: show_logs(self._service, wf.ticket_key, wf.id),
            refresh_after=False,
        )

    def action_clear_db(self) -> None:
        def on_confirm(confirmed: bool) -> None:
            if not confirmed:
                return
            self._run_action_async(
                "Clearing database",
                lambda: clear_database(self._service),
            )

        self.push_screen(  # pyright: ignore[reportCallIssue]
            ConfirmModal("Are you sure you want to clear ALL workflows and checkpoints?"),
            on_confirm,  # pyright: ignore[reportArgumentType]
        )

    def on_data_table_row_highlighted(self, event) -> None:
        workflow_list = self.query_one(WorkflowList)
        selected = workflow_list.get_selected_workflow()
        detail = self.query_one(DetailPane)
        detail_dto = self._fetch_detail(selected)
        detail.update_workflow(detail_dto)
        self._sync_tail(detail_dto)
        self._set_selected_detail(detail_dto)

    # ------------------------------------------------------------------
    # Live log tailing (Stage C)
    # ------------------------------------------------------------------

    def _sync_tail(self, detail: Optional[WorkflowDetail]) -> None:
        """Start, stop, or keep tailing based on the currently-shown workflow.

        Called after every refresh and after every row-highlight change.
        Selecting a different running workflow restarts the tail from offset
        zero; transitioning to a non-running status stops the tail and the
        DetailPane reverts to the snapshot view automatically.
        """
        if detail is None or detail.status != WorkflowStatus.IN_PROGRESS:
            self._stop_tail()
            return
        if self._tail_workflow_id != detail.id:
            self._start_tail(detail.id)

    def _start_tail(self, workflow_id: str) -> None:
        self._stop_tail()
        self._tail_workflow_id = workflow_id
        self._tail_offsets = {stage: 0 for stage in _TAIL_STAGES}
        self._tail_paused = False
        try:
            self.query_one(DetailPane).clear_log_tail()
        except Exception:
            pass
        # First poll is dispatched off the main loop so navigating onto a
        # running workflow doesn't freeze the UI while ``read_logs`` connects
        # (in remote mode that opens an SSE stream with a read timeout).
        # Backlog still appears once the worker returns — typically within a
        # few hundred ms.
        self._schedule_tail_poll()
        if self._tail_interval > 0:
            self._tail_timer = self.set_interval(self._tail_interval, self._schedule_tail_poll)

    def _stop_tail(self) -> None:
        if self._tail_timer is not None:
            try:
                self._tail_timer.stop()
            except Exception:
                pass
            self._tail_timer = None
        self._tail_workflow_id = None
        self._tail_offsets = {}
        self._tail_paused = False
        # A worker still in flight will discard its results via the
        # workflow-id guard in ``_apply_tail_chunk``; reset the flag so the
        # next ``_start_tail`` can dispatch immediately.
        self._tail_in_flight = False

    def _schedule_tail_poll(self) -> None:
        """Dispatch a single tail-poll cycle to a worker thread.

        Called on every timer tick (and once from ``_start_tail``). Bails out
        if no workflow is selected or a previous poll hasn't returned yet —
        the latter prevents back-pressure piling up when ``read_logs`` is
        slow (e.g. a stuck remote server).
        """
        workflow_id = self._tail_workflow_id
        if workflow_id is None or self._tail_in_flight:
            return
        self._tail_in_flight = True
        self.run_worker(
            lambda: self._poll_tail_worker(workflow_id),
            thread=True,
            group="tail",
            exit_on_error=False,
            description=f"tail:{workflow_id[:8]}",
        )

    def _poll_tail_worker(self, workflow_id: str) -> None:
        """Worker-thread body for one tail poll cycle.

        Reads new bytes per stage and schedules ``_apply_tail_chunk`` on the
        main loop for each non-empty chunk. Errors are swallowed so a
        transient ``read_logs`` failure doesn't stop the tail — the next
        timer tick will retry.
        """
        try:
            for stage in _TAIL_STAGES:
                offset = self._tail_offsets.get(stage, 0)
                try:
                    chunks = self._service.read_logs(
                        workflow_id,
                        stage=stage,
                        after_offset=offset,
                    )
                except Exception:
                    continue
                for chunk in chunks:
                    if chunk.stage != stage or not chunk.content:
                        continue
                    self.call_from_thread(
                        self._apply_tail_chunk,
                        workflow_id,
                        chunk.stage,
                        chunk.content,
                        chunk.offset,
                    )
        finally:
            self.call_from_thread(self._mark_tail_idle)

    def _apply_tail_chunk(
        self,
        workflow_id: str,
        stage: str,
        content: str,
        chunk_offset: int,
    ) -> None:
        """Main-thread sink for a tail chunk produced by a worker.

        Discards the chunk if the user has navigated to a different workflow
        since the worker started (prevents one workflow's logs from leaking
        into another's view) and ignores already-applied bytes (defence in
        depth against overlapping polls).
        """
        if self._tail_workflow_id != workflow_id:
            return
        current = self._tail_offsets.get(stage, 0)
        end = chunk_offset + len(content.encode("utf-8"))
        if end <= current:
            return
        try:
            detail = self.query_one(DetailPane)
        except Exception:
            return
        detail.append_log_chunk(stage, content)
        self._tail_offsets[stage] = end

    def _mark_tail_idle(self) -> None:
        self._tail_in_flight = False

    def _poll_tail(self) -> None:
        """Synchronous one-shot poll — used by tests and as the engine the
        worker-thread variant wraps.

        Production code goes through ``_schedule_tail_poll`` so the UI never
        blocks on ``read_logs``; tests call this directly to make polling
        deterministic without coordinating with the worker thread.
        """
        workflow_id = self._tail_workflow_id
        if workflow_id is None:
            return
        try:
            detail = self.query_one(DetailPane)
        except Exception:
            return
        for stage in _TAIL_STAGES:
            offset = self._tail_offsets.get(stage, 0)
            try:
                chunks = self._service.read_logs(
                    workflow_id,
                    stage=stage,
                    after_offset=offset,
                )
            except Exception:
                continue
            for chunk in chunks:
                if chunk.stage != stage or not chunk.content:
                    continue
                detail.append_log_chunk(chunk.stage, chunk.content)
                self._tail_offsets[stage] = chunk.offset + len(chunk.content.encode("utf-8"))

    def action_toggle_tail_pause(self) -> None:
        try:
            detail = self.query_one(DetailPane)
        except Exception:
            return
        if not detail.is_tail_visible():
            return
        self._tail_paused = not self._tail_paused
        detail.set_tail_paused(self._tail_paused)
        self._notify(
            "Tail auto-scroll paused." if self._tail_paused else "Tail auto-scroll resumed.",
            "information",
        )

    async def action_quit(self) -> None:
        """Quit cleanly even when blocking workers are in flight.

        Textual's thread workers run on asyncio's default ThreadPoolExecutor,
        whose threads are non-daemon. A worker stuck in a blocking HTTP read
        (``submit_and_follow``'s SSE iterator, a slow ``read_logs`` poll)
        therefore keeps the whole process alive after ``app.run()`` returns,
        producing the "TUI hangs and doesn't return to the shell" symptom.

        We mitigate this in two steps:

        * Stop the periodic refresh / tail timers so no fresh workers spawn.
        * Close the HTTP client (when the service exposes ``close``); this
          tears down the connection pool and causes any in-flight streamed
          read to raise, letting the worker's exception handler return.

        The hard ``os._exit`` fallback that guarantees the shell prompt
        comes back lives in ``run_tui`` — see the comment there.
        """
        if self._refresh_timer is not None:
            self._refresh_timer.stop()
            self._refresh_timer = None
        if self._tail_timer is not None:
            self._tail_timer.stop()
            self._tail_timer = None
        close = getattr(self._service, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                # Best-effort: closing the client must never block quit.
                pass
        self.exit()


def run_tui() -> None:
    """Entry point for the TUI application."""
    load_dotenv()
    load_runtime_secrets_from_keyvault()
    service = build_workflow_service_from_env()
    app = WorkflowTUI(service)
    try:
        app.run()
    finally:
        # Textual's worker threads run on asyncio's default
        # ThreadPoolExecutor (non-daemon). If a worker is blocked in a
        # network read at quit time it will keep the Python process alive
        # past ``app.run()`` and the user gets stuck staring at a frozen
        # cleared terminal. ``action_quit`` already closes the HTTP client
        # to unblock such reads cleanly; this is the defence-in-depth
        # fallback for the case where a worker is blocked in something
        # else (subprocess, local LangGraph drive, etc.).
        close = getattr(service, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
        os._exit(0)
