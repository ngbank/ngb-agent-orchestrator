#!/usr/bin/env python3
"""
Agent Orchestrator Dispatcher

Thin CLI entrypoint.  All orchestration logic lives in the LangGraph graph
under ``graph/``.  This module is responsible only for:

  - Parsing CLI arguments
  - Handling the dry-run fast-path
  - Invoking the top-level orchestrator graph
  - Catching domain exceptions that bubble out of the graph
  - Handling GraphInterrupt (pending-approval pause)
  - Resuming interrupted graphs via --approve / --reject

Usage:
    python -m dispatcher.run --ticket AOS-36
    python -m dispatcher.run --ticket AOS-36 --dry-run
    python -m dispatcher.run --approve <workflow_id>
    python -m dispatcher.run --reject <workflow_id> --reason "scope too broad"
"""

import getpass
import sys
import uuid
from typing import Optional

import click
from dotenv import load_dotenv

load_dotenv()

from langgraph.errors import GraphInterrupt  # noqa: E402
from langgraph.types import Command  # noqa: E402

from dispatcher.jira_client import (  # noqa: E402
    JiraAuthenticationError,
    JiraConfigurationError,
    JiraTicketNotFoundError,
)
from graph.builder import build_orchestrator  # noqa: E402
from state.state_store import get_workflow, get_workflow_by_ticket, update_status  # noqa: E402
from state.workflow_status import WorkflowStatus  # noqa: E402


def _get_actor() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


@click.command()
@click.option(
    "--ticket",
    default=None,
    help="JIRA ticket key (e.g., AOS-36)",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print actions without executing (no API calls or database changes)",
)
@click.option(
    "--approve",
    "do_approve",
    is_flag=True,
    help="Approve the pending WorkPlan (use with --ticket or --workflow-id)",
)
@click.option(
    "--reject",
    "do_reject",
    is_flag=True,
    help="Reject the pending WorkPlan (use with --ticket or --workflow-id)",
)
@click.option(
    "--cancel",
    "do_cancel",
    is_flag=True,
    help="Cancel any active workflow (use with --ticket or --workflow-id)",
)
@click.option(
    "--reason",
    default=None,
    help="Reason for rejection (used with --reject)",
)
@click.option(
    "--workflow-id",
    "workflow_id",
    default=None,
    metavar="UUID",
    help="Target a specific workflow by ID (use with --approve or --reject)",
)
def run(
    ticket: str,
    dry_run: bool,
    do_approve: bool,
    do_reject: bool,
    do_cancel: bool,
    reason: str,
    workflow_id: str,
) -> None:
    """
    Main dispatcher entry point for workflow orchestration.

    Examples:

        # Run a workflow for a ticket
        dispatcher --ticket AOS-36

        # Preview what would happen without executing
        dispatcher --ticket AOS-36 --dry-run

        # Approve by ticket key
        dispatcher --approve --ticket AOS-36

        # Approve by workflow ID
        dispatcher --approve --workflow-id <uuid>

        # Reject by ticket key
        dispatcher --reject --ticket AOS-36 --reason "scope too broad"

        # Reject by workflow ID
        dispatcher --reject --workflow-id <uuid> --reason "scope too broad"

        # Cancel an active workflow by ticket key
        dispatcher --cancel --ticket AOS-36

        # Cancel an active workflow by workflow ID
        dispatcher --cancel --workflow-id <uuid>
    """
    # --- dispatch to the right sub-command ---
    if do_cancel:
        if not ticket and not workflow_id:
            click.echo("❌ --cancel requires --ticket or --workflow-id", err=True)
            sys.exit(1)
        _handle_cancel(ticket, reason, workflow_id)
        return

    if do_approve:
        if not ticket and not workflow_id:
            click.echo("❌ --approve requires --ticket or --workflow-id", err=True)
            sys.exit(1)
        _handle_approve(ticket, workflow_id)
        return

    if do_reject:
        if not ticket and not workflow_id:
            click.echo("❌ --reject requires --ticket or --workflow-id", err=True)
            sys.exit(1)
        _handle_reject(ticket, reason, workflow_id)
        return

    if not ticket:
        click.echo("❌ --ticket is required when not using --approve or --reject", err=True)
        sys.exit(1)

    if "-" not in ticket:
        click.echo("❌ Invalid ticket format. Expected format: PROJECT-123", err=True)
        sys.exit(1)

    _handle_run(ticket, dry_run)


# ---------------------------------------------------------------------------
# Sub-handlers
# ---------------------------------------------------------------------------


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

    try:
        graph = build_orchestrator()
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

    except GraphInterrupt:
        # The graph hit interrupt() inside await_approval.  The node already
        # printed the approval instructions and the workflow status is already
        # PENDING_APPROVAL in the DB.
        pass

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


def _handle_approve(ticket_key: str, workflow_id: Optional[str] = None) -> None:
    if workflow_id:
        resolved_id = workflow_id
    else:
        pending = [
            w
            for w in get_workflow_by_ticket(ticket_key)
            if w["status"] == WorkflowStatus.PENDING_APPROVAL
        ]
        if not pending:
            click.echo(f"❌ No pending-approval workflow found for ticket: {ticket_key}", err=True)
            sys.exit(1)
        resolved_id = pending[0]["id"]

    workflow = get_workflow(resolved_id)
    if workflow is None:
        click.echo(f"❌ Workflow not found: {resolved_id}", err=True)
        sys.exit(1)

    if workflow["status"] == WorkflowStatus.APPROVED:
        click.echo(f"ℹ️  Workflow {resolved_id} is already approved — nothing to do.")
        return

    if workflow["status"] != WorkflowStatus.PENDING_APPROVAL:
        status_val = workflow["status"].value
        click.echo(
            f"\u274c Workflow {resolved_id} is not pending approval (status: {status_val})",
            err=True,
        )
        sys.exit(1)

    thread_config = {"configurable": {"thread_id": resolved_id}}
    actor = _get_actor()

    try:
        graph = build_orchestrator()
        final_state = graph.invoke(
            Command(resume={"decision": "approved"}),
            config=thread_config,
        )

        wf_id = final_state.get("workflow_id", resolved_id)
        update_status(
            wf_id,
            WorkflowStatus.COMPLETED,
            actor=actor,
            reason="All stages completed successfully after approval",
        )
        click.echo("🎉 Workflow completed successfully")

    except Exception as e:
        click.echo(f"❌ Error resuming workflow: {e}", err=True)
        sys.exit(1)


def _handle_cancel(
    ticket_key: str, reason: Optional[str], workflow_id: Optional[str] = None
) -> None:
    if workflow_id:
        resolved_id = workflow_id
        workflow = get_workflow(resolved_id)
        if workflow is None:
            click.echo(f"❌ Workflow not found: {resolved_id}", err=True)
            sys.exit(1)
        active = [workflow] if workflow["status"].is_active() else []
    else:
        active = [w for w in get_workflow_by_ticket(ticket_key) if w["status"].is_active()]

    if not active:
        click.echo(
            (
                f"❌ No active workflow found for ticket: {ticket_key}"
                if ticket_key
                else f"❌ Workflow not active: {workflow_id}"
            ),
            err=True,
        )
        sys.exit(1)

    actor = _get_actor()
    for wf in active:
        update_status(
            wf["id"],
            WorkflowStatus.CANCELLED,
            actor=actor,
            reason=reason or "Cancelled by user",
        )
        click.echo(f"🚫 Workflow {wf['id']} cancelled" + (f": {reason}" if reason else ""))


def _handle_reject(ticket_key: str, reason: str, workflow_id: Optional[str] = None) -> None:
    if workflow_id:
        resolved_id = workflow_id
    else:
        pending = [
            w
            for w in get_workflow_by_ticket(ticket_key)
            if w["status"] == WorkflowStatus.PENDING_APPROVAL
        ]
        if not pending:
            click.echo(f"❌ No pending-approval workflow found for ticket: {ticket_key}", err=True)
            sys.exit(1)
        resolved_id = pending[0]["id"]

    workflow = get_workflow(resolved_id)
    if workflow is None:
        click.echo(f"❌ Workflow not found: {resolved_id}", err=True)
        sys.exit(1)

    if workflow["status"] != WorkflowStatus.PENDING_APPROVAL:
        status_val = workflow["status"].value
        click.echo(
            f"❌ Workflow {resolved_id} is not pending approval (status: {status_val})",
            err=True,
        )
        sys.exit(1)

    thread_config = {"configurable": {"thread_id": resolved_id}}

    try:
        graph = build_orchestrator()
        graph.invoke(
            Command(resume={"decision": "rejected", "reason": reason}),
            config=thread_config,
        )
        click.echo(f"🚫 Workflow {resolved_id} rejected" + (f": {reason}" if reason else ""))

    except Exception as e:
        click.echo(f"❌ Error resuming workflow: {e}", err=True)
        sys.exit(1)

    except Exception as e:
        click.echo(f"❌ Error resuming workflow: {e}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    run()
