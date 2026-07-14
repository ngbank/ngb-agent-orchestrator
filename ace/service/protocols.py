"""AgentContextEngineService protocol.

Defines the seam between the ACE CLI and the underlying service
implementation.  Local and remote implementations both satisfy this
protocol, keeping the CLI transport-agnostic.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ace.pipeline.runner import RunnerResult


@runtime_checkable
class AgentContextEngineService(Protocol):
    """Protocol for ACE service operations.

    Implementations must provide :meth:`run_mining`, which drives the
    offline learning pipeline (Evaluator → Reflector → Curator).
    """

    def run_mining(
        self,
        *,
        limit: int | None = None,
        dry_run: bool = False,
        workflow_id: str | None = None,
    ) -> RunnerResult:
        """Run the mining pipeline.

        Parameters
        ----------
        limit:
            Maximum number of eligible workflows to process.  Ignored when
            *workflow_id* is supplied.
        dry_run:
            When ``True``, execute the Evaluator and Reflector but do not
            write to the database.
        workflow_id:
            Process only this specific workflow, bypassing the eligibility
            anti-join.

        Returns
        -------
        RunnerResult
            Summary of what happened.
        """
        ...
