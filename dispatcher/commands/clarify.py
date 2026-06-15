"""Handler for --clarify (WorkPlan clarification via editor)."""

import os
import subprocess
import sys
import tempfile
from typing import Optional

import click
from langgraph.errors import GraphInterrupt
from langgraph.types import Command

import dispatcher.commands.common as common
from state.workflow_repository import get_workflow, get_workflow_by_ticket
from state.workflow_status import WorkflowStatus


def _handle_clarify(ticket_key: Optional[str], workflow_id: Optional[str] = None) -> None:
    """Collect clarification answers via file-based editing and resume a suspended WorkPlan."""
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
        graph = common.build_orchestrator()
        common.run_graph_stream(
            graph,
            Command(resume={"answers": answers}),
            workflow_id=resolved_id,
            ticket_key=workflow.get("ticket_key") or ticket_key or "",
            thread_config=thread_config,
        )

        final_state = graph.get_state(thread_config).values or {}

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
            click.echo(f"   To approve:  dispatcher --approve-plan --ticket {approve_ticket}")
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
