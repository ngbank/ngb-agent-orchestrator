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

import json
import sys
import uuid
from typing import Optional

import click
from dotenv import load_dotenv

load_dotenv()

from langgraph.errors import GraphInterrupt  # noqa: E402
from langgraph.types import Command  # noqa: E402

from dispatcher.jira_client import (  # noqa: E402
    JiraAPIError,
    JiraAuthenticationError,
    JiraClient,
    JiraCommentError,
    JiraConfigurationError,
    JiraTicketNotFoundError,
)
from dispatcher.work_plan_formatter import format_execution_summary_comment  # noqa: E402
from graph.builder import build_orchestrator  # noqa: E402
from graph.retry import prepare_retry  # noqa: E402
from graph.utils import _get_actor, log_path  # noqa: E402
from state.state_store import (  # noqa: E402
    clear_db,
    get_latest_retryable_workflow_by_ticket,
    get_workflow,
    get_workflow_by_ticket,
    increment_retry_count,
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
    "--clarify",
    "do_clarify",
    is_flag=True,
    help="Answer WorkPlan clarification questions (use with --ticket or --workflow-id)",
)
@click.option(
    "--retry",
    "do_retry",
    is_flag=True,
    help="Resume a failed workflow from the node that failed (use with --ticket or --workflow-id)",
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
    "--show-clarifications",
    "do_show_clarifications",
    is_flag=True,
    help="Include clarification Q&A history in --history output (default off)",
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
    "--comment-pr",
    "do_comment_pr",
    is_flag=True,
    help=(
        "Comment on a pending PR to trigger incremental re-execution "
        "(use with --ticket or --workflow-id)"
    ),
)
@click.option(
    "--approve-pr",
    "do_approve_pr",
    is_flag=True,
    help=(
        "Approve a pending PR and mark the workflow as completed "
        "(use with --ticket or --workflow-id)"
    ),
)
@click.option(
    "--reject-pr",
    "do_reject_pr",
    is_flag=True,
    help="Reject a pending PR (use with --ticket or --workflow-id)",
)
@click.option(
    "--reason",
    default=None,
    help="Reason for rejection (used with --reject or --reject-pr)",
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
    do_clarify: bool,
    do_retry: bool,
    do_list: bool,
    do_history: bool,
    do_show_clarifications: bool,
    do_clear_db: bool,
    do_logs: bool,
    do_comment_pr: bool,
    do_approve_pr: bool,
    do_reject_pr: bool,
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

        # Answer WorkPlan clarification questions by ticket key
        dispatcher --clarify --ticket AOS-36

        # Answer WorkPlan clarification questions by workflow ID
        dispatcher --clarify --workflow-id <uuid>

        # Retry a failed workflow by ticket key
        dispatcher --retry --ticket AOS-36

        # Retry a failed workflow by workflow ID
        dispatcher --retry --workflow-id <uuid>

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
        _handle_history(ticket, workflow_id, do_show_clarifications)
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

    if do_clarify:
        if not ticket and not workflow_id:
            click.echo("❌ --clarify requires --ticket or --workflow-id", err=True)
            sys.exit(1)
        _handle_clarify(ticket, workflow_id)
        return

    if do_retry:
        if not ticket and not workflow_id:
            click.echo("❌ --retry requires --ticket or --workflow-id", err=True)
            sys.exit(1)
        _handle_retry(ticket, workflow_id)
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

    if do_approve_pr:
        if not ticket and not workflow_id:
            click.echo("❌ --approve-pr requires --ticket or --workflow-id", err=True)
            sys.exit(1)
        _handle_approve_pr(ticket, workflow_id)
        return

    if do_comment_pr:
        if not ticket and not workflow_id:
            click.echo("❌ --comment-pr requires --ticket or --workflow-id", err=True)
            sys.exit(1)
        _handle_comment_pr(ticket, workflow_id)
        return

    if do_reject_pr:
        if not ticket and not workflow_id:
            click.echo("❌ --reject-pr requires --ticket or --workflow-id", err=True)
            sys.exit(1)
        _handle_reject_pr(ticket, reason, workflow_id)
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


def _mark_workflow_interrupted(
    workflow_id: str,
    graph=None,
    thread_config: Optional[dict] = None,
    actor: str = "dispatcher",
) -> None:
    """Mark a workflow as FAILED after a KeyboardInterrupt / Ctrl-C.

    Best-effort: records the node that was about to run as ``failed_node`` in
    the graph state (so ``--retry`` can resume from it) and transitions the DB
    row to FAILED. Safe to call on a workflow that is already terminal.
    """
    workflow = get_workflow(workflow_id)
    if workflow is None or workflow["status"].is_terminal():
        return

    failed_node: Optional[str] = None
    if graph is not None and thread_config is not None:
        try:
            snapshot = graph.get_state(thread_config)
            next_nodes = snapshot.next or ()
            failed_node = next_nodes[0] if next_nodes else None
            graph.update_state(
                thread_config,
                {
                    "error": "Interrupted by user (Ctrl-C)",
                    "failed_node": failed_node or "unknown",
                },
            )
        except Exception:
            # Best-effort — if the graph state can't be updated, we still
            # transition the DB row so --retry has a chance to recover.
            pass

    update_status(
        workflow_id,
        WorkflowStatus.FAILED,
        actor=actor,
        reason=(
            f"Interrupted by user (Ctrl-C) at node '{failed_node}'"
            if failed_node
            else "Interrupted by user (Ctrl-C)"
        ),
    )
    click.echo(
        f"⚠️  Marked workflow {workflow_id} as FAILED "
        f"(failed_node: {failed_node or 'unknown'}). "
        f"Resume with: dispatcher --retry --workflow-id {workflow_id}",
        err=True,
    )


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

    except JiraAPIError as e:
        click.echo(f"❌ JIRA API error: {e}", err=True)
        click.echo("   Please retry or check JIRA availability/connectivity.", err=True)
        sys.exit(1)

    except KeyboardInterrupt:
        click.echo("\n⚠️  Workflow interrupted by user", err=True)
        _mark_workflow_interrupted(workflow_id, graph, thread_config)
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
        execution_summary = final_state.get("execution_summary") or {}
        exec_status = execution_summary.get("status", "")

        if exec_status in ("success", "partial"):
            update_status(
                wf_id,
                WorkflowStatus.PENDING_PR_APPROVAL,
                actor=actor,
                reason="Execution completed — awaiting PR approval",
            )
            click.echo("✅ Execution completed — awaiting PR approval")
        else:
            update_status(
                wf_id,
                WorkflowStatus.FAILED,
                actor=actor,
                reason=(
                    f"Execution failed: "
                    f"{execution_summary.get('error', exec_status or 'unknown')}"
                ),
            )
            click.echo(
                f"❌ Workflow failed — "
                f"build: {execution_summary.get('build', 'unknown')}, "
                f"tests: {execution_summary.get('tests', 'unknown')}",
                err=True,
            )
        _post_execution_comment(ticket_key, execution_summary if execution_summary else None)

    except GraphInterrupt:
        # Graph paused at await_pr_approval after execute_plan.
        click.echo("⏸️  Graph paused at PR approval gate.")

    except KeyboardInterrupt:
        click.echo("\n⚠️  Workflow interrupted by user", err=True)
        _mark_workflow_interrupted(resolved_id, graph, thread_config, actor=actor)
        sys.exit(130)
    except Exception as e:
        click.echo(f"❌ Error resuming workflow: {e}", err=True)
        sys.exit(1)


def _handle_retry(ticket_key: Optional[str], workflow_id: Optional[str] = None) -> None:
    """Resume a failed workflow from the node that failed.

    Resolution rules:
      - If ``workflow_id`` is given, use it directly.
      - Else if ``ticket_key`` is given, pick the most recent retryable
        (status=FAILED) workflow for that ticket.
    """
    if workflow_id:
        resolved_id = workflow_id
    else:
        workflow = get_latest_retryable_workflow_by_ticket(ticket_key or "")
        if workflow is None:
            click.echo(
                f"❌ No retryable (failed / in_progress) workflow found for ticket: "
                f"{ticket_key}",
                err=True,
            )
            sys.exit(1)
        resolved_id = workflow["id"]

    workflow = get_workflow(resolved_id)
    if workflow is None:
        click.echo(f"❌ Workflow not found: {resolved_id}", err=True)
        sys.exit(1)

    if not workflow["status"].is_retryable():
        status_val = workflow["status"].value
        click.echo(
            f"❌ Workflow {resolved_id} is not retryable (status: {status_val}). "
            f"Only FAILED or IN_PROGRESS workflows can be retried.",
            err=True,
        )
        sys.exit(1)

    if workflow["status"] == WorkflowStatus.IN_PROGRESS:
        click.echo(
            f"⚠️  Workflow {resolved_id} is IN_PROGRESS. "
            "Assuming it was interrupted (Ctrl-C, crash, etc.) and resuming. "
            "If another process is still running it, you may get duplicate work.",
            err=True,
        )

    thread_config = {"configurable": {"thread_id": resolved_id}}
    actor = _get_actor()

    graph = build_orchestrator()
    current_state = graph.get_state(thread_config)
    failed_node = (current_state.values or {}).get("failed_node")

    # IN_PROGRESS workflows interrupted by SIGKILL / OOM / terminal close may
    # not have had a chance to record failed_node. Derive from snapshot.next as
    # a fallback so retry can still proceed.
    if not failed_node:
        next_nodes = current_state.next or ()
        if next_nodes:
            failed_node = next_nodes[0]
            click.echo(
                f"ℹ️  No recorded failed_node; using next-up node '{failed_node}' "
                f"as the resume point.",
                err=True,
            )

    if not failed_node:
        click.echo(
            f"❌ Workflow {resolved_id} has no recorded failed_node and no "
            f"pending next node; cannot determine where to resume.",
            err=True,
        )
        sys.exit(1)

    try:
        prepare_retry(graph, thread_config, failed_node)
    except ValueError as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)

    new_count = increment_retry_count(resolved_id, actor=actor)
    update_status(
        resolved_id,
        WorkflowStatus.IN_PROGRESS,
        actor=actor,
        reason=f"Retry attempt #{new_count} from failed_node '{failed_node}'",
    )
    click.echo(
        f"🔁 Retrying workflow {resolved_id} (attempt #{new_count}) "
        f"from node '{failed_node}'..."
    )

    try:
        final_state = graph.invoke(None, config=thread_config)

        ticket_key_final = final_state.get("ticket_key", workflow["ticket_key"])
        execution_summary = final_state.get("execution_summary") or {}
        exec_status = execution_summary.get("status", "")
        new_failed_node = final_state.get("failed_node")
        graph_error = final_state.get("error")

        if execution_summary and exec_status in ("success", "partial"):
            update_status(
                resolved_id,
                WorkflowStatus.COMPLETED,
                actor=actor,
                reason="All stages completed successfully after retry",
            )
            click.echo("🎉 Workflow completed successfully")
            _post_execution_comment(ticket_key_final, execution_summary)
        elif execution_summary or graph_error or new_failed_node:
            update_status(
                resolved_id,
                WorkflowStatus.FAILED,
                actor=actor,
                reason=(
                    f"Retry failed: "
                    f"{execution_summary.get('error') or graph_error or new_failed_node or 'unknown'}"  # noqa: E501
                ),
            )
            click.echo(
                f"❌ Retry failed — failed_node: {new_failed_node or 'n/a'}",
                err=True,
            )
            if execution_summary:
                _post_execution_comment(ticket_key_final, execution_summary)
            sys.exit(1)
        else:
            # No execution_summary and no error/failed_node — graph likely
            # suspended at await_approval (replanned).  Nothing further to do.
            click.echo("⏸️  Retry resumed graph; awaiting next action.")

    except GraphInterrupt:
        # Graph paused at an interrupt() (e.g., await_approval after replan).
        click.echo("⏸️  Graph paused at approval gate after retry.")
    except KeyboardInterrupt:
        click.echo("\n⚠️  Retry interrupted by user", err=True)
        _mark_workflow_interrupted(resolved_id, graph, thread_config, actor=actor)
        sys.exit(130)
    except Exception as e:
        update_status(
            resolved_id,
            WorkflowStatus.FAILED,
            actor=actor,
            reason=f"Retry crashed: {e}",
        )
        click.echo(f"❌ Error during retry: {e}", err=True)
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
        lp = log_path(resolved_id, stage, ticket_key=ticket_key)
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
    "pending_workplan_clarification": ("💬", "pending_workplan_clarification"),
    "pending_approval": ("⏸️ ", "pending_approval"),
    "pending_pr_approval": ("🔍", "pending_pr_approval"),
    "pr_commented": ("💬", "pr_commented"),
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


def _handle_history(
    ticket_key: Optional[str],
    workflow_id: Optional[str],
    show_clarifications: bool = False,
) -> None:
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

    if history:
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
    else:
        click.echo("No history found.")

    # --- Token & turn usage ---
    usage_raw = wf.get("usage_summary")
    if usage_raw:
        try:
            usage: dict = json.loads(usage_raw) if isinstance(usage_raw, str) else usage_raw
        except (json.JSONDecodeError, TypeError):
            usage = {}
        if usage:
            click.echo()
            click.echo("  Token & Turn Usage")
            click.echo(
                f"  {'Stage':<10} {'Turns':>6}  {'Prompt':>10}  "
                f"{'Completion':>12}  {'Total':>10}  Stop Reasons"
            )
            click.echo(f"  {'-'*9} {'-'*6}  {'-'*10}  {'-'*12}  {'-'*10}  {'-'*20}")
            total_turns = total_prompt = total_completion = total_tokens = 0
            for stage, data in sorted(usage.items()):
                turns = data.get("turns", 0)
                prompt = data.get("prompt_tokens", 0)
                completion = data.get("completion_tokens", 0)
                tokens = data.get("total_tokens", 0)
                reasons = ", ".join(sorted(set(data.get("stop_reasons") or [])))
                click.echo(
                    f"  {stage:<10} {turns:>6,}  {prompt:>10,}  "
                    f"{completion:>12,}  {tokens:>10,}  {reasons}"
                )
                total_turns += turns
                total_prompt += prompt
                total_completion += completion
                total_tokens += tokens
            click.echo(f"  {'-'*9} {'-'*6}  {'-'*10}  {'-'*12}  {'-'*10}")
            click.echo(
                f"  {'TOTAL':<10} {total_turns:>6,}  {total_prompt:>10,}  "
                f"{total_completion:>12,}  {total_tokens:>10,}"
            )

    # --- Clarification Q&A history (opt-in) ---
    if show_clarifications:
        clarifications = wf.get("clarification_history") or []
        if clarifications:
            click.echo()
            click.echo("  Clarification Q&A History")
            click.echo(f"  {'-'*50}")
            for entry in clarifications:
                rnd = entry.get("round", "?")
                actor = entry.get("actor", "unknown")
                ts = entry.get("timestamp", "")
                click.echo(f"  Round {rnd}  (actor: {actor},  timestamp: {ts})")
                concerns = entry.get("concerns", [])
                if concerns:
                    click.echo("    Concerns:")
                    for c in concerns:
                        click.echo(f"      • {c}")
                answers = entry.get("answers", [])
                if answers:
                    click.echo("    Answers:")
                    for ans in answers:
                        if isinstance(ans, dict):
                            click.echo(f"      C: {ans.get('concern', '')}")
                            click.echo(f"      A: {ans.get('answer', '')}")
                        else:
                            click.echo(f"      • {ans}")
                click.echo()
        else:
            click.echo()
            click.echo("  No clarification history found.")


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


def _handle_clarify(ticket_key: Optional[str], workflow_id: Optional[str] = None) -> None:
    """Collect clarification answers via file-based editing and resume a suspended WorkPlan."""
    import os
    import subprocess
    import tempfile

    if workflow_id:
        resolved_id = workflow_id
        workflow = get_workflow(resolved_id)
        if workflow is None:
            click.echo(f"❌ Workflow not found: {resolved_id}", err=True)
            sys.exit(1)
    else:
        if ticket_key is None:
            click.echo("❌ Ticket key is required when workflow id is not provided", err=True)
            sys.exit(1)

        pending = [
            w
            for w in get_workflow_by_ticket(ticket_key)
            if w["status"] == WorkflowStatus.PENDING_WORKPLAN_CLARIFICATION
        ]
        if not pending:
            click.echo(
                f"❌ No workflow pending clarification found for ticket: {ticket_key}", err=True
            )
            sys.exit(1)
        workflow = pending[0]
        resolved_id = workflow["id"]

    if workflow["status"] != WorkflowStatus.PENDING_WORKPLAN_CLARIFICATION:
        status_val = workflow["status"].value
        click.echo(
            f"❌ Workflow {resolved_id} is not pending clarification (status: {status_val})",
            err=True,
        )
        sys.exit(1)

    work_plan = workflow.get("work_plan") or {}
    concerns = work_plan.get("concerns", [])

    if not concerns:
        click.echo("⚠️  No concerns found in the stored WorkPlan for this workflow.", err=True)
        click.echo(
            "   The workflow may have been interrupted before the plan was stored.", err=True
        )
        sys.exit(1)

    click.echo("")
    click.echo(f"📋 WorkPlan clarification for workflow: {resolved_id}")
    click.echo(f"   Ticket: {workflow.get('ticket_key', ticket_key)}")
    click.echo("")

    # Build the concerns file content
    lines = [
        "# WorkPlan Concerns — Add your answers below each concern.",
        "# Lines starting with '#' are ignored.",
        "# Prefix your answer with 'A: ' on the line immediately following each concern.",
        "",
    ]
    for concern in concerns:
        lines.append(f"- {concern}")
        lines.append("A: ")
        lines.append("")

    editor = os.environ.get("EDITOR", "nano")

    with tempfile.NamedTemporaryFile(mode="w+", suffix=".txt", delete=False) as tmp:
        tmp.write("\n".join(lines))
        tmp_path = tmp.name

    try:
        click.echo(f"📝 Launching editor ({editor}) to collect answers...")
        subprocess.run([editor, tmp_path], check=True)
    except FileNotFoundError:
        click.echo(
            f"❌ Editor '{editor}' not found. Set the EDITOR environment variable.", err=True
        )
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        click.echo(f"❌ Editor exited with error code {e.returncode}", err=True)
        sys.exit(1)
    finally:
        # Read the file contents regardless of how editor exited
        pass

    with open(tmp_path, "r", encoding="utf-8") as f:
        edited_lines = f.read().splitlines()

    os.unlink(tmp_path)

    # Parse answers from the edited file
    answers = []
    current_concern = None
    for line in edited_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- "):
            current_concern = stripped[2:]
        elif stripped.startswith("A: ") and current_concern is not None:
            answer = stripped[3:]
            answers.append({"concern": current_concern, "answer": answer})
            current_concern = None

    thread_config = {"configurable": {"thread_id": resolved_id}}

    try:
        graph = build_orchestrator()
        final_state = graph.invoke(
            Command(resume={"answers": answers}),
            config=thread_config,
        )

        if final_state.get("error"):
            click.echo(f"❌ Workflow error: {final_state['error']}", err=True)
            sys.exit(1)

        # Check if the graph has suspended again for another clarification round
        wf_id = final_state.get("workflow_id", resolved_id)
        refreshed = get_workflow(wf_id)
        if refreshed and refreshed["status"] == WorkflowStatus.PENDING_WORKPLAN_CLARIFICATION:
            click.echo("")
            click.echo("⏸️  The plan still needs clarification.")
            click.echo(
                f"   Run:  dispatcher --clarify --ticket {workflow.get('ticket_key', ticket_key)}"
            )
            return

        if refreshed and refreshed["status"] == WorkflowStatus.PENDING_APPROVAL:
            click.echo("")
            click.echo("✅ Plan regenerated and posted to JIRA.")
            approve_ticket = workflow.get("ticket_key", ticket_key)
            click.echo(f"   To approve:  dispatcher --approve --ticket {approve_ticket}")
            reject_ticket = workflow.get("ticket_key", ticket_key)
            click.echo(
                f"   To reject:   dispatcher --reject  --ticket {reject_ticket} " '--reason "..."'
            )

    except GraphInterrupt:
        # Another clarification round is needed — await_workplan_clarification suspended again.
        refreshed = get_workflow(resolved_id)
        if refreshed and refreshed["status"] == WorkflowStatus.PENDING_WORKPLAN_CLARIFICATION:
            click.echo("")
            click.echo("⏸️  The plan still needs clarification.")
            click.echo(
                f"   Run:  dispatcher --clarify --ticket {workflow.get('ticket_key', ticket_key)}"
            )
        else:
            click.echo("⏸️  Workflow suspended — check status with:  dispatcher --list")

    except Exception as e:
        click.echo(f"❌ Error resuming workflow: {e}", err=True)
        sys.exit(1)


def _handle_approve_pr(ticket_key: Optional[str], workflow_id: Optional[str] = None) -> None:
    if workflow_id:
        resolved_id = workflow_id
    else:
        if ticket_key is None:
            click.echo("❌ --approve-pr requires --ticket or --workflow-id", err=True)
            sys.exit(1)
        pending = [
            w
            for w in get_workflow_by_ticket(ticket_key)
            if w["status"] == WorkflowStatus.PENDING_PR_APPROVAL
        ]
        if not pending:
            click.echo(
                f"❌ No pending PR approval workflow found for ticket: {ticket_key}", err=True
            )
            sys.exit(1)
        resolved_id = pending[0]["id"]

    workflow = get_workflow(resolved_id)
    if workflow is None:
        click.echo(f"❌ Workflow not found: {resolved_id}", err=True)
        sys.exit(1)

    if workflow["status"] != WorkflowStatus.PENDING_PR_APPROVAL:
        status_val = workflow["status"].value
        click.echo(
            f"❌ Workflow {resolved_id} is not pending PR approval (status: {status_val})",
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
            reason="PR approved by reviewer",
        )
        click.echo("🎉 Workflow completed successfully — PR approved")

    except Exception as e:
        click.echo(f"❌ Error resuming workflow: {e}", err=True)
        sys.exit(1)


def _handle_comment_pr(ticket_key: Optional[str], workflow_id: Optional[str] = None) -> None:
    """Collect PR review comments via file-based editing and resume for incremental fixes."""
    import os
    import subprocess
    import tempfile

    if workflow_id:
        resolved_id = workflow_id
        workflow = get_workflow(resolved_id)
        if workflow is None:
            click.echo(f"❌ Workflow not found: {resolved_id}", err=True)
            sys.exit(1)
    else:
        if ticket_key is None:
            click.echo("❌ Ticket key is required when workflow id is not provided", err=True)
            sys.exit(1)

        pending = [
            w
            for w in get_workflow_by_ticket(ticket_key)
            if w["status"] == WorkflowStatus.PENDING_PR_APPROVAL
        ]
        if not pending:
            click.echo(
                f"❌ No pending PR approval workflow found for ticket: {ticket_key}", err=True
            )
            sys.exit(1)
        workflow = pending[0]
        resolved_id = workflow["id"]

    if workflow["status"] != WorkflowStatus.PENDING_PR_APPROVAL:
        status_val = workflow["status"].value
        click.echo(
            f"❌ Workflow {resolved_id} is not pending PR approval (status: {status_val})",
            err=True,
        )
        sys.exit(1)

    click.echo("")
    click.echo(f"💬 PR review comments for workflow: {resolved_id}")
    click.echo(f"   Ticket: {workflow.get('ticket_key', ticket_key)}")
    click.echo("")

    editor = os.environ.get("EDITOR", "nano")

    lines = [
        "# PR Review Comments — Add your review comments below.",
        "# Lines starting with '#' are ignored.",
        "# Each comment should be on its own line.",
        "",
    ]

    with tempfile.NamedTemporaryFile(mode="w+", suffix=".txt", delete=False) as tmp:
        tmp.write("\n".join(lines))
        tmp_path = tmp.name

    try:
        click.echo(f"📝 Launching editor ({editor}) to collect comments...")
        subprocess.run([editor, tmp_path], check=True)
    except FileNotFoundError:
        click.echo(
            f"❌ Editor '{editor}' not found. Set the EDITOR environment variable.", err=True
        )
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        click.echo(f"❌ Editor exited with error code {e.returncode}", err=True)
        sys.exit(1)

    with open(tmp_path, "r", encoding="utf-8") as f:
        edited_lines = f.read().splitlines()

    os.unlink(tmp_path)

    comments_lines = [
        line for line in edited_lines if line.strip() and not line.strip().startswith("#")
    ]
    comments_text = "\n".join(comments_lines)

    if not comments_text.strip():
        click.echo("⚠️  No comments provided. Aborting.", err=True)
        sys.exit(1)

    thread_config = {"configurable": {"thread_id": resolved_id}}

    try:
        graph = build_orchestrator()
        final_state = graph.invoke(
            Command(resume={"decision": "commented", "comments": comments_text}),
            config=thread_config,
        )

        if final_state.get("error"):
            click.echo(f"❌ Workflow error: {final_state['error']}", err=True)
            sys.exit(1)

        wf_id = final_state.get("workflow_id", resolved_id)
        refreshed = get_workflow(wf_id)
        if refreshed and refreshed["status"] == WorkflowStatus.PENDING_PR_APPROVAL:
            click.echo("")
            click.echo("⏸️  PR still pending approval after re-execution.")
            tk = workflow.get("ticket_key", ticket_key)
            click.echo(f"   Run:  dispatcher --comment-pr --ticket {tk}")
            return

        if refreshed and refreshed["status"] == WorkflowStatus.COMPLETED:
            click.echo("")
            click.echo("✅ PR approved and workflow completed.")

    except GraphInterrupt:
        refreshed = get_workflow(resolved_id)
        if refreshed and refreshed["status"] == WorkflowStatus.PENDING_PR_APPROVAL:
            click.echo("")
            click.echo("⏸️  PR still pending approval after re-execution.")
            tk = workflow.get("ticket_key", ticket_key)
            click.echo(f"   Run:  dispatcher --comment-pr --ticket {tk}")
        else:
            click.echo("⏸️  Workflow suspended — check status with:  dispatcher --list")

    except Exception as e:
        click.echo(f"❌ Error resuming workflow: {e}", err=True)
        sys.exit(1)


def _handle_reject_pr(
    ticket_key: Optional[str], reason: Optional[str], workflow_id: Optional[str] = None
) -> None:
    if workflow_id:
        resolved_id = workflow_id
    else:
        if ticket_key is None:
            click.echo("❌ --reject-pr requires --ticket or --workflow-id", err=True)
            sys.exit(1)
        pending = [
            w
            for w in get_workflow_by_ticket(ticket_key)
            if w["status"] == WorkflowStatus.PENDING_PR_APPROVAL
        ]
        if not pending:
            click.echo(
                f"❌ No pending PR approval workflow found for ticket: {ticket_key}", err=True
            )
            sys.exit(1)
        resolved_id = pending[0]["id"]

    workflow = get_workflow(resolved_id)
    if workflow is None:
        click.echo(f"❌ Workflow not found: {resolved_id}", err=True)
        sys.exit(1)

    if workflow["status"] != WorkflowStatus.PENDING_PR_APPROVAL:
        status_val = workflow["status"].value
        click.echo(
            f"❌ Workflow {resolved_id} is not pending PR approval (status: {status_val})",
            err=True,
        )
        sys.exit(1)

    thread_config = {"configurable": {"thread_id": resolved_id}}
    actor = _get_actor()

    try:
        graph = build_orchestrator()
        graph.invoke(
            Command(resume={"decision": "rejected", "reason": reason}),
            config=thread_config,
        )
        update_status(
            resolved_id,
            WorkflowStatus.REJECTED,
            actor=actor,
            reason=reason or "PR rejected by reviewer",
        )
        click.echo(f"🚫 PR rejected for workflow {resolved_id}" + (f": {reason}" if reason else ""))

    except Exception as e:
        click.echo(f"❌ Error resuming workflow: {e}", err=True)
        sys.exit(1)


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
