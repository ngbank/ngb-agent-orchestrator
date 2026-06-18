"""
WorkPlanner subgraph routing functions.

Each function is a pure conditional-edge callable that inspects the current
state and returns the name of the next node to execute. No I/O side-effects.
"""

from typing import Literal

from orchestrator.work_planner.state import WorkPlannerState


def route_after_validate_input(
    state: WorkPlannerState,
) -> Literal["check_duplicate", "cleanup"]:
    if state.get("error"):
        return "cleanup"
    return "check_duplicate"


def route_after_check_duplicate(
    state: WorkPlannerState,
) -> Literal["fetch_ticket", "cleanup"]:
    if state.get("error"):
        return "cleanup"
    return "fetch_ticket"


def route_after_fetch_ticket(
    state: WorkPlannerState,
) -> Literal["create_workflow_record", "cleanup"]:
    if state.get("error"):
        return "cleanup"
    return "create_workflow_record"


def route_after_resolve_repo(
    state: WorkPlannerState,
) -> Literal["fetch_github_token", "cleanup"]:
    if state.get("error"):
        return "cleanup"
    return "fetch_github_token"


def route_after_fetch_github_token(
    state: WorkPlannerState,
) -> Literal["clone_repo", "cleanup"]:
    if state.get("error"):
        return "cleanup"
    return "clone_repo"


def route_after_clone_repo(
    state: WorkPlannerState,
) -> Literal["generate_plan", "cleanup"]:
    if state.get("error"):
        return "cleanup"
    if not state.get("working_dir"):
        return "cleanup"
    return "generate_plan"


def route_after_generate_plan(
    state: WorkPlannerState,
) -> Literal["validate_plan", "cleanup"]:
    if state.get("error"):
        return "cleanup"
    if not state.get("work_plan_data"):
        return "cleanup"
    return "validate_plan"


def route_after_validate_plan(
    state: WorkPlannerState,
) -> Literal["store_plan", "await_workplan_clarification", "cleanup"]:
    if state.get("error"):
        return "cleanup"
    work_plan_data = state.get("work_plan_data") or {}
    status = work_plan_data.get("status", "")
    concerns = work_plan_data.get("concerns", [])
    if status == "pass" and not concerns:
        return "store_plan"
    if status in ("concerns", "blocked") or bool(concerns):
        return "await_workplan_clarification"
    return "store_plan"


def route_after_workplan_clarification(
    state: WorkPlannerState,
) -> Literal["generate_plan", "cleanup"]:
    if state.get("error"):
        return "cleanup"
    return "generate_plan"


def route_after_cleanup(
    state: WorkPlannerState,
) -> Literal["error_handler", "end"]:
    if state.get("error"):
        return "error_handler"
    return "end"
