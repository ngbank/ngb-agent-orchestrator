"""Shared CLI helpers used across ACE command handlers.

After AOS-229 the CLI routes through ``AgentContextEngineService``, so this
module contains only presentation / side-effect glue the CLI layer still
needs.
"""

from __future__ import annotations

import click

from ace.service.protocols import MiningResult


def _emit_mining_summary(result: MiningResult) -> None:
    """Print a human-readable summary of a mining run."""
    prefix = "[dry-run] " if result.dry_run else ""
    click.echo(
        f"{prefix}Mining complete — "
        f"processed={result.processed} "
        f"succeeded={result.succeeded} "
        f"skipped={result.skipped} "
        f"flagged={result.flagged} "
        f"failed={result.failed}"
    )
    if result.created or result.merged or result.contradicted or result.discarded:
        click.echo(
            f"  Curation: created={result.created} "
            f"merged={result.merged} "
            f"contradicted={result.contradicted} "
            f"discarded={result.discarded}"
        )
