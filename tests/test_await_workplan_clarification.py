"""Unit tests for graph/work_planner/nodes/await_workplan_clarification.py."""

from unittest.mock import MagicMock, patch

import pytest
from langgraph.types import interrupt as langgraph_interrupt

from graph.work_planner.nodes.await_workplan_clarification import (
    MAX_CLARIFICATION_ROUNDS,
    await_workplan_clarification,
)


_PATCH_INTERRUPT = "graph.work_planner.nodes.await_workplan_clarification.interrupt"
_PATCH_UPDATE_STATUS = "graph.work_planner.nodes.await_workplan_clarification.update_status"
_PATCH_GET_ACTOR = "graph.work_planner.nodes.await_workplan_clarification._get_actor"


def _make_state(
    *,
    workflow_id="wf-123",
    ticket_key="AOS-50",
    work_plan_data=None,
    clarifications=None,
):
    return {
        "workflow_id": workflow_id,
        "ticket_key": ticket_key,
        "work_plan_data": work_plan_data or {
            "status": "concerns",
            "questions_for_reviewer": ["What DB?", "Which API?"],
            "risks": ["Risk A"],
        },
        "clarifications": clarifications or [],
    }


# ---------------------------------------------------------------------------
# Max rounds exceeded
# ---------------------------------------------------------------------------


def test_max_rounds_exceeded_returns_error():
    """When clarifications list has MAX_CLARIFICATION_ROUNDS entries, return error."""
    clarifications = [{"round": i} for i in range(1, MAX_CLARIFICATION_ROUNDS + 1)]
    state = _make_state(clarifications=clarifications)

    with patch(_PATCH_UPDATE_STATUS):
        result = await_workplan_clarification(state)

    assert "error" in result
    assert "Maximum clarification rounds" in result["error"]


# ---------------------------------------------------------------------------
# First entry (round 1) — interrupt fires
# ---------------------------------------------------------------------------


def test_first_entry_calls_interrupt(monkeypatch):
    """Node calls interrupt() with workflow_id, questions, risks on first entry."""
    state = _make_state()

    captured_payload = {}

    def fake_interrupt(payload):
        captured_payload.update(payload)
        return {"answers": [{"question": "What DB?", "answer": "SQLite"}, {"question": "Which API?", "answer": "REST"}]}

    with patch(_PATCH_INTERRUPT, side_effect=fake_interrupt):
        with patch(_PATCH_UPDATE_STATUS):
            with patch(_PATCH_GET_ACTOR, return_value="test_user"):
                result = await_workplan_clarification(state)

    assert captured_payload["workflow_id"] == "wf-123"
    assert captured_payload["round"] == 1
    assert "What DB?" in captured_payload["questions"]
    assert "Risk A" in captured_payload["risks"]


def test_first_entry_appends_clarification_round(monkeypatch):
    """After interrupt resumes, clarifications list grows by one."""
    state = _make_state()
    answers = [{"question": "What DB?", "answer": "SQLite"}]

    with patch(_PATCH_INTERRUPT, return_value={"answers": answers}):
        with patch(_PATCH_UPDATE_STATUS):
            with patch(_PATCH_GET_ACTOR, return_value="test_user"):
                result = await_workplan_clarification(state)

    assert "clarifications" in result
    assert len(result["clarifications"]) == 1
    assert result["clarifications"][0]["round"] == 1
    assert result["clarifications"][0]["answers"] == answers


def test_first_entry_clears_work_plan_data(monkeypatch):
    """work_plan_data is cleared so generate_plan runs fresh."""
    state = _make_state()

    with patch(_PATCH_INTERRUPT, return_value={"answers": []}):
        with patch(_PATCH_UPDATE_STATUS):
            with patch(_PATCH_GET_ACTOR, return_value="test_user"):
                result = await_workplan_clarification(state)

    assert result.get("work_plan_data") is None


def test_first_entry_clears_error(monkeypatch):
    """Any previous error is cleared after clarification."""
    state = _make_state()
    state["error"] = "previous error"

    with patch(_PATCH_INTERRUPT, return_value={"answers": []}):
        with patch(_PATCH_UPDATE_STATUS):
            with patch(_PATCH_GET_ACTOR, return_value="test_user"):
                result = await_workplan_clarification(state)

    assert result.get("error") is None


# ---------------------------------------------------------------------------
# Second round (round 2)
# ---------------------------------------------------------------------------


def test_second_round_increments_round_number():
    """On second entry, interrupt payload round == 2."""
    state = _make_state(
        clarifications=[{"round": 1, "questions": ["Q1"], "risks": [], "answers": [{"question": "Q1", "answer": "A1"}]}]
    )

    captured_payload = {}

    def fake_interrupt(payload):
        captured_payload.update(payload)
        return {"answers": []}

    with patch(_PATCH_INTERRUPT, side_effect=fake_interrupt):
        with patch(_PATCH_UPDATE_STATUS):
            with patch(_PATCH_GET_ACTOR, return_value="test_user"):
                await_workplan_clarification(state)

    assert captured_payload["round"] == 2


def test_second_round_accumulates_clarifications():
    """After second round, clarifications list has 2 entries."""
    existing = [{"round": 1, "questions": ["Q1"], "risks": [], "answers": []}]
    state = _make_state(clarifications=existing)

    with patch(_PATCH_INTERRUPT, return_value={"answers": [{"question": "What DB?", "answer": "SQLite"}]}):
        with patch(_PATCH_UPDATE_STATUS):
            with patch(_PATCH_GET_ACTOR, return_value="test_user"):
                result = await_workplan_clarification(state)

    assert len(result["clarifications"]) == 2
    assert result["clarifications"][1]["round"] == 2


# ---------------------------------------------------------------------------
# Status update calls
# ---------------------------------------------------------------------------


def test_status_set_to_pending_before_interrupt():
    """update_status is called with PENDING_WORKPLAN_CLARIFICATION before interrupt."""
    from state.workflow_status import WorkflowStatus

    state = _make_state()
    pre_interrupt_statuses = []

    def fake_update_status(wf_id, status, **kwargs):
        pre_interrupt_statuses.append(status)

    def fake_interrupt(payload):
        return {"answers": []}

    with patch(_PATCH_INTERRUPT, side_effect=fake_interrupt):
        with patch(_PATCH_UPDATE_STATUS, side_effect=fake_update_status):
            with patch(_PATCH_GET_ACTOR, return_value="test_user"):
                await_workplan_clarification(state)

    assert WorkflowStatus.PENDING_WORKPLAN_CLARIFICATION in pre_interrupt_statuses


def test_status_set_to_in_progress_after_interrupt():
    """update_status is called with IN_PROGRESS after resuming."""
    from state.workflow_status import WorkflowStatus

    state = _make_state()
    statuses_called = []

    def fake_update_status(wf_id, status, **kwargs):
        statuses_called.append(status)

    with patch(_PATCH_INTERRUPT, return_value={"answers": []}):
        with patch(_PATCH_UPDATE_STATUS, side_effect=fake_update_status):
            with patch(_PATCH_GET_ACTOR, return_value="test_user"):
                await_workplan_clarification(state)

    assert WorkflowStatus.IN_PROGRESS in statuses_called
