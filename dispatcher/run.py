#!/usr/bin/env python3
"""
Agent Orchestrator Dispatcher

Thin CLI entrypoint.  All orchestration logic lives under dispatcher/commands/.
This module is responsible only for:

  - Parsing CLI arguments
  - Constructing (or accepting via ``ctx.obj``) a ``WorkflowService``
  - Dispatching to the appropriate command handler (lazily loaded)

Each handler submodule is imported only when the relevant command is actually
invoked, which keeps ``dispatcher --list`` / ``dispatcher --help`` near-instant.
The WorkflowService is also constructed lazily so light-weight invocations
(``--help``, ``--tui``) do not pay for repository / graph setup.

Usage:
    python -m dispatcher.run --ticket AOS-36
    python -m dispatcher.run --ticket AOS-36 --dry-run
    python -m dispatcher.run --approve-plan --workflow-id <uuid>
    python -m dispatcher.run --reject --ticket AOS-36 --reason "scope too broad"
"""

import sys
from typing import TYPE_CHECKING, Optional

import click
from dotenv import load_dotenv

from orchestrator.logging_setup import setup_logging
from orchestrator.runtime_secrets import load_runtime_secrets_from_keyvault

if TYPE_CHECKING:
    from orchestrator.workflow_service import WorkflowService

load_dotenv()
load_runtime_secrets_from_keyvault()

# Initialize logging based on LOG_LEVEL environment variable
setup_logging()


def _resolve_service(ctx: click.Context) -> "WorkflowService":
    """Return the WorkflowService for this invocation.

    Tests inject a fake via ``runner.invoke(run, args, obj=fake_service)``;
    production builds the default ``LocalWorkflowService`` lazily so commands
    that do not need it (``--help``, ``--tui``) avoid the import cost.
    """
    if ctx.obj is not None:
        return ctx.obj
    from orchestrator.workflow_service import build_local_workflow_service

    service = build_local_workflow_service()
    ctx.obj = service
    return service


@click.command()
@click.pass_context
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
    "--approve-plan",
    "do_approve_plan",
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
    help="Target a specific workflow by ID (use with --approve-plan or --reject)",
)
@click.option(
    "--tui",
    "do_tui",
    is_flag=True,
    help="Launch the interactive Textual TUI for workflow management",
)
def run(
    ctx: click.Context,
    ticket: Optional[str],
    dry_run: bool,
    do_approve_plan: bool,
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
    reason: Optional[str],
    workflow_id: Optional[str],
    do_tui: bool,
) -> None:
    """
    Main dispatcher entry point for workflow orchestration.

    Examples:

        # Run a workflow for a ticket
        dispatcher --ticket AOS-36

        # Preview what would happen without executing
        dispatcher --ticket AOS-36 --dry-run

        # Approve by ticket key
        dispatcher --approve-plan --ticket AOS-36

        # Approve by workflow ID
        dispatcher --approve-plan --workflow-id <uuid>

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
    if do_tui:
        from dispatcher.tui.app import run_tui

        run_tui()
        return

    service = _resolve_service(ctx)

    if do_logs:
        if not ticket and not workflow_id:
            click.echo("\u274c --logs requires --ticket or --workflow-id", err=True)
            sys.exit(1)
        from dispatcher.commands.admin import _handle_logs

        _handle_logs(service, ticket, workflow_id)
        return

    if do_clear_db:
        from dispatcher.commands.admin import _handle_clear_db

        _handle_clear_db(service)
        return

    if do_history:
        if not ticket and not workflow_id:
            click.echo("\u274c --history requires --ticket or --workflow-id", err=True)
            sys.exit(1)
        from dispatcher.commands.admin import _handle_history

        _handle_history(service, ticket, workflow_id, do_show_clarifications)
        return

    if do_list:
        from dispatcher.commands.admin import _handle_list

        _handle_list(service, ticket)
        return

    if do_cancel:
        if not ticket and not workflow_id:
            click.echo("\u274c --cancel requires --ticket or --workflow-id", err=True)
            sys.exit(1)
        from dispatcher.commands.admin import _handle_cancel

        _handle_cancel(service, ticket, reason, workflow_id)
        return

    if do_clarify:
        if not ticket and not workflow_id:
            click.echo("\u274c --clarify requires --ticket or --workflow-id", err=True)
            sys.exit(1)
        from dispatcher.commands.clarify import _handle_clarify

        _handle_clarify(service, ticket, workflow_id)
        return

    if do_retry:
        if not ticket and not workflow_id:
            click.echo("\u274c --retry requires --ticket or --workflow-id", err=True)
            sys.exit(1)
        from dispatcher.commands.retry import _handle_retry

        _handle_retry(service, ticket, workflow_id)
        return

    if do_approve_plan:
        if not ticket and not workflow_id:
            click.echo("\u274c --approve-plan requires --ticket or --workflow-id", err=True)
            sys.exit(1)
        from dispatcher.commands.approve import _handle_approve

        _handle_approve(service, ticket, workflow_id)
        return

    if do_reject:
        if not ticket and not workflow_id:
            click.echo("\u274c --reject requires --ticket or --workflow-id", err=True)
            sys.exit(1)
        from dispatcher.commands.approve import _handle_reject

        _handle_reject(service, ticket, reason, workflow_id)
        return

    if do_approve_pr:
        if not ticket and not workflow_id:
            click.echo("\u274c --approve-pr requires --ticket or --workflow-id", err=True)
            sys.exit(1)
        from dispatcher.commands.pr import _handle_approve_pr

        _handle_approve_pr(service, ticket, workflow_id)
        return

    if do_comment_pr:
        if not ticket and not workflow_id:
            click.echo("\u274c --comment-pr requires --ticket or --workflow-id", err=True)
            sys.exit(1)
        from dispatcher.commands.pr import _handle_comment_pr

        _handle_comment_pr(service, ticket, workflow_id)
        return

    if do_reject_pr:
        if not ticket and not workflow_id:
            click.echo("\u274c --reject-pr requires --ticket or --workflow-id", err=True)
            sys.exit(1)
        from dispatcher.commands.pr import _handle_reject_pr

        _handle_reject_pr(service, ticket, reason, workflow_id)
        return

    if not ticket:
        click.echo(
            "\u274c --ticket is required when not using --approve-plan or --reject", err=True
        )
        sys.exit(1)

    if "-" not in ticket:
        click.echo("\u274c Invalid ticket format. Expected format: PROJECT-123", err=True)
        sys.exit(1)

    from dispatcher.commands.run_workflow import _handle_run

    _handle_run(service, ticket, dry_run)


if __name__ == "__main__":
    run()
