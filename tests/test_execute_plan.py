"""Tests for code_generator subgraph nodes and shared execution helpers."""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from state.workflow_status import WorkflowStatus

_PATCH_GOOSE_SESSION = "orchestrator.code_generator.nodes.run_goose.goose_session"


@pytest.fixture(autouse=True)
def mock_goose_session():
    """Prevent goose_session from starting a real litellm proxy in tests."""

    @contextmanager
    def _noop(*args, **kwargs):
        yield {}

    with patch(_PATCH_GOOSE_SESSION, _noop):
        yield


# ---------------------------------------------------------------------------
# execution_failure_summary (shared helper)
# ---------------------------------------------------------------------------


def test_execution_failure_summary_semantics():
    from orchestrator.shared.repo_setup.nodes.common import execution_failure_summary

    summary = execution_failure_summary("AOS-63", "boom")

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
# log_path utility (orchestrator/utils.py)
# ---------------------------------------------------------------------------


def test_log_path_without_ticket_key(monkeypatch, tmp_path):
    from orchestrator.utils import log_path

    monkeypatch.setenv("LOGS_DIR", str(tmp_path / "logs"))
    lp = log_path("wf-123", "execute")
    assert lp.name == "wf-123_execute.log"


def test_log_path_with_ticket_key_prefix(monkeypatch, tmp_path):
    from orchestrator.utils import log_path

    monkeypatch.setenv("LOGS_DIR", str(tmp_path / "logs"))
    lp = log_path("wf-123", "execute", ticket_key="AOS-77")
    assert lp.name == "AOS-77_wf-123_execute.log"


def test_log_path_uses_xdg_state_home_by_default(monkeypatch, tmp_path):
    """Without LOGS_DIR, base log path follows XDG state directory."""
    from orchestrator.utils import log_path

    workflow_id = "wf-xdg-123"
    monkeypatch.delenv("LOGS_DIR", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))

    lp = log_path(workflow_id, "execute", ticket_key="AOS-119")

    expected_prefix = (tmp_path / "xdg-state") / "ngb-agent-orchestrator" / "logs" / workflow_id
    assert str(lp).startswith(str(expected_prefix))


def test_log_path_honors_logs_dir_override(monkeypatch, tmp_path):
    """Explicit LOGS_DIR continues to override XDG-derived defaults."""
    from orchestrator.utils import log_path

    workflow_id = "wf-override-123"
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    monkeypatch.setenv("LOGS_DIR", str(tmp_path / "logs-override"))

    lp = log_path(workflow_id, "execute", ticket_key="AOS-119")

    expected_prefix = (tmp_path / "logs-override") / workflow_id
    assert str(lp).startswith(str(expected_prefix))


# ---------------------------------------------------------------------------
# run_goose node
# ---------------------------------------------------------------------------


def test_run_goose_passes_existing_branch_and_comments():
    """run_goose passes existing_branch and pr_comments to the recipe on re-execution."""
    from orchestrator.code_generator.nodes.run_goose import run_goose

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
        patch("orchestrator.code_generator.nodes.run_goose.run_and_tee") as mock_run,
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
    from orchestrator.code_generator.nodes.persist_results import persist_results

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
        patch(
            "orchestrator.code_generator.nodes.persist_results.update_status"
        ) as mock_update_status,
        patch("orchestrator.code_generator.nodes.persist_results.update_execution_summary"),
        patch("orchestrator.code_generator.nodes.persist_results.update_usage_summary"),
        patch(
            "orchestrator.code_generator.nodes.persist_results.aggregate_token_usage",
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
    from orchestrator.code_generator.nodes.persist_results import persist_results

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
        patch(
            "orchestrator.code_generator.nodes.persist_results.update_status"
        ) as mock_update_status,
        patch("orchestrator.code_generator.nodes.persist_results.update_execution_summary"),
    ):
        result = persist_results(state)

        assert mock_update_status.call_args[0][1] == WorkflowStatus.FAILED
        assert result["failed_node"] == "execute_plan"
