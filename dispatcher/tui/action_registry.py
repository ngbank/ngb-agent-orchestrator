"""Per-action precondition registry for the TUI footer.

Each :class:`WorkflowAction` pairs a Textual key binding with a predicate
evaluated against the currently-selected ``WorkflowDetail``. The footer is
rendered by Textual's ``Footer`` widget, which honours ``App.check_action``
to hide bindings whose action is not currently applicable — see
``WorkflowTUI.check_action`` for the wiring.

Keeping the predicates here (instead of inside ``dispatcher/commands/*``)
avoids leaking TUI metadata into the CLI handler modules: handlers retain
their defence-in-depth status guards, while the TUI decides what to show.

Predicates are called on every footer repaint (each row-highlight change,
each periodic refresh). They must be cheap and side-effect free — consume
the already-fetched ``WorkflowDetail`` rather than making service calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from orchestrator.workflow_service import WorkflowDetail
from state.workflow_status import WorkflowStatus


@dataclass(frozen=True)
class WorkflowAction:
    """One entry in the TUI action registry.

    Attributes:
        key: Textual key binding (e.g. ``"a"``).
        action: Action name passed to ``App.BINDINGS`` and dispatched via
            ``action_<name>``. Must match the suffix of a ``WorkflowTUI.action_*``
            method.
        label: Footer label.
        applies: Predicate called with the currently-selected ``WorkflowDetail``
            (or ``None`` when no row is selected). Return ``True`` to show the
            binding in the footer, ``False`` to hide it.
    """

    key: str
    action: str
    label: str
    applies: Callable[[Optional[WorkflowDetail]], bool]


def _always(_detail: Optional[WorkflowDetail]) -> bool:
    """Predicate for actions that never depend on the selected row."""
    return True


def _on_detail(
    predicate: Callable[[WorkflowDetail], bool],
) -> Callable[[Optional[WorkflowDetail]], bool]:
    """Adapt a non-None predicate to the registry signature.

    Returns ``False`` when no row is selected, otherwise delegates to
    ``predicate``. Keeps the registry entries free of repetitive
    ``detail is not None and ...`` boilerplate.
    """

    def wrapped(detail: Optional[WorkflowDetail]) -> bool:
        return detail is not None and predicate(detail)

    return wrapped


# Order matters only for footer display order.
REGISTRY: tuple[WorkflowAction, ...] = (
    # Globally available (no workflow context required).
    WorkflowAction("r", "refresh", "Refresh", _always),
    WorkflowAction("n", "new_run", "New Run", _always),
    WorkflowAction("d", "clear_db", "Clear DB", _always),
    # Workflow-scoped — applicable when a row is selected and its state matches.
    WorkflowAction(
        "a",
        "approve",
        "Approve",
        _on_detail(lambda d: d.status == WorkflowStatus.PENDING_APPROVAL),
    ),
    WorkflowAction(
        "j",
        "reject",
        "Reject",
        _on_detail(lambda d: d.status == WorkflowStatus.PENDING_APPROVAL),
    ),
    WorkflowAction(
        "c",
        "clarify",
        "Clarify",
        # Clarify only makes sense when the WorkPlan actually contains
        # concerns to answer — _handle_clarify exits if concerns is empty,
        # so hiding the binding when there are none avoids dead-end UX.
        _on_detail(
            lambda d: d.status == WorkflowStatus.PENDING_WORKPLAN_CLARIFICATION
            and bool((d.work_plan or {}).get("concerns"))
        ),
    ),
    WorkflowAction(
        "y",
        "retry",
        "Retry",
        # Mirrors _handle_retry's guard: WorkflowStatus.is_retryable() is
        # the authoritative predicate (FAILED, IN_PROGRESS, APPROVED,
        # PR_COMMENTED).
        _on_detail(lambda d: d.status.is_retryable()),
    ),
    WorkflowAction(
        "x",
        "cancel",
        "Cancel",
        # Mirrors _handle_cancel's guard: only active (non-terminal)
        # workflows can be cancelled.
        _on_detail(lambda d: d.status.is_active()),
    ),
    WorkflowAction(
        "o",
        "approve_pr",
        "Approve PR",
        # Status alone is authoritative: matches the CLI guard in
        # dispatcher/commands/pr.py::_handle_approve_pr. The dedicated
        # ``pr_url`` column is not populated when persist_results writes
        # PENDING_PR_APPROVAL — the URL lives in execution_summary.pr_url —
        # so requiring ``d.pr_url`` here would falsely hide the action.
        _on_detail(lambda d: d.status == WorkflowStatus.PENDING_PR_APPROVAL),
    ),
    WorkflowAction(
        "p",
        "comment_pr",
        "Comment PR",
        _on_detail(lambda d: d.status == WorkflowStatus.PENDING_PR_APPROVAL),
    ),
    WorkflowAction(
        "k",
        "reject_pr",
        "Reject PR",
        # ``k`` chosen to avoid collisions with ``j`` (reject), ``l`` (logs),
        # and the global keys. Mirrors _handle_reject_pr's status guard.
        _on_detail(lambda d: d.status == WorkflowStatus.PENDING_PR_APPROVAL),
    ),
    WorkflowAction(
        "l",
        "logs",
        "Logs",
        # Any selected workflow has captured logs to display, even completed
        # ones — _handle_logs reads from disk regardless of current status.
        _on_detail(lambda _d: True),
    ),
)


def action_for(name: str) -> Optional[WorkflowAction]:
    """Return the registry entry for ``action`` name, or ``None`` if unknown.

    Used by ``WorkflowTUI.check_action`` to decide footer visibility for the
    given Textual action name.
    """
    for entry in REGISTRY:
        if entry.action == name:
            return entry
    return None
