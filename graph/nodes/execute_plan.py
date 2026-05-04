"""Node: execute_plan — invoke the Goose execute recipe to implement the approved WorkPlan."""

import json
import os
import subprocess
import tempfile

import click

from graph.state import OrchestratorState
from state.state_store import update_execution_summary, update_status
from state.workflow_status import WorkflowStatus


def execute_plan(state: OrchestratorState) -> dict:
    """Invoke the Goose execute recipe and persist the execution summary.

    1. Writes the WorkPlan JSON to a temp file.
    2. Shells out to `goose run --recipe recipes/execute.yaml`.
    3. Reads and parses the execution summary JSON written by the recipe.
    4. Persists the summary to SQLite via update_execution_summary().
    5. Transitions the workflow status to COMPLETED or FAILED.
    6. Cleans up temp files.
    7. Returns execution_summary into state.
    """
    workflow_id = state.get("workflow_id")
    ticket_key = state.get("ticket_key", "")
    work_plan_data = state.get("work_plan_data")

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix="_workplan.json",
        prefix=f"{workflow_id}_",
        delete=False,
    ) as wp_file:
        json.dump(work_plan_data, wp_file, indent=2)
        work_plan_path = wp_file.name

    summary_fd, summary_path = tempfile.mkstemp(
        suffix="_exec_summary.json",
        prefix=f"{workflow_id}_",
    )
    os.close(summary_fd)

    try:
        click.echo(f"🪿 Running execute recipe for {ticket_key}...")
        result = subprocess.run(
            [
                "goose",
                "run",
                "--recipe",
                "recipes/execute.yaml",
                "--params",
                f"ticket_key={ticket_key}",
                "--params",
                f"work_plan_path={work_plan_path}",
                "--params",
                f"output_path={summary_path}",
            ],
            check=False,
        )

        if result.returncode != 0:
            click.echo(f"⚠️  Goose exited with code {result.returncode}")

        # Read summary written by the recipe
        try:
            with open(summary_path, "r") as f:
                execution_summary = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            execution_summary = {
                "ticket_key": ticket_key,
                "branch": "",
                "build": "fail",
                "tests": "skipped",
                "files_changed": [],
                "commit_sha": "",
                "status": "failed",
                "error": f"Execution summary not written by recipe: {exc}",
            }

        # Persist to SQLite
        if workflow_id:
            update_execution_summary(workflow_id, execution_summary)
            new_status = (
                WorkflowStatus.COMPLETED
                if execution_summary.get("status") in ("success", "partial")
                else WorkflowStatus.FAILED
            )
            update_status(workflow_id, new_status, actor="execute_plan")
            click.echo(
                f"{'✅' if new_status == WorkflowStatus.COMPLETED else '❌'} "
                f"Execution {execution_summary.get('status')} — "
                f"branch: {execution_summary.get('branch', 'n/a')}, "
                f"build: {execution_summary.get('build')}, "
                f"tests: {execution_summary.get('tests')}"
            )

        return {"execution_summary": execution_summary}

    finally:
        # Clean up temp files
        for path in (work_plan_path, summary_path):
            try:
                os.unlink(path)
            except OSError:
                pass
