"""In-process :class:`AgentContextEngineService` implementation.

Wraps :func:`ace.pipeline.runner.run_mining` and the shared
:class:`ContextItemRepository` singleton, translating between the pipeline's
native :class:`~ace.pipeline.runner.RunnerResult` /
:class:`~ace.pipeline.curator.CurationResult` types and the frozen DTOs on the
service boundary.
"""

from __future__ import annotations

from ace.pipeline.runner import RunnerResult, run_mining

from .dtos import MineRequest, MineResult


class LocalAgentContextEngineService:
    """Runs the ACE mining pipeline in-process."""

    def mine(self, request: MineRequest) -> MineResult:
        result = run_mining(
            limit=request.limit,
            dry_run=request.dry_run,
            workflow_id=request.workflow_id,
        )
        return _to_mine_result(result)


def _to_mine_result(result: RunnerResult) -> MineResult:
    """Flatten a :class:`RunnerResult` into the immutable :class:`MineResult`."""
    return MineResult(
        processed=result.processed,
        succeeded=result.succeeded,
        skipped=result.skipped,
        flagged=result.flagged,
        failed=result.failed,
        dry_run=result.dry_run,
        created=result.curation.created,
        merged=result.curation.merged,
        contradicted=result.curation.contradicted,
        discarded=result.curation.discarded,
        comment_units=result.comment_units,
        comment_units_cited=result.comment_units_cited,
    )


def build_local_agent_context_engine_service() -> LocalAgentContextEngineService:
    """Return a :class:`LocalAgentContextEngineService` wired with defaults.

    The mining runner owns its own :class:`ContextItemRepository` instance —
    this factory exists to give callers a stable construction point and to
    parallel :func:`orchestrator.workflow_service.build_local_workflow_service`.
    """
    return LocalAgentContextEngineService()
