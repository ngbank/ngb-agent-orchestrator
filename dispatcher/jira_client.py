"""
JIRA ticket fetching module for Agent Orchestrator.

This module provides functionality to fetch JIRA tickets by key and return
structured ticket information (title, description, labels, status).
"""

import os
import subprocess
from dataclasses import dataclass
from typing import List

from jira import JIRA
from jira.exceptions import JIRAError


class JiraConfigurationError(Exception):
    """Raised when JIRA configuration is missing or invalid."""
    pass


class JiraAuthenticationError(Exception):
    """Raised when JIRA authentication fails."""
    pass


class JiraTicketNotFoundError(Exception):
    """Raised when a JIRA ticket is not found (404)."""
    pass


class JiraCommentError(Exception):
    """Raised when posting a comment to JIRA fails."""
    pass


@dataclass
class JiraTicket:
    """Structured representation of a JIRA ticket."""
    key: str
    title: str
    description: str
    labels: List[str]
    status: str


class JiraClient:
    """Client for interacting with JIRA API."""
    
    def __init__(self):
        """
        Initialize JIRA client with environment-based authentication.
        
        Raises:
            JiraConfigurationError: If required environment variables are missing.
        """
        self.jira_url = os.getenv('JIRA_URL')
        self.jira_email = os.getenv('JIRA_EMAIL')
        self.jira_api_token = os.getenv('JIRA_API_TOKEN')
        
        # Validate configuration
        missing_vars = []
        if not self.jira_url:
            missing_vars.append('JIRA_URL')
        if not self.jira_email:
            missing_vars.append('JIRA_EMAIL')
        if not self.jira_api_token:
            missing_vars.append('JIRA_API_TOKEN')
        
        if missing_vars:
            raise JiraConfigurationError(
                f"Missing required environment variables: {', '.join(missing_vars)}"
            )

        # Detect unedited placeholder values from .env.example
        placeholder_vars = []
        if self.jira_email and self.jira_email == 'your.email@example.com':
            placeholder_vars.append('JIRA_EMAIL')
        if self.jira_api_token and self.jira_api_token == 'your_api_token_here':
            placeholder_vars.append('JIRA_API_TOKEN')
        if placeholder_vars:
            raise JiraConfigurationError(
                f"JIRA credentials contain placeholder values: {', '.join(placeholder_vars)}. "
                "Please update your .env file with real credentials. "
                "Generate an API token at: https://id.atlassian.com/manage-profile/security/api-tokens"
            )

        try:
            self.client = JIRA(
                server=self.jira_url,
                basic_auth=(self.jira_email, self.jira_api_token)
            )
            # Eagerly validate credentials — /myself fails fast with 401/403
            # if the email or API token is wrong.
            self.client.myself()
        except JIRAError as e:
            if e.status_code in (401, 403):
                raise JiraAuthenticationError(
                    f"JIRA authentication failed (HTTP {e.status_code}). "
                    "Check that JIRA_EMAIL and JIRA_API_TOKEN are correct. "
                    "Generate a new token at: https://id.atlassian.com/manage-profile/security/api-tokens"
                ) from e
            else:
                raise JiraAuthenticationError(
                    f"Failed to connect to JIRA: {e}"
                ) from e
    
    def get_ticket(self, ticket_key: str) -> JiraTicket:
        """
        Fetch a JIRA ticket by key and return structured data.
        
        Args:
            ticket_key: The JIRA ticket key (e.g., 'AOS-34')
        
        Returns:
            JiraTicket: Structured ticket data containing key, title, description,
                       labels, and status.
        
        Raises:
            JiraTicketNotFoundError: If the ticket does not exist.
            JiraAuthenticationError: If authentication fails during the request.
        """
        try:
            issue = self.client.issue(ticket_key)
            
            # Extract fields with safe defaults
            key = issue.key
            title = issue.fields.summary or ""
            description = issue.fields.description or ""
            labels = issue.fields.labels or []
            status = issue.fields.status.name if issue.fields.status else "Unknown"
            
            return JiraTicket(
                key=key,
                title=title,
                description=description,
                labels=labels,
                status=status
            )
        
        except JIRAError as e:
            if e.status_code == 404:
                raise JiraTicketNotFoundError(
                    f"Ticket '{ticket_key}' not found. Verify the ticket key is correct."
                ) from e
            elif e.status_code in (401, 403):
                raise JiraAuthenticationError(
                    f"Access denied fetching '{ticket_key}' (HTTP {e.status_code}). "
                    "Check that JIRA_EMAIL and JIRA_API_TOKEN are correct and have access to this project."
                ) from e
            else:
                raise JiraAuthenticationError(
                    f"Failed to fetch ticket '{ticket_key}': {e}"
                ) from e
    
    def post_comment(self, ticket_key: str, comment: str) -> bool:
        """
        Post a comment to a JIRA ticket using ACLI.
        
        Args:
            ticket_key: The JIRA ticket key (e.g., 'AOS-39')
            comment: The comment text to post (supports Jira markdown)
        
        Returns:
            bool: True if comment was posted successfully, False otherwise
        
        Raises:
            JiraCommentError: If posting the comment fails
        """
        try:
            # Use ACLI to post comment
            result = subprocess.run(
                [
                    'acli', 'jira', 'workitem', 'comment', 'add',
                    '--key', ticket_key,
                    '--comment', comment,
                    '-y'
                ],
                capture_output=True,
                text=True,
                check=True
            )
            
            # Check for success message in output
            if '✓' in result.stdout or 'successfully' in result.stdout.lower():
                return True
            else:
                raise JiraCommentError(
                    f"Unexpected output from ACLI: {result.stdout}"
                )
        
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr if e.stderr else e.stdout
            raise JiraCommentError(
                f"Failed to post comment to {ticket_key}: {error_msg}"
            ) from e
        except FileNotFoundError:
            raise JiraCommentError(
                "ACLI command not found. Please ensure Atlassian CLI is installed."
            )


def get_ticket(ticket_key: str) -> JiraTicket:
    """
    Convenience function to fetch a JIRA ticket.
    
    This function creates a JiraClient instance and fetches the specified ticket.
    It's designed for simple use cases where you just need to fetch a single ticket.
    
    Args:
        ticket_key: The JIRA ticket key (e.g., 'AOS-34')
    
    Returns:
        JiraTicket: Structured ticket data containing key, title, description,
                   labels, and status.
    
    Raises:
        JiraConfigurationError: If required environment variables are missing.
        JiraTicketNotFoundError: If the ticket does not exist.
        JiraAuthenticationError: If authentication fails.
    
    Example:
        >>> from dispatcher.jira_client import get_ticket, JiraTicketNotFoundError
        >>> try:
        ...     ticket = get_ticket('AOS-34')
        ...     print(f"Title: {ticket.title}")
        ...     print(f"Status: {ticket.status}")
        ... except JiraTicketNotFoundError as e:
        ...     print(f"Error: {e}")
    """
    client = JiraClient()
    return client.get_ticket(ticket_key)


def post_comment(ticket_key: str, comment: str) -> bool:
    """
    Convenience function to post a comment to a JIRA ticket.
    
    Args:
        ticket_key: The JIRA ticket key (e.g., 'AOS-39')
        comment: The comment text to post (supports Jira markdown)
    
    Returns:
        bool: True if comment was posted successfully
    
    Raises:
        JiraCommentError: If posting the comment fails
    
    Example:
        >>> from dispatcher.jira_client import post_comment, JiraCommentError
        >>> try:
        ...     success = post_comment('AOS-39', '# WorkPlan\\n\\nThis is a test plan.')
        ...     print(f"Comment posted: {success}")
        ... except JiraCommentError as e:
        ...     print(f"Error: {e}")
    """
    client = JiraClient()
    return client.post_comment(ticket_key, comment)
