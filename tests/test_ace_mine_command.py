"""Tests for the ``ace mine`` CLI command."""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from ace.cli.run import cli
from ace.pipeline.runner import RunnerResult


@pytest.fixture
def cli_runner():
    """Create a Click CLI test runner."""
    return CliRunner()


def _make_fake_service(result: RunnerResult | None = None) -> Mock:
    """Build a fake AgentContextEngineService that returns *result*."""
    fake = Mock()
    fake.run_mining.return_value = result or RunnerResult()
    return fake


def test_mine_runs_with_defaults(cli_runner: CliRunner) -> None:
    """``ace mine`` with no options calls service.run_mining with defaults."""
    fake = _make_fake_service()
    result = cli_runner.invoke(cli, ["mine"], obj=fake)

    assert result.exit_code == 0
    fake.run_mining.assert_called_once_with(limit=None, dry_run=False, workflow_id=None)


def test_mine_dry_run(cli_runner: CliRunner) -> None:
    """``ace mine --dry-run`` passes dry_run=True."""
    fake = _make_fake_service()
    result = cli_runner.invoke(cli, ["mine", "--dry-run"], obj=fake)

    assert result.exit_code == 0
    fake.run_mining.assert_called_once_with(limit=None, dry_run=True, workflow_id=None)
    assert "[dry-run]" in result.output


def test_mine_limit(cli_runner: CliRunner) -> None:
    """``ace mine --limit 5`` passes limit=5."""
    fake = _make_fake_service()
    result = cli_runner.invoke(cli, ["mine", "--limit", "5"], obj=fake)

    assert result.exit_code == 0
    fake.run_mining.assert_called_once_with(limit=5, dry_run=False, workflow_id=None)


def test_mine_workflow_id(cli_runner: CliRunner) -> None:
    """``ace mine --workflow-id abc`` passes workflow_id='abc'."""
    fake = _make_fake_service()
    result = cli_runner.invoke(cli, ["mine", "--workflow-id", "abc"], obj=fake)

    assert result.exit_code == 0
    fake.run_mining.assert_called_once_with(limit=None, dry_run=False, workflow_id="abc")


def test_mine_shows_counts(cli_runner: CliRunner) -> None:
    """Output includes processed / succeeded / skipped / flagged / failed counts."""
    fake = _make_fake_service(
        RunnerResult(processed=3, succeeded=2, skipped=1, flagged=0, failed=0)
    )
    result = cli_runner.invoke(cli, ["mine"], obj=fake)

    assert result.exit_code == 0
    assert "processed=3" in result.output
    assert "succeeded=2" in result.output
    assert "skipped=1" in result.output


def test_mine_shows_curation_counts(cli_runner: CliRunner) -> None:
    """Output includes curation counts when non-zero."""
    from ace.pipeline.curator import CurationResult

    fake = _make_fake_service(
        RunnerResult(
            processed=1,
            succeeded=1,
            curation=CurationResult(created=2, merged=1, contradicted=0, discarded=3),
        )
    )
    result = cli_runner.invoke(cli, ["mine"], obj=fake)

    assert result.exit_code == 0
    assert "created=2" in result.output
    assert "merged=1" in result.output
    assert "discarded=3" in result.output
