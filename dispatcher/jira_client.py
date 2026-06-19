"""
JIRA ticket fetching module for Agent Orchestrator.

This module provides functionality to fetch JIRA tickets by key and return
structured ticket information (title, description, labels, status).
"""

import os
from dataclasses import dataclass
from typing import List

import requests
from jira import JIRA
from jira.exceptions import JIRAError

from dispatcher.exceptions import (
    TicketAuthError,
    TicketConfigError,
    TicketNotFoundError,
)


class JiraConfigurationError(TicketConfigError):
    """Raised when JIRA configuration is missing or invalid."""

    pass


class JiraAuthenticationError(TicketAuthError):
    """Raised when JIRA authentication fails."""

    pass


class JiraAPIError(Exception):
    """Raised when non-authentication JIRA API operations fail."""

    pass


class JiraTicketNotFoundError(TicketNotFoundError):
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
        self.jira_url = os.getenv("JIRA_URL")
        self.jira_oauth_client_id = os.getenv("JIRA_OAUTH_CLIENT_ID")
        self.jira_oauth_client_secret = os.getenv("JIRA_OAUTH_CLIENT_SECRET")
        self.jira_oauth_token_url = os.getenv("JIRA_OAUTH_TOKEN_URL") or self._default_token_url()
        self.jira_oauth_scope = os.getenv("JIRA_OAUTH_SCOPE")
        self.jira_oauth_audience = os.getenv("JIRA_OAUTH_AUDIENCE")
        self.jira_api_base = self.jira_url

        # Validate configuration
        missing_vars = []
        if not self.jira_url:
            missing_vars.append("JIRA_URL")
        if not self.jira_oauth_client_id:
            missing_vars.append("JIRA_OAUTH_CLIENT_ID")
        if not self.jira_oauth_client_secret:
            missing_vars.append("JIRA_OAUTH_CLIENT_SECRET")
        if not self.jira_oauth_token_url:
            missing_vars.append("JIRA_OAUTH_TOKEN_URL")

        if missing_vars:
            raise JiraConfigurationError(
                f"Missing required environment variables: {', '.join(missing_vars)}"
            )

        # Detect unedited placeholder values from .env.example
        placeholder_vars = []
        if self.jira_oauth_client_id and self.jira_oauth_client_id == "your-oauth-client-id":
            placeholder_vars.append("JIRA_OAUTH_CLIENT_ID")
        if (
            self.jira_oauth_client_secret
            and self.jira_oauth_client_secret == "your-oauth-client-secret"
        ):
            placeholder_vars.append("JIRA_OAUTH_CLIENT_SECRET")
        if placeholder_vars:
            raise JiraConfigurationError(
                f"JIRA credentials contain placeholder values: {', '.join(placeholder_vars)}. "
                "Please update your .env file or Azure Key Vault with real OAuth credentials."
            )

        try:
            token = self._fetch_oauth_access_token()
            self.jira_api_base = self._resolve_jira_api_base(token)
            self.client = JIRA(server=self.jira_api_base, token_auth=token)
        except JIRAError as e:
            if e.status_code in (401, 403):
                err_text = str(e).lower()
                if "scope" in err_text:
                    raise JiraAuthenticationError(
                        f"JIRA OAuth scope mismatch (HTTP {e.status_code}). "
                        "Grant at least 'read:jira-work' "
                        "(and 'write:jira-work' if posting comments)."
                    ) from e
                raise JiraAuthenticationError(
                    f"JIRA authentication failed (HTTP {e.status_code}). "
                    "Check JIRA_OAUTH_CLIENT_ID and JIRA_OAUTH_CLIENT_SECRET and verify "
                    "the service account has JIRA API permissions."
                ) from e
            else:
                raise JiraAPIError(f"Failed to connect to JIRA: {e}") from e

    def _fetch_oauth_access_token(self) -> str:
        """Fetch an OAuth access token using client credentials."""
        assert self.jira_oauth_client_id is not None
        assert self.jira_oauth_client_secret is not None
        assert self.jira_oauth_token_url is not None

        payload = {"grant_type": "client_credentials"}
        if self.jira_oauth_scope:
            payload["scope"] = self.jira_oauth_scope
        if self.jira_oauth_audience:
            payload["audience"] = self.jira_oauth_audience

        try:
            response = requests.post(
                self.jira_oauth_token_url,
                data=payload,
                auth=(self.jira_oauth_client_id, self.jira_oauth_client_secret),
                timeout=20,
            )
        except requests.RequestException as exc:
            raise JiraAuthenticationError(f"Failed requesting OAuth token: {exc}") from exc

        if response.status_code in (401, 403):
            raise JiraAuthenticationError(
                f"OAuth token request failed (HTTP {response.status_code}). "
                "Check JIRA_OAUTH_CLIENT_ID/JIRA_OAUTH_CLIENT_SECRET and token endpoint."
            )

        if response.status_code == 404:
            raise JiraConfigurationError(
                f"OAuth token endpoint not found (HTTP 404): {self.jira_oauth_token_url}. "
                "Set JIRA_OAUTH_TOKEN_URL explicitly. For Atlassian Cloud use: "
                "https://auth.atlassian.com/oauth/token"
            )

        if response.status_code >= 400:
            raise JiraAPIError(
                f"OAuth token request failed (HTTP {response.status_code}): {response.text}"
            )

        try:
            token = response.json().get("access_token", "")
        except ValueError as exc:
            raise JiraAuthenticationError("OAuth token response is not valid JSON.") from exc

        if not token:
            raise JiraAuthenticationError("OAuth token response does not include access_token.")

        return token

    def _default_token_url(self) -> str:
        """Infer a sensible OAuth token endpoint from JIRA_URL."""
        jira_url = (self.jira_url or "").lower()
        if jira_url.endswith(".atlassian.net") or ".atlassian.net/" in jira_url:
            return "https://auth.atlassian.com/oauth/token"

        base = (self.jira_url or "").rstrip("/")
        return f"{base}/rest/oauth2/latest/token"

    def _resolve_jira_api_base(self, token: str) -> str:
        """Resolve the proper Jira API base URL for OAuth access tokens."""
        assert self.jira_url is not None

        normalized = self.jira_url.rstrip("/")
        if ".atlassian.net" not in normalized.lower():
            return normalized

        try:
            response = requests.get(
                "https://api.atlassian.com/oauth/token/accessible-resources",
                headers={"Authorization": f"Bearer {token}"},
                timeout=20,
            )
        except requests.RequestException as exc:
            raise JiraAuthenticationError(
                f"Failed discovering Atlassian accessible resources: {exc}"
            ) from exc

        if response.status_code in (401, 403):
            raise JiraAuthenticationError(
                "OAuth token cannot access Atlassian resources. "
                "Check OAuth app scopes and audience."
            )

        if response.status_code >= 400:
            raise JiraAPIError(
                f"Failed loading Atlassian accessible resources (HTTP {response.status_code}): "
                f"{response.text}"
            )

        try:
            resources = response.json()
        except ValueError as exc:
            raise JiraAuthenticationError(
                "Invalid JSON from Atlassian accessible-resources endpoint."
            ) from exc

        if not isinstance(resources, list) or not resources:
            raise JiraAuthenticationError(
                "OAuth token has no accessible Atlassian resources. "
                "Check OAuth app installation and scopes."
            )

        matched_resources = []
        for resource in resources:
            url = str(resource.get("url", "")).rstrip("/")
            if url.lower() == normalized.lower():
                matched_resources.append(resource)

        if not matched_resources:
            raise JiraAuthenticationError(
                f"OAuth token has no access to Jira site {normalized}. "
                "Check app installation and granted scopes for this site."
            )

        scopes = set()
        for resource in matched_resources:
            scopes.update(resource.get("scopes", []))

        if "read:jira-work" not in scopes:
            raise JiraAuthenticationError(
                "OAuth token is missing Jira scopes for issue reads. "
                "Grant 'read:jira-work' (and 'write:jira-work' if posting comments)."
            )

        cloud_id = matched_resources[0].get("id")
        if not cloud_id:
            raise JiraAuthenticationError("Atlassian resource missing cloud id.")

        return f"https://api.atlassian.com/ex/jira/{cloud_id}"

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
                key=key, title=title, description=description, labels=labels, status=status
            )

        except JIRAError as e:
            if e.status_code == 404:
                raise JiraTicketNotFoundError(
                    f"Ticket '{ticket_key}' not found. Verify the ticket key is correct."
                ) from e
            elif e.status_code in (401, 403):
                raise JiraAuthenticationError(
                    f"Access denied fetching '{ticket_key}' (HTTP {e.status_code}). "
                    "Check OAuth credentials and ensure the service account has access "
                    "to this project."
                ) from e
            else:
                raise JiraAPIError(f"Failed to fetch ticket '{ticket_key}': {e}") from e

    def post_comment(self, ticket_key: str, comment: str) -> bool:
        """
        Post a comment to a JIRA ticket using the JIRA API.

        Args:
            ticket_key: The JIRA ticket key (e.g., 'AOS-39')
            comment: The comment text to post (supports Jira markdown)

        Returns:
            bool: True if comment was posted successfully

        Raises:
            JiraCommentError: If posting the comment fails
        """
        try:
            self.client.add_comment(ticket_key, comment)
            return True
        except JIRAError as e:
            raise JiraCommentError(f"Failed to post comment to {ticket_key}: {e}") from e


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
