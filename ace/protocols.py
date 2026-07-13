"""Protocol definitions for ACE dependency injection."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ace.pipeline.runner import RunnerResult


@runtime_checkable
class AgentContextEngineService(Protocol):
    """Single contract used by every ACE CLI command handler.

    ``LocalAgentContextEngineService`` provides the default in-process
    implementation; a future ``RemoteAgentContextEngineService`` will
    satisfy the same interface for talking to a remote ACE server.
    """

    def run_mining(
        self,
        *,
        limit: int | None = None,
        dry_run: bool = False,
        workflow_id: str | None = None,
    ) -> RunnerResult:
        """Run the offline mining pipeline.

        Parameters
        ----------
        limit:
            Maximum number of eligible workflows to process.
        dry_run:
            When ``True``, execute the pipeline but do not write to the DB.
        workflow_id:
            Process only this specific workflow.

        Returns
        -------
        RunnerResult
            Summary of what happened.
        """
        ...
