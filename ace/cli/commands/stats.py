"""``ace stats`` command handler.

Renders a snapshot of aggregate ACE store health metrics sourced from
:meth:`~ace.service.AgentContextEngineService.stats`.  No repository or
pipeline code is called from here — all aggregation lives in the service.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import click

from ace.service import AgentContextEngineService, StatsResult
from state.sqlite_state_store import get_connection


def _handle_stats(
    service: AgentContextEngineService,
    *,
    ticket_key: Optional[str] = None,
    workflow_id: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    injection_events_jsonl: Optional[str] = None,
) -> None:
    """Fetch health metrics and optionally export filtered injection events."""
    result = service.stats()
    click.echo(_format_stats(result))
    if injection_events_jsonl:
        count = _export_injection_events(
            Path(injection_events_jsonl), ticket_key, workflow_id, since, until
        )
        click.echo(f"Exported {count} injection events to {injection_events_jsonl}")


def _export_injection_events(
    output_path: Path,
    ticket_key: Optional[str],
    workflow_id: Optional[str],
    since: Optional[str],
    until: Optional[str],
) -> int:
    """Export filtered event-to-durable-block provenance joins as JSONL."""
    clauses: list[str] = []
    params: list[str] = []
    for column, value, operator in (
        ("e.ticket_key", ticket_key, "="),
        ("e.workflow_id", workflow_id, "="),
        ("e.created_at", since, ">="),
        ("e.created_at", until, "<"),
    ):
        if value:
            clauses.append(f"{column} {operator} ?")
            params.append(value)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    conn = get_connection()
    try:
        rows = conn.execute(
            f"""
            SELECT e.*, b.rendered_markdown, b.provenance_manifest,
                   b.input_item_ids AS block_input_item_ids
            FROM ace_injection_events e
            LEFT JOIN synthesized_context_blocks b ON b.cache_key = e.block_cache_key
            {where}
            ORDER BY e.created_at
            """,
            params,
        ).fetchall()
    finally:
        conn.close()

    with output_path.open("w") as output:
        for row in rows:
            output.write(json.dumps(dict(row), sort_keys=True) + "\n")
    return len(rows)


def _format_stats(result: StatsResult) -> str:
    """Render a :class:`StatsResult` as a human-readable report."""
    lines: list[str] = []

    # ------------------------------------------------------------------ #
    # Live store
    # ------------------------------------------------------------------ #
    lines.append("Live context items")
    lines.append("-" * 40)

    if result.by_status:
        lines.append("  by status:")
        for status, count in result.by_status:
            lines.append(f"    {status:<16} {count:>5}")
    else:
        lines.append("  by status:       (none)")

    if result.by_tier:
        lines.append("  by tier:")
        for tier, count in result.by_tier:
            lines.append(f"    {tier:<16} {count:>5}")
    else:
        lines.append("  by tier:         (none)")

    if result.by_pattern_type:
        lines.append("  by pattern_type:")
        for pt, count in result.by_pattern_type:
            lines.append(f"    {pt:<16} {count:>5}")
    else:
        lines.append("  by pattern_type: (none)")

    # ------------------------------------------------------------------ #
    # Staging queue
    # ------------------------------------------------------------------ #
    lines.append("")
    lines.append("Staging queue")
    lines.append("-" * 40)
    lines.append(f"  pending review:  {result.staged_pending:>5}")
    if result.staged_queue_age_days_p50 is not None:
        lines.append(f"  age p50 (days):  {result.staged_queue_age_days_p50:>8.1f}")
        lines.append(f"  age max (days):  {result.staged_queue_age_days_max:>8.1f}")
    else:
        lines.append("  age p50 (days):       n/a")
        lines.append("  age max (days):       n/a")

    # ------------------------------------------------------------------ #
    # Mining productivity
    # ------------------------------------------------------------------ #
    lines.append("")
    lines.append("Mining productivity")
    lines.append("-" * 40)
    lines.append(f"  mined workflows: {result.mined_workflows:>5}")
    if result.generation_rate is not None:
        lines.append(f"  items/workflow:  {result.generation_rate:>8.2f}")
    else:
        lines.append("  items/workflow:       n/a")

    return "\n".join(lines)
