"""Shared CLI helpers used across command handlers.

After AOS-139 (route CLI through WorkflowService) this module no longer owns
graph/state-store helpers — those live behind ``WorkflowService``.  What
remains is purely presentation / side-effect glue the CLI layer still needs:

* :data:`STATUS_DISPLAY`, :data:`NODE_EMOJI` re-exports.
* :func:`_get_actor` (shared with the rest of the codebase).
* :func:`_post_execution_comment` — formats and posts the execution-summary
  JIRA comment after a successful run.
"""

from typing import Optional

import click

from dispatcher.constants import NODE_EMOJI, STATUS_DISPLAY
from dispatcher.protocols import CommentPoster
from orchestrator.utils import _get_actor  # noqa: F401  (re-exported)

# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

_STATUS_DISPLAY = STATUS_DISPLAY
_NODE_EMOJI = NODE_EMOJI

# Lazy-loaded in _post_execution_comment. Kept as module attributes so tests
# can patch them without importing heavy dependencies at module import time.
JiraClient = None
JiraCommentError = Exception
format_code_generation_summary_comment = None


def _post_execution_comment(
    ticket_key: Optional[str],
    code_generation_summary: Optional[dict],
    comment_poster: Optional[CommentPoster] = None,
) -> None:
    """Post execution summary (including pr_url if present) as a JIRA comment.

    Args:
        ticket_key: The JIRA ticket key to post to.
        code_generation_summary: The execution summary dict from the graph final state.
        comment_poster: Optional CommentPoster implementation. Defaults to a
            freshly-constructed JiraClient so existing call sites require no
            changes.
    """
    global JiraClient, JiraCommentError, format_code_generation_summary_comment

    if not ticket_key or not code_generation_summary:
        return
    try:
        if (
            JiraClient is None
            or JiraCommentError is Exception
            or format_code_generation_summary_comment is None
        ):
            from dispatcher.jira_client import JiraClient as _JiraClient
            from dispatcher.jira_client import JiraCommentError as _JiraCommentError
            from orchestrator.work_planner.utilities import (
                format_code_generation_summary_comment as _format_code_generation_summary_comment,
            )

            if JiraClient is None:
                JiraClient = _JiraClient
            if JiraCommentError is Exception:
                JiraCommentError = _JiraCommentError
            if format_code_generation_summary_comment is None:
                format_code_generation_summary_comment = _format_code_generation_summary_comment

        comment = format_code_generation_summary_comment(code_generation_summary)
        poster: CommentPoster = comment_poster if comment_poster is not None else JiraClient()
        poster.post_comment(ticket_key, comment)
        pr_url = code_generation_summary.get("pr_url", "")
        if pr_url:
            click.echo(f"🔗 PR created: {pr_url}")
        click.echo(f"💬 Execution summary posted to {ticket_key}")
    except JiraCommentError as e:
        click.echo(f"⚠️  Could not post execution summary to JIRA: {e}", err=True)
