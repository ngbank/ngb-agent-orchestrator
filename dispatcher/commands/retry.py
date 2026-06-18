"""Handler for --retry (resume a failed or interrupted workflow)."""

import sys
from typing import Optional

import click
from langgraph.errors import GraphInterrupt

import dispatcher.commands.common as common
from orchestrator.retry import prepare_retry
from state.workflow_repository import (
    get_latest_retryable_workflow_by_ticket,
    get_workflow,
    increment_retry_count,
    update_status,
)
from state.workflow_status import WorkflowStatus


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

    thread_config = common.make_thread_config(resolved_id)
    actor = common._get_actor()

    graph = common.build_orchestrator()
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
        common.run_graph_stream(
            graph,
            None,
            workflow_id=resolved_id,
            ticket_key=workflow.get("ticket_key", ticket_key or ""),
            thread_config=thread_config,
        )

        final_state = graph.get_state(thread_config).values or {}

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
            common._post_execution_comment(ticket_key_final, execution_summary)
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
                common._post_execution_comment(ticket_key_final, execution_summary)
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
        common._mark_workflow_interrupted(resolved_id, graph, thread_config, actor=actor)
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
