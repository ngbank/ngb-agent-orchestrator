"""Node: validate_plan — schema-validate the generated WorkPlan."""

import click

from orchestrator.work_planner.state import (
    ValidatePlanInputState,
    ValidatePlanOutputState,
)
from orchestrator.work_planner.utilities import (
    WorkPlanValidationError,
    validate_work_plan,
)


def validate_plan(state: ValidatePlanInputState) -> ValidatePlanOutputState:
    work_plan_data = state.get("work_plan_data")
    if not work_plan_data:
        return {}

    click.echo("🔍 Validating WorkPlan...")
    try:
        work_plan = validate_work_plan(work_plan_data)
    except WorkPlanValidationError as e:
        error = str(e)
        click.echo(f"❌ {error}", err=True)
        return {"error": error, "failed_node": "validate_plan"}

    status = work_plan.status
    needs_clarification = status in ("concerns", "blocked")

    if needs_clarification:
        click.echo(f"⚠️  WorkPlan validated but needs clarification (status: {status})")
    else:
        click.echo(f"✅ WorkPlan validated (status: {status})")
    return {}
