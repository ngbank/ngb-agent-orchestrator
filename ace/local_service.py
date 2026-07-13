"""LocalAgentContextEngineService — in-process implementation of
:class:`~ace.protocols.AgentContextEngineService`.

Wraps :class:`~ace.repository.context_item_repository.ContextItemRepository`
and :func:`~ace.pipeline.runner.run_mining` so the CLI stays thin and the
service seam is ready for a future remote transport.
"""

from __future__ import annotations

from typing import Optional

from ace.pipeline.runner import RunnerResult, run_mining
from ace.repository.context_item_repository import ContextItemRepository


class LocalAgentContextEngineService:
    """In-process ACE service backed by SQLite."""

    def __init__(self, repo: ContextItemRepository) -> None:
        self._repo = repo

    def run_mining(
        self,
        *,
        limit: Optional[int] = None,
        dry_run: bool = False,
        workflow_id: Optional[str] = None,
    ) -> RunnerResult:
        """Run the offline mining pipeline.

        Delegates to :func:`~ace.pipeline.runner.run_mining`.
        """
        return run_mining(limit=limit, dry_run=dry_run, workflow_id=workflow_id)


def build_local_agent_context_engine_service() -> LocalAgentContextEngineService:
    """Factory that builds a :class:`LocalAgentContextEngineService`."""
    repo = ContextItemRepository()
    return LocalAgentContextEngineService(repo=repo)
