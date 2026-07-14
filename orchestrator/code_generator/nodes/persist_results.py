"""Node: persist_results — write code generation summary and status to SQLite."""

import click

from orchestrator.code_generator.state import PersistResultsInputState, PersistResultsOutputState
from orchestrator.litellm_callbacks import aggregate_token_usage
from state.workflow_repository import (
    update_code_generation_summary,
    update_status,
    update_usage_summary,
)
from state.workflow_status import WorkflowStatus


def persist_results(state: PersistResultsInputState) -> PersistResultsOutputState:
    """Persist the code generation summary and transition workflow status.

    On the happy path this also aggregates and stores token usage.
    On the error path (exec_error set) only the failure summary and FAILED
    status are persisted — token aggregation is skipped.

    Reads:  workflow_id, code_generation_summary, exec_error
    Writes: pr_url, failed_node
    Side-effects: SQLite writes via update_code_generation_summary / update_status /
                  update_usage_summary
    """
    workflow_id = state.get("workflow_id")
    code_generation_summary = state.get("code_generation_summary") or {}
    exec_error = state.get("exec_error")

    if workflow_id:
        if not exec_error:
            try:
                usage = aggregate_token_usage(workflow_id, "generate_code")
                update_usage_summary(workflow_id, "generate_code", usage)
            except Exception as exc:  # noqa: BLE001
                click.echo(f"⚠️  Failed to store usage summary: {exc}", err=True)

        update_code_generation_summary(workflow_id, code_generation_summary)

        exec_status = code_generation_summary.get("status")
        pr_url_for_status = code_generation_summary.get("pr_url", "")
        new_status = (
            WorkflowStatus.PENDING_PR_APPROVAL
            if not exec_error and exec_status in ("success", "partial") and pr_url_for_status
            else WorkflowStatus.FAILED
        )
        update_status(
            workflow_id,
            new_status,
            pr_url=pr_url_for_status or None,
            actor="generate_code",
        )
        click.echo(
            f"{chr(0x2705) if new_status == WorkflowStatus.PENDING_PR_APPROVAL else chr(0x274c)} "
            f"Execution {code_generation_summary.get('status')} — "
            f"branch: {code_generation_summary.get('branch', 'n/a')}, "
            f"build: {code_generation_summary.get('build')}, "
            f"tests: {code_generation_summary.get('tests')}"
        )

    pr_url = code_generation_summary.get("pr_url", "")
    is_failure = (
        bool(exec_error)
        or code_generation_summary.get("status") not in ("success", "partial")
        or not pr_url
    )
    return {
        "pr_url": pr_url,
        "failed_node": "generate_code" if is_failure else None,
    }
