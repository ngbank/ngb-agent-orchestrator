from unittest.mock import MagicMock, patch


def _base_state():
    return {
        "ticket_key": "AOS-120",
        "working_dir": "/tmp/repo",
        "repo_url": "https://github.com/ngbank/ngb-agent-orchestrator.git",
        "github_token": "token-123",
        "code_generation_summary": {
            "ticket_key": "AOS-120",
            "branch": "feature/AOS-120+github-app-auth",
            "build": "pass",
            "tests": "pass",
            "files_changed": ["dispatcher/github_client.py"],
            "commit_sha": "abc123",
            "pr_url": "",
            "status": "success",
        },
        "work_plan_data": {
            "summary": "Replace gh CLI with GitHub App authentication",
            "approach": "Move GitHub operations into graph nodes.",
            "tasks": [{"id": "1", "description": "Implement client"}],
        },
        "pr_comments": "",
    }


def test_build_pr_body_fills_repository_template_fields():
    from orchestrator.code_generator.nodes.push_and_create_pr import _build_pr_body

    template = """## Description

<!-- Provide a brief description of the changes in this PR -->

## JIRA Ticket

<!-- Link to the JIRA ticket -->
- Ticket ID:
- Link:

## Changes Made

<!-- List the main changes made in this PR -->

-
-
-

## Testing

<!-- Describe the tests you ran to verify your changes -->
"""
    work_plan = {
        "approach": "Use the established workflow.",
        "tasks": [{"id": 1, "description": "Add regression coverage"}],
    }

    body = _build_pr_body("AOS-238", "Populate PR templates", work_plan, template)

    assert "<!--" not in body
    assert "Populate PR templates" in body
    assert "- Ticket ID: AOS-238" in body
    assert "[AOS-238](https://mirandags.atlassian.net/browse/AOS-238)" in body
    assert "- Add regression coverage" in body
    assert "execution summary" in body


def test_push_and_create_pr_creates_new_pr_when_none_exists():
    from orchestrator.code_generator.nodes.push_and_create_pr import push_and_create_pr

    with (
        patch(
            "orchestrator.code_generator.nodes.push_and_create_pr.push_branch_with_token"
        ) as mock_push,
        patch(
            "orchestrator.code_generator.nodes.push_and_create_pr.get_open_pr", return_value=None
        ),
        patch(
            "orchestrator.code_generator.nodes.push_and_create_pr.create_pr",
            return_value="https://github.com/ngbank/ngb-agent-orchestrator/pull/5",
        ) as mock_create,
    ):
        result = push_and_create_pr(_base_state())

    assert result["code_generation_summary"]["pr_url"].endswith("/pull/5")
    assert mock_create.called
    mock_push.assert_called_once()


def test_push_and_create_pr_reuses_existing_pr_and_adds_comment_when_needed():
    from orchestrator.code_generator.nodes.push_and_create_pr import push_and_create_pr

    state = _base_state()
    state["pr_comments"] = "Please address the review feedback"

    with (
        patch("orchestrator.code_generator.nodes.push_and_create_pr.push_branch_with_token"),
        patch(
            "orchestrator.code_generator.nodes.push_and_create_pr.get_open_pr",
            return_value="https://github.com/ngbank/ngb-agent-orchestrator/pull/7",
        ),
        patch(
            "orchestrator.code_generator.nodes.push_and_create_pr.add_pr_comment"
        ) as mock_comment,
    ):
        result = push_and_create_pr(state)

    assert result["code_generation_summary"]["pr_url"].endswith("/pull/7")
    mock_comment.assert_called_once()


def test_push_and_create_pr_skips_when_exec_error_set():
    from orchestrator.code_generator.nodes.push_and_create_pr import push_and_create_pr

    state = _base_state()
    state["exec_error"] = "earlier failure"

    with patch(
        "orchestrator.code_generator.nodes.push_and_create_pr.push_branch_with_token"
    ) as mock_push:
        result = push_and_create_pr(state)

    assert result["code_generation_summary"]["status"] == "success"
    mock_push.assert_not_called()


def test_push_and_create_pr_fails_when_reexecution_produces_no_new_commits():
    from orchestrator.code_generator.nodes.push_and_create_pr import push_and_create_pr

    state = _base_state()
    state["pr_comments"] = "Please fix the typos"

    # origin/<branch> already at the same SHA — Goose made no new commits
    mock_rev = MagicMock()
    mock_rev.returncode = 0
    mock_rev.stdout = "abc123\n"

    with (
        patch(
            "orchestrator.code_generator.nodes.push_and_create_pr.subprocess.run",
            return_value=mock_rev,
        ) as mock_sub,
        patch(
            "orchestrator.code_generator.nodes.push_and_create_pr.push_branch_with_token"
        ) as mock_push,
    ):
        result = push_and_create_pr(state)

    assert result["code_generation_summary"]["status"] == "failed"
    assert "no new commits" in result["code_generation_summary"]["error"]
    assert result["failed_node"] == "generate_code"
    mock_push.assert_not_called()
    mock_sub.assert_called_once()


def test_push_and_create_pr_proceeds_when_reexecution_has_new_commits():
    from orchestrator.code_generator.nodes.push_and_create_pr import push_and_create_pr

    state = _base_state()
    state["pr_comments"] = "Please fix the typos"

    # origin/<branch> at a different SHA — Goose did make new commits
    mock_rev = MagicMock()
    mock_rev.returncode = 0
    mock_rev.stdout = "old-sha-xyz\n"

    with (
        patch(
            "orchestrator.code_generator.nodes.push_and_create_pr.subprocess.run",
            return_value=mock_rev,
        ),
        patch(
            "orchestrator.code_generator.nodes.push_and_create_pr.push_branch_with_token"
        ) as mock_push,
        patch(
            "orchestrator.code_generator.nodes.push_and_create_pr.get_open_pr",
            return_value="https://github.com/ngbank/ngb-agent-orchestrator/pull/7",
        ),
        patch("orchestrator.code_generator.nodes.push_and_create_pr.add_pr_comment"),
    ):
        result = push_and_create_pr(state)

    mock_push.assert_called_once()
    assert result["code_generation_summary"]["pr_url"].endswith("/pull/7")


def test_push_and_create_pr_downgrades_to_partial_on_push_failure():
    from dispatcher.github_client import GitHubAuthError
    from orchestrator.code_generator.nodes.push_and_create_pr import push_and_create_pr

    with patch(
        "orchestrator.code_generator.nodes.push_and_create_pr.push_branch_with_token",
        side_effect=GitHubAuthError("git push failed"),
    ):
        result = push_and_create_pr(_base_state())

    assert result["code_generation_summary"]["status"] == "partial"
    assert result["code_generation_summary"]["pr_url"] == ""
