"""Unit tests for :mod:`ace.local_service`."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from ace.local_service import (
    LocalAgentContextEngineService,
    build_local_agent_context_engine_service,
)
from ace.pipeline.runner import RunnerResult


class TestLocalAgentContextEngineService:
    """Tests for LocalAgentContextEngineService."""

    def test_run_mining_delegates_to_runner(self) -> None:
        """run_mining delegates to ace.pipeline.runner.run_mining."""
        repo = Mock()
        service = LocalAgentContextEngineService(repo=repo)

        with patch("ace.local_service.run_mining") as mock_run_mining:
            mock_run_mining.return_value = RunnerResult(processed=5, succeeded=5)
            result = service.run_mining(limit=10, dry_run=True, workflow_id="wf-1")

        mock_run_mining.assert_called_once_with(limit=10, dry_run=True, workflow_id="wf-1")
        assert result.processed == 5
        assert result.succeeded == 5

    def test_run_mining_defaults(self) -> None:
        """run_mining works with all-default kwargs."""
        repo = Mock()
        service = LocalAgentContextEngineService(repo=repo)

        with patch("ace.local_service.run_mining") as mock_run_mining:
            mock_run_mining.return_value = RunnerResult()
            result = service.run_mining()

        mock_run_mining.assert_called_once_with(limit=None, dry_run=False, workflow_id=None)
        assert result == RunnerResult()


class TestBuildLocalAgentContextEngineService:
    """Tests for build_local_agent_context_engine_service factory."""

    def test_builds_service_with_repo(self) -> None:
        """Factory constructs a LocalAgentContextEngineService with a ContextItemRepository."""
        with patch("ace.local_service.ContextItemRepository") as mock_repo_cls:
            mock_repo = mock_repo_cls.return_value
            service = build_local_agent_context_engine_service()

        mock_repo_cls.assert_called_once_with()
        assert isinstance(service, LocalAgentContextEngineService)
        assert service._repo is mock_repo
