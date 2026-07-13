"""Unit tests for ace.pipeline.trace_reader.

Uses the autouse fixtures from tests/conftest.py, which point DB_PATH at a
fresh tmp_path SQLite file and run migrations (including 012 and 013, which
create context_extraction_log and workflows.rejection_reason) before every
test.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ace.pipeline.trace_reader import fetch_eligible_traces
from state import get_connection
from state import workflow_repository as state_store
from state.workflow_status import WorkflowStatus


def _mark_extracted(workflow_id: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO context_extraction_log (workflow_id, extracted_at) VALUES (?, ?)",
            (workflow_id, datetime.now(UTC).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def test_fetch_eligible_traces_returns_terminal_workflows():
    completed_id = state_store.create_workflow(ticket_key="AOS-1")
    state_store.update_status(completed_id, WorkflowStatus.COMPLETED)

    failed_id = state_store.create_workflow(ticket_key="AOS-2")
    state_store.update_status(failed_id, WorkflowStatus.FAILED)

    rejected_id = state_store.create_workflow(ticket_key="AOS-3")
    state_store.update_status(rejected_id, WorkflowStatus.REJECTED, reason="Wrong approach")

    pending_id = state_store.create_workflow(ticket_key="AOS-4")

    bundles = fetch_eligible_traces()

    ids = {b.workflow_id for b in bundles}
    assert ids == {completed_id, failed_id, rejected_id}
    assert pending_id not in ids


def test_fetch_eligible_traces_excludes_already_extracted():
    workflow_id = state_store.create_workflow(ticket_key="AOS-1")
    state_store.update_status(workflow_id, WorkflowStatus.COMPLETED)
    _mark_extracted(workflow_id)

    bundles = fetch_eligible_traces()

    assert bundles == []


def test_fetch_eligible_traces_reads_rejection_reason_without_audit_log_join():
    workflow_id = state_store.create_workflow(ticket_key="AOS-1")
    state_store.update_status(workflow_id, WorkflowStatus.REJECTED, reason="Missed the edge case")

    bundles = fetch_eligible_traces()

    assert len(bundles) == 1
    assert bundles[0].rejection_reason == "Missed the edge case"


def test_fetch_eligible_traces_parses_structured_fields():
    workflow_id = state_store.create_workflow(
        ticket_key="AOS-1", work_plan={"status": "concerns", "tasks": []}
    )
    state_store.update_clarification_history(
        workflow_id, {"round": 1, "concerns": ["x"], "answers": []}
    )
    state_store.update_pr_comments(workflow_id, "Looks good with one nit", actor="reviewer1")
    state_store.update_code_generation_summary(workflow_id, {"status": "success"})
    state_store.update_status(workflow_id, WorkflowStatus.COMPLETED)

    bundles = fetch_eligible_traces()

    assert len(bundles) == 1
    bundle = bundles[0]
    assert bundle.work_plan == {"status": "concerns", "tasks": []}
    assert bundle.code_generation_summary == {"status": "success"}
    assert len(bundle.clarification_history) == 1
    assert bundle.clarification_history[0]["round"] == 1
    assert len(bundle.pr_comments) == 1
    assert bundle.pr_comments[0]["comments"] == "Looks good with one nit"
    assert bundle.pr_comments[0]["actor"] == "reviewer1"


def test_fetch_eligible_traces_skips_rows_with_unparseable_pr_comments():
    workflow_id = state_store.create_workflow(ticket_key="AOS-1")
    state_store.update_status(workflow_id, WorkflowStatus.COMPLETED)

    conn = get_connection()
    try:
        conn.execute(
            "UPDATE workflows SET pr_comments = ? WHERE id = ?",
            ("--- Review round 2026-06-06T00:00:00+00:00 ---\nlegacy text", workflow_id),
        )
        conn.commit()
    finally:
        conn.close()

    bundles = fetch_eligible_traces()

    assert bundles == []


def test_fetch_eligible_traces_orders_newest_first():
    older_id = state_store.create_workflow(ticket_key="AOS-1")
    state_store.update_status(older_id, WorkflowStatus.COMPLETED)

    newer_id = state_store.create_workflow(ticket_key="AOS-2")
    state_store.update_status(newer_id, WorkflowStatus.COMPLETED)

    conn = get_connection()
    try:
        conn.execute(
            "UPDATE workflows SET created_at = ? WHERE id = ?",
            ("2026-01-01T00:00:00+00:00", older_id),
        )
        conn.execute(
            "UPDATE workflows SET created_at = ? WHERE id = ?",
            ("2026-06-01T00:00:00+00:00", newer_id),
        )
        conn.commit()
    finally:
        conn.close()

    bundles = fetch_eligible_traces()

    assert [b.workflow_id for b in bundles] == [newer_id, older_id]


def test_fetch_eligible_traces_respects_limit():
    for i in range(3):
        workflow_id = state_store.create_workflow(ticket_key=f"AOS-{i}")
        state_store.update_status(workflow_id, WorkflowStatus.COMPLETED)

    bundles = fetch_eligible_traces(limit=2)

    assert len(bundles) == 2
