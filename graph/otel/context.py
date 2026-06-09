"""OTel correlation context using Python contextvars.

Stores workflow_id, ticket_key, and node_name as context-local variables so
they are automatically available throughout a workflow execution without any
argument plumbing or node modifications.

Usage:
    # At workflow start (dispatcher/commands/run_workflow.py):
    set_workflow_context(workflow_id="WF-123", ticket_key="AOS-109")

    # Anywhere in the codebase (no imports of state required):
    wf_id = get_workflow_id()   # -> "WF-123"
    ticket = get_ticket_key()   # -> "AOS-109"
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# Context variables — one per correlation attribute
# ---------------------------------------------------------------------------

_workflow_id: ContextVar[Optional[str]] = ContextVar("otel_workflow_id", default=None)
_ticket_key: ContextVar[Optional[str]] = ContextVar("otel_ticket_key", default=None)
_node_name: ContextVar[Optional[str]] = ContextVar("otel_node_name", default=None)


# ---------------------------------------------------------------------------
# Public accessors
# ---------------------------------------------------------------------------


def get_workflow_id() -> Optional[str]:
    """Return the current workflow ID from context."""
    return _workflow_id.get()


def get_ticket_key() -> Optional[str]:
    """Return the current JIRA ticket key from context."""
    return _ticket_key.get()


def get_node_name() -> Optional[str]:
    """Return the currently executing node name from context."""
    return _node_name.get()


# ---------------------------------------------------------------------------
# Context setters — called once at workflow start and per-node by the
# stream interceptor; no node code needs to call these.
# ---------------------------------------------------------------------------


def set_workflow_context(
    workflow_id: Optional[str] = None,
    ticket_key: Optional[str] = None,
) -> None:
    """Set workflow-level correlation context.  Call once before graph.invoke().

    Args:
        workflow_id: UUID identifying this workflow run.
        ticket_key:  JIRA ticket key (e.g. ``"AOS-109"``).
    """
    if workflow_id is not None:
        _workflow_id.set(workflow_id)
    if ticket_key is not None:
        _ticket_key.set(ticket_key)


def set_node_context(node_name: Optional[str]) -> None:
    """Update the current node name in context.

    Called by the stream interceptor on each node_start event.

    Args:
        node_name: Name of the node about to execute.
    """
    _node_name.set(node_name)


# ---------------------------------------------------------------------------
# Convenience dataclass for passing context as a single object
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OtelContext:
    """Snapshot of the current OTel correlation context."""

    workflow_id: Optional[str]
    ticket_key: Optional[str]
    node_name: Optional[str]

    @classmethod
    def capture(cls) -> "OtelContext":
        """Capture the current context variable values."""
        return cls(
            workflow_id=get_workflow_id(),
            ticket_key=get_ticket_key(),
            node_name=get_node_name(),
        )

    def as_attributes(self) -> dict[str, str]:
        """Return non-None values as a span attribute dict."""
        return {
            k: v
            for k, v in {
                "workflow.id": self.workflow_id,
                "jira.ticket_key": self.ticket_key,
                "graph.node_name": self.node_name,
            }.items()
            if v is not None
        }
