"""Environment-driven :class:`WorkflowService` factory.

This module owns the dispatcher's local-vs-remote selection logic so the CLI,
TUI, and any future tool can route a single call (``build_workflow_service_from_env``)
to the right implementation based on ``ORCHESTRATOR_MODE``.

Env vars (read on every call so tests using ``monkeypatch.setenv`` see updates
without restarting the process):

* ``ORCHESTRATOR_MODE`` — ``local`` (default) or ``remote``.
* ``ORCHESTRATOR_URL`` — required when ``ORCHESTRATOR_MODE=remote``.  Base URL
  of the orchestrator server (e.g. ``http://orchestrator.internal:8080``).
* ``ORCHESTRATOR_TOKEN`` — optional bearer token sent on every request to the
  remote server; matches the server's ``ORCHESTRATOR_API_TOKEN``.
"""

from __future__ import annotations

import os
from typing import Optional

from .http_client import build_http_workflow_service
from .local import build_local_workflow_service
from .protocols import WorkflowService

MODE_ENV = "ORCHESTRATOR_MODE"
URL_ENV = "ORCHESTRATOR_URL"
TOKEN_ENV = "ORCHESTRATOR_TOKEN"

MODE_LOCAL = "local"
MODE_REMOTE = "remote"


def _resolve_mode() -> str:
    raw = (os.environ.get(MODE_ENV) or MODE_LOCAL).strip().lower()
    if raw not in (MODE_LOCAL, MODE_REMOTE):
        raise ValueError(
            f"{MODE_ENV}={raw!r} is invalid; expected '{MODE_LOCAL}' or '{MODE_REMOTE}'."
        )
    return raw


def build_workflow_service_from_env() -> WorkflowService:
    """Return the :class:`WorkflowService` implied by the process environment.

    Behaviour:

    * ``ORCHESTRATOR_MODE`` unset, empty, or ``local`` → :class:`LocalWorkflowService`.
    * ``ORCHESTRATOR_MODE=remote`` → :class:`HttpWorkflowService` targeting
      ``ORCHESTRATOR_URL`` with ``ORCHESTRATOR_TOKEN`` (when set).

    Raises :class:`ValueError` for unknown modes or when ``remote`` is selected
    without an ``ORCHESTRATOR_URL`` — surfaced to the CLI so the user sees a
    clear configuration error rather than a request-time failure.
    """
    mode = _resolve_mode()
    if mode == MODE_LOCAL:
        return build_local_workflow_service()

    base_url: Optional[str] = (os.environ.get(URL_ENV) or "").strip() or None
    if not base_url:
        raise ValueError(
            f"{MODE_ENV}={MODE_REMOTE} requires {URL_ENV} to be set "
            "(orchestrator server base URL)."
        )
    token: Optional[str] = (os.environ.get(TOKEN_ENV) or "").strip() or None
    return build_http_workflow_service(base_url, token=token)


__all__ = [
    "MODE_ENV",
    "URL_ENV",
    "TOKEN_ENV",
    "MODE_LOCAL",
    "MODE_REMOTE",
    "build_workflow_service_from_env",
]
