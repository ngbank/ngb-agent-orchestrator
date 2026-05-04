"""Unit tests for WorkPlanner subgraph routing functions (edges.py).

All routing functions are pure: they inspect state and return a string
destination — no I/O, no side-effects.
"""

from graph.work_planner.edges import (
    route_after_check_duplicate,
    route_after_fetch_ticket,
    route_after_validate_input,
    route_after_validate_plan,
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
        "error": "WorkPlan status is 'blocked'",
    }
    assert route_after_validate_plan(state) == "error_handler"


def test_route_validate_plan_none_error():
    """Explicit None error must not trigger the error path."""
    state = {"ticket_key": "AOS-50", "dry_run": False, "error": None}
    assert route_after_validate_plan(state) == "store_plan"
