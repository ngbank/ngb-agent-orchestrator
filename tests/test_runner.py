"""Unit tests for ace.pipeline.runner and ace.pipeline.trace_reader.fetch_trace_by_id.

Tests verify:
- proceed verdict: reflect + curate called; context_extraction_log row inserted
- skip verdict: no reflect/curate; context_extraction_log row still inserted
- flag verdict: no reflect/curate; context_extraction_log row still inserted
- pipeline exception: audit_log entry written; no context_extraction_log row
- dry_run: pipeline runs evaluate+reflect but no DB writes
- workflow_id flag: single specific workflow processed, bypasses eligibility filter
- limit flag: caps number of workflows fetched
- fetch_trace_by_id: returns bundle for known ID, None for missing/non-terminal
"""

from __future__ import annotations

from unittest.mock import patch

from ace.models import CandidateItem
from ace.pipeline.curator import CurationResult
from ace.pipeline.runner import run_mining
from ace.pipeline.trace_reader import TraceBundle, fetch_trace_by_id
from state import get_connection
from state import workflow_repository as state_store
from state.workflow_status import WorkflowStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_workflow(
    ticket_key: str = "AOS-1",
    status: WorkflowStatus = WorkflowStatus.COMPLETED,
    rejection_reason: str | None = None,
) -> str:
    wf_id = state_store.create_workflow(ticket_key=ticket_key)
    state_store.update_status(wf_id, status, reason=rejection_reason)
    return wf_id


def _extraction_log_count(workflow_id: str) -> int:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM context_extraction_log WHERE workflow_id = ?",
            (workflow_id,),
        ).fetchone()
        return row[0]
    finally:
        conn.close()


def _audit_failure_count(workflow_id: str) -> int:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE workflow_id = ? AND action = 'learning_pipeline_failed'",
            (workflow_id,),
        ).fetchone()
        return row[0]
    finally:
        conn.close()


def _make_bundle(workflow_id: str = "wf-1", ticket_key: str = "AOS-1") -> TraceBundle:
    return TraceBundle(
        workflow_id=workflow_id,
        ticket_key=ticket_key,
        status="completed",
        created_at="2026-05-01T10:00:00+00:00",
        work_plan={"status": "pass"},
        code_generation_summary={"status": "success"},
        clarification_history=[],
        pr_comments=[],
        rejection_reason=None,
    )


def _candidate() -> CandidateItem:
    return CandidateItem(
        pattern_type="approach",
        scope="codebase_wide",
        scope_value=None,
        description="Always run migrations before deploying code changes.",
        initial_confidence=0.7,
        evidence=[],
    )


# ---------------------------------------------------------------------------
# fetch_trace_by_id
# ---------------------------------------------------------------------------


def test_fetch_trace_by_id_returns_bundle_for_known_terminal_workflow():
    wf_id = _make_workflow()

    bundle = fetch_trace_by_id(wf_id)

    assert bundle is not None
    assert bundle.workflow_id == wf_id
    assert bundle.ticket_key == "AOS-1"


def test_fetch_trace_by_id_returns_none_for_unknown_workflow():
    result = fetch_trace_by_id("non-existent-id")
    assert result is None


def test_fetch_trace_by_id_returns_none_for_non_terminal_workflow():
    wf_id = state_store.create_workflow(ticket_key="AOS-1")

    result = fetch_trace_by_id(wf_id)

    assert result is None


def test_fetch_trace_by_id_bypasses_extraction_log():
    wf_id = _make_workflow()
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO context_extraction_log (workflow_id, extracted_at) VALUES (?, '2026-05-01T00:00:00+00:00')",
            (wf_id,),
        )
        conn.commit()
    finally:
        conn.close()

    bundle = fetch_trace_by_id(wf_id)

    assert bundle is not None
    assert bundle.workflow_id == wf_id


# ---------------------------------------------------------------------------
# run_mining — proceed verdict
# ---------------------------------------------------------------------------


def test_run_mining_proceed_calls_reflect_and_curate():
    wf_id = _make_workflow(status=WorkflowStatus.FAILED)
    # Force clarification history so evaluate() returns "proceed"
    state_store.update_clarification_history(wf_id, {"round": 1, "concerns": ["x"], "answers": []})

    candidates = [_candidate()]
    curation = CurationResult(created=1)

    with (
        patch("ace.pipeline.runner.evaluate", return_value="proceed") as mock_eval,
        patch("ace.pipeline.runner.reflect", return_value=candidates) as mock_reflect,
        patch("ace.pipeline.runner.curate", return_value=curation) as mock_curate,
    ):
        result = run_mining()

    mock_eval.assert_called_once()
    mock_reflect.assert_called_once()
    mock_curate.assert_called_once()
    assert result.processed == 1
    assert result.succeeded == 1
    assert result.failed == 0
    assert result.curation.created == 1
    assert _extraction_log_count(wf_id) == 1


def test_run_mining_proceed_inserts_extraction_log_on_success():
    wf_id = _make_workflow(status=WorkflowStatus.COMPLETED)

    with (
        patch("ace.pipeline.runner.evaluate", return_value="proceed"),
        patch("ace.pipeline.runner.reflect", return_value=[]),
        patch("ace.pipeline.runner.curate", return_value=CurationResult()),
    ):
        run_mining()

    assert _extraction_log_count(wf_id) == 1


# ---------------------------------------------------------------------------
# run_mining — skip verdict
# ---------------------------------------------------------------------------


def test_run_mining_skip_inserts_extraction_log_without_reflect():
    wf_id = _make_workflow()

    with (
        patch("ace.pipeline.runner.evaluate", return_value="skip"),
        patch("ace.pipeline.runner.reflect") as mock_reflect,
    ):
        result = run_mining()

    mock_reflect.assert_not_called()
    assert result.skipped == 1
    assert result.succeeded == 1
    assert _extraction_log_count(wf_id) == 1


# ---------------------------------------------------------------------------
# run_mining — flag verdict
# ---------------------------------------------------------------------------


def test_run_mining_flag_inserts_extraction_log_without_reflect():
    wf_id = _make_workflow()

    with (
        patch("ace.pipeline.runner.evaluate", return_value="flag"),
        patch("ace.pipeline.runner.reflect") as mock_reflect,
    ):
        result = run_mining()

    mock_reflect.assert_not_called()
    assert result.flagged == 1
    assert result.succeeded == 1
    assert _extraction_log_count(wf_id) == 1


# ---------------------------------------------------------------------------
# run_mining — failure path
# ---------------------------------------------------------------------------


def test_run_mining_pipeline_exception_writes_audit_log():
    wf_id = _make_workflow()

    with patch("ace.pipeline.runner.evaluate", side_effect=RuntimeError("boom")):
        result = run_mining()

    assert result.failed == 1
    assert result.succeeded == 0
    assert _extraction_log_count(wf_id) == 0
    assert _audit_failure_count(wf_id) == 1


def test_run_mining_reflector_error_writes_audit_log():
    from ace.pipeline.reflector import ReflectorError

    wf_id = _make_workflow()

    with (
        patch("ace.pipeline.runner.evaluate", return_value="proceed"),
        patch("ace.pipeline.runner.reflect", side_effect=ReflectorError("llm timeout")),
    ):
        result = run_mining()

    assert result.failed == 1
    assert _extraction_log_count(wf_id) == 0
    assert _audit_failure_count(wf_id) == 1


def test_run_mining_failure_does_not_abort_remaining_workflows():
    wf1 = _make_workflow(ticket_key="AOS-1")
    _make_workflow(ticket_key="AOS-2")

    call_count = 0

    def failing_evaluate(bundle: TraceBundle) -> str:
        nonlocal call_count
        call_count += 1
        if bundle.workflow_id == wf1:
            raise RuntimeError("first fails")
        return "skip"

    with patch("ace.pipeline.runner.evaluate", side_effect=failing_evaluate):
        result = run_mining()

    assert result.processed == 2
    assert result.failed == 1
    assert result.skipped == 1


# ---------------------------------------------------------------------------
# run_mining — dry_run
# ---------------------------------------------------------------------------


def test_run_mining_dry_run_skips_all_db_writes():
    wf_id = _make_workflow()

    with (
        patch("ace.pipeline.runner.evaluate", return_value="proceed"),
        patch("ace.pipeline.runner.reflect", return_value=[_candidate()]),
        patch("ace.pipeline.runner.curate") as mock_curate,
    ):
        result = run_mining(dry_run=True)

    mock_curate.assert_not_called()
    assert result.dry_run is True
    assert _extraction_log_count(wf_id) == 0


def test_run_mining_dry_run_failure_skips_audit_log():
    wf_id = _make_workflow()

    with patch("ace.pipeline.runner.evaluate", side_effect=RuntimeError("dry boom")):
        result = run_mining(dry_run=True)

    assert result.failed == 1
    assert _audit_failure_count(wf_id) == 0


# ---------------------------------------------------------------------------
# run_mining — workflow_id flag
# ---------------------------------------------------------------------------


def test_run_mining_workflow_id_processes_specific_workflow():
    wf_id = _make_workflow(ticket_key="AOS-99")
    _make_workflow(ticket_key="AOS-100")  # second workflow; should be ignored

    with (
        patch("ace.pipeline.runner.evaluate", return_value="skip"),
        patch("ace.pipeline.runner.reflect") as mock_reflect,
    ):
        result = run_mining(workflow_id=wf_id)

    mock_reflect.assert_not_called()
    assert result.processed == 1
    assert _extraction_log_count(wf_id) == 1


def test_run_mining_workflow_id_returns_empty_for_unknown():
    result = run_mining(workflow_id="no-such-workflow")

    assert result.processed == 0


def test_run_mining_workflow_id_bypasses_extraction_log():
    wf_id = _make_workflow()
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO context_extraction_log (workflow_id, extracted_at) VALUES (?, '2026-05-01T00:00:00+00:00')",
            (wf_id,),
        )
        conn.commit()
    finally:
        conn.close()

    with (patch("ace.pipeline.runner.evaluate", return_value="skip"),):
        result = run_mining(workflow_id=wf_id)

    assert result.processed == 1


# ---------------------------------------------------------------------------
# run_mining — limit flag
# ---------------------------------------------------------------------------


def test_run_mining_limit_caps_workflows():
    for i in range(5):
        _make_workflow(ticket_key=f"AOS-{i}")

    with (patch("ace.pipeline.runner.evaluate", return_value="skip"),):
        result = run_mining(limit=2)

    assert result.processed == 2


# ---------------------------------------------------------------------------
# run_mining — idempotency
# ---------------------------------------------------------------------------


def test_run_mining_already_extracted_workflow_is_not_reprocessed():
    wf_id = _make_workflow()
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO context_extraction_log (workflow_id, extracted_at) VALUES (?, '2026-05-01T00:00:00+00:00')",
            (wf_id,),
        )
        conn.commit()
    finally:
        conn.close()

    with patch("ace.pipeline.runner.evaluate") as mock_eval:
        result = run_mining()

    mock_eval.assert_not_called()
    assert result.processed == 0
