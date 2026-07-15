"""In-process :class:`AgentContextEngineService` implementation.

Wraps :func:`ace.pipeline.runner.run_mining` and the shared
:class:`ContextItemRepository` singleton, translating between the pipeline's
native :class:`~ace.pipeline.runner.RunnerResult` /
:class:`~ace.pipeline.curator.CurationResult` types and the frozen DTOs on the
service boundary.
"""

from __future__ import annotations

import statistics
from datetime import UTC, datetime
from typing import Optional

from ace.config import confidence_to_tier, tier_to_confidence_range
from ace.pipeline.runner import RunnerResult, run_mining
from ace.repository.context_item_repository import ContextItemRepository, ContextStoreRawStats

from .dtos import (
    ItemSummaryDTO,
    ListItemsRequest,
    ListItemsResult,
    MineRequest,
    MineResult,
    PromoteRequest,
    PromoteResult,
    ProvenanceEntryDTO,
    RejectRequest,
    RejectResult,
    ShowItemRequest,
    ShowItemResult,
    StatsResult,
)


class LocalAgentContextEngineService:
    """Runs the ACE mining pipeline in-process."""

    def __init__(self, repo: Optional[ContextItemRepository] = None) -> None:
        self._repo = repo or ContextItemRepository()

    def mine(self, request: MineRequest) -> MineResult:
        result = run_mining(
            limit=request.limit,
            dry_run=request.dry_run,
            workflow_id=request.workflow_id,
        )
        return _to_mine_result(result)

    def list_items(self, request: ListItemsRequest) -> ListItemsResult:
        if request.status == "staged":
            raw = self._repo.list_staged()
        else:
            min_confidence: Optional[float] = None
            if request.confidence_tier is not None:
                lo, _ = tier_to_confidence_range(request.confidence_tier)  # type: ignore[arg-type]
                min_confidence = lo
            raw = self._repo.list_items(
                pattern_type=request.pattern_type,
                scope=request.scope,
                status=request.status,
                min_confidence=min_confidence,
            )

        items = []
        for item in raw:
            tier = confidence_to_tier(item.confidence)
            if request.confidence_tier is not None and tier != request.confidence_tier:
                continue
            if request.status == "staged" and request.pattern_type is not None:
                if item.pattern_type != request.pattern_type:
                    continue
            if request.status == "staged" and request.scope is not None:
                if item.scope != request.scope:
                    continue
            items.append(
                ItemSummaryDTO(
                    id=item.id,
                    pattern_type=item.pattern_type,
                    scope=item.scope,
                    scope_value=item.scope_value,
                    description=item.description,
                    confidence=item.confidence,
                    confidence_tier=tier,
                    status=item.status,
                    last_validated=item.last_validated,
                )
            )
        return ListItemsResult(items=tuple(items))

    def show_item(self, request: ShowItemRequest) -> Optional[ShowItemResult]:
        item = self._repo.get(request.item_id) or self._repo.get_staged(request.item_id)
        if item is None:
            return None
        provenance = tuple(
            ProvenanceEntryDTO(
                signal_source=e.signal_source,
                workflow_date=e.workflow_date,
                contributed_confidence=e.contributed_confidence,
                workflow_id=e.workflow_id,
                ticket_key=e.ticket_key,
                signal_detail=e.signal_detail,
            )
            for e in item.provenance
        )
        return ShowItemResult(
            id=item.id,
            pattern_type=item.pattern_type,
            scope=item.scope,
            scope_value=item.scope_value,
            description=item.description,
            confidence=item.confidence,
            confidence_tier=confidence_to_tier(item.confidence),
            status=item.status,
            last_validated=item.last_validated,
            created_at=item.created_at,
            updated_at=item.updated_at,
            provenance=provenance,
            conflicts_with=tuple(item.conflicts_with),
            project=item.project,
            repo=item.repo,
            platform=item.platform,
        )

    def promote(self, request: PromoteRequest) -> PromoteResult:
        item_id = self._repo.promote(
            request.item_id,
            review_notes=request.notes,
            scope=request.scope,  # type: ignore[arg-type]
            scope_value=request.scope_value,
        )
        return PromoteResult(item_id=item_id)

    def reject(self, request: RejectRequest) -> RejectResult:
        self._repo.reject(request.item_id, review_notes=request.notes)
        return RejectResult(item_id=request.item_id)

    def stats(self) -> StatsResult:
        raw = self._repo.get_stats()
        return _to_stats_result(raw)


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


def _to_stats_result(raw: ContextStoreRawStats) -> StatsResult:
    """Translate repository raw stats into the frozen :class:`StatsResult` DTO."""
    # --- by_status ---
    by_status = tuple(sorted(raw.status_counts, key=lambda x: x[1], reverse=True))

    # --- by_tier ---
    tier_buckets: dict[str, int] = {}
    for conf in raw.live_confidence_values:
        tier = confidence_to_tier(conf) or "BELOW_THRESHOLD"
        tier_buckets[tier] = tier_buckets.get(tier, 0) + 1
    tier_order = ["ESTABLISHED", "PATTERN", "TENTATIVE", "BELOW_THRESHOLD"]
    by_tier = tuple((t, tier_buckets[t]) for t in tier_order if t in tier_buckets)

    # --- by_pattern_type ---
    by_pattern_type = tuple(sorted(raw.pattern_type_counts, key=lambda x: x[1], reverse=True))

    # --- staging queue age ---
    staged_pending = len(raw.staged_pending_created_at)
    if staged_pending:
        now = datetime.now(UTC)
        ages = [
            (now - datetime.fromisoformat(ts)).total_seconds() / 86400
            for ts in raw.staged_pending_created_at
        ]
        staged_queue_age_days_p50: Optional[float] = statistics.median(ages)
        staged_queue_age_days_max: Optional[float] = max(ages)
    else:
        staged_queue_age_days_p50 = None
        staged_queue_age_days_max = None

    # --- generation rate ---
    generation_rate: Optional[float] = (
        raw.staged_total / raw.mined_workflows if raw.mined_workflows > 0 else None
    )

    return StatsResult(
        by_status=by_status,
        by_tier=by_tier,
        by_pattern_type=by_pattern_type,
        staged_pending=staged_pending,
        staged_queue_age_days_p50=staged_queue_age_days_p50,
        staged_queue_age_days_max=staged_queue_age_days_max,
        mined_workflows=raw.mined_workflows,
        generation_rate=generation_rate,
    )


def build_local_agent_context_engine_service() -> LocalAgentContextEngineService:
    """Return a :class:`LocalAgentContextEngineService` wired with defaults.

    The service owns a :class:`ContextItemRepository` instance and delegates to
    the mining runner.  This factory gives callers a stable construction point
    and mirrors :func:`orchestrator.workflow_service.build_local_workflow_service`.
    """
    return LocalAgentContextEngineService()
