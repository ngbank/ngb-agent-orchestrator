"""Trace reader: the topic-07 extraction query, minus the ``audit_log`` JOIN.

Reads terminal (``completed`` / ``failed`` / ``rejected``) workflows from
SQLite and assembles them into :class:`TraceBundle` objects — the raw input
to the Evaluator (ticket 2.2). Two changes from the topic-07 query as
originally sketched in ``docs/ACE/07-ace-orchestrator-current-state.md``:

- ``rejection_reason`` is read directly from the ``workflows`` column added
  by migration 013, instead of a ``LEFT JOIN`` on ``audit_log``.
- Eligibility anti-joins against ``context_extraction_log`` (migration 012)
  so already-mined workflows aren't re-read — see
  ``docs/ACE/09-ace-orchestrator-learning-pipeline.md``.

``pr_comments`` is expected to already be in the structured JSON-array format
(migration 015 + the one-time backfill). Per ``docs/ACE/11-ace-orchestrator-
data-model.md``, a row whose ``pr_comments`` doesn't parse as a JSON array is
skipped rather than guessed at — it's deferred until the backfill covers it,
keeping this reader single-format.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from typing import Any, Optional

from state.sqlite_state_store import get_connection
from state.workflow_status import WorkflowStatus

logger = logging.getLogger(__name__)

_ELIGIBLE_STATUSES = (
    WorkflowStatus.COMPLETED.value,
    WorkflowStatus.FAILED.value,
    WorkflowStatus.REJECTED.value,
)


@dataclass(frozen=True)
class TraceBundle:
    """One terminal workflow's full trace, ready for the Evaluator.

    ``created_at`` is the source date the Curator must use for
    ``last_validated`` / ``ProvenanceEntry.workflow_date`` (see topic 11) —
    never the extraction date.
    """

    workflow_id: str
    ticket_key: str
    status: str
    created_at: str
    work_plan: Optional[dict[str, Any]]
    code_generation_summary: Optional[dict[str, Any]]
    clarification_history: list[dict[str, Any]]
    pr_comments: list[dict[str, Any]]
    rejection_reason: Optional[str]


def fetch_eligible_traces(*, limit: Optional[int] = None) -> list[TraceBundle]:
    """Return :class:`TraceBundle` objects for terminal workflows not yet mined.

    Eligibility: ``status`` in ``('completed', 'failed', 'rejected')`` AND no
    matching row in ``context_extraction_log`` (the ACE-owned idempotency
    ledger the mining runner writes to on success). Ordered newest-first.
    """
    placeholders = ",".join("?" * len(_ELIGIBLE_STATUSES))
    query = f"""
        SELECT
            w.id,
            w.ticket_key,
            w.status,
            w.created_at,
            w.work_plan,
            w.code_generation_summary,
            w.clarification_history,
            w.pr_comments,
            w.rejection_reason
        FROM workflows w
        WHERE w.status IN ({placeholders})
          AND NOT EXISTS (
              SELECT 1 FROM context_extraction_log l WHERE l.workflow_id = w.id
          )
        ORDER BY w.created_at DESC
    """
    params: list[Any] = list(_ELIGIBLE_STATUSES)
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)

    conn = get_connection()
    try:
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()

    bundles = []
    for row in rows:
        bundle = _row_to_trace_bundle(row)
        if bundle is not None:
            bundles.append(bundle)
    return bundles


def fetch_trace_by_id(workflow_id: str) -> Optional[TraceBundle]:
    """Return a :class:`TraceBundle` for a specific *workflow_id*, or ``None``.

    Unlike :func:`fetch_eligible_traces` this bypasses the
    ``context_extraction_log`` anti-join, so it can be used to re-mine a
    workflow that was already processed (e.g. after a pipeline failure).
    Returns ``None`` if the workflow does not exist or has a non-terminal
    status.
    """
    placeholders = ",".join("?" * len(_ELIGIBLE_STATUSES))
    query = f"""
        SELECT
            w.id,
            w.ticket_key,
            w.status,
            w.created_at,
            w.work_plan,
            w.code_generation_summary,
            w.clarification_history,
            w.pr_comments,
            w.rejection_reason
        FROM workflows w
        WHERE w.id = ?
          AND w.status IN ({placeholders})
    """
    conn = get_connection()
    try:
        row = conn.execute(query, [workflow_id, *_ELIGIBLE_STATUSES]).fetchone()
    finally:
        conn.close()

    if row is None:
        return None
    return _row_to_trace_bundle(row)


def _row_to_trace_bundle(row: sqlite3.Row) -> Optional[TraceBundle]:
    """Build a :class:`TraceBundle` from a query row, or ``None`` to skip it.

    ``pr_comments`` must parse as a JSON array — a row that fails this check
    still has legacy free-text comments awaiting backfill, so it's skipped
    (deferred to a later run) rather than read in two different shapes.
    """
    raw_pr_comments = row["pr_comments"]
    try:
        pr_comments = json.loads(raw_pr_comments) if raw_pr_comments else []
        if not isinstance(pr_comments, list):
            raise ValueError("pr_comments JSON is not an array")
    except (json.JSONDecodeError, ValueError, TypeError):
        logger.warning(
            "Skipping workflow %s: pr_comments is not valid JSON (needs backfill)",
            row["id"],
        )
        return None

    work_plan = None
    if row["work_plan"]:
        try:
            work_plan = json.loads(row["work_plan"])
        except (json.JSONDecodeError, TypeError):
            work_plan = None

    code_generation_summary = None
    if row["code_generation_summary"]:
        try:
            code_generation_summary = json.loads(row["code_generation_summary"])
        except (json.JSONDecodeError, TypeError):
            code_generation_summary = None

    clarification_history: list[dict[str, Any]] = []
    if row["clarification_history"]:
        try:
            parsed = json.loads(row["clarification_history"])
            if isinstance(parsed, list):
                clarification_history = parsed
        except (json.JSONDecodeError, TypeError):
            clarification_history = []

    return TraceBundle(
        workflow_id=row["id"],
        ticket_key=row["ticket_key"],
        status=row["status"],
        created_at=row["created_at"],
        work_plan=work_plan,
        code_generation_summary=code_generation_summary,
        clarification_history=clarification_history,
        pr_comments=pr_comments,
        rejection_reason=row["rejection_reason"],
    )


__all__ = [
    "TraceBundle",
    "fetch_eligible_traces",
]
