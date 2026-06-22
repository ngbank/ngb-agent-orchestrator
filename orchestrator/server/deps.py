"""Dependency-injection helpers for the orchestrator HTTP server.

The route handlers depend on ``get_service`` rather than reaching into
``orchestrator.workflow_service`` directly so tests (and a future
multi-tenant deployment) can swap the implementation via
``app.dependency_overrides``.
"""

from __future__ import annotations

from functools import lru_cache

from fastapi import Request

from orchestrator.workflow_service import (
    WorkflowService,
    build_local_workflow_service,
)

from .background import BackgroundDispatcherProtocol


@lru_cache(maxsize=1)
def _default_service() -> WorkflowService:
    """Build the process-wide default WorkflowService exactly once."""
    return build_local_workflow_service()


def get_service() -> WorkflowService:
    """FastAPI dependency that returns the active WorkflowService.

    Override with ``app.dependency_overrides[get_service] = lambda: fake``
    in tests.
    """
    return _default_service()


def get_background_dispatcher(request: Request) -> BackgroundDispatcherProtocol:
    """FastAPI dependency that returns the app's background dispatcher.

    The dispatcher is created by the FastAPI lifespan (see
    :func:`create_app`) and stashed on ``app.state``.  Override with
    ``app.dependency_overrides[get_background_dispatcher] = lambda: fake``
    in tests that want inline (synchronous) execution.
    """
    return request.app.state.background_dispatcher
