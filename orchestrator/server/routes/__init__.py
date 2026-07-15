"""REST routes for the orchestrator HTTP server.

Split by resource so each concern lives in its own module:

* :mod:`.health` — liveness probe.
* :mod:`.workflows` — workflow CRUD (start / list / get / cancel /
  history / audit-log).
* :mod:`.decisions` — human-decision gate resume verbs and retry.
* :mod:`.streams` — Server-Sent Events endpoints (events + logs).
* :mod:`.admin` — DB wipe + mark-interrupted.

All routes depend on:

* ``get_service`` — supplies the :class:`WorkflowService` to call.
* ``get_background_dispatcher`` — supplies the worker pool that runs
  graph-running operations off the request thread (fire-and-forget).
* ``require_bearer_token`` / ``require_admin_token`` — enforces the
  auth stub (no-op when the ``ORCHESTRATOR_API_TOKEN`` env var is unset
  for the bearer variant; hard 503 for the admin variant).

Graph-running mutating routes (``start``, ``approve_plan``, ``comment_pr``
…) return ``202 Accepted`` immediately and dispatch the actual graph
drive to the background dispatcher.  Clients observe progress via
``/workflows/{id}/events`` (SSE) and ``/workflows/{id}`` (snapshot).

The three ``APIRouter`` instances live in :mod:`._shared`; each
submodule is imported below purely for the side effect of registering
its handlers on those routers.
"""

from __future__ import annotations

# Side-effect imports — each module registers its handlers on the shared
# routers defined in ``_shared`` (loaded transitively via each submodule).
# Order does not matter (no route-path collisions across modules), but
# keeping it alphabetical avoids churn when new modules are added.
from . import admin as _admin_routes  # noqa: F401
from . import decisions as _decision_routes  # noqa: F401
from . import health as _health_routes  # noqa: F401
from . import streams as _stream_routes  # noqa: F401
from . import workflows as _workflow_routes  # noqa: F401
from ._shared import admin_router, health_router, workflow_router

__all__ = ["admin_router", "health_router", "workflow_router"]
