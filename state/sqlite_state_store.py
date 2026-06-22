"""
State store infrastructure for workflow execution tracking.

This module contains only the SQLite infrastructure concerns:
  - DB path resolution
  - Connection factory
  - Schema migrations
  - Internal audit-log helper
  - Admin clear_db utility

All workflow CRUD operations have been moved to :mod:`state.workflow_repository`
(``SQLiteWorkflowRepository``).  Import from there for any read/write
operations on workflow records.
"""

import logging
import os
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

from orchestrator.log_paths import state_base_dir

logger = logging.getLogger(__name__)

_LEGACY_DB_PATH = Path("state") / "local.db"
_legacy_warning_emitted = False


def get_db_path() -> str:
    """Resolve the SQLite database path.

    Resolution order:
      1. ``DB_PATH`` env var (explicit override).
      2. ``$XDG_STATE_HOME/ngb-agent-orchestrator/db/local.db`` when
         ``XDG_STATE_HOME`` is set.
      3. ``~/.local/state/ngb-agent-orchestrator/db/local.db`` otherwise.

    The parent directory is created on first use. When the XDG default is in
    effect and a legacy ``./state/local.db`` exists relative to the current
    working directory, a one-line warning is logged pointing at the new
    location.  The legacy file is never moved automatically.
    """
    override = os.getenv("DB_PATH")
    if override:
        db_path = Path(override).expanduser()
    else:
        db_path = state_base_dir() / "db" / "local.db"
        _maybe_warn_legacy_db(db_path)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    return str(db_path)


def _maybe_warn_legacy_db(new_path: Path) -> None:
    """Log a one-shot warning when a legacy ./state/local.db is shadowed."""
    global _legacy_warning_emitted
    if _legacy_warning_emitted:
        return
    if new_path.exists():
        return
    if not _LEGACY_DB_PATH.exists():
        return

    legacy_abs = _LEGACY_DB_PATH.resolve()
    logger.warning(
        "Legacy SQLite DB detected at %s but the orchestrator now resolves to %s. "
        "Move the file manually to migrate (see docs/configuration.md).",
        legacy_abs,
        new_path,
    )
    _legacy_warning_emitted = True


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
