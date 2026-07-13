"""Unit tests for ace.pipeline.evaluator — topic-09 triage rules.

Each test targets one row of the triage table (or a behavioural edge case)
to keep coverage transparent and traceable to the spec.
"""

from __future__ import annotations

import pytest

from ace.pipeline.evaluator import Verdict, evaluate
from ace.pipeline.trace_reader import TraceBundle
from state.workflow_status import WorkflowStatus

_COMPLETED = WorkflowStatus.COMPLETED.value
_FAILED = WorkflowStatus.FAILED.value
_REJECTED = WorkflowStatus.REJECTED.value


def _bundle(
    *,
    status: str = _COMPLETED,
    plan_status: str | None = "pass",
    exec_status: str | None = "success",
    clarifications: list | None = None,
    pr_comments: list | None = None,
    exec_error: str | None = None,
) -> TraceBundle:
    """Construct a minimal TraceBundle for evaluator tests."""
    work_plan = {"status": plan_status} if plan_status is not None else None
    cg: dict | None
    if exec_status is not None or exec_error is not None:
        cg = {}
        if exec_status is not None:
            cg["status"] = exec_status
        if exec_error is not None:
            cg["error"] = exec_error
    else:
        cg = None
    return TraceBundle(
        workflow_id="test-wf-1",
        ticket_key="AOS-1",
        status=status,
        created_at="2026-01-01T00:00:00",
        work_plan=work_plan,
        code_generation_summary=cg,
        clarification_history=clarifications or [],
        pr_comments=pr_comments or [],
        rejection_reason=None,
    )


# ---------------------------------------------------------------------------
# Rule 1 — trivial success → skip
# ---------------------------------------------------------------------------


def test_trivial_success_returns_skip():
    """Plan pass + exec success + no clarifications + no PR comments → skip."""
    bundle = _bundle(
        status=_COMPLETED,
        plan_status="pass",
        exec_status="success",
        clarifications=[],
        pr_comments=[],
    )
    assert evaluate(bundle) == "skip"


def test_trivial_success_with_clarifications_does_not_skip():
    """Clarifications break the 'trivial' condition — rule 3 fires instead."""
    bundle = _bundle(
        status=_COMPLETED,
        plan_status="pass",
        exec_status="success",
        clarifications=[{"round": 1}],
        pr_comments=[],
    )
    assert evaluate(bundle) == "proceed"


def test_trivial_success_with_pr_comments_does_not_skip():
    """PR comments break the 'trivial' condition — rule 4 fires instead."""
    bundle = _bundle(
        status=_COMPLETED,
        plan_status="pass",
        exec_status="success",
        clarifications=[],
        pr_comments=[{"body": "nit: rename x"}],
    )
    assert evaluate(bundle) == "proceed"


# ---------------------------------------------------------------------------
# Rule 2 — concerns/blocked resolved → proceed
# ---------------------------------------------------------------------------


def test_plan_concerns_exec_success_returns_proceed():
    bundle = _bundle(
        status=_COMPLETED,
        plan_status="concerns",
        exec_status="success",
    )
    assert evaluate(bundle) == "proceed"


def test_plan_blocked_exec_success_returns_proceed():
    bundle = _bundle(
        status=_COMPLETED,
        plan_status="blocked",
        exec_status="success",
    )
    assert evaluate(bundle) == "proceed"


def test_plan_concerns_exec_not_success_falls_through():
    """Concerns + non-success exec: rule 2 does not fire; default proceed."""
    bundle = _bundle(
        status=_FAILED,
        plan_status="concerns",
        exec_status="failed",
        exec_error="build error",
    )
    # exec_error is set → rule 6 fires (flag) before default
    assert evaluate(bundle) == "flag"


# ---------------------------------------------------------------------------
# Rule 3 — clarifications present → proceed
# ---------------------------------------------------------------------------


def test_clarifications_present_returns_proceed():
    bundle = _bundle(clarifications=[{"round": 1, "concerns": ["x"]}])
    assert evaluate(bundle) == "proceed"


def test_clarifications_present_on_failed_workflow_returns_proceed():
    """Clarifications are always signal, even on a failed terminal path."""
    bundle = _bundle(
        status=_FAILED,
        plan_status="pass",
        exec_status=None,
        clarifications=[{"round": 1}],
    )
    assert evaluate(bundle) == "proceed"


# ---------------------------------------------------------------------------
# Rule 4 — PR comments present → proceed
# ---------------------------------------------------------------------------


def test_pr_comments_present_returns_proceed():
    bundle = _bundle(pr_comments=[{"body": "please rename this"}])
    assert evaluate(bundle) == "proceed"


def test_pr_comments_present_on_rejected_workflow_returns_proceed():
    """PR comments take priority and return proceed before the rejected rule."""
    bundle = _bundle(
        status=_REJECTED,
        plan_status="pass",
        exec_status=None,
        pr_comments=[{"body": "wrong approach"}],
    )
    assert evaluate(bundle) == "proceed"


# ---------------------------------------------------------------------------
# Rule 5 — terminal status rejected → proceed
# ---------------------------------------------------------------------------


def test_rejected_returns_proceed():
    bundle = _bundle(
        status=_REJECTED,
        plan_status="pass",
        exec_status=None,
        clarifications=[],
        pr_comments=[],
    )
    assert evaluate(bundle) == "proceed"


# ---------------------------------------------------------------------------
# Rule 6 — failed + exec_error → flag
# ---------------------------------------------------------------------------


def test_failed_with_exec_error_in_summary_returns_flag():
    """exec_error captured in code_generation_summary → flag."""
    bundle = _bundle(
        status=_FAILED,
        plan_status="pass",
        exec_status="failed",
        exec_error="network timeout during repo clone",
    )
    assert evaluate(bundle) == "flag"


def test_failed_with_no_summary_returns_flag():
    """No summary at all → early infra failure proxy → flag."""
    bundle = TraceBundle(
        workflow_id="test-wf-2",
        ticket_key="AOS-2",
        status=_FAILED,
        created_at="2026-01-01T00:00:00",
        work_plan={"status": "pass"},
        code_generation_summary=None,
        clarification_history=[],
        pr_comments=[],
        rejection_reason=None,
    )
    assert evaluate(bundle) == "flag"


def test_failed_with_empty_summary_returns_flag():
    """Empty summary dict (no status, no error) → early infra failure → flag."""
    bundle = TraceBundle(
        workflow_id="test-wf-3",
        ticket_key="AOS-3",
        status=_FAILED,
        created_at="2026-01-01T00:00:00",
        work_plan={"status": "pass"},
        code_generation_summary={},
        clarification_history=[],
        pr_comments=[],
        rejection_reason=None,
    )
    assert evaluate(bundle) == "flag"


# ---------------------------------------------------------------------------
# Rule 7 — failed + plan was pass → proceed
# ---------------------------------------------------------------------------


def test_failed_plan_pass_no_exec_error_returns_proceed():
    """Plan was confident but execution failed without infra error → proceed."""
    bundle = _bundle(
        status=_FAILED,
        plan_status="pass",
        exec_status="failed",
        exec_error=None,
    )
    assert evaluate(bundle) == "proceed"


# ---------------------------------------------------------------------------
# Rule ordering: exec_error (rule 6) takes priority over plan-pass (rule 7)
# ---------------------------------------------------------------------------


def test_failed_plan_pass_with_exec_error_returns_flag_not_proceed():
    """When both rule 6 and rule 7 conditions match, rule 6 fires first."""
    bundle = _bundle(
        status=_FAILED,
        plan_status="pass",
        exec_status="failed",
        exec_error="disk full",
    )
    assert evaluate(bundle) == "flag"


# ---------------------------------------------------------------------------
# Default — ambiguous / unknown patterns → proceed (conservative)
# ---------------------------------------------------------------------------


def test_completed_no_plan_no_summary_returns_proceed():
    """No plan, no summary, no clarifications: falls through to default."""
    bundle = TraceBundle(
        workflow_id="test-wf-4",
        ticket_key="AOS-4",
        status=_COMPLETED,
        created_at="2026-01-01T00:00:00",
        work_plan=None,
        code_generation_summary=None,
        clarification_history=[],
        pr_comments=[],
        rejection_reason=None,
    )
    assert evaluate(bundle) == "proceed"


# ---------------------------------------------------------------------------
# Return type: all verdicts are valid Literal values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bundle,expected",
    [
        (_bundle(plan_status="pass", exec_status="success"), "skip"),
        (_bundle(plan_status="concerns", exec_status="success"), "proceed"),
        (_bundle(clarifications=[{"round": 1}]), "proceed"),
        (_bundle(pr_comments=[{"body": "nit"}]), "proceed"),
        (_bundle(status=_REJECTED, exec_status=None), "proceed"),
        (
            _bundle(status=_FAILED, exec_status="failed", exec_error="err"),
            "flag",
        ),
        (_bundle(status=_FAILED, exec_status="failed", exec_error=None), "proceed"),
    ],
)
def test_verdict_values_are_valid(bundle: TraceBundle, expected: Verdict):
    assert evaluate(bundle) == expected
