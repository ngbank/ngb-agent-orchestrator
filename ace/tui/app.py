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
"""

from __future__ import annotations

import os
from typing import Optional

from dotenv import load_dotenv
from textual.app import App, ComposeResult
from textual.timer import Timer
from textual.widgets import Footer, Header

from ace.service.dtos import ListItemsRequest
from ace.service.factory import build_agent_context_engine_service_from_env
from ace.service.protocols import AgentContextEngineService
from ace.tui.action_registry import REGISTRY, action_for
from ace.tui.widgets import StagingQueueList


class AceTUI(App[None]):
    """Textual TUI for keyboard-driven ACE staging queue review."""

    CSS = """
    Screen {
        layout: vertical;
    }
    StagingQueueList {
        height: 1fr;
        width: 100%;
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
        yield StagingQueueList()
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

    def _notify(self, message: str, severity: str = "information") -> None:
        self.notify(message, severity=severity, timeout=4)  # pyright: ignore[reportArgumentType]

    def check_action(self, action: str, parameters: tuple[object, ...]) -> Optional[bool]:
        """Hide footer bindings whose registry predicate rejects the row."""
        entry = action_for(action)
        if entry is None:
            return True
        selected = self.query_one(StagingQueueList).get_selected_item()
        return entry.applies(selected)

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
        """Promote the selected staged item.  Full implementation in ticket 3.5."""
        self._notify("Promote: not yet implemented (ticket 3.5)", "warning")

    def action_reject(self) -> None:
        """Reject the selected staged item.  Full implementation in ticket 3.5."""
        self._notify("Reject: not yet implemented (ticket 3.5)", "warning")


def run_tui() -> None:
    """Entry point for the ``ace-tui`` command.

    Loads ``.env``, builds the :class:`AgentContextEngineService` from the
    environment, and runs the :class:`AceTUI` app.
    """
    load_dotenv()
    service = build_agent_context_engine_service_from_env()
    AceTUI(service).run()
