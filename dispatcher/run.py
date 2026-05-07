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
    JiraClient,
    JiraCommentError,
    JiraConfigurationError,
    JiraTicketNotFoundError,
)
from dispatcher.work_plan_formatter import format_execution_summary_comment  # noqa: E402
from graph.builder import build_orchestrator  # noqa: E402
from graph.utils import _get_actor, log_path  # noqa: E402
from state.state_store import (  # noqa: E402
    clear_db,
    get_workflow,
    get_workflow_by_ticket,
    list_workflows,
    update_status,
)
from state.workflow_status import WorkflowStatus  # noqa: E402



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
    "--list",
    "do_list",
    is_flag=True,
    help="List workflows and their statuses (optionally filter with --ticket)",
)
@click.option(
    "--history",
    "do_history",
    is_flag=True,
    help="Show node traversal history for a workflow (use with --ticket or --workflow-id)",
)
@click.option(
    "--clear-db",
    "do_clear_db",
    is_flag=True,
    help="Delete all workflows and checkpoints from the local database (prompts for confirmation)",
)
@click.option(
    "--logs",
    "do_logs",
    is_flag=True,
    help="Print captured Goose output logs for a workflow (use with --ticket or --workflow-id)",
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
    do_list: bool,
    do_history: bool,
    do_clear_db: bool,
    do_logs: bool,
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

        # List all workflows
        dispatcher --list

        # List workflows for a specific ticket
        dispatcher --list --ticket AOS-36

        # Show node traversal history for a ticket
        dispatcher --history --ticket AOS-36

        # Show history for a specific workflow ID
        dispatcher --history --workflow-id <uuid>

        # Clear all workflows and checkpoints from the local database
        dispatcher --clear-db
    """
    # --- dispatch to the right sub-command ---
    if do_logs:
        if not ticket and not workflow_id:
            click.echo("\u274c --logs requires --ticket or --workflow-id", err=True)
            sys.exit(1)
        _handle_logs(ticket, workflow_id)
        return

    if do_clear_db:
        _handle_clear_db()
        return

    if do_history:
        if not ticket and not workflow_id:
            click.echo("\u274c --history requires --ticket or --workflow-id", err=True)
            sys.exit(1)
        _handle_history(ticket, workflow_id)
        return

    if do_list:
        _handle_list(ticket)
        return

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
        _post_execution_comment(ticket, final_state.get("execution_summary"))

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
        ticket_key = final_state.get("ticket_key", "")
        update_status(
            wf_id,
            WorkflowStatus.COMPLETED,
            actor=actor,
            reason="All stages completed successfully after approval",
        )
        click.echo("🎉 Workflow completed successfully")
        _post_execution_comment(ticket_key, final_state.get("execution_summary"))

    except Exception as e:
        click.echo(f"❌ Error resuming workflow: {e}", err=True)
        sys.exit(1)


def _post_execution_comment(ticket_key: Optional[str], execution_summary: Optional[dict]) -> None:
    """Post execution summary (including pr_url if present) as a JIRA comment."""
    if not ticket_key or not execution_summary:
        return
    try:
        comment = format_execution_summary_comment(execution_summary)
        jira = JiraClient()
        jira.post_comment(ticket_key, comment)
        pr_url = execution_summary.get("pr_url", "")
        if pr_url:
            click.echo(f"🔗 PR created: {pr_url}")
        click.echo(f"💬 Execution summary posted to {ticket_key}")
    except JiraCommentError as e:
        click.echo(f"⚠️  Could not post execution summary to JIRA: {e}", err=True)


def _handle_logs(ticket_key: Optional[str], workflow_id: Optional[str]) -> None:
    """Print the captured Goose log(s) for a workflow."""
    if workflow_id:
        resolved_id = workflow_id
    else:
        workflows = get_workflow_by_ticket(ticket_key)  # type: ignore[arg-type]
        if not workflows:
            click.echo(f"❌ No workflows found for ticket: {ticket_key}", err=True)
            sys.exit(1)
        resolved_id = sorted(workflows, key=lambda w: w["created_at"])[-1]["id"]

    found_any = False
    for stage in ("plan", "execute"):
        lp = log_path(resolved_id, stage)
        if lp.exists():
            found_any = True
            click.echo(f"\n{'='*60}")
            click.echo(f"  {stage.upper()} LOG  ({lp})")
            click.echo(f"{'='*60}")
            click.echo(lp.read_text())
        else:
            click.echo(f"ℹ️  No {stage} log found at {lp}")

    if not found_any:
        click.echo("No logs found for this workflow.")


def _handle_clear_db() -> None:
    """Prompt for confirmation then wipe all workflows and LangGraph checkpoints."""
    click.echo("⚠️  This will permanently delete ALL workflow records and LangGraph checkpoints.")
    if not click.confirm("Are you sure?", default=False):
        click.echo("Aborted.")
        return
    wf_deleted, cp_deleted = clear_db()
    click.echo(f"🗑️  Cleared {wf_deleted} workflow(s) and {cp_deleted} checkpoint(s).")


# Status display config: (emoji, label)
_STATUS_DISPLAY = {
    "pending": ("🕐", "pending"),
    "in_progress": ("⚙️ ", "in_progress"),
    "pending_approval": ("⏸️ ", "pending_approval"),
    "approved": ("✅", "approved"),
    "rejected": ("🚫", "rejected"),
    "completed": ("🎉", "completed"),
    "failed": ("❌", "failed"),
    "cancelled": ("⛔", "cancelled"),
}


def _handle_list(ticket_key: Optional[str]) -> None:
    workflows = list_workflows(ticket_key=ticket_key, limit=50)

    if not workflows:
        if ticket_key:
            click.echo(f"No workflows found for ticket: {ticket_key}")
        else:
            click.echo("No workflows found.")
        return

    header = f"{'TICKET':<12} {'STATUS':<18} {'WORKFLOW ID':<38} {'CREATED'}"
    click.echo(header)
    click.echo("-" * len(header))

    for wf in workflows:
        status_val = wf["status"].value
        emoji, label = _STATUS_DISPLAY.get(status_val, ("  ", status_val))
        created = wf["created_at"][:19].replace("T", " ")
        click.echo(f"{wf['ticket_key']:<12} {emoji} {label:<16} {wf['id']}  {created}")


# Node display config: emoji per top-level node name
_NODE_EMOJI = {
    "__start__": "▶ ",
    "work_planner": "📋",
    "await_approval": "⏸️ ",
    "execute_plan": "⚙️ ",
    "__end__": "🏁",
}


def _handle_history(ticket_key: Optional[str], workflow_id: Optional[str]) -> None:
    """Print the node traversal history for a workflow, oldest step first."""
    # Resolve workflow_id from ticket if not provided directly
    if workflow_id:
        resolved_id = workflow_id
        wf = get_workflow(resolved_id)
        if wf is None:
            click.echo(f"❌ Workflow not found: {resolved_id}", err=True)
            sys.exit(1)
        resolved_ticket = wf["ticket_key"]
    else:
        workflows = get_workflow_by_ticket(ticket_key)  # type: ignore[arg-type]
        if not workflows:
            click.echo(f"❌ No workflows found for ticket: {ticket_key}", err=True)
            sys.exit(1)
        # Show history for the most recent workflow
        wf = sorted(workflows, key=lambda w: w["created_at"])[-1]
        resolved_id = wf["id"]
        resolved_ticket = ticket_key

    status_val = wf["status"].value
    emoji, label = _STATUS_DISPLAY.get(status_val, ("  ", status_val))
    click.echo(f"\nWorkflow history for {resolved_ticket} ({resolved_id})")
    click.echo(f"Status: {emoji} {label}")
    click.echo()

    thread_config = {"configurable": {"thread_id": resolved_id}}
    try:
        graph = build_orchestrator()
        # get_state_history returns newest-first; reverse for chronological order
        history = list(graph.get_state_history(thread_config))
        history.reverse()
    except Exception as e:
        click.echo(f"❌ Could not read workflow history: {e}", err=True)
        sys.exit(1)

    if not history:
        click.echo("No history found.")
        return

    click.echo(f"  {'STEP':<6} {'NODE':<20} {'OUTCOME'}")
    click.echo(f"  {'-'*5} {'-'*19} {'-'*30}")

    for state in history:
        step = state.metadata.get("step", "?")
        if step == -1:
            # input step — skip internal detail
            continue
        for task in state.tasks:
            node = task.name
            node_emoji = _NODE_EMOJI.get(node, "  ")
            # Determine outcome
            if task.error:
                outcome = f"❌ error: {task.error}"
            elif task.interrupts:
                outcome = "⏸️  interrupted (awaiting approval)"
            elif task.result:
                # Summarise key result fields
                result_keys = list(task.result.keys())
                outcome = f"✅ → {', '.join(result_keys)}"
            else:
                outcome = "✅ done"
            click.echo(f"  {step:<6} {node_emoji} {node:<18} {outcome}")


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


if __name__ == "__main__":
    run()
