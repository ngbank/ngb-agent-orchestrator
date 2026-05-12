"""
WorkPlanner subgraph routing functions.

Each function is a pure conditional-edge callable that inspects the current
state and returns the name of the next node to execute.  No I/O side-effects.
"""

from typing import Literal

from graph.work_planner.state import WorkPlannerState


def route_after_validate_input(
    state: WorkPlannerState,
) -> Literal["check_duplicate", "error_handler"]:
    if state.get("error"):
        return "error_handler"
    return "check_duplicate"


def route_after_check_duplicate(
    state: WorkPlannerState,
) -> Literal["fetch_ticket", "error_handler"]:
    if state.get("error"):
        return "error_handler"
    return "fetch_ticket"


def route_after_fetch_ticket(
    state: WorkPlannerState,
) -> Literal["create_workflow_record", "error_handler"]:
    if state.get("error"):
        return "error_handler"
    return "create_workflow_record"


def route_after_generate_plan(
    state: WorkPlannerState,
) -> Literal["validate_plan", "error_handler"]:
    if state.get("error"):
        return "error_handler"
    if not state.get("work_plan_data"):
        return "error_handler"
    return "validate_plan"


def route_after_validate_plan(
    state: WorkPlannerState,
) -> Literal["store_plan", "await_workplan_clarification", "error_handler"]:
    if state.get("error"):
        return "error_handler"
    work_plan_data = state.get("work_plan_data") or {}
    status = work_plan_data.get("status", "")
    questions = work_plan_data.get("questions_for_reviewer", [])
    if status in ("concerns", "blocked") or bool(questions):
        return "await_workplan_clarification"
    return "store_plan"


def route_after_workplan_clarification(
    state: WorkPlannerState,
) -> Literal["generate_plan", "error_handler"]:
    if state.get("error"):
        return "error_handler"
    return "generate_plan"
