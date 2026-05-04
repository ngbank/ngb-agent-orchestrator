"""Node: store_plan — persist the validated WorkPlan to SQLite."""

import click

from graph.work_planner.state import WorkPlannerState
from state.state_store import update_work_plan


def store_plan(state: WorkPlannerState) -> dict:
    work_plan_data = state.get("work_plan_data")
    workflow_id = state.get("workflow_id")

    if not work_plan_data or not workflow_id:
        return {}

    ticket_key = state.get("ticket_key", "")
    click.echo("💾 Storing WorkPlan to database...")
    update_work_plan(
        workflow_id,
        work_plan_data,
        actor="dispatcher",
        reason=f"WorkPlan generated for {ticket_key}",
    )
    click.echo("✅ WorkPlan stored to SQLite")
    return {}
