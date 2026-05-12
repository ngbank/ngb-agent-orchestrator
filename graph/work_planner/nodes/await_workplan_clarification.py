"""Node: await_workplan_clarification — pause for human clarification on WorkPlan questions."""

import click
from langgraph.types import interrupt

from graph.utils import _get_actor
from graph.work_planner.state import WorkPlannerState
from state.state_store import update_status
from state.workflow_status import WorkflowStatus

MAX_CLARIFICATION_ROUNDS = 3


def await_workplan_clarification(state: WorkPlannerState) -> dict:
    """Interrupt the graph until the developer answers WorkPlan questions.

    On first entry (or subsequent rounds):
      - Checks max clarification rounds
      - Marks workflow as PENDING_WORKPLAN_CLARIFICATION in the DB
      - Prints questions/concerns to CLI
      - Calls interrupt() with the questions payload
      - Resumes when run.py calls graph.invoke(Command(resume=...))

    On resume:
      - Reads answers from Command(resume={"answers": [...]})
      - Appends Q&A round to clarifications list
      - Clears work_plan_data so generate_plan runs fresh
      - Returns updated state to route back to generate_plan
    """
    workflow_id = state.get("workflow_id")
    work_plan_data = state.get("work_plan_data") or {}
    clarifications = list(state.get("clarifications") or [])
    current_round = len(clarifications) + 1

    if current_round > MAX_CLARIFICATION_ROUNDS:
        return {
            "error": (
                f"Maximum clarification rounds ({MAX_CLARIFICATION_ROUNDS}) exceeded. "
                "Please create a new workflow with a clearer ticket description."
            ),
        }

    questions = work_plan_data.get("questions_for_reviewer", [])
    risks = work_plan_data.get("risks", [])
    status = work_plan_data.get("status", "")

    # Mark as pending clarification before suspending.
    if workflow_id:
        update_status(
            workflow_id,
            WorkflowStatus.PENDING_WORKPLAN_CLARIFICATION,
            actor="dispatcher",
            reason=f"Awaiting workplan clarification (round {current_round})",
        )

    ticket_key = state.get("ticket_key", workflow_id)
    click.echo("")
    click.echo(f"⏸️  WorkPlan needs clarification (round {current_round}/{MAX_CLARIFICATION_ROUNDS})")
    click.echo(f"   Status: {status}")
    click.echo(f"   Workflow ID: {workflow_id}")

    if risks:
        click.echo("")
        click.echo("   Risks identified:")
        for i, risk in enumerate(risks, 1):
            click.echo(f"     {i}. {risk}")

    if questions:
        click.echo("")
        click.echo("   Questions for reviewer:")
        for i, question in enumerate(questions, 1):
            click.echo(f"     {i}. {question}")

    click.echo("")
    click.echo(f"   To clarify:  dispatcher --clarify --ticket {ticket_key}")
    click.echo("")

    # Suspend here — resumes when Command(resume={"answers": [...]}) is passed.
    resume_payload: dict = interrupt({
        "workflow_id": workflow_id,
        "round": current_round,
        "questions": questions,
        "risks": risks,
        "status": status,
    })

    answers = resume_payload.get("answers", [])
    actor = _get_actor()

    if workflow_id:
        update_status(
            workflow_id,
            WorkflowStatus.IN_PROGRESS,
            actor=actor,
            reason=f"Clarification received (round {current_round}), regenerating plan",
        )

    # Append this round's Q&A to clarifications
    clarifications.append({
        "round": current_round,
        "questions": questions,
        "risks": risks,
        "answers": answers,
    })

    click.echo(f"📝 Clarification received (round {current_round}) — regenerating plan...")

    return {
        "clarifications": clarifications,
        "work_plan_data": None,  # clear so generate_plan runs fresh
        "error": None,  # clear any previous error
    }
