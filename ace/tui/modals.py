"""Modal screens for the ACE TUI.

Shared primitives (``InputModal``, ``ConfirmModal``) mirror
``dispatcher/tui/modals.py``.  ``PromoteModal`` and ``RejectModal`` are
ACE-specific and collect the inputs needed by
:meth:`~ace.service.protocols.AgentContextEngineService.promote` and
:meth:`~ace.service.protocols.AgentContextEngineService.reject`.
"""

from __future__ import annotations

from typing import NamedTuple, Optional

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static


class InputModal(ModalScreen[str | None]):
    """Modal screen that collects free-text input from the user."""

    DEFAULT_CSS = """
    InputModal {
        align: center middle;
    }
    InputModal > Vertical {
        width: 60;
        height: auto;
        border: thick $background 80%;
        background: $surface;
        padding: 1 2;
    }
    InputModal > Vertical > Static {
        height: auto;
        margin-bottom: 1;
    }
    InputModal > Vertical > Input {
        margin-bottom: 1;
    }
    InputModal > Vertical > Horizontal {
        height: auto;
        align: center middle;
    }
    InputModal > Vertical > Horizontal > Button {
        margin: 0 1;
    }
    """

    def __init__(self, title: str, placeholder: str = "") -> None:
        super().__init__()
        self.title = title
        self.placeholder = placeholder

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self.title, id="input_title")  # pyright: ignore[reportArgumentType]
            yield Input(placeholder=self.placeholder, id="input_field")
            with Horizontal():
                yield Button("Submit", variant="primary", id="submit")
                yield Button("Cancel", variant="default", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "submit":
            self.dismiss(self.query_one("#input_field", Input).value)
        else:
            self.dismiss(None)

    def on_input_submitted(self) -> None:
        self.dismiss(self.query_one("#input_field", Input).value)


class ConfirmModal(ModalScreen[bool]):
    """Modal screen that asks for a yes/no confirmation."""

    DEFAULT_CSS = """
    ConfirmModal {
        align: center middle;
    }
    ConfirmModal > Vertical {
        width: 50;
        height: auto;
        border: thick $background 80%;
        background: $surface;
        padding: 1 2;
    }
    ConfirmModal > Vertical > Static {
        height: auto;
        margin-bottom: 1;
    }
    ConfirmModal > Vertical > Horizontal {
        height: auto;
        align: center middle;
    }
    ConfirmModal > Vertical > Horizontal > Button {
        margin: 0 1;
    }
    """

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self.message, id="confirm_message")
            with Horizontal():
                yield Button("Yes", variant="error", id="yes")
                yield Button("No", variant="primary", id="no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")


# ---------------------------------------------------------------------------
# ACE-specific modals
# ---------------------------------------------------------------------------


class PromoteFormData(NamedTuple):
    """Data returned by :class:`PromoteModal` on submit.

    Fields map directly to :class:`~ace.service.dtos.PromoteRequest`:

    * *notes* — reviewer annotation (stored in ``review_notes``).
    * *scope* — optional scope override; ``None`` keeps the staged value.
    * *scope_value* — optional scope-value override; ``None`` keeps the staged value.
    """

    notes: Optional[str]
    scope: Optional[str]
    scope_value: Optional[str]


class PromoteModal(ModalScreen["PromoteFormData | None"]):
    """Collect promote notes and an optional scope override.

    Pre-fills *scope* and *scope_value* with the item's current staged values
    so the reviewer can confirm or narrow them.  Leaving either field blank
    keeps the staged value (passed as ``None`` to ``PromoteRequest``).
    """

    DEFAULT_CSS = """
    PromoteModal {
        align: center middle;
    }
    PromoteModal > Vertical {
        width: 70;
        height: auto;
        border: thick $background 80%;
        background: $surface;
        padding: 1 2;
    }
    PromoteModal > Vertical > Label {
        height: auto;
        margin-bottom: 1;
        color: $text-muted;
    }
    PromoteModal > Vertical > Input {
        margin-bottom: 1;
    }
    PromoteModal > Vertical > Horizontal {
        height: auto;
        align: center middle;
    }
    PromoteModal > Vertical > Horizontal > Button {
        margin: 0 1;
    }
    """

    def __init__(
        self,
        current_scope: str = "",
        current_scope_value: str = "",
    ) -> None:
        super().__init__()
        self._current_scope = current_scope
        self._current_scope_value = current_scope_value

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(
                "[bold green]Promote item[/bold green]", id="modal_title"
            )  # pyright: ignore[reportArgumentType]
            yield Label("Notes (optional — stored as review annotation):")
            yield Input(placeholder="Review notes...", id="notes_field")
            yield Label(
                f"Scope override (current: [cyan]{self._current_scope or '—'}[/cyan]  "
                "— leave blank to keep):"
            )
            yield Input(
                value=self._current_scope,
                placeholder="task_type / file_pattern / codebase_wide",
                id="scope_field",
            )
            yield Label(
                f"Scope value override (current: [cyan]{self._current_scope_value or '—'}[/cyan]"
                "  — leave blank to keep):"
            )
            yield Input(
                value=self._current_scope_value,
                placeholder="e.g. migration, *.py",
                id="scope_value_field",
            )
            with Horizontal():
                yield Button("Promote", variant="success", id="promote")
                yield Button("Cancel", variant="default", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        notes_val = self.query_one("#notes_field", Input).value.strip() or None
        scope_raw = self.query_one("#scope_field", Input).value.strip()
        scope_val_raw = self.query_one("#scope_value_field", Input).value.strip()
        scope = scope_raw or None
        scope_value = scope_val_raw or None
        self.dismiss(PromoteFormData(notes=notes_val, scope=scope, scope_value=scope_value))


class RejectModal(ModalScreen["str | None"]):
    """Collect optional reject notes.

    Returns the notes string (possibly empty) on submit, or ``None`` when the
    user cancels.  An empty string means "no notes" — the caller should pass
    ``None`` to ``RejectRequest.notes`` in that case.
    """

    DEFAULT_CSS = """
    RejectModal {
        align: center middle;
    }
    RejectModal > Vertical {
        width: 60;
        height: auto;
        border: thick $background 80%;
        background: $surface;
        padding: 1 2;
    }
    RejectModal > Vertical > Static {
        height: auto;
        margin-bottom: 1;
    }
    RejectModal > Vertical > Input {
        margin-bottom: 1;
    }
    RejectModal > Vertical > Horizontal {
        height: auto;
        align: center middle;
    }
    RejectModal > Vertical > Horizontal > Button {
        margin: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(
                "[bold red]Reject item[/bold red]", id="modal_title"
            )  # pyright: ignore[reportArgumentType]
            yield Static("Notes (optional — stored as review annotation):")
            yield Input(placeholder="Rejection reason...", id="notes_field")
            with Horizontal():
                yield Button("Reject", variant="error", id="reject")
                yield Button("Cancel", variant="default", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        self.dismiss(self.query_one("#notes_field", Input).value)

    def on_input_submitted(self) -> None:
        self.dismiss(self.query_one("#notes_field", Input).value)
