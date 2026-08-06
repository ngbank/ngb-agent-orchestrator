"""Public API for the event_publisher module."""

from __future__ import annotations

from typing import Any, Optional

from orchestrator.event_publisher.events import ExecutionStatusEvent
from orchestrator.event_publisher.mapper import map_status, plan_generated_event_type
from orchestrator.event_publisher.publisher import get_publisher

__all__ = [
    "ExecutionStatusEvent",
    "get_publisher",
    "publish_status_event",
    "publish_plan_generated_event",
]


def publish_status_event(
    *,
    workflow_id: str,
    status_value: str,
    pr_url: Optional[str] = None,
    error_message: Optional[str] = None,
    ticket_id: Optional[str] = None,
) -> None:
    """Build an ExecutionStatusEvent from a WorkflowStatus and publish it.

    Uses workflow_id as executionId (1:1 mapping until FleetOps-supplied
    execution IDs are threaded through the dispatch path).
    """
    try:
        event_type, status = map_status(status_value)
    except ValueError:
        return  # unmapped status (e.g. APPROVED, PR_COMMENTED) — no event

    event = ExecutionStatusEvent(
        execution_id=workflow_id,
        event_type=event_type,
        orchestrator_workflow_id=workflow_id,
        status=status,
        pr_url=pr_url or None,
        error_message=error_message or None,
        ticket_id=ticket_id or None,
    )
    get_publisher().publish(event)


def publish_plan_generated_event(
    *,
    workflow_id: str,
    ticket_id: Optional[str] = None,
    work_plan: Optional[Any] = None,
) -> None:
    """Publish a plan.generated event carrying the work-plan payload."""
    event = ExecutionStatusEvent(
        execution_id=workflow_id,
        event_type=plan_generated_event_type(),
        orchestrator_workflow_id=workflow_id,
        status=None,
        ticket_id=ticket_id or None,
        work_plan=work_plan,
    )
    get_publisher().publish(event)
