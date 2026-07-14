"""Unit and integration tests for the ACE service layer.

Covers :mod:`ace.service.protocols`, :mod:`ace.service.local_agent_context_engine_service`,
and :mod:`ace.service.factory`.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

from ace.pipeline.runner import RunnerResult
from ace.repository.context_item_repository import ContextItemRepository
from ace.service.factory import build_local_agent_context_engine_service
from ace.service.local_agent_context_engine_service import LocalAgentContextEngineService
from ace.service.protocols import AgentContextEngineService


class TestAgentContextEngineServiceProtocol:
    """Protocol conformance and structural tests."""

    def test_local_service_is_instance_of_protocol(self):
        repo = Mock(spec=ContextItemRepository)
        service = LocalAgentContextEngineService(repo)
        assert isinstance(service, AgentContextEngineService)

    def test_protocol_has_run_mining_method(self):
        assert hasattr(AgentContextEngineService, "run_mining")


class TestLocalAgentContextEngineService:
    """Tests for :class:`~ace.service.local_agent_context_engine_service.LocalAgentContextEngineService`."""

    def test_run_mining_delegates_to_runner(self):
        repo = Mock(spec=ContextItemRepository)
        service = LocalAgentContextEngineService(repo)

        with patch("ace.service.local_agent_context_engine_service.run_mining") as mock_run:
            mock_run.return_value = RunnerResult(
                processed=2, succeeded=2, skipped=0, flagged=0, failed=0, dry_run=False
            )
            result = service.run_mining(limit=5, dry_run=True, workflow_id="wf-1")

        mock_run.assert_called_once_with(limit=5, dry_run=True, workflow_id="wf-1")
        assert result.processed == 2
        assert result.succeeded == 2

    def test_run_mining_with_defaults(self):
        repo = Mock(spec=ContextItemRepository)
        service = LocalAgentContextEngineService(repo)

        with patch("ace.service.local_agent_context_engine_service.run_mining") as mock_run:
            mock_run.return_value = RunnerResult(
                processed=0, succeeded=0, skipped=0, flagged=0, failed=0, dry_run=False
            )
            service.run_mining()

        mock_run.assert_called_once_with(limit=None, dry_run=False, workflow_id=None)

    def test_service_stores_repo_reference(self):
        repo = Mock(spec=ContextItemRepository)
        service = LocalAgentContextEngineService(repo)
        assert service._repo is repo


class TestBuildLocalAgentContextEngineService:
    """Tests for :func:`~ace.service.factory.build_local_agent_context_engine_service`."""

    def test_factory_returns_service(self):
        with patch("ace.service.factory.ContextItemRepository") as mock_repo_cls:
            mock_repo_cls.return_value = Mock(spec=ContextItemRepository)
            service = build_local_agent_context_engine_service()
            assert isinstance(service, LocalAgentContextEngineService)

    def test_factory_creates_repository(self):
        with patch("ace.service.factory.ContextItemRepository") as mock_repo_cls:
            mock_repo = Mock(spec=ContextItemRepository)
            mock_repo_cls.return_value = mock_repo
            service = build_local_agent_context_engine_service()
            assert service._repo is mock_repo
            mock_repo_cls.assert_called_once_with()

    def test_factory_return_satisfies_protocol(self):
        with patch("ace.service.factory.ContextItemRepository") as mock_repo_cls:
            mock_repo_cls.return_value = Mock(spec=ContextItemRepository)
            service = build_local_agent_context_engine_service()
            assert isinstance(service, AgentContextEngineService)
