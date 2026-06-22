"""Handlers for --approve-pr, --reject-pr, and --comment-pr."""

import os
import subprocess
import sys
import tempfile
from typing import TYPE_CHECKING, Optional

import click

import dispatcher.commands.common as common  # noqa: F401  (kept for parity)
from dispatcher.commands.follow import submit_and_follow
from state.workflow_status import WorkflowStatus

if TYPE_CHECKING:
    from orchestrator.workflow_service import WorkflowService


def _resolve_pending_pr(
    service: "WorkflowService",
    ticket_key: Optional[str],
    workflow_id: Optional[str],
    flag_name: str,
) -> str:
    if workflow_id:
        return workflow_id
    if ticket_key is None:
        click.echo(f"❌ {flag_name} requires --ticket or --workflow-id", err=True)
        sys.exit(1)
    pending = [
        w
        for w in service.get_by_ticket(ticket_key)
        if w.status == WorkflowStatus.PENDING_PR_APPROVAL
    ]
    if not pending:
        click.echo(f"❌ No pending PR approval workflow found for ticket: {ticket_key}", err=True)
        sys.exit(1)
    return pending[0].id


def _handle_approve_pr(
    service: "WorkflowService",
    ticket_key: Optional[str],
    workflow_id: Optional[str] = None,
    detach: bool = False,
) -> None:
    resolved_id = _resolve_pending_pr(service, ticket_key, workflow_id, "--approve-pr")

    detail = service.get(resolved_id)
    if detail is None:
        click.echo(f"❌ Workflow not found: {resolved_id}", err=True)
        sys.exit(1)

    if detail.status != WorkflowStatus.PENDING_PR_APPROVAL:
        status_val = detail.status.value
        click.echo(
            f"❌ Workflow {resolved_id} is not pending PR approval (status: {status_val})",
            err=True,
        )
        sys.exit(1)

    try:
        submit_and_follow(
            service,
            service.approve_pr,
            resolved_id,
            workflow_id_hint=resolved_id,
            detach=detach,
        )
        click.echo("🎉 Workflow completed successfully — PR approved")
    except Exception as e:
        click.echo(f"❌ Error resuming workflow: {e}", err=True)
        sys.exit(1)


def _handle_comment_pr(
    service: "WorkflowService",
    ticket_key: Optional[str],
    workflow_id: Optional[str] = None,
    detach: bool = False,
) -> None:
    """Collect PR review comments via file-based editing and resume for incremental fixes."""
    if workflow_id:
        resolved_id = workflow_id
        detail = service.get(resolved_id)
        if detail is None:
            click.echo(f"❌ Workflow not found: {resolved_id}", err=True)
            sys.exit(1)
    else:
        if ticket_key is None:
            click.echo("❌ Ticket key is required when workflow id is not provided", err=True)
            sys.exit(1)
        pending = [
            w
            for w in service.get_by_ticket(ticket_key)
            if w.status == WorkflowStatus.PENDING_PR_APPROVAL
        ]
        if not pending:
            click.echo(
                f"❌ No pending PR approval workflow found for ticket: {ticket_key}", err=True
            )
            sys.exit(1)
        resolved_id = pending[0].id
        detail = service.get(resolved_id)
        if detail is None:
            click.echo(f"❌ Workflow not found: {resolved_id}", err=True)
            sys.exit(1)

    if detail.status != WorkflowStatus.PENDING_PR_APPROVAL:
        status_val = detail.status.value
        click.echo(
            f"❌ Workflow {resolved_id} is not pending PR approval (status: {status_val})",
            err=True,
        )
        sys.exit(1)

    workflow_ticket = detail.ticket_key or ticket_key

    click.echo("")
    click.echo(f"💬 PR review comments for workflow: {resolved_id}")
    click.echo(f"   Ticket: {workflow_ticket}")
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

    try:
        result = submit_and_follow(
            service,
            service.comment_pr,
            resolved_id,
            comments_text,
            workflow_id_hint=resolved_id,
            detach=detach,
        )

        if result.error:
            click.echo(f"❌ Workflow error: {result.error}", err=True)
            sys.exit(1)

        refreshed = service.get(resolved_id)
        refreshed_status = refreshed.status if refreshed else None

        if refreshed_status == WorkflowStatus.PENDING_PR_APPROVAL:
            click.echo("")
            click.echo("⏸️  PR still pending approval after re-execution.")
            click.echo(f"   Run:  dispatcher --comment-pr --ticket {workflow_ticket}")
            return

        if refreshed_status == WorkflowStatus.COMPLETED:
            click.echo("")
            click.echo("✅ PR approved and workflow completed.")
            return

        if result.interrupted:
            click.echo("⏸️  Workflow suspended — check status with:  dispatcher --list")

    except Exception as e:
        click.echo(f"❌ Error resuming workflow: {e}", err=True)
        sys.exit(1)


def _handle_reject_pr(
    service: "WorkflowService",
    ticket_key: Optional[str],
    reason: Optional[str],
    workflow_id: Optional[str] = None,
    detach: bool = False,
) -> None:
    resolved_id = _resolve_pending_pr(service, ticket_key, workflow_id, "--reject-pr")

    detail = service.get(resolved_id)
    if detail is None:
        click.echo(f"❌ Workflow not found: {resolved_id}", err=True)
        sys.exit(1)

    if detail.status != WorkflowStatus.PENDING_PR_APPROVAL:
        status_val = detail.status.value
        click.echo(
            f"❌ Workflow {resolved_id} is not pending PR approval (status: {status_val})",
            err=True,
        )
        sys.exit(1)

    try:
        submit_and_follow(
            service,
            service.reject_pr,
            resolved_id,
            reason,
            workflow_id_hint=resolved_id,
            detach=detach,
        )
        click.echo(f"🚫 PR rejected for workflow {resolved_id}" + (f": {reason}" if reason else ""))
    except Exception as e:
        click.echo(f"❌ Error resuming workflow: {e}", err=True)
        sys.exit(1)
