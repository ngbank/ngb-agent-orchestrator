#!/usr/bin/env python3
"""Run a lightweight Goose smoke test when injected prompt files are staged."""

from __future__ import annotations

import argparse
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

WATCHED_FILES = {
    "recipes/plan.yaml",
    "recipes/execute.yaml",
    "config/developer-rules.json",
}


def get_staged_files() -> list[str]:
    """Return staged file paths from git index (ACMR only)."""
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            (result.stderr or result.stdout).strip() or "failed to read staged files"
        )

    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def should_run_smoke_test(staged_files: list[str]) -> bool:
    """Return True when watched injected files are included in staged changes."""
    return any(path in WATCHED_FILES for path in staged_files)


def _goose_command(output_dir: str, changed_files: list[str]) -> list[str]:
    return [
        "goose",
        "run",
        "--recipe",
        "recipes/smoke_test.yaml",
        "--params",
        f"output_dir={output_dir}",
        "--params",
        f"changed_files={','.join(changed_files)}",
        "--params",
        f"GOOSE_MCP_PYTHON={sys.executable}",
    ]


def run_smoke_test(changed_files: list[str]) -> tuple[bool, str]:
    """Execute smoke test recipe and assert hello_world.txt exists."""
    if shutil.which("goose") is None:
        return False, "goose CLI not found in PATH"

    temp_dir = tempfile.mkdtemp(prefix="guardrail-smoke-")
    try:
        cmd = _goose_command(temp_dir, changed_files)
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)

        hello_path = Path(temp_dir) / "hello_world.txt"
        if result.returncode != 0:
            output = "\n".join(
                part for part in [result.stdout.strip(), result.stderr.strip()] if part
            )
            return (
                False,
                "Goose smoke test command failed.\n"
                f"Command: {' '.join(shlex.quote(c) for c in cmd)}\n"
                f"Exit code: {result.returncode}\n"
                f"Output:\n{output or '<no output>'}",
            )

        if not hello_path.exists():
            output = "\n".join(
                part for part in [result.stdout.strip(), result.stderr.strip()] if part
            )
            return (
                False,
                "Guardrail smoke test failed: hello_world.txt was not created.\n"
                "This may indicate a guardrail blocked file-writing instructions\n"
                "in injected prompts.\n"
                f"Command: {' '.join(shlex.quote(c) for c in cmd)}\n"
                f"Goose output:\n{output or '<no output>'}",
            )

        return True, "Guardrail smoke test passed."
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--staged-files",
        nargs="*",
        default=None,
        help="Optional staged files override (used in tests).",
    )
    args = parser.parse_args(argv)

    try:
        staged_files = args.staged_files if args.staged_files is not None else get_staged_files()
    except RuntimeError as exc:
        print(f"[guardrail-smoke] Unable to determine staged files: {exc}", file=sys.stderr)
        return 1

    matched = [f for f in staged_files if f in WATCHED_FILES]
    if not matched:
        print("[guardrail-smoke] Skipped (no watched injected files staged.)")
        return 0

    print("[guardrail-smoke] Watched files staged; running Goose smoke test...")
    ok, message = run_smoke_test(matched)
    stream = sys.stdout if ok else sys.stderr
    print(f"[guardrail-smoke] {message}", file=stream)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
