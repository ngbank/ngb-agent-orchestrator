"""Node: run_goose — invoke the Goose execute recipe against the cloned workspace."""

import json
import os
import re
from pathlib import Path

import click

from orchestrator.code_generator.state import RunGooseInputState
from orchestrator.utils import goose_session, run_and_tee


def run_goose(state: RunGooseInputState) -> dict:
    """Shell out to `goose run --recipe recipes/execute.yaml`.

    goose_session is opened and closed entirely within this node — it is the
    only node that requires a live Goose session.

    Reads:  workflow_id, ticket_key, working_dir, work_plan_path, summary_path,
            reasoning_path, exec_log_path, execution_summary (for existing_branch
            on PR re-runs), pr_comments
    Writes: nothing (summary written to summary_path on disk by the recipe)
    """
    workflow_id = state.get("workflow_id")
    ticket_key = state.get("ticket_key", "")
    working_dir = state.get("working_dir", "")
    work_plan_path = state.get("work_plan_path", "")
    summary_path = state.get("summary_path", "")
    reasoning_path = state.get("reasoning_path", "")
    exec_log_path = state.get("exec_log_path", "")

    # Existing branch is used on PR re-runs to avoid re-creating the branch.
    existing_exec_summary = state.get("execution_summary") or {}
    existing_branch = existing_exec_summary.get("branch", "")
    pr_comments = state.get("pr_comments", "")

    # Compute a deterministic branch name from the work plan summary + workflow_id suffix.
    # This prevents remote collisions when the same ticket is run multiple times.
    with open(work_plan_path) as _f:
        _work_plan = json.load(_f)
    _slug = (
        re.sub(r"[^a-z0-9]+", "-", _work_plan.get("summary", "").lower())
        .strip("-")[:40]
        .rstrip("-")
    )
    branch_name = f"feature/{ticket_key}+{_slug}-{str(workflow_id)[:8]}"

    mcp_python = os.environ.get("GOOSE_MCP_PYTHON", "python")
    max_turns = os.environ.get("GOOSE_MAX_TURNS", "200")
    recipe_path = Path(__file__).resolve().parents[3] / "recipes" / "execute.yaml"

    click.echo(f"🪵 Running execute recipe for {ticket_key}...")

    with (
        open(exec_log_path, "a") as log_file,
        goose_session(workflow_id=workflow_id, stage="execute", ticket_key=ticket_key) as goose_env,
    ):
        log_file.write("\n=== goose run execute recipe ===\n")
        result = run_and_tee(
            [
                "goose",
                "run",
                "--recipe",
                str(recipe_path),
                "--max-turns",
                max_turns,
                "--params",
                f"ticket_key={ticket_key}",
                "--params",
                f"work_plan_path={work_plan_path}",
                "--params",
                f"working_dir={working_dir}",
                "--params",
                f"output_path={summary_path}",
                "--params",
                f"reasoning_path={reasoning_path}",
                "--params",
                f"GOOSE_MCP_PYTHON={mcp_python}",
                "--params",
                f"existing_branch={existing_branch}",
                "--params",
                f"pr_comments={pr_comments}",
                "--params",
                f"branch_name={branch_name}",
            ],
            log_file,
            cwd=working_dir,
            env=goose_env,
        )

    # Append reasoning diary to log
    if os.path.exists(reasoning_path):
        reasoning_text = open(reasoning_path).read().strip()
        if reasoning_text:
            with open(exec_log_path, "a") as log_file:
                log_file.write("\n\n" + "=" * 60 + "\n")
                log_file.write("  AGENT REASONING DIARY\n")
                log_file.write("=" * 60 + "\n")
                log_file.write(reasoning_text + "\n")

    if result.returncode != 0:
        click.echo(f"⚠️  Goose exited with code {result.returncode}")

    return {}
