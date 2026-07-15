"""Unit tests for ``ace stats`` CLI command.

Uses the autouse fixtures from tests/conftest.py, which point DB_PATH at a
fresh tmp_path SQLite file and run migrations (including 012 and 014, which
create context_extraction_log, context_items, and context_items_staged) before
every test.
"""

from __future__ import annotations

from datetime import UTC, datetime

from click.testing import CliRunner

from ace.cli.run import run
from state import get_connection


def _insert_extraction_log(workflow_id: str, extracted_at: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO context_extraction_log (workflow_id, extracted_at) VALUES (?, ?)",
            (workflow_id, extracted_at),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_staged_item(
    item_id: str,
    pattern_type: str,
    confidence: float,
    rejected_at: str | None = None,
) -> None:
    now = datetime.now(UTC).isoformat()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO context_items_staged (
                id, pattern_type, scope, description, confidence,
                last_validated, created_at, updated_at, status, rejected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item_id,
                pattern_type,
                "codebase_wide",
                "Test description",
                confidence,
                now,
                now,
                now,
                "staged",
                rejected_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_live_item(
    item_id: str,
    pattern_type: str,
    confidence: float,
) -> None:
    now = datetime.now(UTC).isoformat()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO context_items (
                id, pattern_type, scope, description, confidence,
                last_validated, created_at, updated_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item_id,
                pattern_type,
                "codebase_wide",
                "Test description",
                confidence,
                now,
                now,
                now,
                "active",
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_stats_empty_tables():
    """All aggregations should report zero when tables are empty."""
    runner = CliRunner()
    result = runner.invoke(run, ["stats"])

    assert result.exit_code == 0
    assert "Workflows mined: 0" in result.output
    assert "Most recent extraction: —" in result.output
    assert "Staged items: 0" in result.output
    assert "Promoted items: 0" in result.output


def test_stats_mining_summary():
    """Mining summary reflects inserted extraction log rows."""
    _insert_extraction_log("wf-1", "2026-07-01T12:00:00+00:00")
    _insert_extraction_log("wf-2", "2026-07-10T08:30:00+00:00")

    runner = CliRunner()
    result = runner.invoke(run, ["stats"])

    assert result.exit_code == 0
    assert "Workflows mined: 2" in result.output
    assert "Most recent extraction: 2026-07-10T08:30:00+00:00" in result.output


def test_stats_staged_breakdown():
    """Staged items are broken down by pattern_type and tier."""
    _insert_staged_item("s-1", "approach", 0.85)
    _insert_staged_item("s-2", "approach", 0.60)
    _insert_staged_item("s-3", "concern", 0.30)
    # rejected item should be excluded
    _insert_staged_item("s-4", "concern", 0.90, rejected_at="2026-07-01T00:00:00+00:00")

    runner = CliRunner()
    result = runner.invoke(run, ["stats"])

    assert result.exit_code == 0
    assert "Staged items: 3" in result.output
    assert "approach: 2" in result.output
    assert "concern: 1" in result.output
    assert "ESTABLISHED: 1" in result.output
    assert "PATTERN: 1" in result.output
    assert "TENTATIVE: 1" in result.output


def test_stats_promoted_breakdown():
    """Promoted (live) items are broken down by pattern_type and tier."""
    _insert_live_item("l-1", "test_coverage", 0.90)
    _insert_live_item("l-2", "implementation", 0.55)
    _insert_live_item("l-3", "implementation", 0.20)

    runner = CliRunner()
    result = runner.invoke(run, ["stats"])

    assert result.exit_code == 0
    assert "Promoted items: 3" in result.output
    assert "test_coverage: 1" in result.output
    assert "implementation: 2" in result.output
    assert "ESTABLISHED: 1" in result.output
    assert "PATTERN: 1" in result.output
    assert "TENTATIVE: 1" in result.output


def test_stats_combined():
    """All sections print correctly when every table has data."""
    _insert_extraction_log("wf-1", "2026-07-14T10:00:00+00:00")
    _insert_staged_item("s-1", "approach", 0.75)
    _insert_live_item("l-1", "concern", 0.65)

    runner = CliRunner()
    result = runner.invoke(run, ["stats"])

    assert result.exit_code == 0
    assert "Workflows mined: 1" in result.output
    assert "Staged items: 1" in result.output
    assert "Promoted items: 1" in result.output
    assert "approach: 1" in result.output
    assert "concern: 1" in result.output
