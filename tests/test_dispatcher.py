"""Integration tests for dispatcher/run.py (LangGraph-based orchestrator)"""

import pytest
import os
import tempfile
from unittest.mock import Mock, patch
from click.testing import CliRunner
from langgraph.checkpoint.memory import MemorySaver

from dispatcher.run import run
from dispatcher.jira_client import JiraTicket, JiraTicketNotFoundError, JiraConfigurationError
from state import state_store
from state.workflow_status import WorkflowStatus


@pytest.fixture
def test_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as f:
        db_path = f.name
    
    # Remove the file so the db can be created fresh
    os.unlink(db_path)
    
    # Set environment variable for test
    original_db_path = os.environ.get('DB_PATH')
    os.environ['DB_PATH'] = db_path
    
    # Run migrations
    state_store.run_migrations()
    
    yield db_path
    
    # Cleanup
    if os.path.exists(db_path):
        os.unlink(db_path)
    
    # Restore original env var
    if original_db_path:
        os.environ['DB_PATH'] = original_db_path
    elif 'DB_PATH' in os.environ:
        del os.environ['DB_PATH']


@pytest.fixture
def memory_checkpointer():
    """In-memory checkpointer so tests don't need SqliteSaver setup."""
    return MemorySaver()


@pytest.fixture
def mock_jira_client():
    """Mock JIRA client for predictable responses."""
    with patch('graph.work_planner.nodes.fetch_ticket.JiraClient') as mock:
        mock_instance = Mock()
        mock_instance.get_ticket.return_value = JiraTicket(
            key='TEST-123',
            title='Test Ticket',
            description='Test description',
            labels=['test', 'automation'],
            status='To Do'
        )
        mock.return_value = mock_instance
        yield mock


@pytest.fixture
def cli_runner():
    """Create a Click CLI test runner."""
    return CliRunner()


def test_run_creates_workflow(test_db, mock_jira_client, cli_runner, memory_checkpointer):
    """Test that running the dispatcher creates a workflow record and pauses for approval."""
    with patch('dispatcher.run.build_orchestrator') as mock_build:
        from graph.builder import build_orchestrator
        mock_build.side_effect = lambda: build_orchestrator(checkpointer=memory_checkpointer)

        result = cli_runner.invoke(run, ['--ticket', 'TEST-123'])

    assert result.exit_code == 0
    assert '🚀 Starting workflow for ticket: TEST-123' in result.output
    assert '✅ Ticket fetched: Test Ticket' in result.output
    assert '📝 Creating workflow record...' in result.output
    # Graph pauses at await_approval — no completion message yet
    assert '⏸️  WorkPlan is ready for review.' in result.output

    # Verify workflow was created and is pending approval
    workflows = state_store.get_workflow_by_ticket('TEST-123')
    assert len(workflows) == 1
    assert workflows[0]['ticket_key'] == 'TEST-123'
    assert workflows[0]['status'] == WorkflowStatus.PENDING_APPROVAL


def test_run_rejects_duplicate(test_db, mock_jira_client, cli_runner, memory_checkpointer):
    """Test that duplicate detection prevents running workflows twice."""
    # Create an in-progress workflow
    workflow_id = state_store.create_workflow('TEST-123', status=WorkflowStatus.IN_PROGRESS)

    with patch('dispatcher.run.build_orchestrator') as mock_build:
        from graph.builder import build_orchestrator
        mock_build.side_effect = lambda: build_orchestrator(checkpointer=memory_checkpointer)

        result = cli_runner.invoke(run, ['--ticket', 'TEST-123'])

    assert result.exit_code == 1
    assert '❌ Workflow already in progress' in result.output
    assert workflow_id in result.output
    assert 'Cannot start a new workflow while one is active' in result.output


def test_run_allows_rerun_after_completion(test_db, mock_jira_client, cli_runner, memory_checkpointer):
    """Test that completed workflows can be re-run (pauses at approval gate)."""
    # Create a completed workflow
    state_store.create_workflow('TEST-123', status=WorkflowStatus.COMPLETED)

    with patch('dispatcher.run.build_orchestrator') as mock_build:
        from graph.builder import build_orchestrator
        mock_build.side_effect = lambda: build_orchestrator(checkpointer=memory_checkpointer)

        result = cli_runner.invoke(run, ['--ticket', 'TEST-123'])

    assert result.exit_code == 0
    assert '⚠️  Warning: 1 completed workflow(s) exist for TEST-123' in result.output
    # New run pauses at approval gate
    assert '⏸️  WorkPlan is ready for review.' in result.output

    # Verify new workflow was created (2 total)
    workflows = state_store.get_workflow_by_ticket('TEST-123')
    assert len(workflows) == 2


def test_run_handles_ticket_not_found(test_db, cli_runner, memory_checkpointer):
    """Test graceful handling of non-existent tickets."""
    with patch('graph.work_planner.nodes.fetch_ticket.JiraClient') as mock:
        mock_instance = Mock()
        mock_instance.get_ticket.side_effect = JiraTicketNotFoundError("Ticket not found")
        mock.return_value = mock_instance

        with patch('dispatcher.run.build_orchestrator') as mock_build:
            from graph.builder import build_orchestrator
            mock_build.side_effect = lambda: build_orchestrator(checkpointer=memory_checkpointer)

            result = cli_runner.invoke(run, ['--ticket', 'NOTFOUND-999'])

    assert result.exit_code == 1
    assert '❌ Ticket not found' in result.output

    # Verify no workflow was created (error happens before workflow creation)
    workflows = state_store.get_workflow_by_ticket('NOTFOUND-999')
    assert len(workflows) == 0


def test_run_dry_run_mode(test_db, cli_runner):
    """Test that dry-run mode doesn't mutate state."""
    result = cli_runner.invoke(run, ['--ticket', 'TEST-123', '--dry-run'])
    
    assert result.exit_code == 0
    assert '[DRY RUN] Mode enabled' in result.output
    assert '[DRY RUN] Would fetch ticket: TEST-123' in result.output
    assert '[DRY RUN] Would create workflow for ticket: TEST-123' in result.output
    assert '[DRY RUN] Would execute workflow stages' in result.output
    assert '✅ Dry run completed successfully' in result.output
    
    # Verify no workflow was created in database
    workflows = state_store.get_workflow_by_ticket('TEST-123')
    assert len(workflows) == 0


def test_run_logs_transitions(test_db, mock_jira_client, cli_runner, memory_checkpointer):
    """Test that all stage transitions are logged to audit log."""
    with patch('dispatcher.run.build_orchestrator') as mock_build:
        from graph.builder import build_orchestrator
        mock_build.side_effect = lambda: build_orchestrator(checkpointer=memory_checkpointer)

        result = cli_runner.invoke(run, ['--ticket', 'TEST-123'])

    assert result.exit_code == 0

    # Get workflow and audit log
    workflows = state_store.get_workflow_by_ticket('TEST-123')
    workflow_id = workflows[0]['id']
    audit_log = state_store.get_audit_log(workflow_id)

    # Verify audit log entries (created, in_progress, pending_approval)
    assert len(audit_log) >= 3

    # Check for workflow_created entry
    created_entries = [e for e in audit_log if e['action'] == 'workflow_created']
    assert len(created_entries) == 1
    assert created_entries[0]['actor'] == 'system'

    # Check for status_change entries
    status_changes = [e for e in audit_log if e['action'] == 'status_change']
    assert len(status_changes) >= 2  # in_progress, pending_approval

    # Verify dispatcher is the actor for stage transitions
    dispatcher_entries = [e for e in audit_log if e['actor'] == 'dispatcher']
    assert len(dispatcher_entries) >= 2

    # Workflow should be paused pending approval
    assert workflows[0]['status'] == WorkflowStatus.PENDING_APPROVAL


def test_run_handles_exceptions(test_db, cli_runner):
    """Test that exceptions are caught and logged with failed status."""
    with patch('graph.work_planner.nodes.fetch_ticket.JiraClient') as mock:
        mock.side_effect = Exception("Unexpected error")
        
        result = cli_runner.invoke(run, ['--ticket', 'TEST-123'])
        
        assert result.exit_code == 1
        assert '❌ Unhandled error' in result.output


def test_run_validates_ticket_format(test_db, cli_runner):
    """Test that invalid ticket formats are rejected."""
    result = cli_runner.invoke(run, ['--ticket', 'invalid'])
    
    assert result.exit_code == 1
    assert '❌ Invalid ticket format' in result.output


def test_run_handles_jira_config_error(test_db, cli_runner, memory_checkpointer):
    """Test handling of JIRA configuration errors."""
    with patch('graph.work_planner.nodes.fetch_ticket.JiraClient') as mock:
        mock.side_effect = JiraConfigurationError("Missing JIRA_URL")

        with patch('dispatcher.run.build_orchestrator') as mock_build:
            from graph.builder import build_orchestrator
            mock_build.side_effect = lambda: build_orchestrator(checkpointer=memory_checkpointer)

            result = cli_runner.invoke(run, ['--ticket', 'TEST-123'])

    assert result.exit_code == 1
    assert '❌ JIRA configuration error' in result.output
    assert 'JIRA_URL' in result.output


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
    with patch('graph.work_planner.nodes.fetch_ticket.JiraClient') as mock:
        mock_instance = Mock()
        mock_instance.get_ticket.side_effect = KeyboardInterrupt()
        mock.return_value = mock_instance

        with patch('dispatcher.run.build_orchestrator') as mock_build:
            from graph.builder import build_orchestrator
            mock_build.side_effect = lambda: build_orchestrator(checkpointer=memory_checkpointer)

            result = cli_runner.invoke(run, ['--ticket', 'TEST-123'])

    assert result.exit_code == 130  # Standard SIGINT exit code
    assert '⚠️  Workflow interrupted by user' in result.output
