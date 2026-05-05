"""Node: generate_plan — invoke the Goose plan recipe to generate a WorkPlan JSON."""

import json
import os
import tempfile

import click

from graph.utils import log_path, run_and_tee
from graph.work_planner.state import WorkPlannerState


def generate_plan(state: WorkPlannerState) -> dict:
    """Invoke the Goose plan recipe and return the resulting WorkPlan as state.

    1. Creates a temp file path for the output JSON.
    2. Shells out to `goose run --recipe recipes/plan.yaml`.
    3. Reads and parses the WorkPlan JSON written by the recipe.
    4. Returns {"work_plan_data": <dict>} on success.
    5. Returns {"error": <message>} on any failure so route_after_generate_plan
       sends the workflow to error_handler.
    """
    ticket_key = state.get("ticket_key", "")
    workflow_id = state.get("workflow_id") or ticket_key

    summary_fd, output_path = tempfile.mkstemp(
        suffix="_workplan.json",
        prefix=f"{ticket_key}_",
    )
    os.close(summary_fd)

    try:
        lp = log_path(workflow_id, "plan")
        click.echo(f"🪿 Running plan recipe for {ticket_key}... (log: {lp})")
        with open(lp, "w") as log_file:
            result = run_and_tee(
                [
                    "goose",
                    "run",
                    "--recipe",
                    "recipes/plan.yaml",
                    "--params",
                    f"ticket_key={ticket_key}",
                    "--params",
                    f"output_path={output_path}",
                ],
                log_file,
            )

        if result.returncode != 0:
            return {"error": f"Goose plan recipe exited with code {result.returncode}"}

        try:
            with open(output_path, "r") as f:
                work_plan_data = json.load(f)
        except FileNotFoundError:
            return {"error": "Goose plan recipe did not write output file"}
        except json.JSONDecodeError as exc:
            return {"error": f"Goose plan recipe wrote invalid JSON: {exc}"}

        if not work_plan_data:
            return {"error": "Goose plan recipe wrote empty WorkPlan"}

        click.echo(f"✅ WorkPlan generated for {ticket_key}")
        return {"work_plan_data": work_plan_data}

    finally:
        if os.path.exists(output_path):
            os.unlink(output_path)
