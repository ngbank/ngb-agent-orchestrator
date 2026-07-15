"""Offline mining runner: batch-processes eligible workflows through the
ACE learning pipeline (Evaluator → Reflector → Curator).

Entry point for the ``ace mine`` CLI command. This module is intentionally
side-effect-free in dry-run mode — no DB writes occur.

Pipeline per workflow:

1. Fetch a :class:`~ace.pipeline.trace_reader.TraceBundle`.
2. Pass to :func:`~ace.pipeline.evaluator.evaluate`; skip or flag as directed.
3. If verdict is ``proceed``: call :func:`~ace.pipeline.reflector.reflect`,
   then :func:`~ace.pipeline.curator.curate`.
4. Insert a ``context_extraction_log`` row to mark the workflow as processed.
5. On any exception: write a ``learning_pipeline_failed`` audit entry and
   continue to the next workflow (the row stays absent from
   ``context_extraction_log`` so the next run will retry it).

Flags exposed through the public :func:`run_mining` function:

- ``limit``       — cap the number of workflows fetched (ignored when
  ``workflow_id`` is given).
- ``dry_run``     — evaluate and reflect but skip all DB writes; useful for
  calibration without modifying state.
- ``workflow_id`` — process a single specific workflow, bypassing the
  eligibility anti-join (useful for re-running after a pipeline failure).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Optional

from ace.pipeline.curator import CurationResult, curate
from ace.pipeline.evaluator import Verdict, evaluate
from ace.pipeline.reflector import comment_recall, reflect
from ace.pipeline.trace_reader import fetch_eligible_traces, fetch_trace_by_id
from ace.repository.context_item_repository import ContextItemRepository
from state.sqlite_state_store import _create_audit_log, get_connection

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class RunnerResult:
    """Aggregated outcome of a :func:`run_mining` call."""

    processed: int = 0
    """Total number of workflows attempted (including failures)."""

    succeeded: int = 0
    """Workflows that completed the pipeline without exception."""

    skipped: int = 0
    """Workflows whose Evaluator verdict was ``skip``."""

    flagged: int = 0
    """Workflows whose Evaluator verdict was ``flag``."""

    failed: int = 0
    """Workflows where an exception occurred during pipeline execution."""

    dry_run: bool = False
    """Whether DB writes were suppressed."""

    curation: CurationResult = field(default_factory=CurationResult)
    """Cumulative Curator counts across all succeeded ``proceed`` workflows."""

    comment_units: int = 0
    """Total PR-comment units shown to the Reflector across ``proceed`` workflows."""

    comment_units_cited: int = 0
    """Distinct PR-comment units cited in candidate evidence (recall numerator)."""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_mining(
    *,
    limit: Optional[int] = None,
    dry_run: bool = False,
    workflow_id: Optional[str] = None,
) -> RunnerResult:
    """Run the offline mining pipeline.

    Parameters
    ----------
    limit:
        Maximum number of eligible workflows to process.  Ignored when
        *workflow_id* is supplied.
    dry_run:
        When ``True``, execute the Evaluator and Reflector but do not write
        to ``context_items_staged`` or ``context_extraction_log``.
    workflow_id:
        Process only this specific workflow.  Bypasses the
        ``context_extraction_log`` anti-join so already-processed workflows
        can be re-mined after a previous failure.

    Returns
    -------
    RunnerResult
        Summary of what happened.
    """
    result = RunnerResult(dry_run=dry_run)
    repo = ContextItemRepository()

    if workflow_id is not None:
        bundle = fetch_trace_by_id(workflow_id)
        if bundle is None:
            logger.warning(
                "Runner: workflow %s not found or not in a terminal status; nothing to do",
                workflow_id,
            )
            return result
        bundles = [bundle]
    else:
        bundles = fetch_eligible_traces(limit=limit)

    logger.info(
        "Runner: %s%d workflow(s) to process",
        "[dry-run] " if dry_run else "",
        len(bundles),
    )

    for bundle in bundles:
        result.processed += 1
        try:
            verdict: Verdict = evaluate(bundle)
            logger.debug(
                "Runner: workflow %s verdict=%s",
                bundle.workflow_id,
                verdict,
            )

            if verdict == "skip":
                logger.info(
                    "Runner: skipping workflow %s (trivial success)",
                    bundle.workflow_id,
                )
                result.skipped += 1
                if not dry_run:
                    _mark_extracted(bundle.workflow_id)
                result.succeeded += 1
                continue

            if verdict == "flag":
                logger.info(
                    "Runner: flagging workflow %s for manual review",
                    bundle.workflow_id,
                )
                result.flagged += 1
                if not dry_run:
                    _mark_extracted(bundle.workflow_id)
                result.succeeded += 1
                continue

            # verdict == "proceed"
            candidates = reflect(bundle)
            units_total, units_cited = comment_recall(bundle, candidates)
            result.comment_units += units_total
            result.comment_units_cited += units_cited
            logger.debug(
                "Runner: workflow %s reflector returned %d candidate(s), " "comment recall %d/%d",
                bundle.workflow_id,
                len(candidates),
                units_cited,
                units_total,
            )

            if not dry_run:
                curation_result = curate(candidates, bundle, repo=repo)
                result.curation.created += curation_result.created
                result.curation.merged += curation_result.merged
                result.curation.contradicted += curation_result.contradicted
                result.curation.discarded += curation_result.discarded
                _mark_extracted(
                    bundle.workflow_id,
                    comment_units=units_total,
                    comment_units_cited=units_cited,
                )
            else:
                logger.info(
                    "Runner: [dry-run] would curate %d candidate(s) for workflow %s",
                    len(candidates),
                    bundle.workflow_id,
                )

            result.succeeded += 1

        except Exception as exc:
            result.failed += 1
            logger.error(
                "Runner: pipeline failed for workflow %s: %s",
                bundle.workflow_id,
                exc,
                exc_info=True,
            )
            if not dry_run:
                _write_pipeline_failure(bundle.workflow_id, exc)

    recall_note = ""
    if result.comment_units:
        recall_note = (
            f" comment_recall={result.comment_units_cited}/{result.comment_units}"
            f" ({result.comment_units_cited / result.comment_units:.0%})"
        )
    logger.info(
        "Runner: done — processed=%d succeeded=%d skipped=%d flagged=%d failed=%d%s%s",
        result.processed,
        result.succeeded,
        result.skipped,
        result.flagged,
        result.failed,
        recall_note,
        " [dry-run]" if dry_run else "",
    )
    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _mark_extracted(
    workflow_id: str,
    *,
    comment_units: Optional[int] = None,
    comment_units_cited: Optional[int] = None,
) -> None:
    """Insert a ``context_extraction_log`` row for *workflow_id*.

    ``comment_units`` / ``comment_units_cited`` carry the per-comment recall
    metric (migration 018); both stay ``NULL`` when the Reflector never ran
    (skip/flag verdicts). Uses an upsert so a re-invocation with
    ``--workflow-id`` refreshes the metric instead of raising on a duplicate.
    """
    now = datetime.now(UTC).isoformat()
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO context_extraction_log"
            " (workflow_id, extracted_at, comment_units, comment_units_cited)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(workflow_id) DO UPDATE SET"
            "   extracted_at = excluded.extracted_at,"
            "   comment_units = excluded.comment_units,"
            "   comment_units_cited = excluded.comment_units_cited",
            (workflow_id, now, comment_units, comment_units_cited),
        )
        conn.commit()
    finally:
        conn.close()


def _write_pipeline_failure(workflow_id: str, exc: Exception) -> None:
    """Append a ``learning_pipeline_failed`` entry to ``audit_log``."""
    conn = get_connection()
    try:
        _create_audit_log(
            conn,
            workflow_id=workflow_id,
            actor="ace",
            action="learning_pipeline_failed",
            reason=str(exc),
        )
        conn.commit()
    finally:
        conn.close()
