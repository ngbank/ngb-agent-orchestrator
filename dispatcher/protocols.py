"""
Protocol definitions for dependency injection across command handlers.

High-level modules accept these structural subtypes rather than concrete
implementations (Dependency Inversion Principle).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from dispatcher.jira_client import JiraTicket


class CommentPoster(Protocol):
    """Anything that can post a comment to a ticket."""

    def post_comment(self, ticket_key: str, comment: str) -> bool: ...


class TicketSource(Protocol):
    """Anything that can retrieve a ticket by key."""

    def get_ticket(self, ticket_key: str) -> JiraTicket: ...
