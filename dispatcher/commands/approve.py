"""Handlers for --approve-plan and --reject (WorkPlan approval gate)."""

import sys
from typing import TYPE_CHECKING, Optional

import click

import dispatcher.commands.common as common
from dispatcher.commands.follow import submit_and_follow
from state.workflow_status import WorkflowStatus

if TYPE_CHECKING:
    from orchestrator.workflow_service import WorkflowService


def _resolve_pending_approval(
    service: "WorkflowService",
    ticket_key: Optional[str],
    workflow_id: Optional[str],
) -> str:
    if workflow_id:
        return workflow_id
    pending = [
        w
        for w in service.get_by_ticket(ticket_key or "")
        if w.status == WorkflowStatus.PENDING_APPROVAL
    ]
    if not pending:
        click.echo(f"❌ No pending-approval workflow found for ticket: {ticket_key}", err=True)
        sys.exit(1)
    return pending[0].id


def _handle_approve(
    service: "WorkflowService",
    ticket_key: Optional[str],
    workflow_id: Optional[str] = None,
    detach: bool = False,
) -> None:
    resolved_id = _resolve_pending_approval(service, ticket_key, workflow_id)

    detail = service.get(resolved_id)
    if detail is None:
        click.echo(f"❌ Workflow not found: {resolved_id}", err=True)
        sys.exit(1)

    if detail.status == WorkflowStatus.APPROVED:
        click.echo(f"ℹ️  Workflow {resolved_id} is already approved — nothing to do.")
        return

    if detail.status != WorkflowStatus.PENDING_APPROVAL:
        status_val = detail.status.value
        click.echo(
            f"\u274c Workflow {resolved_id} is not pending approval (status: {status_val})",
            err=True,
        )
        sys.exit(1)

    try:
        result = submit_and_follow(
            service,
            service.approve_plan,
            resolved_id,
            workflow_id_hint=resolved_id,
            detach=detach,
        )

        if result.interrupted:
            # Graph paused at await_pr_approval after generate_code.
            click.echo("⏸️  Graph paused at PR approval gate.")
            return

        if result.final_status == WorkflowStatus.PENDING_PR_APPROVAL:
            click.echo("✅ Execution completed — awaiting PR approval")
            if result.pr_url:
                click.echo(f"   PR URL: {result.pr_url}")
        elif result.final_status == WorkflowStatus.FAILED:
            execution_summary = result.execution_summary or {}
            click.echo(
                f"❌ Workflow failed — "
                f"build: {execution_summary.get('build', 'unknown')}, "
                f"tests: {execution_summary.get('tests', 'unknown')}",
                err=True,
            )

        ticket_for_comment = result.ticket_key or ticket_key or detail.ticket_key
        common._post_execution_comment(ticket_for_comment, result.execution_summary)

    except KeyboardInterrupt:
        click.echo("\n⚠️  Workflow interrupted by user", err=True)
        service.mark_interrupted(resolved_id, actor=common._get_actor())
        click.echo(
            f"⚠️  Marked workflow {resolved_id} as FAILED. "
            f"Resume with: dispatcher --retry --workflow-id {resolved_id}",
            err=True,
        )
        sys.exit(130)
    except Exception as e:
        click.echo(f"❌ Error resuming workflow: {e}", err=True)
        sys.exit(1)


def _handle_reject(
    service: "WorkflowService",
    ticket_key: Optional[str],
    reason: Optional[str],
    workflow_id: Optional[str] = None,
    detach: bool = False,
) -> None:
    resolved_id = _resolve_pending_approval(service, ticket_key, workflow_id)

    detail = service.get(resolved_id)
    if detail is None:
        click.echo(f"❌ Workflow not found: {resolved_id}", err=True)
        sys.exit(1)

    if detail.status != WorkflowStatus.PENDING_APPROVAL:
        status_val = detail.status.value
        click.echo(
            f"❌ Workflow {resolved_id} is not pending approval (status: {status_val})",
            err=True,
        )
        sys.exit(1)

    try:
        submit_and_follow(
            service,
            service.reject_plan,
            resolved_id,
            reason,
            workflow_id_hint=resolved_id,
            detach=detach,
        )
        click.echo(f"🚫 Workflow {resolved_id} rejected" + (f": {reason}" if reason else ""))

    except Exception as e:
        click.echo(f"❌ Error resuming workflow: {e}", err=True)
        sys.exit(1)
