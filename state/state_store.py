"""
State store for workflow execution tracking.

This module provides functions to create, update, and retrieve workflow state
stored in a SQLite database. Each workflow maps to one JIRA ticket run.
"""

import sqlite3
import json
import uuid
from datetime import datetime, UTC
from pathlib import Path
from typing import Optional, Dict, List
import os

from .workflow_status import WorkflowStatus


def get_db_path() -> str:
    """Get the database path from environment or use default."""
    db_path = os.getenv("DB_PATH", "state/local.db")
    # Ensure directory exists
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return db_path


def get_connection() -> sqlite3.Connection:
    """Get a database connection."""
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # Enable dict-like access to rows
    return conn


def run_migrations() -> None:
    """
    Run database migrations.
    This is idempotent - safe to run multiple times.
    """
    migrations_dir = Path(__file__).parent / "migrations"
    migration_files = sorted(migrations_dir.glob("*.sql"))
    
    conn = get_connection()
    try:
        for migration_file in migration_files:
            with open(migration_file, 'r') as f:
                sql = f.read()
                conn.executescript(sql)
        conn.commit()
    finally:
        conn.close()


def create_workflow(
    ticket_key: str,
    work_plan: Optional[Dict] = None,
    status: WorkflowStatus = WorkflowStatus.PENDING
) -> str:
    """
    Create a new workflow record.
    
    Args:
        ticket_key: JIRA ticket key (e.g., "AOS-35")
        work_plan: Dictionary containing the work plan (will be JSON-serialized)
        status: Initial status (default: WorkflowStatus.PENDING)
    
    Returns:
        workflow_id: UUID of created workflow
    """
    workflow_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    work_plan_json = json.dumps(work_plan) if work_plan else None
    
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO workflows (id, ticket_key, status, work_plan, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (workflow_id, ticket_key, status.value, work_plan_json, now, now)
        )
        conn.commit()
        
        # Create audit log entry
        _create_audit_log(
            conn,
            workflow_id=workflow_id,
            actor="system",
            action="workflow_created",
            reason=f"Created workflow for {ticket_key}"
        )
        conn.commit()
    finally:
        conn.close()
    
    return workflow_id


def update_status(
    workflow_id: str,
    status: WorkflowStatus,
    pr_url: Optional[str] = None,
    actor: str = "system",
    reason: Optional[str] = None
) -> None:
    """
    Update workflow status and optionally PR URL.
    Also creates an audit log entry.
    
    Args:
        workflow_id: UUID of the workflow
        status: New status value
        pr_url: Pull request URL (optional)
        actor: Who/what performed the update
        reason: Reason for the update (optional)
    """
    now = datetime.now(UTC).isoformat()
    
    conn = get_connection()
    try:
        # Update workflow
        if pr_url:
            conn.execute(
                """
                UPDATE workflows
                SET status = ?, pr_url = ?, updated_at = ?
                WHERE id = ?
                """,
                (status.value, pr_url, now, workflow_id)
            )
        else:
            conn.execute(
                """
                UPDATE workflows
                SET status = ?, updated_at = ?
                WHERE id = ?
                """,
                (status.value, now, workflow_id)
            )
        
        conn.commit()
        
        # Create audit log entry
        _create_audit_log(
            conn,
            workflow_id=workflow_id,
            actor=actor,
            action="status_change",
            reason=reason or f"Status changed to {status.value}"
        )
        conn.commit()
    finally:
        conn.close()


def get_workflow(workflow_id: str) -> Optional[Dict]:
    """
    Retrieve workflow by ID.
    
    Args:
        workflow_id: UUID of the workflow
    
    Returns:
        Dictionary with workflow data, or None if not found
    """
    conn = get_connection()
    try:
        cursor = conn.execute(
            "SELECT * FROM workflows WHERE id = ?",
            (workflow_id,)
        )
        row = cursor.fetchone()
        
        if row is None:
            return None
        
        # Convert to dict and deserialize work_plan and status
        workflow = dict(row)
        if workflow['work_plan']:
            workflow['work_plan'] = json.loads(workflow['work_plan'])
        workflow['status'] = WorkflowStatus(workflow['status'])
        
        return workflow
    finally:
        conn.close()


def get_workflow_by_ticket(ticket_key: str) -> List[Dict]:
    """
    Retrieve all workflows for a given ticket.
    
    Args:
        ticket_key: JIRA ticket key
    
    Returns:
        List of workflow dictionaries
    """
    conn = get_connection()
    try:
        cursor = conn.execute(
            "SELECT * FROM workflows WHERE ticket_key = ? ORDER BY created_at DESC",
            (ticket_key,)
        )
        rows = cursor.fetchall()
        
        workflows = []
        for row in rows:
            workflow = dict(row)
            if workflow['work_plan']:
                workflow['work_plan'] = json.loads(workflow['work_plan'])
            workflow['status'] = WorkflowStatus(workflow['status'])
            workflows.append(workflow)
        
        return workflows
    finally:
        conn.close()


def get_audit_log(workflow_id: str) -> List[Dict]:
    """
    Retrieve audit log entries for a workflow.
    
    Args:
        workflow_id: UUID of the workflow
    
    Returns:
        List of audit log entries
    """
    conn = get_connection()
    try:
        cursor = conn.execute(
            "SELECT * FROM audit_log WHERE workflow_id = ? ORDER BY created_at ASC",
            (workflow_id,)
        )
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _create_audit_log(
    conn: sqlite3.Connection,
    workflow_id: str,
    actor: str,
    action: str,
    reason: Optional[str] = None
) -> None:
    """
    Internal function to create an audit log entry.
    Note: This is append-only - no delete operations.
    
    Args:
        conn: Database connection
        workflow_id: UUID of the workflow
        actor: Who/what performed the action
        action: Action performed
        reason: Optional reason for the action
    """
    audit_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    
    conn.execute(
        """
        INSERT INTO audit_log (id, workflow_id, actor, action, reason, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (audit_id, workflow_id, actor, action, reason, now)
    )


# Initialize database on module import
run_migrations()
