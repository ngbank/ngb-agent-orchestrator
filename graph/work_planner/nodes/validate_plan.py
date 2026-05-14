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

    status = work_plan.status
    questions = work_plan_data.get("questions_for_reviewer", [])
    risks = work_plan_data.get("risks", [])
    needs_clarification = status in ("concerns", "blocked") or bool(questions) or bool(risks)

    if needs_clarification:
        click.echo(f"⚠️  WorkPlan validated but needs clarification (status: {status})")
    else:
        click.echo(f"✅ WorkPlan validated (status: {status})")
    return {}
