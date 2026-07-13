"""Tests for the one-time pr_comments JSON backfill script."""

import os
import tempfile

import pytest

from scripts.backfill_pr_comments_json import backfill
from state import workflow_repository as state_store


@pytest.fixture
def test_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        db_path = f.name
    os.unlink(db_path)

    original_db_path = os.environ.get("DB_PATH")
    os.environ["DB_PATH"] = db_path
    state_store.run_migrations()

    yield db_path

    if os.path.exists(db_path):
        os.unlink(db_path)
    if original_db_path:
        os.environ["DB_PATH"] = original_db_path
    elif "DB_PATH" in os.environ:
        del os.environ["DB_PATH"]


def _seed_legacy_text(workflow_id: str, text: str) -> None:
    from state import get_connection

    conn = get_connection()
    try:
        conn.execute("UPDATE workflows SET pr_comments = ? WHERE id = ?", (text, workflow_id))
        conn.commit()
    finally:
        conn.close()


def test_backfill_converts_single_round(test_db):
    workflow_id = state_store.create_workflow(ticket_key="AOS-221")
    _seed_legacy_text(
        workflow_id,
        "--- Review round 2026-06-06T00:00:00+00:00 ---\nFix the typo on line 42",
    )

    converted, skipped, failed = backfill()

    assert (converted, skipped, failed) == (1, 0, 0)
    workflow = state_store.get_workflow(workflow_id)
    rounds = workflow["pr_comments"]
    assert len(rounds) == 1
    assert rounds[0] == {
        "round": 1,
        "comments": "Fix the typo on line 42",
        "actor": "unknown",
        "timestamp": "2026-06-06T00:00:00+00:00",
    }


def test_backfill_converts_multiple_rounds_and_recovers_actor(test_db):
    workflow_id = state_store.create_workflow(ticket_key="AOS-221")
    # update_pr_comments writes both the legacy row shape (pre-refactor callers would
    # have used raw text) and the audit_log actor trail; we simulate the legacy text
    # directly and seed audit_log rows in the same shape the old writer produced.
    from state import get_connection

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO audit_log (id, workflow_id, actor, action, reason, created_at)
            VALUES (?, ?, ?, 'pr_comments_updated', 'PR review comments appended', ?)
            """,
            ("audit-1", workflow_id, "reviewer1", "2026-06-06T00:00:00+00:00"),
        )
        conn.execute(
            """
            INSERT INTO audit_log (id, workflow_id, actor, action, reason, created_at)
            VALUES (?, ?, ?, 'pr_comments_updated', 'PR review comments appended', ?)
            """,
            ("audit-2", workflow_id, "reviewer2", "2026-06-07T00:00:00+00:00"),
        )
        conn.commit()
    finally:
        conn.close()

    _seed_legacy_text(
        workflow_id,
        "--- Review round 2026-06-06T00:00:00+00:00 ---\n"
        "Missing test coverage\n\n"
        "--- Review round 2026-06-07T00:00:00+00:00 ---\n"
        "Looks better, one nit",
    )

    converted, skipped, failed = backfill()

    assert (converted, skipped, failed) == (1, 0, 0)
    rounds = state_store.get_workflow(workflow_id)["pr_comments"]
    assert len(rounds) == 2
    assert rounds[0]["comments"] == "Missing test coverage"
    assert rounds[0]["actor"] == "reviewer1"
    assert rounds[1]["comments"] == "Looks better, one nit"
    assert rounds[1]["actor"] == "reviewer2"


def test_backfill_skips_rows_already_json(test_db):
    workflow_id = state_store.create_workflow(ticket_key="AOS-221")
    state_store.update_pr_comments(workflow_id, "Already structured", actor="developer")

    converted, skipped, failed = backfill()

    assert (converted, skipped, failed) == (0, 1, 0)


def test_backfill_is_idempotent(test_db):
    workflow_id = state_store.create_workflow(ticket_key="AOS-221")
    _seed_legacy_text(
        workflow_id,
        "--- Review round 2026-06-06T00:00:00+00:00 ---\nFix the typo",
    )

    first = backfill()
    second = backfill()

    assert first == (1, 0, 0)
    assert second == (0, 1, 0)


def test_backfill_reports_unparseable_rows_as_failed(test_db):
    workflow_id = state_store.create_workflow(ticket_key="AOS-221")
    _seed_legacy_text(workflow_id, "not a recognizable format at all")

    converted, skipped, failed = backfill()

    assert (converted, skipped, failed) == (0, 0, 1)


def test_backfill_dry_run_does_not_write(test_db):
    workflow_id = state_store.create_workflow(ticket_key="AOS-221")
    _seed_legacy_text(
        workflow_id,
        "--- Review round 2026-06-06T00:00:00+00:00 ---\nFix the typo",
    )

    converted, skipped, failed = backfill(dry_run=True)

    assert (converted, skipped, failed) == (1, 0, 0)
    # Underlying row is untouched — still legacy text, not JSON.
    workflow = state_store.get_workflow(workflow_id)
    assert workflow["pr_comments"] == []  # legacy text fails JSON parse, degrades to []
