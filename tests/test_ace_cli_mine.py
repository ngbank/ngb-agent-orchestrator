"""Tests for the ``ace`` CLI (``ace/cli/run.py``) and its ``mine`` subcommand.

Tests inject a fake :class:`AgentContextEngineService` via
``runner.invoke(run, args, obj=fake_service)`` — the same seam used by
``tests/test_dispatcher.py``.  No pipeline or repository code is exercised
here; this file only verifies CLI wiring.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import pytest
from click.testing import CliRunner

from ace.cli.run import run
from ace.service import MineRequest, MineResult

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


def _empty_result(dry_run: bool = False) -> MineResult:
    return MineResult(
        processed=0,
        succeeded=0,
        skipped=0,
        flagged=0,
        failed=0,
        dry_run=dry_run,
        created=0,
        merged=0,
        contradicted=0,
        discarded=0,
        comment_units=0,
        comment_units_cited=0,
    )


@dataclass
class FakeService:
    """Fake :class:`AgentContextEngineService` that records calls."""

    result: MineResult = field(default_factory=_empty_result)
    calls: List[MineRequest] = field(default_factory=list)

    def mine(self, request: MineRequest) -> MineResult:
        self.calls.append(request)
        return self.result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


# ---------------------------------------------------------------------------
# Group-level behaviour
# ---------------------------------------------------------------------------


def test_ace_help_lists_mine(cli_runner: CliRunner) -> None:
    """``ace --help`` renders the group and includes the ``mine`` verb."""
    result = cli_runner.invoke(run, ["--help"])
    assert result.exit_code == 0
    assert "mine" in result.output


def test_ace_without_subcommand_prints_help(cli_runner: CliRunner) -> None:
    """Invoking ``ace`` with no subcommand shows the group help."""
    fake = FakeService()
    result = cli_runner.invoke(run, [], obj=fake)
    # Click groups exit with code 2 and print usage when no subcommand is given.
    assert result.exit_code in (0, 2)
    assert "mine" in result.output
    assert fake.calls == []


# ---------------------------------------------------------------------------
# ``ace mine`` — flag routing
# ---------------------------------------------------------------------------


def test_mine_defaults_route_to_service(cli_runner: CliRunner) -> None:
    """``ace mine`` with no flags calls the service with the DTO defaults."""
    fake = FakeService()
    result = cli_runner.invoke(run, ["mine"], obj=fake)

    assert result.exit_code == 0, result.output
    assert len(fake.calls) == 1
    req = fake.calls[0]
    assert isinstance(req, MineRequest)
    assert req.limit is None
    assert req.dry_run is False
    assert req.workflow_id is None


def test_mine_limit_flag(cli_runner: CliRunner) -> None:
    fake = FakeService()
    result = cli_runner.invoke(run, ["mine", "--limit", "5"], obj=fake)
    assert result.exit_code == 0, result.output
    assert fake.calls[0].limit == 5


def test_mine_dry_run_flag(cli_runner: CliRunner) -> None:
    fake = FakeService(result=_empty_result(dry_run=True))
    result = cli_runner.invoke(run, ["mine", "--dry-run"], obj=fake)
    assert result.exit_code == 0, result.output
    assert fake.calls[0].dry_run is True
    assert "[dry-run]" in result.output


def test_mine_workflow_id_flag(cli_runner: CliRunner) -> None:
    fake = FakeService()
    wf_id = "11111111-2222-3333-4444-555555555555"
    result = cli_runner.invoke(run, ["mine", "--workflow-id", wf_id], obj=fake)
    assert result.exit_code == 0, result.output
    assert fake.calls[0].workflow_id == wf_id


def test_mine_limit_rejects_non_integer(cli_runner: CliRunner) -> None:
    """``--limit`` is typed ``int``; Click rejects garbage."""
    fake = FakeService()
    result = cli_runner.invoke(run, ["mine", "--limit", "abc"], obj=fake)
    assert result.exit_code != 0
    assert fake.calls == []


# ---------------------------------------------------------------------------
# ``ace mine`` — stdout summary
# ---------------------------------------------------------------------------


def test_mine_summary_renders_all_counts(cli_runner: CliRunner) -> None:
    """The stdout summary reports workflow and curation counts."""
    fake = FakeService(
        result=MineResult(
            processed=7,
            succeeded=6,
            skipped=2,
            flagged=1,
            failed=1,
            dry_run=False,
            created=3,
            merged=2,
            contradicted=1,
            discarded=4,
            comment_units=10,
            comment_units_cited=7,
        )
    )
    result = cli_runner.invoke(run, ["mine"], obj=fake)
    assert result.exit_code == 0, result.output
    out = result.output
    assert "processed=7" in out
    assert "succeeded=6" in out
    assert "skipped=2" in out
    assert "flagged=1" in out
    assert "failed=1" in out
    assert "created=3" in out
    assert "merged=2" in out
    assert "contradicted=1" in out
    assert "discarded=4" in out
    assert "comment recall: 7/10" in out


def test_mine_summary_omits_comment_recall_when_no_units(cli_runner: CliRunner) -> None:
    fake = FakeService()
    result = cli_runner.invoke(run, ["mine"], obj=fake)
    assert result.exit_code == 0, result.output
    assert "comment recall" not in result.output


# ---------------------------------------------------------------------------
# Service resolution (env → factory) — no ctx.obj injected
# ---------------------------------------------------------------------------


def test_mine_falls_back_to_env_factory(
    monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner
) -> None:
    """When ``ctx.obj`` is not supplied, ``_resolve_service`` calls the env factory."""
    captured: dict[str, Optional[MineRequest]] = {"req": None}

    class EnvFake:
        def mine(self, request: MineRequest) -> MineResult:
            captured["req"] = request
            return _empty_result()

    fake = EnvFake()

    def fake_factory():
        return fake

    monkeypatch.setattr(
        "ace.service.build_agent_context_engine_service_from_env",
        fake_factory,
    )

    result = cli_runner.invoke(run, ["mine", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert captured["req"] is not None
    assert captured["req"].dry_run is True


def test_mine_reports_config_error_from_factory(
    monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner
) -> None:
    """A ``ValueError`` from the env factory becomes a user-facing error."""

    def boom():
        raise ValueError("ACE_MODE='remote' not supported yet")

    monkeypatch.setattr(
        "ace.service.build_agent_context_engine_service_from_env",
        boom,
    )

    result = cli_runner.invoke(run, ["mine"])
    assert result.exit_code == 2
    assert "not supported" in result.output
