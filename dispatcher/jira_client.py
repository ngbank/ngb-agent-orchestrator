"""
JIRA ticket fetching module for Agent Orchestrator.

This module provides functionality to fetch JIRA tickets by key and return
structured ticket information (title, description, labels, status).
"""

import os
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
        
        try:
            self.client = JIRA(
                server=self.jira_url,
                basic_auth=(self.jira_email, self.jira_api_token)
            )
        except JIRAError as e:
            if e.status_code == 401:
                raise JiraAuthenticationError(
                    "Authentication failed. Please check JIRA_EMAIL and JIRA_API_TOKEN."
                ) from e
            else:
                raise JiraAuthenticationError(
                    f"Failed to authenticate with JIRA: {e}"
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
                    f"Ticket '{ticket_key}' not found. Please verify the ticket key."
                ) from e
            elif e.status_code == 401:
                raise JiraAuthenticationError(
                    "Authentication failed during ticket fetch. Please check your credentials."
                ) from e
            else:
                # Re-raise other JIRA errors with more context
                raise JiraAuthenticationError(
                    f"Failed to fetch ticket '{ticket_key}': {e}"
                ) from e


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
