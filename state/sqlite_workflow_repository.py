"""
SQLiteWorkflowRepository: concrete SQLite implementation of WorkflowRepository.

Import this module when you need the SQLite-backed implementation or the
module-level singleton accessor.  Import :mod:`state.workflow_repository` when you only
need the ``WorkflowRepository`` Protocol for type annotations or testing.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Dict, List, Optional

from .sqlite_state_store import (
    _create_audit_log,
    get_connection,
)
from .workflow_status import WorkflowStatus


class SQLiteWorkflowRepository:
    """Concrete WorkflowRepository backed by SQLite.

    All connection management is delegated to :func:`state.sqlite_state_store.get_connection`
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
                workflow["work_plan"] = json.loads(workflow["work_plan"])
            if workflow.get("clarification_history"):
                try:
                    workflow["clarification_history"] = json.loads(
                        workflow["clarification_history"]
                    )
                except (json.JSONDecodeError, TypeError):
                    workflow["clarification_history"] = []
            if workflow.get("pr_comments"):
                try:
                    workflow["pr_comments"] = json.loads(workflow["pr_comments"])
                except (json.JSONDecodeError, TypeError):
                    workflow["pr_comments"] = []
            if workflow.get("code_generation_summary"):
                try:
                    workflow["code_generation_summary"] = json.loads(
                        workflow["code_generation_summary"]
                    )
                except (json.JSONDecodeError, TypeError):
                    workflow["code_generation_summary"] = None
            if workflow.get("usage_summary"):
                try:
                    workflow["usage_summary"] = json.loads(workflow["usage_summary"])
                except (json.JSONDecodeError, TypeError):
                    workflow["usage_summary"] = None
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
                    workflow["work_plan"] = json.loads(workflow["work_plan"])
                if workflow.get("clarification_history"):
                    try:
                        workflow["clarification_history"] = json.loads(
                            workflow["clarification_history"]
                        )
                    except (json.JSONDecodeError, TypeError):
                        workflow["clarification_history"] = []
                if workflow.get("pr_comments"):
                    try:
                        workflow["pr_comments"] = json.loads(workflow["pr_comments"])
                    except (json.JSONDecodeError, TypeError):
                        workflow["pr_comments"] = []
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
                    wf["work_plan"] = json.loads(wf["work_plan"])
                if wf.get("clarification_history"):
                    try:
                        wf["clarification_history"] = json.loads(wf["clarification_history"])
                    except (json.JSONDecodeError, TypeError):
                        wf["clarification_history"] = []
                if wf.get("pr_comments"):
                    try:
                        wf["pr_comments"] = json.loads(wf["pr_comments"])
                    except (json.JSONDecodeError, TypeError):
                        wf["pr_comments"] = []
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

    def clear_db(self) -> tuple[int, int]:
        """Delete all workflow, audit log, and LangGraph checkpoint data.

        Returns:
            (workflows_deleted, checkpoints_deleted)
        """
        from .sqlite_state_store import clear_db as _clear_db

        return _clear_db()

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
        """Create a new workflow record and return its UUID.

        The workflow creation and initial audit log entry are written atomically
        in a single transaction. If either fails, both are rolled back.
        """
        workflow_id = workflow_id or str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        work_plan_json = json.dumps(work_plan) if work_plan else None

        conn = get_connection()
        try:
            # Start explicit transaction
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """
                    INSERT INTO workflows
                        (id, ticket_key, status, work_plan, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        workflow_id,
                        ticket_key,
                        status.value,
                        work_plan_json,
                        now,
                        now,
                    ),
                )
                _create_audit_log(
                    conn,
                    workflow_id=workflow_id,
                    actor="system",
                    action="workflow_created",
                    reason=f"Created workflow for {ticket_key}",
                )
                # Commit both operations atomically
                conn.commit()
            except Exception:
                # Rollback on any error during the transaction
                conn.rollback()
                raise
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
        pr_approval_decision: Optional[str] = None,
    ) -> None:
        """Update workflow status and optionally PR URL / approval decision.

        When *status* is REJECTED, *reason* is also written to the
        `rejection_reason` column, alongside the audit log entry, so callers
        can read it without a JOIN on audit_log.

        When *pr_approval_decision* is provided (``"approved"`` / ``"rejected"``
        / ``"commented"``), it is written to the ``pr_approval_decision``
        column in the same transaction so downstream readers do not have to
        replay LangGraph state or the audit log to recover the decision.

        The workflow status update and corresponding audit log entry are written
        atomically in a single transaction. If either fails, both are rolled back.
        """
        now = datetime.now(UTC).isoformat()
        conn = get_connection()
        try:
            # Start explicit transaction
            conn.execute("BEGIN IMMEDIATE")
            try:
                set_clauses = ["status = ?", "updated_at = ?"]
                params: list = [status.value, now]
                if pr_url:
                    set_clauses.append("pr_url = ?")
                    params.append(pr_url)
                if pr_approval_decision:
                    set_clauses.append("pr_approval_decision = ?")
                    params.append(pr_approval_decision)
                if status == WorkflowStatus.REJECTED:
                    set_clauses.append("rejection_reason = ?")
                    params.append(reason)
                params.append(workflow_id)
                conn.execute(
                    f"UPDATE workflows SET {', '.join(set_clauses)} WHERE id = ?",
                    params,
                )
                _create_audit_log(
                    conn,
                    workflow_id=workflow_id,
                    actor=actor,
                    action="status_change",
                    reason=reason or f"Status changed to {status.value}",
                )
                # Commit both operations atomically
                conn.commit()
            except Exception:
                # Rollback on any error during the transaction
                conn.rollback()
                raise
        finally:
            conn.close()

    def update_work_plan(
        self,
        workflow_id: str,
        work_plan: Dict,
        actor: str = "system",
        reason: Optional[str] = None,
    ) -> None:
        """Persist a new work plan for *workflow_id*.

        The work plan update and corresponding audit log entry are written
        atomically in a single transaction. If either fails, both are rolled back.
        """
        now = datetime.now(UTC).isoformat()
        work_plan_json = json.dumps(work_plan)

        conn = get_connection()
        try:
            # Start explicit transaction
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """
                    UPDATE workflows
                    SET work_plan = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (work_plan_json, now, workflow_id),
                )
                _create_audit_log(
                    conn,
                    workflow_id=workflow_id,
                    actor=actor,
                    action="work_plan_updated",
                    reason=reason or "WorkPlan stored",
                )
                # Commit both operations atomically
                conn.commit()
            except Exception:
                # Rollback on any error during the transaction
                conn.rollback()
                raise
        finally:
            conn.close()

    def update_code_generation_summary(
        self,
        workflow_id: str,
        code_generation_summary: Dict,
        actor: str = "system",
    ) -> None:
        """Persist the code generation summary for *workflow_id*.

        The code generation summary update and corresponding audit log entry are written
        atomically in a single transaction. If either fails, both are rolled back.
        """
        now = datetime.now(UTC).isoformat()
        summary_json = json.dumps(code_generation_summary)

        conn = get_connection()
        try:
            # Start explicit transaction
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """
                    UPDATE workflows
                    SET code_generation_summary = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (summary_json, now, workflow_id),
                )
                _create_audit_log(
                    conn,
                    workflow_id=workflow_id,
                    actor=actor,
                    action="code_generation_summary_stored",
                    reason="Code generation summary saved from generate_code recipe",
                )
                # Commit both operations atomically
                conn.commit()
            except Exception:
                # Rollback on any error during the transaction
                conn.rollback()
                raise
        finally:
            conn.close()

    def update_clarification_history(
        self,
        workflow_id: str,
        round_entry: Dict,
        actor: str = "system",
    ) -> None:
        """Append a clarification round entry to *workflow_id*.

        The clarification history update and corresponding audit log entry are written
        atomically in a single transaction. If either fails, both are rolled back.
        """
        now = datetime.now(UTC).isoformat()

        conn = get_connection()
        try:
            # Start explicit transaction
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT clarification_history FROM workflows WHERE id = ?",
                    (workflow_id,),
                ).fetchone()
                if row is None:
                    conn.rollback()
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
                _create_audit_log(
                    conn,
                    workflow_id=workflow_id,
                    actor=actor,
                    action="clarification_history_updated",
                    reason=f"Clarification round {enriched.get('round', '?')} appended",
                )
                # Commit both operations atomically
                conn.commit()
            except Exception:
                # Rollback on any error during the transaction
                conn.rollback()
                raise
        finally:
            conn.close()

    def update_pr_comments(
        self,
        workflow_id: str,
        comments: str,
        actor: str = "system",
    ) -> None:
        """Append a PR review round entry to *workflow_id*'s pr_comments JSON array.

        Stored as a JSON array of round entries (round, comments, actor, timestamp),
        parallel to clarification_history. The PR comments update and corresponding
        audit log entry are written atomically in a single transaction. If either
        fails, both are rolled back.
        """
        now = datetime.now(UTC).isoformat()

        conn = get_connection()
        try:
            # Start explicit transaction
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT pr_comments FROM workflows WHERE id = ?",
                    (workflow_id,),
                ).fetchone()
                if row is None:
                    conn.rollback()
                    return

                rounds: List[Dict] = []
                if row["pr_comments"]:
                    try:
                        rounds = json.loads(row["pr_comments"])
                        if not isinstance(rounds, list):
                            rounds = []
                    except (json.JSONDecodeError, TypeError):
                        rounds = []

                rounds.append(
                    {
                        "round": len(rounds) + 1,
                        "comments": comments,
                        "actor": actor,
                        "timestamp": now,
                    }
                )

                conn.execute(
                    """
                    UPDATE workflows
                    SET pr_comments = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (json.dumps(rounds), now, workflow_id),
                )
                _create_audit_log(
                    conn,
                    workflow_id=workflow_id,
                    actor=actor,
                    action="pr_comments_updated",
                    reason=f"PR review round {len(rounds)} appended",
                )
                # Commit both operations atomically
                conn.commit()
            except Exception:
                # Rollback on any error during the transaction
                conn.rollback()
                raise
        finally:
            conn.close()

    def update_usage_summary(
        self,
        workflow_id: str,
        stage: str,
        data: Dict,
        actor: str = "system",
    ) -> None:
        """Merge per-stage LLM token usage data into *workflow_id*.

        The usage summary update and corresponding audit log entry are written
        atomically in a single transaction. If either fails, both are rolled back.
        """
        now = datetime.now(UTC).isoformat()

        conn = get_connection()
        try:
            # Start explicit transaction
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT usage_summary FROM workflows WHERE id = ?",
                    (workflow_id,),
                ).fetchone()
                if row is None:
                    conn.rollback()
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
                _create_audit_log(
                    conn,
                    workflow_id=workflow_id,
                    actor=actor,
                    action="usage_summary_stored",
                    reason=f"Token usage summary saved for stage '{stage}'",
                )
                # Commit both operations atomically
                conn.commit()
            except Exception:
                # Rollback on any error during the transaction
                conn.rollback()
                raise
        finally:
            conn.close()

    def increment_retry_count(self, workflow_id: str, actor: str = "system") -> int:
        """Increment the retry counter for *workflow_id* and return the new value.

        The retry count update and corresponding audit log entry are written
        atomically in a single transaction. If either fails, both are rolled back.
        """
        now = datetime.now(UTC).isoformat()
        conn = get_connection()
        try:
            # Start explicit transaction
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT retry_count FROM workflows WHERE id = ?", (workflow_id,)
                ).fetchone()
                if row is None:
                    conn.rollback()
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
                _create_audit_log(
                    conn,
                    workflow_id=workflow_id,
                    actor=actor,
                    action="workflow_retried",
                    reason=f"Retry attempt #{new_count}",
                )
                # Commit both operations atomically
                conn.commit()
                return new_count
            except Exception:
                # Rollback on any error during the transaction
                conn.rollback()
                raise
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


__all__ = [
    "SQLiteWorkflowRepository",
    "get_repository",
]
