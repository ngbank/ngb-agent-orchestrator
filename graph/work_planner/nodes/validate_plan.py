"""Node: validate_plan — schema-validate the generated WorkPlan."""

import click

from dispatcher.work_plan_validator import WorkPlanValidationError, validate_work_plan
from graph.work_planner.state import WorkPlannerState


def validate_plan(state: WorkPlannerState) -> dict:
    work_plan_data = state.get("work_plan_data")
    if not work_plan_data:
        return {}

    click.echo("🔍 Validating WorkPlan...")
    try:
        work_plan = validate_work_plan(work_plan_data)
    except WorkPlanValidationError as e:
        error = str(e)
        click.echo(f"❌ {error}", err=True)
        return {"error": error}

    if work_plan.status == "blocked":
        error = "WorkPlan status is 'blocked' — workflow cannot proceed."
        click.echo(f"❌ {error}", err=True)
        return {"error": error}

    click.echo(f"✅ WorkPlan validated (status: {work_plan.status})")
    return {}
