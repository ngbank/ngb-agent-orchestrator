"""Integration tests for ace/cli/run.py and ace/cli/commands/mine.py."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner

from ace.cli.run import run
from ace.pipeline.runner import RunnerResult


@pytest.fixture
def cli_runner():
    """Create a Click CLI test runner."""
    return CliRunner()


@pytest.fixture
def fake_service():
    """Return a mock AgentContextEngineService with sensible defaults."""
    service = Mock()
    service.run_mining.return_value = RunnerResult(
        processed=3,
        succeeded=3,
        skipped=1,
        flagged=0,
        failed=0,
        dry_run=False,
    )
    return service


class TestAceCliHelp:
    """Smoke tests for CLI help and group structure."""

    def test_help_shows_description(self, cli_runner):
        result = cli_runner.invoke(run, ["--help"])
        assert result.exit_code == 0
        assert "ACE (Agent Context Engine) CLI" in result.output

    def test_mine_help_shows_options(self, cli_runner):
        result = cli_runner.invoke(run, ["mine", "--help"])
        assert result.exit_code == 0
        assert "--limit" in result.output
        assert "--dry-run" in result.output
        assert "--workflow-id" in result.output


class TestAceMineCommand:
    """Tests for ``ace mine`` dispatch and output."""

    def test_mine_runs_service(self, cli_runner, fake_service):
        result = cli_runner.invoke(run, ["mine"], obj=fake_service)
        assert result.exit_code == 0
        fake_service.run_mining.assert_called_once_with(limit=None, dry_run=False, workflow_id=None)

    def test_mine_with_limit(self, cli_runner, fake_service):
        result = cli_runner.invoke(run, ["mine", "--limit", "5"], obj=fake_service)
        assert result.exit_code == 0
        fake_service.run_mining.assert_called_once_with(limit=5, dry_run=False, workflow_id=None)

    def test_mine_with_dry_run(self, cli_runner, fake_service):
        result = cli_runner.invoke(run, ["mine", "--dry-run"], obj=fake_service)
        assert result.exit_code == 0
        fake_service.run_mining.assert_called_once_with(limit=None, dry_run=True, workflow_id=None)
        assert "[DRY RUN]" in result.output

    def test_mine_with_workflow_id(self, cli_runner, fake_service):
        result = cli_runner.invoke(run, ["mine", "--workflow-id", "wf-123"], obj=fake_service)
        assert result.exit_code == 0
        fake_service.run_mining.assert_called_once_with(
            limit=None, dry_run=False, workflow_id="wf-123"
        )

    def test_mine_combined_flags(self, cli_runner, fake_service):
        result = cli_runner.invoke(
            run,
            ["mine", "--limit", "10", "--dry-run", "--workflow-id", "wf-456"],
            obj=fake_service,
        )
        assert result.exit_code == 0
        fake_service.run_mining.assert_called_once_with(
            limit=10, dry_run=True, workflow_id="wf-456"
        )

    def test_mine_output_shows_summary(self, cli_runner, fake_service):
        result = cli_runner.invoke(run, ["mine"], obj=fake_service)
        assert result.exit_code == 0
        assert "Processed: 3" in result.output
        assert "Succeeded: 3" in result.output
        assert "Skipped: 1" in result.output

    def test_mine_output_shows_curation(self, cli_runner):
        service = Mock()
        service.run_mining.return_value = RunnerResult(
            processed=2,
            succeeded=2,
            skipped=0,
            flagged=0,
            failed=0,
            dry_run=False,
        )
        service.run_mining.return_value.curation.created = 3
        service.run_mining.return_value.curation.merged = 1
        service.run_mining.return_value.curation.contradicted = 0
        service.run_mining.return_value.curation.discarded = 2

        result = cli_runner.invoke(run, ["mine"], obj=service)
        assert result.exit_code == 0
        assert "Curation: created=3 merged=1 contradicted=0 discarded=2" in result.output

    def test_mine_exits_nonzero_on_failure(self, cli_runner):
        service = Mock()
        service.run_mining.return_value = RunnerResult(
            processed=5,
            succeeded=4,
            skipped=0,
            flagged=0,
            failed=1,
            dry_run=False,
        )

        result = cli_runner.invoke(run, ["mine"], obj=service)
        assert result.exit_code == 1
        assert "Failed: 1" in result.output

    def test_mine_no_curation_line_when_empty(self, cli_runner, fake_service):
        result = cli_runner.invoke(run, ["mine"], obj=fake_service)
        assert result.exit_code == 0
        assert "Curation:" not in result.output


class TestAceLazyServiceResolution:
    """Tests that the service is built lazily and only when needed."""

    def test_help_does_not_build_service(self, cli_runner):
        with patch("ace.service.factory.build_local_agent_context_engine_service") as mock_build:
            result = cli_runner.invoke(run, ["--help"])
            assert result.exit_code == 0
            mock_build.assert_not_called()

    def test_mine_builds_service_when_no_obj_injected(self, cli_runner):
        with patch("ace.service.factory.build_local_agent_context_engine_service") as mock_build:
            mock_service = Mock()
            mock_service.run_mining.return_value = RunnerResult(
                processed=0, succeeded=0, skipped=0, flagged=0, failed=0, dry_run=False
            )
            mock_build.return_value = mock_service

            result = cli_runner.invoke(run, ["mine"])
            assert result.exit_code == 0
            mock_build.assert_called_once()

    def test_injected_service_is_reused(self, cli_runner, fake_service):
        with patch("ace.service.factory.build_local_agent_context_engine_service") as mock_build:
            result = cli_runner.invoke(run, ["mine"], obj=fake_service)
            assert result.exit_code == 0
            mock_build.assert_not_called()
            fake_service.run_mining.assert_called_once()
