"""Factory for :class:`AgentContextEngineService` implementations.

Provides ``build_local_agent_context_engine_service`` — the default factory
used by the ``ace`` CLI.  A future ``build_remote_agent_context_engine_service``
will be added when the remote transport (AOS-263) is implemented.
"""

from __future__ import annotations

from ace.repository.context_item_repository import ContextItemRepository

from .local_agent_context_engine_service import LocalAgentContextEngineService
from .protocols import AgentContextEngineService


def build_local_agent_context_engine_service() -> AgentContextEngineService:
    """Return a local in-process ACE service wired with default singletons."""
    repo = ContextItemRepository()
    return LocalAgentContextEngineService(repo=repo)
