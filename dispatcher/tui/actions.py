"""Action handlers that delegate to existing CLI command functions.

Every action takes the ``WorkflowService`` owned by the TUI app as its first
argument. The CLI handlers themselves already accept a service, so these
wrappers just translate ``SystemExit`` / exceptions into ``ActionError`` for
the TUI's notification layer.
"""

from __future__ import annotations

from typing import Optional

from dispatcher.commands.admin import _handle_cancel, _handle_clear_db, _handle_logs
from dispatcher.commands.approve import _handle_approve, _handle_reject
from dispatcher.commands.clarify import _handle_clarify
from dispatcher.commands.pr import _handle_approve_pr, _handle_comment_pr, _handle_reject_pr
from dispatcher.commands.retry import _handle_retry
from dispatcher.commands.run_workflow import _handle_run
from orchestrator.workflow_service import WorkflowService


class ActionError(Exception):
    """Raised when a TUI action fails."""

    pass


def approve_workflow(
    service: WorkflowService, ticket_key: Optional[str], workflow_id: Optional[str]
) -> str:
    """Approve a pending WorkPlan."""
    try:
        # ``detach=True`` keeps the Textual event loop responsive: the HTTP
        # call still happens, but we do not block waiting for the SSE
        # follower to drain. The TUI's periodic refresh picks up the new
        # status.
        _handle_approve(service, ticket_key or "", workflow_id, detach=True)
        return "Workflow approved."
    except SystemExit as e:
        if e.code != 0:
            raise ActionError("Approval failed.") from e
        return "Workflow approved."
    except Exception as e:
        raise ActionError(f"Approval failed: {e}") from e


def reject_workflow(
    service: WorkflowService,
    ticket_key: Optional[str],
    workflow_id: Optional[str],
    reason: str,
) -> str:
    """Reject a pending WorkPlan."""
    try:
        _handle_reject(service, ticket_key or "", reason, workflow_id, detach=True)
        return f"Workflow rejected: {reason}"
    except SystemExit as e:
        if e.code != 0:
            raise ActionError("Rejection failed.") from e
        return f"Workflow rejected: {reason}"
    except Exception as e:
        raise ActionError(f"Rejection failed: {e}") from e


def clarify_workflow(
    service: WorkflowService, ticket_key: Optional[str], workflow_id: Optional[str]
) -> str:
    """Answer WorkPlan clarification questions via editor."""
    try:
        _handle_clarify(service, ticket_key or "", workflow_id, detach=True)
        return "Clarification submitted."
    except SystemExit as e:
        if e.code != 0:
            raise ActionError("Clarification failed.") from e
        return "Clarification submitted."
    except Exception as e:
        raise ActionError(f"Clarification failed: {e}") from e


def retry_workflow(
    service: WorkflowService, ticket_key: Optional[str], workflow_id: Optional[str]
) -> str:
    """Resume a failed workflow."""
    try:
        _handle_retry(service, ticket_key or "", workflow_id, detach=True)
        return "Retry initiated."
    except SystemExit as e:
        if e.code != 0:
            raise ActionError("Retry failed.") from e
        return "Retry initiated."
    except Exception as e:
        raise ActionError(f"Retry failed: {e}") from e


def cancel_workflow(
    service: WorkflowService,
    ticket_key: Optional[str],
    workflow_id: Optional[str],
    reason: Optional[str] = None,
) -> str:
    """Cancel an active workflow."""
    try:
        _handle_cancel(service, ticket_key or "", reason, workflow_id)
        return "Workflow cancelled."
    except SystemExit as e:
        if e.code != 0:
            raise ActionError("Cancel failed.") from e
        return "Workflow cancelled."
    except Exception as e:
        raise ActionError(f"Cancel failed: {e}") from e


def approve_pr(
    service: WorkflowService, ticket_key: Optional[str], workflow_id: Optional[str]
) -> str:
    """Approve a pending PR."""
    try:
        _handle_approve_pr(service, ticket_key or "", workflow_id, detach=True)
        return "PR approved."
    except SystemExit as e:
        if e.code != 0:
            raise ActionError("PR approval failed.") from e
        return "PR approved."
    except Exception as e:
        raise ActionError(f"PR approval failed: {e}") from e


def comment_pr(
    service: WorkflowService, ticket_key: Optional[str], workflow_id: Optional[str]
) -> str:
    """Comment on a pending PR to trigger re-execution."""
    try:
        _handle_comment_pr(service, ticket_key or "", workflow_id, detach=True)
        return "PR comment submitted."
    except SystemExit as e:
        if e.code != 0:
            raise ActionError("PR comment failed.") from e
        return "PR comment submitted."
    except Exception as e:
        raise ActionError(f"PR comment failed: {e}") from e


def reject_pr(
    service: WorkflowService,
    ticket_key: Optional[str],
    workflow_id: Optional[str],
    reason: Optional[str] = None,
) -> str:
    """Reject a pending PR."""
    try:
        _handle_reject_pr(service, ticket_key or "", reason, workflow_id, detach=True)
        return f"PR rejected: {reason}"
    except SystemExit as e:
        if e.code != 0:
            raise ActionError("PR rejection failed.") from e
        return f"PR rejected: {reason}"
    except Exception as e:
        raise ActionError(f"PR rejection failed: {e}") from e


def show_logs(
    service: WorkflowService, ticket_key: Optional[str], workflow_id: Optional[str]
) -> str:
    """Print captured Goose output logs for a workflow."""
    try:
        _handle_logs(service, ticket_key, workflow_id)
        return "Logs printed to console."
    except SystemExit as e:
        if e.code != 0:
            raise ActionError("Failed to show logs.") from e
        return "Logs printed to console."
    except Exception as e:
        raise ActionError(f"Failed to show logs: {e}") from e


def clear_database(service: WorkflowService) -> str:
    """Prompt for confirmation then wipe all workflows and checkpoints."""
    try:
        _handle_clear_db(service)
        return "Database cleared."
    except SystemExit as e:
        if e.code != 0:
            raise ActionError("Clear DB failed.") from e
        return "Database cleared."
    except Exception as e:
        raise ActionError(f"Clear DB failed: {e}") from e


def run_workflow(service: WorkflowService, ticket_key: str, dry_run: bool = False) -> str:
    """Start a new workflow for a ticket."""
    try:
        _handle_run(service, ticket_key, dry_run, detach=True)
        return f"Workflow started for {ticket_key}."
    except SystemExit as e:
        if e.code != 0:
            raise ActionError(f"Failed to start workflow for {ticket_key}.") from e
        return f"Workflow started for {ticket_key}."
    except Exception as e:
        raise ActionError(f"Failed to start workflow for {ticket_key}: {e}") from e
