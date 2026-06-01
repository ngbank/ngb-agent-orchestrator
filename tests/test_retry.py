"""Tests for the --retry workflow resumption feature (AOS-88)."""

import os
import tempfile
from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner
from langgraph.checkpoint.memory import MemorySaver

from dispatcher.jira_client import JiraTicket
from dispatcher.run import run
from graph.retry import find_rewind_config, resolve_parent_node
from state import state_store
from state.workflow_status import WorkflowStatus


@pytest.fixture
def test_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        db_path = f.name
    os.unlink(db_path)

    original_db_path = os.environ.get("DB_PATH")
    os.environ["DB_PATH"] = db_path
    state_store.run_migrations()

    yield db_path

    if os.path.exists(db_path):
        os.unlink(db_path)
    if original_db_path:
        os.environ["DB_PATH"] = original_db_path
    elif "DB_PATH" in os.environ:
        del os.environ["DB_PATH"]


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def memory_checkpointer():
    return MemorySaver()


@pytest.fixture
def mock_jira_client():
    """Mock JIRA client for predictable responses."""
    with patch("graph.work_planner.nodes.fetch_ticket.JiraClient") as mock:
        mock_instance = Mock()
        mock_instance.get_ticket.return_value = JiraTicket(
            key="TEST-123",
            title="Test Ticket",
            description="Test description",
            labels=["test"],
            status="To Do",
        )
        mock.return_value = mock_instance
        yield mock


# ---------------------------------------------------------------------------
# WorkflowStatus.is_retryable
# ---------------------------------------------------------------------------


def test_is_retryable_failed_and_pr_commented():
    retryable = {WorkflowStatus.FAILED, WorkflowStatus.IN_PROGRESS, WorkflowStatus.PR_COMMENTED}
    for status in WorkflowStatus:
        if status in retryable:
            assert status.is_retryable() is True, f"{status} should be retryable"
        else:
            assert status.is_retryable() is False, f"{status} should not be retryable"


# ---------------------------------------------------------------------------
# state_store.increment_retry_count
# ---------------------------------------------------------------------------


def test_increment_retry_count_starts_at_zero(test_db):
    wf_id = state_store.create_workflow("TEST-1")
    wf = state_store.get_workflow(wf_id)
    assert wf["retry_count"] == 0


def test_increment_retry_count_increments(test_db):
    wf_id = state_store.create_workflow("TEST-1")

    assert state_store.increment_retry_count(wf_id) == 1
    assert state_store.get_workflow(wf_id)["retry_count"] == 1

    assert state_store.increment_retry_count(wf_id) == 2
    assert state_store.get_workflow(wf_id)["retry_count"] == 2


def test_increment_retry_count_unknown_workflow_returns_zero(test_db):
    assert state_store.increment_retry_count("does-not-exist") == 0


def test_increment_retry_count_writes_audit_log(test_db):
    wf_id = state_store.create_workflow("TEST-1")
    state_store.increment_retry_count(wf_id, actor="alice")

    log = state_store.get_audit_log(wf_id)
    actions = [entry["action"] for entry in log]
    assert "workflow_retried" in actions
    retry_entry = next(e for e in log if e["action"] == "workflow_retried")
    assert retry_entry["actor"] == "alice"
    assert "#1" in (retry_entry["reason"] or "")


# ---------------------------------------------------------------------------
# state_store.get_latest_retryable_workflow_by_ticket
# ---------------------------------------------------------------------------


def test_get_latest_retryable_returns_none_when_no_workflows(test_db):
    assert state_store.get_latest_retryable_workflow_by_ticket("NOPE-1") is None


def test_get_latest_retryable_returns_none_when_no_failed(test_db):
    state_store.create_workflow("TEST-1", status=WorkflowStatus.COMPLETED)
    state_store.create_workflow("TEST-1", status=WorkflowStatus.PENDING_APPROVAL)
    assert state_store.get_latest_retryable_workflow_by_ticket("TEST-1") is None


def test_get_latest_retryable_returns_most_recent_failed(test_db):
    old_id = state_store.create_workflow("TEST-1", status=WorkflowStatus.FAILED)
    # second insertion has a strictly later created_at because we generate it now
    new_id = state_store.create_workflow("TEST-1", status=WorkflowStatus.FAILED)

    result = state_store.get_latest_retryable_workflow_by_ticket("TEST-1")
    assert result is not None
    assert result["id"] in {old_id, new_id}
    # The most-recently created workflow should win.  Both rows have timestamps
    # produced by `datetime.now(UTC)` at different microseconds; we accept
    # either ordering on collision but require deterministic preference for
    # non-collision case.
    rows = state_store.get_workflow_by_ticket("TEST-1")
    expected = rows[0]["id"]  # rows are ordered DESC by created_at
    assert result["id"] == expected


# ---------------------------------------------------------------------------
# graph.retry helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "failed_node,expected_parent",
    [
        ("validate_input", "work_planner"),
        ("check_duplicate", "work_planner"),
        ("fetch_ticket", "work_planner"),
        ("create_workflow_record", "work_planner"),
        ("generate_plan", "work_planner"),
        ("validate_plan", "work_planner"),
        ("store_plan", "work_planner"),
        ("post_to_jira", "work_planner"),
        ("execute_plan", "execute_plan"),
        ("await_approval", "await_approval"),
    ],
)
def test_resolve_parent_node(failed_node, expected_parent):
    assert resolve_parent_node(failed_node) == expected_parent


def test_find_rewind_config_picks_first_matching_snapshot():
    """The most recent snapshot whose ``next`` includes the parent node wins."""
    mock_graph = Mock()
    cfg_a = {"configurable": {"thread_id": "t", "checkpoint_id": "a"}}
    cfg_b = {"configurable": {"thread_id": "t", "checkpoint_id": "b"}}
    cfg_c = {"configurable": {"thread_id": "t", "checkpoint_id": "c"}}

    snap_a = Mock(config=cfg_a, next=("error_handler",))
    snap_b = Mock(config=cfg_b, next=("execute_plan",))
    snap_c = Mock(config=cfg_c, next=("await_approval",))

    # get_state_history yields newest first
    mock_graph.get_state_history.return_value = iter([snap_a, snap_b, snap_c])

    result = find_rewind_config(mock_graph, {"configurable": {"thread_id": "t"}}, "execute_plan")
    assert result == cfg_b


def test_find_rewind_config_returns_none_when_no_match():
    mock_graph = Mock()
    snap = Mock(config={"x": 1}, next=("error_handler",))
    mock_graph.get_state_history.return_value = iter([snap])

    result = find_rewind_config(mock_graph, {}, "execute_plan")
    assert result is None


# ---------------------------------------------------------------------------
# dispatcher --retry handler
# ---------------------------------------------------------------------------


def _failed_workflow(ticket_key: str, failed_node: str) -> str:
    """Create a FAILED workflow row and return its id."""
    wf_id = state_store.create_workflow(ticket_key, status=WorkflowStatus.FAILED)
    return wf_id


def _mock_graph_with_failed_node(failed_node: str, retry_result: dict) -> Mock:
    """Build a mock graph whose ``get_state`` reports ``failed_node`` and
    whose ``invoke`` returns ``retry_result``.
    """
    mock_graph = Mock()
    mock_state = Mock(values={"failed_node": failed_node})
    mock_graph.get_state.return_value = mock_state

    # Provide one snapshot whose ``next`` matches the parent node so
    # find_rewind_config succeeds.
    parent = resolve_parent_node(failed_node)
    snap = Mock(config={"configurable": {"thread_id": "t", "checkpoint_id": "cp"}})
    snap.next = (parent,)
    mock_graph.get_state_history.return_value = iter([snap])

    mock_graph.invoke.return_value = retry_result
    return mock_graph


def test_retry_requires_ticket_or_workflow_id(test_db, cli_runner):
    result = cli_runner.invoke(run, ["--retry"])
    assert result.exit_code != 0
    assert "--retry requires --ticket or --workflow-id" in result.output


def test_retry_rejects_non_failed_workflow(test_db, cli_runner):
    wf_id = state_store.create_workflow("TEST-1", status=WorkflowStatus.COMPLETED)
    result = cli_runner.invoke(run, ["--retry", "--workflow-id", wf_id])
    assert result.exit_code != 0
    assert "not retryable" in result.output


def test_retry_resolves_by_ticket(test_db, cli_runner):
    wf_id = _failed_workflow("TEST-1", "execute_plan")
    success_summary = {
        "status": "success",
        "branch": "feature/TEST-1+x",
        "build": "pass",
        "tests": "pass",
        "pr_url": "https://pr",
    }

    with patch("dispatcher.run.build_orchestrator") as mock_build:
        mock_build.return_value = _mock_graph_with_failed_node(
            "execute_plan",
            {"workflow_id": wf_id, "ticket_key": "TEST-1", "execution_summary": success_summary},
        )
        with patch("dispatcher.run._post_execution_comment"):
            result = cli_runner.invoke(run, ["--retry", "--ticket", "TEST-1"])

    assert result.exit_code == 0, result.output
    wf = state_store.get_workflow(wf_id)
    assert wf["status"] == WorkflowStatus.COMPLETED
    assert wf["retry_count"] == 1


def test_retry_resolves_by_workflow_id(test_db, cli_runner):
    wf_id = _failed_workflow("TEST-1", "execute_plan")
    success_summary = {
        "status": "success",
        "branch": "feature/TEST-1+x",
        "build": "pass",
        "tests": "pass",
        "pr_url": "https://pr",
    }

    with patch("dispatcher.run.build_orchestrator") as mock_build:
        mock_build.return_value = _mock_graph_with_failed_node(
            "execute_plan",
            {"workflow_id": wf_id, "ticket_key": "TEST-1", "execution_summary": success_summary},
        )
        with patch("dispatcher.run._post_execution_comment"):
            result = cli_runner.invoke(run, ["--retry", "--workflow-id", wf_id])

    assert result.exit_code == 0, result.output
    assert state_store.get_workflow(wf_id)["retry_count"] == 1


def test_retry_without_failed_node_errors(test_db, cli_runner):
    wf_id = _failed_workflow("TEST-1", "execute_plan")

    with patch("dispatcher.run.build_orchestrator") as mock_build:
        mock_graph = Mock()
        # No failed_node recorded AND no pending next node → cannot resume.
        mock_graph.get_state.return_value = Mock(values={}, next=())
        mock_build.return_value = mock_graph
        result = cli_runner.invoke(run, ["--retry", "--workflow-id", wf_id])

    assert result.exit_code != 0
    assert "no recorded failed_node" in result.output


def test_retry_transitions_to_in_progress_then_completed(test_db, cli_runner):
    wf_id = _failed_workflow("TEST-1", "execute_plan")
    success_summary = {
        "status": "success",
        "branch": "feature/TEST-1+x",
        "build": "pass",
        "tests": "pass",
        "pr_url": "https://pr",
    }

    captured_status_during_invoke = {}

    def fake_invoke(*_args, **_kwargs):
        captured_status_during_invoke["status"] = state_store.get_workflow(wf_id)["status"]
        return {"workflow_id": wf_id, "ticket_key": "TEST-1", "execution_summary": success_summary}

    with patch("dispatcher.run.build_orchestrator") as mock_build:
        mock_graph = _mock_graph_with_failed_node("execute_plan", {})
        mock_graph.invoke = fake_invoke
        mock_build.return_value = mock_graph
        with patch("dispatcher.run._post_execution_comment"):
            result = cli_runner.invoke(run, ["--retry", "--workflow-id", wf_id])

    assert result.exit_code == 0, result.output
    assert captured_status_during_invoke["status"] == WorkflowStatus.IN_PROGRESS
    assert state_store.get_workflow(wf_id)["status"] == WorkflowStatus.COMPLETED


def test_retry_marks_failed_when_second_attempt_also_fails(test_db, cli_runner):
    wf_id = _failed_workflow("TEST-1", "execute_plan")
    failure_summary = {
        "status": "failed",
        "build": "fail",
        "tests": "skipped",
        "error": "still broken",
    }

    with patch("dispatcher.run.build_orchestrator") as mock_build:
        mock_build.return_value = _mock_graph_with_failed_node(
            "execute_plan",
            {
                "workflow_id": wf_id,
                "ticket_key": "TEST-1",
                "execution_summary": failure_summary,
                "failed_node": "execute_plan",
            },
        )
        with patch("dispatcher.run._post_execution_comment"):
            result = cli_runner.invoke(run, ["--retry", "--workflow-id", wf_id])

    assert result.exit_code != 0
    wf = state_store.get_workflow(wf_id)
    assert wf["status"] == WorkflowStatus.FAILED
    assert wf["retry_count"] == 1


# ---------------------------------------------------------------------------
# End-to-end integration: real LangGraph fails, then retry rewinds and resumes
# ---------------------------------------------------------------------------


_VALID_WORK_PLAN = {
    "schema_version": "1.0",
    "ticket_key": "TEST-123",
    "summary": "Do the thing",
    "approach": "Test approach",
    "tasks": [{"id": 1, "description": "Do it", "files_likely_affected": []}],
    "concerns": [],
    "status": "pass",
}


def test_retry_integration_plan_failure_then_success(
    test_db, mock_jira_client, cli_runner, memory_checkpointer
):
    """End-to-end: a plan-stage failure leaves the workflow FAILED with
    failed_node='generate_plan'.  --retry rewinds before work_planner,
    re-runs the subgraph (with generate_plan now returning a valid plan),
    and the workflow pauses at await_approval.
    """
    from graph.builder import build_orchestrator

    checkpointer = memory_checkpointer
    call_counter = {"count": 0}

    def flaky_generate_plan(state):
        call_counter["count"] += 1
        if call_counter["count"] == 1:
            return {
                "error": "simulated plan failure",
                "failed_node": "generate_plan",
            }
        return {"work_plan_data": _VALID_WORK_PLAN}

    with patch(
        "graph.work_planner.builder.generate_plan",
        side_effect=flaky_generate_plan,
    ):
        with patch("dispatcher.run.build_orchestrator") as mock_build:
            mock_build.side_effect = lambda: build_orchestrator(checkpointer=checkpointer)

            # First run — generate_plan fails, work_planner subgraph routes to
            # error_handler which marks the workflow FAILED.
            result = cli_runner.invoke(run, ["--ticket", "TEST-123"])

        workflows = state_store.get_workflow_by_ticket("TEST-123")
        assert len(workflows) == 1
        wf_id = workflows[0]["id"]
        assert workflows[0]["status"] == WorkflowStatus.FAILED, result.output

        # --retry rewinds and re-invokes; this time generate_plan succeeds and
        # the graph pauses at await_approval.
        with patch("dispatcher.run.build_orchestrator") as mock_build:
            mock_build.side_effect = lambda: build_orchestrator(checkpointer=checkpointer)
            retry_result = cli_runner.invoke(run, ["--retry", "--ticket", "TEST-123"])

    assert retry_result.exit_code == 0, retry_result.output
    assert "🔁 Retrying workflow" in retry_result.output

    wf_after = state_store.get_workflow(wf_id)
    assert wf_after["retry_count"] == 1
    # After successful replan the workflow is awaiting approval (interrupt).
    assert wf_after["status"] == WorkflowStatus.PENDING_APPROVAL
    # generate_plan ran twice: once failing, once succeeding.
    assert call_counter["count"] == 2


# ---------------------------------------------------------------------------
# AOS-89 — IN_PROGRESS retry + SIGINT recovery
# ---------------------------------------------------------------------------


def _in_progress_workflow(ticket_key: str) -> str:
    """Create an IN_PROGRESS workflow row and return its id."""
    return state_store.create_workflow(ticket_key, status=WorkflowStatus.IN_PROGRESS)


def test_retry_accepts_in_progress_workflow(test_db, cli_runner):
    """An IN_PROGRESS workflow with a recorded failed_node should be retryable."""
    wf_id = _in_progress_workflow("TEST-1")
    success_summary = {
        "status": "success",
        "branch": "feature/TEST-1+x",
        "build": "pass",
        "tests": "pass",
        "pr_url": "https://pr",
    }

    with patch("dispatcher.run.build_orchestrator") as mock_build:
        mock_build.return_value = _mock_graph_with_failed_node(
            "execute_plan",
            {"workflow_id": wf_id, "ticket_key": "TEST-1", "execution_summary": success_summary},
        )
        with patch("dispatcher.run._post_execution_comment"):
            result = cli_runner.invoke(run, ["--retry", "--workflow-id", wf_id])

    assert result.exit_code == 0, result.output
    assert "IN_PROGRESS" in result.output
    assert state_store.get_workflow(wf_id)["status"] == WorkflowStatus.COMPLETED


def test_retry_derives_failed_node_from_next_when_missing(test_db, cli_runner):
    """When failed_node is empty but snapshot.next has a value, use that as resume point."""
    wf_id = _in_progress_workflow("TEST-1")
    success_summary = {
        "status": "success",
        "branch": "feature/TEST-1+x",
        "build": "pass",
        "tests": "pass",
        "pr_url": "https://pr",
    }

    with patch("dispatcher.run.build_orchestrator") as mock_build:
        mock_graph = Mock()
        # No failed_node in state, but snapshot.next reveals where it stopped.
        mock_graph.get_state.return_value = Mock(values={}, next=("execute_plan",))
        snap = Mock(config={"configurable": {"thread_id": "t", "checkpoint_id": "cp"}})
        snap.next = ("execute_plan",)
        mock_graph.get_state_history.return_value = iter([snap])
        mock_graph.invoke.return_value = {
            "workflow_id": wf_id,
            "ticket_key": "TEST-1",
            "execution_summary": success_summary,
        }
        mock_build.return_value = mock_graph
        with patch("dispatcher.run._post_execution_comment"):
            result = cli_runner.invoke(run, ["--retry", "--workflow-id", wf_id])

    assert result.exit_code == 0, result.output
    assert "next-up node 'execute_plan'" in result.output
    assert state_store.get_workflow(wf_id)["status"] == WorkflowStatus.COMPLETED


def test_retry_still_rejects_completed_workflow(test_db, cli_runner):
    """Terminal non-failed statuses must remain non-retryable."""
    wf_id = state_store.create_workflow("TEST-1", status=WorkflowStatus.COMPLETED)
    result = cli_runner.invoke(run, ["--retry", "--workflow-id", wf_id])
    assert result.exit_code != 0
    assert "not retryable" in result.output


def test_mark_workflow_interrupted_transitions_to_failed(test_db):
    """SIGINT helper transitions an active workflow to FAILED with failed_node set."""
    from dispatcher.run import _mark_workflow_interrupted

    wf_id = _in_progress_workflow("TEST-1")
    mock_graph = Mock()
    mock_graph.get_state.return_value = Mock(values={}, next=("execute_plan",))

    _mark_workflow_interrupted(wf_id, mock_graph, {"configurable": {"thread_id": wf_id}})

    wf = state_store.get_workflow(wf_id)
    assert wf["status"] == WorkflowStatus.FAILED
    # The helper should have called update_state to record failed_node.
    mock_graph.update_state.assert_called_once()
    update_args = mock_graph.update_state.call_args
    assert update_args.args[1]["failed_node"] == "execute_plan"
    assert "Interrupted" in update_args.args[1]["error"]


def test_mark_workflow_interrupted_no_op_on_terminal(test_db):
    """Helper does nothing if the workflow is already terminal."""
    from dispatcher.run import _mark_workflow_interrupted

    wf_id = state_store.create_workflow("TEST-1", status=WorkflowStatus.COMPLETED)
    mock_graph = Mock()

    _mark_workflow_interrupted(wf_id, mock_graph, {"configurable": {"thread_id": wf_id}})

    assert state_store.get_workflow(wf_id)["status"] == WorkflowStatus.COMPLETED
    mock_graph.update_state.assert_not_called()


def test_mark_workflow_interrupted_handles_missing_workflow(test_db):
    """Helper does not crash when the workflow id is unknown."""
    from dispatcher.run import _mark_workflow_interrupted

    # Should not raise.
    _mark_workflow_interrupted("does-not-exist", None, None)


def test_mark_workflow_interrupted_db_only_when_no_graph(test_db):
    """Helper still transitions DB even if graph/state update fails."""
    from dispatcher.run import _mark_workflow_interrupted

    wf_id = _in_progress_workflow("TEST-1")
    _mark_workflow_interrupted(wf_id, None, None)

    wf = state_store.get_workflow(wf_id)
    assert wf["status"] == WorkflowStatus.FAILED
