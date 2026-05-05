"""Node: execute_plan — invoke the Goose execute recipe to implement the approved WorkPlan."""

import json
import os
import shutil
import subprocess
import tempfile

import click

from graph.state import OrchestratorState
from mcp_server.server import get_repo_for_project
from state.state_store import update_execution_summary, update_status
from state.workflow_status import WorkflowStatus


def _project_key(ticket_key: str) -> str:
    """Extract project key from a ticket key, e.g. 'AOS-42' -> 'AOS'."""
    return ticket_key.split("-")[0].upper()


def execute_plan(state: OrchestratorState) -> dict:
    """Invoke the Goose execute recipe and persist the execution summary.

    1. Resolves the target repository URL via get_repo_for_project.
    2. Clones the repo into a fresh temp directory under /tmp.
    3. Writes the WorkPlan JSON to a temp file.
    4. Shells out to `goose run --recipe recipes/execute.yaml`.
    5. Reads and parses the execution summary JSON written by the recipe.
    6. Persists the summary to SQLite via update_execution_summary().
    7. Transitions the workflow status to COMPLETED or FAILED.
    8. Cleans up the temp clone and temp files.
    9. Returns execution_summary into state.
    """
    workflow_id = state.get("workflow_id")
    ticket_key = state.get("ticket_key", "")
    work_plan_data = state.get("work_plan_data")

    # --- Resolve target repository ---
    project_key = _project_key(ticket_key)
    try:
        repo_url = get_repo_for_project(project_key)
    except ValueError as e:
        click.echo(f"❌ {e}", err=True)
        summary = {
            "ticket_key": ticket_key,
            "branch": "",
            "build": "fail",
            "tests": "skipped",
            "files_changed": [],
            "commit_sha": "",
            "pr_url": "",
            "status": "failed",
            "error": str(e),
        }
        if workflow_id:
            update_execution_summary(workflow_id, summary)
            update_status(workflow_id, WorkflowStatus.FAILED, actor="execute_plan")
        return {"execution_summary": summary}

    # --- Clone into a fresh temp directory ---
    working_dir = f"/tmp/ngb-execute-{workflow_id}"
    click.echo(f"📂 Cloning {repo_url} into {working_dir}...")
    try:
        subprocess.run(
            ["git", "clone", repo_url, working_dir],
            check=True,
        )
    except subprocess.CalledProcessError as e:
        click.echo(f"❌ Failed to clone repository: {e}", err=True)
        summary = {
            "ticket_key": ticket_key,
            "branch": "",
            "build": "fail",
            "tests": "skipped",
            "files_changed": [],
            "commit_sha": "",
            "pr_url": "",
            "status": "failed",
            "error": f"Failed to clone {repo_url}: {e}",
        }
        if workflow_id:
            update_execution_summary(workflow_id, summary)
            update_status(workflow_id, WorkflowStatus.FAILED, actor="execute_plan")
        return {"execution_summary": summary}

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
        click.echo(f"🪵 Running execute recipe for {ticket_key}...")
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
                f"working_dir={working_dir}",
                "--params",
                f"output_path={summary_path}",
            ],
            check=False,
            cwd=working_dir,
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
                "pr_url": "",
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
                f"{chr(0x2705) if new_status == WorkflowStatus.COMPLETED else chr(0x274c)} "
                f"Execution {execution_summary.get('status')} — "
                f"branch: {execution_summary.get('branch', 'n/a')}, "
                f"build: {execution_summary.get('build')}, "
                f"tests: {execution_summary.get('tests')}"
            )

        return {"execution_summary": execution_summary}

    finally:
        # Clean up temp files and the working clone
        for path in (work_plan_path, summary_path):
            try:
                os.unlink(path)
            except OSError:
                pass
        if os.path.isdir(working_dir):
            shutil.rmtree(working_dir, ignore_errors=True)
            click.echo(f"🧹 Cleaned up working directory: {working_dir}")
