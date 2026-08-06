"""Map internal WorkflowStatus values to the wire-format (eventType, status) pair.

The orchestrator's WorkflowStatus enum must never cross the publish boundary
unchanged — this module is the single source of truth for that translation.
"""

from __future__ import annotations

from typing import Optional, Tuple

from state.workflow_status import WorkflowStatus

# (eventType, status) — status is None for events that carry no execution status
_STATUS_MAP: dict[str, Tuple[str, Optional[str]]] = {
    WorkflowStatus.IN_PROGRESS.value: ("execution.started", "RUNNING"),
    WorkflowStatus.PENDING_APPROVAL.value: ("approval.pending", "PENDING_APPROVAL"),
    WorkflowStatus.PENDING_PR_APPROVAL.value: ("pr_approval.pending", "PENDING_PR_APPROVAL"),
    WorkflowStatus.PENDING_WORKPLAN_CLARIFICATION.value: (
        "workplan_clarification.pending",
        "PENDING_WORKPLAN_CLARIFICATION",
    ),
    WorkflowStatus.COMPLETED.value: ("execution.completed", "SUCCEEDED"),
    WorkflowStatus.FAILED.value: ("execution.failed", "FAILED"),
    WorkflowStatus.CANCELLED.value: ("execution.failed", "CANCELLED"),
    WorkflowStatus.REJECTED.value: ("execution.failed", "FAILED"),
}


def map_status(status_value: str) -> Tuple[str, Optional[str]]:
    if status_value not in _STATUS_MAP:
        raise ValueError(f"No event mapping for WorkflowStatus '{status_value}'")
    return _STATUS_MAP[status_value]
