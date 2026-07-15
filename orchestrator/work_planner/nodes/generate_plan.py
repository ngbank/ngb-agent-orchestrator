"""Node: generate_plan — invoke the Goose plan recipe to generate a WorkPlan JSON."""

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import click

from orchestrator.failure import mark_failure
from orchestrator.litellm_callbacks import aggregate_token_usage
from orchestrator.utils import goose_session, run_and_tee
from orchestrator.work_planner.state import (
    GeneratePlanInputState,
    GeneratePlanOutputState,
)
from state.workflow_repository import update_usage_summary

_RECIPE_PATH = Path(__file__).resolve().parent.parent / "recipes" / "plan.yaml"

logger = logging.getLogger(__name__)


def generate_plan(state: GeneratePlanInputState) -> GeneratePlanOutputState:
    """Invoke the Goose plan recipe and return the resulting WorkPlan as state.

    1. Creates a temp file path for the output JSON.
    2. Shells out to `goose run --recipe orchestrator/work_planner/recipes/plan.yaml`.
    3. Reads and parses the WorkPlan JSON written by the recipe.
    4. Returns {"work_plan_data": <dict>} on success.
    5. Returns {"error": <message>} on any failure so route_after_generate_plan
       sends the workflow to error_handler.
    """
    ticket_key = state.get("ticket_key", "")
    workflow_id = state.get("workflow_id") or ticket_key
    clarifications = state.get("clarifications") or []
    working_dir = state.get("working_dir")

    summary_fd, output_path = tempfile.mkstemp(
        suffix="_workplan.json",
        prefix=f"{ticket_key}_",
    )
    os.close(summary_fd)

    # Write clarifications to a temp file so the recipe can read them
    clarifications_path = None
    if clarifications:
        clar_fd, clarifications_path = tempfile.mkstemp(
            suffix="_clarifications.json",
            prefix=f"{ticket_key}_",
        )
        os.close(clar_fd)
        with open(clarifications_path, "w") as f:
            json.dump(clarifications, f, indent=2)

    try:
        round_num = len(clarifications)
        if round_num:
            click.echo(
                f"🪿 Re-running plan recipe for {ticket_key} "
                f"with {round_num} clarification round(s)..."
            )
        else:
            click.echo(f"🪿 Running plan recipe for {ticket_key}...")

        cmd = [
            "goose",
            "run",
            "--recipe",
            str(_RECIPE_PATH),
            "--max-turns",
            os.environ.get("GOOSE_MAX_TURNS", "200"),
            "--params",
            f"ticket_key={ticket_key}",
            "--params",
            f"output_path={output_path}",
        ]
        if clarifications_path:
            cmd.extend(["--params", f"clarifications_path={clarifications_path}"])

        if working_dir and not os.path.isdir(working_dir):
            return mark_failure(
                "generate_plan",
                f"Working directory does not exist: {working_dir}",
            )

        logger.info("=== goose run plan recipe ===")
        with goose_session(
            workflow_id=workflow_id, stage="plan", ticket_key=ticket_key
        ) as goose_env:
            run_kwargs: dict[str, Any] = {"env": goose_env}
            if working_dir:
                run_kwargs["cwd"] = working_dir
            result = run_and_tee(cmd, "subprocess.goose", **run_kwargs)

        # Persist token usage to SQLite
        try:
            usage = aggregate_token_usage(workflow_id, "plan")
            update_usage_summary(workflow_id, "plan", usage)
        except Exception as exc:  # noqa: BLE001
            click.echo(f"⚠️  Failed to store usage summary: {exc}", err=True)

        if result.returncode != 0:
            return mark_failure(
                "generate_plan",
                f"Goose plan recipe exited with code {result.returncode}",
            )

        try:
            with open(output_path, "r") as f:
                work_plan_data = json.load(f)
        except FileNotFoundError:
            return mark_failure(
                "generate_plan",
                "Goose plan recipe did not write output file",
            )
        except json.JSONDecodeError as exc:
            return mark_failure(
                "generate_plan",
                f"Goose plan recipe wrote invalid JSON: {exc}",
            )

        if not work_plan_data:
            return mark_failure(
                "generate_plan",
                "Goose plan recipe wrote empty WorkPlan",
            )

        click.echo(f"✅ WorkPlan generated for {ticket_key}")
        return {"work_plan_data": work_plan_data}

    finally:
        if os.path.exists(output_path):
            os.unlink(output_path)
        if clarifications_path and os.path.exists(clarifications_path):
            os.unlink(clarifications_path)
