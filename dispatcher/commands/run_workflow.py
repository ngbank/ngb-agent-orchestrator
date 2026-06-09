"""Handler for the main --ticket workflow run."""

import sys
import uuid

import click
from langgraph.errors import GraphInterrupt

import dispatcher.commands.common as common
from dispatcher.exceptions import TicketAuthError, TicketConfigError, TicketNotFoundError
from state.workflow_repository import update_status
from state.workflow_status import WorkflowStatus


def _handle_run(ticket: str, dry_run: bool) -> None:
    click.echo(f"🚀 Starting workflow for ticket: {ticket}")

    if dry_run:
        click.echo("[DRY RUN] Mode enabled - no changes will be made")
        click.echo(f"[DRY RUN] Would fetch ticket: {ticket}")
        click.echo("[DRY RUN] Would check for duplicate workflows")
        click.echo(f"[DRY RUN] Would create workflow for ticket: {ticket}")
        click.echo("[DRY RUN] Would execute workflow stages")
        click.echo("✅ Dry run completed successfully")
        return

    # Pre-generate a UUID that acts as both the workflow DB ID and the
    # LangGraph thread_id, keeping the two systems in sync.
    workflow_id = str(uuid.uuid4())
    thread_config = {"configurable": {"thread_id": workflow_id}}
    graph = None

    try:
        graph = common.build_orchestrator()
        final_state = graph.invoke(
            {"ticket_key": ticket, "dry_run": False, "workflow_id": workflow_id},
            config=thread_config,
        )

        if final_state.get("error"):
            sys.exit(1)

        wf_id = final_state.get("workflow_id", workflow_id)
        if final_state.get("approval_decision") != "approved":
            # Graph suspended at await_approval — instructions already printed
            # by the node.  Nothing more to do here.
            return

        update_status(
            wf_id,
            WorkflowStatus.COMPLETED,
            actor="dispatcher",
            reason="All stages completed successfully",
        )
        click.echo("🎉 Workflow completed successfully")
        common._post_execution_comment(ticket, final_state.get("execution_summary"))

    except GraphInterrupt:
        # The graph hit interrupt() inside await_approval.  The node already
        # printed the approval instructions and the workflow status is already
        # PENDING_APPROVAL in the DB.
        pass

    except TicketNotFoundError as e:
        click.echo(f"❌ Ticket not found: {e}", err=True)
        sys.exit(1)

    except TicketConfigError as e:
        click.echo(f"❌ JIRA configuration error: {e}", err=True)
        click.echo("   Please check your environment variables:", err=True)
        click.echo("     - JIRA_URL", err=True)
        click.echo("     - JIRA_EMAIL", err=True)
        click.echo("     - JIRA_API_TOKEN", err=True)
        sys.exit(1)

    except TicketAuthError as e:
        click.echo(f"❌ JIRA authentication error: {e}", err=True)
        click.echo("   Please verify your credentials are correct.", err=True)
        sys.exit(1)

    except KeyboardInterrupt:
        click.echo("\n⚠️  Workflow interrupted by user", err=True)
        common._mark_workflow_interrupted(workflow_id, graph, thread_config)
        sys.exit(130)

    except Exception as e:
        click.echo(f"❌ Unhandled error: {e}", err=True)
        sys.exit(1)
