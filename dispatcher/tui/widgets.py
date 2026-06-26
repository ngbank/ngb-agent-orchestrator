"""Custom widgets for the Workflow TUI."""

from __future__ import annotations

from typing import List, Optional

from textual.widgets import DataTable, Label, Log, Static

from dispatcher.constants import STATUS_DISPLAY
from orchestrator.workflow_service import WorkflowDetail, WorkflowSummary
from state.workflow_status import WorkflowStatus


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
            # ``STATUS_DISPLAY`` adds a trailing space to some emojis to
            # compensate for narrow rendering in CLI text output. ``DataTable``
            # does its own column padding, so that trailing space just
            # stretches the affected cell by one char and pushes every column
            # to its right out of alignment for that row only.
            emoji = emoji.rstrip()
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


class LogTail(Static):
    """Live log tailing widget.

    Wraps a Textual ``Log`` so the TUI can append streamed bytes as they
    arrive from ``WorkflowService.read_logs`` and toggle auto-scroll.  Each
    appended chunk is prefixed with a ``[stage]`` marker so plan/execute
    output stays distinguishable when both streams interleave.
    """

    DEFAULT_CSS = """
    LogTail {
        height: 1fr;
        width: 100%;
        border: solid $accent;
        margin-top: 1;
    }
    LogTail > Log {
        height: 1fr;
    }
    LogTail > #tail_status {
        height: 1;
        color: $text-muted;
        content-align: right middle;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._paused = False
        self._last_stage: Optional[str] = None

    def compose(self):
        yield Log(id="tail_log", auto_scroll=True, max_lines=10000)
        yield Static("[live · auto-scroll]", id="tail_status")

    def append_content(self, stage: str, content: str) -> None:
        if not content:
            return
        try:
            log = self.query_one("#tail_log", Log)
        except Exception:
            return
        if stage != self._last_stage:
            if self._last_stage is not None:
                log.write_line("")
            log.write_line(f"── [{stage}] ──")
            self._last_stage = stage
        log.write(content)

    def clear(self) -> None:
        try:
            self.query_one("#tail_log", Log).clear()
        except Exception:
            pass
        self._last_stage = None

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        try:
            log = self.query_one("#tail_log", Log)
            log.auto_scroll = not paused
        except Exception:
            pass
        try:
            status = self.query_one("#tail_status", Static)
            status.update("[live · paused]" if paused else "[live · auto-scroll]")
        except Exception:
            pass

    @property
    def is_paused(self) -> bool:
        return self._paused


class DetailPane(Static):
    """Detail pane showing selected workflow information.

    For non-running workflows the pane renders only the static metadata
    snapshot (today's behaviour).  When the selected workflow is
    ``IN_PROGRESS`` an embedded :class:`LogTail` becomes visible and the
    owning :class:`WorkflowTUI` drives it with chunks from
    ``WorkflowService.read_logs`` on a poll timer.
    """

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
        tail = LogTail()
        tail.display = False
        yield tail

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
            self._set_tail_visible(False)
            return

        status_val = workflow.status.value
        emoji, label = STATUS_DISPLAY.get(status_val, ("  ", status_val))
        # See ``WorkflowList.update_workflows`` — strip the CLI-only trailing
        # space so the title aligns consistently across statuses.
        emoji = emoji.rstrip()
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
        self._set_tail_visible(workflow.status == WorkflowStatus.IN_PROGRESS)

    # ------------------------------------------------------------------
    # LogTail helpers (called by WorkflowTUI's tail timer)
    # ------------------------------------------------------------------

    def _get_tail(self) -> Optional[LogTail]:
        try:
            return self.query_one(LogTail)
        except Exception:
            return None

    def _set_tail_visible(self, visible: bool) -> None:
        tail = self._get_tail()
        if tail is None:
            return
        tail.display = visible
        if not visible:
            tail.clear()
            tail.set_paused(False)

    def append_log_chunk(self, stage: str, content: str) -> None:
        tail = self._get_tail()
        if tail is None:
            return
        tail.append_content(stage, content)

    def clear_log_tail(self) -> None:
        tail = self._get_tail()
        if tail is None:
            return
        tail.clear()

    def set_tail_paused(self, paused: bool) -> None:
        tail = self._get_tail()
        if tail is None:
            return
        tail.set_paused(paused)

    def is_tail_paused(self) -> bool:
        tail = self._get_tail()
        if tail is None:
            return False
        return tail.is_paused

    def is_tail_visible(self) -> bool:
        tail = self._get_tail()
        if tail is None:
            return False
        return bool(tail.display)


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
            "p:comment-pr  o:approve-pr  l:logs  d:clear-db  space:pause-tail  ↑↓:navigate"
        )
