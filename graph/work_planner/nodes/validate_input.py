"""Node: validate_input — check ticket key format before any I/O."""

import click

from graph.node_result import WorkPlannerNodeResult
from graph.work_planner.state import WorkPlannerState


def validate_input(state: WorkPlannerState) -> WorkPlannerNodeResult:
    ticket_key = state.get("ticket_key", "")
    if not ticket_key or "-" not in ticket_key:
        error = f"Invalid ticket format: '{ticket_key}'. Expected format: PROJECT-123"
        click.echo(f"❌ {error}", err=True)
        return {"error": error, "failed_node": "validate_input"}
    return {}
