"""Workflow status enum for type-safe status management."""

from enum import Enum


class WorkflowStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"

    def is_active(self) -> bool:
        """Return True if the workflow is still in-flight."""
        return self in (WorkflowStatus.PENDING, WorkflowStatus.IN_PROGRESS)

    def is_terminal(self) -> bool:
        """Return True if the workflow has reached a final state."""
        return self in (WorkflowStatus.COMPLETED, WorkflowStatus.FAILED)
