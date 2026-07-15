"""``ace stats`` command handler.

Renders a snapshot of aggregate ACE store health metrics sourced from
:meth:`~ace.service.AgentContextEngineService.stats`.  No repository or
pipeline code is called from here — all aggregation lives in the service.
"""

from __future__ import annotations

import click

from ace.service import AgentContextEngineService, StatsResult


def _handle_stats(service: AgentContextEngineService) -> None:
    """Fetch and print aggregate ACE store health metrics."""
    result = service.stats()
    click.echo(_format_stats(result))


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
