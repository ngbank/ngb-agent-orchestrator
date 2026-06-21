"""WorkflowService Protocol — the single contract every caller depends on.

The dispatcher CLI, the TUI, and future HTTP/UI clients all program against
this Protocol rather than reaching into ``state``, ``orchestrator.builder``, or
log-path helpers directly.  ``LocalWorkflowService`` provides the default
in-process implementation; a future ``HttpWorkflowService`` will satisfy the
same interface for talking to a remote orchestrator server.

Design rules:

* Methods take primitive inputs (workflow_id, ticket_key, etc.) and small
  request DTOs — no langgraph types leak across the boundary.
* Methods return frozen DTOs from :mod:`orchestrator.workflow_service.dtos`.
* Graph-running methods return ``WorkflowRunResult`` and never print, post to
  JIRA, or catch ``KeyboardInterrupt`` (callers handle UX concerns).
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Protocol, runtime_checkable

from state.workflow_status import WorkflowStatus

from .dtos import (
    WorkflowAuditEntry,
    WorkflowDetail,
    WorkflowEvent,
    WorkflowHistoryEntry,
    WorkflowLogChunk,
    WorkflowRunResult,
    WorkflowStartRequest,
    WorkflowSummary,
)


@runtime_checkable
class WorkflowService(Protocol):
    """Single workflow contract used by every caller."""

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def get(self, workflow_id: str) -> Optional[WorkflowDetail]:
        """Return the full workflow record, or ``None`` if not found."""
        ...

    def get_by_ticket(self, ticket_key: str) -> List[WorkflowSummary]:
        """Return all workflows for ``ticket_key``, newest first."""
        ...

    def get_latest_retryable_by_ticket(self, ticket_key: str) -> Optional[WorkflowSummary]:
        """Return the most recent retryable workflow for ``ticket_key``, or ``None``."""
        ...

    def list(
        self,
        ticket_key: Optional[str] = None,
        status: Optional[WorkflowStatus] = None,
        limit: int = 50,
    ) -> List[WorkflowSummary]:
        """List workflows, optionally filtered by ticket and/or status."""
        ...

    def get_history(self, workflow_id: str) -> List[WorkflowHistoryEntry]:
        """Return the node traversal history for a workflow, oldest first."""
        ...

    def get_audit_log(self, workflow_id: str) -> List[WorkflowAuditEntry]:
        """Return the audit log entries for a workflow, oldest first."""
        ...

    def read_logs(
        self,
        workflow_id: str,
        stage: Optional[str] = None,
        after_offset: int = 0,
    ) -> List[WorkflowLogChunk]:
        """Read captured stage logs from disk.

        When ``stage`` is ``None`` returns every stage that has a log file
        (typically ``"plan"`` and ``"execute"``).  Stages with no log file on
        disk are omitted from the result rather than raising.

        ``after_offset`` (bytes) skips already-delivered content from the
        **start** of each returned stage's log file.  It applies to every
        stage uniformly — callers that need per-stage resume should pass a
        single ``stage`` plus the corresponding offset.  Each returned chunk
        carries its starting ``offset`` so the caller can advance reliably.
        """
        ...

    def stream_events(
        self,
        workflow_id: str,
        after_seq: int = 0,
    ) -> Iterable[WorkflowEvent]:
        """Yield workflow events derived from the graph state history.

        Replay-only for now: returns every recorded event with ``seq > after_seq``
        in chronological order, then stops.  A future live-stream variant will
        also yield events as they happen (Stage C).
        """
        ...

    # ------------------------------------------------------------------
    # Admin / status mutations
    # ------------------------------------------------------------------

    def cancel(
        self,
        workflow_id: str,
        reason: Optional[str] = None,
        actor: str = "system",
    ) -> None:
        """Mark an active workflow as CANCELLED."""
        ...

    def mark_interrupted(
        self,
        workflow_id: str,
        failed_node: Optional[str] = None,
        actor: str = "system",
    ) -> None:
        """Mark an in-flight workflow as FAILED after a KeyboardInterrupt.

        Best-effort: no-op if the workflow is already terminal.  Records
        ``failed_node`` so ``retry`` can resume from the right place.
        """
        ...

    def clear_db(self) -> tuple[int, int]:
        """Wipe all workflows and LangGraph checkpoints.  Returns (workflows, checkpoints)."""
        ...

    # ------------------------------------------------------------------
    # Graph-running operations
    # ------------------------------------------------------------------

    def start(self, request: WorkflowStartRequest) -> WorkflowRunResult:
        """Start a new workflow from a ticket key and run the graph to completion or pause."""
        ...

    def approve_plan(self, workflow_id: str) -> WorkflowRunResult:
        """Resume a workflow paused at await_approval with an approved decision."""
        ...

    def reject_plan(self, workflow_id: str, reason: Optional[str]) -> WorkflowRunResult:
        """Resume a workflow paused at await_approval with a rejected decision."""
        ...

    def submit_clarification(
        self,
        workflow_id: str,
        answers: List[Dict[str, str]],
    ) -> WorkflowRunResult:
        """Resume a workflow paused at await_workplan_clarification with answers."""
        ...

    def retry(self, workflow_id: str) -> WorkflowRunResult:
        """Rewind a failed / interrupted workflow to its failed_node and re-run."""
        ...

    def approve_pr(self, workflow_id: str) -> WorkflowRunResult:
        """Resume a workflow paused at await_pr_approval with an approved decision."""
        ...

    def comment_pr(self, workflow_id: str, comments: str) -> WorkflowRunResult:
        """Resume a workflow paused at await_pr_approval with review comments."""
        ...

    def reject_pr(self, workflow_id: str, reason: Optional[str]) -> WorkflowRunResult:
        """Resume a workflow paused at await_pr_approval with a rejected decision."""
        ...
