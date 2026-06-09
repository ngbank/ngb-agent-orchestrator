"""Node: fetch_ticket — retrieve JIRA ticket details via the API."""

import dataclasses
from typing import Optional

import click

from dispatcher.jira_client import JiraClient
from dispatcher.protocols import TicketSource
from graph.node_result import WorkPlannerNodeResult
from graph.work_planner.state import WorkPlannerState


def fetch_ticket(
    state: WorkPlannerState,
    ticket_source: Optional[TicketSource] = None,
) -> WorkPlannerNodeResult:
    """Fetch a JIRA ticket and store it in state.

    Args:
        state: Current work planner state.
        ticket_source: Optional TicketSource implementation. Defaults to a
            freshly-constructed JiraClient so existing invocations require no
            changes. Tests can inject a stub here.
    """
    ticket_key = state.get("ticket_key", "")
    click.echo(f"📥 Fetching ticket {ticket_key}...")
    source: TicketSource = ticket_source if ticket_source is not None else JiraClient()
    ticket = source.get_ticket(ticket_key)
    click.echo(f"✅ Ticket fetched: {ticket.title}")
    # Store as a plain dict so LangGraph can checkpoint it without needing
    # to register JiraTicket as a custom msgpack type.
    return {"ticket": dataclasses.asdict(ticket)}
