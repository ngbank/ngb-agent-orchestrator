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
        # AOS-117: capture a rollup of what happened so the root span is
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
    """Inner generator: yields events and manages per-node spans.

    When the caller uses the default ``stream_mode="updates"``, this
    function asks LangGraph for ``["updates", "debug"]`` with
    ``subgraphs=True``.  The debug-stream ``task`` / ``task_result``
    events drive the lifecycle of one ``graph.node.<name>`` span per
    actual node dispatch — including nodes that return no state delta
    (which never appear in ``updates``) and nodes inside subgraphs.

    Caller-visible behaviour is preserved: only top-level (namespace
    ``()``) ``updates`` events are forwarded, as plain dicts.

    The legacy code path (synthesising one span per key of each yielded
    ``updates`` dict) is kept as a fallback so test fixtures that mock
    ``graph.stream`` to yield bare dicts still produce node spans.
    """
    from opentelemetry import trace as _trace_api

    # Per-namespace parent context so a subgraph's nodes nest under the
    # span we opened for the subgraph host node (e.g. graph.node.work_planner
    # → graph.node.validate_input).  The empty tuple namespace = top-level,
    # parented under workflow.run.
    root_ctx = _trace_api.set_span_in_context(root_span)
    namespace_parents: dict[tuple[str, ...], Any] = {(): root_ctx}

    # task_id -> (namespace, span) — open node spans not yet closed by
    # their matching task_result event.
    inflight: dict[str, tuple[tuple[str, ...], Span]] = {}

    # Flip to True as soon as we see any debug event.  When True, the
    # synthetic-span fallback below is skipped (debug already produced
    # the span for the node).
    debug_active = False

    effective_mode: Any = stream_mode
    use_subgraphs = False
    if isinstance(stream_mode, str) and stream_mode == "updates":
        effective_mode = ["updates", "debug"]
        use_subgraphs = True

    stream_kwargs: dict[str, Any] = {"stream_mode": effective_mode}
    if use_subgraphs:
        stream_kwargs["subgraphs"] = True

    try:
        stream_iter = graph.stream(initial_state, config, **stream_kwargs)
    except TypeError:
        # Older LangGraph or test mocks that don't accept subgraphs / multi-mode
        stream_iter = graph.stream(initial_state, config, stream_mode=stream_mode)
        effective_mode = stream_mode
        use_subgraphs = False

    for raw in stream_iter:
        ns, mode, chunk = _classify_stream_item(raw, use_subgraphs)

        if mode == "debug" and isinstance(chunk, dict):
            debug_active = True
            _handle_debug_event(chunk, ns, tracer, namespace_parents, inflight)
            continue

        # Legacy / mocked path: when no debug events are driving span
        # lifecycle, synthesise spans from update keys (preserves test
        # behaviour and any caller that overrides stream_mode).
        if not debug_active and mode == "updates" and isinstance(chunk, dict):
            for node_name, node_output in chunk.items():
                set_node_context(node_name)
                ctx = OtelContext.capture()
                with tracer.start_as_current_span(
                    f"graph.node.{node_name}",
                    attributes=ctx.as_attributes(),
                ) as node_span:
                    _record_node_output(node_span, node_name, node_output)

        # Only surface top-level updates to the caller (matches the prior
        # contract — subgraph deltas were never visible at this layer).
        if mode == "updates" and ns == ():
            yield chunk

    # Close any spans that never received a task_result (interrupted etc.)
    for _ns, span in list(inflight.values()):
        try:
            span.end()
        except Exception:
            pass

    set_node_context(None)


def _classify_stream_item(raw: Any, use_subgraphs: bool) -> tuple[tuple[str, ...], str, Any]:
    """Normalise a LangGraph stream item to ``(namespace, mode, chunk)``.

    Real LangGraph yields:
      - 3-tuples ``(namespace, mode, chunk)`` when ``subgraphs=True`` and
        ``stream_mode`` is a list.
      - 2-tuples ``(mode, chunk)`` when only ``stream_mode`` is a list.
      - bare ``chunk`` otherwise (used by test mocks).
    """
    if (
        use_subgraphs
        and isinstance(raw, tuple)
        and len(raw) == 3
        and isinstance(raw[0], tuple)
        and isinstance(raw[1], str)
    ):
        ns, mode, chunk = raw
        return tuple(str(p) for p in ns), mode, chunk
    if isinstance(raw, tuple) and len(raw) == 2 and isinstance(raw[0], str):
        mode, chunk = raw
        return (), mode, chunk
    return (), "updates", raw


def _handle_debug_event(
    chunk: dict[str, Any],
    ns: tuple[str, ...],
    tracer: trace.Tracer,
    namespace_parents: dict[tuple[str, ...], Any],
    inflight: dict[str, tuple[tuple[str, ...], Span]],
) -> None:
    """Drive ``graph.node.<name>`` span lifecycle from a debug-stream event.

    Recognises ``type: "task"`` (opens a span) and ``type: "task_result"``
    (closes it and records output / error).  Other debug event types
    (e.g. ``checkpoint``) are ignored — those are emitted as separate
    ``graph.checkpoint`` spans by the langgraph instrumentation
    elsewhere in this module.
    """
    from opentelemetry import trace as _trace_api

    event_type = chunk.get("type")
    inner = chunk.get("payload") or {}
    task_id = str(inner.get("id") or "")
    node_name = str(inner.get("name") or "")
    if not node_name:
        return

    if event_type == "task":
        parent_ctx = namespace_parents.get(ns) or namespace_parents[()]
        set_node_context(node_name)
        ctx = OtelContext.capture()
        attrs: dict[str, Any] = {**ctx.as_attributes()}
        step = chunk.get("step")
        if step is not None:
            attrs["graph.step"] = step
        if task_id:
            attrs["graph.task_id"] = task_id
        if ns:
            attrs["graph.namespace"] = "/".join(ns)
        triggers = inner.get("triggers")
        if triggers:
            attrs["graph.triggers"] = [str(t) for t in triggers]

        span = tracer.start_span(
            f"graph.node.{node_name}",
            context=parent_ctx,
            attributes=attrs,
        )
        key = task_id or f"{node_name}:{len(inflight)}"
        inflight[key] = (ns, span)
        # Any subgraph spawned by this node will use this span as its parent.
        child_ns = ns + (f"{node_name}:{task_id}",)
        namespace_parents[child_ns] = _trace_api.set_span_in_context(span, context=parent_ctx)

    elif event_type == "task_result":
        match_key: str | None = task_id if task_id and task_id in inflight else None
        if match_key is None:
            # Best-effort: find by node name when task_id missing.
            for k, (_ns, sp) in list(inflight.items()):
                if k.startswith(f"{node_name}:"):
                    match_key = k
                    break
        if match_key is None:
            return
        _ns, span = inflight.pop(match_key)
        result = inner.get("result")
        if isinstance(result, dict):
            _record_node_output(span, node_name, result)
        error = inner.get("error")
        if error:
            span.set_status(Status(StatusCode.ERROR, str(error)))
            span.set_attribute("graph.node.error", str(error))
        interrupts = inner.get("interrupts") or []
        if interrupts:
            span.set_attribute("graph.interrupts_count", len(interrupts))
        try:
            span.end()
        except Exception:
            pass
        # Drop the subgraph parent registration we may have added.
        child_ns = ns + (f"{node_name}:{task_id}",)
        namespace_parents.pop(child_ns, None)


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
