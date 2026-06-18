"""Unit tests for work planner repo setup/cleanup nodes."""

import os
from unittest.mock import MagicMock, patch

from dispatcher.github_client import GitHubAuthError
from orchestrator.work_planner.nodes.cleanup import cleanup
from orchestrator.work_planner.nodes.clone_repo import clone_repo
from orchestrator.work_planner.nodes.fetch_github_token import fetch_github_token
from orchestrator.work_planner.nodes.resolve_repo import resolve_repo


def _run_result(returncode: int) -> MagicMock:
    mock = MagicMock()
    mock.returncode = returncode
    return mock


def test_resolve_repo_prefers_existing_repo_url():
    result = resolve_repo(
        {
            "ticket_key": "AOS-122",
            "repo_url": "https://github.com/ngbank/ngb-agent-orchestrator.git",
        }
    )
    assert result["repo_url"] == "https://github.com/ngbank/ngb-agent-orchestrator.git"


def test_resolve_repo_uses_project_mapping():
    with patch("orchestrator.work_planner.nodes.resolve_repo.get_repo_for_project") as mock_get:
        mock_get.return_value = "git@github.com-ngbank:ngbank/ngb-agent-orchestrator.git"
        result = resolve_repo({"ticket_key": "AOS-122"})

    assert result["repo_url"] == "git@github.com-ngbank:ngbank/ngb-agent-orchestrator.git"


def test_resolve_repo_returns_error_on_missing_mapping():
    with patch("orchestrator.work_planner.nodes.resolve_repo.get_repo_for_project") as mock_get:
        mock_get.side_effect = ValueError("No repository mapped for project 'AOS'")
        result = resolve_repo({"ticket_key": "AOS-122"})

    assert "error" in result
    assert result["failed_node"] == "resolve_repo"


def test_fetch_github_token_skips_for_ssh_repo():
    result = fetch_github_token(
        {
            "ticket_key": "AOS-122",
            "repo_url": "git@github.com-ngbank:ngbank/ngb-agent-orchestrator.git",
        }
    )
    assert result == {}


def test_fetch_github_token_returns_token_for_https_repo():
    with patch(
        "orchestrator.work_planner.nodes.fetch_github_token.get_installation_token"
    ) as mock_get_token:
        mock_get_token.return_value = "ghs_test"
        result = fetch_github_token(
            {
                "ticket_key": "AOS-122",
                "repo_url": "https://github.com/ngbank/ngb-agent-orchestrator.git",
            }
        )

    assert result["github_token"] == "ghs_test"


def test_fetch_github_token_returns_error_on_auth_failure():
    with patch(
        "orchestrator.work_planner.nodes.fetch_github_token.get_installation_token"
    ) as mock_get_token:
        mock_get_token.side_effect = GitHubAuthError("auth failed")
        result = fetch_github_token(
            {
                "ticket_key": "AOS-122",
                "repo_url": "https://github.com/ngbank/ngb-agent-orchestrator.git",
            }
        )

    assert "error" in result
    assert result["failed_node"] == "fetch_github_token"


def test_clone_repo_returns_working_dir_on_success(tmp_path):
    with patch("orchestrator.work_planner.nodes.clone_repo.run_and_tee") as mock_run:
        mock_run.return_value = _run_result(0)
        result = clone_repo(
            {
                "workflow_id": "wf-1",
                "ticket_key": "AOS-122",
                "repo_url": "git@github.com-ngbank:ngbank/ngb-agent-orchestrator.git",
            }
        )

    assert "working_dir" in result
    assert os.path.isdir(result["working_dir"])
    cleanup({"working_dir": result["working_dir"]})


def test_clone_repo_returns_error_on_clone_failure():
    with patch("orchestrator.work_planner.nodes.clone_repo.run_and_tee") as mock_run:
        mock_run.return_value = _run_result(1)
        result = clone_repo(
            {
                "workflow_id": "wf-1",
                "ticket_key": "AOS-122",
                "repo_url": "git@github.com-ngbank:ngbank/ngb-agent-orchestrator.git",
            }
        )

    assert "error" in result
    assert result["failed_node"] == "clone_repo"
    if result.get("working_dir"):
        cleanup({"working_dir": result["working_dir"]})


def test_cleanup_removes_working_directory(tmp_path):
    working_dir = tmp_path / "clone"
    working_dir.mkdir()
    (working_dir / "dummy.txt").write_text("x", encoding="utf-8")

    cleanup({"working_dir": str(working_dir)})

    assert not working_dir.exists()
