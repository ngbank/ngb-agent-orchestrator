"""``ace mine`` command handler.

Consumes an :class:`AgentContextEngineService` — never calls the repository
directly.  This creates the seam needed for a future remote service
(AOS-263) without requiring changes to command code.
"""

from __future__ import annotations

from typing import Optional

import click

from ace.service.protocols import AgentContextEngineService

from .common import _emit_mining_summary


def _handle_mine(
    service: AgentContextEngineService,
    *,
    dry_run: bool = False,
    limit: Optional[int] = None,
    workflow_id: Optional[str] = None,
) -> None:
    """Run the mining pipeline and print results."""
    result = service.mine(dry_run=dry_run, limit=limit, workflow_id=workflow_id)
    _emit_mining_summary(result)
