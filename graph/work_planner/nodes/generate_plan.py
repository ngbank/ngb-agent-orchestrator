"""
Node: generate_plan — stub for future Goose-driven WorkPlan generation.

A future ticket (AOS-51) will call the Goose CLI / plan recipe here and populate
``work_plan_data`` in state. For now this sets an explicit error so the router
sends the workflow to error_handler instead of silently continuing with empty state.
"""

from graph.work_planner.state import WorkPlannerState


def generate_plan(state: WorkPlannerState) -> dict:
    # TODO(AOS-51): invoke Goose plan recipe and return {"work_plan_data": <dict>}
    return {"error": "Plan generation not yet implemented (AOS-51). Cannot continue."}
