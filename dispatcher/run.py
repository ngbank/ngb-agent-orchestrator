#!/usr/bin/env python3
"""
Agent Orchestrator Dispatcher

Thin CLI entrypoint.  All orchestration logic lives in the LangGraph graph
under ``graph/``.  This module is responsible only for:

  - Parsing CLI arguments
  - Handling the dry-run fast-path
  - Invoking the top-level orchestrator graph
  - Catching domain exceptions that bubble out of the graph
  - Marking the workflow COMPLETED on success

Usage:
    python dispatcher/run.py --ticket AOS-36
    python dispatcher/run.py --ticket AOS-36 --dry-run
"""

import sys
import click
from dotenv import load_dotenv

load_dotenv()

from dispatcher.jira_client import (
    JiraConfigurationError,
    JiraAuthenticationError,
    JiraTicketNotFoundError,
)
from state.state_store import update_status
from state.workflow_status import WorkflowStatus
from graph.builder import build_orchestrator


@click.command()
@click.option(
    '--ticket',
    required=True,
    help='JIRA ticket key (e.g., AOS-36)',
)
@click.option(
    '--dry-run',
    is_flag=True,
    help='Print actions without executing (no API calls or database changes)',
)
def run(ticket: str, dry_run: bool) -> None:
    """
    Main dispatcher entry point for workflow orchestration.

    Fetches the specified JIRA ticket, creates a workflow record, and
    orchestrates the complete workflow lifecycle including plan generation,
    code execution, and PR creation.

    Examples:

        # Run a workflow for a ticket
        python dispatcher/run.py --ticket AOS-36

        # Preview what would happen without executing
        python dispatcher/run.py --ticket AOS-36 --dry-run
    """
    if not ticket or '-' not in ticket:
        click.echo("❌ Invalid ticket format. Expected format: PROJECT-123", err=True)
        sys.exit(1)

    click.echo(f"🚀 Starting workflow for ticket: {ticket}")

    if dry_run:
        click.echo("[DRY RUN] Mode enabled - no changes will be made")
        click.echo(f"[DRY RUN] Would fetch ticket: {ticket}")
        click.echo("[DRY RUN] Would check for duplicate workflows")
        click.echo(f"[DRY RUN] Would create workflow for ticket: {ticket}")
        click.echo("[DRY RUN] Would execute workflow stages")
        click.echo("✅ Dry run completed successfully")
        return

    try:
        graph = build_orchestrator()
        final_state = graph.invoke({"ticket_key": ticket, "dry_run": False})

        if final_state.get("error"):
            # Error was already printed by the node that set it.
            sys.exit(1)

        workflow_id = final_state.get("workflow_id")
        if workflow_id:
            update_status(
                workflow_id,
                WorkflowStatus.COMPLETED,
                actor='dispatcher',
                reason='All stages completed successfully',
            )

        click.echo("🎉 Workflow completed successfully")

    except JiraTicketNotFoundError as e:
        click.echo(f"❌ Ticket not found: {e}", err=True)
        sys.exit(1)

    except JiraConfigurationError as e:
        click.echo(f"❌ JIRA configuration error: {e}", err=True)
        click.echo("   Please check your environment variables:", err=True)
        click.echo("     - JIRA_URL", err=True)
        click.echo("     - JIRA_EMAIL", err=True)
        click.echo("     - JIRA_API_TOKEN", err=True)
        sys.exit(1)

    except JiraAuthenticationError as e:
        click.echo(f"❌ JIRA authentication error: {e}", err=True)
        click.echo("   Please verify your credentials are correct.", err=True)
        sys.exit(1)

    except KeyboardInterrupt:
        click.echo("\n⚠️  Workflow interrupted by user", err=True)
        sys.exit(130)

    except Exception as e:
        click.echo(f"❌ Unhandled error: {e}", err=True)
        sys.exit(1)


if __name__ == '__main__':
    run()
