"""FastAPI application factory + console-script entry point.

Public surface:

* :func:`create_app` — builds a new ``FastAPI`` instance.  Used by tests
  to inject a fake :class:`WorkflowService` via
  ``app.dependency_overrides``.
* :data:`app` — module-level singleton built with the default
  ``LocalWorkflowService`` so ``uvicorn orchestrator.server.app:app`` runs
  the production wiring.
* :func:`run` — console-script entry that boots uvicorn.  Reads
  ``ORCHESTRATOR_HOST`` / ``ORCHESTRATOR_PORT`` / ``ORCHESTRATOR_LOG_LEVEL``
  for runtime config.

OpenTelemetry instrumentation is best-effort: if
``opentelemetry-instrumentation-fastapi`` is not installed the server still
boots and serves traffic — just without per-request spans.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import FastAPI

from orchestrator.workflow_service import WorkflowService

from .auth import API_TOKEN_ENV, is_auth_enabled
from .deps import get_service
from .routes import admin_router, health_router, workflow_router

logger = logging.getLogger(__name__)


def _instrument_fastapi(app: FastAPI) -> None:
    """Attach OTel request instrumentation if the optional dep is present."""
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    except ImportError:
        logger.info(
            "opentelemetry-instrumentation-fastapi not installed; "
            "skipping HTTP span instrumentation"
        )
        return
    FastAPIInstrumentor.instrument_app(app)


def create_app(service: Optional[WorkflowService] = None) -> FastAPI:
    """Build a FastAPI app for the orchestrator.

    Pass ``service`` to wire in a custom :class:`WorkflowService`
    implementation (e.g. a fake in tests).  When omitted, the default
    in-process ``LocalWorkflowService`` is used via the ``get_service``
    dependency.
    """
    app = FastAPI(
        title="NGB Agent Orchestrator",
        description=(
            "REST surface for the agent orchestrator workflow service. "
            "Exposes the non-streaming subset of WorkflowService."
        ),
        version="0.1.0",
    )

    app.include_router(health_router)
    app.include_router(workflow_router)
    app.include_router(admin_router)

    if service is not None:
        app.dependency_overrides[get_service] = lambda: service

    if not is_auth_enabled():
        logger.warning(
            "%s is unset — orchestrator HTTP server running with auth DISABLED. "
            "Set the env var to enforce bearer-token auth.",
            API_TOKEN_ENV,
        )

    _instrument_fastapi(app)
    return app


# Module-level app for ``uvicorn orchestrator.server.app:app``.
app = create_app()


def run() -> None:
    """Boot uvicorn with config read from the environment.

    Env vars (all optional):

    * ``ORCHESTRATOR_HOST`` (default ``0.0.0.0``)
    * ``ORCHESTRATOR_PORT`` (default ``8080``)
    * ``ORCHESTRATOR_LOG_LEVEL`` (default ``info``)
    * ``ORCHESTRATOR_RELOAD`` — when set to ``1`` / ``true``, enables
      uvicorn's reload mode (dev only).
    """
    import uvicorn

    host = os.environ.get("ORCHESTRATOR_HOST", "0.0.0.0")
    port = int(os.environ.get("ORCHESTRATOR_PORT", "8080"))
    log_level = os.environ.get("ORCHESTRATOR_LOG_LEVEL", "info")
    reload_flag = os.environ.get("ORCHESTRATOR_RELOAD", "").lower() in ("1", "true", "yes")

    uvicorn.run(
        "orchestrator.server.app:app",
        host=host,
        port=port,
        log_level=log_level,
        reload=reload_flag,
    )


__all__ = ["app", "create_app", "run"]
