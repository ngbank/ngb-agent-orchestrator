"""Node: mark_executing — flip workflow status to IN_PROGRESS at execute_plan entry.

The graph reaches this node after the user approves the WorkPlan (status =
APPROVED) or comments on a PR (status = PR_COMMENTED). Without it the row
would stay in the prior status for the entire execute run (clone → Goose →
build → push → PR — often minutes), making ``dispatcher --list`` and the TUI
lie about what the workflow is actually doing.

Flipping to IN_PROGRESS here also preserves the meaning of APPROVED as a
true transient crash-recovery state: APPROVED should only persist if the
server dies between ``await_approval`` and the first ``code_generator``
step.
"""

from orchestrator.code_generator.state import CodeGeneratorState
from state.workflow_repository import update_status
from state.workflow_status import WorkflowStatus


def mark_executing(state: CodeGeneratorState) -> dict:
    workflow_id = state.get("workflow_id")
    if workflow_id:
        update_status(
            workflow_id,
            WorkflowStatus.IN_PROGRESS,
            actor="execute_plan",
            reason="execute_plan started",
        )
    return {}
