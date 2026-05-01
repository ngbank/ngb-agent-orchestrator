"""
Node: generate_plan — stub for future Goose-driven WorkPlan generation.

A future ticket will call the Goose CLI / plan recipe here and populate
``work_plan_data`` in state. For now this is a no-op placeholder.
"""

from graph.work_planner.state import WorkPlannerState


def generate_plan(state: WorkPlannerState) -> dict:
    # TODO: invoke Goose plan recipe and return {"work_plan_data": <dict>}
    return {}
