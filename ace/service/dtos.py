"""DTOs exchanged across the :class:`AgentContextEngineService` boundary.

All types are frozen dataclasses so callers cannot mutate returned values and
so no ``ace.pipeline`` internals leak through the seam.  A future
``RemoteAgentContextEngineService`` will marshal these to/from JSON — keep
them primitive.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class MineRequest:
    """Inputs for :meth:`AgentContextEngineService.mine`.

    Fields mirror the flags exposed by the ``ace mine`` CLI verb and
    :func:`ace.pipeline.runner.run_mining`.
    """

    limit: Optional[int] = None
    """Cap the number of eligible workflows fetched.  Ignored when
    *workflow_id* is set."""

    dry_run: bool = False
    """When ``True``, evaluate and reflect but skip all DB writes."""

    workflow_id: Optional[str] = None
    """Process only this specific workflow.  Bypasses the
    ``context_extraction_log`` anti-join so already-processed workflows can be
    re-mined after a previous failure."""


@dataclass(frozen=True)
class MineResult:
    """Aggregated outcome of a :meth:`AgentContextEngineService.mine` call.

    Curator counts are flattened onto this DTO rather than nesting
    :class:`ace.pipeline.curator.CurationResult`, so pipeline types do not
    leak across the service boundary.
    """

    processed: int
    """Total workflows attempted (including failures)."""

    succeeded: int
    """Workflows that completed the pipeline without exception."""

    skipped: int
    """Workflows whose Evaluator verdict was ``skip``."""

    flagged: int
    """Workflows whose Evaluator verdict was ``flag``."""

    failed: int
    """Workflows where an exception occurred during pipeline execution."""

    dry_run: bool
    """Whether DB writes were suppressed."""

    created: int
    """Staged rows created by the Curator across all ``proceed`` workflows."""

    merged: int
    """Existing staged rows that gained a provenance entry via exact-dedup."""

    contradicted: int
    """New staged rows whose Curator populated ``conflicts_with``."""

    discarded: int
    """Candidates dropped by the Curator quality gate."""

    comment_units: int
    """Total PR-comment units shown to the Reflector across ``proceed`` workflows."""

    comment_units_cited: int
    """Distinct PR-comment units cited in candidate evidence (recall numerator)."""


# ---------------------------------------------------------------------------
# ace items list
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ListItemsRequest:
    """Inputs for :meth:`AgentContextEngineService.list_items`."""

    status: Optional[str] = None
    """Filter by item status (``active``, ``staged``, ``deprecated``, ``conflicted``).
    When ``staged``, the staging table is queried instead of the live table."""

    pattern_type: Optional[str] = None
    """Filter by pattern type (``approach``, ``concern``, ``test_coverage``, ``implementation``)."""

    scope: Optional[str] = None
    """Filter by scope dimension (``task_type``, ``file_pattern``, ``codebase_wide``)."""

    confidence_tier: Optional[str] = None
    """Filter by named confidence tier (``ESTABLISHED``, ``PATTERN``, ``TENTATIVE``).
    Translates to an exact confidence band — items outside the tier are excluded."""


@dataclass(frozen=True)
class ItemSummaryDTO:
    """Compact view of one context item, suitable for tabular list output."""

    id: str
    pattern_type: str
    scope: str
    scope_value: Optional[str]
    description: str
    confidence: float
    confidence_tier: Optional[str]
    status: str
    last_validated: str


@dataclass(frozen=True)
class ListItemsResult:
    """Outcome of :meth:`AgentContextEngineService.list_items`."""

    items: Tuple[ItemSummaryDTO, ...]


# ---------------------------------------------------------------------------
# ace items show
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ShowItemRequest:
    """Inputs for :meth:`AgentContextEngineService.show_item`."""

    item_id: str
    """The UUID of the item to retrieve."""


@dataclass(frozen=True)
class ProvenanceEntryDTO:
    """One evidence event in a context item's provenance chain."""

    signal_source: str
    workflow_date: str
    contributed_confidence: float
    workflow_id: Optional[str]
    ticket_key: Optional[str]
    signal_detail: Optional[str]


@dataclass(frozen=True)
class ShowItemResult:
    """Full detail for one context item, including its provenance chain."""

    id: str
    pattern_type: str
    scope: str
    scope_value: Optional[str]
    description: str
    confidence: float
    confidence_tier: Optional[str]
    status: str
    last_validated: str
    created_at: str
    updated_at: str
    provenance: Tuple[ProvenanceEntryDTO, ...]
    conflicts_with: Tuple[str, ...]
    project: Optional[str]
    repo: Optional[str]
    platform: Optional[str]


# ---------------------------------------------------------------------------
# ace promote
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PromoteRequest:
    """Inputs for :meth:`AgentContextEngineService.promote`."""

    item_id: str
    """The UUID of the staged item to promote."""

    notes: Optional[str] = None
    """Optional reviewer annotations stored in ``review_notes``."""

    scope: Optional[str] = None
    """Narrow the scope dimension at promotion time (overrides the staged value)."""

    scope_value: Optional[str] = None
    """Narrow the scope value at promotion time (overrides the staged value)."""


@dataclass(frozen=True)
class PromoteResult:
    """Outcome of :meth:`AgentContextEngineService.promote`."""

    item_id: str
    """The id of the newly created live context item (same UUID as the staged row)."""


# ---------------------------------------------------------------------------
# ace reject
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RejectRequest:
    """Inputs for :meth:`AgentContextEngineService.reject`."""

    item_id: str
    """The UUID of the staged item to reject."""

    notes: Optional[str] = None
    """Optional reviewer annotations stored in ``review_notes``."""


@dataclass(frozen=True)
class RejectResult:
    """Outcome of :meth:`AgentContextEngineService.reject`."""

    item_id: str
    """The id of the rejected staged item."""
