"""Main Textual App for the Workflow TUI."""

from __future__ import annotations

import os
from typing import Optional

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
from state.workflow_repository import list_workflows


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
    ]

    def __init__(self) -> None:
        super().__init__()
        self._refresh_timer: Optional[Timer] = None
        self._poll_interval = float(os.environ.get("DISPATCHER_TUI_POLL", "2"))

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
            workflows = list_workflows(limit=100)
        except Exception:
            workflows = []
        workflow_list = self.query_one(WorkflowList)
        workflow_list.update_workflows(workflows)
        detail = self.query_one(DetailPane)
        selected = workflow_list.get_selected_workflow()
        detail.update_workflow(selected)

    def _get_selected(self) -> Optional[dict]:
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
                msg = run_workflow(ticket_key, dry_run=False)
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
            msg = approve_workflow(wf.get("ticket_key"), wf["id"])
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
                msg = reject_workflow(wf.get("ticket_key"), wf["id"], reason)
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
            msg = clarify_workflow(wf.get("ticket_key"), wf["id"])
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
            msg = retry_workflow(wf.get("ticket_key"), wf["id"])
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
                msg = cancel_workflow(wf.get("ticket_key"), wf["id"], reason)
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
            msg = approve_pr(wf.get("ticket_key"), wf["id"])
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
            msg = comment_pr(wf.get("ticket_key"), wf["id"])
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
                msg = reject_pr(wf.get("ticket_key"), wf["id"], reason)
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
            msg = show_logs(wf.get("ticket_key"), wf["id"])
            self._notify(msg, "information")
        except ActionError as e:
            self._notify(str(e), "error")

    def action_clear_db(self) -> None:
        def on_confirm(confirmed: bool) -> None:
            if not confirmed:
                return
            try:
                msg = clear_database()
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
        detail.update_workflow(selected)


def run_tui() -> None:
    """Entry point for the TUI application."""
    load_dotenv()
    load_runtime_secrets_from_keyvault()
    app = WorkflowTUI()
    app.run()
