"""Custom widgets for the Workflow TUI."""

from __future__ import annotations

from typing import List, Optional

from textual.widgets import DataTable, Label, Static

from dispatcher.constants import STATUS_DISPLAY
from orchestrator.workflow_service import WorkflowDetail, WorkflowSummary


class WorkflowList(Static):
    """Scrollable workflow list rendered as a DataTable."""

    DEFAULT_CSS = """
    WorkflowList {
        height: 100%;
        width: 60%;
    }
    DataTable {
        height: 100%;
        width: 100%;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._workflows: List[WorkflowSummary] = []

    def compose(self):
        table = DataTable(id="workflow_table", cursor_type="row")
        table.add_columns("Ticket", "Status", "Updated", "Workflow ID")
        yield table

    def update_workflows(self, workflows: List[WorkflowSummary]) -> None:
        table = self.query_one("#workflow_table", DataTable)

        # Preserve current selection when the table is rebuilt on auto-refresh.
        selected_id = None
        try:
            if table.cursor_row is not None and table.is_valid_row_index(table.cursor_row):
                selected_id = self._workflows[table.cursor_row].id
        except Exception:
            selected_id = None

        self._workflows = workflows
        table.clear()
        for wf in workflows:
            status_val = wf.status.value
            emoji, label = STATUS_DISPLAY.get(status_val, ("  ", status_val))
            updated = (wf.updated_at or "")[:19].replace("T", " ")
            table.add_row(
                wf.ticket_key,
                f"{emoji} {label}",
                updated,
                wf.id,
                key=wf.id,
            )

        if not workflows:
            return

        selected_index = 0
        if selected_id is not None:
            for idx, wf in enumerate(workflows):
                if wf.id == selected_id:
                    selected_index = idx
                    break

        try:
            table.move_cursor(row=selected_index, column=0)
        except Exception:
            pass

    def get_selected_workflow(self) -> Optional[WorkflowSummary]:
        try:
            table = self.query_one("#workflow_table", DataTable)
        except Exception:
            return None
        if table.cursor_row is None or table.row_count == 0:
            return None
        if not table.is_valid_row_index(table.cursor_row):
            return None
        if table.cursor_row >= len(self._workflows):
            return None
        return self._workflows[table.cursor_row]


class DetailPane(Static):
    """Detail pane showing selected workflow information."""

    DEFAULT_CSS = """
    DetailPane {
        height: 100%;
        width: 40%;
        padding: 1;
        border: solid $primary;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._workflow: Optional[WorkflowDetail] = None

    def compose(self):
        yield Label("Select a workflow to view details", id="detail_title")
        yield Static("", id="detail_body")

    def update_workflow(self, workflow: Optional[WorkflowDetail]) -> None:
        self._workflow = workflow
        try:
            title = self.query_one("#detail_title", Label)
            body = self.query_one("#detail_body", Static)
        except Exception:
            return
        if workflow is None:
            title.update("No workflow selected")
            body.update("")
            return

        status_val = workflow.status.value
        emoji, label = STATUS_DISPLAY.get(status_val, ("  ", status_val))
        title.update(f"{emoji} {workflow.ticket_key} — {label}")

        lines: List[str] = [
            f"[b]Workflow ID:[/b] {workflow.id}",
            f"[b]Status:[/b]     {status_val}",
            f"[b]Created:[/b]    {(workflow.created_at or '')[:19].replace('T', ' ')}",
            f"[b]Updated:[/b]    {(workflow.updated_at or '')[:19].replace('T', ' ')}",
            "",
        ]

        work_plan = workflow.work_plan or {}
        if work_plan:
            summary = work_plan.get("summary", "")
            if summary:
                lines.append(f"[b]Summary:[/b]    {summary}")
                lines.append("")

        if workflow.pr_url:
            lines.append(f"[b]PR URL:[/b]     {workflow.pr_url}")
            lines.append("")

        if workflow.retry_count:
            lines.append(f"[b]Retries:[/b]    {workflow.retry_count}")
            lines.append("")

        body.update("\n".join(lines))


class StatusBar(Static):
    """Bottom status bar with keybindings help."""

    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        width: 100%;
        background: $surface;
        color: $text;
        content-align: center middle;
    }
    """

    def compose(self):
        yield Label(
            "q:quit  r:refresh  n:new-run  a:approve  j:reject  c:clarify  y:retry  x:cancel  "
            "p:comment-pr  o:approve-pr  l:logs  d:clear-db  ↑↓:navigate"
        )
