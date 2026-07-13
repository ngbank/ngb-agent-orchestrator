"""Rule-based evaluator: topic-09 triage table encoded verbatim.

Reads a :class:`~ace.pipeline.trace_reader.TraceBundle` and returns one of
three verdicts:

- ``proceed`` — useful learning signal; pass to the Reflector
- ``skip``    — trivial success or insufficient signal; do not mine further
- ``flag``    — probable environment failure; queue for manual review

Rules are applied in the order they appear in the triage table
(docs/ACE/09-ace-orchestrator-learning-pipeline.md § "The evaluator in
detail"), which defines their priority when multiple conditions match.
"""

from __future__ import annotations

from typing import Literal

from ace.pipeline.trace_reader import TraceBundle
from state.workflow_status import WorkflowStatus

Verdict = Literal["proceed", "skip", "flag"]

_FAILED = WorkflowStatus.FAILED.value
_REJECTED = WorkflowStatus.REJECTED.value


def evaluate(bundle: TraceBundle) -> Verdict:
    """Return a triage verdict for *bundle* using the topic-09 rules."""
    plan_status = (bundle.work_plan or {}).get("status")
    exec_status = (bundle.code_generation_summary or {}).get("status")
    has_clarifications = bool(bundle.clarification_history)
    has_pr_comments = bool(bundle.pr_comments)

    # Rule 1 — trivial success: plan pass, execution succeeded, no human
    # feedback of any kind → low information content.
    if (
        plan_status == "pass"
        and exec_status == "success"
        and not has_clarifications
        and not has_pr_comments
    ):
        return "skip"

    # Rule 2 — concerns resolved: the planner flagged risk but execution
    # succeeded → the resolution path is signal.
    if plan_status in ("concerns", "blocked") and exec_status == "success":
        return "proceed"

    # Rule 3 — explicit human correction always produces signal.
    if has_clarifications:
        return "proceed"

    # Rule 4 — post-execution human feedback always produces signal.
    if has_pr_comments:
        return "proceed"

    # Rule 5 — rejected workflow: failure path strategy.
    if bundle.status == _REJECTED:
        return "proceed"

    # Rule 6 — failed + probable environment error: flag for manual review
    # rather than treating it as agent-reasoning signal.
    if bundle.status == _FAILED and _has_exec_error(bundle):
        return "flag"

    # Rule 7 — failed + plan was confident: the plan was overconfident;
    # the divergence between plan confidence and outcome is signal.
    if bundle.status == _FAILED and plan_status == "pass":
        return "proceed"

    # Default: proceed (conservative — don't silently discard ambiguous traces).
    return "proceed"


def _has_exec_error(bundle: TraceBundle) -> bool:
    """Return True if the bundle shows signs of an infrastructure failure.

    ``exec_error`` is a graph-state field that is not persisted to the
    ``workflows`` table directly.  Two observable proxies cover the cases
    where it is set:

    1. ``code_generation_summary`` is absent or empty — the error occurred
       before any code generation summary was written (e.g. branch setup
       failure in an early node).
    2. ``code_generation_summary["error"]`` is present — the error was
       captured by :func:`~orchestrator.shared.repo_setup.nodes.common.
       code_generation_failure_summary` and stored in the summary dict.
    """
    cg = bundle.code_generation_summary
    if not cg:
        return True
    return bool(cg.get("error"))
