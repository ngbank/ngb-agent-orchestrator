"""Node: check_duplicate — prevent concurrent workflows for the same ticket."""

import click

from graph.work_planner.state import WorkPlannerState
from state.state_store import get_workflow_by_ticket


def check_duplicate(state: WorkPlannerState) -> dict:
    ticket_key = state.get("ticket_key", "")
    workflows = get_workflow_by_ticket(ticket_key)

    completed_count = 0

    for workflow in workflows:
        if workflow["status"].is_active():
            error = f"Workflow already in progress for {ticket_key} " f"(ID: {workflow['id']})"
            click.echo(f"❌ {error}", err=True)
            click.echo("   Cannot start a new workflow while one is active.", err=True)
            click.echo("   Wait for the current workflow to complete or fail.", err=True)
            return {"error": error}
        if workflow["status"].value == "completed":
            completed_count += 1

    if completed_count > 0:
        click.echo(f"⚠️  Warning: {completed_count} completed workflow(s) exist for {ticket_key}")
        click.echo("   Creating new workflow run...")

    return {}
