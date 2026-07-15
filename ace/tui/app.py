"""Main Textual App for the ACE TUI.

Mirrors the structure of ``dispatcher/tui/app.py``:

* An :class:`AgentContextEngineService` instance is built in
  :func:`run_tui` via the environment-driven factory and injected into the
  app constructor.  Screens, widgets, and actions consume only the service
  — never the repository or DB directly.  This keeps the TUI compatible
  with a future ``RemoteAgentContextEngineService`` (Epic 9) without
  modification.

* Actions are dispatched via ``action_<name>`` methods; the footer is
  shaped per-row by ``check_action`` consulting the REGISTRY predicates.

* The staging queue auto-refreshes on a configurable poll interval
  (``ACE_TUI_POLL`` env var, default 5 s; set to 0 to disable).

* Sort order (confidence / age / pattern_type) is controlled by the 1/2/3
  key bindings and persists across refreshes.

* Promote (``p``) and reject (``x``) push ACE-specific modals that collect
  review notes and an optional scope override, then call the service in a
  worker thread so the UI stays responsive.
"""

from __future__ import annotations

import os
from typing import Callable, Optional

from dotenv import load_dotenv
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.timer import Timer
from textual.widgets import Footer, Header

from ace.service.dtos import ListItemsRequest, ShowItemRequest
from ace.service.factory import build_agent_context_engine_service_from_env
from ace.service.protocols import AgentContextEngineService
from ace.tui.action_registry import REGISTRY, action_for
from ace.tui.actions import ActionError, promote_item, reject_item
from ace.tui.modals import PromoteFormData, PromoteModal, RejectModal
from ace.tui.widgets import ItemDetailPane, StagingQueueList


class AceTUI(App[None]):
    """Textual TUI for keyboard-driven ACE staging queue review."""

    CSS = """
    Screen {
        layout: vertical;
    }
    #main {
        height: 1fr;
        width: 100%;
    }
    StagingQueueList {
        height: 100%;
        width: 55%;
    }
    """

    # BINDINGS is assembled from the action registry. The footer is then
    # narrowed per row by check_action, so adding or removing actions
    # touches the registry only — no edits needed here.
    BINDINGS = [
        ("q", "quit", "Quit"),
        *[(a.key, a.action, a.label) for a in REGISTRY],
    ]

    def __init__(self, service: AgentContextEngineService) -> None:
        super().__init__()
        self._service = service
        self._refresh_timer: Optional[Timer] = None
        self._poll_interval = float(os.environ.get("ACE_TUI_POLL", "5"))
        self._sort_key: str = "confidence"

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main"):
            yield StagingQueueList()
            yield ItemDetailPane()
        yield Footer()

    def on_mount(self) -> None:
        self.title = "ACE TUI"
        self.sub_title = "Staging Queue"
        self._refresh_queue()
        if self._poll_interval > 0:
            self._refresh_timer = self.set_interval(self._poll_interval, self._refresh_queue)

    def _refresh_queue(self) -> None:
        try:
            result = self._service.list_items(ListItemsRequest(status="staged"))
            items = list(result.items)
        except Exception:
            items = []
        self.query_one(StagingQueueList).update_items(items, sort_key=self._sort_key)
        self._sync_detail_pane()

    def _sync_detail_pane(self) -> None:
        """Fetch full detail for the selected item and update the detail pane."""
        selected = self.query_one(StagingQueueList).get_selected_item()
        detail_result = None
        if selected is not None:
            try:
                detail_result = self._service.show_item(ShowItemRequest(item_id=selected.id))
            except Exception:
                detail_result = None
        try:
            self.query_one(ItemDetailPane).update_item(detail_result)
        except Exception:
            pass

    def _notify(self, message: str, severity: str = "information") -> None:
        self.notify(message, severity=severity, timeout=4)  # pyright: ignore[reportArgumentType]

    # ------------------------------------------------------------------
    # Worker thread dispatcher (mirrors dispatcher/tui/app.py pattern)
    # ------------------------------------------------------------------

    def _run_action_async(
        self,
        label: str,
        fn: Callable[[], str],
        *,
        refresh_after: bool = True,
    ) -> None:
        """Run a blocking service call in a Textual worker thread.

        *label* is shown in an immediate "…" notification so the user knows
        the action was accepted.  *fn* runs off-thread; its return value is
        surfaced as an info notification, :class:`ActionError` as an error
        notification.  The queue is refreshed on completion unless
        *refresh_after* is ``False``.
        """
        self._notify(f"{label}\u2026", "information")

        def worker() -> None:
            try:
                msg = fn()
            except ActionError as exc:
                self.call_from_thread(self._notify, str(exc), "error")
            except Exception as exc:
                self.call_from_thread(self._notify, f"{label} failed: {exc}", "error")
            else:
                self.call_from_thread(self._notify, msg, "information")
            finally:
                if refresh_after:
                    self.call_from_thread(self._refresh_queue)

        self.run_worker(
            worker,
            thread=True,
            group="actions",
            exit_on_error=False,
            description=label,
        )

    def check_action(self, action: str, parameters: tuple[object, ...]) -> Optional[bool]:
        """Hide footer bindings whose registry predicate rejects the row."""
        entry = action_for(action)
        if entry is None:
            return True
        selected = self.query_one(StagingQueueList).get_selected_item()
        return entry.applies(selected)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def on_data_table_row_highlighted(self, _event: object) -> None:
        """Sync the detail pane whenever the staging queue cursor moves."""
        self._sync_detail_pane()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_refresh(self) -> None:
        self._refresh_queue()

    def action_sort_confidence(self) -> None:
        self._sort_key = "confidence"
        self._refresh_queue()
        self._notify("Sorted by confidence (highest first)")

    def action_sort_age(self) -> None:
        self._sort_key = "age"
        self._refresh_queue()
        self._notify("Sorted by age (oldest first)")

    def action_sort_pattern_type(self) -> None:
        self._sort_key = "pattern_type"
        self._refresh_queue()
        self._notify("Sorted by pattern type")

    def action_promote(self) -> None:
        """Push the :class:`PromoteModal`, then promote via service in a worker thread."""
        item = self.query_one(StagingQueueList).get_selected_item()
        if item is None:
            self._notify("No item selected.", "warning")
            return

        def on_result(result: PromoteFormData | None) -> None:
            if result is None:
                return  # user cancelled
            self._run_action_async(
                f"Promoting {item.id[:8]}\u2026",
                lambda: promote_item(
                    self._service,
                    item.id,
                    notes=result.notes,
                    scope=result.scope,
                    scope_value=result.scope_value,
                ),
            )

        self.push_screen(
            PromoteModal(
                current_scope=item.scope or "",
                current_scope_value=item.scope_value or "",
            ),
            on_result,
        )

    def action_reject(self) -> None:
        """Push the :class:`RejectModal`, then reject via service in a worker thread."""
        item = self.query_one(StagingQueueList).get_selected_item()
        if item is None:
            self._notify("No item selected.", "warning")
            return

        def on_notes(notes: str | None) -> None:
            if notes is None:
                return  # user cancelled
            self._run_action_async(
                f"Rejecting {item.id[:8]}\u2026",
                lambda: reject_item(
                    self._service,
                    item.id,
                    notes=notes or None,
                ),
            )

        self.push_screen(RejectModal(), on_notes)


def run_tui() -> None:
    """Entry point for the ``ace-tui`` command.

    Loads ``.env``, builds the :class:`AgentContextEngineService` from the
    environment, and runs the :class:`AceTUI` app.
    """
    load_dotenv()
    service = build_agent_context_engine_service_from_env()
    AceTUI(service).run()
