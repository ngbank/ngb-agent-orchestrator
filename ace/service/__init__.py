"""ACE service layer — protocol and implementations for the Agent Context Engine.

Public surface:

* ``AgentContextEngineService`` — the Protocol all callers depend on.
* ``LocalAgentContextEngineService`` — in-process implementation wrapping
  ``ContextItemRepository`` and the mining runner.
* ``build_local_agent_context_engine_service`` — factory returning a local
  service wired with default singletons.
"""

from .factory import build_local_agent_context_engine_service
from .local_agent_context_engine_service import LocalAgentContextEngineService
from .protocols import AgentContextEngineService

__all__ = [
    "AgentContextEngineService",
    "LocalAgentContextEngineService",
    "build_local_agent_context_engine_service",
]
