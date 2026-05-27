"""Workflow status enum for type-safe status management."""

from enum import Enum


class WorkflowStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    PENDING_WORKPLAN_CLARIFICATION = "pending_workplan_clarification"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    def is_active(self) -> bool:
        """Return True if the workflow is still in-flight."""
        return self in (
            WorkflowStatus.PENDING,
            WorkflowStatus.IN_PROGRESS,
            WorkflowStatus.PENDING_WORKPLAN_CLARIFICATION,
            WorkflowStatus.PENDING_APPROVAL,
            WorkflowStatus.APPROVED,
        )

    def is_terminal(self) -> bool:
        """Return True if the workflow has reached a final state."""
        return self in (
            WorkflowStatus.COMPLETED,
            WorkflowStatus.FAILED,
            WorkflowStatus.REJECTED,
            WorkflowStatus.CANCELLED,
        )

    def is_retryable(self) -> bool:
        """Return True if a workflow in this status can be resumed via --retry.

        FAILED is the canonical retryable terminal state: the graph stopped at
        a node that raised or set an error, and a retry resumes from that node.
        """
        return self == WorkflowStatus.FAILED
