from unittest.mock import patch

from state.workflow_status import WorkflowStatus


def _base_state(status="success", pr_url="https://github.com/ngbank/repo/pull/5", exec_error=None):
    return {
        "workflow_id": "wf-123",
        "code_generation_summary": {
            "status": status,
            "branch": "feature/AOS-120",
            "build": "pass",
            "tests": "pass",
            "pr_url": pr_url,
        },
        "exec_error": exec_error,
    }


def _run(state):
    from orchestrator.code_generator.nodes.persist_results import persist_results

    with (
        patch(
            "orchestrator.code_generator.nodes.persist_results.aggregate_token_usage",
            return_value={},
        ),
        patch("orchestrator.code_generator.nodes.persist_results.update_usage_summary"),
        patch("orchestrator.code_generator.nodes.persist_results.update_code_generation_summary"),
        patch("orchestrator.code_generator.nodes.persist_results.update_status") as mock_status,
    ):
        result = persist_results(state)
    return result, mock_status


def test_success_with_pr_url_routes_to_pending_pr_approval():
    result, mock_status = _run(
        _base_state(status="success", pr_url="https://github.com/ngbank/repo/pull/5")
    )

    mock_status.assert_called_once_with(
        "wf-123",
        WorkflowStatus.PENDING_PR_APPROVAL,
        pr_url="https://github.com/ngbank/repo/pull/5",
        actor="generate_code",
    )
    assert result["pr_url"] == "https://github.com/ngbank/repo/pull/5"
    assert result["failed_node"] is None


def test_partial_with_pr_url_routes_to_pending_pr_approval():
    result, mock_status = _run(
        _base_state(status="partial", pr_url="https://github.com/ngbank/repo/pull/5")
    )

    mock_status.assert_called_once_with(
        "wf-123",
        WorkflowStatus.PENDING_PR_APPROVAL,
        pr_url="https://github.com/ngbank/repo/pull/5",
        actor="generate_code",
    )
    assert result["failed_node"] is None


def test_push_failure_partial_with_no_pr_url_routes_to_failed():
    """Push failure sets status=partial and pr_url=''; must not reach PENDING_PR_APPROVAL."""
    result, mock_status = _run(_base_state(status="partial", pr_url=""))

    mock_status.assert_called_once_with(
        "wf-123",
        WorkflowStatus.FAILED,
        pr_url=None,
        actor="generate_code",
    )
    assert result["failed_node"] == "generate_code"
    assert result["pr_url"] == ""


def test_success_with_no_pr_url_routes_to_failed():
    """A success status with no PR URL should still fail — nothing for the reviewer."""
    result, mock_status = _run(_base_state(status="success", pr_url=""))

    mock_status.assert_called_once_with(
        "wf-123",
        WorkflowStatus.FAILED,
        pr_url=None,
        actor="generate_code",
    )
    assert result["failed_node"] == "generate_code"


def test_exec_error_routes_to_failed_regardless_of_pr_url():
    result, mock_status = _run(
        _base_state(
            status="success", pr_url="https://github.com/ngbank/repo/pull/5", exec_error="boom"
        )
    )

    mock_status.assert_called_once_with(
        "wf-123",
        WorkflowStatus.FAILED,
        pr_url="https://github.com/ngbank/repo/pull/5",
        actor="generate_code",
    )
    assert result["failed_node"] == "generate_code"


def test_failed_status_routes_to_failed():
    result, mock_status = _run(_base_state(status="failed", pr_url=""))

    mock_status.assert_called_once_with(
        "wf-123",
        WorkflowStatus.FAILED,
        pr_url=None,
        actor="generate_code",
    )
    assert result["failed_node"] == "generate_code"
