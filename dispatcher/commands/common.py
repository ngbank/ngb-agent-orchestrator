"""
Shared constants, utilities, and helpers used across command handlers.

Heavy imports live at this module's top level — they are only triggered when a
command handler submodule (which imports this module) is itself lazily imported
inside a dispatch branch in dispatcher/run.py. This keeps CLI startup fast for
commands that don't need the graph or JIRA stack.
"""

from typing import Optional

import click

from dispatcher.jira_client import JiraClient, JiraCommentError  # noqa: F401
from dispatcher.work_plan_formatter import format_execution_summary_comment
from graph.builder import build_orchestrator  # noqa: F401
from graph.utils import _get_actor  # noqa: F401
from state.repository import get_workflow, update_status
from state.workflow_status import WorkflowStatus

# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

# Status display config: (emoji, label)
_STATUS_DISPLAY = {
    "pending": ("🕐", "pending"),
    "in_progress": ("⚙️ ", "in_progress"),
    "pending_workplan_clarification": ("💬", "pending_workplan_clarification"),
    "pending_approval": ("⏸️ ", "pending_approval"),
    "pending_pr_approval": ("🔍", "pending_pr_approval"),
    "pr_commented": ("💬", "pr_commented"),
    "approved": ("✅", "approved"),
    "rejected": ("🚫", "rejected"),
    "completed": ("🎉", "completed"),
    "failed": ("❌", "failed"),
    "cancelled": ("⛔", "cancelled"),
}

# Node display config: emoji per top-level node name
_NODE_EMOJI = {
    "__start__": "▶ ",
    "work_planner": "📋",
    "await_approval": "⏸️ ",
    "execute_plan": "⚙️ ",
    "__end__": "🏁",
}

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _mark_workflow_interrupted(
    workflow_id: str,
    graph=None,
    thread_config: Optional[dict] = None,
    actor: str = "dispatcher",
) -> None:
    """Mark a workflow as FAILED after a KeyboardInterrupt / Ctrl-C.

    Best-effort: records the node that was about to run as ``failed_node`` in
    the graph state (so ``--retry`` can resume from it) and transitions the DB
    row to FAILED. Safe to call on a workflow that is already terminal.
    """
    workflow = get_workflow(workflow_id)
    if workflow is None or workflow["status"].is_terminal():
        return

    failed_node: Optional[str] = None
    if graph is not None and thread_config is not None:
        try:
            snapshot = graph.get_state(thread_config)
            next_nodes = snapshot.next or ()
            failed_node = next_nodes[0] if next_nodes else None
            graph.update_state(
                thread_config,
                {
                    "error": "Interrupted by user (Ctrl-C)",
                    "failed_node": failed_node or "unknown",
                },
            )
        except Exception:
            pass

    update_status(
        workflow_id,
        WorkflowStatus.FAILED,
        actor=actor,
        reason=(
            f"Interrupted by user (Ctrl-C) at node '{failed_node}'"
            if failed_node
            else "Interrupted by user (Ctrl-C)"
        ),
    )
    click.echo(
        f"⚠️  Marked workflow {workflow_id} as FAILED "
        f"(failed_node: {failed_node or 'unknown'}). "
        f"Resume with: dispatcher --retry --workflow-id {workflow_id}",
        err=True,
    )


def _post_execution_comment(ticket_key: Optional[str], execution_summary: Optional[dict]) -> None:
    """Post execution summary (including pr_url if present) as a JIRA comment."""
    if not ticket_key or not execution_summary:
        return
    try:
        comment = format_execution_summary_comment(execution_summary)
        jira = JiraClient()
        jira.post_comment(ticket_key, comment)
        pr_url = execution_summary.get("pr_url", "")
        if pr_url:
            click.echo(f"🔗 PR created: {pr_url}")
        click.echo(f"💬 Execution summary posted to {ticket_key}")
    except JiraCommentError as e:
        click.echo(f"⚠️  Could not post execution summary to JIRA: {e}", err=True)
