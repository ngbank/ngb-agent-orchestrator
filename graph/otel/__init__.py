"""OpenTelemetry instrumentation for the orchestrator graph.

Provides zero-annotation tracing: no individual node modifications required.
Instrumentation is injected via:
  - context.py   : ContextVar-based correlation propagation
  - exporters.py : Configurable span exporters (console / OTLP)
  - instrumentation.py : Stream-based LangGraph event interceptor
"""

from graph.otel.context import (
    OtelContext,
    get_node_name,
    get_ticket_key,
    get_workflow_id,
    set_workflow_context,
)
from graph.otel.instrumentation import (
    get_tracer,
    instrument_graph_stream,
    setup_tracing,
)
from graph.otel.litellm_callback import OtelLiteLLMCallback

__all__ = [
    "OtelContext",
    "OtelLiteLLMCallback",
    "get_node_name",
    "get_ticket_key",
    "get_workflow_id",
    "set_workflow_context",
    "get_tracer",
    "instrument_graph_stream",
    "setup_tracing",
]
