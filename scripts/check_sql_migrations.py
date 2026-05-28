#!/usr/bin/env python3
"""
Pre-commit hook: check SQL migration files for unsafe DDL.

Since migrations have been consolidated into a single baseline file
(001_initial_schema.sql), the check is simplified: we only verify that
no bare DROP TABLE without IF EXISTS appears. The consolidated baseline
uses CREATE TABLE IF NOT EXISTS and contains no DROP TABLE statements.
"""

import re
import sys
from pathlib import Path

# Pattern: DROP TABLE not followed by IF EXISTS (case-insensitive)
UNSAFE_DROP_PATTERN = re.compile(r"\bDROP\s+TABLE\s+(?!IF\s+EXISTS\b)", re.IGNORECASE)


def check_file(path: str) -> list[str]:
    errors: list[str] = []
    content = Path(path).read_text()
    for i, line in enumerate(content.splitlines(), start=1):
        if UNSAFE_DROP_PATTERN.search(line):
            errors.append(
                f"{path}:{i}: unsafe DROP TABLE without IF EXISTS — "
                "use 'DROP TABLE IF EXISTS' or avoid DROP TABLE in baseline migrations."
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
