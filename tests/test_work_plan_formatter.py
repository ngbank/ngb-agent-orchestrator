"""
Unit tests for WorkPlan comment formatter.

Tests cover:
- Complete WorkPlan formatting with all sections
- Minimal WorkPlan with empty arrays
- Special characters and markdown handling
- All required sections present in output
"""

import pytest

from dispatcher.work_plan_formatter import (
    format_execution_summary_comment,
    format_work_plan_comment,
)


@pytest.fixture
def complete_work_plan():
    """Complete WorkPlan with all fields populated."""
    return {
        "schema_version": "1.0",
        "ticket_key": "AOS-39",
        "summary": "Post WorkPlan to Jira as formatted comment",
        "approach": "Implement ACLI integration to post comments and store JSON in SQLite",
        "tasks": [
            {
                "id": 1,
                "description": "Extend Jira client for comment posting",
                "files_likely_affected": ["dispatcher/jira_client.py"],
            },
            {
                "id": 2,
                "description": "Create WorkPlan comment formatter",
                "files_likely_affected": ["dispatcher/work_plan_formatter.py"],
            },
            {
                "id": 3,
                "description": "Integrate into dispatcher",
                "files_likely_affected": ["dispatcher/run.py", "state/state_store.py"],
            },
        ],
        "risks": [
            "ACLI might not be authenticated",
            "Jira comment size limits could be exceeded for large plans",
        ],
        "questions_for_reviewer": [
            "Should we support updating existing comments?",
            "Do we need to notify reviewers via @mention?",
        ],
        "status": "pass",
    }


@pytest.fixture
def minimal_work_plan():
    """Minimal WorkPlan with required fields only."""
    return {
        "schema_version": "1.0",
        "ticket_key": "AOS-40",
        "summary": "Simple task",
        "approach": "Direct implementation",
        "tasks": [{"id": 1, "description": "Do the thing", "files_likely_affected": []}],
        "risks": [],
        "questions_for_reviewer": [],
        "status": "concerns",
    }


@pytest.fixture
def blocked_work_plan():
    """WorkPlan with blocked status."""
    return {
        "schema_version": "1.0",
        "ticket_key": "AOS-41",
        "summary": "Cannot proceed",
        "approach": "This is blocked",
        "tasks": [{"id": 1, "description": "Cannot do this", "files_likely_affected": []}],
        "risks": ["Critical blocker found"],
        "questions_for_reviewer": ["How should we proceed?"],
        "status": "blocked",
    }


def test_format_complete_plan(complete_work_plan):
    """Test formatting a complete WorkPlan with all sections."""
    comment = format_work_plan_comment(complete_work_plan, "AOS-39")

    assert "# 🤖 Agent WorkPlan for AOS-39" in comment
    assert "## 📋 Plan Summary" in comment
    assert "## ✅ Task List" in comment
    assert "## ⚠️ Risks" in comment
    assert "## ❓ Questions for Reviewer" in comment
    assert "## 🎯 Approval Instructions" in comment

    assert "Post WorkPlan to Jira as formatted comment" in comment
    assert "Implement ACLI integration" in comment
    assert "Extend Jira client for comment posting" in comment
    assert "ACLI might not be authenticated" in comment
    assert "Should we support updating existing comments?" in comment

    assert "dispatcher approve AOS-39" in comment
    assert "dispatcher reject AOS-39" in comment

    assert "✅ PASS" in comment

    assert "<!-- WorkPlan v1.0 -->" in comment
    assert "*WorkPlan Schema Version: 1.0*" in comment


def test_format_minimal_plan(minimal_work_plan):
    """Test formatting a minimal WorkPlan with empty arrays."""
    comment = format_work_plan_comment(minimal_work_plan, "AOS-40")

    assert "# 🤖 Agent WorkPlan for AOS-40" in comment
    assert "## 📋 Plan Summary" in comment
    assert "## ✅ Task List" in comment
    assert "## ⚠️ Risks" in comment
    assert "## ❓ Questions for Reviewer" in comment
    assert "## 🎯 Approval Instructions" in comment

    assert "*No risks identified*" in comment
    assert "*No questions*" in comment

    assert "⚠️ CONCERNS" in comment


def test_format_blocked_plan(blocked_work_plan):
    """Test formatting a blocked WorkPlan."""
    comment = format_work_plan_comment(blocked_work_plan, "AOS-41")

    assert "🚫 BLOCKED" in comment

    assert "Cannot proceed" in comment
    assert "Critical blocker found" in comment
    assert "How should we proceed?" in comment


def test_task_formatting(complete_work_plan):
    """Test that tasks are formatted correctly."""
    comment = format_work_plan_comment(complete_work_plan, "AOS-39")

    assert "### Task 1" in comment
    assert "### Task 2" in comment
    assert "### Task 3" in comment

    assert "dispatcher/jira_client.py" in comment
    assert "dispatcher/work_plan_formatter.py" in comment
    assert "dispatcher/run.py" in comment
    assert "state/state_store.py" in comment

    assert "*Files likely affected:*" in comment


def test_approval_instructions(complete_work_plan):
    """Test that approval instructions are formatted correctly."""
    comment = format_work_plan_comment(complete_work_plan, "AOS-39")

    assert "{code}" in comment
    assert "dispatcher approve AOS-39" in comment
    assert "dispatcher reject AOS-39" in comment


def test_missing_optional_fields():
    """Test handling of missing optional fields."""
    incomplete_plan = {
        "schema_version": "1.0",
        "ticket_key": "AOS-42",
        "summary": "Test",
        "approach": "Test approach",
        "tasks": [],
        "risks": [],
        "questions_for_reviewer": [],
        "status": "pass",
    }

    comment = format_work_plan_comment(incomplete_plan, "AOS-42")

    assert "AOS-42" in comment
    assert "*No tasks defined*" in comment
    assert "*No risks identified*" in comment
    assert "*No questions*" in comment


def test_special_characters_in_content():
    """Test that special characters are preserved in formatting."""
    plan_with_special_chars = {
        "schema_version": "1.0",
        "ticket_key": "AOS-43",
        "summary": 'Task with <special> & "characters"',
        "approach": "Handle edge cases: *asterisks*, _underscores_, [brackets]",
        "tasks": [
            {
                "id": 1,
                "description": "Fix bug in component: <Button />",
                "files_likely_affected": ["src/Button.tsx"],
            }
        ],
        "risks": ['Risk with "quotes" and & ampersands'],
        "questions_for_reviewer": ["What about $variables?"],
        "status": "pass",
    }

    comment = format_work_plan_comment(plan_with_special_chars, "AOS-43")

    assert "<special>" in comment
    assert "&" in comment
    assert '"characters"' in comment
    assert "*asterisks*" in comment
    assert "<Button />" in comment


class TestFormatExecutionSummaryComment:
    """Tests for format_execution_summary_comment."""

    def test_success_with_pr_url(self):
        """Test formatting a successful execution summary that includes a PR URL."""
        summary = {
            "ticket_key": "AOS-42",
            "branch": "feature/AOS-42+branch-push-and-pr",
            "build": "pass",
            "tests": "pass",
            "files_changed": ["dispatcher/run.py", "dispatcher/work_plan_formatter.py"],
            "commit_sha": "abc123def456",
            "pr_url": "https://github.com/org/repo/pull/99",
            "status": "success",
        }
        comment = format_execution_summary_comment(summary)

        assert "# ✅ Execution Summary" in comment
        assert "*Branch:* {code}feature/AOS-42+branch-push-and-pr{code}" in comment
        assert "*Status:* SUCCESS" in comment
        assert "*Build:* pass" in comment
        assert "*Tests:* pass" in comment
        assert "*Files changed:*" in comment
        assert "- {code}dispatcher/run.py{code}" in comment
        assert "- {code}dispatcher/work_plan_formatter.py{code}" in comment
        assert "*Commit:* {code}abc123def456{code}" in comment
        assert (
            "*Pull Request:* [https://github.com/org/repo/pull/99|https://github.com/org/repo/pull/99]"
            in comment
        )

    def test_partial_without_pr_url(self):
        """Test formatting a partial execution summary without PR URL."""
        summary = {
            "ticket_key": "AOS-42",
            "branch": "feature/AOS-42+branch-push-and-pr",
            "build": "pass",
            "tests": "fail",
            "files_changed": ["dispatcher/run.py"],
            "commit_sha": "abcdef123456",
            "pr_url": "",
            "status": "partial",
        }
        comment = format_execution_summary_comment(summary)

        assert "# ⚠️ Execution Summary" in comment
        assert "*Status:* PARTIAL" in comment
        assert "*Build:* pass" in comment
        assert "*Tests:* fail" in comment
        assert "*Pull Request:*" not in comment

    def test_failed_with_error(self):
        """Test formatting a failed execution summary with error text."""
        summary = {
            "ticket_key": "AOS-42",
            "branch": "",
            "build": "fail",
            "tests": "skipped",
            "files_changed": [],
            "commit_sha": "",
            "pr_url": "",
            "status": "failed",
            "error": "Goose command timed out",
        }
        comment = format_execution_summary_comment(summary)

        assert "# ❌ Execution Summary" in comment
        assert "*Status:* FAILED" in comment
        assert "*Build:* fail" in comment
        assert "*Tests:* skipped" in comment
        assert "*Error:* Goose command timed out" in comment
        assert "*Branch:*" not in comment
        assert "*Commit:*" not in comment
        assert "*Files changed:*" not in comment

    def test_unknown_status_fallback(self):
        """Unknown status should use fallback emoji and uppercase status text."""
        summary = {
            "ticket_key": "AOS-42",
            "branch": "",
            "build": "pass",
            "tests": "pass",
            "files_changed": [],
            "commit_sha": "",
            "pr_url": "",
            "status": "mystery",
        }
        comment = format_execution_summary_comment(summary)

        assert "# ❓ Execution Summary" in comment
        assert "*Status:* MYSTERY" in comment
