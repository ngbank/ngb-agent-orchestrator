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

import os
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Optional

from opentelemetry.context import Context as _OtelContextType

# ---------------------------------------------------------------------------
# Context variables — one per correlation attribute
# ---------------------------------------------------------------------------

_workflow_id: ContextVar[Optional[str]] = ContextVar("otel_workflow_id", default=None)
_ticket_key: ContextVar[Optional[str]] = ContextVar("otel_ticket_key", default=None)
_node_name: ContextVar[Optional[str]] = ContextVar("otel_node_name", default=None)
_workflow_stage: ContextVar[Optional[str]] = ContextVar("otel_workflow_stage", default=None)


# ---------------------------------------------------------------------------
# Public accessors
# ---------------------------------------------------------------------------


def get_workflow_id() -> Optional[str]:
    """Return the current workflow ID from context.

    Falls back to ``NGB_WORKFLOW_ID`` in the process environment when the
    ContextVar is empty. The LiteLLM proxy subprocess (see
    ``otel/litellm_proxy_setup.py``) runs each request as a fresh uvicorn
    task that does not inherit the module-import-time ContextVar, so the
    env-var fallback is what carries ``workflow.id`` onto ``llm.call``
    spans emitted from the proxy.
    """
    return _workflow_id.get() or os.environ.get("NGB_WORKFLOW_ID") or None


def get_ticket_key() -> Optional[str]:
    """Return the current JIRA ticket key from context.

    Falls back to ``NGB_TICKET_KEY`` in the process environment for the
    same reason as :func:`get_workflow_id`.
    """
    return _ticket_key.get() or os.environ.get("NGB_TICKET_KEY") or None


def get_node_name() -> Optional[str]:
    """Return the currently executing node name from context."""
    return _node_name.get()


def get_workflow_stage() -> Optional[str]:
    """Return the current workflow stage (``plan`` / ``generate_code``) from context.

    Falls back to ``NGB_WORKFLOW_STAGE`` in the process environment for the
    same reason as :func:`get_workflow_id`: the LiteLLM proxy subprocess
    runs each request as a fresh uvicorn task that does not inherit the
    module-import-time ContextVar, so the env-var fallback is what carries
    ``workflow.stage`` onto ``llm.call`` spans emitted from the proxy —
    which ``orchestrator.litellm_callbacks.aggregate_token_usage`` needs to
    filter spans by stage.
    """
    return _workflow_stage.get() or os.environ.get("NGB_WORKFLOW_STAGE") or None


# ---------------------------------------------------------------------------
# Context setters — called once at workflow start and per-node by the
# stream interceptor; no node code needs to call these.
# ---------------------------------------------------------------------------


def set_workflow_context(
    workflow_id: Optional[str] = None,
    ticket_key: Optional[str] = None,
    stage: Optional[str] = None,
) -> None:
    """Set workflow-level correlation context.  Call once before graph.invoke().

    Args:
        workflow_id: UUID identifying this workflow run.
        ticket_key:  JIRA ticket key (e.g. ``"AOS-109"``).
        stage:       Workflow stage (``"plan"`` / ``"generate_code"``).
    """
    if workflow_id is not None:
        _workflow_id.set(workflow_id)
    if ticket_key is not None:
        _ticket_key.set(ticket_key)
    if stage is not None:
        _workflow_stage.set(stage)


def set_node_context(node_name: Optional[str]) -> None:
    """Update the current node name in context.

    Called by the stream interceptor on each node_start event.

    Args:
        node_name: Name of the node about to execute.
    """
    _node_name.set(node_name)


# ---------------------------------------------------------------------------
# Cross-process OTel parent context (W3C traceparent)
# ---------------------------------------------------------------------------
#
# The LiteLLM proxy runs as its own subprocess and emits ``llm.call`` spans
# from a fresh OTel context.  To attach those spans to the dispatcher's
# trace tree, we inject the active traceparent into the proxy environment
# (see ``graph.utils.goose_session``) and the proxy bootstrap
# (``otel/litellm_proxy_setup.py``) calls
# :func:`set_proxy_parent_context` with the extracted Context object so
# :class:`otel.litellm_callback.OtelLiteLLMCallback` can use it as the
# parent when starting each ``llm.call`` span.
#
# This slot lives here (rather than in ``litellm_proxy_setup``) so the
# callback module can read it without importing the proxy bootstrap and
# creating an import cycle.

_proxy_parent_context: _OtelContextType | None = None


def set_proxy_parent_context(ctx: _OtelContextType | None) -> None:
    """Store the OTel ``Context`` to use as parent for proxy llm.call spans."""
    global _proxy_parent_context
    _proxy_parent_context = ctx


def get_proxy_parent_context() -> _OtelContextType | None:
    """Return the proxy llm.call parent context, or ``None`` if unset."""
    return _proxy_parent_context


# ---------------------------------------------------------------------------
# Convenience dataclass for passing context as a single object
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OtelContext:
    """Snapshot of the current OTel correlation context."""

    workflow_id: Optional[str]
    ticket_key: Optional[str]
    node_name: Optional[str]
    stage: Optional[str]

    @classmethod
    def capture(cls) -> "OtelContext":
        """Capture the current context variable values."""
        return cls(
            workflow_id=get_workflow_id(),
            ticket_key=get_ticket_key(),
            node_name=get_node_name(),
            stage=get_workflow_stage(),
        )

    def as_attributes(self) -> dict[str, str]:
        """Return non-None values as a span attribute dict."""
        return {
            k: v
            for k, v in {
                "workflow.id": self.workflow_id,
                "jira.ticket_key": self.ticket_key,
                "graph.node_name": self.node_name,
                "workflow.stage": self.stage,
            }.items()
            if v is not None
        }
