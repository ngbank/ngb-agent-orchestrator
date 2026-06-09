"""
Shared constants, utilities, and helpers used across command handlers.

Heavy dependencies are imported lazily inside helper functions so modules that
only consume lightweight helpers/constants do not pay graph/JIRA startup costs.
"""

from typing import Optional

import click

from dispatcher.constants import NODE_EMOJI, STATUS_DISPLAY
from dispatcher.protocols import CommentPoster
from graph.utils import _get_actor  # noqa: F401
from state.workflow_repository import get_workflow, update_status
from state.workflow_status import WorkflowStatus

# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

_STATUS_DISPLAY = STATUS_DISPLAY
_NODE_EMOJI = NODE_EMOJI

# Lazy-loaded in _post_execution_comment. Kept as module attributes so tests
# can patch them without importing heavy dependencies at module import time.
JiraClient = None
JiraCommentError = Exception
format_execution_summary_comment = None

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def build_orchestrator(*args, **kwargs):
    """Lazily import and construct the orchestrator graph."""
    from graph.builder import build_orchestrator as _build_orchestrator

    return _build_orchestrator(*args, **kwargs)


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


def _post_execution_comment(
    ticket_key: Optional[str],
    execution_summary: Optional[dict],
    comment_poster: Optional[CommentPoster] = None,
) -> None:
    """Post execution summary (including pr_url if present) as a JIRA comment.

    Args:
        ticket_key: The JIRA ticket key to post to.
        execution_summary: The execution summary dict from the graph final state.
        comment_poster: Optional CommentPoster implementation. Defaults to a
            freshly-constructed JiraClient so existing call sites require no
            changes.
    """
    global JiraClient, JiraCommentError, format_execution_summary_comment

    if not ticket_key or not execution_summary:
        return
    try:
        if (
            JiraClient is None
            or JiraCommentError is Exception
            or format_execution_summary_comment is None
        ):
            from dispatcher.jira_client import JiraClient as _JiraClient
            from dispatcher.jira_client import JiraCommentError as _JiraCommentError
            from dispatcher.work_plan_formatter import (
                format_execution_summary_comment as _format_execution_summary_comment,
            )

            if JiraClient is None:
                JiraClient = _JiraClient
            if JiraCommentError is Exception:
                JiraCommentError = _JiraCommentError
            if format_execution_summary_comment is None:
                format_execution_summary_comment = _format_execution_summary_comment

        comment = format_execution_summary_comment(execution_summary)
        poster: CommentPoster = comment_poster if comment_poster is not None else JiraClient()
        poster.post_comment(ticket_key, comment)
        pr_url = execution_summary.get("pr_url", "")
        if pr_url:
            click.echo(f"🔗 PR created: {pr_url}")
        click.echo(f"💬 Execution summary posted to {ticket_key}")
    except JiraCommentError as e:
        click.echo(f"⚠️  Could not post execution summary to JIRA: {e}", err=True)
