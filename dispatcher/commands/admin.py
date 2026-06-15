"""Handlers for admin/inspection commands: --list, --history, --logs, --clear-db, --cancel."""

import json
import sys
from typing import Optional

import click

import dispatcher.commands.common as common
from dispatcher.constants import NODE_EMOJI, STATUS_DISPLAY
from graph.utils import log_path
from state.workflow_repository import (
    clear_db,
    get_workflow,
    get_workflow_by_ticket,
    list_workflows,
    update_status,
)
from state.workflow_status import WorkflowStatus


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
        emoji, label = STATUS_DISPLAY.get(status_val, ("  ", status_val))
        created = wf["created_at"][:19].replace("T", " ")
        click.echo(f"{wf['ticket_key']:<12} {emoji} {label:<16} {wf['id']}  {created}")


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
    emoji, label = STATUS_DISPLAY.get(status_val, ("  ", status_val))
    click.echo(f"\nWorkflow history for {resolved_ticket} ({resolved_id})")
    click.echo(f"Status: {emoji} {label}")
    click.echo()

    thread_config = common.make_thread_config(resolved_id)
    try:
        graph = common.build_orchestrator()
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
            step = (state.metadata or {}).get("step", "?")
            if step == -1:
                # input step — skip internal detail
                continue
            for task in state.tasks:
                node = task.name
                node_emoji = NODE_EMOJI.get(node, "  ")
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

    actor = common._get_actor()
    for wf in active:
        update_status(
            wf["id"],
            WorkflowStatus.CANCELLED,
            actor=actor,
            reason=reason or "Cancelled by user",
        )
        click.echo(f"🚫 Workflow {wf['id']} cancelled" + (f": {reason}" if reason else ""))
