"""Integration tests for ace/cli/run.py and ace/cli/commands/mine.py."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from ace.cli.run import run
from ace.service.protocols import AgentContextEngineService, MiningResult


class FakeAgentContextEngineService:
    """Test double satisfying AgentContextEngineService."""

    def mine(
        self,
        *,
        limit=None,
        dry_run=False,
        workflow_id=None,
    ) -> MiningResult:
        return MiningResult(
            processed=2,
            succeeded=2,
            skipped=0,
            flagged=0,
            failed=0,
            dry_run=dry_run,
            created=1,
            merged=0,
            contradicted=0,
            discarded=0,
        )


def _make_fake_service() -> AgentContextEngineService:
    return FakeAgentContextEngineService()


@pytest.fixture
def cli_runner():
    """Create a Click CLI test runner."""
    return CliRunner()


# ---------------------------------------------------------------------------
# ace mine
# ---------------------------------------------------------------------------


def test_mine_runs_pipeline(cli_runner):
    """ace mine delegates to the service and prints summary."""
    fake_service = _make_fake_service()
    result = cli_runner.invoke(run, ["mine"], obj=fake_service)
    assert result.exit_code == 0
    assert "Mining complete" in result.output
    assert "processed=2" in result.output
    assert "succeeded=2" in result.output


def test_mine_dry_run(cli_runner):
    """ace mine --dry-run passes dry_run=True to the service."""
    fake_service = _make_fake_service()
    result = cli_runner.invoke(run, ["mine", "--dry-run"], obj=fake_service)
    assert result.exit_code == 0
    assert "[dry-run]" in result.output


def test_mine_with_limit(cli_runner):
    """ace mine --limit 10 passes limit=10 to the service."""
    calls = []

    class RecordingService:
        def mine(self, *, limit=None, dry_run=False, workflow_id=None):
            calls.append({"limit": limit, "dry_run": dry_run, "workflow_id": workflow_id})
            return MiningResult()

    result = cli_runner.invoke(run, ["mine", "--limit", "10"], obj=RecordingService())
    assert result.exit_code == 0
    assert calls == [{"limit": 10, "dry_run": False, "workflow_id": None}]


def test_mine_with_workflow_id(cli_runner):
    """ace mine --workflow-id <id> passes workflow_id to the service."""
    calls = []

    class RecordingService:
        def mine(self, *, limit=None, dry_run=False, workflow_id=None):
            calls.append({"limit": limit, "dry_run": dry_run, "workflow_id": workflow_id})
            return MiningResult()

    result = cli_runner.invoke(run, ["mine", "--workflow-id", "wf-abc"], obj=RecordingService())
    assert result.exit_code == 0
    assert calls == [{"limit": None, "dry_run": False, "workflow_id": "wf-abc"}]


# ---------------------------------------------------------------------------
# Lazy service resolution
# ---------------------------------------------------------------------------


def test_help_does_not_build_service(cli_runner):
    """--help should not trigger service construction."""
    result = cli_runner.invoke(run, ["--help"])
    assert result.exit_code == 0
    assert "ACE CLI" in result.output
