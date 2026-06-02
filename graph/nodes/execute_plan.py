"""Node: execute_plan — invoke the Goose execute recipe to implement the approved WorkPlan."""

import json
import os
import shutil
import tempfile
from pathlib import Path

import click

from graph.litellm_callbacks import aggregate_token_usage
from graph.state import OrchestratorState
from graph.utils import goose_session, log_path, run_and_tee
from mcp_server.server import get_repo_for_project
from state.repository import update_execution_summary, update_status, update_usage_summary
from state.workflow_status import WorkflowStatus


def _failure_summary(ticket_key: str, error: str) -> dict:
    """Return a standard failed execution summary dict."""
    return {
        "ticket_key": ticket_key,
        "branch": "",
        "build": "fail",
        "tests": "skipped",
        "files_changed": [],
        "commit_sha": "",
        "pr_url": "",
        "status": "failed",
        "error": error,
    }


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
        summary = _failure_summary(ticket_key, str(e))
        if workflow_id:
            update_execution_summary(workflow_id, summary)
            update_status(workflow_id, WorkflowStatus.FAILED, actor="execute_plan")
        return {"execution_summary": summary, "failed_node": "execute_plan"}

    # --- Check for existing branch (PR re-execution) ---
    exec_summary = state.get("execution_summary") or {}
    existing_branch = exec_summary.get("branch", "")
    pr_comments = state.get("pr_comments", "")

    # --- Clone into a fresh temp directory ---
    working_dir = tempfile.mkdtemp(prefix=f"ngb-execute-{workflow_id}-")
    lp = log_path(workflow_id or "unknown", "execute", ticket_key=ticket_key)
    click.echo(f"📂 Cloning {repo_url} into {working_dir}... (log: {lp})")
    try:
        with open(lp, "w") as log_file:
            log_file.write(f"=== git clone {repo_url} ===\n")
            clone_result = run_and_tee(
                ["git", "clone", repo_url, working_dir],
                log_file,
            )
        if clone_result.returncode != 0:
            raise Exception(f"git clone exited with code {clone_result.returncode}")
    except Exception as e:
        click.echo(f"❌ Failed to clone repository: {e}", err=True)
        summary = _failure_summary(ticket_key, f"Failed to clone {repo_url}: {e}")
        if workflow_id:
            update_execution_summary(workflow_id, summary)
            update_status(workflow_id, WorkflowStatus.FAILED, actor="execute_plan")
        return {"execution_summary": summary, "failed_node": "execute_plan"}

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

    reasoning_fd, reasoning_path = tempfile.mkstemp(
        suffix="_reasoning.txt",
        prefix=f"{workflow_id}_",
    )
    os.close(reasoning_fd)

    try:
        click.echo(f"🪵 Running execute recipe for {ticket_key}...")
        mcp_python = os.environ.get("GOOSE_MCP_PYTHON", "python")
        max_turns = os.environ.get("GOOSE_MAX_TURNS", "200")
        with (
            open(lp, "a") as log_file,
            goose_session(
                workflow_id=workflow_id, stage="execute", ticket_key=ticket_key
            ) as goose_env,
        ):
            log_file.write("\n=== goose run execute recipe ===\n")
            recipe_path = Path(__file__).resolve().parents[2] / "recipes" / "execute.yaml"
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
                ],
                log_file,
                cwd=working_dir,
                env=goose_env,
            )

        # Append reasoning diary to log
        if os.path.exists(reasoning_path):
            reasoning_text = open(reasoning_path).read().strip()
            if reasoning_text:
                with open(lp, "a") as log_file:
                    log_file.write("\n\n" + "=" * 60 + "\n")
                    log_file.write("  AGENT REASONING DIARY\n")
                    log_file.write("=" * 60 + "\n")
                    log_file.write(reasoning_text + "\n")

        if result.returncode != 0:
            click.echo(f"⚠️  Goose exited with code {result.returncode}")

        # Read summary written by the recipe
        try:
            with open(summary_path, "r") as f:
                execution_summary = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            execution_summary = _failure_summary(
                ticket_key, f"Execution summary not written by recipe: {exc}"
            )

        # Persist token usage to SQLite
        if workflow_id:
            try:
                usage = aggregate_token_usage(workflow_id, "execute")
                update_usage_summary(workflow_id, "execute", usage)
            except Exception as exc:  # noqa: BLE001
                click.echo(f"⚠️  Failed to store usage summary: {exc}", err=True)

        # Persist to SQLite
        if workflow_id:
            update_execution_summary(workflow_id, execution_summary)
            new_status = (
                WorkflowStatus.PENDING_PR_APPROVAL
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

        return {
            "execution_summary": execution_summary,
            "pr_url": execution_summary.get("pr_url", ""),
            "failed_node": (
                "execute_plan"
                if execution_summary.get("status") not in ("success", "partial")
                else None
            ),
        }

    finally:
        # Clean up temp files and the working clone
        for path in (work_plan_path, summary_path, reasoning_path):
            try:
                os.unlink(path)
            except OSError:
                pass
        if os.path.isdir(working_dir):
            shutil.rmtree(working_dir, ignore_errors=True)
            click.echo(f"🧹 Cleaned up working directory: {working_dir}")
