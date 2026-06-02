"""
State store infrastructure for workflow execution tracking.

This module contains only the SQLite infrastructure concerns:
  - DB path resolution
  - Connection factory
  - Schema migrations
  - Internal audit-log helper
  - Admin clear_db utility

All workflow CRUD operations have been moved to :mod:`state.repository`
(``SQLiteWorkflowRepository``).  Import from there for any read/write
operations on workflow records.
"""

import os
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Dict, List, Optional


def _normalize_work_plan(work_plan: Optional[Dict]) -> Optional[Dict]:
    """Migrate legacy risks/questions_for_reviewer fields to concerns on read."""
    if work_plan is None:
        return None
    has_legacy = "risks" in work_plan or "questions_for_reviewer" in work_plan
    if has_legacy:
        concerns = []
        concerns.extend(work_plan.get("risks", []))
        concerns.extend(work_plan.get("questions_for_reviewer", []))
        work_plan["concerns"] = concerns
    # Clean up legacy fields if present
    work_plan.pop("risks", None)
    work_plan.pop("questions_for_reviewer", None)
    return work_plan


def _normalize_clarification_history(history: Optional[List[Dict]]) -> Optional[List[Dict]]:
    """Migrate legacy questions/risks fields to concerns in clarification history on read."""
    if not history:
        return history
    for entry in history:
        if "concerns" not in entry:
            concerns = []
            concerns.extend(entry.get("questions", []))
            concerns.extend(entry.get("risks", []))
            entry["concerns"] = concerns
        entry.pop("questions", None)
        entry.pop("risks", None)
    return history


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
    Tracks applied migrations in a schema_migrations table so each file runs exactly once.
    """
    migrations_dir = Path(__file__).parent / "migrations"
    migration_files = sorted(migrations_dir.glob("*.sql"))

    conn = get_connection()
    try:
        # Bootstrap the migrations tracking table
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                name TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            """)
        conn.commit()

        for migration_file in migration_files:
            name = migration_file.name
            already_applied = conn.execute(
                "SELECT 1 FROM schema_migrations WHERE name = ?", (name,)
            ).fetchone()
            if already_applied:
                continue

            with open(migration_file, "r") as f:
                sql = f.read()
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO schema_migrations (name, applied_at) VALUES (?, ?)",
                (name, datetime.now(UTC).isoformat()),
            )
            conn.commit()
    finally:
        conn.close()


def _create_audit_log(
    conn: sqlite3.Connection,
    workflow_id: str,
    actor: str,
    action: str,
    reason: Optional[str] = None,
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
        (audit_id, workflow_id, actor, action, reason, now),
    )


def clear_db() -> tuple[int, int]:
    """
    Delete all workflow and audit log data, and reset LangGraph checkpoints.

    Returns:
        (workflows_deleted, checkpoints_deleted)
    """
    conn = get_connection()
    try:
        wf_count = conn.execute("SELECT COUNT(*) FROM workflows").fetchone()[0]
        cp_count = conn.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0]

        conn.executescript("""
            DELETE FROM audit_log;
            DELETE FROM workflows;
            DELETE FROM checkpoints;
            DELETE FROM writes;
        """)
        conn.commit()
        return wf_count, cp_count
    finally:
        conn.close()


# Initialize database on module import
run_migrations()
