"""Handler for --clarify (WorkPlan clarification via editor)."""

import os
import subprocess
import sys
import tempfile
from typing import TYPE_CHECKING, Optional

import click

import dispatcher.commands.common as common  # noqa: F401  (kept for symmetry)
from state.workflow_status import WorkflowStatus

if TYPE_CHECKING:
    from orchestrator.workflow_service import WorkflowService


def _handle_clarify(
    service: "WorkflowService",
    ticket_key: Optional[str],
    workflow_id: Optional[str] = None,
) -> None:
    """Collect clarification answers via file-based editing and resume a suspended WorkPlan."""
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
            if w.status == WorkflowStatus.PENDING_WORKPLAN_CLARIFICATION
        ]
        if not pending:
            click.echo(
                f"❌ No workflow pending clarification found for ticket: {ticket_key}", err=True
            )
            sys.exit(1)
        resolved_id = pending[0].id
        detail = service.get(resolved_id)
        if detail is None:
            click.echo(f"❌ Workflow not found: {resolved_id}", err=True)
            sys.exit(1)

    if detail.status != WorkflowStatus.PENDING_WORKPLAN_CLARIFICATION:
        status_val = detail.status.value
        click.echo(
            f"❌ Workflow {resolved_id} is not pending clarification (status: {status_val})",
            err=True,
        )
        sys.exit(1)

    work_plan = detail.work_plan or {}
    concerns = work_plan.get("concerns", [])

    if not concerns:
        click.echo("⚠️  No concerns found in the stored WorkPlan for this workflow.", err=True)
        click.echo(
            "   The workflow may have been interrupted before the plan was stored.", err=True
        )
        sys.exit(1)

    workflow_ticket = detail.ticket_key or ticket_key

    click.echo("")
    click.echo(f"📋 WorkPlan clarification for workflow: {resolved_id}")
    click.echo(f"   Ticket: {workflow_ticket}")
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

    with open(tmp_path, "r", encoding="utf-8") as f:
        edited_lines = f.read().splitlines()

    os.unlink(tmp_path)

    # Parse answers from the edited file
    answers = []
    current_concern: Optional[str] = None
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

    try:
        result = service.submit_clarification(resolved_id, answers)

        if result.error:
            click.echo(f"❌ Workflow error: {result.error}", err=True)
            sys.exit(1)

        # The graph may have suspended again (interrupted=True) or completed a
        # round trip and stored a new plan; either way, read the refreshed
        # status to decide the user-facing message.
        refreshed = service.get(resolved_id)
        refreshed_status = refreshed.status if refreshed else None

        if refreshed_status == WorkflowStatus.PENDING_WORKPLAN_CLARIFICATION:
            click.echo("")
            click.echo("⏸️  The plan still needs clarification.")
            click.echo(f"   Run:  dispatcher --clarify --ticket {workflow_ticket}")
            return

        if refreshed_status == WorkflowStatus.PENDING_APPROVAL:
            click.echo("")
            click.echo("✅ Plan regenerated and posted to JIRA.")
            click.echo(f"   To approve:  dispatcher --approve-plan --ticket {workflow_ticket}")
            click.echo(
                f"   To reject:   dispatcher --reject  --ticket {workflow_ticket} " '--reason "..."'
            )
            return

        if result.interrupted:
            click.echo("⏸️  Workflow suspended — check status with:  dispatcher --list")

    except Exception as e:
        click.echo(f"❌ Error resuming workflow: {e}", err=True)
        sys.exit(1)
