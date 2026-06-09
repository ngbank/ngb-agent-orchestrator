"""
Abstract exception hierarchy for ticket system errors.

High-level modules depend on these abstractions rather than on JIRA-SDK
specific exception types (Dependency Inversion Principle).
"""


class TicketSystemError(Exception):
    """Base class for all ticket system errors."""

    pass


class TicketNotFoundError(TicketSystemError):
    """Raised when a requested ticket does not exist."""

    pass


class TicketAuthError(TicketSystemError):
    """Raised when authentication or authorisation fails."""

    pass


class TicketConfigError(TicketSystemError):
    """Raised when the ticket system client is misconfigured."""

    pass
