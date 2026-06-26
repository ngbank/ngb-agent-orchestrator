"""Modal screens for free-text input and confirmation dialogs."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static


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
        # The buttons \u2014 not the contents of the text field \u2014 decide
        # whether the action runs. ``Submit`` always returns the raw value
        # (caller validates if it needs to); ``Cancel`` aborts with ``None``.
        if event.button.id == "submit":
            self.dismiss(self.query_one("#input_field", Input).value)
        else:
            self.dismiss(None)

    def on_input_submitted(self) -> None:
        # Pressing Enter inside the input is equivalent to clicking Submit.
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
