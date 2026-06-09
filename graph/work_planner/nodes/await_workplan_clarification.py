"""Node: await_workplan_clarification — pause for human clarification on WorkPlan concerns."""

import click
from langgraph.types import interrupt

from graph.utils import _get_actor
from graph.work_planner.state import (
    AwaitClarificationInputState,
    AwaitClarificationOutputState,
)
from state.workflow_repository import update_clarification_history, update_status, update_work_plan
from state.workflow_status import WorkflowStatus

MAX_CLARIFICATION_ROUNDS = 3


def await_workplan_clarification(
    state: AwaitClarificationInputState,
) -> AwaitClarificationOutputState:
    """Interrupt the graph until the developer answers WorkPlan concerns.

    On first entry (or subsequent rounds):
      - Checks max clarification rounds
      - Marks workflow as PENDING_WORKPLAN_CLARIFICATION in the DB
      - Prints concerns to CLI
      - Calls interrupt() with the concerns payload
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

    concerns = work_plan_data.get("concerns", [])
    status = work_plan_data.get("status", "")

    # Mark as pending clarification before suspending.
    if workflow_id:
        update_status(
            workflow_id,
            WorkflowStatus.PENDING_WORKPLAN_CLARIFICATION,
            actor="dispatcher",
            reason=f"Awaiting workplan clarification (round {current_round})",
        )
        # Persist the work_plan_data so _handle_clarify can read concerns
        # from the state store (store_plan is skipped when we route here).
        if work_plan_data:
            update_work_plan(
                workflow_id,
                work_plan_data,
                actor="dispatcher",
                reason=f"WorkPlan stored for clarification (round {current_round})",
            )

    ticket_key = state.get("ticket_key", workflow_id)
    click.echo("")
    click.echo(
        f"⏸️  WorkPlan needs clarification (round {current_round}/{MAX_CLARIFICATION_ROUNDS})"
    )
    click.echo(f"   Status: {status}")
    click.echo(f"   Workflow ID: {workflow_id}")

    if concerns:
        click.echo("")
        click.echo("   Concerns identified:")
        for i, concern in enumerate(concerns, 1):
            click.echo(f"     {i}. {concern}")

    click.echo("")
    click.echo(f"   To clarify:  dispatcher --clarify --ticket {ticket_key}")
    click.echo("")

    # Suspend here — resumes when Command(resume={"answers": [...]}) is passed.
    resume_payload: dict = interrupt(
        {
            "workflow_id": workflow_id,
            "round": current_round,
            "concerns": concerns,
            "status": status,
        }
    )

    answers = resume_payload.get("answers", [])
    actor = _get_actor()

    if workflow_id:
        update_status(
            workflow_id,
            WorkflowStatus.IN_PROGRESS,
            actor=actor,
            reason=f"Clarification received (round {current_round}), regenerating plan",
        )

    # Build the round entry with full metadata
    round_entry = {
        "round": current_round,
        "concerns": concerns,
        "answers": answers,
    }

    # Persist to database
    if workflow_id:
        update_clarification_history(
            workflow_id,
            round_entry,
            actor=actor,
        )

    # Append this round's Q&A to in-memory clarifications
    clarifications.append(round_entry)

    click.echo(f"📝 Clarification received (round {current_round}) — regenerating plan...")

    return {
        "clarifications": clarifications,
        "work_plan_data": None,  # clear so generate_plan runs fresh
        "error": None,  # clear any previous error
    }
