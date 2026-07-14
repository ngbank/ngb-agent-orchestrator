"""Factory for building :class:`~ace.service.protocols.AgentContextEngineService`
implementations.

Production code calls :func:`build_local_agent_context_engine_service`; tests
may inject a fake service directly via ``click.Context.obj``.
"""

from __future__ import annotations

from ace.repository.context_item_repository import ContextItemRepository
from ace.service.local_agent_context_engine_service import LocalAgentContextEngineService
from ace.service.protocols import AgentContextEngineService


def build_local_agent_context_engine_service() -> AgentContextEngineService:
    """Return a local-mode AgentContextEngineService.

    Constructs a
    :class:`~ace.repository.context_item_repository.ContextItemRepository`
    and wraps it in
    :class:`~ace.service.local_agent_context_engine_service.LocalAgentContextEngineService`.
    """
    repo = ContextItemRepository()
    return LocalAgentContextEngineService(repo)
