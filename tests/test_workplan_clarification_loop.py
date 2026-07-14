"""Convergence tests for the WorkPlanner clarification loop.

These tests exercise the ``validate_plan → route_after_validate_plan →
await_workplan_clarification → generate_plan`` cycle in composition, not
individual node behaviour (which is covered by ``test_generate_plan.py``,
``test_await_workplan_clarification.py``, and ``test_graph_edges.py``).

Purpose: verify that when the planner obeys the recipe's tightened
contract — remove resolved concerns, emit ``status="pass"`` with
``concerns=[]`` once every concern has been answered — the loop
converges to ``store_plan`` before ``MAX_CLARIFICATION_ROUNDS``. And
verify that when it does not obey (verbatim self-repetition), the loop
routes back to ``await_workplan_clarification`` and eventually errors
out.

The tests intentionally stitch together the real ``validate_plan`` and
``await_workplan_clarification`` node functions with a scripted planner
stand-in. They do not exercise ``generate_plan`` (which shells out to
Goose) — the planner outputs are supplied directly. This mirrors how a
production run would look if the LLM produced each ``work_plan_data``
payload deterministically.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import patch

from orchestrator.work_planner.edges import route_after_validate_plan
from orchestrator.work_planner.nodes.await_workplan_clarification import (
    MAX_CLARIFICATION_ROUNDS,
    await_workplan_clarification,
)
from orchestrator.work_planner.nodes.validate_plan import validate_plan

_PATCH_INTERRUPT = "orchestrator.work_planner.nodes.await_workplan_clarification.interrupt"
_PATCH_UPDATE_STATUS = "orchestrator.work_planner.nodes.await_workplan_clarification.update_status"
_PATCH_UPDATE_WORK_PLAN = (
    "orchestrator.work_planner.nodes.await_workplan_clarification.update_work_plan"
)
_PATCH_UPDATE_CLARIFICATION_HISTORY = (
    "orchestrator.work_planner.nodes.await_workplan_clarification.update_clarification_history"
)
_PATCH_GET_ACTOR = "orchestrator.work_planner.nodes.await_workplan_clarification._get_actor"


def _base_state() -> dict[str, Any]:
    return {
        "workflow_id": "wf-test",
        "ticket_key": "TEST-123",
        "clarifications": [],
    }


def _valid_plan(concerns: list[str], status: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "ticket_key": "TEST-123",
        "summary": "Test plan",
        "approach": "Test approach",
        "tasks": [
            {
                "id": 1,
                "description": "Do the thing",
                "files_likely_affected": ["a.py"],
            }
        ],
        "concerns": concerns,
        "status": status,
    }


def _run_loop(
    initial_state: dict[str, Any],
    planner_outputs: list[dict[str, Any]],
    answers_per_round: list[list[dict[str, str]]],
) -> tuple[dict[str, Any], str, int]:
    """Drive validate_plan → route → await_clarify → (next plan) cycles.

    Args:
        initial_state: Seed state before the first planner call.
        planner_outputs: Sequential ``work_plan_data`` payloads the
            stand-in planner returns, one per round.
        answers_per_round: Reviewer answers returned by the mocked
            ``interrupt`` on each round.

    Returns:
        Tuple ``(final_state, terminal_route, rounds_executed)`` where
        ``terminal_route`` is one of ``"store_plan"``, ``"cleanup"``, or
        ``"max_rounds_exceeded"``.
    """
    state = dict(initial_state)
    for round_idx, plan in enumerate(planner_outputs, start=1):
        # Simulate generate_plan writing work_plan_data.
        state["work_plan_data"] = plan
        state.pop("error", None)

        # Real validate_plan — sanity-check the plan payload.
        state.update(validate_plan(cast(Any, state)))
        if state.get("error"):
            return state, "cleanup", round_idx

        # Real router — this is the contract under test.
        decision = route_after_validate_plan(cast(Any, state))
        if decision == "store_plan":
            return state, "store_plan", round_idx
        if decision == "cleanup":
            return state, "cleanup", round_idx

        # decision == "await_workplan_clarification"
        answers = answers_per_round[round_idx - 1] if round_idx - 1 < len(answers_per_round) else []
        with (
            patch(_PATCH_INTERRUPT, return_value={"answers": answers}),
            patch(_PATCH_UPDATE_STATUS),
            patch(_PATCH_UPDATE_WORK_PLAN),
            patch(_PATCH_UPDATE_CLARIFICATION_HISTORY),
            patch(_PATCH_GET_ACTOR, return_value="test"),
        ):
            clarify_result = await_workplan_clarification(cast(Any, state))

        # Mimic LangGraph state merge behaviour: clarify_result overrides
        # keys it sets (clarifications, work_plan_data, error).
        for key, value in clarify_result.items():
            state[key] = value

        if state.get("error"):
            return state, "max_rounds_exceeded", round_idx

    # Ran out of scripted planner outputs without terminating.
    return state, "loop_exhausted", len(planner_outputs)


# ---------------------------------------------------------------------------
# Positive: reviewer answers resolve concerns → loop converges to store_plan
# ---------------------------------------------------------------------------


def test_loop_converges_when_planner_clears_resolved_concerns():
    """Round 1 raises two concerns → reviewer resolves both → round 2 planner
    obeys the tightened recipe (empty concerns, status=pass) → router sends
    the plan to store_plan.
    """
    round1 = _valid_plan(
        concerns=[
            "Confirm module path for ace/service/",
            "Confirm protocol surface area with team",
        ],
        status="concerns",
    )
    round2 = _valid_plan(concerns=[], status="pass")

    answers = [
        [
            {
                "concern": "Confirm module path for ace/service/",
                "answer": "ace/service/ is correct, mirrors dispatcher/service",
            },
            {
                "concern": "Confirm protocol surface area with team",
                "answer": "scope to run_mining(...) only",
            },
        ]
    ]

    final_state, terminal, rounds = _run_loop(
        _base_state(),
        planner_outputs=[round1, round2],
        answers_per_round=answers,
    )

    assert terminal == "store_plan"
    assert rounds == 2
    assert final_state["work_plan_data"]["status"] == "pass"
    assert final_state["work_plan_data"]["concerns"] == []


def test_loop_converges_within_max_rounds_for_multi_round_resolution():
    """Two rounds of concerns, cleared in round 3 → converges before the
    max-rounds guard fires.
    """
    round1 = _valid_plan(concerns=["C1", "C2"], status="concerns")
    round2 = _valid_plan(concerns=["C2b"], status="concerns")  # C1 removed, C2 refined
    round3 = _valid_plan(concerns=[], status="pass")

    answers = [
        [
            {"concern": "C1", "answer": "resolved: do X"},
            {"concern": "C2", "answer": "partial: needs more info on Y"},
        ],
        [{"concern": "C2b", "answer": "resolved: Y is Z"}],
    ]

    final_state, terminal, rounds = _run_loop(
        _base_state(),
        planner_outputs=[round1, round2, round3],
        answers_per_round=answers,
    )

    assert terminal == "store_plan"
    assert rounds == 3
    assert rounds <= MAX_CLARIFICATION_ROUNDS
    assert final_state["work_plan_data"]["concerns"] == []


# ---------------------------------------------------------------------------
# Negative: planner disobeys → loop hits MAX_CLARIFICATION_ROUNDS
# ---------------------------------------------------------------------------


def test_loop_errors_when_planner_re_emits_same_concerns_verbatim():
    """Simulates the AOS-270 failure mode: planner re-emits the same
    concern verbatim in every round despite the reviewer resolving it.
    Router keeps looping back; the max-rounds guard eventually errors.
    """
    unresolved = _valid_plan(concerns=["Same concern verbatim"], status="concerns")
    # Planner re-emits the same plan every round.
    planner_outputs = [unresolved] * (MAX_CLARIFICATION_ROUNDS + 1)

    answers = [
        [{"concern": "Same concern verbatim", "answer": "resolved: proceed"}]
    ] * MAX_CLARIFICATION_ROUNDS

    final_state, terminal, rounds = _run_loop(
        _base_state(),
        planner_outputs=planner_outputs,
        answers_per_round=answers,
    )

    assert terminal == "max_rounds_exceeded"
    assert rounds == MAX_CLARIFICATION_ROUNDS + 1
    assert "Maximum clarification rounds" in final_state["error"]


def test_loop_routes_back_on_pass_status_with_nonempty_concerns():
    """Regression guard: if the planner emits status=pass but leaves
    a non-empty concerns array (the pre-fix behaviour the recipe used to
    permit), the router MUST route back to await_workplan_clarification.
    """
    non_convergent = _valid_plan(
        concerns=["Not-cleared acknowledgement"],
        status="pass",  # Pre-fix planner behaviour: pass with concerns.
    )
    # Two rounds should both loop back; on the third round the max-rounds
    # guard fires.
    planner_outputs = [non_convergent] * (MAX_CLARIFICATION_ROUNDS + 1)
    answers = [
        [{"concern": "Not-cleared acknowledgement", "answer": "acknowledged"}]
    ] * MAX_CLARIFICATION_ROUNDS

    final_state, terminal, _ = _run_loop(
        _base_state(),
        planner_outputs=planner_outputs,
        answers_per_round=answers,
    )

    assert terminal == "max_rounds_exceeded"
    assert "Maximum clarification rounds" in final_state["error"]
