"""Tests for the code_generator subgraph nodes (replaces the old monolithic execute_plan tests)."""

from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from state.workflow_status import WorkflowStatus

_PATCH_GOOSE_SESSION = "graph.code_generator.nodes.run_goose.goose_session"


@pytest.fixture(autouse=True)
def mock_goose_session():
    """Prevent goose_session from starting a real litellm proxy in tests."""

    @contextmanager
    def _noop(*args, **kwargs):
        yield {}

    with patch(_PATCH_GOOSE_SESSION, _noop):
        yield


# ---------------------------------------------------------------------------
# _failure_summary
# ---------------------------------------------------------------------------


def test_failure_summary_helper_semantics():
    from graph.code_generator.nodes.resolve_repo import _failure_summary

    summary = _failure_summary("AOS-63", "boom")

    assert summary == {
        "ticket_key": "AOS-63",
        "branch": "",
        "build": "fail",
        "tests": "skipped",
        "files_changed": [],
        "commit_sha": "",
        "pr_url": "",
        "status": "failed",
        "error": "boom",
    }


# ---------------------------------------------------------------------------
# log_path utility (unchanged, lives in graph/utils.py)
# ---------------------------------------------------------------------------


def test_log_path_without_ticket_key():
    from graph.utils import log_path

    lp = log_path("wf-123", "execute")
    assert lp.name == "wf-123_execute.log"


def test_log_path_with_ticket_key_prefix():
    from graph.utils import log_path

    lp = log_path("wf-123", "execute", ticket_key="AOS-77")
    assert lp.name == "AOS-77_wf-123_execute.log"


# ---------------------------------------------------------------------------
# clone_repo node
# ---------------------------------------------------------------------------


def test_clone_repo_uses_mkdtemp_for_working_dir():
    """mkdtemp is used so re-runs with the same workflow_id never collide."""
    from graph.code_generator.nodes.clone_repo import clone_repo

    workflow_id = "test-workflow-123"
    state = {
        "workflow_id": workflow_id,
        "ticket_key": "AOS-61",
        "repo_url": "https://github.com/org/repo.git",
        "work_plan_data": {"tasks": []},
    }

    with (
        patch(
            "graph.code_generator.nodes.clone_repo.tempfile.mkdtemp",
            return_value="/tmp/test-dir",
        ) as mock_mkdtemp,
        patch("graph.code_generator.nodes.clone_repo.run_and_tee") as mock_run,
        patch(
            "graph.code_generator.nodes.clone_repo.log_path",
            return_value=Path("/tmp/test.log"),
        ),
        patch("builtins.open", MagicMock()),
    ):
        mock_run.return_value = MagicMock(returncode=1)  # clone fails — early return
        clone_repo(state)

        mock_mkdtemp.assert_called_once()
        prefix_arg = mock_mkdtemp.call_args[1].get("prefix", "")
        assert workflow_id in prefix_arg


def test_clone_repo_no_hardcoded_tmp_path():
    """Structural: no hardcoded /tmp/ngb-execute path in clone_repo source."""
    import inspect

    import graph.code_generator.nodes.clone_repo as module

    source = inspect.getsource(module)
    assert (
        "/tmp/ngb-execute-" not in source
    ), "Hardcoded /tmp path found — replace with tempfile.mkdtemp()"


def test_clone_repo_returns_workspace_paths_even_on_failure():
    """cleanup node must have access to working_dir even when clone fails."""
    from graph.code_generator.nodes.clone_repo import clone_repo

    state = {
        "workflow_id": "wf-fail",
        "ticket_key": "AOS-61",
        "repo_url": "https://github.com/org/repo.git",
        "work_plan_data": {"tasks": []},
    }

    with (
        patch(
            "graph.code_generator.nodes.clone_repo.tempfile.mkdtemp",
            return_value="/tmp/test-dir",
        ),
        patch("graph.code_generator.nodes.clone_repo.run_and_tee") as mock_run,
        patch(
            "graph.code_generator.nodes.clone_repo.log_path",
            return_value=Path("/tmp/test.log"),
        ),
        patch("builtins.open", MagicMock()),
        patch(
            "graph.code_generator.nodes.clone_repo.tempfile.mkstemp",
            side_effect=[
                (0, "/tmp/summary.json"),
                (0, "/tmp/reasoning.txt"),
            ],
        ),
        patch("os.close"),
        patch(
            "graph.code_generator.nodes.clone_repo.tempfile.NamedTemporaryFile",
        ) as mock_ntf,
    ):
        mock_ntf.return_value.__enter__ = lambda s: MagicMock(name="/tmp/wp.json")
        mock_ntf.return_value.__exit__ = MagicMock(return_value=False)
        mock_run.return_value = MagicMock(returncode=1)  # clone fails
        result = clone_repo(state)

        assert "working_dir" in result
        assert "exec_error" in result
        assert result["exec_error"]


# ---------------------------------------------------------------------------
# run_goose node
# ---------------------------------------------------------------------------


def test_run_goose_passes_existing_branch_and_comments():
    """run_goose passes existing_branch and pr_comments to the recipe on re-execution."""
    from graph.code_generator.nodes.run_goose import run_goose

    state = {
        "workflow_id": "wf-123",
        "ticket_key": "AOS-92",
        "working_dir": "/tmp/test-dir",
        "work_plan_path": "/tmp/workplan.json",
        "summary_path": "/tmp/summary.json",
        "reasoning_path": "/tmp/reasoning.txt",
        "exec_log_path": "/tmp/test.log",
        "execution_summary": {"branch": "feature/AOS-92+test"},
        "pr_comments": "Fix typo in line 42",
    }

    with (
        patch("graph.code_generator.nodes.run_goose.run_and_tee") as mock_run,
        patch("builtins.open", MagicMock()),
        patch("os.path.exists", return_value=False),
    ):
        mock_run.return_value = MagicMock(returncode=0)
        run_goose(state)

        cmd = mock_run.call_args[0][0]
        assert "existing_branch=feature/AOS-92+test" in cmd
        assert "pr_comments=Fix typo in line 42" in cmd


# ---------------------------------------------------------------------------
# persist_results node
# ---------------------------------------------------------------------------


def test_persist_results_transitions_to_pending_pr_approval_on_success():
    """persist_results transitions workflow to PENDING_PR_APPROVAL on success."""
    from graph.code_generator.nodes.persist_results import persist_results

    state = {
        "workflow_id": "wf-123",
        "execution_summary": {
            "ticket_key": "AOS-92",
            "branch": "feature/AOS-92+test",
            "build": "pass",
            "tests": "pass",
            "files_changed": [],
            "commit_sha": "abc123",
            "pr_url": "https://github.com/org/repo/pull/1",
            "status": "success",
        },
        "exec_error": None,
    }

    with (
        patch("graph.code_generator.nodes.persist_results.update_status") as mock_update_status,
        patch("graph.code_generator.nodes.persist_results.update_execution_summary"),
        patch("graph.code_generator.nodes.persist_results.update_usage_summary"),
        patch(
            "graph.code_generator.nodes.persist_results.aggregate_token_usage",
            return_value={},
        ),
    ):
        result = persist_results(state)

        status_calls = mock_update_status.call_args_list
        assert len(status_calls) >= 1
        assert status_calls[-1][0][1] == WorkflowStatus.PENDING_PR_APPROVAL
        assert result["failed_node"] is None
        assert result["pr_url"] == "https://github.com/org/repo/pull/1"


def test_persist_results_transitions_to_failed_on_exec_error():
    """persist_results sets FAILED status and failed_node when exec_error is set."""
    from graph.code_generator.nodes.persist_results import persist_results

    state = {
        "workflow_id": "wf-123",
        "execution_summary": {
            "ticket_key": "AOS-92",
            "status": "failed",
            "error": "git clone failed",
            "branch": "",
            "build": "fail",
            "tests": "skipped",
            "files_changed": [],
            "commit_sha": "",
            "pr_url": "",
        },
        "exec_error": "git clone failed",
    }

    with (
        patch("graph.code_generator.nodes.persist_results.update_status") as mock_update_status,
        patch("graph.code_generator.nodes.persist_results.update_execution_summary"),
    ):
        result = persist_results(state)

        assert mock_update_status.call_args[0][1] == WorkflowStatus.FAILED
        assert result["failed_node"] == "execute_plan"
