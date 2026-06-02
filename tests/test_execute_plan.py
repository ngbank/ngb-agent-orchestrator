"""Unit tests for graph/nodes/execute_plan.py."""

import tempfile
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from state.workflow_status import WorkflowStatus

_PATCH_SESSION = "graph.nodes.execute_plan.goose_session"


@pytest.fixture(autouse=True)
def mock_goose_session():
    """Prevent goose_session from starting a real litellm proxy in tests."""

    @contextmanager
    def _noop(*args, **kwargs):
        yield {}

    with patch(_PATCH_SESSION, _noop):
        yield


def test_execute_plan_uses_mkdtemp_for_working_dir():
    """mkdtemp is used so re-runs with the same workflow_id never collide."""
    workflow_id = "test-workflow-123"
    real_mkdtemp = tempfile.mkdtemp
    unique_dirs = set()

    def fake_mkdtemp(prefix=""):
        d = real_mkdtemp(prefix=prefix)
        unique_dirs.add(d)
        return d

    state = {
        "workflow_id": workflow_id,
        "ticket_key": "AOS-61",
        "work_plan_data": {"tasks": []},
    }

    with (
        patch(
            "graph.nodes.execute_plan.get_repo_for_project",
            return_value="https://github.com/org/repo.git",
        ),
        patch(
            "graph.nodes.execute_plan.tempfile.mkdtemp", side_effect=fake_mkdtemp
        ) as mock_mkdtemp,
        patch("graph.nodes.execute_plan.run_and_tee") as mock_run,
        patch("graph.nodes.execute_plan.log_path", return_value="/tmp/test.log"),
        patch("builtins.open", MagicMock()),
        patch("graph.nodes.execute_plan.update_status"),
        patch("graph.nodes.execute_plan.update_execution_summary"),
        patch("shutil.rmtree"),
        patch("os.path.isdir", return_value=False),
    ):

        mock_run.return_value = MagicMock(returncode=1)  # clone fails → early return

        from graph.nodes.execute_plan import execute_plan

        execute_plan(state)

        mock_mkdtemp.assert_called_once()
        call_kwargs = mock_mkdtemp.call_args
        prefix_arg = call_kwargs[1].get("prefix") or (call_kwargs[0][0] if call_kwargs[0] else "")
        assert workflow_id in prefix_arg


def test_execute_plan_no_hardcoded_tmp_path():
    """Structural: no hardcoded /tmp/ngb-execute path remains in execute_plan source."""
    import inspect

    import graph.nodes.execute_plan as ep_module

    source = inspect.getsource(ep_module)
    assert (
        "/tmp/ngb-execute-" not in source
    ), "Hardcoded /tmp path found — replace with tempfile.mkdtemp()"


def test_failure_summary_helper_semantics():
    from graph.nodes.execute_plan import _failure_summary

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


def test_log_path_without_ticket_key():
    """log_path omits prefix when ticket_key is not provided."""
    from graph.utils import log_path

    lp = log_path("wf-123", "execute")
    assert lp.name == "wf-123_execute.log"


def test_log_path_with_ticket_key_prefix():
    """log_path prefixes filename with ticket_key when provided."""
    from graph.utils import log_path

    lp = log_path("wf-123", "execute", ticket_key="AOS-77")
    assert lp.name == "AOS-77_wf-123_execute.log"


def test_execute_plan_passes_existing_branch_and_comments():
    """execute_plan passes existing_branch and pr_comments to the recipe on re-execution."""
    import io
    import json

    from graph.nodes.execute_plan import execute_plan

    state = {
        "workflow_id": "wf-123",
        "ticket_key": "AOS-92",
        "work_plan_data": {"tasks": []},
        "execution_summary": {"branch": "feature/AOS-92+test"},
        "pr_comments": "Fix typo in line 42",
    }

    summary_json = json.dumps(
        {
            "ticket_key": "AOS-92",
            "branch": "feature/AOS-92+test",
            "build": "pass",
            "tests": "pass",
            "files_changed": [],
            "commit_sha": "abc123",
            "pr_url": "https://github.com/org/repo/pull/1",
            "status": "success",
        }
    )

    def mock_open(path, mode="r", **kwargs):
        if str(path).endswith("_exec_summary.json"):
            return io.StringIO(summary_json)
        return io.StringIO("")

    with (
        patch(
            "graph.nodes.execute_plan.get_repo_for_project",
            return_value="https://github.com/org/repo.git",
        ),
        patch("graph.nodes.execute_plan.tempfile.mkdtemp", return_value="/tmp/test-dir"),
        patch("graph.nodes.execute_plan.run_and_tee") as mock_run,
        patch("graph.nodes.execute_plan.log_path", return_value="/tmp/test.log"),
        patch("builtins.open", side_effect=mock_open),
        patch("graph.nodes.execute_plan.update_status"),
        patch("graph.nodes.execute_plan.update_execution_summary"),
        patch("shutil.rmtree"),
        patch("os.path.isdir", return_value=False),
    ):
        mock_run.return_value = MagicMock(returncode=0)
        execute_plan(state)

        # Find the goose run call and check params
        calls = mock_run.call_args_list
        goose_calls = [c for c in calls if c[0][0][0] == "goose"]
        assert len(goose_calls) == 1
        cmd = goose_calls[0][0][0]
        assert "existing_branch=feature/AOS-92+test" in cmd
        assert "pr_comments=Fix typo in line 42" in cmd


def test_execute_plan_transitions_to_pending_pr_approval_on_success():
    """execute_plan transitions workflow to PENDING_PR_APPROVAL on success."""
    import io
    import json

    from graph.nodes.execute_plan import execute_plan

    state = {
        "workflow_id": "wf-123",
        "ticket_key": "AOS-92",
        "work_plan_data": {"tasks": []},
    }

    summary_json = json.dumps(
        {
            "ticket_key": "AOS-92",
            "branch": "feature/AOS-92+test",
            "build": "pass",
            "tests": "pass",
            "files_changed": [],
            "commit_sha": "abc123",
            "pr_url": "https://github.com/org/repo/pull/1",
            "status": "success",
        }
    )

    def mock_open(path, mode="r", **kwargs):
        if str(path).endswith("_exec_summary.json"):
            return io.StringIO(summary_json)
        return io.StringIO("")

    with (
        patch(
            "graph.nodes.execute_plan.get_repo_for_project",
            return_value="https://github.com/org/repo.git",
        ),
        patch("graph.nodes.execute_plan.tempfile.mkdtemp", return_value="/tmp/test-dir"),
        patch("graph.nodes.execute_plan.run_and_tee") as mock_run,
        patch("graph.nodes.execute_plan.log_path", return_value="/tmp/test.log"),
        patch("builtins.open", side_effect=mock_open),
        patch("graph.nodes.execute_plan.update_status") as mock_update_status,
        patch("graph.nodes.execute_plan.update_execution_summary"),
        patch("shutil.rmtree"),
        patch("os.path.isdir", return_value=False),
    ):
        mock_run.return_value = MagicMock(returncode=0)
        execute_plan(state)

        # Check that update_status was called with PENDING_PR_APPROVAL
        status_calls = [c for c in mock_update_status.call_args_list]
        assert len(status_calls) >= 1
        assert status_calls[-1][0][1] == WorkflowStatus.PENDING_PR_APPROVAL
