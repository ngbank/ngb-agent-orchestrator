"""Unit tests for orchestrator/nodes/await_pr_approval.py."""

from unittest.mock import patch

from orchestrator.nodes.await_pr_approval import await_pr_approval
from state.workflow_status import WorkflowStatus

_PATCH_INTERRUPT = "orchestrator.nodes.await_pr_approval.interrupt"
_PATCH_UPDATE_STATUS = "orchestrator.nodes.await_pr_approval.update_status"
_PATCH_UPDATE_PR_COMMENTS = "orchestrator.nodes.await_pr_approval.update_pr_comments"
_PATCH_GET_WORKFLOW = "orchestrator.nodes.await_pr_approval.get_workflow"
_PATCH_GET_ACTOR = "orchestrator.nodes.await_pr_approval._get_actor"


def _make_state(**overrides):
    base = {
        "workflow_id": "wf-abc",
        "ticket_key": "TEST-100",
        "pr_url": "https://github.com/org/repo/pull/9",
    }
    base.update(overrides)
    return base


def _final_call(mock, status):
    """Return the (args, kwargs) tuple for the last call with the given status."""
    for call in reversed(mock.call_args_list):
        if len(call.args) >= 2 and call.args[1] == status:
            return call
    raise AssertionError(f"No update_status call with status {status}")


def test_approved_decision_persists_to_column():
    """Approved resume writes pr_approval_decision='approved' alongside COMPLETED."""
    state = _make_state()

    with (
        patch(_PATCH_GET_WORKFLOW, return_value=None),
        patch(_PATCH_INTERRUPT, return_value={"decision": "approved"}),
        patch(_PATCH_UPDATE_STATUS) as mock_update_status,
        patch(_PATCH_GET_ACTOR, return_value="reviewer"),
    ):
        result = await_pr_approval(state)

    call = _final_call(mock_update_status, WorkflowStatus.COMPLETED)
    assert call.kwargs["pr_approval_decision"] == "approved"
    assert call.kwargs["actor"] == "reviewer"
    assert result["pr_approval_decision"] == "approved"


def test_commented_decision_persists_to_column():
    """Commented resume writes pr_approval_decision='commented' alongside PR_COMMENTED."""
    state = _make_state()

    with (
        patch(_PATCH_GET_WORKFLOW, return_value=None),
        patch(
            _PATCH_INTERRUPT,
            return_value={"decision": "commented", "comments": "fix the null check"},
        ),
        patch(_PATCH_UPDATE_STATUS) as mock_update_status,
        patch(_PATCH_UPDATE_PR_COMMENTS) as mock_update_pr_comments,
        patch(_PATCH_GET_ACTOR, return_value="reviewer"),
    ):
        result = await_pr_approval(state)

    call = _final_call(mock_update_status, WorkflowStatus.PR_COMMENTED)
    assert call.kwargs["pr_approval_decision"] == "commented"
    mock_update_pr_comments.assert_called_once_with(
        "wf-abc", "fix the null check", actor="reviewer"
    )
    assert result["pr_approval_decision"] == "commented"
    assert result["pr_comments"] == "fix the null check"


def test_rejected_decision_persists_to_column():
    """Rejected resume writes pr_approval_decision='rejected' alongside REJECTED."""
    state = _make_state()

    with (
        patch(_PATCH_GET_WORKFLOW, return_value=None),
        patch(
            _PATCH_INTERRUPT,
            return_value={"decision": "rejected", "reason": "wrong approach"},
        ),
        patch(_PATCH_UPDATE_STATUS) as mock_update_status,
        patch(_PATCH_GET_ACTOR, return_value="reviewer"),
    ):
        result = await_pr_approval(state)

    call = _final_call(mock_update_status, WorkflowStatus.REJECTED)
    assert call.kwargs["pr_approval_decision"] == "rejected"
    assert call.kwargs["reason"] == "wrong approach"
    assert result["pr_approval_decision"] == "rejected"


def test_unknown_decision_falls_through_to_rejected():
    """A missing/unknown decision string is treated as rejection."""
    state = _make_state()

    with (
        patch(_PATCH_GET_WORKFLOW, return_value=None),
        patch(_PATCH_INTERRUPT, return_value={"decision": ""}),
        patch(_PATCH_UPDATE_STATUS) as mock_update_status,
        patch(_PATCH_GET_ACTOR, return_value="reviewer"),
    ):
        result = await_pr_approval(state)

    call = _final_call(mock_update_status, WorkflowStatus.REJECTED)
    assert call.kwargs["pr_approval_decision"] == "rejected"
    assert result["pr_approval_decision"] == "rejected"
