"""Cross-node invariant check: every node that reports a failure must set both
``error`` and ``failed_node`` (see ``orchestrator.failure``).

These tests drive each failure-producing node with the minimum arguments
needed to reach its error path, capture the returned partial-state dict,
and run ``assert_failure_consistent`` over it. The goal is not to
re-test the node's business logic but to guarantee that migrations to
``mark_failure`` stay in place — if a future edit hand-rolls a
``{"error": "..."}`` dict without ``failed_node``, this suite fails.
"""

from __future__ import annotations

from typing import Any, Callable
from unittest.mock import patch

import pytest

from orchestrator.failure import assert_failure_consistent
from orchestrator.shared.repo_setup.nodes.common import failure_update
from orchestrator.work_planner.nodes.await_workplan_clarification import (
    await_workplan_clarification,
)
from orchestrator.work_planner.nodes.check_duplicate import check_duplicate
from orchestrator.work_planner.nodes.validate_input import validate_input
from orchestrator.work_planner.nodes.validate_plan import validate_plan


def _validate_input_failure() -> Any:
    # No hyphen → fails the format check.
    return validate_input({"ticket_key": "invalid"})


def _check_duplicate_failure() -> Any:
    class _ActiveStatus:
        value = "in_progress"

        def is_active(self) -> bool:
            return True

    existing_workflow = {"id": "wf-existing", "status": _ActiveStatus()}
    with patch(
        "orchestrator.work_planner.nodes.check_duplicate.get_workflow_by_ticket",
        return_value=[existing_workflow],
    ):
        return check_duplicate({"ticket_key": "AOS-1", "workflow_id": None})


def _validate_plan_failure() -> Any:
    return validate_plan({"work_plan_data": {"invalid": True}})


def _await_workplan_clarification_max_rounds() -> Any:
    # 4 clarifications means current_round=5 > MAX_CLARIFICATION_ROUNDS(3).
    result = await_workplan_clarification(
        {
            "workflow_id": None,
            "work_plan_data": {},
            "clarifications": [{"round": i, "concerns": [], "answers": []} for i in range(1, 5)],
            "ticket_key": "AOS-1",
        }
    )
    return dict(result)


def _repo_setup_failure() -> Any:
    return failure_update({"ticket_key": "AOS-1"}, "clone failed", mode="repo_setup")


FAILURE_NODE_CASES: list[tuple[str, Callable[[], Any]]] = [
    ("validate_input", _validate_input_failure),
    ("check_duplicate", _check_duplicate_failure),
    ("validate_plan", _validate_plan_failure),
    ("await_workplan_clarification_max_rounds", _await_workplan_clarification_max_rounds),
    ("repo_setup_failure_update", _repo_setup_failure),
]


@pytest.mark.parametrize(
    "name,producer", FAILURE_NODE_CASES, ids=[n for n, _ in FAILURE_NODE_CASES]
)
def test_failure_producing_node_sets_both_fields(name: str, producer: Callable[[], Any]) -> None:
    """Every failure path must set both ``error`` and ``failed_node``."""
    result = producer()
    assert result.get("error"), f"{name} produced no error message: {result!r}"
    assert result.get("failed_node"), f"{name} produced an error but no failed_node: {result!r}"
    # Belt and braces: run the canonical invariant helper.
    assert_failure_consistent(result)
