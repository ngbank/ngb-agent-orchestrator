"""AgentContextEngineService Protocol — the single contract every ACE caller
depends on.

The ACE CLI, the ACE TUI, and any future HTTP/UI clients all program against
this Protocol rather than reaching into :mod:`ace.pipeline` or
:mod:`ace.repository` directly.  :class:`~ace.service.local_service.LocalAgentContextEngineService`
provides the default in-process implementation; a future
``RemoteAgentContextEngineService`` will satisfy the same interface for
talking to a remote ACE server (Epic 9 / AOS-263).

Design rules (mirror :mod:`orchestrator.workflow_service.protocols`):

* Methods take small request DTOs (never ``ace.pipeline`` internals) and
  return frozen DTOs from :mod:`ace.service.dtos`.
* Handlers must never print, prompt the operator, or catch
  ``KeyboardInterrupt`` — callers own UX concerns.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .dtos import MineRequest, MineResult


@runtime_checkable
class AgentContextEngineService(Protocol):
    """Single ACE contract used by every caller."""

    def mine(self, request: MineRequest) -> MineResult:
        """Run the offline mining pipeline over eligible workflows.

        Mirrors :func:`ace.pipeline.runner.run_mining` but crosses the service
        boundary with DTOs only.
        """
        ...
