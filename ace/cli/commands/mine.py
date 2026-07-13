"""Handler for the ``ace mine`` command."""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from ace.protocols import AgentContextEngineService


def _handle_mine(
    service: "AgentContextEngineService",
    limit: int | None,
    dry_run: bool,
    workflow_id: str | None,
) -> None:
    """Run the ACE mining pipeline."""
    result = service.run_mining(limit=limit, dry_run=dry_run, workflow_id=workflow_id)

    prefix = "[dry-run] " if dry_run else ""
    click.echo(
        f"{prefix}Mining complete — "
        f"processed={result.processed} succeeded={result.succeeded} "
        f"skipped={result.skipped} flagged={result.flagged} failed={result.failed}"
    )
    if result.curation.created:
        click.echo(f"  created={result.curation.created}")
    if result.curation.merged:
        click.echo(f"  merged={result.curation.merged}")
    if result.curation.contradicted:
        click.echo(f"  contradicted={result.curation.contradicted}")
    if result.curation.discarded:
        click.echo(f"  discarded={result.curation.discarded}")
