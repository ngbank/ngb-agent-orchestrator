"""AgentContextEngineService Protocol — the single contract every ACE caller depends on.

The ace CLI and future HTTP/UI clients all program against this Protocol
rather than reaching into ``ace.repository`` or ``ace.pipeline`` directly.
``LocalAgentContextEngineService`` provides the default in-process
implementation; a future ``RemoteAgentContextEngineService`` (AOS-263) will
satisfy the same interface for talking to a remote ACE server.

Design rules:

* Methods take primitive inputs and return small DTOs — no pipeline types leak
  across the boundary.
* The mine method returns ``MiningResult`` and never prints or catches
  ``KeyboardInterrupt`` (callers handle UX concerns).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable


@dataclass
class MiningResult:
    """Outcome of a mining operation.

    ``processed`` is the total number of workflows attempted (including
    failures).  ``succeeded`` is the number that completed the pipeline
    without exception.  ``skipped`` and ``flagged`` are workflows whose
    Evaluator verdict was ``skip`` or ``flag``.  ``failed`` is the number
    where an exception occurred during pipeline execution.
    """

    processed: int = 0
    succeeded: int = 0
    skipped: int = 0
    flagged: int = 0
    failed: int = 0
    dry_run: bool = False
    created: int = 0
    merged: int = 0
    contradicted: int = 0
    discarded: int = 0


@runtime_checkable
class AgentContextEngineService(Protocol):
    """Single ACE contract used by every caller."""

    def mine(
        self,
        *,
        limit: Optional[int] = None,
        dry_run: bool = False,
        workflow_id: Optional[str] = None,
    ) -> MiningResult:
        """Run the offline mining pipeline.

        Parameters
        ----------
        limit:
            Maximum number of eligible workflows to process.  Ignored when
            *workflow_id* is supplied.
        dry_run:
            When ``True``, execute the Evaluator and Reflector but skip all
            DB writes.
        workflow_id:
            Process only this specific workflow, bypassing the eligibility
            anti-join.

        Returns
        -------
        MiningResult
            Summary of what happened.
        """
        ...
