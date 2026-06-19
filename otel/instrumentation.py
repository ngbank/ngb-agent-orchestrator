"""OTel tracer initialisation and LangGraph stream interceptor.

Provides zero-annotation instrumentation for all orchestrator graph nodes.

Key design:
  - ``setup_tracing()``          — call once at process start.
  - ``instrument_graph_stream()``— wrap any ``graph.stream()`` call; handles
                                   node start/end/error spans automatically.
  - No individual node files need to be modified.

Span structure emitted per workflow run:
  workflow.run (root)
  └── graph.node.work_planner
  └── graph.node.await_approval
  └── graph.node.execute_plan        (may appear multiple times on retry)
  └── graph.node.await_pr_approval

Each span carries:
  - workflow.id
  - jira.ticket_key
  - graph.node_name
  - graph.event_type  (on error spans: exception recorded)
"""

from __future__ import annotations

import os
from typing import Any, Generator

import litellm
from langchain_core.runnables import RunnableConfig
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
from opentelemetry.trace import Span, Status, StatusCode

from otel.context import OtelContext, set_node_context
from otel.exporters import create_exporter

_SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "ngb-agent-orchestrator")
_TRACER_NAME = "graph.orchestrator"

_tracer: trace.Tracer | None = None


def setup_tracing(*, synchronous: bool = False) -> None:
    """Initialise the global OTel tracer provider.

    Safe to call multiple times (idempotent after first call).  Reads
    exporter config from environment — see ``otel/exporters.py``.

    Args:
        synchronous: If ``True``, use ``SimpleSpanProcessor`` so spans are
            exported synchronously inside the callback that produced them.
            Required in the LiteLLM proxy subprocess: the dispatcher kills
            the proxy with ``SIGTERM`` (see ``graph.utils.goose_session``)
            and uvicorn's own SIGTERM handler does not always trigger the
            ``atexit`` hook that flushes ``BatchSpanProcessor``. Spans
            buffered there would be lost. The dispatcher process keeps the
            default batched behaviour for performance.

    Environment variables:
        OTEL_SERVICE_NAME       Service name attached to all spans.
                                Default: ``ngb-agent-orchestrator``
        OTEL_EXPORTERS          Comma-separated list of ``console`` and/or ``otlp``.
                                File logging is always on regardless of this setting.
        OTEL_EXPORTER_OTLP_ENDPOINT  gRPC endpoint for OTLP exporter.
                                     Default: ``http://localhost:4317``
    """
    global _tracer

    if _tracer is not None:
        # Already initialised — skip.
        return

    resource = Resource.create({"service.name": _SERVICE_NAME})
    provider = TracerProvider(resource=resource)

    exporter = create_exporter()
    processor: SpanProcessor = (
        SimpleSpanProcessor(exporter) if synchronous else BatchSpanProcessor(exporter)
    )
    provider.add_span_processor(processor)

    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer(_TRACER_NAME)

    # Register OTel LiteLLM callback so LLM calls emit child spans.
    from otel.litellm_callback import otel_callback_instance

    if otel_callback_instance not in litellm.callbacks:
        litellm.callbacks.append(otel_callback_instance)


def get_tracer() -> trace.Tracer:
    """Return the module-level tracer, initialising tracing if necessary."""
    if _tracer is None:
        setup_tracing()
    assert _tracer is not None
    return _tracer


# ---------------------------------------------------------------------------
# Stream interceptor — wraps graph.stream() without touching nodes
# ---------------------------------------------------------------------------


def instrument_graph_stream(
    graph: Any,
    initial_state: Any,
    config: RunnableConfig,
    *,
    stream_mode: str = "updates",
) -> Generator[dict[str, Any], None, None]:
    """Wrap ``graph.stream()`` to emit OTel spans per node execution.

    Replaces a direct ``graph.stream(state, config)`` call.  All node spans
    are children of a root ``workflow.run`` span that carries the workflow-
    level correlation attributes.

    Args:
        graph:         The compiled LangGraph ``CompiledGraph``.
        initial_state: State dict passed to ``graph.stream()``.
        config:        LangGraph config dict (must include thread_id).
        stream_mode:   Passed through to ``graph.stream()``.

    Yields:
        Each event dict from ``graph.stream()``, unchanged.

    Example::

        for event in instrument_graph_stream(graph, state, config):
            process_event(event)
    """
    tracer = get_tracer()
    ctx = OtelContext.capture()

    with tracer.start_as_current_span(
        "workflow.run",
        attributes={
            **ctx.as_attributes(),
            "graph.thread_id": config.get("configurable", {}).get("thread_id", ""),
        },
    ) as root_span:
        # Capture a rollup of what happened so the root span is
        # self-describing (without grepping all child spans).
        node_count = 0
        last_node: str | None = None
        exit_reason = "completed"
        try:
            for event in _stream_with_node_spans(
                graph, initial_state, config, stream_mode, tracer, root_span
            ):
                if isinstance(event, dict):
                    for node_name in event.keys():
                        node_count += 1
                        last_node = node_name
                yield event
        except Exception as exc:
            exit_reason = "error"
            root_span.record_exception(exc)
            root_span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise
        else:
            # Detect interrupt by checking the last observed node name; LangGraph
            # surfaces an "__interrupt__" pseudo-node when the graph pauses.
            if last_node == "__interrupt__":
                exit_reason = "interrupted"
            root_span.set_status(Status(StatusCode.OK))
        finally:
            root_span.set_attribute("workflow.node_count", node_count)
            root_span.set_attribute("workflow.exit_reason", exit_reason)
            if last_node is not None:
                root_span.set_attribute("workflow.last_node", last_node)


def _stream_with_node_spans(
    graph: Any,
    initial_state: dict[str, Any],
    config: RunnableConfig,
    stream_mode: str,
    tracer: trace.Tracer,
    root_span: Span,
) -> Generator[dict[str, Any], None, None]:
    """Inner generator: yields events and manages per-node spans."""
    for event in graph.stream(initial_state, config, stream_mode=stream_mode):
        # LangGraph emits dicts keyed by node name in "updates" mode.
        # Each key is a node name; the value is the state delta from that node.
        if isinstance(event, dict):
            for node_name, node_output in event.items():
                # Node finished — record as a span.
                set_node_context(node_name)
                ctx = OtelContext.capture()

                span_name = f"graph.node.{node_name}"
                with tracer.start_as_current_span(
                    span_name,
                    attributes=ctx.as_attributes(),
                ) as node_span:
                    _record_node_output(node_span, node_name, node_output)

        yield event

    set_node_context(None)


def _record_node_output(span: Span, node_name: str, output: Any) -> None:
    """Attach node output metadata to the span; record errors if present."""
    if not isinstance(output, dict):
        return

    # AOS-117 enrichment: surface which state keys this node produced (no values,
    # avoids any redaction concern) and a rough size signal.
    keys = sorted(str(k) for k in output.keys())
    if keys:
        span.set_attribute("graph.node.state_keys_changed", keys)
    try:
        import json as _json

        size_bytes = len(_json.dumps(output, default=str).encode("utf-8"))
        span.set_attribute("graph.node.output_size_bytes", size_bytes)
    except (TypeError, ValueError):
        # Non-serialisable output is rare but should not break instrumentation.
        pass

    error = output.get("error")
    if error:
        span.set_status(Status(StatusCode.ERROR, str(error)))
        span.set_attribute("graph.node.error", str(error))

        failed_node = output.get("failed_node")
        if failed_node:
            span.set_attribute("graph.node.failed_node", str(failed_node))
    else:
        span.set_status(Status(StatusCode.OK))

    # Attach workflow status if present in the node output
    workflow_status = output.get("workflow_status")
    if workflow_status is not None:
        span.set_attribute("workflow.status", str(workflow_status))
