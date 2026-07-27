"""Tests for ``ace stats`` CLI command.

Injects a fake :class:`AgentContextEngineService` via
``runner.invoke(run, args, obj=fake_service)`` — same seam used by other
``test_ace_cli_*.py`` modules.  No pipeline or repository code is exercised.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pytest
from click.testing import CliRunner

from ace.cli.commands.stats import _format_stats
from ace.cli.run import run
from ace.service import (
    ListItemsRequest,
    ListItemsResult,
    MineRequest,
    MineResult,
    PromoteRequest,
    PromoteResult,
    RejectRequest,
    RejectResult,
    ShowItemRequest,
    ShowItemResult,
    StatsResult,
)
from state.sqlite_state_store import get_connection

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


@dataclass
class FakeService:
    """Fake :class:`AgentContextEngineService` for stats CLI tests."""

    stats_result: StatsResult = field(
        default_factory=lambda: StatsResult(
            by_status=(("active", 10), ("deprecated", 2)),
            by_tier=(("ESTABLISHED", 5), ("PATTERN", 4), ("TENTATIVE", 3)),
            by_pattern_type=(("approach", 6), ("concern", 4), ("test_coverage", 2)),
            staged_pending=3,
            staged_queue_age_days_p50=2.5,
            staged_queue_age_days_max=7.0,
            mined_workflows=20,
            generation_rate=1.5,
        )
    )

    def mine(self, request: MineRequest) -> MineResult:  # pragma: no cover
        raise NotImplementedError

    def list_items(self, request: ListItemsRequest) -> ListItemsResult:  # pragma: no cover
        raise NotImplementedError

    def show_item(self, request: ShowItemRequest) -> Optional[ShowItemResult]:  # pragma: no cover
        raise NotImplementedError

    def promote(self, request: PromoteRequest) -> PromoteResult:  # pragma: no cover
        raise NotImplementedError

    def reject(self, request: RejectRequest) -> RejectResult:  # pragma: no cover
        raise NotImplementedError

    def stats(self) -> StatsResult:
        return self.stats_result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


# ---------------------------------------------------------------------------
# ``ace --help`` lists stats
# ---------------------------------------------------------------------------


def test_ace_help_lists_stats(cli_runner: CliRunner) -> None:
    result = cli_runner.invoke(run, ["--help"])
    assert result.exit_code == 0
    assert "stats" in result.output


# ---------------------------------------------------------------------------
# ``ace stats`` — basic routing
# ---------------------------------------------------------------------------


def test_stats_exits_zero(cli_runner: CliRunner) -> None:
    fake = FakeService()
    result = cli_runner.invoke(run, ["stats"], obj=fake)
    assert result.exit_code == 0, result.output


def test_stats_calls_service_stats(cli_runner: CliRunner) -> None:
    fake = FakeService()
    result = cli_runner.invoke(run, ["stats"], obj=fake)
    assert result.exit_code == 0
    # verify some output was produced — non-empty means the handler was invoked
    assert result.output.strip()


# ---------------------------------------------------------------------------
# ``_format_stats`` — section presence and value rendering
# ---------------------------------------------------------------------------


def test_format_stats_shows_status_counts() -> None:
    result = StatsResult(
        by_status=(("active", 10), ("deprecated", 2)),
        by_tier=(),
        by_pattern_type=(),
        staged_pending=0,
        staged_queue_age_days_p50=None,
        staged_queue_age_days_max=None,
        mined_workflows=0,
        generation_rate=None,
    )
    output = _format_stats(result)
    assert "active" in output
    assert "10" in output
    assert "deprecated" in output
    assert "2" in output


def test_format_stats_shows_tier_counts() -> None:
    result = StatsResult(
        by_status=(),
        by_tier=(("ESTABLISHED", 5), ("PATTERN", 3)),
        by_pattern_type=(),
        staged_pending=0,
        staged_queue_age_days_p50=None,
        staged_queue_age_days_max=None,
        mined_workflows=0,
        generation_rate=None,
    )
    output = _format_stats(result)
    assert "ESTABLISHED" in output
    assert "PATTERN" in output


def test_format_stats_shows_pattern_type_counts() -> None:
    result = StatsResult(
        by_status=(),
        by_tier=(),
        by_pattern_type=(("approach", 6), ("concern", 2)),
        staged_pending=0,
        staged_queue_age_days_p50=None,
        staged_queue_age_days_max=None,
        mined_workflows=0,
        generation_rate=None,
    )
    output = _format_stats(result)
    assert "approach" in output
    assert "concern" in output


def test_format_stats_shows_staging_queue_age_when_present() -> None:
    result = StatsResult(
        by_status=(),
        by_tier=(),
        by_pattern_type=(),
        staged_pending=3,
        staged_queue_age_days_p50=2.5,
        staged_queue_age_days_max=7.0,
        mined_workflows=10,
        generation_rate=1.5,
    )
    output = _format_stats(result)
    assert "2.5" in output
    assert "7.0" in output
    assert "1.50" in output


def test_format_stats_shows_na_when_queue_empty() -> None:
    result = StatsResult(
        by_status=(),
        by_tier=(),
        by_pattern_type=(),
        staged_pending=0,
        staged_queue_age_days_p50=None,
        staged_queue_age_days_max=None,
        mined_workflows=0,
        generation_rate=None,
    )
    output = _format_stats(result)
    assert "n/a" in output


def test_format_stats_shows_mined_workflows() -> None:
    result = StatsResult(
        by_status=(),
        by_tier=(),
        by_pattern_type=(),
        staged_pending=0,
        staged_queue_age_days_p50=None,
        staged_queue_age_days_max=None,
        mined_workflows=42,
        generation_rate=2.1,
    )
    output = _format_stats(result)
    assert "42" in output
    assert "2.10" in output


def test_format_stats_empty_live_store_shows_none_labels() -> None:
    result = StatsResult(
        by_status=(),
        by_tier=(),
        by_pattern_type=(),
        staged_pending=0,
        staged_queue_age_days_p50=None,
        staged_queue_age_days_max=None,
        mined_workflows=0,
        generation_rate=None,
    )
    output = _format_stats(result)
    assert "(none)" in output


# ---------------------------------------------------------------------------
# ``ace stats --help``
# ---------------------------------------------------------------------------


def test_stats_exports_filtered_injection_event_joins(cli_runner: CliRunner, tmp_path) -> None:
    from state.sqlite_state_store import run_migrations

    run_migrations()
    conn = get_connection()
    conn.execute(
        """INSERT INTO synthesized_context_blocks
           (cache_key, rendered_markdown, provenance_manifest, input_item_ids, created_at)
           VALUES ('block-1', 'rendered', '{\"rules\":[\"item-1\"]}', '[\"item-1\"]', '2026-01-01T00:00:00+00:00')"""
    )
    conn.execute("""INSERT INTO ace_injection_events
           (workflow_id, ticket_key, injection_point, synthesizer, block_cache_key,
            retrieved_item_ids, rendered_length, created_at)
           VALUES ('workflow-1', 'AOS-239', 'planner', 'ace', 'block-1',
                   '[\"item-1\"]', 8, '2026-01-02T00:00:00+00:00')""")
    conn.commit()
    conn.close()
    output = tmp_path / "events.jsonl"

    result = cli_runner.invoke(
        run,
        ["stats", "--ticket-key", "AOS-239", "--injection-events-jsonl", str(output)],
        obj=FakeService(),
    )

    assert result.exit_code == 0, result.output
    event = __import__("json").loads(output.read_text())
    assert event["workflow_id"] == "workflow-1"
    assert event["provenance_manifest"] == '{"rules":["item-1"]}'


def test_stats_help(cli_runner: CliRunner) -> None:
    result = cli_runner.invoke(run, ["stats", "--help"])
    assert result.exit_code == 0
    assert "health" in result.output.lower() or "aggregate" in result.output.lower()
