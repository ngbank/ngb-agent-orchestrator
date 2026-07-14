"""Local implementation of :class:`~ace.service.protocols.AgentContextEngineService`.

Wraps :class:`~ace.repository.context_item_repository.ContextItemRepository` and
:class:`~ace.pipeline.runner.run_mining` to provide a concrete service for
local-mode CLI invocations.
"""

from __future__ import annotations

from typing import Optional

from ace.pipeline.runner import RunnerResult, run_mining
from ace.repository.context_item_repository import ContextItemRepository


class LocalAgentContextEngineService:
    """Concrete service that runs the mining pipeline locally."""

    def __init__(self, repo: ContextItemRepository) -> None:
        self._repo = repo

    def run_mining(
        self,
        *,
        limit: Optional[int] = None,
        dry_run: bool = False,
        workflow_id: Optional[str] = None,
    ) -> RunnerResult:
        """Run the mining pipeline via :func:`ace.pipeline.runner.run_mining`.

        The repository is passed implicitly — :func:`run_mining` constructs its
        own ``ContextItemRepository`` internally, so this method is a thin
        wrapper that keeps the protocol seam clean.
        """
        return run_mining(limit=limit, dry_run=dry_run, workflow_id=workflow_id)
