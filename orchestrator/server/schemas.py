"""Pydantic v2 schemas for the orchestrator HTTP API.

Schemas mirror the transport-agnostic DTOs in
:mod:`orchestrator.workflow_service.dtos` but live on the HTTP boundary so
the DTOs themselves never grow a Pydantic dependency.  Each response model
has a ``from_dto`` classmethod for one-line conversion in route handlers.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from orchestrator.workflow_service.dtos import (
    WorkflowAuditEntry,
    WorkflowDetail,
    WorkflowHistoryEntry,
    WorkflowRunResult,
    WorkflowStartRequest,
    WorkflowSummary,
)
from state.workflow_status import WorkflowStatus


class StartWorkflowRequest(BaseModel):
    """Request body for ``POST /workflows``."""

    model_config = ConfigDict(extra="forbid")

    ticket_key: str = Field(..., min_length=1, description="JIRA ticket key, e.g. 'AOS-141'.")
    dry_run: bool = Field(
        False, description="When True, validate inputs without writing to the DB."
    )
    workflow_id: Optional[str] = Field(
        None,
        description="Optional caller-supplied workflow id; the server generates one when omitted.",
    )

    def to_dto(self) -> WorkflowStartRequest:
        return WorkflowStartRequest(
            ticket_key=self.ticket_key,
            dry_run=self.dry_run,
            workflow_id=self.workflow_id,
        )


class CancelWorkflowRequest(BaseModel):
    """Request body for ``POST /workflows/{id}/cancel``."""

    model_config = ConfigDict(extra="forbid")

    reason: Optional[str] = Field(None, description="Human-readable cancellation reason.")
    actor: str = Field("api", description="Actor recorded in the audit log.")


class RejectPlanRequest(BaseModel):
    """Request body for ``POST /workflows/{id}/reject-plan``."""

    model_config = ConfigDict(extra="forbid")

    reason: Optional[str] = Field(None, description="Reason for rejecting the WorkPlan.")


class ClarificationAnswer(BaseModel):
    """One concern/answer pair in a clarification payload."""

    model_config = ConfigDict(extra="forbid")

    concern: str = Field(..., min_length=1, description="The original concern text.")
    answer: str = Field(..., description="Dispatcher-supplied answer.")


class SubmitClarificationRequest(BaseModel):
    """Request body for ``POST /workflows/{id}/clarification``."""

    model_config = ConfigDict(extra="forbid")

    answers: List[ClarificationAnswer] = Field(
        ..., description="Ordered list of answers, one per outstanding concern."
    )


class RejectPrRequest(BaseModel):
    """Request body for ``POST /workflows/{id}/reject-pr``."""

    model_config = ConfigDict(extra="forbid")

    reason: Optional[str] = Field(None, description="Reason for rejecting the PR.")


class CommentPrRequest(BaseModel):
    """Request body for ``POST /workflows/{id}/comment-pr``."""

    model_config = ConfigDict(extra="forbid")

    comments: str = Field(..., min_length=1, description="Review comments to apply.")


class MarkInterruptedRequest(BaseModel):
    """Request body for ``POST /workflows/{id}/mark-interrupted``."""

    model_config = ConfigDict(extra="forbid")

    failed_node: Optional[str] = Field(
        None, description="Node where the workflow was interrupted; informational."
    )
    actor: str = Field("api", description="Actor recorded in the audit log.")


class ClearDbResponse(BaseModel):
    """Response body for ``POST /admin/clear-db``."""

    workflows: int = Field(..., description="Number of workflow rows deleted.")
    checkpoints: int = Field(..., description="Number of LangGraph checkpoint rows deleted.")


class WorkflowHistoryEntryResponse(BaseModel):
    """One entry returned by ``GET /workflows/{id}/history``."""

    step: int
    node: str
    outcome: str
    result_keys: List[str] = Field(default_factory=list)
    error: Optional[str] = None

    @classmethod
    def from_dto(cls, entry: WorkflowHistoryEntry) -> "WorkflowHistoryEntryResponse":
        return cls(
            step=entry.step,
            node=entry.node,
            outcome=entry.outcome,
            result_keys=list(entry.result_keys),
            error=entry.error,
        )


class WorkflowAuditEntryResponse(BaseModel):
    """One entry returned by ``GET /workflows/{id}/audit-log``."""

    workflow_id: str
    actor: str
    action: str
    timestamp: str
    details: Optional[Dict[str, Any]] = None

    @classmethod
    def from_dto(cls, entry: WorkflowAuditEntry) -> "WorkflowAuditEntryResponse":
        return cls(
            workflow_id=entry.workflow_id,
            actor=entry.actor,
            action=entry.action,
            timestamp=entry.timestamp,
            details=entry.details,
        )


class WorkflowSummaryResponse(BaseModel):
    """Lightweight workflow view returned by list endpoints."""

    id: str
    ticket_key: str
    status: str
    created_at: str
    updated_at: str
    pr_url: Optional[str] = None

    @classmethod
    def from_dto(cls, summary: WorkflowSummary) -> "WorkflowSummaryResponse":
        return cls(
            id=summary.id,
            ticket_key=summary.ticket_key,
            status=summary.status.value,
            created_at=summary.created_at,
            updated_at=summary.updated_at,
            pr_url=summary.pr_url,
        )


class WorkflowDetailResponse(BaseModel):
    """Full workflow record returned by ``GET /workflows/{id}``."""

    id: str
    ticket_key: str
    status: str
    created_at: str
    updated_at: str
    pr_url: Optional[str] = None
    work_plan: Optional[Dict[str, Any]] = None
    code_generation_summary: Optional[Dict[str, Any]] = None
    clarification_history: List[Dict[str, Any]] = Field(default_factory=list)
    pr_comments: Optional[str] = None
    usage_summary: Dict[str, Any] = Field(default_factory=dict)
    retry_count: int = 0

    @classmethod
    def from_dto(cls, detail: WorkflowDetail) -> "WorkflowDetailResponse":
        return cls(
            id=detail.id,
            ticket_key=detail.ticket_key,
            status=detail.status.value,
            created_at=detail.created_at,
            updated_at=detail.updated_at,
            pr_url=detail.pr_url,
            work_plan=detail.work_plan,
            code_generation_summary=detail.code_generation_summary,
            clarification_history=list(detail.clarification_history),
            pr_comments=detail.pr_comments,
            usage_summary=dict(detail.usage_summary),
            retry_count=detail.retry_count,
        )


class WorkflowRunResponse(BaseModel):
    """Response body for the start endpoint."""

    workflow_id: str
    ticket_key: Optional[str] = None
    final_status: str
    interrupted: bool = False
    error: Optional[str] = None
    code_generation_summary: Optional[Dict[str, Any]] = None
    pr_url: Optional[str] = None
    failed_node: Optional[str] = None

    @classmethod
    def from_dto(cls, result: WorkflowRunResult) -> "WorkflowRunResponse":
        return cls(
            workflow_id=result.workflow_id,
            ticket_key=result.ticket_key,
            final_status=result.final_status.value,
            interrupted=result.interrupted,
            error=result.error,
            code_generation_summary=result.code_generation_summary,
            pr_url=result.pr_url,
            failed_node=result.failed_node,
        )


class HealthResponse(BaseModel):
    status: str = "ok"


def parse_status(value: Optional[str]) -> Optional[WorkflowStatus]:
    """Translate the ``?status=`` query param into a ``WorkflowStatus``.

    Returns ``None`` for ``None`` / empty input.  Raises ``ValueError`` for
    unknown values; the route layer turns that into a 400 response.
    """
    if value is None or value == "":
        return None
    try:
        return WorkflowStatus(value)
    except ValueError as exc:
        valid = ", ".join(s.value for s in WorkflowStatus)
        raise ValueError(f"Unknown status '{value}'. Valid values: {valid}") from exc
