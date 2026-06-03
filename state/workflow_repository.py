"""
WorkflowRepository Protocol — the public interface for workflow persistence.

This module intentionally contains *only* the Protocol definition and thin
module-level convenience functions.  It has no dependency on SQLite or any
concrete storage backend.

Import this module when you need:
  - The ``WorkflowRepository`` type for annotations or isinstance checks
  - The convenience functions (``create_workflow``, ``get_workflow``, …) that
    delegate to the default SQLite singleton

Import :mod:`state.sqlite_workflow_repository` when you need the concrete
``SQLiteWorkflowRepository`` class or the ``get_repository()`` singleton
directly.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Protocol, runtime_checkable

from .sqlite_state_store import get_db_path, run_migrations
from .workflow_status import WorkflowStatus


@runtime_checkable
class WorkflowRepository(Protocol):
    """Read/write interface for workflow persistence.

    High-level modules depend on this protocol rather than on SQLite directly,
    satisfying the Dependency Inversion Principle.  Tests can supply a
    FakeWorkflowRepository without touching the database.
    """

    def get_workflow(self, workflow_id: str) -> Optional[Dict]: ...

    def get_workflow_by_ticket(self, ticket_key: str) -> List[Dict]: ...

    def get_latest_retryable_workflow_by_ticket(self, ticket_key: str) -> Optional[Dict]: ...

    def list_workflows(
        self,
        ticket_key: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict]: ...

    def create_workflow(
        self,
        ticket_key: str,
        work_plan: Optional[Dict] = None,
        status: WorkflowStatus = WorkflowStatus.PENDING,
        workflow_id: Optional[str] = None,
    ) -> str: ...

    def update_status(
        self,
        workflow_id: str,
        status: WorkflowStatus,
        pr_url: Optional[str] = None,
        actor: str = "system",
        reason: Optional[str] = None,
    ) -> None: ...

    def update_work_plan(
        self,
        workflow_id: str,
        work_plan: Dict,
        actor: str = "system",
        reason: Optional[str] = None,
    ) -> None: ...

    def update_execution_summary(
        self,
        workflow_id: str,
        execution_summary: Dict,
        actor: str = "system",
    ) -> None: ...

    def update_clarification_history(
        self,
        workflow_id: str,
        round_entry: Dict,
        actor: str = "system",
    ) -> None: ...

    def update_pr_comments(
        self,
        workflow_id: str,
        comments: str,
        actor: str = "system",
    ) -> None: ...

    def update_usage_summary(
        self,
        workflow_id: str,
        stage: str,
        data: Dict,
        actor: str = "system",
    ) -> None: ...

    def increment_retry_count(self, workflow_id: str, actor: str = "system") -> int: ...

    def get_audit_log(self, workflow_id: str) -> List[Dict]: ...


# ---------------------------------------------------------------------------
# Module-level convenience functions
#
# These delegate to the SQLiteWorkflowRepository singleton via a lazy import
# so that importing ``state.repository`` (e.g. for type annotations or tests
# with a FakeWorkflowRepository) does not transitively load SQLite
# infrastructure.
# ---------------------------------------------------------------------------


def _default_repo():
    # Lazy import breaks the circular dependency:
    #   workflow_repository → sqlite_workflow_repository → workflow_repository (for the Protocol)
    from .sqlite_workflow_repository import get_repository

    return get_repository()


def create_workflow(
    ticket_key: str,
    work_plan: Optional[Dict] = None,
    status: WorkflowStatus = WorkflowStatus.PENDING,
    workflow_id: Optional[str] = None,
) -> str:
    return _default_repo().create_workflow(ticket_key, work_plan, status, workflow_id)


def update_status(
    workflow_id: str,
    status: WorkflowStatus,
    pr_url: Optional[str] = None,
    actor: str = "system",
    reason: Optional[str] = None,
) -> None:
    return _default_repo().update_status(workflow_id, status, pr_url, actor, reason)


def update_work_plan(
    workflow_id: str,
    work_plan: Dict,
    actor: str = "system",
    reason: Optional[str] = None,
) -> None:
    return _default_repo().update_work_plan(workflow_id, work_plan, actor, reason)


def update_execution_summary(
    workflow_id: str,
    execution_summary: Dict,
    actor: str = "system",
) -> None:
    return _default_repo().update_execution_summary(workflow_id, execution_summary, actor)


def update_clarification_history(
    workflow_id: str,
    round_entry: Dict,
    actor: str = "system",
) -> None:
    return _default_repo().update_clarification_history(workflow_id, round_entry, actor)


def update_pr_comments(
    workflow_id: str,
    comments: str,
    actor: str = "system",
) -> None:
    return _default_repo().update_pr_comments(workflow_id, comments, actor)


def update_usage_summary(
    workflow_id: str,
    stage: str,
    data: Dict,
    actor: str = "system",
) -> None:
    return _default_repo().update_usage_summary(workflow_id, stage, data, actor)


def list_workflows(
    ticket_key: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
) -> List[Dict]:
    return _default_repo().list_workflows(ticket_key, status, limit)


def get_workflow(workflow_id: str) -> Optional[Dict]:
    return _default_repo().get_workflow(workflow_id)


def get_workflow_by_ticket(ticket_key: str) -> List[Dict]:
    return _default_repo().get_workflow_by_ticket(ticket_key)


def get_latest_retryable_workflow_by_ticket(ticket_key: str) -> Optional[Dict]:
    return _default_repo().get_latest_retryable_workflow_by_ticket(ticket_key)


def increment_retry_count(workflow_id: str, actor: str = "system") -> int:
    return _default_repo().increment_retry_count(workflow_id, actor)


def get_audit_log(workflow_id: str) -> List[Dict]:
    return _default_repo().get_audit_log(workflow_id)


__all__ = [
    # Protocol
    "WorkflowRepository",
    # Convenience functions
    "create_workflow",
    "update_status",
    "update_work_plan",
    "update_execution_summary",
    "update_clarification_history",
    "update_pr_comments",
    "update_usage_summary",
    "list_workflows",
    "get_workflow",
    "get_workflow_by_ticket",
    "get_latest_retryable_workflow_by_ticket",
    "increment_retry_count",
    "get_audit_log",
    # Infrastructure re-exports
    "get_db_path",
    "run_migrations",
]
