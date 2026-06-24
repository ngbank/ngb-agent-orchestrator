"""Main Textual App for the Workflow TUI."""

from __future__ import annotations

import os
from typing import Dict, Optional

from dotenv import load_dotenv
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.timer import Timer
from textual.widgets import Footer, Header

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
from dispatcher.tui.widgets import DetailPane, StatusBar, WorkflowList
from orchestrator.runtime_secrets import load_runtime_secrets_from_keyvault
from orchestrator.workflow_service import (
    WorkflowDetail,
    WorkflowService,
    WorkflowSummary,
    build_workflow_service_from_env,
)
from state.workflow_status import WorkflowStatus

# Stage names tailed in the live log view.  Matches the stages
# ``WorkflowService.read_logs`` knows about (plan + execute).
_TAIL_STAGES = ("plan", "execute")


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

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh"),
        ("n", "new_run", "New Run"),
        ("a", "approve", "Approve"),
        ("j", "reject", "Reject"),
        ("c", "clarify", "Clarify"),
        ("y", "retry", "Retry"),
        ("x", "cancel", "Cancel"),
        ("p", "comment_pr", "Comment PR"),
        ("o", "approve_pr", "Approve PR"),
        ("l", "logs", "Logs"),
        ("d", "clear_db", "Clear DB"),
        ("space", "toggle_tail_pause", "Pause Tail"),
    ]

    def __init__(self, service: WorkflowService) -> None:
        super().__init__()
        self._service = service
        self._refresh_timer: Optional[Timer] = None
        self._poll_interval = float(os.environ.get("DISPATCHER_TUI_POLL", "2"))
        # Live log tailing state (Stage C).  ``_tail_workflow_id`` is the
        # workflow currently being tailed; ``_tail_offsets`` tracks the
        # next byte to fetch per stage so reconnect-after-poll only
        # delivers new bytes.
        self._tail_timer: Optional[Timer] = None
        self._tail_interval = float(os.environ.get("DISPATCHER_TUI_TAIL_POLL", "1"))
        self._tail_workflow_id: Optional[str] = None
        self._tail_offsets: Dict[str, int] = {}
        self._tail_paused: bool = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main"):
            yield WorkflowList()
            yield DetailPane()
        yield StatusBar()
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

    def _fetch_detail(self, summary: Optional[WorkflowSummary]) -> Optional[WorkflowDetail]:
        if summary is None:
            return None
        try:
            return self._service.get(summary.id)
        except Exception:
            return None

    def _get_selected(self) -> Optional[WorkflowSummary]:
        return self.query_one(WorkflowList).get_selected_workflow()

    def _notify(self, message: str, severity: str = "information") -> None:
        # Textual's `severity` parameter uses Literal types; we accept str at the
        # boundary for caller convenience and trust the documented values.
        self.notify(message, severity=severity, timeout=4)  # pyright: ignore[reportArgumentType]

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

            try:
                msg = run_workflow(self._service, ticket_key, dry_run=False)
                self._notify(msg, "information")
            except ActionError as e:
                self._notify(str(e), "error")
            self._refresh_workflows()

        self.push_screen(
            InputModal("Enter ticket key to start workflow:", placeholder="AOS-999"),
            on_ticket,
        )

    def action_approve(self) -> None:
        wf = self._get_selected()
        if wf is None:
            self._notify("No workflow selected.", "warning")
            return
        try:
            msg = approve_workflow(self._service, wf.ticket_key, wf.id)
            self._notify(msg, "information")
        except ActionError as e:
            self._notify(str(e), "error")
        self._refresh_workflows()

    def action_reject(self) -> None:
        wf = self._get_selected()
        if wf is None:
            self._notify("No workflow selected.", "warning")
            return

        def on_reason(reason: str | None) -> None:
            if reason is None:
                return
            try:
                msg = reject_workflow(self._service, wf.ticket_key, wf.id, reason)
                self._notify(msg, "information")
            except ActionError as e:
                self._notify(str(e), "error")
            self._refresh_workflows()

        self.push_screen(
            InputModal("Enter rejection reason:", placeholder="Reason..."),
            on_reason,
        )

    def action_clarify(self) -> None:
        wf = self._get_selected()
        if wf is None:
            self._notify("No workflow selected.", "warning")
            return
        try:
            msg = clarify_workflow(self._service, wf.ticket_key, wf.id)
            self._notify(msg, "information")
        except ActionError as e:
            self._notify(str(e), "error")
        self._refresh_workflows()

    def action_retry(self) -> None:
        wf = self._get_selected()
        if wf is None:
            self._notify("No workflow selected.", "warning")
            return
        try:
            msg = retry_workflow(self._service, wf.ticket_key, wf.id)
            self._notify(msg, "information")
        except ActionError as e:
            self._notify(str(e), "error")
        self._refresh_workflows()

    def action_cancel(self) -> None:
        wf = self._get_selected()
        if wf is None:
            self._notify("No workflow selected.", "warning")
            return

        def on_reason(reason: str | None) -> None:
            if reason is None:
                return
            try:
                msg = cancel_workflow(self._service, wf.ticket_key, wf.id, reason)
                self._notify(msg, "information")
            except ActionError as e:
                self._notify(str(e), "error")
            self._refresh_workflows()

        self.push_screen(
            InputModal("Enter cancellation reason (optional):", placeholder="Reason..."),
            on_reason,
        )

    def action_approve_pr(self) -> None:
        wf = self._get_selected()
        if wf is None:
            self._notify("No workflow selected.", "warning")
            return
        try:
            msg = approve_pr(self._service, wf.ticket_key, wf.id)
            self._notify(msg, "information")
        except ActionError as e:
            self._notify(str(e), "error")
        self._refresh_workflows()

    def action_comment_pr(self) -> None:
        wf = self._get_selected()
        if wf is None:
            self._notify("No workflow selected.", "warning")
            return
        try:
            msg = comment_pr(self._service, wf.ticket_key, wf.id)
            self._notify(msg, "information")
        except ActionError as e:
            self._notify(str(e), "error")
        self._refresh_workflows()

    def action_reject_pr(self) -> None:
        wf = self._get_selected()
        if wf is None:
            self._notify("No workflow selected.", "warning")
            return

        def on_reason(reason: str | None) -> None:
            if reason is None:
                return
            try:
                msg = reject_pr(self._service, wf.ticket_key, wf.id, reason)
                self._notify(msg, "information")
            except ActionError as e:
                self._notify(str(e), "error")
            self._refresh_workflows()

        self.push_screen(
            InputModal("Enter PR rejection reason:", placeholder="Reason..."),
            on_reason,
        )

    def action_logs(self) -> None:
        wf = self._get_selected()
        if wf is None:
            self._notify("No workflow selected.", "warning")
            return
        try:
            msg = show_logs(self._service, wf.ticket_key, wf.id)
            self._notify(msg, "information")
        except ActionError as e:
            self._notify(str(e), "error")

    def action_clear_db(self) -> None:
        def on_confirm(confirmed: bool) -> None:
            if not confirmed:
                return
            try:
                msg = clear_database(self._service)
                self._notify(msg, "information")
            except ActionError as e:
                self._notify(str(e), "error")
            self._refresh_workflows()

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
        # Immediate first poll so the user sees backlog right away; then
        # schedule periodic polls until the workflow stops running.
        self._poll_tail()
        if self._tail_interval > 0:
            self._tail_timer = self.set_interval(self._tail_interval, self._poll_tail)

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

    def _poll_tail(self) -> None:
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


def run_tui() -> None:
    """Entry point for the TUI application."""
    load_dotenv()
    load_runtime_secrets_from_keyvault()
    service = build_workflow_service_from_env()
    app = WorkflowTUI(service)
    app.run()
