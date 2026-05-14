"""Integration tests for dispatcher/run.py (LangGraph-based orchestrator)"""

import os
import re
import tempfile
from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner
from langgraph.checkpoint.memory import MemorySaver

from dispatcher.jira_client import JiraConfigurationError, JiraTicket, JiraTicketNotFoundError
from dispatcher.run import run
from state import state_store
from state.workflow_status import WorkflowStatus


@pytest.fixture
def test_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        db_path = f.name

    # Remove the file so the db can be created fresh
    os.unlink(db_path)

    # Set environment variable for test
    original_db_path = os.environ.get("DB_PATH")
    os.environ["DB_PATH"] = db_path

    # Run migrations
    state_store.run_migrations()

    yield db_path

    # Cleanup
    if os.path.exists(db_path):
        os.unlink(db_path)

    # Restore original env var
    if original_db_path:
        os.environ["DB_PATH"] = original_db_path
    elif "DB_PATH" in os.environ:
        del os.environ["DB_PATH"]


@pytest.fixture
def memory_checkpointer():
    """In-memory checkpointer so tests don't need SqliteSaver setup."""
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
            labels=["test", "automation"],
            status="To Do",
        )
        mock.return_value = mock_instance
        yield mock


@pytest.fixture
def cli_runner():
    """Create a Click CLI test runner."""
    return CliRunner()


@pytest.fixture
def mock_generate_plan():
    """Mock generate_plan node to return a schema-valid WorkPlan so tests reach await_approval."""
    with patch("graph.work_planner.builder.generate_plan") as mock:
        mock.return_value = {
            "work_plan_data": {
                "schema_version": "1.0",
                "ticket_key": "TEST-123",
                "summary": "Test plan",
                "approach": "test approach",
                "tasks": [{"id": 1, "description": "Do the thing", "files_likely_affected": []}],
                "risks": [],
                "questions_for_reviewer": [],
                "status": "pass",
            }
        }
        yield mock


def test_run_creates_workflow(
    test_db, mock_jira_client, mock_generate_plan, cli_runner, memory_checkpointer
):
    """Test that running the dispatcher creates a workflow record and pauses for approval."""
    with patch("dispatcher.run.build_orchestrator") as mock_build:
        from graph.builder import build_orchestrator

        mock_build.side_effect = lambda: build_orchestrator(checkpointer=memory_checkpointer)

        result = cli_runner.invoke(run, ["--ticket", "TEST-123"])

    assert result.exit_code == 0
    assert "🚀 Starting workflow for ticket: TEST-123" in result.output
    assert "✅ Ticket fetched: Test Ticket" in result.output
    assert "📝 Creating workflow record..." in result.output
    # Graph pauses at await_approval — no completion message yet
    assert "⏸️  WorkPlan is ready for review." in result.output

    # Verify workflow was created and is pending approval
    workflows = state_store.get_workflow_by_ticket("TEST-123")
    assert len(workflows) == 1
    assert workflows[0]["ticket_key"] == "TEST-123"
    assert workflows[0]["status"] == WorkflowStatus.PENDING_APPROVAL


def test_run_rejects_duplicate(test_db, mock_jira_client, cli_runner, memory_checkpointer):
    """Test that duplicate detection prevents running workflows twice."""
    # Create an in-progress workflow
    workflow_id = state_store.create_workflow("TEST-123", status=WorkflowStatus.IN_PROGRESS)

    with patch("dispatcher.run.build_orchestrator") as mock_build:
        from graph.builder import build_orchestrator

        mock_build.side_effect = lambda: build_orchestrator(checkpointer=memory_checkpointer)

        result = cli_runner.invoke(run, ["--ticket", "TEST-123"])

    assert result.exit_code == 1
    assert "❌ Workflow already in progress" in result.output
    assert workflow_id in result.output
    assert "Cannot start a new workflow while one is active" in result.output


def test_run_allows_rerun_after_completion(
    test_db, mock_jira_client, mock_generate_plan, cli_runner, memory_checkpointer
):
    """Test that completed workflows can be re-run (pauses at approval gate)."""
    # Create a completed workflow
    state_store.create_workflow("TEST-123", status=WorkflowStatus.COMPLETED)

    with patch("dispatcher.run.build_orchestrator") as mock_build:
        from graph.builder import build_orchestrator

        mock_build.side_effect = lambda: build_orchestrator(checkpointer=memory_checkpointer)

        result = cli_runner.invoke(run, ["--ticket", "TEST-123"])

    assert result.exit_code == 0
    assert "⚠️  Warning: 1 completed workflow(s) exist for TEST-123" in result.output
    # New run pauses at approval gate
    assert "⏸️  WorkPlan is ready for review." in result.output

    # Verify new workflow was created (2 total)
    workflows = state_store.get_workflow_by_ticket("TEST-123")
    assert len(workflows) == 2


def test_run_handles_ticket_not_found(test_db, cli_runner, memory_checkpointer):
    """Test graceful handling of non-existent tickets."""
    with patch("graph.work_planner.nodes.fetch_ticket.JiraClient") as mock:
        mock_instance = Mock()
        mock_instance.get_ticket.side_effect = JiraTicketNotFoundError("Ticket not found")
        mock.return_value = mock_instance

        with patch("dispatcher.run.build_orchestrator") as mock_build:
            from graph.builder import build_orchestrator

            mock_build.side_effect = lambda: build_orchestrator(checkpointer=memory_checkpointer)

            result = cli_runner.invoke(run, ["--ticket", "NOTFOUND-999"])

    assert result.exit_code == 1
    assert "❌ Ticket not found" in result.output

    # Verify no workflow was created (error happens before workflow creation)
    workflows = state_store.get_workflow_by_ticket("NOTFOUND-999")
    assert len(workflows) == 0


def test_run_dry_run_mode(test_db, cli_runner):
    """Test that dry-run mode doesn't mutate state."""
    result = cli_runner.invoke(run, ["--ticket", "TEST-123", "--dry-run"])

    assert result.exit_code == 0
    assert "[DRY RUN] Mode enabled" in result.output
    assert "[DRY RUN] Would fetch ticket: TEST-123" in result.output
    assert "[DRY RUN] Would create workflow for ticket: TEST-123" in result.output
    assert "[DRY RUN] Would execute workflow stages" in result.output
    assert "✅ Dry run completed successfully" in result.output

    # Verify no workflow was created in database
    workflows = state_store.get_workflow_by_ticket("TEST-123")
    assert len(workflows) == 0


def test_run_logs_transitions(
    test_db, mock_jira_client, mock_generate_plan, cli_runner, memory_checkpointer
):
    """Test that all stage transitions are logged to audit log."""
    with patch("dispatcher.run.build_orchestrator") as mock_build:
        from graph.builder import build_orchestrator

        mock_build.side_effect = lambda: build_orchestrator(checkpointer=memory_checkpointer)

        result = cli_runner.invoke(run, ["--ticket", "TEST-123"])

    assert result.exit_code == 0

    # Get workflow and audit log
    workflows = state_store.get_workflow_by_ticket("TEST-123")
    workflow_id = workflows[0]["id"]
    audit_log = state_store.get_audit_log(workflow_id)

    # Verify audit log entries (created, in_progress, pending_approval)
    assert len(audit_log) >= 3

    # Check for workflow_created entry
    created_entries = [e for e in audit_log if e["action"] == "workflow_created"]
    assert len(created_entries) == 1
    assert created_entries[0]["actor"] == "system"

    # Check for status_change entries
    status_changes = [e for e in audit_log if e["action"] == "status_change"]
    assert len(status_changes) >= 2  # in_progress, pending_approval

    # Verify dispatcher is the actor for stage transitions
    dispatcher_entries = [e for e in audit_log if e["actor"] == "dispatcher"]
    assert len(dispatcher_entries) >= 2

    # Workflow should be paused pending approval
    assert workflows[0]["status"] == WorkflowStatus.PENDING_APPROVAL


def test_run_handles_exceptions(test_db, cli_runner):
    """Test that exceptions are caught and logged with failed status."""
    with patch("graph.work_planner.nodes.fetch_ticket.JiraClient") as mock:
        mock.side_effect = Exception("Unexpected error")

        result = cli_runner.invoke(run, ["--ticket", "TEST-123"])

        assert result.exit_code == 1
        assert "❌ Unhandled error" in result.output


def test_run_validates_ticket_format(test_db, cli_runner):
    """Test that invalid ticket formats are rejected."""
    result = cli_runner.invoke(run, ["--ticket", "invalid"])

    assert result.exit_code == 1
    assert "❌ Invalid ticket format" in result.output


def test_architecture_work_planner_order_and_fetch_annotation():
    """Architecture docs should reflect work_planner order and JiraClient wording."""
    with open("docs/architecture.md", encoding="utf-8") as f:
        content = f.read()

    fetch_pos = content.find("├── fetch_ticket")
    create_pos = content.find("├── create_workflow_record")
    assert fetch_pos != -1, "fetch_ticket node missing from architecture diagram"
    assert create_pos != -1, "create_workflow_record node missing from architecture diagram"
    assert fetch_pos < create_pos, "fetch_ticket must appear before create_workflow_record"

    fetch_line_match = re.search(r"^.*fetch_ticket.*$", content, flags=re.MULTILINE)
    assert fetch_line_match is not None
    fetch_line = fetch_line_match.group(0)
    assert "JiraClient" in fetch_line
    assert "Call JIRA via acli" not in fetch_line


def test_run_handles_jira_config_error(test_db, cli_runner, memory_checkpointer):
    """Test handling of JIRA configuration errors."""
    with patch("graph.work_planner.nodes.fetch_ticket.JiraClient") as mock:
        mock.side_effect = JiraConfigurationError("Missing JIRA_URL")

        with patch("dispatcher.run.build_orchestrator") as mock_build:
            from graph.builder import build_orchestrator

            mock_build.side_effect = lambda: build_orchestrator(checkpointer=memory_checkpointer)

            result = cli_runner.invoke(run, ["--ticket", "TEST-123"])

    assert result.exit_code == 1
    assert "❌ JIRA configuration error" in result.output
    assert "JIRA_URL" in result.output


def test_check_duplicate_node(test_db):
    """Test duplicate detection via the check_duplicate graph node."""
    from graph.work_planner.nodes.check_duplicate import check_duplicate

    # No workflows — should return empty dict (no error)
    result = check_duplicate({"ticket_key": "TEST-999", "dry_run": False})
    assert result == {}

    # Create pending workflow — should set error containing the workflow id
    workflow_id = state_store.create_workflow("TEST-999", status=WorkflowStatus.PENDING)
    result = check_duplicate({"ticket_key": "TEST-999", "dry_run": False})
    assert "error" in result
    assert workflow_id in result["error"]

    # Mark completed — should return empty dict again
    state_store.update_status(workflow_id, WorkflowStatus.COMPLETED)
    result = check_duplicate({"ticket_key": "TEST-999", "dry_run": False})
    assert result == {}

    # Create in_progress workflow — should set error
    workflow_id2 = state_store.create_workflow("TEST-999", status=WorkflowStatus.IN_PROGRESS)
    result = check_duplicate({"ticket_key": "TEST-999", "dry_run": False})
    assert "error" in result
    assert workflow_id2 in result["error"]


def test_create_workflow_record_node(test_db):
    """Test that create_workflow_record creates a workflow and transitions to IN_PROGRESS."""
    from graph.work_planner.nodes.create_workflow_record import create_workflow_record

    result = create_workflow_record({"ticket_key": "TEST-123", "dry_run": False})

    assert "workflow_id" in result
    workflow_id = result["workflow_id"]

    workflow = state_store.get_workflow(workflow_id)
    assert workflow["status"] == WorkflowStatus.IN_PROGRESS

    audit_log = state_store.get_audit_log(workflow_id)
    status_changes = [e for e in audit_log if e["action"] == "status_change"]
    assert len(status_changes) >= 1


def test_run_keyboard_interrupt(test_db, cli_runner, memory_checkpointer):
    """Test that KeyboardInterrupt is handled gracefully."""
    with patch("graph.work_planner.nodes.fetch_ticket.JiraClient") as mock:
        mock_instance = Mock()
        mock_instance.get_ticket.side_effect = KeyboardInterrupt()
        mock.return_value = mock_instance

        with patch("dispatcher.run.build_orchestrator") as mock_build:
            from graph.builder import build_orchestrator

            mock_build.side_effect = lambda: build_orchestrator(checkpointer=memory_checkpointer)

            result = cli_runner.invoke(run, ["--ticket", "TEST-123"])

    assert result.exit_code == 130  # Standard SIGINT exit code
    assert "⚠️  Workflow interrupted by user" in result.output


class TestPostExecutionComment:
    """Tests for _post_execution_comment helper."""

    def test_posts_comment_with_pr_url(self, test_db):
        """Test that pr_url from the execution summary is posted to JIRA and echoed."""
        from dispatcher.run import _post_execution_comment

        execution_summary = {
            "ticket_key": "AOS-42",
            "branch": "feature/AOS-42+branch-push-and-pr",
            "build": "pass",
            "tests": "pass",
            "files_changed": ["dispatcher/run.py"],
            "commit_sha": "abc123",
            "pr_url": "https://github.com/org/repo/pull/99",
            "status": "success",
        }

        with patch("dispatcher.run.JiraClient") as mock_jira_class:
            mock_jira = mock_jira_class.return_value
            _post_execution_comment("AOS-42", execution_summary)
            mock_jira.post_comment.assert_called_once()
            call_args = mock_jira.post_comment.call_args
            assert call_args[0][0] == "AOS-42"
            assert "https://github.com/org/repo/pull/99" in call_args[0][1]

    def test_skips_when_no_ticket(self):
        """Test that no JIRA call is made when ticket_key is absent."""
        from dispatcher.run import _post_execution_comment

        with patch("dispatcher.run.JiraClient") as mock_jira_class:
            _post_execution_comment(None, {"status": "success", "pr_url": "http://x"})
            mock_jira_class.assert_not_called()

    def test_skips_when_no_summary(self):
        """Test that no JIRA call is made when execution_summary is None."""
        from dispatcher.run import _post_execution_comment

        with patch("dispatcher.run.JiraClient") as mock_jira_class:
            _post_execution_comment("AOS-42", None)
            mock_jira_class.assert_not_called()

    def test_tolerates_jira_comment_error(self):
        """Test that a JiraCommentError is caught and does not raise."""
        from dispatcher.jira_client import JiraCommentError
        from dispatcher.run import _post_execution_comment

        with patch("dispatcher.run.JiraClient") as mock_jira_class:
            mock_jira = mock_jira_class.return_value
            mock_jira.post_comment.side_effect = JiraCommentError("network error")
            # Should not raise
            _post_execution_comment("AOS-42", {"status": "success", "pr_url": ""})


class TestHandleHistory:
    """Tests for --history / _handle_history."""

    def test_history_errors_without_ticket_or_workflow_id(self, test_db, cli_runner):
        """--history with neither --ticket nor --workflow-id should exit 1."""
        result = cli_runner.invoke(run, ["--history"])
        assert result.exit_code == 1
        assert "--history requires --ticket or --workflow-id" in result.output

    def test_history_no_workflows_found(self, test_db, cli_runner):
        """--history --ticket for a ticket with no workflows should exit 1."""
        result = cli_runner.invoke(run, ["--history", "--ticket", "AOS-999"])
        assert result.exit_code == 1
        assert "No workflows found for ticket: AOS-999" in result.output

    def test_history_unknown_workflow_id(self, test_db, cli_runner):
        """--history --workflow-id with a non-existent UUID should exit 1."""
        result = cli_runner.invoke(
            run, ["--history", "--workflow-id", "00000000-0000-0000-0000-000000000000"]
        )
        assert result.exit_code == 1
        assert "Workflow not found" in result.output

    def test_history_shows_steps(
        self, test_db, mock_jira_client, mock_generate_plan, cli_runner, memory_checkpointer
    ):
        """--history should list at least one step after a workflow run."""
        from graph.builder import build_orchestrator

        checkpointer = memory_checkpointer
        with patch("dispatcher.run.build_orchestrator") as mock_build:
            mock_build.side_effect = lambda: build_orchestrator(checkpointer=checkpointer)

            # Start workflow — pauses at await_approval
            cli_runner.invoke(run, ["--ticket", "TEST-123"])

        workflows = state_store.get_workflow_by_ticket("TEST-123")
        assert workflows, "expected at least one workflow"
        resolved_id = workflows[0]["id"]

        with patch("dispatcher.run.build_orchestrator") as mock_build:
            mock_build.side_effect = lambda: build_orchestrator(checkpointer=checkpointer)
            result = cli_runner.invoke(run, ["--history", "--ticket", "TEST-123"])

        assert result.exit_code == 0
        assert "Workflow history for TEST-123" in result.output
        assert resolved_id in result.output
        # At minimum the work_planner step should appear
        assert "work_planner" in result.output

    def test_history_by_workflow_id(
        self, test_db, mock_jira_client, mock_generate_plan, cli_runner, memory_checkpointer
    ):
        """--history --workflow-id should show the same output as --ticket."""
        from graph.builder import build_orchestrator

        checkpointer = memory_checkpointer
        with patch("dispatcher.run.build_orchestrator") as mock_build:
            mock_build.side_effect = lambda: build_orchestrator(checkpointer=checkpointer)
            cli_runner.invoke(run, ["--ticket", "TEST-123"])

        workflows = state_store.get_workflow_by_ticket("TEST-123")
        resolved_id = workflows[0]["id"]

        with patch("dispatcher.run.build_orchestrator") as mock_build:
            mock_build.side_effect = lambda: build_orchestrator(checkpointer=checkpointer)
            result = cli_runner.invoke(run, ["--history", "--workflow-id", resolved_id])

        assert result.exit_code == 0
        assert "Workflow history for TEST-123" in result.output
        assert resolved_id in result.output


def test_reject_handles_resume_error(test_db, cli_runner):
    """Test that reject path reports resume errors with existing message."""
    workflow_id = state_store.create_workflow("TEST-123", status=WorkflowStatus.PENDING_APPROVAL)

    with patch("dispatcher.run.build_orchestrator") as mock_build:
        mock_graph = Mock()
        mock_graph.invoke.side_effect = Exception("boom")
        mock_build.return_value = mock_graph

        result = cli_runner.invoke(run, ["--reject", "--workflow-id", workflow_id])

    assert result.exit_code == 1
    assert "❌ Error resuming workflow: boom" in result.output


def test_get_actor_imported_from_graph_utils():
    """Dispatcher should consume shared _get_actor from graph.utils."""
    import dispatcher.run as run_module
    from graph.utils import _get_actor as shared_get_actor

    assert run_module._get_actor is shared_get_actor


class TestHandleApproveFailedExecution:
    """Regression tests for approve path when execution fails.

    Prior to the fix, _handle_approve unconditionally set the workflow status
    to COMPLETED after graph.invoke() returned, even when execute_plan had
    already written a 'failed' execution summary. This caused failed runs to
    appear as completed in the database.
    """

    def _make_pending_workflow(self, ticket_key: str) -> str:
        return state_store.create_workflow(ticket_key, status=WorkflowStatus.PENDING_APPROVAL)

    def test_approve_marks_failed_when_execution_fails(self, test_db, cli_runner):
        """When execute_plan returns a failed summary, status must stay FAILED."""
        workflow_id = self._make_pending_workflow("TEST-123")

        failed_summary = {
            "ticket_key": "TEST-123",
            "branch": "",
            "build": "fail",
            "tests": "skipped",
            "files_changed": [],
            "commit_sha": "",
            "pr_url": "",
            "status": "failed",
            "error": "Execution summary not written by recipe",
        }

        with patch("dispatcher.run.build_orchestrator") as mock_build:
            mock_graph = Mock()
            mock_graph.invoke.return_value = {
                "workflow_id": workflow_id,
                "ticket_key": "TEST-123",
                "execution_summary": failed_summary,
            }
            mock_build.return_value = mock_graph

            with patch("dispatcher.run._post_execution_comment"):
                result = cli_runner.invoke(run, ["--approve", "--workflow-id", workflow_id])

        assert result.exit_code == 0
        workflow = state_store.get_workflow(workflow_id)
        assert workflow["status"] == WorkflowStatus.FAILED, (
            "Expected FAILED but got %s — approve must not unconditionally set COMPLETED"
            % workflow["status"]
        )
        assert "❌ Workflow failed" in result.output

    def test_approve_marks_completed_when_execution_succeeds(self, test_db, cli_runner):
        """When execute_plan returns a success summary, status must be COMPLETED."""
        workflow_id = self._make_pending_workflow("TEST-123")

        success_summary = {
            "ticket_key": "TEST-123",
            "branch": "feature/TEST-123+do-the-thing",
            "build": "pass",
            "tests": "pass",
            "files_changed": ["src/foo.py"],
            "commit_sha": "abc123",
            "pr_url": "https://github.com/org/repo/pull/1",
            "status": "success",
        }

        with patch("dispatcher.run.build_orchestrator") as mock_build:
            mock_graph = Mock()
            mock_graph.invoke.return_value = {
                "workflow_id": workflow_id,
                "ticket_key": "TEST-123",
                "execution_summary": success_summary,
            }
            mock_build.return_value = mock_graph

            with patch("dispatcher.run._post_execution_comment"):
                result = cli_runner.invoke(run, ["--approve", "--workflow-id", workflow_id])

        assert result.exit_code == 0
        workflow = state_store.get_workflow(workflow_id)
        assert workflow["status"] == WorkflowStatus.COMPLETED
        assert "🎉 Workflow completed successfully" in result.output

    def test_approve_marks_failed_when_summary_absent(self, test_db, cli_runner):
        """When execution_summary is missing entirely, status must be FAILED."""
        workflow_id = self._make_pending_workflow("TEST-123")

        with patch("dispatcher.run.build_orchestrator") as mock_build:
            mock_graph = Mock()
            mock_graph.invoke.return_value = {
                "workflow_id": workflow_id,
                "ticket_key": "TEST-123",
                # no execution_summary key
            }
            mock_build.return_value = mock_graph

            with patch("dispatcher.run._post_execution_comment"):
                result = cli_runner.invoke(run, ["--approve", "--workflow-id", workflow_id])

        assert result.exit_code == 0
        workflow = state_store.get_workflow(workflow_id)
        assert workflow["status"] == WorkflowStatus.FAILED

    def test_approve_marks_completed_for_partial_status(self, test_db, cli_runner):
        """A 'partial' execution (build pass, tests fail) should be COMPLETED."""
        workflow_id = self._make_pending_workflow("TEST-123")

        partial_summary = {
            "ticket_key": "TEST-123",
            "branch": "feature/TEST-123+do-the-thing",
            "build": "pass",
            "tests": "fail",
            "files_changed": ["src/foo.py"],
            "commit_sha": "abc123",
            "pr_url": "",
            "status": "partial",
        }

        with patch("dispatcher.run.build_orchestrator") as mock_build:
            mock_graph = Mock()
            mock_graph.invoke.return_value = {
                "workflow_id": workflow_id,
                "ticket_key": "TEST-123",
                "execution_summary": partial_summary,
            }
            mock_build.return_value = mock_graph

            with patch("dispatcher.run._post_execution_comment"):
                result = cli_runner.invoke(run, ["--approve", "--workflow-id", workflow_id])

        assert result.exit_code == 0
        workflow = state_store.get_workflow(workflow_id)
        assert workflow["status"] == WorkflowStatus.COMPLETED


# ---------------------------------------------------------------------------
# --clarify tests
# ---------------------------------------------------------------------------


class TestHandleClarify:
    """Tests for the --clarify CLI handler."""

    def _make_pending_clarification_workflow(self, ticket_key: str) -> str:
        """Create a workflow stuck at PENDING_WORKPLAN_CLARIFICATION with questions."""
        workflow_id = state_store.create_workflow(
            ticket_key, status=WorkflowStatus.PENDING_WORKPLAN_CLARIFICATION
        )
        state_store.update_work_plan(
            workflow_id,
            {
                "schema_version": "1.0",
                "ticket_key": ticket_key,
                "summary": "Test",
                "approach": "test",
                "tasks": [{"id": 1, "description": "Do it", "files_likely_affected": []}],
                "risks": ["Risk A"],
                "questions_for_reviewer": ["What DB?", "Which API?"],
                "status": "concerns",
            },
        )
        return workflow_id

    def test_clarify_requires_ticket_or_workflow_id(self, test_db, cli_runner):
        result = cli_runner.invoke(run, ["--clarify"])
        assert result.exit_code == 1
        assert "--clarify requires --ticket or --workflow-id" in result.output

    def test_clarify_no_pending_workflow(self, test_db, cli_runner):
        result = cli_runner.invoke(run, ["--clarify", "--ticket", "TEST-123"])
        assert result.exit_code == 1
        assert "No workflow pending clarification" in result.output

    def test_clarify_wrong_status_fails(self, test_db, cli_runner):
        """--clarify on a PENDING_APPROVAL workflow should exit with error."""
        workflow_id = state_store.create_workflow(
            "TEST-123", status=WorkflowStatus.PENDING_APPROVAL
        )

        result = cli_runner.invoke(run, ["--clarify", "--workflow-id", workflow_id])
        assert result.exit_code == 1
        assert "not pending clarification" in result.output

    def test_clarify_workflow_not_found(self, test_db, cli_runner):
        result = cli_runner.invoke(
            run, ["--clarify", "--workflow-id", "00000000-0000-1000-8000-000000000000"]
        )
        assert result.exit_code == 1
        assert "Workflow not found" in result.output

    def test_clarify_resumes_graph_with_answers(self, test_db, cli_runner):
        """--clarify feeds answers to the graph via Command(resume=...)."""
        workflow_id = self._make_pending_clarification_workflow("TEST-123")

        with patch("dispatcher.run.build_orchestrator") as mock_build:
            mock_graph = Mock()
            mock_graph.invoke.return_value = {
                "workflow_id": workflow_id,
                "ticket_key": "TEST-123",
            }
            mock_build.return_value = mock_graph

            # Simulate user typing answers for 2 questions
            result = cli_runner.invoke(
                run,
                ["--clarify", "--workflow-id", workflow_id],
                input="SQLite\nREST\n",
            )

        assert result.exit_code == 0
        # Verify graph was resumed with a Command containing answers
        from langgraph.types import Command

        call_args = mock_graph.invoke.call_args
        command = call_args[0][0]
        assert isinstance(command, Command)
        answers = command.resume["answers"]
        assert len(answers) == 2
        assert answers[0]["question"] == "What DB?"
        assert answers[0]["answer"] == "SQLite"
        assert answers[1]["question"] == "Which API?"
        assert answers[1]["answer"] == "REST"

    def test_clarify_by_ticket_key(self, test_db, cli_runner):
        """--clarify --ticket resolves the pending workflow automatically."""
        self._make_pending_clarification_workflow("TEST-123")

        with patch("dispatcher.run.build_orchestrator") as mock_build:
            mock_graph = Mock()
            mock_graph.invoke.return_value = {"workflow_id": "any", "ticket_key": "TEST-123"}
            mock_build.return_value = mock_graph

            result = cli_runner.invoke(
                run,
                ["--clarify", "--ticket", "TEST-123"],
                input="SQLite\nREST\n",
            )

        assert result.exit_code == 0
        mock_graph.invoke.assert_called_once()

    def test_clarify_shows_approval_instructions_after_success(self, test_db, cli_runner):
        """After the clarification loop posts a new plan, show approval instructions."""
        workflow_id = self._make_pending_clarification_workflow("TEST-123")

        def _invoke_and_transition(command, config):
            # Simulate the graph transitioning to PENDING_APPROVAL during invocation
            state_store.update_status(
                workflow_id, WorkflowStatus.PENDING_APPROVAL, actor="test", reason="plan done"
            )
            return {"workflow_id": workflow_id, "ticket_key": "TEST-123"}

        with patch("dispatcher.run.build_orchestrator") as mock_build:
            mock_graph = Mock()
            mock_graph.invoke.side_effect = _invoke_and_transition
            mock_build.return_value = mock_graph

            result = cli_runner.invoke(
                run,
                ["--clarify", "--workflow-id", workflow_id],
                input="SQLite\nREST\n",
            )

        assert result.exit_code == 0
        assert "--approve" in result.output
