"""Unit tests for ace.pipeline.curator — quality gate + dedup + conflict flag.

The Curator does three things (see the module docstring for the full
rationale, and docs/ACE/15-ace-injection-synthesizer.md for why semantic
consolidation moved to read time):

- create: no similar staged item → new row inserted
- merge (exact-dedup safety net): similar staged item found, compatible
  polarity → append a ProvenanceEntry to the existing row; confidence is NOT
  touched
- contradict: similar staged item found, opposing polarity → BOTH rows are
  written as pending staged items with `conflicts_with` populated
  symmetrically. Neither row is blocked with status='conflicted'.
- discard: quality gate strips run-specific references; too short → discarded
- reformulate: quality gate strips reference but keeps useful remainder
- last_validated anchoring: uses bundle.created_at, not extraction date
- pattern_type isolation: items of different pattern_types are never merged
- empty candidates: returns zero CurationResult
"""

from __future__ import annotations

from typing import Any

import pytest

from ace.models import CandidateItem, ContextItem, PatternType, Scope, Status
from ace.pipeline.curator import (
    CurationResult,
    _has_negation_polarity,
    _jaccard,
    _quality_gate,
    _tokenise,
    curate,
)
from ace.pipeline.trace_reader import TraceBundle
from ace.repository.context_item_repository import ContextItemRepository

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def repo() -> ContextItemRepository:
    return ContextItemRepository()


def _bundle(
    *,
    workflow_id: str = "wf-test-1",
    ticket_key: str = "AOS-1",
    created_at: str = "2026-05-01T10:00:00Z",
) -> TraceBundle:
    return TraceBundle(
        workflow_id=workflow_id,
        ticket_key=ticket_key,
        status="completed",
        created_at=created_at,
        work_plan={"status": "pass"},
        code_generation_summary={"status": "success"},
        clarification_history=[],
        pr_comments=[],
        rejection_reason=None,
    )


def _candidate(
    description: str = "Always run migrations before deploying code changes.",
    *,
    pattern_type: PatternType = "approach",
    scope: Scope = "codebase_wide",
    initial_confidence: float = 0.6,
    evidence: list[dict[str, Any]] | None = None,
    scope_value: str | None = None,
    project: str | None = None,
    repo: str | None = None,
    platform: str | None = None,
) -> CandidateItem:
    return CandidateItem(
        pattern_type=pattern_type,
        scope=scope,
        scope_value=scope_value,
        description=description,
        initial_confidence=initial_confidence,
        evidence=evidence or [],
        project=project,
        repo=repo,
        platform=platform,
    )


def _staged_item(
    item_id: str,
    description: str,
    *,
    pattern_type: PatternType = "approach",
    scope: Scope = "codebase_wide",
    confidence: float = 0.6,
    status: Status = "staged",
) -> ContextItem:
    return ContextItem(
        id=item_id,
        pattern_type=pattern_type,
        scope=scope,
        description=description,
        confidence=confidence,
        last_validated="2026-04-01T00:00:00Z",
        created_at="2026-04-01T00:00:00Z",
        updated_at="2026-04-01T00:00:00Z",
        status=status,
        provenance=[],
    )


# ---------------------------------------------------------------------------
# Unit tests: pure helper functions
# ---------------------------------------------------------------------------


def test_tokenise_removes_stop_words():
    tokens = _tokenise("Run the migrations and deploying code changes")
    assert "the" not in tokens
    assert "and" not in tokens
    assert "run" in tokens
    assert "migrations" in tokens
    assert "deploying" in tokens


def test_tokenise_lowercases():
    assert _tokenise("Always Use SQLite") == _tokenise("always use sqlite")


def test_jaccard_identical_sets():
    a = frozenset(["a", "b", "c"])
    assert _jaccard(a, a) == 1.0


def test_jaccard_disjoint_sets():
    assert _jaccard(frozenset(["a"]), frozenset(["b"])) == 0.0


def test_jaccard_empty_sets_returns_one():
    assert _jaccard(frozenset(), frozenset()) == 1.0


def test_jaccard_one_empty_returns_zero():
    assert _jaccard(frozenset(["a"]), frozenset()) == 0.0


def test_has_negation_polarity_true():
    assert _has_negation_polarity("Never use inline ALTER TABLE in SQLite")
    assert _has_negation_polarity("Avoid modifying existing migrations")
    assert _has_negation_polarity("Do not run tests without a fixture")


def test_has_negation_polarity_false():
    assert not _has_negation_polarity("Always run migrations before deploying")
    assert not _has_negation_polarity("Use additive migrations for SQLite schema changes")


# ---------------------------------------------------------------------------
# Quality gate tests
# ---------------------------------------------------------------------------


def test_quality_gate_strips_ticket_key():
    desc = "When working on AOS-226, always run migrations first."
    cleaned = _quality_gate(desc)
    assert cleaned is not None
    assert "AOS-226" not in cleaned
    assert "migrations" in cleaned


def test_quality_gate_strips_feature_branch():
    desc = "Branch feature/my-feature required a migration before merging."
    cleaned = _quality_gate(desc)
    assert cleaned is not None
    assert "feature/" not in cleaned


def test_quality_gate_strips_commit_hash():
    desc = "Commit abc1234def was the root cause of the migration failure."
    cleaned = _quality_gate(desc)
    assert cleaned is not None
    assert "abc1234def" not in cleaned


def test_quality_gate_discards_when_too_short_after_stripping():
    # After removing the ticket key, only whitespace/short text remains.
    result = _quality_gate("AOS-226.")
    assert result is None


def test_quality_gate_preserves_generalisable_description():
    desc = "Always run migrations before deploying code changes."
    cleaned = _quality_gate(desc)
    assert cleaned == desc


# ---------------------------------------------------------------------------
# Integration tests: curate() against a real (test-isolated) SQLite DB
# ---------------------------------------------------------------------------


def test_empty_candidates_returns_zero_result(repo):
    result = curate([], _bundle(), repo=repo)
    assert result == CurationResult(created=0, merged=0, contradicted=0, discarded=0)


def test_create_new_staged_item_when_no_similar_exists(repo):
    """No existing staged items → new row created in context_items_staged."""
    candidate = _candidate("Always run migrations before deploying code changes.")
    result = curate([candidate], _bundle(), repo=repo)

    assert result.created == 1
    assert result.merged == 0
    assert result.contradicted == 0
    assert result.discarded == 0

    staged = repo.list_staged()
    assert len(staged) == 1
    assert staged[0].description == candidate.description
    assert staged[0].status == "staged"
    assert staged[0].conflicts_with == []
    assert staged[0].evidence_count == 1


def test_created_item_confidence_equals_initial_confidence(repo):
    confidence = 0.72
    candidate = _candidate(
        "Always run migrations before deploying code changes.", initial_confidence=confidence
    )
    curate([candidate], _bundle(), repo=repo)

    staged = repo.list_staged()
    assert staged[0].confidence == pytest.approx(confidence)


def test_last_validated_uses_bundle_created_at_not_extraction_date(repo):
    """last_validated must be the *source* workflow date, not the extraction date."""
    source_date = "2025-12-25T08:00:00Z"
    candidate = _candidate("Use additive migrations for SQLite schema changes.")
    curate([candidate], _bundle(created_at=source_date), repo=repo)

    staged = repo.list_staged()
    assert staged[0].last_validated == source_date


def test_created_item_provenance_uses_bundle_date(repo):
    source_date = "2026-03-10T12:00:00Z"
    candidate = _candidate(
        "Prefer small, focused PRs for easier review.",
        evidence=[{"signal_source": "pr_comment", "detail": "reviewer asked for smaller PRs"}],
    )
    curate([candidate], _bundle(created_at=source_date, workflow_id="wf-42"), repo=repo)

    staged = repo.list_staged()
    assert len(staged[0].provenance) == 1
    prov = staged[0].provenance[0]
    assert prov.workflow_date == source_date
    assert prov.workflow_id == "wf-42"
    assert prov.signal_source == "pr_comment"


def test_merge_appends_provenance_without_touching_confidence(repo):
    """Existing staged item with similar description → provenance appended,
    confidence unchanged (no weighted-mean recomputation)."""
    existing = _staged_item(
        "item-1",
        "Always run migrations before deploying code changes to keep schema consistent.",
        confidence=0.6,
    )
    repo.create_staged(existing)

    candidate = _candidate(
        "Run database migrations before deploying new code to keep schema consistent.",
        initial_confidence=0.7,
    )
    result = curate([candidate], _bundle(), repo=repo)

    assert result.merged == 1
    assert result.created == 0
    assert result.contradicted == 0

    updated = repo.get_staged("item-1")
    assert updated is not None
    # Confidence is UNCHANGED — the Curator no longer recomputes it.
    assert updated.confidence == pytest.approx(0.6)
    # But provenance grows by one entry (the new evidence event).
    assert len(updated.provenance) == 1
    assert updated.evidence_count == 1
    # No new row was created — still one staged row total.
    assert len(repo.list_staged()) == 1


def test_merge_provenance_entry_has_correct_bundle_date(repo):
    existing = _staged_item(
        "item-2",
        "Always run migrations before deploying code changes to keep schema consistent.",
        confidence=0.5,
    )
    repo.create_staged(existing)

    candidate = _candidate(
        "Run database migrations before deploying new code to keep schema consistent.",
        initial_confidence=0.6,
    )
    source_date = "2026-06-01T00:00:00Z"
    curate([candidate], _bundle(created_at=source_date), repo=repo)

    updated = repo.get_staged("item-2")
    assert updated is not None
    assert updated.provenance[0].workflow_date == source_date


def test_contradict_writes_both_rows_with_symmetric_conflicts_with(repo):
    """Similar items with opposing polarity → BOTH rows written as pending
    staged items, conflicts_with populated symmetrically. Neither row is
    blocked with status='conflicted'."""
    existing = _staged_item(
        "item-3",
        "Run migrations before deploying code changes to production.",
        confidence=0.7,
    )
    repo.create_staged(existing)

    # Same core subject but opposing primary directive (never vs affirmative).
    # Jaccard of these two token sets is well above MERGE_THRESHOLD.
    candidate = _candidate(
        "Never run migrations before deploying code changes to production.",
        initial_confidence=0.5,
    )
    result = curate([candidate], _bundle(), repo=repo)

    assert result.contradicted == 1
    assert result.created == 0
    assert result.merged == 0

    # Existing row stays pending — no more status='conflicted' blocking.
    existing_after = repo.get_staged("item-3")
    assert existing_after is not None
    assert existing_after.status == "staged"
    assert existing_after.promoted_at is None
    assert existing_after.rejected_at is None

    # A new pending staged row was written for the candidate (previously it
    # was silently discarded — that was a latent bug).
    all_pending = repo.list_staged(pending_only=True)
    assert {i.id for i in all_pending} >= {"item-3"}
    assert len(all_pending) == 2
    new_row = next(i for i in all_pending if i.id != "item-3")
    assert new_row.status == "staged"
    assert new_row.description == candidate.description

    # conflicts_with is populated symmetrically on both rows.
    assert existing_after.conflicts_with == [new_row.id]
    assert new_row.conflicts_with == ["item-3"]


def test_quality_gate_discards_run_specific_candidate(repo):
    """Candidate whose description collapses below the minimum length → discarded."""
    # After stripping the ticket key the remainder is too short to be useful.
    candidate = _candidate("Fix for AOS-226.")
    result = curate([candidate], _bundle(), repo=repo)

    assert result.discarded == 1
    assert result.created == 0
    assert len(repo.list_staged()) == 0


def test_quality_gate_reformulates_and_creates(repo):
    """Quality gate strips ticket key but preserves generalisable content."""
    candidate = _candidate(
        "In AOS-226 we learned that migration files must have sequential numeric prefixes.",
        initial_confidence=0.65,
    )
    result = curate([candidate], _bundle(), repo=repo)

    assert result.created == 1
    assert result.discarded == 0
    staged = repo.list_staged()
    assert "AOS-226" not in staged[0].description
    assert "migration" in staged[0].description


def test_different_pattern_types_are_never_merged(repo):
    """Items with different pattern_type are orthogonal and must not be merged."""
    existing = _staged_item(
        "item-5",
        "Always run migrations before deploying code changes.",
        pattern_type="approach",
    )
    repo.create_staged(existing)

    # Same description text but different pattern_type.
    candidate = _candidate(
        "Always run migrations before deploying code changes.",
        pattern_type="concern",
    )
    result = curate([candidate], _bundle(), repo=repo)

    assert result.created == 1
    assert result.merged == 0
    assert len(repo.list_staged()) == 2


def test_multiple_candidates_mixed_operations(repo):
    """Multiple candidates in one call: each processed independently."""
    existing = _staged_item(
        "item-6",
        "Always run migrations before deploying code changes to keep schema consistent.",
        confidence=0.5,
    )
    repo.create_staged(existing)

    candidates = [
        # Will merge with item-6.
        _candidate(
            "Run database migrations before deploying new code to keep schema consistent.",
            initial_confidence=0.6,
        ),
        # Unrelated → create.
        _candidate(
            "Prefer small focused pull requests to make code review easier.",
            pattern_type="approach",
            initial_confidence=0.55,
        ),
        # Run-specific → discard (short remainder after stripping ticket key).
        _candidate("Fix for AOS-99."),
    ]
    result = curate(candidates, _bundle(), repo=repo)

    assert result.merged == 1
    assert result.created == 1
    assert result.discarded == 1
    assert result.contradicted == 0


# ---------------------------------------------------------------------------
# Applicability dimensions
# ---------------------------------------------------------------------------


def test_created_item_carries_applicability_dimensions(repo):
    """project / repo / platform on the candidate flow through to the staged row."""
    candidate = _candidate(
        "Service protocols should grow additively via structural subtyping.",
        pattern_type="approach",
        platform="python",
        repo="ngb-agent-orchestrator",
    )
    curate([candidate], _bundle(), repo=repo)

    staged = repo.list_staged()
    assert len(staged) == 1
    assert staged[0].platform == "python"
    assert staged[0].repo == "ngb-agent-orchestrator"
    assert staged[0].project is None


def test_created_item_defaults_applicability_dimensions_to_none(repo):
    """A candidate without applicability fields writes NULLs, meaning applies everywhere."""
    candidate = _candidate("Always run migrations before deploying code changes.")
    curate([candidate], _bundle(), repo=repo)

    staged = repo.list_staged()
    assert staged[0].project is None
    assert staged[0].repo is None
    assert staged[0].platform is None
