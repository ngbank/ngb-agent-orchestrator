"""Workflow status enum for type-safe status management."""

from enum import Enum


class WorkflowStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    PENDING_WORKPLAN_CLARIFICATION = "pending_workplan_clarification"
    PENDING_APPROVAL = "pending_approval"
    PENDING_PR_APPROVAL = "pending_pr_approval"
    PR_COMMENTED = "pr_commented"
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
            WorkflowStatus.PENDING_PR_APPROVAL,
            WorkflowStatus.PR_COMMENTED,
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

        IN_PROGRESS is also retryable as a safety net for workflows that were
        interrupted (Ctrl-C, SIGKILL, terminal close, OOM, etc.) and left
        stuck. A dispatcher run has at most one workflow active at a time, so
        any IN_PROGRESS workflow not currently executing is effectively dead
        and should be resumable.
        """
        return self in (
            WorkflowStatus.FAILED,
            WorkflowStatus.IN_PROGRESS,
            WorkflowStatus.PR_COMMENTED,
        )
