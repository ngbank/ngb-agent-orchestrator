"""Unit tests for WorkPlanner subgraph routing functions (edges.py).

All routing functions are pure: they inspect state and return a string
destination — no I/O, no side-effects.
"""

from graph.work_planner.edges import (
    route_after_check_duplicate,
    route_after_fetch_ticket,
    route_after_generate_plan,
    route_after_validate_input,
    route_after_validate_plan,
    route_after_workplan_clarification,
)

# ---------------------------------------------------------------------------
# route_after_validate_input
# ---------------------------------------------------------------------------


def test_route_validate_input_no_error():
    state = {"ticket_key": "AOS-50", "dry_run": False}
    assert route_after_validate_input(state) == "check_duplicate"


def test_route_validate_input_with_error():
    state = {"ticket_key": "invalid", "dry_run": False, "error": "bad format"}
    assert route_after_validate_input(state) == "error_handler"


def test_route_validate_input_empty_error_is_falsy():
    """An empty-string error must not trigger the error path."""
    state = {"ticket_key": "AOS-50", "dry_run": False, "error": ""}
    assert route_after_validate_input(state) == "check_duplicate"


# ---------------------------------------------------------------------------
# route_after_check_duplicate
# ---------------------------------------------------------------------------


def test_route_check_duplicate_no_error():
    state = {"ticket_key": "AOS-50", "dry_run": False}
    assert route_after_check_duplicate(state) == "fetch_ticket"


def test_route_check_duplicate_with_error():
    state = {
        "ticket_key": "AOS-50",
        "dry_run": False,
        "error": "Workflow already in progress for AOS-50 (ID: abc123)",
    }
    assert route_after_check_duplicate(state) == "error_handler"


# ---------------------------------------------------------------------------
# route_after_fetch_ticket
# ---------------------------------------------------------------------------


def test_route_fetch_ticket_no_error():
    state = {"ticket_key": "AOS-50", "dry_run": False, "ticket": object()}
    assert route_after_fetch_ticket(state) == "create_workflow_record"


def test_route_fetch_ticket_with_error():
    state = {"ticket_key": "AOS-50", "dry_run": False, "error": "not found"}
    assert route_after_fetch_ticket(state) == "error_handler"


# ---------------------------------------------------------------------------
# route_after_validate_plan
# ---------------------------------------------------------------------------


def test_route_validate_plan_no_error():
    state = {"ticket_key": "AOS-50", "dry_run": False}
    assert route_after_validate_plan(state) == "store_plan"


def test_route_validate_plan_with_error():
    state = {
        "ticket_key": "AOS-50",
        "dry_run": False,
        "error": "Schema validation failed",
    }
    assert route_after_validate_plan(state) == "error_handler"


def test_route_validate_plan_none_error():
    """Explicit None error must not trigger the error path."""
    state = {"ticket_key": "AOS-50", "dry_run": False, "error": None}
    assert route_after_validate_plan(state) == "store_plan"


def test_route_validate_plan_status_concerns_routes_to_clarification():
    """'concerns' status must route to await_workplan_clarification."""
    state = {
        "ticket_key": "AOS-50",
        "work_plan_data": {"status": "concerns", "questions_for_reviewer": []},
    }
    assert route_after_validate_plan(state) == "await_workplan_clarification"


def test_route_validate_plan_status_blocked_routes_to_clarification():
    """'blocked' status must route to await_workplan_clarification."""
    state = {
        "ticket_key": "AOS-50",
        "work_plan_data": {"status": "blocked", "questions_for_reviewer": []},
    }
    assert route_after_validate_plan(state) == "await_workplan_clarification"


def test_route_validate_plan_questions_present_routes_to_clarification():
    """Non-empty questions_for_reviewer must route to await_workplan_clarification."""
    state = {
        "ticket_key": "AOS-50",
        "work_plan_data": {
            "status": "pass",
            "questions_for_reviewer": ["What database should we use?"],
        },
    }
    assert route_after_validate_plan(state) == "await_workplan_clarification"


def test_route_validate_plan_pass_with_no_questions_routes_to_store():
    """'pass' status with empty questions must route to store_plan."""
    state = {
        "ticket_key": "AOS-50",
        "work_plan_data": {"status": "pass", "questions_for_reviewer": []},
    }
    assert route_after_validate_plan(state) == "store_plan"


def test_route_validate_plan_error_takes_priority_over_clarification():
    """An error must route to error_handler even if plan needs clarification."""
    state = {
        "ticket_key": "AOS-50",
        "error": "Schema error",
        "work_plan_data": {"status": "blocked", "questions_for_reviewer": ["Q?"]},
    }
    assert route_after_validate_plan(state) == "error_handler"


# ---------------------------------------------------------------------------
# route_after_workplan_clarification
# ---------------------------------------------------------------------------


def test_route_workplan_clarification_no_error():
    state = {"ticket_key": "AOS-50", "clarifications": [{"round": 1, "answers": []}]}
    assert route_after_workplan_clarification(state) == "generate_plan"


def test_route_workplan_clarification_with_error():
    state = {
        "ticket_key": "AOS-50",
        "error": "Maximum clarification rounds exceeded.",
    }
    assert route_after_workplan_clarification(state) == "error_handler"


def test_route_workplan_clarification_none_error():
    """Explicit None error must not trigger the error path."""
    state = {"ticket_key": "AOS-50", "error": None}
    assert route_after_workplan_clarification(state) == "generate_plan"


# ---------------------------------------------------------------------------
# route_after_generate_plan
# ---------------------------------------------------------------------------


def test_route_generate_plan_with_work_plan_data():
    state = {"ticket_key": "AOS-50", "work_plan_data": {"tasks": []}}
    assert route_after_generate_plan(state) == "validate_plan"


def test_route_generate_plan_empty_work_plan_data():
    """Empty dict for work_plan_data must route to error_handler."""
    state = {"ticket_key": "AOS-50", "work_plan_data": {}}
    assert route_after_generate_plan(state) == "error_handler"


def test_route_generate_plan_missing_work_plan_data():
    """Missing work_plan_data key must route to error_handler."""
    state = {"ticket_key": "AOS-50"}
    assert route_after_generate_plan(state) == "error_handler"


def test_route_generate_plan_none_work_plan_data():
    """Explicit None work_plan_data must route to error_handler."""
    state = {"ticket_key": "AOS-50", "work_plan_data": None}
    assert route_after_generate_plan(state) == "error_handler"


def test_route_generate_plan_with_error():
    """An error in state must route to error_handler regardless of work_plan_data."""
    state = {
        "ticket_key": "AOS-50",
        "work_plan_data": {"tasks": []},
        "error": "Plan generation not yet implemented (AOS-51).",
    }
    assert route_after_generate_plan(state) == "error_handler"
