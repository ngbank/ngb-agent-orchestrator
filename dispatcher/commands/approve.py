"""Handlers for --approve-plan and --reject (WorkPlan approval gate)."""

import sys
from typing import Optional

import click
from langgraph.errors import GraphInterrupt
from langgraph.types import Command

import dispatcher.commands.common as common

from state.workflow_repository import get_workflow, get_workflow_by_ticket, update_status
from state.workflow_status import WorkflowStatus


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
    actor = common._get_actor()

    try:
        graph = common.build_orchestrator()
        common.run_graph_stream(
            graph,
            Command(resume={"decision": "approved"}),
            workflow_id=resolved_id,
            ticket_key=ticket_key,
            thread_config=thread_config,
        )

        final_state = graph.get_state(thread_config).values or {}

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
        common._post_execution_comment(ticket_key, execution_summary if execution_summary else None)

    except GraphInterrupt:
        # Graph paused at await_pr_approval after execute_plan.
        click.echo("⏸️  Graph paused at PR approval gate.")

    except KeyboardInterrupt:
        click.echo("\n⚠️  Workflow interrupted by user", err=True)
        common._mark_workflow_interrupted(resolved_id, graph, thread_config, actor=actor)
        sys.exit(130)
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
        graph = common.build_orchestrator()
        common.run_graph_stream(
            graph,
            Command(resume={"decision": "rejected", "reason": reason}),
            workflow_id=resolved_id,
            ticket_key=ticket_key,
            thread_config=thread_config,
        )

        click.echo(f"🚫 Workflow {resolved_id} rejected" + (f": {reason}" if reason else ""))

    except Exception as e:
        click.echo(f"❌ Error resuming workflow: {e}", err=True)
        sys.exit(1)
