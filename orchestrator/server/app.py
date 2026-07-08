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
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from fastapi import FastAPI

from orchestrator.logging_setup import setup_logging
from orchestrator.workflow_service import WorkflowService

from .auth import (
    ADMIN_ALLOW_UNAUTHENTICATED_ENV,
    API_TOKEN_ENV,
    is_admin_open_for_dev,
    is_auth_enabled,
)
from .background import BackgroundDispatcher, BackgroundDispatcherProtocol
from .deps import get_background_dispatcher, get_service
from .routes import admin_router, health_router, workflow_router

logger = logging.getLogger(__name__)


def _instrument_fastapi(app: FastAPI) -> None:
    """Attach OTel request instrumentation if the optional dep is present."""
    try:
        from opentelemetry.instrumentation.fastapi import (  # pyright: ignore[reportMissingImports]
            FastAPIInstrumentor,
        )
    except ImportError:
        logger.info(
            "opentelemetry-instrumentation-fastapi not installed; "
            "skipping HTTP span instrumentation"
        )
        return
    FastAPIInstrumentor.instrument_app(app)


def create_app(
    service: Optional[WorkflowService] = None,
    *,
    background_dispatcher: Optional[BackgroundDispatcherProtocol] = None,
) -> FastAPI:
    """Build a FastAPI app for the orchestrator.

    Pass ``service`` to wire in a custom :class:`WorkflowService`
    implementation (e.g. a fake in tests).  When omitted, the default
    in-process ``LocalWorkflowService`` is used via the ``get_service``
    dependency.

    Pass ``background_dispatcher`` to inject a custom dispatcher (typically
    a :class:`SyncBackgroundDispatcher` in tests so route assertions run
    inline).  When omitted, the lifespan creates a process-wide
    :class:`BackgroundDispatcher` (worker pool sized via
    ``ORCHESTRATOR_BACKGROUND_WORKERS``) and shuts it down on app teardown.
    """
    # Configure Python's root logger before wiring anything up. Without
    # this, root stays at the WARNING default and every ``subprocess.goose
    # - INFO`` record (and everything else the per-workflow
    # ``WorkflowFileHandler`` depends on) is dropped at the logger-level
    # filter — leaving ``workflow.log`` empty and the TUI's live tail pane
    # blank in remote mode. The CLI entry point calls this from
    # ``dispatcher/run.py``; the server never did until now.
    setup_logging()

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        if background_dispatcher is not None:
            app.state.background_dispatcher = background_dispatcher
            owns_dispatcher = False
        else:
            workers_env = os.environ.get("ORCHESTRATOR_BACKGROUND_WORKERS")
            kwargs: dict = {}
            if workers_env:
                try:
                    kwargs["max_workers"] = int(workers_env)
                except ValueError:
                    logger.warning(
                        "Invalid ORCHESTRATOR_BACKGROUND_WORKERS=%r; using default",
                        workers_env,
                    )
            app.state.background_dispatcher = BackgroundDispatcher(**kwargs)
            owns_dispatcher = True
        try:
            yield
        finally:
            if owns_dispatcher:
                app.state.background_dispatcher.shutdown(wait=False)

    app = FastAPI(
        title="NGB Agent Orchestrator",
        description=(
            "REST surface for the agent orchestrator workflow service. "
            "Exposes the non-streaming subset of WorkflowService."
        ),
        version="0.1.0",
        lifespan=_lifespan,
    )

    app.include_router(health_router)
    app.include_router(workflow_router)
    app.include_router(admin_router)

    if service is not None:
        app.dependency_overrides[get_service] = lambda: service
    if background_dispatcher is not None:
        app.dependency_overrides[get_background_dispatcher] = lambda: background_dispatcher

    if not is_auth_enabled():
        logger.warning(
            "%s is unset — orchestrator HTTP server running with auth DISABLED. "
            "Set the env var to enforce bearer-token auth.",
            API_TOKEN_ENV,
        )

    if is_admin_open_for_dev():
        logger.warning(
            "%s is truthy with %s unset — /admin/* endpoints are OPEN to any "
            "caller. Development use only; do not enable in production.",
            ADMIN_ALLOW_UNAUTHENTICATED_ENV,
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
