"""OTel bootstrap for the LiteLLM proxy subprocess.

The dispatcher spawns LiteLLM as a separate Python process (see
``graph.utils.goose_session``).  That subprocess has its own
``TracerProvider`` and never inherits the dispatcher's exporter pipeline,
so ``llm.call`` spans emitted by :class:`otel.litellm_callback.OtelLiteLLMCallback`
were not landing in the per-workflow ``otel.jsonl`` file used for cost /
latency analysis.

This module is referenced from the proxy YAML (see
``graph.utils._litellm_config_yaml``) as the single ``callbacks`` entry.
Importing it has three side-effects:

1. Seeds :mod:`otel.context` from ``NGB_WORKFLOW_ID`` / ``NGB_TICKET_KEY``
   env vars (forwarded by the dispatcher) so the proxy-side
   ``OtelContext.capture()`` populates ``workflow.id`` and
   ``jira.ticket_key`` on every span.
2. Calls :func:`otel.instrumentation.setup_tracing` which installs the
   :class:`otel.exporters.LocalJsonFileExporter` and appends
   :data:`otel.litellm_callback.otel_callback_instance` to
   ``litellm.callbacks`` so ``llm.call`` spans are emitted.
3. Re-exports :data:`graph.litellm_callbacks.proxy_handler_instance` (the
   existing :class:`graph.litellm_callbacks.TokenUsageLogger`) as the YAML
   callback target.  LiteLLM only loads one dotted-path callback per
   config; routing through this module preserves the existing token-usage
   JSONL while adding the OTel pipeline.
"""

from __future__ import annotations

import os

from graph.litellm_callbacks import proxy_handler_instance
from otel.context import set_proxy_parent_context, set_workflow_context
from otel.instrumentation import setup_tracing


def _bootstrap_proxy_otel() -> None:
    """Seed correlation context and start the OTel pipeline.

    Idempotent: ``setup_tracing`` short-circuits after the first call, and
    ``set_workflow_context`` is a no-op when both env vars are unset.

    ``synchronous=True`` switches the proxy's tracer provider to a
    ``SimpleSpanProcessor`` so each ``llm.call`` span is written to
    ``otel.jsonl`` immediately inside the callback that produced it. The
    dispatcher kills the proxy with ``SIGTERM`` (see
    ``graph.utils.goose_session``), and uvicorn's own SIGTERM handler does
    not reliably trigger the ``atexit`` hook that flushes
    ``BatchSpanProcessor`` — any buffered proxy spans would be lost.

    Also extracts the dispatcher's W3C ``traceparent`` (forwarded via
    ``NGB_TRACEPARENT``/``NGB_TRACESTATE`` env vars by
    ``graph.utils.goose_session``) and stashes the resulting OTel
    ``Context`` so ``OtelLiteLLMCallback`` can use it as the parent when
    starting each ``llm.call`` span — turning what used to be 58 orphan
    traces per workflow into a single trace tree rooted at
    ``workflow.run``.
    """
    workflow_id = os.environ.get("NGB_WORKFLOW_ID")
    ticket_key = os.environ.get("NGB_TICKET_KEY")
    if workflow_id or ticket_key:
        set_workflow_context(workflow_id=workflow_id, ticket_key=ticket_key)
    setup_tracing(synchronous=True)

    carrier: dict[str, str] = {}
    traceparent = os.environ.get("NGB_TRACEPARENT")
    if traceparent:
        carrier["traceparent"] = traceparent
    tracestate = os.environ.get("NGB_TRACESTATE")
    if tracestate:
        carrier["tracestate"] = tracestate
    if carrier:
        try:
            from opentelemetry.propagate import extract as _extract

            set_proxy_parent_context(_extract(carrier))
        except Exception:
            # Best-effort — fall back to orphan llm.call traces.
            set_proxy_parent_context(None)


_bootstrap_proxy_otel()


__all__ = ["proxy_handler_instance"]
