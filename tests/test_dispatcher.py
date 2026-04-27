"""Integration tests for dispatcher/run.py"""

import pytest
import os
import tempfile
from unittest.mock import Mock, patch
from click.testing import CliRunner

from dispatcher.run import run, check_for_duplicate_workflow, execute_workflow
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
def mock_jira_client():
    """Mock JIRA client for predictable responses."""
    with patch('dispatcher.run.JiraClient') as mock:
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


def test_run_creates_workflow(test_db, mock_jira_client, cli_runner):
    """Test that running the dispatcher creates a workflow record."""
    result = cli_runner.invoke(run, ['--ticket', 'TEST-123'])
    
    assert result.exit_code == 0
    assert '🚀 Starting workflow for ticket: TEST-123' in result.output
    assert '✅ Ticket fetched: Test Ticket' in result.output
    assert '📝 Creating workflow record...' in result.output
    assert '🎉 Workflow completed successfully' in result.output
    
    # Verify workflow was created in database
    workflows = state_store.get_workflow_by_ticket('TEST-123')
    assert len(workflows) == 1
    assert workflows[0]['ticket_key'] == 'TEST-123'
    assert workflows[0]['status'] == WorkflowStatus.COMPLETED


def test_run_rejects_duplicate(test_db, mock_jira_client, cli_runner):
    """Test that duplicate detection prevents running workflows twice."""
    # Create an in-progress workflow
    workflow_id = state_store.create_workflow('TEST-123', status=WorkflowStatus.IN_PROGRESS)
    
    # Try to run again
    result = cli_runner.invoke(run, ['--ticket', 'TEST-123'])
    
    assert result.exit_code == 1
    assert '❌ Workflow already in progress' in result.output
    assert workflow_id in result.output
    assert 'Cannot start a new workflow while one is active' in result.output


def test_run_allows_rerun_after_completion(test_db, mock_jira_client, cli_runner):
    """Test that completed workflows can be re-run."""
    # Create a completed workflow
    workflow_id = state_store.create_workflow('TEST-123', status=WorkflowStatus.COMPLETED)
    
    # Try to run again - should succeed with warning
    result = cli_runner.invoke(run, ['--ticket', 'TEST-123'])
    
    assert result.exit_code == 0
    assert '⚠️  Warning: 1 completed workflow(s) exist for TEST-123' in result.output
    assert '🎉 Workflow completed successfully' in result.output
    
    # Verify new workflow was created
    workflows = state_store.get_workflow_by_ticket('TEST-123')
    assert len(workflows) == 2


def test_run_handles_ticket_not_found(test_db, cli_runner):
    """Test graceful handling of non-existent tickets."""
    with patch('dispatcher.run.JiraClient') as mock:
        mock_instance = Mock()
        mock_instance.get_ticket.side_effect = JiraTicketNotFoundError("Ticket not found")
        mock.return_value = mock_instance
        
        result = cli_runner.invoke(run, ['--ticket', 'NOTFOUND-999'])
        
        assert result.exit_code == 1
        assert '❌ Ticket not found' in result.output
        
        # Verify no workflow was created (error happens before workflow creation)
        workflows = state_store.get_workflow_by_ticket('NOTFOUND-999')
        assert len(workflows) == 0


def test_run_dry_run_mode(test_db, mock_jira_client, cli_runner):
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


def test_run_logs_transitions(test_db, mock_jira_client, cli_runner):
    """Test that all stage transitions are logged to audit log."""
    result = cli_runner.invoke(run, ['--ticket', 'TEST-123'])
    
    assert result.exit_code == 0
    
    # Get workflow and audit log
    workflows = state_store.get_workflow_by_ticket('TEST-123')
    workflow_id = workflows[0]['id']
    audit_log = state_store.get_audit_log(workflow_id)
    
    # Verify audit log entries
    assert len(audit_log) >= 3  # Created, in_progress, completed
    
    # Check for workflow_created entry
    created_entries = [e for e in audit_log if e['action'] == 'workflow_created']
    assert len(created_entries) == 1
    assert created_entries[0]['actor'] == 'system'
    
    # Check for status_change entries
    status_changes = [e for e in audit_log if e['action'] == 'status_change']
    assert len(status_changes) >= 2  # in_progress, completed
    
    # Verify dispatcher is the actor for stage transitions
    dispatcher_entries = [e for e in audit_log if e['actor'] == 'dispatcher']
    assert len(dispatcher_entries) >= 2


def test_run_handles_exceptions(test_db, cli_runner):
    """Test that exceptions are caught and logged with failed status."""
    with patch('dispatcher.run.JiraClient') as mock:
        mock.side_effect = Exception("Unexpected error")
        
        result = cli_runner.invoke(run, ['--ticket', 'TEST-123'])
        
        assert result.exit_code == 1
        assert '❌ Unhandled error' in result.output


def test_run_validates_ticket_format(test_db, cli_runner):
    """Test that invalid ticket formats are rejected."""
    result = cli_runner.invoke(run, ['--ticket', 'invalid'])
    
    assert result.exit_code == 1
    assert '❌ Invalid ticket format' in result.output


def test_run_handles_jira_config_error(test_db, cli_runner):
    """Test handling of JIRA configuration errors."""
    with patch('dispatcher.run.JiraClient') as mock:
        mock.side_effect = JiraConfigurationError("Missing JIRA_URL")
        
        result = cli_runner.invoke(run, ['--ticket', 'TEST-123'])
        
        assert result.exit_code == 1
        assert '❌ JIRA configuration error' in result.output
        assert 'JIRA_URL' in result.output


def test_check_for_duplicate_workflow(test_db):
    """Test duplicate detection logic independently."""
    # No workflows - should return None
    result = check_for_duplicate_workflow('TEST-999')
    assert result is None
    
    # Create pending workflow - should return workflow_id
    workflow_id = state_store.create_workflow('TEST-999', status=WorkflowStatus.PENDING)
    result = check_for_duplicate_workflow('TEST-999')
    assert result == workflow_id
    
    # Update to completed - should return None
    state_store.update_status(workflow_id, WorkflowStatus.COMPLETED)
    result = check_for_duplicate_workflow('TEST-999')
    assert result is None
    
    # Create in_progress workflow - should return workflow_id
    workflow_id2 = state_store.create_workflow('TEST-999', status=WorkflowStatus.IN_PROGRESS)
    result = check_for_duplicate_workflow('TEST-999')
    assert result == workflow_id2


def test_execute_workflow_placeholder(test_db, capsys):
    """Test that execute_workflow runs without errors (placeholder)."""
    ticket = JiraTicket(
        key='TEST-123',
        title='Test Ticket',
        description='Test description',
        labels=['test'],
        status='To Do'
    )
    
    workflow_id = state_store.create_workflow('TEST-123', status=WorkflowStatus.PENDING)
    
    # Execute workflow (not dry-run)
    execute_workflow(workflow_id, ticket, dry_run=False)
    
    # Verify workflow transitioned to in_progress
    workflow = state_store.get_workflow(workflow_id)
    assert workflow['status'] == WorkflowStatus.IN_PROGRESS
    
    # Check audit log
    audit_log = state_store.get_audit_log(workflow_id)
    status_changes = [e for e in audit_log if e['action'] == 'status_change']
    assert len(status_changes) >= 1


def test_execute_workflow_dry_run(test_db, capsys):
    """Test that execute_workflow in dry-run mode doesn't mutate state."""
    ticket = JiraTicket(
        key='TEST-123',
        title='Test Ticket',
        description='Test description',
        labels=['test'],
        status='To Do'
    )
    
    workflow_id = state_store.create_workflow('TEST-123', status=WorkflowStatus.PENDING)
    
    # Execute workflow in dry-run mode
    execute_workflow(workflow_id, ticket, dry_run=True)
    
    # Verify workflow status unchanged
    workflow = state_store.get_workflow(workflow_id)
    assert workflow['status'] == WorkflowStatus.PENDING


def test_run_keyboard_interrupt(test_db, cli_runner):
    """Test that KeyboardInterrupt is handled gracefully."""
    with patch('dispatcher.run.JiraClient') as mock:
        mock_instance = Mock()
        mock_instance.get_ticket.side_effect = KeyboardInterrupt()
        mock.return_value = mock_instance
        
        result = cli_runner.invoke(run, ['--ticket', 'TEST-123'])
        
        assert result.exit_code == 130  # Standard SIGINT exit code
        assert '⚠️  Workflow interrupted by user' in result.output
