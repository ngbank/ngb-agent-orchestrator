"""Handler for the ``ace stats`` subcommand.

Read-only aggregation over ACE-owned tables:
  - context_extraction_log
  - context_items_staged
  - context_items
"""

from __future__ import annotations

import click

from state.sqlite_state_store import get_connection


def _fetch_mining_summary() -> tuple[int, str | None]:
    """Return (total_workflows_mined, most_recent_extracted_at)."""
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT COUNT(*) AS total,
                   MAX(extracted_at) AS most_recent
            FROM context_extraction_log
            """).fetchone()
    finally:
        conn.close()
    return row["total"], row["most_recent"]


def _fetch_staged_breakdown() -> dict:
    """Return staged-item aggregations."""
    conn = get_connection()
    try:
        pattern_rows = conn.execute("""
            SELECT pattern_type, COUNT(*) AS cnt
            FROM context_items_staged
            WHERE rejected_at IS NULL
            GROUP BY pattern_type
            ORDER BY cnt DESC
            """).fetchall()

        tier_rows = conn.execute("""
            SELECT
                CASE
                    WHEN confidence >= 0.8 THEN 'ESTABLISHED'
                    WHEN confidence >= 0.5 THEN 'PATTERN'
                    ELSE 'TENTATIVE'
                END AS tier,
                COUNT(*) AS cnt
            FROM context_items_staged
            WHERE rejected_at IS NULL
            GROUP BY tier
            """).fetchall()
    finally:
        conn.close()

    by_pattern = {row["pattern_type"]: row["cnt"] for row in pattern_rows}
    by_tier = {row["tier"]: row["cnt"] for row in tier_rows}

    total = sum(by_pattern.values())
    return {"total": total, "by_pattern": by_pattern, "by_tier": by_tier}


def _fetch_promoted_breakdown() -> dict:
    """Return promoted-item aggregations."""
    conn = get_connection()
    try:
        pattern_rows = conn.execute("""
            SELECT pattern_type, COUNT(*) AS cnt
            FROM context_items
            GROUP BY pattern_type
            ORDER BY cnt DESC
            """).fetchall()

        tier_rows = conn.execute("""
            SELECT
                CASE
                    WHEN confidence >= 0.8 THEN 'ESTABLISHED'
                    WHEN confidence >= 0.5 THEN 'PATTERN'
                    ELSE 'TENTATIVE'
                END AS tier,
                COUNT(*) AS cnt
            FROM context_items
            GROUP BY tier
            """).fetchall()
    finally:
        conn.close()

    by_pattern = {row["pattern_type"]: row["cnt"] for row in pattern_rows}
    by_tier = {row["tier"]: row["cnt"] for row in tier_rows}

    total = sum(by_pattern.values())
    return {"total": total, "by_pattern": by_pattern, "by_tier": by_tier}


def handle_stats() -> None:
    """Print the ACE mining summary."""
    mined_total, mined_recent = _fetch_mining_summary()
    staged = _fetch_staged_breakdown()
    promoted = _fetch_promoted_breakdown()

    click.echo("ACE Mining Summary")
    click.echo("=" * 40)
    click.echo()

    click.echo(f"Workflows mined: {mined_total}")
    if mined_recent:
        click.echo(f"Most recent extraction: {mined_recent}")
    else:
        click.echo("Most recent extraction: —")
    click.echo()

    click.echo(f"Staged items: {staged['total']}")
    if staged["by_pattern"]:
        click.echo("  By pattern_type:")
        for pt, cnt in staged["by_pattern"].items():
            click.echo(f"    {pt}: {cnt}")
    if staged["by_tier"]:
        click.echo("  By tier:")
        for tier in ("ESTABLISHED", "PATTERN", "TENTATIVE"):
            cnt = staged["by_tier"].get(tier, 0)
            if cnt:
                click.echo(f"    {tier}: {cnt}")
    click.echo()

    click.echo(f"Promoted items: {promoted['total']}")
    if promoted["by_pattern"]:
        click.echo("  By pattern_type:")
        for pt, cnt in promoted["by_pattern"].items():
            click.echo(f"    {pt}: {cnt}")
    if promoted["by_tier"]:
        click.echo("  By tier:")
        for tier in ("ESTABLISHED", "PATTERN", "TENTATIVE"):
            cnt = promoted["by_tier"].get(tier, 0)
            if cnt:
                click.echo(f"    {tier}: {cnt}")
