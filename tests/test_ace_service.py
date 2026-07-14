"""Unit tests for ace.service — protocol, local implementation, and factory."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ace.service import (
    AgentContextEngineService,
    LocalAgentContextEngineService,
    build_local_agent_context_engine_service,
)
from ace.service.protocols import MiningResult


# ---------------------------------------------------------------------------
# MiningResult
# ---------------------------------------------------------------------------


def test_mining_result_defaults():
    """All counters default to zero."""
    result = MiningResult()
    assert result.processed == 0
    assert result.succeeded == 0
    assert result.skipped == 0
    assert result.flagged == 0
    assert result.failed == 0
    assert result.dry_run is False
    assert result.created == 0
    assert result.merged == 0
    assert result.contradicted == 0
    assert result.discarded == 0


def test_mining_result_with_values():
    result = MiningResult(
        processed=5,
        succeeded=4,
        skipped=1,
        flagged=0,
        failed=0,
        dry_run=True,
        created=2,
        merged=1,
        contradicted=0,
        discarded=1,
    )
    assert result.processed == 5
    assert result.dry_run is True
    assert result.created == 2


# ---------------------------------------------------------------------------
# LocalAgentContextEngineService
# ---------------------------------------------------------------------------


class FakeRunnerResult:
    """Minimal stand-in for ace.pipeline.runner.RunnerResult."""

    def __init__(self, **kwargs) -> None:
        self.processed = kwargs.get("processed", 0)
        self.succeeded = kwargs.get("succeeded", 0)
        self.skipped = kwargs.get("skipped", 0)
        self.flagged = kwargs.get("flagged", 0)
        self.failed = kwargs.get("failed", 0)
        self.dry_run = kwargs.get("dry_run", False)
        self.curation = MagicMock()
        self.curation.created = kwargs.get("created", 0)
        self.curation.merged = kwargs.get("merged", 0)
        self.curation.contradicted = kwargs.get("contradicted", 0)
        self.curation.discarded = kwargs.get("discarded", 0)


def test_local_service_mine_delegates_to_runner():
    """mine() calls run_mining and maps RunnerResult → MiningResult."""
    fake_result = FakeRunnerResult(
        processed=3,
        succeeded=2,
        skipped=1,
        created=5,
        merged=2,
    )
    with patch(
        "ace.service.local_agent_context_engine_service._run_mining",
        return_value=fake_result,
    ):
        service = LocalAgentContextEngineService()
        result = service.mine(limit=10, dry_run=False)

    assert result.processed == 3
    assert result.succeeded == 2
    assert result.skipped == 1
    assert result.created == 5
    assert result.merged == 2


def test_local_service_mine_with_workflow_id():
    fake_result = FakeRunnerResult(processed=1, succeeded=1)
    with patch(
        "ace.service.local_agent_context_engine_service._run_mining",
        return_value=fake_result,
    ) as mock_run:
        service = LocalAgentContextEngineService()
        result = service.mine(workflow_id="wf-123", dry_run=True)

    mock_run.assert_called_once_with(limit=None, dry_run=True, workflow_id="wf-123")
    assert result.processed == 1
    assert result.dry_run is False  # from fake_result default


def test_local_service_is_instance_of_protocol():
    """LocalAgentContextEngineService satisfies AgentContextEngineService at runtime."""
    service = LocalAgentContextEngineService()
    assert isinstance(service, AgentContextEngineService)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def test_build_local_factory_returns_service():
    """build_local_agent_context_engine_service returns a LocalAgentContextEngineService."""
    with patch("ace.service.factory.ContextItemRepository") as mock_repo:
        service = build_local_agent_context_engine_service()
        assert isinstance(service, LocalAgentContextEngineService)
        mock_repo.assert_called_once()
