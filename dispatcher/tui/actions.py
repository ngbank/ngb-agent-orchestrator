"""Action handlers that delegate to existing CLI command functions."""

from __future__ import annotations

from typing import Optional

from dispatcher.commands.admin import _handle_cancel, _handle_clear_db, _handle_logs
from dispatcher.commands.approve import _handle_approve, _handle_reject
from dispatcher.commands.clarify import _handle_clarify
from dispatcher.commands.pr import _handle_approve_pr, _handle_comment_pr, _handle_reject_pr
from dispatcher.commands.retry import _handle_retry
from dispatcher.commands.run_workflow import _handle_run
from orchestrator.workflow_service import WorkflowService, build_local_workflow_service


class ActionError(Exception):
    """Raised when a TUI action fails."""

    pass


def _service() -> WorkflowService:
    """Build a fresh ``LocalWorkflowService`` for one TUI action.

    AOS-139: every dispatcher handler now takes a ``WorkflowService`` as its
    first argument, so the TUI must supply one. A new instance per call is
    cheap (lazy graph_factory, repo from env) and avoids holding open SQLite
    connections between actions.
    """
    return build_local_workflow_service()


def approve_workflow(ticket_key: Optional[str], workflow_id: Optional[str]) -> str:
    """Approve a pending WorkPlan."""
    try:
        _handle_approve(_service(), ticket_key or "", workflow_id)
        return "Workflow approved."
    except SystemExit as e:
        if e.code != 0:
            raise ActionError("Approval failed.") from e
        return "Workflow approved."
    except Exception as e:
        raise ActionError(f"Approval failed: {e}") from e


def reject_workflow(ticket_key: Optional[str], workflow_id: Optional[str], reason: str) -> str:
    """Reject a pending WorkPlan."""
    try:
        _handle_reject(_service(), ticket_key or "", reason, workflow_id)
        return f"Workflow rejected: {reason}"
    except SystemExit as e:
        if e.code != 0:
            raise ActionError("Rejection failed.") from e
        return f"Workflow rejected: {reason}"
    except Exception as e:
        raise ActionError(f"Rejection failed: {e}") from e


def clarify_workflow(ticket_key: Optional[str], workflow_id: Optional[str]) -> str:
    """Answer WorkPlan clarification questions via editor."""
    try:
        _handle_clarify(_service(), ticket_key or "", workflow_id)
        return "Clarification submitted."
    except SystemExit as e:
        if e.code != 0:
            raise ActionError("Clarification failed.") from e
        return "Clarification submitted."
    except Exception as e:
        raise ActionError(f"Clarification failed: {e}") from e


def retry_workflow(ticket_key: Optional[str], workflow_id: Optional[str]) -> str:
    """Resume a failed workflow."""
    try:
        _handle_retry(_service(), ticket_key or "", workflow_id)
        return "Retry initiated."
    except SystemExit as e:
        if e.code != 0:
            raise ActionError("Retry failed.") from e
        return "Retry initiated."
    except Exception as e:
        raise ActionError(f"Retry failed: {e}") from e


def cancel_workflow(
    ticket_key: Optional[str], workflow_id: Optional[str], reason: Optional[str] = None
) -> str:
    """Cancel an active workflow."""
    try:
        _handle_cancel(_service(), ticket_key or "", reason, workflow_id)
        return "Workflow cancelled."
    except SystemExit as e:
        if e.code != 0:
            raise ActionError("Cancel failed.") from e
        return "Workflow cancelled."
    except Exception as e:
        raise ActionError(f"Cancel failed: {e}") from e


def approve_pr(ticket_key: Optional[str], workflow_id: Optional[str]) -> str:
    """Approve a pending PR."""
    try:
        _handle_approve_pr(_service(), ticket_key or "", workflow_id)
        return "PR approved."
    except SystemExit as e:
        if e.code != 0:
            raise ActionError("PR approval failed.") from e
        return "PR approved."
    except Exception as e:
        raise ActionError(f"PR approval failed: {e}") from e


def comment_pr(ticket_key: Optional[str], workflow_id: Optional[str]) -> str:
    """Comment on a pending PR to trigger re-execution."""
    try:
        _handle_comment_pr(_service(), ticket_key or "", workflow_id)
        return "PR comment submitted."
    except SystemExit as e:
        if e.code != 0:
            raise ActionError("PR comment failed.") from e
        return "PR comment submitted."
    except Exception as e:
        raise ActionError(f"PR comment failed: {e}") from e


def reject_pr(
    ticket_key: Optional[str], workflow_id: Optional[str], reason: Optional[str] = None
) -> str:
    """Reject a pending PR."""
    try:
        _handle_reject_pr(_service(), ticket_key or "", reason, workflow_id)
        return f"PR rejected: {reason}"
    except SystemExit as e:
        if e.code != 0:
            raise ActionError("PR rejection failed.") from e
        return f"PR rejected: {reason}"
    except Exception as e:
        raise ActionError(f"PR rejection failed: {e}") from e


def show_logs(ticket_key: Optional[str], workflow_id: Optional[str]) -> str:
    """Print captured Goose output logs for a workflow."""
    try:
        _handle_logs(_service(), ticket_key, workflow_id)
        return "Logs printed to console."
    except SystemExit as e:
        if e.code != 0:
            raise ActionError("Failed to show logs.") from e
        return "Logs printed to console."
    except Exception as e:
        raise ActionError(f"Failed to show logs: {e}") from e


def clear_database() -> str:
    """Prompt for confirmation then wipe all workflows and checkpoints."""
    try:
        _handle_clear_db(_service())
        return "Database cleared."
    except SystemExit as e:
        if e.code != 0:
            raise ActionError("Clear DB failed.") from e
        return "Database cleared."
    except Exception as e:
        raise ActionError(f"Clear DB failed: {e}") from e


def run_workflow(ticket_key: str, dry_run: bool = False) -> str:
    """Start a new workflow for a ticket."""
    try:
        _handle_run(_service(), ticket_key, dry_run)
        return f"Workflow started for {ticket_key}."
    except SystemExit as e:
        if e.code != 0:
            raise ActionError(f"Failed to start workflow for {ticket_key}.") from e
        return f"Workflow started for {ticket_key}."
    except Exception as e:
        raise ActionError(f"Failed to start workflow for {ticket_key}: {e}") from e
