"""Health / liveness routes."""

from __future__ import annotations

from ..schemas import HealthResponse
from ._shared import health_router


@health_router.get("/healthz", response_model=HealthResponse)
def healthz() -> HealthResponse:
    """Liveness probe — always returns 200 when the process is up."""
    return HealthResponse()
