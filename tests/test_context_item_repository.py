"""Unit tests for ace.models and ace.repository.context_item_repository.

Uses the session/function autouse fixtures from tests/conftest.py, which point
DB_PATH at a fresh tmp_path SQLite file and run migrations (including 014,
which creates context_items / context_items_staged) before every test.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from ace.models import CandidateItem, ContextItem, ProvenanceEntry
from ace.repository.context_item_repository import (
    PROMOTION_CONFIDENCE_BOOST,
    ContextItemRepository,
)


@pytest.fixture
def repo() -> ContextItemRepository:
    return ContextItemRepository()


def _make_item(item_id: str = "item-1", **overrides: Any) -> ContextItem:
    base = ContextItem(
        id=item_id,
        pattern_type="approach",
        scope="file_pattern",
        scope_value="state/migrations/**",
        description="Use additive migrations, never in-place ALTER for SQLite.",
        confidence=0.6,
        last_validated="2026-05-15T14:32:00Z",
        created_at="2026-05-15T14:32:00Z",
        updated_at="2026-05-15T14:32:00Z",
        status="active",
        provenance=[
            ProvenanceEntry(
                workflow_id="wf-1",
                ticket_key="AOS-41",
                signal_source="clarification_round_1",
                signal_detail="Reviewer corrected inline ALTER TABLE",
                workflow_date="2026-05-15T14:32:00Z",
                contributed_confidence=0.15,
            )
        ],
    )
    return dataclasses.replace(base, **overrides) if overrides else base


# ---------------------------------------------------------------------------
# ProvenanceEntry / ContextItem serialization
# ---------------------------------------------------------------------------


def test_provenance_entry_round_trip():
    entry = ProvenanceEntry(
        workflow_id="wf-1",
        ticket_key="AOS-41",
        signal_source="pr_rejection",
        signal_detail="reviewer flagged missing index",
        workflow_date="2026-06-01T00:00:00Z",
        contributed_confidence=0.1,
    )
    assert ProvenanceEntry.from_dict(entry.to_dict()) == entry


def test_candidate_item_defaults():
    candidate = CandidateItem(
        pattern_type="concern",
        scope="codebase_wide",
        description="Reviewer flagged missing test coverage on state transitions.",
        initial_confidence=0.5,
    )
    assert candidate.evidence == []
    assert candidate.scope_value is None
    assert candidate.suggested_tier is None


# ---------------------------------------------------------------------------
# Live store: create / get / list / update_confidence / append_provenance / set_status
# ---------------------------------------------------------------------------


def test_create_and_get_round_trips_item_with_provenance(repo):
    item = _make_item()
    returned_id = repo.create(item)
    assert returned_id == "item-1"

    fetched = repo.get("item-1")
    assert fetched is not None
    assert fetched.description == item.description
    assert fetched.confidence == 0.6
    assert fetched.status == "active"
    assert len(fetched.provenance) == 1
    assert fetched.provenance[0] == item.provenance[0]
    # Live rows have no staging fields.
    assert fetched.review_notes is None
    assert fetched.promoted_at is None
    assert fetched.rejected_at is None


def test_get_missing_item_returns_none(repo):
    assert repo.get("does-not-exist") is None


def test_list_filters_by_pattern_type_scope_status_and_confidence(repo):
    repo.create(_make_item("a", pattern_type="approach", confidence=0.9, status="active"))
    repo.create(_make_item("b", pattern_type="concern", confidence=0.3, status="active"))
    repo.create(_make_item("c", pattern_type="approach", confidence=0.9, status="deprecated"))

    approaches = repo.list_items(pattern_type="approach")
    assert {i.id for i in approaches} == {"a", "c"}

    active_only = repo.list_items(status="active")
    assert {i.id for i in active_only} == {"a", "b"}

    high_confidence = repo.list_items(min_confidence=0.5)
    assert {i.id for i in high_confidence} == {"a", "c"}

    combined = repo.list_items(pattern_type="approach", status="active", min_confidence=0.5)
    assert [i.id for i in combined] == ["a"]


def test_list_orders_by_confidence_descending(repo):
    repo.create(_make_item("low", confidence=0.2))
    repo.create(_make_item("high", confidence=0.95))
    repo.create(_make_item("mid", confidence=0.5))

    ordered = [i.id for i in repo.list_items()]
    assert ordered == ["high", "mid", "low"]


def test_update_confidence_persists_new_value(repo):
    repo.create(_make_item(confidence=0.5))
    repo.update_confidence("item-1", 0.8)

    fetched = repo.get("item-1")
    assert fetched.confidence == 0.8


def test_append_provenance_adds_entry_without_dropping_existing(repo):
    repo.create(_make_item())

    new_entry = ProvenanceEntry(
        workflow_id="wf-2",
        signal_source="execution_outcome",
        signal_detail="Reinforced by a second run",
        workflow_date="2026-06-10T00:00:00Z",
        contributed_confidence=0.1,
    )
    repo.append_provenance("item-1", new_entry)

    fetched = repo.get("item-1")
    assert len(fetched.provenance) == 2
    assert fetched.provenance[0].signal_source == "clarification_round_1"
    assert fetched.provenance[1] == new_entry


def test_append_provenance_noop_for_missing_item(repo):
    # Should not raise.
    entry = ProvenanceEntry(
        signal_source="execution_outcome",
        workflow_date="2026-06-10T00:00:00Z",
        contributed_confidence=0.1,
    )
    repo.append_provenance("does-not-exist", entry)


def test_set_status_transitions_and_preserves_row(repo):
    repo.create(_make_item(status="active"))
    repo.set_status("item-1", "deprecated")

    fetched = repo.get("item-1")
    assert fetched.status == "deprecated"
    assert fetched.description == "Use additive migrations, never in-place ALTER for SQLite."


# ---------------------------------------------------------------------------
# Staging: create_staged / get_staged / list_staged / promote / reject
# ---------------------------------------------------------------------------


def _make_staged_item(item_id: str = "staged-1", **overrides: Any) -> ContextItem:
    base = ContextItem(
        id=item_id,
        pattern_type="concern",
        scope="codebase_wide",
        scope_value=None,
        description="Reviewer flagged missing test coverage on state transitions.",
        confidence=0.55,
        last_validated="2026-06-01T00:00:00Z",
        created_at="2026-06-01T00:00:00Z",
        updated_at="2026-06-01T00:00:00Z",
        status="staged",
        provenance=[],
    )
    return dataclasses.replace(base, **overrides) if overrides else base


def test_create_staged_and_get_staged_round_trip(repo):
    repo.create_staged(_make_staged_item())

    fetched = repo.get_staged("staged-1")
    assert fetched is not None
    assert fetched.status == "staged"
    assert fetched.promoted_at is None
    assert fetched.rejected_at is None


def test_list_staged_pending_only_excludes_promoted_and_rejected(repo):
    repo.create_staged(_make_staged_item("pending"))
    repo.create_staged(_make_staged_item("to-promote"))
    repo.create_staged(_make_staged_item("to-reject"))

    repo.promote("to-promote")
    repo.reject("to-reject")

    pending = repo.list_staged(pending_only=True)
    assert {i.id for i in pending} == {"pending"}

    everything = repo.list_staged(pending_only=False)
    assert {i.id for i in everything} == {"pending", "to-promote", "to-reject"}


def test_update_staged_confidence(repo):
    repo.create_staged(_make_staged_item(confidence=0.4))
    repo.update_staged_confidence("staged-1", 0.6)

    fetched = repo.get_staged("staged-1")
    assert fetched.confidence == 0.6


def test_append_staged_provenance(repo):
    repo.create_staged(_make_staged_item())
    entry = ProvenanceEntry(
        signal_source="clarification_round_2",
        signal_detail="Second reviewer agreed",
        workflow_date="2026-06-02T00:00:00Z",
        contributed_confidence=0.1,
    )
    repo.append_staged_provenance("staged-1", entry)

    fetched = repo.get_staged("staged-1")
    assert len(fetched.provenance) == 1
    assert fetched.provenance[0] == entry


def test_promote_moves_item_to_live_store_with_boosted_confidence(repo):
    repo.create_staged(_make_staged_item(confidence=0.55))

    new_id = repo.promote("staged-1")
    assert new_id == "staged-1"

    live = repo.get("staged-1")
    assert live is not None
    assert live.status == "active"
    assert live.confidence == pytest.approx(0.55 + PROMOTION_CONFIDENCE_BOOST)
    assert len(live.provenance) == 1
    assert live.provenance[0].signal_source == "human_review"

    # Staged row is preserved for audit, status stays 'staged'.
    staged = repo.get_staged("staged-1")
    assert staged.status == "staged"
    assert staged.promoted_at is not None
    assert staged.rejected_at is None


def test_promote_caps_confidence_at_one(repo):
    repo.create_staged(_make_staged_item(confidence=0.9))

    repo.promote("staged-1")

    live = repo.get("staged-1")
    assert live.confidence == 1.0


def test_promote_allows_scope_narrowing(repo):
    repo.create_staged(_make_staged_item(scope="codebase_wide", scope_value=None))

    repo.promote(
        "staged-1",
        scope="file_pattern",
        scope_value="state/migrations/**",
        review_notes="Narrowed to migrations only per reviewer",
    )

    live = repo.get("staged-1")
    assert live.scope == "file_pattern"
    assert live.scope_value == "state/migrations/**"

    staged = repo.get_staged("staged-1")
    assert staged.review_notes == "Narrowed to migrations only per reviewer"


def test_promote_missing_staged_item_raises(repo):
    with pytest.raises(ValueError):
        repo.promote("does-not-exist")


def test_reject_marks_rejected_at_and_preserves_row(repo):
    repo.create_staged(_make_staged_item())

    repo.reject("staged-1", review_notes="Too specific to one ticket")

    staged = repo.get_staged("staged-1")
    assert staged.rejected_at is not None
    assert staged.promoted_at is None
    assert staged.review_notes == "Too specific to one ticket"

    # Rejected items never reach the live store.
    assert repo.get("staged-1") is None


# ---------------------------------------------------------------------------
# Applicability dimensions (AOS-268)
# ---------------------------------------------------------------------------


def test_create_round_trips_applicability_dimensions(repo):
    """project / repo / platform round-trip through the live store."""
    item = _make_item(
        project="AOS",
        repo="ngb-agent-orchestrator",
        platform="python",
    )
    repo.create(item)

    fetched = repo.get(item.id)
    assert fetched is not None
    assert fetched.project == "AOS"
    assert fetched.repo == "ngb-agent-orchestrator"
    assert fetched.platform == "python"


def test_create_defaults_applicability_dimensions_to_none(repo):
    """An item constructed without the new fields reads back as NULL on all three."""
    repo.create(_make_item())

    fetched = repo.get("item-1")
    assert fetched.project is None
    assert fetched.repo is None
    assert fetched.platform is None


def test_create_staged_round_trips_applicability_dimensions(repo):
    item = _make_staged_item(
        project="AOS",
        repo="ngb-agent-orchestrator",
        platform="python",
    )
    repo.create_staged(item)

    fetched = repo.get_staged(item.id)
    assert fetched is not None
    assert fetched.project == "AOS"
    assert fetched.repo == "ngb-agent-orchestrator"
    assert fetched.platform == "python"


def test_promote_preserves_applicability_dimensions(repo):
    """Promotion copies project / repo / platform into the live row."""
    repo.create_staged(
        _make_staged_item(
            project="AOS",
            repo="ngb-agent-orchestrator",
            platform="python",
        )
    )

    repo.promote("staged-1")

    live = repo.get("staged-1")
    assert live is not None
    assert live.project == "AOS"
    assert live.repo == "ngb-agent-orchestrator"
    assert live.platform == "python"


# ---------------------------------------------------------------------------
# conflicts_with + flag_conflict + list_staged_by_pattern_type (AOS-273)
# ---------------------------------------------------------------------------


def test_evidence_count_derives_from_provenance():
    """evidence_count is a derived property, not a stored column (AOS-273)."""
    item = _make_item()  # provenance has one entry
    assert item.evidence_count == 1

    empty = _make_staged_item()  # provenance = []
    assert empty.evidence_count == 0


def test_create_staged_round_trips_empty_conflicts_with(repo):
    """Freshly-staged items default to an empty conflicts_with list."""
    repo.create_staged(_make_staged_item())

    fetched = repo.get_staged("staged-1")
    assert fetched is not None
    assert fetched.conflicts_with == []


def test_create_live_round_trips_conflicts_with(repo):
    """A live item preserves any conflicts_with ids across a round trip."""
    item = _make_item(conflicts_with=["other-a", "other-b"])
    repo.create(item)

    fetched = repo.get(item.id)
    assert fetched is not None
    assert fetched.conflicts_with == ["other-a", "other-b"]


def test_flag_conflict_symmetrically_appends_ids(repo):
    repo.create_staged(_make_staged_item("a"))
    repo.create_staged(_make_staged_item("b"))

    repo.flag_conflict(staged_id="a", other_id="b")

    a = repo.get_staged("a")
    b = repo.get_staged("b")
    assert a is not None and b is not None
    assert a.conflicts_with == ["b"]
    assert b.conflicts_with == ["a"]


def test_flag_conflict_is_idempotent(repo):
    repo.create_staged(_make_staged_item("a"))
    repo.create_staged(_make_staged_item("b"))

    repo.flag_conflict(staged_id="a", other_id="b")
    repo.flag_conflict(staged_id="a", other_id="b")

    assert repo.get_staged("a").conflicts_with == ["b"]
    assert repo.get_staged("b").conflicts_with == ["a"]


def test_flag_conflict_missing_row_is_noop_for_that_side(repo):
    repo.create_staged(_make_staged_item("a"))

    repo.flag_conflict(staged_id="a", other_id="ghost")

    assert repo.get_staged("a").conflicts_with == ["ghost"]


def test_promote_preserves_conflicts_with(repo):
    repo.create_staged(_make_staged_item("a"))
    repo.create_staged(_make_staged_item("b"))
    repo.flag_conflict(staged_id="a", other_id="b")

    repo.promote("a")

    live = repo.get("a")
    assert live is not None
    assert live.conflicts_with == ["b"]


def test_list_staged_by_pattern_type_filters_in_sql(repo):
    repo.create_staged(_make_staged_item("c1", pattern_type="concern"))
    repo.create_staged(_make_staged_item("c2", pattern_type="concern"))
    repo.create_staged(_make_staged_item("a1", pattern_type="approach"))

    concerns = repo.list_staged_by_pattern_type("concern")
    assert {i.id for i in concerns} == {"c1", "c2"}

    approaches = repo.list_staged_by_pattern_type("approach")
    assert {i.id for i in approaches} == {"a1"}


def test_list_staged_by_pattern_type_pending_only_excludes_promoted(repo):
    repo.create_staged(_make_staged_item("pending", pattern_type="concern"))
    repo.create_staged(_make_staged_item("promoted", pattern_type="concern"))
    repo.promote("promoted")

    pending = repo.list_staged_by_pattern_type("concern", pending_only=True)
    assert {i.id for i in pending} == {"pending"}

    everything = repo.list_staged_by_pattern_type("concern", pending_only=False)
    assert {i.id for i in everything} == {"pending", "promoted"}
