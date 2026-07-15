"""AgentContextEngineService abstraction — the single boundary for every ACE
CLI/TUI operation.

Public surface:

* :class:`AgentContextEngineService` — the Protocol all callers depend on.
* :class:`LocalAgentContextEngineService` — in-process implementation wrapping
  :class:`~ace.repository.context_item_repository.ContextItemRepository` and the
  offline mining runner.
* :func:`build_local_agent_context_engine_service` — factory that returns a
  :class:`LocalAgentContextEngineService` wired with the default singletons.
* :func:`build_agent_context_engine_service_from_env` — environment-driven
  factory that will grow a remote branch in Epic 9 (AOS-263).
* DTO types exchanged across the boundary (frozen dataclasses; transport-agnostic).

This module mirrors :mod:`orchestrator.workflow_service` so the ACE CLI and TUI
program against a single contract rather than reaching into
:mod:`ace.pipeline` or :mod:`ace.repository` directly.  A future
``RemoteAgentContextEngineService`` will satisfy the same Protocol without any
command-code change.
"""

from .dtos import (
    ItemSummaryDTO,
    ListItemsRequest,
    ListItemsResult,
    MineRequest,
    MineResult,
    PromoteRequest,
    PromoteResult,
    ProvenanceEntryDTO,
    RejectRequest,
    RejectResult,
    ShowItemRequest,
    ShowItemResult,
    StatsResult,
)
from .factory import (
    build_agent_context_engine_service_from_env,
    build_local_agent_context_engine_service,
)
from .local_service import LocalAgentContextEngineService
from .protocols import AgentContextEngineService

__all__ = [
    "AgentContextEngineService",
    "ItemSummaryDTO",
    "ListItemsRequest",
    "ListItemsResult",
    "LocalAgentContextEngineService",
    "MineRequest",
    "MineResult",
    "PromoteRequest",
    "PromoteResult",
    "ProvenanceEntryDTO",
    "RejectRequest",
    "RejectResult",
    "ShowItemRequest",
    "ShowItemResult",
    "StatsResult",
    "build_agent_context_engine_service_from_env",
    "build_local_agent_context_engine_service",
]
