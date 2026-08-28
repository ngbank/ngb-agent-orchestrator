"""Node: run_learning_pipeline — tail node that mines the just-finished workflow.

Invoked on every terminal path of the orchestrator graph (approved / rejected /
failed) so the ACE learning pipeline (Evaluator → Reflector → Curator) runs
automatically the moment a workflow reaches a terminal status.

The whole node body is wrapped in a broad ``try/except``. A pipeline exception
here must never propagate: the workflow's terminal status (already persisted by
the upstream node) is the contract with the caller. Failures are logged and, in
addition, ``run_mining`` itself writes a ``learning_pipeline_failed`` audit entry
so the row is picked up on the next ``ace mine`` sweep.
"""

from __future__ import annotations

import logging

from orchestrator.state import OrchestratorState

logger = logging.getLogger(__name__)


def run_learning_pipeline(state: OrchestratorState) -> dict:
    """Kick off the ACE learning pipeline for the current workflow.

    Returns an empty dict — this node never mutates orchestrator state; the
    workflow's terminal status is owned by the upstream node.
    """
    workflow_id = state.get("workflow_id")
    if not workflow_id:
        logger.debug(
            "run_learning_pipeline: no workflow_id in state; skipping (fresh run "
            "that failed before persistence)."
        )
        return {}

    try:
        # Imported lazily so orchestrator startup doesn't pay the ACE import cost
        # and so tests can patch the runner without a top-level import cycle.
        from ace.pipeline.runner import run_mining

        result = run_mining(workflow_id=workflow_id)
        logger.info(
            "run_learning_pipeline: workflow=%s processed=%d succeeded=%d "
            "skipped=%d flagged=%d failed=%d",
            workflow_id,
            result.processed,
            result.succeeded,
            result.skipped,
            result.flagged,
            result.failed,
        )
    except Exception:
        logger.exception(
            "run_learning_pipeline: mining failed for workflow %s; "
            "workflow terminal status is unaffected.",
            workflow_id,
        )

    return {}
