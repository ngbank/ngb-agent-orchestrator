"""Deterministic finalizer for the code-generation recipe.

Invoked as the final shell step of ``generate_code.yaml``. Writes
``raw_results.json`` at ``--output`` from git state plus two marker files
the recipe drops in ``--working-dir`` during the commit loop:

* ``.ngb_status`` — one of ``success | partial | failed`` (missing → ``failed``)
* ``.ngb_error``  — optional free-text error message

Because this step is machine-authored (not LLM-authored), the recipe cannot
"forget" to emit the summary the way it did on AOS-239. If the recipe never
gets far enough to call this script, the downstream ``load_raw_results``
node fails loud so the workflow retries instead of silently succeeding.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import List, Optional, Tuple


def _run(cmd: List[str], cwd: str) -> Tuple[int, str]:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return proc.returncode, proc.stdout.strip()


def _read_marker(working_dir: str, name: str) -> str:
    path = os.path.join(working_dir, name)
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except OSError:
        return ""


def _diff_base(working_dir: str) -> Optional[str]:
    """Return the merge-base SHA against origin/main (falling back to main)."""
    for ref in ("origin/main", "main"):
        rc, sha = _run(["git", "merge-base", "HEAD", ref], working_dir)
        if rc == 0 and sha:
            return sha
    return None


def _files_changed(working_dir: str, base: Optional[str]) -> List[str]:
    if not base:
        return []
    rc, out = _run(["git", "diff", "--name-only", f"{base}..HEAD"], working_dir)
    if rc != 0:
        return []
    return [line for line in out.splitlines() if line]


def build_raw_results(ticket_key: str, working_dir: str) -> dict:
    status_marker = _read_marker(working_dir, ".ngb_status")
    error_marker = _read_marker(working_dir, ".ngb_error")

    status = status_marker if status_marker in ("success", "partial", "failed") else "failed"

    _, commit_sha = _run(["git", "rev-parse", "HEAD"], working_dir)
    _, branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], working_dir)
    base_sha = _diff_base(working_dir)
    files = _files_changed(working_dir, base_sha)

    if status == "success":
        build, tests = "pass", "pass"
    elif status == "partial":
        build, tests = "pass", "fail"
    else:
        build, tests = "fail", "skipped"

    result = {
        "ticket_key": ticket_key,
        "branch": branch if commit_sha else "",
        "commit_sha": commit_sha,
        "files_changed": files,
        "build": build,
        "tests": tests,
        "pr_url": "",
        "status": status,
        "diff_base_sha": base_sha or "",
    }
    if error_marker:
        result["error"] = error_marker
    return result


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticket-key", required=True)
    parser.add_argument("--working-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    if not os.path.isdir(args.working_dir):
        sys.stderr.write(f"❌ working_dir does not exist: {args.working_dir}\n")
        return 2

    result = build_raw_results(args.ticket_key, args.working_dir)

    with open(args.output, "w") as f:
        json.dump(result, f, indent=2)
    sys.stdout.write(f"✅ raw_results written to {args.output} (status={result['status']})\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
