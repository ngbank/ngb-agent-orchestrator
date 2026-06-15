"""Handlers for --approve-pr, --reject-pr, and --comment-pr."""

import os
import subprocess
import sys
import tempfile
from typing import Optional

import click
from langgraph.errors import GraphInterrupt
from langgraph.types import Command

import dispatcher.commands.common as common
from state.workflow_repository import get_workflow, get_workflow_by_ticket, update_status
from state.workflow_status import WorkflowStatus


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

    thread_config = common.make_thread_config(resolved_id)
    actor = common._get_actor()

    try:
        graph = common.build_orchestrator()
        common.run_graph_stream(
            graph,
            Command(resume={"decision": "approved"}),
            workflow_id=resolved_id,
            ticket_key=ticket_key or workflow.get("ticket_key", ""),
            thread_config=thread_config,
        )

        final_state = graph.get_state(thread_config).values or {}
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

    thread_config = common.make_thread_config(resolved_id)

    try:
        graph = common.build_orchestrator()
        common.run_graph_stream(
            graph,
            Command(resume={"decision": "commented", "comments": comments_text}),
            workflow_id=resolved_id,
            ticket_key=workflow.get("ticket_key") or ticket_key or "",
            thread_config=thread_config,
        )

        final_state = graph.get_state(thread_config).values or {}

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

    thread_config = common.make_thread_config(resolved_id)
    actor = common._get_actor()

    try:
        graph = common.build_orchestrator()
        common.run_graph_stream(
            graph,
            Command(resume={"decision": "rejected", "reason": reason}),
            workflow_id=resolved_id,
            ticket_key=ticket_key or workflow.get("ticket_key", ""),
            thread_config=thread_config,
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
