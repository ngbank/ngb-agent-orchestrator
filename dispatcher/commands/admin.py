"""Handlers for admin/inspection commands: --list, --history, --logs, --clear-db, --cancel."""

import sys
from typing import TYPE_CHECKING, Optional

import click

import dispatcher.commands.common as common
from dispatcher.constants import NODE_EMOJI, STATUS_DISPLAY

if TYPE_CHECKING:
    from orchestrator.workflow_service import WorkflowService


def _resolve_workflow_id(
    service: "WorkflowService",
    ticket_key: Optional[str],
    workflow_id: Optional[str],
) -> str:
    """Resolve a workflow id from either an explicit id or the latest for a ticket."""
    if workflow_id:
        return workflow_id
    summaries = service.get_by_ticket(ticket_key or "")
    if not summaries:
        click.echo(f"❌ No workflows found for ticket: {ticket_key}", err=True)
        sys.exit(1)
    # get_by_ticket returns newest-first per the Protocol contract.
    return summaries[0].id


def _handle_logs(
    service: "WorkflowService",
    ticket_key: Optional[str],
    workflow_id: Optional[str],
) -> None:
    """Print the captured workflow log for a workflow."""
    resolved_id = _resolve_workflow_id(service, ticket_key, workflow_id)

    chunks = service.read_logs(resolved_id)
    if not chunks:
        click.echo(f"ℹ️  No log found for workflow {resolved_id}")
        return

    for chunk in chunks:
        click.echo(f"\n{'='*60}")
        click.echo(f"  {chunk.stage.upper()} LOG  ({chunk.path})")
        click.echo(f"{'='*60}")
        click.echo(chunk.content)


def _handle_clear_db(service: "WorkflowService") -> None:
    """Prompt for confirmation then wipe all workflows and LangGraph checkpoints."""
    click.echo("⚠️  This will permanently delete ALL workflow records and LangGraph checkpoints.")
    if not click.confirm("Are you sure?", default=False):
        click.echo("Aborted.")
        return
    wf_deleted, cp_deleted = service.clear_db()
    click.echo(f"🗑️  Cleared {wf_deleted} workflow(s) and {cp_deleted} checkpoint(s).")


def _handle_list(service: "WorkflowService", ticket_key: Optional[str]) -> None:
    summaries = service.list(ticket_key=ticket_key, limit=50)

    if not summaries:
        if ticket_key:
            click.echo(f"No workflows found for ticket: {ticket_key}")
        else:
            click.echo("No workflows found.")
        return

    header = f"{'TICKET':<12} {'STATUS':<18} {'WORKFLOW ID':<38} {'CREATED'}"
    click.echo(header)
    click.echo("-" * len(header))

    for wf in summaries:
        status_val = wf.status.value
        emoji, label = STATUS_DISPLAY.get(status_val, ("  ", status_val))
        created = wf.created_at[:19].replace("T", " ")
        click.echo(f"{wf.ticket_key:<12} {emoji} {label:<16} {wf.id}  {created}")


def _handle_history(
    service: "WorkflowService",
    ticket_key: Optional[str],
    workflow_id: Optional[str],
    show_clarifications: bool = False,
) -> None:
    """Print the node traversal history for a workflow, oldest step first."""
    # Resolve workflow_id from ticket if not provided directly
    if workflow_id:
        resolved_id = workflow_id
        detail = service.get(resolved_id)
        if detail is None:
            click.echo(f"❌ Workflow not found: {resolved_id}", err=True)
            sys.exit(1)
    else:
        summaries = service.get_by_ticket(ticket_key or "")
        if not summaries:
            click.echo(f"❌ No workflows found for ticket: {ticket_key}", err=True)
            sys.exit(1)
        # Show history for the most recent workflow
        resolved_id = summaries[0].id
        detail = service.get(resolved_id)
        if detail is None:
            click.echo(f"❌ Workflow not found: {resolved_id}", err=True)
            sys.exit(1)

    status_val = detail.status.value
    emoji, label = STATUS_DISPLAY.get(status_val, ("  ", status_val))
    click.echo(f"\nWorkflow history for {detail.ticket_key} ({resolved_id})")
    click.echo(f"Status: {emoji} {label}")
    click.echo()

    try:
        history = service.get_history(resolved_id)
    except Exception as e:
        click.echo(f"❌ Could not read workflow history: {e}", err=True)
        sys.exit(1)

    if history:
        click.echo(f"  {'STEP':<6} {'NODE':<20} {'OUTCOME'}")
        click.echo(f"  {'-'*5} {'-'*19} {'-'*30}")

        for entry in history:
            node = entry.node
            node_emoji = NODE_EMOJI.get(node, "  ")
            if entry.outcome == "error":
                outcome = f"❌ error: {entry.error}"
            elif entry.outcome == "interrupted":
                outcome = "⏸️  interrupted (awaiting approval)"
            elif entry.result_keys:
                outcome = f"✅ → {', '.join(entry.result_keys)}"
            else:
                outcome = "✅ done"
            click.echo(f"  {entry.step:<6} {node_emoji} {node:<18} {outcome}")
    else:
        click.echo("No history found.")

    # --- Token & turn usage ---
    usage = detail.usage_summary or {}
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
        clarifications = detail.clarification_history or []
        if clarifications:
            click.echo()
            click.echo("  Clarification Q&A History")
            click.echo(f"  {'-'*50}")
            for clarification in clarifications:
                rnd = clarification.get("round", "?")
                actor = clarification.get("actor", "unknown")
                ts = clarification.get("timestamp", "")
                click.echo(f"  Round {rnd}  (actor: {actor},  timestamp: {ts})")
                concerns = clarification.get("concerns", [])
                if concerns:
                    click.echo("    Concerns:")
                    for c in concerns:
                        click.echo(f"      • {c}")
                answers = clarification.get("answers", [])
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
    service: "WorkflowService",
    ticket_key: Optional[str],
    reason: Optional[str],
    workflow_id: Optional[str] = None,
) -> None:
    if workflow_id:
        detail = service.get(workflow_id)
        if detail is None:
            click.echo(f"❌ Workflow not found: {workflow_id}", err=True)
            sys.exit(1)
        active: list = [detail] if detail.status.is_active() else []
    else:
        summaries = service.get_by_ticket(ticket_key or "")
        active = [w for w in summaries if w.status.is_active()]

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
        service.cancel(wf.id, reason=reason, actor=actor)
        click.echo(f"🚫 Workflow {wf.id} cancelled" + (f": {reason}" if reason else ""))
