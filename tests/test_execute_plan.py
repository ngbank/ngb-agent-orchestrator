"""Unit tests for graph/nodes/execute_plan.py."""

import tempfile
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

_PATCH_SESSION = "graph.nodes.execute_plan.goose_session"


@pytest.fixture(autouse=True)
def mock_goose_session():
    """Prevent goose_session from starting a real litellm proxy in tests."""

    @contextmanager
    def _noop():
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
