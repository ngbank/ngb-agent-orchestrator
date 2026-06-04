"""Node: persist_results — write execution summary and status to SQLite."""

import click

from graph.executor.state import ExecutionState
from graph.litellm_callbacks import aggregate_token_usage
from state.workflow_repository import update_execution_summary, update_status, update_usage_summary
from state.workflow_status import WorkflowStatus


def persist_results(state: ExecutionState) -> dict:
    """Persist the execution summary and transition workflow status.

    On the happy path this also aggregates and stores token usage.
    On the error path (exec_error set) only the failure summary and FAILED
    status are persisted — token aggregation is skipped.

    Reads:  workflow_id, execution_summary, exec_error
    Writes: pr_url, failed_node
    Side-effects: SQLite writes via update_execution_summary / update_status /
                  update_usage_summary
    """
    workflow_id = state.get("workflow_id")
    execution_summary = state.get("execution_summary") or {}
    exec_error = state.get("exec_error")

    if workflow_id:
        if not exec_error:
            try:
                usage = aggregate_token_usage(workflow_id, "execute")
                update_usage_summary(workflow_id, "execute", usage)
            except Exception as exc:  # noqa: BLE001
                click.echo(f"⚠️  Failed to store usage summary: {exc}", err=True)

        update_execution_summary(workflow_id, execution_summary)

        exec_status = execution_summary.get("status")
        new_status = (
            WorkflowStatus.PENDING_PR_APPROVAL
            if not exec_error and exec_status in ("success", "partial")
            else WorkflowStatus.FAILED
        )
        update_status(workflow_id, new_status, actor="execute_plan")
        click.echo(
            f"{chr(0x2705) if new_status == WorkflowStatus.PENDING_PR_APPROVAL else chr(0x274c)} "
            f"Execution {execution_summary.get('status')} — "
            f"branch: {execution_summary.get('branch', 'n/a')}, "
            f"build: {execution_summary.get('build')}, "
            f"tests: {execution_summary.get('tests')}"
        )

    is_failure = bool(exec_error) or execution_summary.get("status") not in ("success", "partial")
    return {
        "pr_url": execution_summary.get("pr_url", ""),
        "failed_node": "execute_plan" if is_failure else None,
    }
