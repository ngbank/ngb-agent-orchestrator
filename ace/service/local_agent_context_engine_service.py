"""LocalAgentContextEngineService — in-process implementation.

Wraps ``ContextItemRepository`` and the mining runner from
``ace.pipeline.runner``.  This is the default implementation used by the
``ace`` CLI in local mode.
"""

from __future__ import annotations

from typing import Optional

from ace.repository.context_item_repository import ContextItemRepository

from .protocols import AgentContextEngineService, MiningResult


def _run_mining(*, limit, dry_run, workflow_id):
    """Lazy import wrapper so tests can patch this symbol."""
    from ace.pipeline.runner import run_mining

    return run_mining(limit=limit, dry_run=dry_run, workflow_id=workflow_id)


class LocalAgentContextEngineService:
    """In-process ACE service backed by SQLite."""

    def __init__(
        self,
        *,
        repo: Optional[ContextItemRepository] = None,
    ) -> None:
        self._repo = repo or ContextItemRepository()

    def mine(
        self,
        *,
        limit: Optional[int] = None,
        dry_run: bool = False,
        workflow_id: Optional[str] = None,
    ) -> MiningResult:
        """Run the offline mining pipeline."""
        runner_result = _run_mining(
            limit=limit,
            dry_run=dry_run,
            workflow_id=workflow_id,
        )
        return MiningResult(
            processed=runner_result.processed,
            succeeded=runner_result.succeeded,
            skipped=runner_result.skipped,
            flagged=runner_result.flagged,
            failed=runner_result.failed,
            dry_run=runner_result.dry_run,
            created=runner_result.curation.created,
            merged=runner_result.curation.merged,
            contradicted=runner_result.curation.contradicted,
            discarded=runner_result.curation.discarded,
        )


# Satisfy the Protocol at runtime for isinstance checks.
AgentContextEngineService.register(LocalAgentContextEngineService)
