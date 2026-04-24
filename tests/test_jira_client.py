"""
Comprehensive test suite for JIRA client module.

Tests cover:
- Happy path: Successful ticket fetch with all fields
- 404 handling: Ticket not found scenarios
- Authentication errors: Missing env vars and invalid credentials
- Edge cases: Empty descriptions, missing labels
"""

import os
from unittest.mock import Mock, patch

import pytest
from jira.exceptions import JIRAError

from dispatcher.jira_client import (
    JiraClient,
    JiraTicket,
    get_ticket,
    JiraConfigurationError,
    JiraAuthenticationError,
    JiraTicketNotFoundError,
)


@pytest.fixture
def mock_env_vars(monkeypatch):
    """Set up mock environment variables for JIRA configuration."""
    monkeypatch.setenv('JIRA_URL', 'https://test.atlassian.net')
    monkeypatch.setenv('JIRA_EMAIL', 'test@example.com')
    monkeypatch.setenv('JIRA_API_TOKEN', 'test-api-token')


@pytest.fixture
def mock_jira_issue():
    """Create a mock JIRA issue with typical fields."""
    issue = Mock()
    issue.key = 'AOS-34'
    issue.fields = Mock()
    issue.fields.summary = 'Jira Read Integration'
    issue.fields.description = 'Implement a Python module for JIRA integration'
    issue.fields.labels = ['backend', 'integration']
    issue.fields.status = Mock()
    issue.fields.status.name = 'To Do'
    return issue


@pytest.fixture
def mock_jira_issue_minimal():
    """Create a mock JIRA issue with minimal/empty fields (edge case)."""
    issue = Mock()
    issue.key = 'AOS-1'
    issue.fields = Mock()
    issue.fields.summary = 'Minimal Ticket'
    issue.fields.description = ''  # Empty description
    issue.fields.labels = []  # No labels
    issue.fields.status = Mock()
    issue.fields.status.name = 'Done'
    return issue


class TestJiraClient:
    """Test suite for JiraClient class."""
    
    def test_init_missing_jira_url(self, monkeypatch):
        """Test that JiraConfigurationError is raised when JIRA_URL is missing."""
        monkeypatch.setenv('JIRA_EMAIL', 'test@example.com')
        monkeypatch.setenv('JIRA_API_TOKEN', 'test-token')
        monkeypatch.delenv('JIRA_URL', raising=False)
        
        with pytest.raises(JiraConfigurationError) as exc_info:
            JiraClient()
        
        assert 'JIRA_URL' in str(exc_info.value)
    
    def test_init_missing_jira_email(self, monkeypatch):
        """Test that JiraConfigurationError is raised when JIRA_EMAIL is missing."""
        monkeypatch.setenv('JIRA_URL', 'https://test.atlassian.net')
        monkeypatch.setenv('JIRA_API_TOKEN', 'test-token')
        monkeypatch.delenv('JIRA_EMAIL', raising=False)
        
        with pytest.raises(JiraConfigurationError) as exc_info:
            JiraClient()
        
        assert 'JIRA_EMAIL' in str(exc_info.value)
    
    def test_init_missing_jira_api_token(self, monkeypatch):
        """Test that JiraConfigurationError is raised when JIRA_API_TOKEN is missing."""
        monkeypatch.setenv('JIRA_URL', 'https://test.atlassian.net')
        monkeypatch.setenv('JIRA_EMAIL', 'test@example.com')
        monkeypatch.delenv('JIRA_API_TOKEN', raising=False)
        
        with pytest.raises(JiraConfigurationError) as exc_info:
            JiraClient()
        
        assert 'JIRA_API_TOKEN' in str(exc_info.value)
    
    def test_init_missing_multiple_env_vars(self, monkeypatch):
        """Test that all missing env vars are reported."""
        monkeypatch.delenv('JIRA_URL', raising=False)
        monkeypatch.delenv('JIRA_EMAIL', raising=False)
        monkeypatch.delenv('JIRA_API_TOKEN', raising=False)
        
        with pytest.raises(JiraConfigurationError) as exc_info:
            JiraClient()
        
        error_msg = str(exc_info.value)
        assert 'JIRA_URL' in error_msg
        assert 'JIRA_EMAIL' in error_msg
        assert 'JIRA_API_TOKEN' in error_msg
    
    @patch('dispatcher.jira_client.JIRA')
    def test_init_authentication_failure_401(self, mock_jira_class, mock_env_vars):
        """Test that JiraAuthenticationError is raised on 401 authentication failure."""
        # Mock JIRA constructor to raise 401 error
        jira_error = JIRAError(status_code=401, text='Unauthorized')
        mock_jira_class.side_effect = jira_error
        
        with pytest.raises(JiraAuthenticationError) as exc_info:
            JiraClient()
        
        assert 'Authentication failed' in str(exc_info.value)
        assert 'JIRA_EMAIL' in str(exc_info.value) or 'JIRA_API_TOKEN' in str(exc_info.value)
    
    @patch('dispatcher.jira_client.JIRA')
    def test_init_success(self, mock_jira_class, mock_env_vars):
        """Test successful JiraClient initialization."""
        mock_jira_instance = Mock()
        mock_jira_class.return_value = mock_jira_instance
        
        client = JiraClient()
        
        assert client.jira_url == 'https://test.atlassian.net'
        assert client.jira_email == 'test@example.com'
        assert client.jira_api_token == 'test-api-token'
        assert client.client == mock_jira_instance
        
        # Verify JIRA was initialized with correct parameters
        mock_jira_class.assert_called_once_with(
            server='https://test.atlassian.net',
            basic_auth=('test@example.com', 'test-api-token')
        )
    
    @patch('dispatcher.jira_client.JIRA')
    def test_get_ticket_success(self, mock_jira_class, mock_env_vars, mock_jira_issue):
        """Test successful ticket fetch with all fields populated."""
        mock_jira_instance = Mock()
        mock_jira_instance.issue.return_value = mock_jira_issue
        mock_jira_class.return_value = mock_jira_instance
        
        client = JiraClient()
        ticket = client.get_ticket('AOS-34')
        
        assert isinstance(ticket, JiraTicket)
        assert ticket.key == 'AOS-34'
        assert ticket.title == 'Jira Read Integration'
        assert ticket.description == 'Implement a Python module for JIRA integration'
        assert ticket.labels == ['backend', 'integration']
        assert ticket.status == 'To Do'
        
        mock_jira_instance.issue.assert_called_once_with('AOS-34')
    
    @patch('dispatcher.jira_client.JIRA')
    def test_get_ticket_empty_description(self, mock_jira_class, mock_env_vars, mock_jira_issue_minimal):
        """Test ticket fetch with empty description and no labels."""
        mock_jira_instance = Mock()
        mock_jira_instance.issue.return_value = mock_jira_issue_minimal
        mock_jira_class.return_value = mock_jira_instance
        
        client = JiraClient()
        ticket = client.get_ticket('AOS-1')
        
        assert ticket.key == 'AOS-1'
        assert ticket.title == 'Minimal Ticket'
        assert ticket.description == ''
        assert ticket.labels == []
        assert ticket.status == 'Done'
    
    @patch('dispatcher.jira_client.JIRA')
    def test_get_ticket_null_fields(self, mock_jira_class, mock_env_vars):
        """Test ticket fetch with None/null fields."""
        issue = Mock()
        issue.key = 'AOS-2'
        issue.fields = Mock()
        issue.fields.summary = None
        issue.fields.description = None
        issue.fields.labels = None
        issue.fields.status = None
        
        mock_jira_instance = Mock()
        mock_jira_instance.issue.return_value = issue
        mock_jira_class.return_value = mock_jira_instance
        
        client = JiraClient()
        ticket = client.get_ticket('AOS-2')
        
        assert ticket.key == 'AOS-2'
        assert ticket.title == ''
        assert ticket.description == ''
        assert ticket.labels == []
        assert ticket.status == 'Unknown'
    
    @patch('dispatcher.jira_client.JIRA')
    def test_get_ticket_not_found_404(self, mock_jira_class, mock_env_vars):
        """Test that JiraTicketNotFoundError is raised for non-existent tickets."""
        mock_jira_instance = Mock()
        jira_error = JIRAError(status_code=404, text='Issue does not exist')
        mock_jira_instance.issue.side_effect = jira_error
        mock_jira_class.return_value = mock_jira_instance
        
        client = JiraClient()
        
        with pytest.raises(JiraTicketNotFoundError) as exc_info:
            client.get_ticket('INVALID-999')
        
        assert 'INVALID-999' in str(exc_info.value)
        assert 'not found' in str(exc_info.value).lower()
    
    @patch('dispatcher.jira_client.JIRA')
    def test_get_ticket_auth_error_401(self, mock_jira_class, mock_env_vars):
        """Test that JiraAuthenticationError is raised on 401 during ticket fetch."""
        mock_jira_instance = Mock()
        jira_error = JIRAError(status_code=401, text='Unauthorized')
        mock_jira_instance.issue.side_effect = jira_error
        mock_jira_class.return_value = mock_jira_instance
        
        client = JiraClient()
        
        with pytest.raises(JiraAuthenticationError) as exc_info:
            client.get_ticket('AOS-34')
        
        assert 'Authentication failed' in str(exc_info.value)
    
    @patch('dispatcher.jira_client.JIRA')
    def test_get_ticket_other_error(self, mock_jira_class, mock_env_vars):
        """Test that other JIRA errors are wrapped in JiraAuthenticationError."""
        mock_jira_instance = Mock()
        jira_error = JIRAError(status_code=500, text='Internal Server Error')
        mock_jira_instance.issue.side_effect = jira_error
        mock_jira_class.return_value = mock_jira_instance
        
        client = JiraClient()
        
        with pytest.raises(JiraAuthenticationError) as exc_info:
            client.get_ticket('AOS-34')
        
        assert 'Failed to fetch ticket' in str(exc_info.value)
        assert 'AOS-34' in str(exc_info.value)


class TestGetTicketConvenienceFunction:
    """Test suite for the get_ticket() convenience function."""
    
    @patch('dispatcher.jira_client.JIRA')
    def test_get_ticket_function_success(self, mock_jira_class, mock_env_vars, mock_jira_issue):
        """Test that get_ticket() convenience function works correctly."""
        mock_jira_instance = Mock()
        mock_jira_instance.issue.return_value = mock_jira_issue
        mock_jira_class.return_value = mock_jira_instance
        
        ticket = get_ticket('AOS-34')
        
        assert isinstance(ticket, JiraTicket)
        assert ticket.key == 'AOS-34'
        assert ticket.title == 'Jira Read Integration'
    
    def test_get_ticket_function_missing_env(self, monkeypatch):
        """Test that get_ticket() raises JiraConfigurationError when env vars missing."""
        monkeypatch.delenv('JIRA_URL', raising=False)
        monkeypatch.delenv('JIRA_EMAIL', raising=False)
        monkeypatch.delenv('JIRA_API_TOKEN', raising=False)
        
        with pytest.raises(JiraConfigurationError):
            get_ticket('AOS-34')
    
    @patch('dispatcher.jira_client.JIRA')
    def test_get_ticket_function_not_found(self, mock_jira_class, mock_env_vars):
        """Test that get_ticket() raises JiraTicketNotFoundError for invalid tickets."""
        mock_jira_instance = Mock()
        jira_error = JIRAError(status_code=404, text='Issue does not exist')
        mock_jira_instance.issue.side_effect = jira_error
        mock_jira_class.return_value = mock_jira_instance
        
        with pytest.raises(JiraTicketNotFoundError):
            get_ticket('INVALID-999')


class TestJiraTicketDataclass:
    """Test suite for JiraTicket dataclass."""
    
    def test_jira_ticket_creation(self):
        """Test JiraTicket dataclass can be created with all fields."""
        ticket = JiraTicket(
            key='AOS-34',
            title='Test Ticket',
            description='Test description',
            labels=['test', 'sample'],
            status='In Progress'
        )
        
        assert ticket.key == 'AOS-34'
        assert ticket.title == 'Test Ticket'
        assert ticket.description == 'Test description'
        assert ticket.labels == ['test', 'sample']
        assert ticket.status == 'In Progress'
    
    def test_jira_ticket_empty_fields(self):
        """Test JiraTicket with empty fields."""
        ticket = JiraTicket(
            key='AOS-1',
            title='',
            description='',
            labels=[],
            status=''
        )
        
        assert ticket.key == 'AOS-1'
        assert ticket.title == ''
        assert ticket.description == ''
        assert ticket.labels == []
        assert ticket.status == ''
