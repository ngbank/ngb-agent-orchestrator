"""Tests for code_generator subgraph nodes and shared execution helpers."""

import json
from contextlib import contextmanager
from unittest.mock import MagicMock, mock_open, patch

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
# code_generation_failure_summary (shared helper)
# ---------------------------------------------------------------------------


def test_code_generation_failure_summary_semantics():
    from orchestrator.shared.repo_setup.nodes.common import code_generation_failure_summary

    summary = code_generation_failure_summary("AOS-63", "boom")

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
    lp = log_path("wf-123", "generate_code")
    assert lp.name == "wf-123_generate_code.log"


def test_log_path_with_ticket_key_prefix(monkeypatch, tmp_path):
    from orchestrator.utils import log_path

    monkeypatch.setenv("LOGS_DIR", str(tmp_path / "logs"))
    lp = log_path("wf-123", "generate_code", ticket_key="AOS-77")
    assert lp.name == "AOS-77_wf-123_generate_code.log"


def test_log_path_uses_xdg_state_home_by_default(monkeypatch, tmp_path):
    """Without LOGS_DIR, base log path follows XDG state directory."""
    from orchestrator.utils import log_path

    workflow_id = "wf-xdg-123"
    monkeypatch.delenv("LOGS_DIR", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))

    lp = log_path(workflow_id, "generate_code", ticket_key="AOS-119")

    expected_prefix = (tmp_path / "xdg-state") / "ngb-agent-orchestrator" / "logs" / workflow_id
    assert str(lp).startswith(str(expected_prefix))


def test_log_path_honors_logs_dir_override(monkeypatch, tmp_path):
    """Explicit LOGS_DIR continues to override XDG-derived defaults."""
    from orchestrator.utils import log_path

    workflow_id = "wf-override-123"
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    monkeypatch.setenv("LOGS_DIR", str(tmp_path / "logs-override"))

    lp = log_path(workflow_id, "generate_code", ticket_key="AOS-119")

    expected_prefix = (tmp_path / "logs-override") / workflow_id
    assert str(lp).startswith(str(expected_prefix))


# ---------------------------------------------------------------------------
# prepare_workspace node
# ---------------------------------------------------------------------------


def test_prepare_workspace_creates_workspace_paths(monkeypatch, tmp_path):
    """prepare_workspace must populate recipe input/output paths in state."""
    import json
    import os

    from orchestrator.code_generator.nodes.prepare_workspace import prepare_workspace

    monkeypatch.setenv("LOGS_DIR", str(tmp_path / "logs"))
    state = {
        "workflow_id": "wf-prep",
        "ticket_key": "AOS-94",
        "work_plan_data": {"tasks": [{"id": 1, "title": "do thing"}]},
    }

    result = prepare_workspace(state)

    for key in ("work_plan_path", "summary_path", "reasoning_path"):
        assert result.get(key), f"{key} missing or empty"
    assert os.path.isfile(result["work_plan_path"])
    with open(result["work_plan_path"]) as f:
        assert json.load(f) == state["work_plan_data"]
    assert os.path.isfile(result["summary_path"])
    assert os.path.isfile(result["reasoning_path"])
    assert "exec_log_path" not in result

    for p in (result["work_plan_path"], result["summary_path"], result["reasoning_path"]):
        os.unlink(p)


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
        "code_generation_summary": {"branch": "feature/AOS-92+test"},
        "pr_comments": "Fix typo in line 42",
    }

    work_plan_json = json.dumps({"summary": "test work plan summary"})
    with (
        patch("orchestrator.code_generator.nodes.run_goose.run_and_tee") as mock_run,
        patch("builtins.open", mock_open(read_data=work_plan_json)),
        patch("os.path.exists", return_value=False),
    ):
        mock_run.return_value = MagicMock(returncode=0)
        run_goose(state)

        cmd = mock_run.call_args[0][0]
        assert "existing_branch=feature/AOS-92+test" in cmd
        assert "pr_comments=Fix typo in line 42" in cmd
        assert any(arg.startswith("branch_name=feature/AOS-92+") for arg in cmd)


# ---------------------------------------------------------------------------
# infer_branch_prefix node
# ---------------------------------------------------------------------------


def _mock_litellm_response(content: str) -> MagicMock:
    """Build a minimal litellm response mock with the given message content."""
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


def test_infer_branch_prefix_returns_correct_prefix(monkeypatch):
    """infer_branch_prefix returns the prefix the LLM classifies."""
    from orchestrator.code_generator.nodes.infer_branch_prefix import infer_branch_prefix

    monkeypatch.setenv("GOOSE_MODEL", "openai/gpt-4o")
    state = {
        "work_plan_data": {
            "summary": "Fix null pointer in payment processor",
            "approach": "Add null check before dereferencing",
            "tasks": [{"description": "Add guard clause in payment_processor.py"}],
        }
    }

    with patch(
        "orchestrator.code_generator.nodes.infer_branch_prefix.litellm.completion",
        return_value=_mock_litellm_response('{"prefix": "bugfix"}'),
    ):
        result = infer_branch_prefix(state)

    assert result == {"branch_prefix": "bugfix"}


def test_infer_branch_prefix_fails_on_invalid_response(monkeypatch):
    """infer_branch_prefix sets exec_error when LLM returns an unrecognised prefix."""
    from orchestrator.code_generator.nodes.infer_branch_prefix import infer_branch_prefix

    monkeypatch.setenv("GOOSE_MODEL", "openai/gpt-4o")
    state = {"work_plan_data": {"summary": "Do something", "approach": "", "tasks": []}}

    with patch(
        "orchestrator.code_generator.nodes.infer_branch_prefix.litellm.completion",
        return_value=_mock_litellm_response('{"prefix": "hotfix"}'),
    ):
        result = infer_branch_prefix(state)

    assert "exec_error" in result
    assert result["failed_node"] == "infer_branch_prefix"


def test_infer_branch_prefix_fails_on_exception(monkeypatch):
    """infer_branch_prefix sets exec_error when the LLM call raises."""
    from orchestrator.code_generator.nodes.infer_branch_prefix import infer_branch_prefix

    monkeypatch.setenv("GOOSE_MODEL", "openai/gpt-4o")
    state = {"work_plan_data": {"summary": "Do something", "approach": "", "tasks": []}}

    with patch(
        "orchestrator.code_generator.nodes.infer_branch_prefix.litellm.completion",
        side_effect=RuntimeError("connection timeout"),
    ):
        result = infer_branch_prefix(state)

    assert "exec_error" in result
    assert "connection timeout" in result["exec_error"]
    assert result["failed_node"] == "infer_branch_prefix"


def test_infer_branch_prefix_fails_when_no_model(monkeypatch):
    """infer_branch_prefix sets exec_error when GOOSE_MODEL is not set."""
    from orchestrator.code_generator.nodes.infer_branch_prefix import infer_branch_prefix

    monkeypatch.delenv("GOOSE_MODEL", raising=False)
    state = {"work_plan_data": {"summary": "something", "approach": "", "tasks": []}}

    result = infer_branch_prefix(state)

    assert "exec_error" in result
    assert result["failed_node"] == "infer_branch_prefix"


def test_run_goose_uses_inferred_branch_prefix():
    """run_goose uses branch_prefix from state when building branch_name."""
    from orchestrator.code_generator.nodes.run_goose import run_goose

    state = {
        "workflow_id": "wf-456",
        "ticket_key": "AOS-99",
        "working_dir": "/tmp/test-dir",
        "work_plan_path": "/tmp/workplan.json",
        "summary_path": "/tmp/summary.json",
        "reasoning_path": "/tmp/reasoning.txt",
        "branch_prefix": "bugfix",
    }

    work_plan_json = json.dumps({"summary": "fix null pointer in processor"})
    with (
        patch("orchestrator.code_generator.nodes.run_goose.run_and_tee") as mock_run,
        patch("builtins.open", mock_open(read_data=work_plan_json)),
        patch("os.path.exists", return_value=False),
    ):
        mock_run.return_value = MagicMock(returncode=0)
        run_goose(state)

        cmd = mock_run.call_args[0][0]
        assert any(arg.startswith("branch_name=bugfix/AOS-99+") for arg in cmd)


# ---------------------------------------------------------------------------
# persist_results node
# ---------------------------------------------------------------------------


def test_persist_results_transitions_to_pending_pr_approval_on_success():
    """persist_results transitions workflow to PENDING_PR_APPROVAL on success."""
    from orchestrator.code_generator.nodes.persist_results import persist_results

    state = {
        "workflow_id": "wf-123",
        "code_generation_summary": {
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
        patch("orchestrator.code_generator.nodes.persist_results.update_code_generation_summary"),
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
        # pr_url must be forwarded so the top-level column is written.
        assert status_calls[-1][1]["pr_url"] == "https://github.com/org/repo/pull/1"
        assert result["failed_node"] is None
        assert result["pr_url"] == "https://github.com/org/repo/pull/1"


def test_persist_results_transitions_to_failed_on_exec_error():
    """persist_results sets FAILED status and failed_node when exec_error is set."""
    from orchestrator.code_generator.nodes.persist_results import persist_results

    state = {
        "workflow_id": "wf-123",
        "code_generation_summary": {
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
        patch("orchestrator.code_generator.nodes.persist_results.update_code_generation_summary"),
    ):
        result = persist_results(state)

        assert mock_update_status.call_args[0][1] == WorkflowStatus.FAILED
        assert result["failed_node"] == "generate_code"
