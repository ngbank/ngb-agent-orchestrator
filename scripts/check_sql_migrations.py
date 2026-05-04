#!/usr/bin/env python3
"""
Pre-commit hook: check SQL migration files for unsafe DDL.

Blocks any migration that contains a bare DROP TABLE without IF EXISTS,
which could silently destroy data if run against a schema that has evolved.
Migration 002 (which intentionally uses DROP TABLE) is excluded since it
wraps the operation safely inside a data-preserving copy-and-rename pattern.
"""

import re
import sys
from pathlib import Path

# Migrations that are known to use DROP TABLE safely and are excluded.
ALLOWED_DROP_TABLE_MIGRATIONS = {"002_approval_statuses.sql"}

# Pattern: DROP TABLE not followed by IF EXISTS (case-insensitive)
UNSAFE_DROP_PATTERN = re.compile(r"\bDROP\s+TABLE\s+(?!IF\s+EXISTS\b)", re.IGNORECASE)


def check_file(path: str) -> list[str]:
    errors: list[str] = []
    name = Path(path).name

    if name in ALLOWED_DROP_TABLE_MIGRATIONS:
        return errors

    content = Path(path).read_text()
    for i, line in enumerate(content.splitlines(), start=1):
        if UNSAFE_DROP_PATTERN.search(line):
            errors.append(
                f"{path}:{i}: unsafe DROP TABLE without IF EXISTS — "
                "use 'DROP TABLE IF EXISTS' or follow the copy-rename pattern in 002."
            )
    return errors


def main() -> int:
    files = sys.argv[1:]
    if not files:
        return 0

    all_errors = []
    for f in files:
        all_errors.extend(check_file(f))

    if all_errors:
        print("SQL migration safety check failed:")
        for err in all_errors:
            print(f"  {err}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
