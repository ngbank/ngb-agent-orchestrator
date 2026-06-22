"""Bearer-token auth stub for the orchestrator HTTP server.

This is a placeholder gate intended for early environments and follow-up
hardening in a later epic.  Behaviour:

* When ``ORCHESTRATOR_API_TOKEN`` is unset (or empty), auth is **disabled**
  — every request is allowed.  The application logs a warning at startup
  so the operator sees the open posture.
* When the env var is set, every protected request must present a
  matching ``Authorization: Bearer <token>`` header.  Missing or wrong
  tokens return ``401`` with a JSON error body.
* The ``/healthz`` and OpenAPI endpoints are deliberately left open so
  load balancers and tooling can probe the service without credentials.
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import Header, HTTPException, status

API_TOKEN_ENV = "ORCHESTRATOR_API_TOKEN"


def _configured_token() -> Optional[str]:
    """Return the bearer token from env, or ``None`` when auth is disabled.

    Read on every call so tests using ``monkeypatch.setenv`` see updates
    without restarting the app.
    """
    token = os.environ.get(API_TOKEN_ENV)
    if token is None or token.strip() == "":
        return None
    return token


def is_auth_enabled() -> bool:
    """Return True when ``ORCHESTRATOR_API_TOKEN`` is set to a non-empty value."""
    return _configured_token() is not None


def require_bearer_token(authorization: Optional[str] = Header(default=None)) -> None:
    """FastAPI dependency that enforces the bearer token when configured."""
    expected = _configured_token()
    if expected is None:
        return

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header",
            headers={"WWW-Authenticate": 'Bearer realm="orchestrator"'},
        )

    presented = authorization.split(" ", 1)[1].strip()
    if presented != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token",
            headers={"WWW-Authenticate": 'Bearer realm="orchestrator"'},
        )


def require_admin_token(authorization: Optional[str] = Header(default=None)) -> None:
    """FastAPI dependency for admin endpoints (``clear_db``, ``mark_interrupted``).

    Admin routes are gated more strictly than the rest of the API: they
    refuse to run at all unless ``ORCHESTRATOR_API_TOKEN`` is configured.
    This avoids exposing destructive operations on an open development
    server.

    * ``ORCHESTRATOR_API_TOKEN`` unset → ``503 Service Unavailable`` with a
      message instructing the operator to configure the token.
    * Token configured + missing/wrong ``Authorization`` header → ``401``
      (same wire format as :func:`require_bearer_token`).
    * Token configured + matching header → request proceeds.
    """
    expected = _configured_token()
    if expected is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"Admin endpoints are disabled; set {API_TOKEN_ENV} on the "
                "server to enable them."
            ),
        )

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header",
            headers={"WWW-Authenticate": 'Bearer realm="orchestrator-admin"'},
        )

    presented = authorization.split(" ", 1)[1].strip()
    if presented != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token",
            headers={"WWW-Authenticate": 'Bearer realm="orchestrator-admin"'},
        )
