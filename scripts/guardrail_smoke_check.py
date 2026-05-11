#!/usr/bin/env python3
"""Pre-commit guardrail smoke check for prompt-injection-sensitive files."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

WATCHED_FILES = {
    "recipes/plan.yaml",
    "recipes/execute.yaml",
    "config/developer-rules.json",
}


def get_staged_files() -> list[str]:
    """Return staged files from git index."""
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def run_smoke_check(changed_files: list[str]) -> int:
    """Run smoke recipe with changed files threaded as parameter."""
    env = os.environ.copy()
    env["GOOSE_MCP_PYTHON"] = sys.executable

    changed_value = ",".join(changed_files)
    cmd = [
        "goose",
        "run",
        "--recipe",
        "recipes/smoke_test.yaml",
        "--param",
        f"changed_files={changed_value}",
    ]
    result = subprocess.run(cmd, env=env, check=False)
    return result.returncode


def main() -> int:
    staged_files = get_staged_files()
    relevant = [f for f in staged_files if f in WATCHED_FILES]

    if not relevant:
        print("Guardrail smoke check skipped: no watched files are staged.")
        return 0

    if not Path("recipes/smoke_test.yaml").exists():
        print(
            "Guardrail smoke check failed: recipes/smoke_test.yaml not found. "
            "Add the smoke recipe or bypass with SKIP=guardrail-smoke-check if intentional."
        )
        return 1

    print("Running guardrail smoke check for watched staged files: " + ", ".join(relevant))
    rc = run_smoke_check(relevant)
    if rc != 0:
        print(
            "Guardrail smoke check failed. Ensure prompt-injection-sensitive changes "
            "still pass the smoke recipe. If this change is intentional and reviewed, "
            "you may bypass once with SKIP=guardrail-smoke-check."
        )
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
