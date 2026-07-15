"""``ace mine`` command handler.

Wraps the runner behind the :class:`AgentContextEngineService` seam so the CLI
never imports :mod:`ace.pipeline` or :mod:`ace.repository` directly.
"""

from __future__ import annotations

from typing import Optional

import click

from ace.service import AgentContextEngineService, MineRequest, MineResult


def _handle_mine(
    service: AgentContextEngineService,
    *,
    limit: Optional[int],
    dry_run: bool,
    workflow_id: Optional[str],
) -> None:
    """Run the offline mining pipeline via *service* and print a summary."""
    request = MineRequest(limit=limit, dry_run=dry_run, workflow_id=workflow_id)
    result = service.mine(request)
    click.echo(_format_summary(result))


def _format_summary(result: MineResult) -> str:
    """Render a compact one-block summary of a :class:`MineResult`."""
    header = "ace mine: done"
    if result.dry_run:
        header += " [dry-run]"

    counts = (
        f"  workflows: processed={result.processed} succeeded={result.succeeded} "
        f"skipped={result.skipped} flagged={result.flagged} failed={result.failed}"
    )
    curation = (
        f"  curation:  created={result.created} merged={result.merged} "
        f"contradicted={result.contradicted} discarded={result.discarded}"
    )
    lines = [header, counts, curation]

    if result.comment_units:
        recall_pct = result.comment_units_cited / result.comment_units
        lines.append(
            "  comment recall: "
            f"{result.comment_units_cited}/{result.comment_units} ({recall_pct:.0%})"
        )

    return "\n".join(lines)
