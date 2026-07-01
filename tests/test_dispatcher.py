"""Integration tests for dispatcher/run.py (LangGraph-based orchestrator)"""

import os
import tempfile
from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner
from langgraph.checkpoint.memory import MemorySaver

from dispatcher.jira_client import JiraConfigurationError, JiraTicket, JiraTicketNotFoundError
from dispatcher.run import run
from orchestrator.workflow_service import build_local_workflow_service
from state import workflow_repository as state_store
from state.workflow_status import WorkflowStatus


def _make_test_service(graph=None, graph_factory=None):
    """Build a LocalWorkflowService for tests, wired to the given graph.

    After AOS-139 the CLI no longer patches ``build_orchestrator``; instead
    tests inject a pre-built ``WorkflowService`` via ``cli_runner.invoke(run,
    args, obj=service)`` and pass either a concrete ``graph`` or a
    ``graph_factory`` callable.
    """
    if graph_factory is None:
        if graph is None:
            raise ValueError("Provide graph or graph_factory")
        graph_factory = lambda: graph  # noqa: E731
    return build_local_workflow_service(graph_factory=graph_factory)


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
    with (
        patch("orchestrator.work_planner.nodes.fetch_ticket.JiraClient") as mock_fetch,
        patch("orchestrator.work_planner.nodes.post_to_jira.JiraClient") as mock_post,
    ):
        mock_instance = Mock()
        mock_instance.get_ticket.return_value = JiraTicket(
            key="TEST-123",
            title="Test Ticket",
            description="Test description",
            labels=["test", "automation"],
            status="To Do",
        )
        # Mock post_comment to avoid real JIRA calls
        mock_instance.post_comment.return_value = None
        mock_fetch.return_value = mock_instance
        mock_post.return_value = mock_instance
        yield mock_fetch


@pytest.fixture
def cli_runner():
    """Create a Click CLI test runner."""
    return CliRunner()


@pytest.fixture
def mock_generate_plan():
    """Mock generate_plan node to return a schema-valid WorkPlan so tests reach await_approval."""
    with patch("orchestrator.work_planner.builder.generate_plan") as mock:
        mock.return_value = {
            "work_plan_data": {
                "schema_version": "1.0",
                "ticket_key": "TEST-123",
                "summary": "Test plan",
                "approach": "test approach",
                "tasks": [{"id": 1, "description": "Do the thing", "files_likely_affected": []}],
                "concerns": [],
                "status": "pass",
            }
        }
        yield mock


@pytest.fixture
def mock_repo_setup():
    """Mock repo setup nodes (resolve_repo, fetch_github_token, clone_repo) to bypass actual git/network operations."""
    import os
    import shutil

    patches = [
        patch("orchestrator.shared.repo_setup.nodes.resolve_repo.resolve_repository_url"),
        patch("orchestrator.shared.repo_setup.nodes.fetch_github_token.fetch_token_for_repo"),
        patch("orchestrator.shared.repo_setup.nodes.clone_repo.clone_repository"),
    ]

    started = [p.start() for p in patches]
    started[0].return_value = "git@github.com:test/repo.git"  # resolve_repository_url
    started[1].return_value = "ghs_test_token"  # fetch_token_for_repo

    import tempfile

    mock_workdir = tempfile.mkdtemp(prefix="test-plan-")
    started[2].return_value = mock_workdir  # clone_repository

    yield started

    for p in patches:
        p.stop()

    # Cleanup temp directory
    if os.path.exists(mock_workdir):
        shutil.rmtree(mock_workdir, ignore_errors=True)


def test_run_creates_workflow(
    test_db, mock_jira_client, mock_generate_plan, mock_repo_setup, cli_runner, memory_checkpointer
):
    """Test that running the dispatcher creates a workflow record and pauses for approval."""
    from orchestrator.builder import build_orchestrator

    service = _make_test_service(
        graph_factory=lambda: build_orchestrator(checkpointer=memory_checkpointer)
    )
    result = cli_runner.invoke(run, ["--ticket", "TEST-123"], obj=service)

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

    from orchestrator.builder import build_orchestrator

    service = _make_test_service(
        graph_factory=lambda: build_orchestrator(checkpointer=memory_checkpointer)
    )
    result = cli_runner.invoke(run, ["--ticket", "TEST-123"], obj=service)

    assert result.exit_code == 1
    assert "❌ Workflow already in progress" in result.output
    assert workflow_id in result.output
    assert "Cannot start a new workflow while one is active" in result.output


def test_run_allows_rerun_after_completion(
    test_db, mock_jira_client, mock_generate_plan, mock_repo_setup, cli_runner, memory_checkpointer
):
    """Test that completed workflows can be re-run (pauses at approval gate)."""
    # Create a completed workflow
    state_store.create_workflow("TEST-123", status=WorkflowStatus.COMPLETED)

    from orchestrator.builder import build_orchestrator

    service = _make_test_service(
        graph_factory=lambda: build_orchestrator(checkpointer=memory_checkpointer)
    )
    result = cli_runner.invoke(run, ["--ticket", "TEST-123"], obj=service)

    assert result.exit_code == 0
    assert "⚠️  Warning: 1 completed workflow(s) exist for TEST-123" in result.output
    # New run pauses at approval gate
    assert "⏸️  WorkPlan is ready for review." in result.output

    # Verify new workflow was created (2 total)
    workflows = state_store.get_workflow_by_ticket("TEST-123")
    assert len(workflows) == 2


def test_run_handles_ticket_not_found(test_db, cli_runner, memory_checkpointer):
    """Test graceful handling of non-existent tickets."""
    with patch("orchestrator.work_planner.nodes.fetch_ticket.JiraClient") as mock:
        mock_instance = Mock()
        mock_instance.get_ticket.side_effect = JiraTicketNotFoundError("Ticket not found")
        mock.return_value = mock_instance

        from orchestrator.builder import build_orchestrator

        service = _make_test_service(
            graph_factory=lambda: build_orchestrator(checkpointer=memory_checkpointer)
        )
        result = cli_runner.invoke(run, ["--ticket", "NOTFOUND-999"], obj=service)

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
    test_db, mock_jira_client, mock_generate_plan, mock_repo_setup, cli_runner, memory_checkpointer
):
    """Test that all stage transitions are logged to audit log."""
    from orchestrator.builder import build_orchestrator

    service = _make_test_service(
        graph_factory=lambda: build_orchestrator(checkpointer=memory_checkpointer)
    )
    result = cli_runner.invoke(run, ["--ticket", "TEST-123"], obj=service)

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
    with patch("orchestrator.work_planner.nodes.fetch_ticket.JiraClient") as mock:
        mock.side_effect = Exception("Unexpected error")

        result = cli_runner.invoke(run, ["--ticket", "TEST-123"])

        assert result.exit_code == 1
        assert "❌ Unhandled error" in result.output


def test_run_validates_ticket_format(test_db, cli_runner):
    """Test that invalid ticket formats are rejected."""
    result = cli_runner.invoke(run, ["--ticket", "invalid"])

    assert result.exit_code == 1
    assert "❌ Invalid ticket format" in result.output


def test_run_handles_jira_config_error(test_db, cli_runner, memory_checkpointer):
    """Test handling of JIRA configuration errors."""
    with patch("orchestrator.work_planner.nodes.fetch_ticket.JiraClient") as mock:
        mock.side_effect = JiraConfigurationError("Missing JIRA_URL")

        from orchestrator.builder import build_orchestrator

        service = _make_test_service(
            graph_factory=lambda: build_orchestrator(checkpointer=memory_checkpointer)
        )
        result = cli_runner.invoke(run, ["--ticket", "TEST-123"], obj=service)

    assert result.exit_code == 1
    assert "❌ JIRA configuration error" in result.output
    assert "JIRA_URL" in result.output


def test_check_duplicate_node(test_db):
    """Test duplicate detection via the check_duplicate graph node."""
    from orchestrator.work_planner.nodes.check_duplicate import check_duplicate

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
    from orchestrator.work_planner.nodes.create_workflow_record import create_workflow_record

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
    with patch("orchestrator.work_planner.nodes.fetch_ticket.JiraClient") as mock:
        mock_instance = Mock()
        mock_instance.get_ticket.side_effect = KeyboardInterrupt()
        mock.return_value = mock_instance

        from orchestrator.builder import build_orchestrator

        service = _make_test_service(
            graph_factory=lambda: build_orchestrator(checkpointer=memory_checkpointer)
        )
        result = cli_runner.invoke(run, ["--ticket", "TEST-123"], obj=service)

    assert result.exit_code == 130  # Standard SIGINT exit code
    assert "⚠️  Workflow interrupted by user" in result.output


def test_logs_reads_from_xdg_default_base_dir(test_db, cli_runner, monkeypatch, tmp_path):
    """--logs resolves from XDG base path when LOGS_DIR is not set."""
    from orchestrator.paths import workflow_logs_dir

    monkeypatch.delenv("LOGS_DIR", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    workflow_id = state_store.create_workflow("AOS-119", status=WorkflowStatus.COMPLETED)

    workflow_log = workflow_logs_dir(workflow_id) / "workflow.log"
    workflow_log.write_text("workflow log from xdg default", encoding="utf-8")

    result = cli_runner.invoke(run, ["--ticket", "AOS-119", "--logs"])

    assert result.exit_code == 0
    assert "workflow log from xdg default" in result.output


def test_logs_uses_explicit_logs_dir_override(test_db, cli_runner, monkeypatch, tmp_path):
    """--logs honors explicit LOGS_DIR override when provided."""
    from orchestrator.paths import workflow_logs_dir

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    monkeypatch.setenv("LOGS_DIR", str(tmp_path / "logs-override"))
    workflow_id = state_store.create_workflow("AOS-119", status=WorkflowStatus.COMPLETED)

    workflow_log = workflow_logs_dir(workflow_id) / "workflow.log"
    workflow_log.write_text("workflow log from override", encoding="utf-8")

    result = cli_runner.invoke(run, ["--ticket", "AOS-119", "--logs"])

    assert result.exit_code == 0
    assert "workflow log from override" in result.output


class TestPostExecutionComment:
    """Tests for _post_execution_comment helper."""

    def test_posts_comment_with_pr_url(self, test_db):
        """Test that pr_url from the execution summary is posted to JIRA and echoed."""
        from dispatcher.commands.common import _post_execution_comment

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

        with patch("dispatcher.commands.common.JiraClient") as mock_jira_class:
            mock_jira = mock_jira_class.return_value
            _post_execution_comment("AOS-42", execution_summary)
            mock_jira.post_comment.assert_called_once()
            call_args = mock_jira.post_comment.call_args
            assert call_args[0][0] == "AOS-42"
            assert "https://github.com/org/repo/pull/99" in call_args[0][1]

    def test_skips_when_no_ticket(self):
        """Test that no JIRA call is made when ticket_key is absent."""
        from dispatcher.commands.common import _post_execution_comment

        with patch("dispatcher.commands.common.JiraClient") as mock_jira_class:
            _post_execution_comment(None, {"status": "success", "pr_url": "http://x"})
            mock_jira_class.assert_not_called()

    def test_skips_when_no_summary(self):
        """Test that no JIRA call is made when execution_summary is None."""
        from dispatcher.commands.common import _post_execution_comment

        with patch("dispatcher.commands.common.JiraClient") as mock_jira_class:
            _post_execution_comment("AOS-42", None)
            mock_jira_class.assert_not_called()

    def test_tolerates_jira_comment_error(self):
        """Test that a JiraCommentError is caught and does not raise."""
        from dispatcher.commands.common import _post_execution_comment
        from dispatcher.jira_client import JiraCommentError

        with patch("dispatcher.commands.common.JiraClient") as mock_jira_class:
            mock_jira = mock_jira_class.return_value
            mock_jira.post_comment.side_effect = JiraCommentError("network error")
            # Should not raise
            _post_execution_comment("AOS-42", {"status": "success", "pr_url": ""})

    def test_accepts_injected_comment_poster(self):
        """FakeCommentPoster can be injected — no JiraClient constructed."""
        from dispatcher.commands.common import _post_execution_comment

        class FakeCommentPoster:
            def __init__(self):
                self.calls = []

            def post_comment(self, ticket_key: str, comment: str) -> bool:
                self.calls.append((ticket_key, comment))
                return True

        fake = FakeCommentPoster()
        execution_summary = {
            "ticket_key": "AOS-100",
            "branch": "feature/AOS-100",
            "build": "pass",
            "tests": "pass",
            "files_changed": [],
            "commit_sha": "abc123",
            "pr_url": "https://github.com/org/repo/pull/1",
            "status": "success",
        }
        with patch("dispatcher.commands.common.JiraClient") as mock_jira_class:
            _post_execution_comment("AOS-100", execution_summary, comment_poster=fake)
            mock_jira_class.assert_not_called()

        assert len(fake.calls) == 1
        assert fake.calls[0][0] == "AOS-100"

    def test_abstract_ticket_not_found_caught_by_run_workflow(
        self, test_db, cli_runner, memory_checkpointer
    ):
        """TicketNotFoundError (abstract) raised directly is caught by run_workflow handler."""
        from dispatcher.exceptions import TicketNotFoundError

        with patch("orchestrator.work_planner.nodes.fetch_ticket.JiraClient") as mock:
            mock_instance = Mock()
            mock_instance.get_ticket.side_effect = TicketNotFoundError("abstract not found")
            mock.return_value = mock_instance

            from orchestrator.builder import build_orchestrator

            service = _make_test_service(
                graph_factory=lambda: build_orchestrator(checkpointer=memory_checkpointer)
            )
            result = cli_runner.invoke(run, ["--ticket", "ABSTRACT-1"], obj=service)

        assert result.exit_code == 1
        assert "❌ Ticket not found" in result.output


class TestFetchTicketNode:
    """Tests for graph/work_planner/nodes/fetch_ticket.py dependency injection."""

    def test_uses_injected_ticket_source(self):
        """fetch_ticket uses an injected TicketSource stub — no JiraClient constructed."""
        from dispatcher.jira_client import JiraTicket
        from orchestrator.work_planner.nodes.fetch_ticket import fetch_ticket

        class FakeTicketSource:
            def get_ticket(self, ticket_key: str) -> JiraTicket:
                return JiraTicket(
                    key=ticket_key,
                    title="Fake Ticket",
                    description="Injected description",
                    labels=["injected"],
                    status="In Progress",
                )

        result = fetch_ticket(
            {"ticket_key": "FAKE-1"},
            ticket_source=FakeTicketSource(),
        )
        assert result["ticket"]["key"] == "FAKE-1"
        assert result["ticket"]["title"] == "Fake Ticket"
        assert result["ticket"]["labels"] == ["injected"]

    def test_default_uses_jira_client(self):
        """fetch_ticket constructs a JiraClient when no ticket_source is provided."""
        from orchestrator.work_planner.nodes.fetch_ticket import fetch_ticket

        with patch("orchestrator.work_planner.nodes.fetch_ticket.JiraClient") as mock_cls:
            mock_instance = Mock()
            from dispatcher.jira_client import JiraTicket

            mock_instance.get_ticket.return_value = JiraTicket(
                key="TEST-1", title="T", description="", labels=[], status="To Do"
            )
            mock_cls.return_value = mock_instance

            result = fetch_ticket({"ticket_key": "TEST-1"})

        mock_cls.assert_called_once()
        assert result["ticket"]["key"] == "TEST-1"


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
        self,
        test_db,
        mock_jira_client,
        mock_generate_plan,
        mock_repo_setup,
        cli_runner,
        memory_checkpointer,
    ):
        """--history should list at least one step after a workflow run."""
        from orchestrator.builder import build_orchestrator

        checkpointer = memory_checkpointer
        service = _make_test_service(
            graph_factory=lambda: build_orchestrator(checkpointer=checkpointer)
        )

        # Start workflow — pauses at await_approval
        cli_runner.invoke(run, ["--ticket", "TEST-123"], obj=service)

        workflows = state_store.get_workflow_by_ticket("TEST-123")
        assert workflows, "expected at least one workflow"
        resolved_id = workflows[0]["id"]

        history_service = _make_test_service(
            graph_factory=lambda: build_orchestrator(checkpointer=checkpointer)
        )
        result = cli_runner.invoke(run, ["--history", "--ticket", "TEST-123"], obj=history_service)

        assert result.exit_code == 0
        assert "Workflow history for TEST-123" in result.output
        assert resolved_id in result.output
        # At minimum the work_planner step should appear
        assert "work_planner" in result.output

    def test_history_by_workflow_id(
        self,
        test_db,
        mock_jira_client,
        mock_generate_plan,
        mock_repo_setup,
        cli_runner,
        memory_checkpointer,
    ):
        """--history --workflow-id should show the same output as --ticket."""
        from orchestrator.builder import build_orchestrator

        checkpointer = memory_checkpointer
        service = _make_test_service(
            graph_factory=lambda: build_orchestrator(checkpointer=checkpointer)
        )
        cli_runner.invoke(run, ["--ticket", "TEST-123"], obj=service)

        workflows = state_store.get_workflow_by_ticket("TEST-123")
        resolved_id = workflows[0]["id"]

        history_service = _make_test_service(
            graph_factory=lambda: build_orchestrator(checkpointer=checkpointer)
        )
        result = cli_runner.invoke(
            run, ["--history", "--workflow-id", resolved_id], obj=history_service
        )

        assert result.exit_code == 0
        assert "Workflow history for TEST-123" in result.output
        assert resolved_id in result.output

    def test_history_show_clarifications_flag(self, test_db, cli_runner):
        """--history --show-clarifications should print clarification Q&A when present."""
        workflow_id = state_store.create_workflow("TEST-123", status=WorkflowStatus.COMPLETED)
        state_store.update_clarification_history(
            workflow_id,
            {
                "round": 1,
                "concerns": ["What DB?", "Risk A"],
                "answers": [{"concern": "What DB?", "answer": "SQLite"}],
            },
            actor="developer",
        )

        mock_graph = Mock()
        mock_graph.get_state_history.return_value = []
        service = _make_test_service(graph=mock_graph)
        result = cli_runner.invoke(
            run,
            ["--history", "--workflow-id", workflow_id, "--show-clarifications"],
            obj=service,
        )

        assert result.exit_code == 0
        assert "Clarification Q&A History" in result.output
        assert "Round 1" in result.output
        assert "What DB?" in result.output
        assert "SQLite" in result.output
        assert "Risk A" in result.output

    def test_history_show_clarifications_empty(self, test_db, cli_runner):
        """--history --show-clarifications on a workflow with no history should say so."""
        workflow_id = state_store.create_workflow("TEST-123", status=WorkflowStatus.COMPLETED)

        mock_graph = Mock()
        mock_graph.get_state_history.return_value = []
        service = _make_test_service(graph=mock_graph)
        result = cli_runner.invoke(
            run,
            ["--history", "--workflow-id", workflow_id, "--show-clarifications"],
            obj=service,
        )

        assert result.exit_code == 0
        assert "No clarification history found." in result.output


def test_reject_handles_resume_error(test_db, cli_runner):
    """Test that reject path reports resume errors with existing message."""
    workflow_id = state_store.create_workflow("TEST-123", status=WorkflowStatus.PENDING_APPROVAL)

    mock_graph = Mock()
    mock_graph.stream.side_effect = Exception("boom")
    service = _make_test_service(graph=mock_graph)

    result = cli_runner.invoke(run, ["--reject", "--workflow-id", workflow_id], obj=service)

    assert result.exit_code == 1
    assert "❌ Error resuming workflow: boom" in result.output


def test_get_actor_imported_from_graph_utils():
    """Dispatcher should consume shared _get_actor from orchestrator.utils."""
    import dispatcher.commands.common as run_module
    from orchestrator.utils import _get_actor as shared_get_actor

    assert run_module._get_actor is shared_get_actor


def test_dispatcher_commands_have_no_direct_repo_or_builder_imports():
    """AOS-139: dispatcher/commands/ must route everything through WorkflowService.

    The handlers must not directly import the SQLite repository, the LangGraph
    builder, the retry helpers, or LangGraph itself; doing so would bypass the
    service boundary that lets us swap the backend (e.g. for the MCP server).
    """
    import pathlib

    forbidden = [
        "state.sqlite_workflow_repository",
        "state.workflow_repository",
        "state.observable_sqlite_saver",
        "orchestrator.builder",
        "orchestrator.retry",
        "langgraph",
    ]
    cmds = pathlib.Path(__file__).parent.parent / "dispatcher" / "commands"
    assert cmds.is_dir(), f"expected dispatcher/commands/ at {cmds}"

    offenders: list[str] = []
    for py_file in sorted(cmds.glob("*.py")):
        if py_file.name == "__init__.py":
            continue
        src = py_file.read_text(encoding="utf-8")
        for token in forbidden:
            if token in src:
                offenders.append(f"{py_file.name}: {token}")

    assert not offenders, (
        "dispatcher/commands/ must go through WorkflowService — direct imports found:\n  - "
        + "\n  - ".join(offenders)
    )


class TestHandleApproveFailedExecution:
    """Regression tests for approve path when execution fails.

    Prior to the fix, _handle_approve unconditionally set the workflow status
    to COMPLETED after graph.invoke() returned, even when generate_code had
    already written a 'failed' execution summary. This caused failed runs to
    appear as completed in the database.
    """

    def _make_pending_workflow(self, ticket_key: str) -> str:
        return state_store.create_workflow(ticket_key, status=WorkflowStatus.PENDING_APPROVAL)

    def test_approve_marks_failed_when_execution_fails(self, test_db, cli_runner):
        """When generate_code returns a failed summary, status must stay FAILED."""
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

        mock_graph = Mock()
        mock_graph.stream.return_value = iter([])
        mock_graph.get_state.return_value = Mock(
            values={
                "workflow_id": workflow_id,
                "ticket_key": "TEST-123",
                "execution_summary": failed_summary,
            }
        )
        service = _make_test_service(graph=mock_graph)

        with patch("dispatcher.commands.common._post_execution_comment"):
            result = cli_runner.invoke(
                run, ["--approve-plan", "--workflow-id", workflow_id], obj=service
            )

        assert result.exit_code == 0
        workflow = state_store.get_workflow(workflow_id)
        assert workflow["status"] == WorkflowStatus.FAILED, (
            "Expected FAILED but got %s — approve must not unconditionally set COMPLETED"
            % workflow["status"]
        )
        assert "❌ Workflow failed" in result.output

    def test_approve_marks_pending_pr_approval_when_execution_succeeds(self, test_db, cli_runner):
        """When generate_code returns a success summary, status must be PENDING_PR_APPROVAL."""
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

        mock_graph = Mock()
        mock_graph.stream.return_value = iter([])
        mock_graph.get_state.return_value = Mock(
            values={
                "workflow_id": workflow_id,
                "ticket_key": "TEST-123",
                "execution_summary": success_summary,
            }
        )
        service = _make_test_service(graph=mock_graph)

        with patch("dispatcher.commands.common._post_execution_comment"):
            result = cli_runner.invoke(
                run, ["--approve-plan", "--workflow-id", workflow_id], obj=service
            )

        assert result.exit_code == 0
        workflow = state_store.get_workflow(workflow_id)
        assert workflow["status"] == WorkflowStatus.PENDING_PR_APPROVAL
        assert "awaiting PR approval" in result.output

    def test_approve_marks_failed_when_summary_absent(self, test_db, cli_runner):
        """When execution_summary is missing entirely, status must be FAILED."""
        workflow_id = self._make_pending_workflow("TEST-123")

        mock_graph = Mock()
        mock_graph.stream.return_value = iter([])
        mock_graph.get_state.return_value = Mock(
            values={
                "workflow_id": workflow_id,
                "ticket_key": "TEST-123",
                # no execution_summary key
            }
        )
        service = _make_test_service(graph=mock_graph)

        with patch("dispatcher.commands.common._post_execution_comment"):
            result = cli_runner.invoke(
                run, ["--approve-plan", "--workflow-id", workflow_id], obj=service
            )

        assert result.exit_code == 0
        workflow = state_store.get_workflow(workflow_id)
        assert workflow["status"] == WorkflowStatus.FAILED

    def test_approve_marks_pending_pr_approval_for_partial_status(self, test_db, cli_runner):
        """A 'partial' execution (build pass, tests fail) should be PENDING_PR_APPROVAL."""
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

        mock_graph = Mock()
        mock_graph.stream.return_value = iter([])
        mock_graph.get_state.return_value = Mock(
            values={
                "workflow_id": workflow_id,
                "ticket_key": "TEST-123",
                "execution_summary": partial_summary,
            }
        )
        service = _make_test_service(graph=mock_graph)

        with patch("dispatcher.commands.common._post_execution_comment"):
            result = cli_runner.invoke(
                run, ["--approve-plan", "--workflow-id", workflow_id], obj=service
            )

        assert result.exit_code == 0
        workflow = state_store.get_workflow(workflow_id)
        assert workflow["status"] == WorkflowStatus.PENDING_PR_APPROVAL


# ---------------------------------------------------------------------------
# --clarify tests
# ---------------------------------------------------------------------------


class TestHandleClarify:
    """Tests for the --clarify CLI handler."""

    def _make_pending_clarification_workflow(self, ticket_key: str) -> str:
        """Create a workflow stuck at PENDING_WORKPLAN_CLARIFICATION with concerns."""
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
                "concerns": ["Risk A", "What DB?", "Which API?"],
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

        def fake_editor(tmp_path):
            # Simulate user editing the concerns file with answers
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(
                    "- Risk A\nA: proceed with caution\n\n"
                    "- What DB?\nA: SQLite\n\n"
                    "- Which API?\nA: REST\n"
                )

        mock_graph = Mock()
        mock_graph.stream.return_value = iter([])
        mock_graph.get_state.return_value = Mock(
            values={
                "workflow_id": workflow_id,
                "ticket_key": "TEST-123",
            }
        )
        service = _make_test_service(graph=mock_graph)

        with patch("subprocess.run", side_effect=lambda cmd, **kwargs: fake_editor(cmd[1])):
            result = cli_runner.invoke(
                run,
                ["--clarify", "--workflow-id", workflow_id],
                obj=service,
            )

        assert result.exit_code == 0
        # Verify graph was resumed with a Command containing answers
        from langgraph.types import Command

        call_args = mock_graph.stream.call_args
        command = call_args[0][0]
        assert isinstance(command, Command)
        answers = command.resume["answers"]
        assert len(answers) == 3
        assert answers[0]["concern"] == "Risk A"
        assert answers[0]["answer"] == "proceed with caution"
        assert answers[1]["concern"] == "What DB?"
        assert answers[1]["answer"] == "SQLite"
        assert answers[2]["concern"] == "Which API?"
        assert answers[2]["answer"] == "REST"

    def test_clarify_by_ticket_key(self, test_db, cli_runner):
        """--clarify --ticket resolves the pending workflow automatically."""
        self._make_pending_clarification_workflow("TEST-123")

        def fake_editor(tmp_path):
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write("- Risk A\nA: ok\n")

        mock_graph = Mock()
        mock_graph.stream.return_value = iter([])
        mock_graph.get_state.return_value = Mock(
            values={"workflow_id": "any", "ticket_key": "TEST-123"}
        )
        service = _make_test_service(graph=mock_graph)

        with patch("subprocess.run", side_effect=lambda cmd, **kwargs: fake_editor(cmd[1])):
            result = cli_runner.invoke(
                run,
                ["--clarify", "--ticket", "TEST-123"],
                obj=service,
            )

        assert result.exit_code == 0
        mock_graph.stream.assert_called_once()

    def test_clarify_shows_approval_instructions_after_success(self, test_db, cli_runner):
        """After the clarification loop posts a new plan, show approval instructions."""
        workflow_id = self._make_pending_clarification_workflow("TEST-123")

        def _stream_and_transition(*_args, **_kwargs):
            # Simulate the graph transitioning to PENDING_APPROVAL during invocation
            state_store.update_status(
                workflow_id, WorkflowStatus.PENDING_APPROVAL, actor="test", reason="plan done"
            )
            return iter([])

        def fake_editor(tmp_path):
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write("- Risk A\nA: ok\n")

        mock_graph = Mock()
        mock_graph.stream.side_effect = _stream_and_transition
        mock_graph.get_state.return_value = Mock(
            values={"workflow_id": workflow_id, "ticket_key": "TEST-123"}
        )
        service = _make_test_service(graph=mock_graph)

        with patch("subprocess.run", side_effect=lambda cmd, **kwargs: fake_editor(cmd[1])):
            result = cli_runner.invoke(
                run,
                ["--clarify", "--workflow-id", workflow_id],
                obj=service,
            )

        assert result.exit_code == 0
        assert "--approve-plan" in result.output


# ---------------------------------------------------------------------------
# --approve-pr / --comment-pr / --reject-pr tests
# ---------------------------------------------------------------------------


class TestHandleApprovePr:
    """Tests for the --approve-pr CLI handler."""

    def _make_pending_pr_approval_workflow(self, ticket_key: str) -> str:
        return state_store.create_workflow(ticket_key, status=WorkflowStatus.PENDING_PR_APPROVAL)

    def test_approve_pr_requires_ticket_or_workflow_id(self, test_db, cli_runner):
        result = cli_runner.invoke(run, ["--approve-pr"])
        assert result.exit_code == 1
        assert "--approve-pr requires --ticket or --workflow-id" in result.output

    def test_approve_pr_no_pending_workflow(self, test_db, cli_runner):
        result = cli_runner.invoke(run, ["--approve-pr", "--ticket", "TEST-123"])
        assert result.exit_code == 1
        assert "No pending PR approval workflow found" in result.output

    def test_approve_pr_wrong_status_fails(self, test_db, cli_runner):
        workflow_id = state_store.create_workflow(
            "TEST-123", status=WorkflowStatus.PENDING_APPROVAL
        )
        result = cli_runner.invoke(run, ["--approve-pr", "--workflow-id", workflow_id])
        assert result.exit_code == 1
        assert "not pending PR approval" in result.output

    def test_approve_pr_completes_workflow(self, test_db, cli_runner):
        workflow_id = self._make_pending_pr_approval_workflow("TEST-123")

        mock_graph = Mock()
        mock_graph.stream.return_value = iter([])
        mock_graph.get_state.return_value = Mock(
            values={
                "workflow_id": workflow_id,
                "ticket_key": "TEST-123",
            }
        )
        service = _make_test_service(graph=mock_graph)

        result = cli_runner.invoke(run, ["--approve-pr", "--workflow-id", workflow_id], obj=service)

        assert result.exit_code == 0
        workflow = state_store.get_workflow(workflow_id)
        assert workflow["status"] == WorkflowStatus.COMPLETED
        assert "PR approved" in result.output


class TestHandleCommentPr:
    """Tests for the --comment-pr CLI handler."""

    def _make_pending_pr_approval_workflow(self, ticket_key: str) -> str:
        return state_store.create_workflow(ticket_key, status=WorkflowStatus.PENDING_PR_APPROVAL)

    def test_comment_pr_requires_ticket_or_workflow_id(self, test_db, cli_runner):
        result = cli_runner.invoke(run, ["--comment-pr"])
        assert result.exit_code == 1
        assert "--comment-pr requires --ticket or --workflow-id" in result.output

    def test_comment_pr_no_pending_workflow(self, test_db, cli_runner):
        result = cli_runner.invoke(run, ["--comment-pr", "--ticket", "TEST-123"])
        assert result.exit_code == 1
        assert "No pending PR approval workflow found" in result.output

    def test_comment_pr_wrong_status_fails(self, test_db, cli_runner):
        workflow_id = state_store.create_workflow(
            "TEST-123", status=WorkflowStatus.PENDING_APPROVAL
        )
        result = cli_runner.invoke(run, ["--comment-pr", "--workflow-id", workflow_id])
        assert result.exit_code == 1
        assert "not pending PR approval" in result.output

    def test_comment_pr_resumes_with_comments(self, test_db, cli_runner):
        workflow_id = self._make_pending_pr_approval_workflow("TEST-123")

        def fake_editor(tmp_path):
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write("Fix the typo in line 42\nAdd more tests\n")

        mock_graph = Mock()
        mock_graph.stream.return_value = iter([])
        mock_graph.get_state.return_value = Mock(
            values={
                "workflow_id": workflow_id,
                "ticket_key": "TEST-123",
            }
        )
        service = _make_test_service(graph=mock_graph)

        with patch("subprocess.run", side_effect=lambda cmd, **kwargs: fake_editor(cmd[1])):
            result = cli_runner.invoke(
                run,
                ["--comment-pr", "--workflow-id", workflow_id],
                obj=service,
            )

        assert result.exit_code == 0
        from langgraph.types import Command

        call_args = mock_graph.stream.call_args
        command = call_args[0][0]
        assert isinstance(command, Command)
        assert command.resume["decision"] == "commented"
        assert "Fix the typo" in command.resume["comments"]


class TestHandleRejectPr:
    """Tests for the --reject-pr CLI handler."""

    def _make_pending_pr_approval_workflow(self, ticket_key: str) -> str:
        return state_store.create_workflow(ticket_key, status=WorkflowStatus.PENDING_PR_APPROVAL)

    def test_reject_pr_requires_ticket_or_workflow_id(self, test_db, cli_runner):
        result = cli_runner.invoke(run, ["--reject-pr"])
        assert result.exit_code == 1
        assert "--reject-pr requires --ticket or --workflow-id" in result.output

    def test_reject_pr_no_pending_workflow(self, test_db, cli_runner):
        result = cli_runner.invoke(run, ["--reject-pr", "--ticket", "TEST-123"])
        assert result.exit_code == 1
        assert "No pending PR approval workflow found" in result.output

    def test_reject_pr_rejects_workflow(self, test_db, cli_runner):
        workflow_id = self._make_pending_pr_approval_workflow("TEST-123")

        mock_graph = Mock()
        mock_graph.stream.return_value = iter([])
        mock_graph.get_state.return_value = Mock(
            values={
                "workflow_id": workflow_id,
                "ticket_key": "TEST-123",
            }
        )
        service = _make_test_service(graph=mock_graph)

        result = cli_runner.invoke(
            run,
            ["--reject-pr", "--workflow-id", workflow_id, "--reason", "scope too broad"],
            obj=service,
        )

        assert result.exit_code == 0
        workflow = state_store.get_workflow(workflow_id)
        assert workflow["status"] == WorkflowStatus.REJECTED
        assert "PR rejected" in result.output


# ---------------------------------------------------------------------------
# Editor injection — handlers that shell out to ``$EDITOR`` must honour an
# externally provided ``editor_runner`` (used by the TUI to wrap the
# subprocess in ``App.suspend()``). The CLI keeps its current behaviour by
# leaving ``editor_runner`` unset (the default falls back to
# ``subprocess.run``).
# ---------------------------------------------------------------------------


class TestHandlerEditorRunnerInjection:
    """``_handle_clarify`` and ``_handle_comment_pr`` must call the injected
    runner instead of ``subprocess.run`` directly when one is provided."""

    def _make_pending_clarification_workflow(self, ticket_key: str) -> str:
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
                "concerns": ["Risk A"],
                "status": "concerns",
            },
        )
        return workflow_id

    def _make_pending_pr_approval_workflow(self, ticket_key: str) -> str:
        return state_store.create_workflow(ticket_key, status=WorkflowStatus.PENDING_PR_APPROVAL)

    def test_clarify_uses_injected_editor_runner(self, test_db):
        from dispatcher.commands.clarify import _handle_clarify

        workflow_id = self._make_pending_clarification_workflow("TEST-EDITOR-1")

        runner_calls = []

        def runner(cmd):
            runner_calls.append(cmd)
            # Simulate user editing the concerns file with an answer.
            with open(cmd[1], "w", encoding="utf-8") as f:
                f.write("- Risk A\nA: ok\n")

        mock_graph = Mock()
        mock_graph.stream.return_value = iter([])
        mock_graph.get_state.return_value = Mock(
            values={"workflow_id": workflow_id, "ticket_key": "TEST-EDITOR-1"}
        )
        service = _make_test_service(graph=mock_graph)

        with patch("subprocess.run") as direct_subprocess:
            _handle_clarify(service, "TEST-EDITOR-1", workflow_id, editor_runner=runner)

        # The injected runner was used; ``subprocess.run`` was never touched.
        assert len(runner_calls) == 1
        direct_subprocess.assert_not_called()
        # And the answer the runner wrote actually reached the graph.
        mock_graph.stream.assert_called_once()

    def test_comment_pr_uses_injected_editor_runner(self, test_db):
        from dispatcher.commands.pr import _handle_comment_pr

        workflow_id = self._make_pending_pr_approval_workflow("TEST-EDITOR-2")

        runner_calls = []

        def runner(cmd):
            runner_calls.append(cmd)
            with open(cmd[1], "w", encoding="utf-8") as f:
                f.write("Fix the typo\n")

        mock_graph = Mock()
        mock_graph.stream.return_value = iter([])
        mock_graph.get_state.return_value = Mock(
            values={"workflow_id": workflow_id, "ticket_key": "TEST-EDITOR-2"}
        )
        service = _make_test_service(graph=mock_graph)

        with patch("subprocess.run") as direct_subprocess:
            _handle_comment_pr(service, "TEST-EDITOR-2", workflow_id, editor_runner=runner)

        assert len(runner_calls) == 1
        direct_subprocess.assert_not_called()
        mock_graph.stream.assert_called_once()

    def test_clarify_default_runner_calls_subprocess(self, test_db):
        """No ``editor_runner`` argument → handler uses ``subprocess.run`` directly.
        This is the CLI path and must keep working unchanged."""
        from dispatcher.commands.clarify import _handle_clarify

        workflow_id = self._make_pending_clarification_workflow("TEST-EDITOR-3")

        def fake_subprocess_run(cmd, **kwargs):
            with open(cmd[1], "w", encoding="utf-8") as f:
                f.write("- Risk A\nA: yes\n")

            class _CP:
                returncode = 0

            return _CP()

        mock_graph = Mock()
        mock_graph.stream.return_value = iter([])
        mock_graph.get_state.return_value = Mock(
            values={"workflow_id": workflow_id, "ticket_key": "TEST-EDITOR-3"}
        )
        service = _make_test_service(graph=mock_graph)

        with patch("subprocess.run", side_effect=fake_subprocess_run) as direct_subprocess:
            _handle_clarify(service, "TEST-EDITOR-3", workflow_id)

        direct_subprocess.assert_called_once()
        mock_graph.stream.assert_called_once()

    def test_comment_pr_default_runner_calls_subprocess(self, test_db):
        from dispatcher.commands.pr import _handle_comment_pr

        workflow_id = self._make_pending_pr_approval_workflow("TEST-EDITOR-4")

        def fake_subprocess_run(cmd, **kwargs):
            with open(cmd[1], "w", encoding="utf-8") as f:
                f.write("Fix it\n")

            class _CP:
                returncode = 0

            return _CP()

        mock_graph = Mock()
        mock_graph.stream.return_value = iter([])
        mock_graph.get_state.return_value = Mock(
            values={"workflow_id": workflow_id, "ticket_key": "TEST-EDITOR-4"}
        )
        service = _make_test_service(graph=mock_graph)

        with patch("subprocess.run", side_effect=fake_subprocess_run) as direct_subprocess:
            _handle_comment_pr(service, "TEST-EDITOR-4", workflow_id)

        direct_subprocess.assert_called_once()
        mock_graph.stream.assert_called_once()
