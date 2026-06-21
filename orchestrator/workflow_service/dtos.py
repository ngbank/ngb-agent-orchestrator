"""Transport-agnostic DTOs exchanged across the WorkflowService boundary.

All dataclasses are frozen so they can be shared safely across threads, cached,
and compared by value in tests.  They intentionally avoid any FastAPI / Pydantic
types so they can be reused unchanged by a future HTTP transport.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from state.workflow_status import WorkflowStatus


@dataclass(frozen=True)
class StartRequest:
    """Inputs for ``WorkflowService.start``."""

    ticket_key: str
    dry_run: bool = False
    workflow_id: Optional[str] = None  # if None, the service generates a UUID


@dataclass(frozen=True)
class WorkflowSummary:
    """Lightweight view of a workflow row — what ``list`` and ``get_by_ticket`` return."""

    id: str
    ticket_key: str
    status: WorkflowStatus
    created_at: str
    updated_at: str
    pr_url: Optional[str] = None


@dataclass(frozen=True)
class WorkflowDetail:
    """Full workflow record — what ``get`` returns."""

    id: str
    ticket_key: str
    status: WorkflowStatus
    created_at: str
    updated_at: str
    pr_url: Optional[str] = None
    work_plan: Optional[Dict[str, Any]] = None
    execution_summary: Optional[Dict[str, Any]] = None
    clarification_history: List[Dict[str, Any]] = field(default_factory=list)
    pr_comments: Optional[str] = None
    usage_summary: Dict[str, Any] = field(default_factory=dict)
    retry_count: int = 0


@dataclass(frozen=True)
class LogChunk:
    """One stage's captured log output."""

    workflow_id: str
    stage: str  # e.g. "plan", "execute"
    path: str  # absolute path on disk (informational)
    content: str  # full log contents


@dataclass(frozen=True)
class HistoryEntry:
    """One step in a workflow's node traversal history."""

    step: int
    node: str
    outcome: str  # "ok" | "error" | "interrupted"
    result_keys: List[str] = field(default_factory=list)
    error: Optional[str] = None


@dataclass(frozen=True)
class AuditEntry:
    """One row from the audit log for a workflow."""

    workflow_id: str
    actor: str
    action: str
    timestamp: str
    details: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class WorkflowEvent:
    """A workflow execution event derived from langgraph's state history.

    Used by ``stream_events`` to expose a transport-agnostic view of graph
    progress.  ``seq`` is a monotonically increasing index within the
    workflow's history so reconnecting clients can resume from a known point.
    """

    workflow_id: str
    seq: int
    kind: str  # "node_start" | "node_end" | "interrupt" | "completed" | "failed"
    node: Optional[str] = None
    data: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class WorkflowRunResult:
    """Outcome of a graph-running operation (start / approve / reject / retry / clarify / pr-*).

    ``final_status`` is the workflow's status after the operation completed
    (the row in the workflow table).  ``interrupted`` is True when the graph
    paused at an ``interrupt()`` call (e.g. await_approval).  ``error`` is set
    when the operation failed.

    ``execution_summary`` and ``pr_url`` are surfaced separately so callers
    (CLI / future HTTP layer) can render or forward them without re-reading
    the workflow record.
    """

    workflow_id: str
    ticket_key: Optional[str]
    final_status: WorkflowStatus
    interrupted: bool = False
    error: Optional[str] = None
    execution_summary: Optional[Dict[str, Any]] = None
    pr_url: Optional[str] = None
    failed_node: Optional[str] = None
    final_state: Optional[Dict[str, Any]] = None
