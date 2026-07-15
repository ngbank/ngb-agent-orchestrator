"""Environment-driven :class:`AgentContextEngineService` factory.

Mirrors :mod:`orchestrator.workflow_service.factory` so the ACE CLI, TUI, and
any future tool can route through a single call
(:func:`build_agent_context_engine_service_from_env`) to the right
implementation.

Env vars (read on every call so tests using ``monkeypatch.setenv`` see updates
without restarting the process):

* ``ACE_MODE`` — ``local`` (default).  A future ``remote`` branch will be
  wired in Epic 9 (AOS-263); today an unknown value raises so operators see a
  clear configuration error.
"""

from __future__ import annotations

import os

from .local_service import (
    LocalAgentContextEngineService,
    build_local_agent_context_engine_service,
)
from .protocols import AgentContextEngineService

MODE_ENV = "ACE_MODE"

MODE_LOCAL = "local"


def _resolve_mode() -> str:
    raw = (os.environ.get(MODE_ENV) or MODE_LOCAL).strip().lower()
    if raw != MODE_LOCAL:
        raise ValueError(
            f"{MODE_ENV}={raw!r} is invalid; only {MODE_LOCAL!r} is supported today. "
            "Remote ACE support ships in Epic 9 (AOS-263)."
        )
    return raw


def build_agent_context_engine_service_from_env() -> AgentContextEngineService:
    """Return the :class:`AgentContextEngineService` implied by the environment.

    Behaviour:

    * ``ACE_MODE`` unset, empty, or ``local`` →
      :class:`LocalAgentContextEngineService`.

    Raises :class:`ValueError` for unknown modes — surfaced to the CLI so the
    user sees a clear configuration error rather than a request-time failure.
    """
    _resolve_mode()
    return build_local_agent_context_engine_service()


__all__ = [
    "MODE_ENV",
    "MODE_LOCAL",
    "build_agent_context_engine_service_from_env",
    "build_local_agent_context_engine_service",
    "LocalAgentContextEngineService",
]
