"""Node: fetch_ticket — retrieve JIRA ticket details via the API."""

import click

from dispatcher.jira_client import JiraClient
from graph.work_planner.state import WorkPlannerState


def fetch_ticket(state: WorkPlannerState) -> dict:
    ticket_key = state.get("ticket_key", "")
    click.echo(f"📥 Fetching ticket {ticket_key}...")
    jira_client = JiraClient()
    ticket = jira_client.get_ticket(ticket_key)
    click.echo(f"✅ Ticket fetched: {ticket.title}")
    return {"ticket": ticket}
