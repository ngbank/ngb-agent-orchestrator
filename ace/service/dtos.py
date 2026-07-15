"""DTOs exchanged across the :class:`AgentContextEngineService` boundary.

All types are frozen dataclasses so callers cannot mutate returned values and
so no ``ace.pipeline`` internals leak through the seam.  A future
``RemoteAgentContextEngineService`` will marshal these to/from JSON — keep
them primitive.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


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
