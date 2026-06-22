"""Handler for --retry (resume a failed or interrupted workflow)."""

import sys
from typing import TYPE_CHECKING, Optional

import click

import dispatcher.commands.common as common
from dispatcher.commands.follow import submit_and_follow
from state.workflow_status import WorkflowStatus

if TYPE_CHECKING:
    from orchestrator.workflow_service import WorkflowService


def _handle_retry(
    service: "WorkflowService",
    ticket_key: Optional[str],
    workflow_id: Optional[str] = None,
    detach: bool = False,
) -> None:
    """Resume a failed workflow from the node that failed.

    Resolution rules:
      - If ``workflow_id`` is given, use it directly.
      - Else if ``ticket_key`` is given, pick the most recent retryable
        (status=FAILED) workflow for that ticket.
    """
    if workflow_id:
        resolved_id = workflow_id
    else:
        candidate = service.get_latest_retryable_by_ticket(ticket_key or "")
        if candidate is None:
            click.echo(
                f"❌ No retryable (failed / in_progress) workflow found for ticket: "
                f"{ticket_key}",
                err=True,
            )
            sys.exit(1)
        resolved_id = candidate.id

    detail = service.get(resolved_id)
    if detail is None:
        click.echo(f"❌ Workflow not found: {resolved_id}", err=True)
        sys.exit(1)

    if not detail.status.is_retryable():
        status_val = detail.status.value
        click.echo(
            f"❌ Workflow {resolved_id} is not retryable (status: {status_val}). "
            f"Only FAILED or IN_PROGRESS workflows can be retried.",
            err=True,
        )
        sys.exit(1)

    if detail.status == WorkflowStatus.IN_PROGRESS:
        click.echo(
            f"⚠️  Workflow {resolved_id} is IN_PROGRESS. "
            "Assuming it was interrupted (Ctrl-C, crash, etc.) and resuming. "
            "If another process is still running it, you may get duplicate work.",
            err=True,
        )

    try:
        result = submit_and_follow(
            service,
            service.retry,
            resolved_id,
            workflow_id_hint=resolved_id,
            detach=detach,
        )
    except ValueError as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)
    except KeyboardInterrupt:
        click.echo("\n⚠️  Retry interrupted by user", err=True)
        service.mark_interrupted(resolved_id, actor=common._get_actor())
        click.echo(
            f"⚠️  Marked workflow {resolved_id} as FAILED. "
            f"Resume with: dispatcher --retry --workflow-id {resolved_id}",
            err=True,
        )
        sys.exit(130)
    except Exception as e:
        # Best-effort: mark the workflow FAILED so it remains visible /
        # retryable rather than stuck in whatever transient state retry()
        # left it in.  Mirrors the old handler's recovery path.
        try:
            service.mark_interrupted(resolved_id, actor=common._get_actor())
        except Exception:
            pass
        click.echo(f"❌ Error during retry: {e}", err=True)
        sys.exit(1)

    # Banner for the retry attempt — read from the latest detail so we can
    # surface the attempt number and resume node.  The service has already
    # incremented retry_count and updated status by this point.
    refreshed = service.get(resolved_id) or detail
    failed_node = result.failed_node or "unknown"
    click.echo(
        f"🔁 Retrying workflow {resolved_id} (attempt #{refreshed.retry_count}) "
        f"from node '{failed_node}'..."
    )

    if result.interrupted:
        click.echo("⏸️  Graph paused at approval gate after retry.")
        return

    execution_summary = result.execution_summary or {}
    exec_status = execution_summary.get("status", "")
    ticket_key_final = result.ticket_key or ticket_key or detail.ticket_key

    if execution_summary and exec_status in ("success", "partial"):
        click.echo("🎉 Workflow completed successfully")
        common._post_execution_comment(ticket_key_final, execution_summary)
    elif execution_summary or result.error or result.failed_node:
        click.echo(
            f"❌ Retry failed — failed_node: {result.failed_node or 'n/a'}",
            err=True,
        )
        if execution_summary:
            common._post_execution_comment(ticket_key_final, execution_summary)
        sys.exit(1)
    else:
        click.echo("⏸️  Retry resumed graph; awaiting next action.")
