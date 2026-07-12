"""One-time backfill: convert workflows.pr_comments from the legacy
separator-delimited text format to the structured JSON array format
(round, comments, actor, timestamp), parallel to clarification_history.

Run once, manually, after migration 015 has been applied. Safe to re-run —
rows already holding a valid JSON array are skipped, so a second pass
converts zero rows.

Per-round actor is recovered from audit_log (action='pr_comments_updated',
ordered by created_at) since the legacy text format never captured it.

Usage:
    python -m scripts.backfill_pr_comments_json [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3

from state.sqlite_state_store import get_connection

# Matches the separator written by the legacy update_pr_comments():
#   "\n\n--- Review round <ISO timestamp> ---\n"
# The first round has no leading blank line because the legacy writer
# strip()s the final accumulated string.
_ROUND_SEPARATOR = re.compile(r"\n*--- Review round (.+?) ---\n")


def _parse_legacy_text(text: str) -> list[dict]:
    """Split legacy separator-delimited text into {timestamp, comments} segments."""
    parts = _ROUND_SEPARATOR.split(text)
    if parts and parts[0] == "":
        parts = parts[1:]

    segments = []
    for i in range(0, len(parts) - 1, 2):
        segments.append({"timestamp": parts[i], "comments": parts[i + 1].strip()})
    return segments


def _actors_for_workflow(conn: sqlite3.Connection, workflow_id: str) -> list[str]:
    """Recover per-round actors from audit_log, in write order."""
    rows = conn.execute(
        """
        SELECT actor FROM audit_log
        WHERE workflow_id = ? AND action = 'pr_comments_updated'
        ORDER BY created_at ASC
        """,
        (workflow_id,),
    ).fetchall()
    return [r["actor"] for r in rows]


def backfill(dry_run: bool = False) -> tuple[int, int, int]:
    """Convert all legacy-format pr_comments rows to JSON.

    Returns (converted, skipped, failed) counts.
    """
    conn = get_connection()
    converted = skipped = failed = 0
    try:
        rows = conn.execute(
            "SELECT id, pr_comments FROM workflows "
            "WHERE pr_comments IS NOT NULL AND pr_comments != ''"
        ).fetchall()

        for row in rows:
            workflow_id = row["id"]
            raw = row["pr_comments"]

            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    skipped += 1
                    continue
            except (json.JSONDecodeError, TypeError):
                pass  # legacy text format — fall through to conversion

            segments = _parse_legacy_text(raw)
            if not segments:
                print(f"WARN: workflow {workflow_id}: no rounds parsed from pr_comments, skipping")
                failed += 1
                continue

            actors = _actors_for_workflow(conn, workflow_id)
            rounds = [
                {
                    "round": i + 1,
                    "comments": segment["comments"],
                    "actor": actors[i] if i < len(actors) else "unknown",
                    "timestamp": segment["timestamp"],
                }
                for i, segment in enumerate(segments)
            ]

            if not dry_run:
                conn.execute(
                    "UPDATE workflows SET pr_comments = ? WHERE id = ?",
                    (json.dumps(rounds), workflow_id),
                )
            converted += 1

        if not dry_run:
            conn.commit()
    finally:
        conn.close()

    return converted, skipped, failed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview conversions without writing"
    )
    args = parser.parse_args()

    converted, skipped, failed = backfill(dry_run=args.dry_run)
    mode = "DRY RUN — " if args.dry_run else ""
    print(f"{mode}converted={converted} skipped={skipped} failed={failed}")


if __name__ == "__main__":
    main()
