"""FastAPI orchestrator server.

Exposes the non-streaming surface of :class:`WorkflowService` as REST
endpoints so remote clients (a future ``HttpWorkflowService``, dashboards,
external automation) can drive workflows without importing the in-process
implementation.

Public surface:

* ``create_app`` — application factory.  Accepts an optional ``WorkflowService``
  to inject; tests use this to plug in a fake.
* ``app`` — module-level ASGI application built with the default
  ``LocalWorkflowService`` so ``uvicorn orchestrator.server.app:app`` works
  out of the box.
* ``run`` — console-script entry point that boots uvicorn.
"""

from .app import app, create_app, run

__all__ = ["app", "create_app", "run"]
