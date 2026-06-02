"""
WorkflowRepository abstraction over the SQLite state store.

Provides:
  - WorkflowRepository: typing.Protocol defining the public interface (DIP)
  - SQLiteWorkflowRepository: concrete SQLite implementation
  - get_repository(): module-level singleton accessor
  - Module-level convenience functions for drop-in replacement of
    the old ``state.state_store`` imports.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Dict, List, Optional, Protocol, runtime_checkable

from .state_store import (
    _create_audit_log,
    _normalize_clarification_history,
    _normalize_work_plan,
    get_connection,
    get_db_path,
    run_migrations,
)
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


class SQLiteWorkflowRepository:
    """Concrete WorkflowRepository backed by SQLite.

    All connection management is delegated to :func:`state.state_store.get_connection`
    so the DB path is read from the ``DB_PATH`` environment variable on every call.
    This keeps the class stateless and makes it safe to use with environment-variable
    based test fixtures.
    """

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def get_workflow(self, workflow_id: str) -> Optional[Dict]:
        """Retrieve workflow by ID, or None if not found."""
        conn = get_connection()
        try:
            cursor = conn.execute("SELECT * FROM workflows WHERE id = ?", (workflow_id,))
            row = cursor.fetchone()
            if row is None:
                return None
            workflow = dict(row)
            if workflow["work_plan"]:
                workflow["work_plan"] = _normalize_work_plan(json.loads(workflow["work_plan"]))
            if workflow.get("clarification_history"):
                try:
                    workflow["clarification_history"] = _normalize_clarification_history(
                        json.loads(workflow["clarification_history"])
                    )
                except (json.JSONDecodeError, TypeError):
                    workflow["clarification_history"] = []
            workflow["status"] = WorkflowStatus(workflow["status"])
            return workflow
        finally:
            conn.close()

    def get_workflow_by_ticket(self, ticket_key: str) -> List[Dict]:
        """Return all workflows for *ticket_key*, newest first."""
        conn = get_connection()
        try:
            cursor = conn.execute(
                "SELECT * FROM workflows WHERE ticket_key = ? ORDER BY created_at DESC",
                (ticket_key,),
            )
            rows = cursor.fetchall()
            workflows = []
            for row in rows:
                workflow = dict(row)
                if workflow["work_plan"]:
                    workflow["work_plan"] = _normalize_work_plan(json.loads(workflow["work_plan"]))
                if workflow.get("clarification_history"):
                    try:
                        workflow["clarification_history"] = _normalize_clarification_history(
                            json.loads(workflow["clarification_history"])
                        )
                    except (json.JSONDecodeError, TypeError):
                        workflow["clarification_history"] = []
                workflow["status"] = WorkflowStatus(workflow["status"])
                workflows.append(workflow)
            return workflows
        finally:
            conn.close()

    def get_latest_retryable_workflow_by_ticket(self, ticket_key: str) -> Optional[Dict]:
        """Return the most recent retryable workflow for *ticket_key*, or None."""
        workflows = self.get_workflow_by_ticket(ticket_key)
        for wf in workflows:  # already ordered created_at DESC
            if wf["status"].is_retryable():
                return wf
        return None

    def list_workflows(
        self,
        ticket_key: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict]:
        """List workflows, optionally filtered by ticket key and/or status."""
        clauses = []
        params: list = []
        if ticket_key:
            clauses.append("ticket_key = ?")
            params.append(ticket_key)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)

        conn = get_connection()
        try:
            cursor = conn.execute(
                f"SELECT * FROM workflows {where} ORDER BY created_at DESC LIMIT ?",
                params,
            )
            rows = cursor.fetchall()
            result = []
            for row in rows:
                wf = dict(row)
                if wf["work_plan"]:
                    wf["work_plan"] = _normalize_work_plan(json.loads(wf["work_plan"]))
                if wf.get("clarification_history"):
                    try:
                        wf["clarification_history"] = _normalize_clarification_history(
                            json.loads(wf["clarification_history"])
                        )
                    except (json.JSONDecodeError, TypeError):
                        wf["clarification_history"] = []
                wf["status"] = WorkflowStatus(wf["status"])
                result.append(wf)
            return result
        finally:
            conn.close()

    def get_audit_log(self, workflow_id: str) -> List[Dict]:
        """Return audit log entries for *workflow_id*, oldest first."""
        conn = get_connection()
        try:
            cursor = conn.execute(
                "SELECT * FROM audit_log WHERE workflow_id = ? ORDER BY created_at ASC",
                (workflow_id,),
            )
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def create_workflow(
        self,
        ticket_key: str,
        work_plan: Optional[Dict] = None,
        status: WorkflowStatus = WorkflowStatus.PENDING,
        workflow_id: Optional[str] = None,
    ) -> str:
        """Create a new workflow record and return its UUID."""
        workflow_id = workflow_id or str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        work_plan_json = json.dumps(work_plan) if work_plan else None

        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO workflows (id, ticket_key, status, work_plan, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (workflow_id, ticket_key, status.value, work_plan_json, now, now),
            )
            conn.commit()
            _create_audit_log(
                conn,
                workflow_id=workflow_id,
                actor="system",
                action="workflow_created",
                reason=f"Created workflow for {ticket_key}",
            )
            conn.commit()
        finally:
            conn.close()

        return workflow_id

    def update_status(
        self,
        workflow_id: str,
        status: WorkflowStatus,
        pr_url: Optional[str] = None,
        actor: str = "system",
        reason: Optional[str] = None,
    ) -> None:
        """Update workflow status and optionally PR URL."""
        now = datetime.now(UTC).isoformat()
        conn = get_connection()
        try:
            if pr_url:
                conn.execute(
                    """
                    UPDATE workflows
                    SET status = ?, pr_url = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (status.value, pr_url, now, workflow_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE workflows
                    SET status = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (status.value, now, workflow_id),
                )
            conn.commit()
            _create_audit_log(
                conn,
                workflow_id=workflow_id,
                actor=actor,
                action="status_change",
                reason=reason or f"Status changed to {status.value}",
            )
            conn.commit()
        finally:
            conn.close()

    def update_work_plan(
        self,
        workflow_id: str,
        work_plan: Dict,
        actor: str = "system",
        reason: Optional[str] = None,
    ) -> None:
        """Persist a new work plan for *workflow_id*."""
        now = datetime.now(UTC).isoformat()
        work_plan_json = json.dumps(work_plan)

        conn = get_connection()
        try:
            conn.execute(
                """
                UPDATE workflows
                SET work_plan = ?, updated_at = ?
                WHERE id = ?
                """,
                (work_plan_json, now, workflow_id),
            )
            conn.commit()
            _create_audit_log(
                conn,
                workflow_id=workflow_id,
                actor=actor,
                action="work_plan_updated",
                reason=reason or "WorkPlan stored",
            )
            conn.commit()
        finally:
            conn.close()

    def update_execution_summary(
        self,
        workflow_id: str,
        execution_summary: Dict,
        actor: str = "system",
    ) -> None:
        """Persist the execution summary for *workflow_id*."""
        now = datetime.now(UTC).isoformat()
        summary_json = json.dumps(execution_summary)

        conn = get_connection()
        try:
            conn.execute(
                """
                UPDATE workflows
                SET execution_summary = ?, updated_at = ?
                WHERE id = ?
                """,
                (summary_json, now, workflow_id),
            )
            conn.commit()
            _create_audit_log(
                conn,
                workflow_id=workflow_id,
                actor=actor,
                action="execution_summary_stored",
                reason="Execution summary saved from execute recipe",
            )
            conn.commit()
        finally:
            conn.close()

    def update_clarification_history(
        self,
        workflow_id: str,
        round_entry: Dict,
        actor: str = "system",
    ) -> None:
        """Append a clarification round entry to *workflow_id*."""
        now = datetime.now(UTC).isoformat()

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT clarification_history FROM workflows WHERE id = ?",
                (workflow_id,),
            ).fetchone()
            if row is None:
                return

            history: List[Dict] = []
            if row["clarification_history"]:
                try:
                    history = json.loads(row["clarification_history"])
                    if not isinstance(history, list):
                        history = []
                except (json.JSONDecodeError, TypeError):
                    history = []

            enriched = dict(round_entry)
            enriched.setdefault("actor", actor)
            enriched.setdefault("timestamp", now)
            history.append(enriched)

            history_json = json.dumps(history)
            conn.execute(
                """
                UPDATE workflows
                SET clarification_history = ?, updated_at = ?
                WHERE id = ?
                """,
                (history_json, now, workflow_id),
            )
            conn.commit()
            _create_audit_log(
                conn,
                workflow_id=workflow_id,
                actor=actor,
                action="clarification_history_updated",
                reason=f"Clarification round {enriched.get('round', '?')} appended",
            )
            conn.commit()
        finally:
            conn.close()

    def update_pr_comments(
        self,
        workflow_id: str,
        comments: str,
        actor: str = "system",
    ) -> None:
        """Append PR review comments to *workflow_id*, preserving previous rounds."""
        now = datetime.now(UTC).isoformat()

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT pr_comments FROM workflows WHERE id = ?",
                (workflow_id,),
            ).fetchone()
            if row is None:
                return

            existing = row["pr_comments"] or ""
            separator = f"\n\n--- Review round {now} ---\n"
            updated = (existing + separator + comments).strip()

            conn.execute(
                """
                UPDATE workflows
                SET pr_comments = ?, updated_at = ?
                WHERE id = ?
                """,
                (updated, now, workflow_id),
            )
            conn.commit()
            _create_audit_log(
                conn,
                workflow_id=workflow_id,
                actor=actor,
                action="pr_comments_updated",
                reason="PR review comments appended",
            )
            conn.commit()
        finally:
            conn.close()

    def update_usage_summary(
        self,
        workflow_id: str,
        stage: str,
        data: Dict,
        actor: str = "system",
    ) -> None:
        """Merge per-stage LLM token usage data into *workflow_id*."""
        now = datetime.now(UTC).isoformat()

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT usage_summary FROM workflows WHERE id = ?",
                (workflow_id,),
            ).fetchone()
            if row is None:
                return

            existing: Dict = {}
            if row["usage_summary"]:
                try:
                    existing = json.loads(row["usage_summary"])
                except (json.JSONDecodeError, TypeError):
                    existing = {}

            existing[stage] = data
            summary_json = json.dumps(existing)

            conn.execute(
                """
                UPDATE workflows
                SET usage_summary = ?, updated_at = ?
                WHERE id = ?
                """,
                (summary_json, now, workflow_id),
            )
            conn.commit()
            _create_audit_log(
                conn,
                workflow_id=workflow_id,
                actor=actor,
                action="usage_summary_stored",
                reason=f"Token usage summary saved for stage '{stage}'",
            )
            conn.commit()
        finally:
            conn.close()

    def increment_retry_count(self, workflow_id: str, actor: str = "system") -> int:
        """Increment the retry counter for *workflow_id* and return the new value."""
        now = datetime.now(UTC).isoformat()
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT retry_count FROM workflows WHERE id = ?", (workflow_id,)
            ).fetchone()
            if row is None:
                return 0
            new_count = int(row["retry_count"] or 0) + 1
            conn.execute(
                """
                UPDATE workflows
                SET retry_count = ?, updated_at = ?
                WHERE id = ?
                """,
                (new_count, now, workflow_id),
            )
            conn.commit()
            _create_audit_log(
                conn,
                workflow_id=workflow_id,
                actor=actor,
                action="workflow_retried",
                reason=f"Retry attempt #{new_count}",
            )
            conn.commit()
            return new_count
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_repo: Optional[SQLiteWorkflowRepository] = None


def get_repository() -> SQLiteWorkflowRepository:
    """Return the module-level SQLiteWorkflowRepository singleton."""
    global _repo
    if _repo is None:
        _repo = SQLiteWorkflowRepository()
    return _repo


# ---------------------------------------------------------------------------
# Module-level convenience functions
#
# These are thin delegators to the singleton so callers can continue using
# the familiar ``from state.repository import create_workflow`` style without
# being aware of the class.
# ---------------------------------------------------------------------------


def create_workflow(
    ticket_key: str,
    work_plan: Optional[Dict] = None,
    status: WorkflowStatus = WorkflowStatus.PENDING,
    workflow_id: Optional[str] = None,
) -> str:
    return get_repository().create_workflow(ticket_key, work_plan, status, workflow_id)


def update_status(
    workflow_id: str,
    status: WorkflowStatus,
    pr_url: Optional[str] = None,
    actor: str = "system",
    reason: Optional[str] = None,
) -> None:
    return get_repository().update_status(workflow_id, status, pr_url, actor, reason)


def update_work_plan(
    workflow_id: str,
    work_plan: Dict,
    actor: str = "system",
    reason: Optional[str] = None,
) -> None:
    return get_repository().update_work_plan(workflow_id, work_plan, actor, reason)


def update_execution_summary(
    workflow_id: str,
    execution_summary: Dict,
    actor: str = "system",
) -> None:
    return get_repository().update_execution_summary(workflow_id, execution_summary, actor)


def update_clarification_history(
    workflow_id: str,
    round_entry: Dict,
    actor: str = "system",
) -> None:
    return get_repository().update_clarification_history(workflow_id, round_entry, actor)


def update_pr_comments(
    workflow_id: str,
    comments: str,
    actor: str = "system",
) -> None:
    return get_repository().update_pr_comments(workflow_id, comments, actor)


def update_usage_summary(
    workflow_id: str,
    stage: str,
    data: Dict,
    actor: str = "system",
) -> None:
    return get_repository().update_usage_summary(workflow_id, stage, data, actor)


def list_workflows(
    ticket_key: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
) -> List[Dict]:
    return get_repository().list_workflows(ticket_key, status, limit)


def get_workflow(workflow_id: str) -> Optional[Dict]:
    return get_repository().get_workflow(workflow_id)


def get_workflow_by_ticket(ticket_key: str) -> List[Dict]:
    return get_repository().get_workflow_by_ticket(ticket_key)


def get_latest_retryable_workflow_by_ticket(ticket_key: str) -> Optional[Dict]:
    return get_repository().get_latest_retryable_workflow_by_ticket(ticket_key)


def increment_retry_count(workflow_id: str, actor: str = "system") -> int:
    return get_repository().increment_retry_count(workflow_id, actor)


def get_audit_log(workflow_id: str) -> List[Dict]:
    return get_repository().get_audit_log(workflow_id)


# Re-export infrastructure helpers so callers can use a single import path.
__all__ = [
    # Protocol
    "WorkflowRepository",
    # Concrete implementation
    "SQLiteWorkflowRepository",
    # Singleton accessor
    "get_repository",
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
    # Infrastructure re-exports (for callers that need them)
    "get_db_path",
    "run_migrations",
]
