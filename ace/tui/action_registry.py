"""Per-action precondition registry for the ACE TUI footer.

Each :class:`AceAction` pairs a Textual key binding with a predicate
evaluated against the currently-selected :class:`~ace.service.dtos.ItemSummaryDTO`.
The footer is rendered by Textual's ``Footer`` widget, which honours
``App.check_action`` to hide bindings whose action is not currently applicable —
see ``AceTUI.check_action`` for the wiring.

Keeping the predicates here (instead of inside command handlers) avoids
leaking TUI metadata into other modules: handlers retain their status
guards, while the TUI decides what to show.

Predicates are called on every footer repaint. They must be cheap and
side-effect free — consume the already-fetched ``ItemSummaryDTO`` rather
than making service calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from ace.service.dtos import ItemSummaryDTO


@dataclass(frozen=True)
class AceAction:
    """One entry in the ACE TUI action registry.

    Attributes:
        key: Textual key binding (e.g. ``"r"``).
        action: Action name passed to ``App.BINDINGS`` and dispatched via
            ``action_<name>``. Must match the suffix of an ``AceTUI.action_*``
            method.
        label: Footer label.
        applies: Predicate called with the currently-selected
            ``ItemSummaryDTO`` (or ``None`` when no row is selected). Return
            ``True`` to show the binding in the footer, ``False`` to hide it.
    """

    key: str
    action: str
    label: str
    applies: Callable[[Optional[ItemSummaryDTO]], bool]


def _always(_item: Optional[ItemSummaryDTO]) -> bool:
    """Predicate for actions that never depend on the selected row."""
    return True


def _on_item(
    predicate: Callable[[ItemSummaryDTO], bool],
) -> Callable[[Optional[ItemSummaryDTO]], bool]:
    """Adapt a non-None predicate to the registry signature.

    Returns ``False`` when no row is selected, otherwise delegates to
    ``predicate``. Keeps the registry entries free of repetitive
    ``item is not None and ...`` boilerplate.
    """

    def wrapped(item: Optional[ItemSummaryDTO]) -> bool:
        return item is not None and predicate(item)

    return wrapped


# Order matters only for footer display order.
REGISTRY: tuple[AceAction, ...] = (
    # Globally available (no item context required).
    AceAction("r", "refresh", "Refresh", _always),
    AceAction("1", "sort_confidence", "Sort:Confidence", _always),
    AceAction("2", "sort_age", "Sort:Age", _always),
    AceAction("3", "sort_pattern_type", "Sort:Pattern", _always),
    # Item-scoped — applicable when a row is selected.
    # Promote and reject land in ticket 3.5; the entries are registered here
    # so the registry is the single place to add bindings and the app's
    # BINDINGS list stays in sync automatically.
    AceAction(
        "p",
        "promote",
        "Promote",
        _on_item(lambda i: i.status == "staged"),
    ),
    AceAction(
        "x",
        "reject",
        "Reject",
        _on_item(lambda i: i.status == "staged"),
    ),
)


def action_for(action: str) -> Optional[AceAction]:
    """Return the registry entry for *action*, or ``None`` if not found."""
    for entry in REGISTRY:
        if entry.action == action:
            return entry
    return None
