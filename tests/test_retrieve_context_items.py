"""Unit tests for ace.retrieval.retrieve.retrieve_context_items.

Uses the autouse fixtures from tests/conftest.py, which point DB_PATH at a
fresh tmp_path SQLite file and run migrations (including 014 and 016, which
create context_items and its applicability columns) before every test.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from ace.models import ContextItem
from ace.repository.context_item_repository import ContextItemRepository
from ace.retrieval.retrieve import retrieve_context_items


@pytest.fixture
def repo() -> ContextItemRepository:
    return ContextItemRepository()


def _make_item(item_id: str = "item-1", **overrides: Any) -> ContextItem:
    base = ContextItem(
        id=item_id,
        pattern_type="approach",
        scope="codebase_wide",
        description="Use additive migrations, never in-place ALTER for SQLite.",
        confidence=0.6,
        last_validated="2026-05-15T14:32:00Z",
        created_at="2026-05-15T14:32:00Z",
        updated_at="2026-05-15T14:32:00Z",
        status="active",
    )
    return dataclasses.replace(base, **overrides) if overrides else base


def test_empty_store_returns_empty_list():
    assert retrieve_context_items(query_text="anything") == []


def test_excludes_items_below_tentative_floor(repo):
    repo.create(_make_item("below", confidence=0.49))
    repo.create(_make_item("at_floor", confidence=0.50))

    results = retrieve_context_items()
    assert {i.id for i in results} == {"at_floor"}


def test_excludes_non_active_status(repo):
    repo.create(_make_item("active", status="active"))
    repo.create(_make_item("deprecated", status="deprecated"))
    repo.create(_make_item("staged", status="staged"))

    results = retrieve_context_items()
    assert {i.id for i in results} == {"active"}


def test_codebase_wide_scope_always_matches(repo):
    repo.create(_make_item("cw", scope="codebase_wide"))

    results = retrieve_context_items(task_type="totally_unrelated", file_path="unrelated.py")
    assert {i.id for i in results} == {"cw"}


def test_task_type_scope_filter(repo):
    repo.create(_make_item("match", scope="task_type", scope_value="state_machine_change"))
    repo.create(_make_item("nomatch", scope="task_type", scope_value="other_type"))

    results = retrieve_context_items(task_type="state_machine_change")
    assert {i.id for i in results} == {"match"}

    # No task_type given: scoped items don't match, only codebase_wide would.
    assert retrieve_context_items() == []


def test_file_pattern_scope_filter(repo):
    repo.create(_make_item("match", scope="file_pattern", scope_value="state/migrations/%"))
    repo.create(_make_item("nomatch", scope="file_pattern", scope_value="orchestrator/%"))

    results = retrieve_context_items(file_path="state/migrations/019_new.sql")
    assert {i.id for i in results} == {"match"}


def test_applicability_filters_project_repo_platform(repo):
    repo.create(_make_item("universal"))
    repo.create(_make_item("aos_only", project="AOS"))
    repo.create(_make_item("other_project", project="OTHER"))
    repo.create(_make_item("python_only", platform="python"))
    repo.create(_make_item("dotnet_only", platform="dotnet"))

    results = retrieve_context_items(project="AOS", platform="python")
    assert {i.id for i in results} == {"universal", "aos_only", "python_only"}


def test_keyword_ranking_prefers_higher_overlap(repo):
    repo.create(
        _make_item(
            "relevant",
            description="SQLite migrations must be additive, never in-place ALTER TABLE.",
            confidence=0.55,
        )
    )
    repo.create(
        _make_item(
            "irrelevant",
            description="Code generator recipes render Goose parameters as templates.",
            confidence=0.9,
        )
    )

    results = retrieve_context_items(query_text="How do SQLite migrations work with ALTER TABLE?")
    assert [i.id for i in results] == ["relevant", "irrelevant"]


def test_confidence_breaks_ties_when_no_query_text(repo):
    repo.create(_make_item("low", confidence=0.55))
    repo.create(_make_item("high", confidence=0.95))

    results = retrieve_context_items()
    assert [i.id for i in results] == ["high", "low"]


def test_top_k_truncates_results(repo):
    for i in range(5):
        repo.create(_make_item(f"item-{i}", confidence=0.5 + i * 0.01))

    results = retrieve_context_items(top_k=2)
    assert len(results) == 2
    # Highest confidence items win when there's no keyword signal.
    assert {i.id for i in results} == {"item-3", "item-4"}
