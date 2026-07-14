"""Handler for the ``ace mine`` command.

Runs the offline learning pipeline (Evaluator → Reflector → Curator) over
eligible workflows.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from ace.service.protocols import AgentContextEngineService


def _handle_mine(
    service: "AgentContextEngineService",
    limit: int | None,
    dry_run: bool,
    workflow_id: str | None,
) -> None:
    """Run the mining pipeline and print a summary."""
    if dry_run:
        click.echo("[DRY RUN] Evaluating and reflecting without DB writes")

    result = service.run_mining(limit=limit, dry_run=dry_run, workflow_id=workflow_id)

    click.echo(
        f"Processed: {result.processed} | "
        f"Succeeded: {result.succeeded} | "
        f"Skipped: {result.skipped} | "
        f"Flagged: {result.flagged} | "
        f"Failed: {result.failed}"
    )

    if (
        result.curation.created
        or result.curation.merged
        or result.curation.contradicted
        or result.curation.discarded
    ):
        click.echo(
            f"Curation: created={result.curation.created} merged={result.curation.merged} "
            f"contradicted={result.curation.contradicted} discarded={result.curation.discarded}"
        )

    if result.failed:
        sys.exit(1)
