"""OTel LiteLLM callback — emits an ``llm.call`` span per LLM API call.

Plugs into LiteLLM's ``CustomLogger`` hook system.  Each span is a child of
the active OTel context so it automatically nests under the enclosing
``graph.node.*`` span:

    graph.node.work_planner
    └── llm.call   (model, input_tokens, output_tokens, latency_ms,
                    finish_reason, reasoning_content, has_tool_calls)

Handles both synchronous (``litellm.completion``) and asynchronous
(``litellm.acompletion`` / proxy) call paths:

- ``log_success_event`` / ``log_failure_event`` — called for sync calls
  (e.g. direct ``litellm.completion()`` in node code such as
  ``infer_branch_prefix``).
- ``async_log_success_event`` / ``async_log_failure_event`` — called for
  async completions and proxy-routed calls; pass ``get_proxy_parent_context()``
  so proxy subprocess spans attach to the dispatcher's trace tree.

No node code modifications are required — registration happens once via
``register_otel_callback()`` called from ``otel/instrumentation.py``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from litellm.integrations.custom_logger import CustomLogger
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from otel.context import OtelContext, get_proxy_parent_context


def _duration_ms(start: Any, end: Any) -> float | None:
    """Return elapsed milliseconds between two datetime-like objects."""
    try:
        if isinstance(start, datetime) and isinstance(end, datetime):
            return (end - start).total_seconds() * 1000
    except Exception:
        pass
    return None


def _coerce_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


class OtelLiteLLMCallback(CustomLogger):
    """LiteLLM custom logger that emits OTel spans for every LLM API call."""

    # ------------------------------------------------------------------
    # Shared attribute builders
    # ------------------------------------------------------------------

    def _build_success_attributes(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: Any,
        end_time: Any,
    ) -> dict[str, Any]:
        ctx = OtelContext.capture()
        usage = self._extract_usage(response_obj)
        latency = _duration_ms(start_time, end_time)

        attributes: dict[str, Any] = {
            **ctx.as_attributes(),
            "llm.model": kwargs.get("model", "unknown"),
            "llm.input_tokens": _coerce_int(
                usage.get("prompt_tokens") or usage.get("input_tokens")
            ),
            "llm.output_tokens": _coerce_int(
                usage.get("completion_tokens") or usage.get("output_tokens")
            ),
            "llm.total_tokens": _coerce_int(usage.get("total_tokens")),
            "llm.request_id": kwargs.get("litellm_call_id", ""),
        }
        if latency is not None:
            attributes["llm.latency_ms"] = round(latency, 2)

        # Surface response metadata useful for diagnosing model-specific
        # behaviour (e.g. reasoning models that return content in
        # reasoning_content rather than message.content).
        try:
            choices = getattr(response_obj, "choices", None) or []
            if choices:
                choice = choices[0]
                finish_reason = getattr(choice, "finish_reason", None)
                if finish_reason:
                    attributes["llm.finish_reason"] = str(finish_reason)
                msg = getattr(choice, "message", None)
                if msg:
                    reasoning = getattr(msg, "reasoning_content", None)
                    if reasoning:
                        attributes["llm.reasoning_content"] = str(reasoning)
                    attributes["llm.has_tool_calls"] = bool(getattr(msg, "tool_calls", None))
        except Exception:
            pass

        return attributes

    def _build_failure_attributes(
        self,
        kwargs: dict[str, Any],
        start_time: Any,
        end_time: Any,
    ) -> dict[str, Any]:
        ctx = OtelContext.capture()
        exception = kwargs.get("exception")
        latency = _duration_ms(start_time, end_time)

        attributes: dict[str, Any] = {
            **ctx.as_attributes(),
            "llm.model": kwargs.get("model", "unknown"),
            "llm.request_id": kwargs.get("litellm_call_id", ""),
            "llm.error_type": type(exception).__name__ if exception else "unknown",
        }
        if latency is not None:
            attributes["llm.latency_ms"] = round(latency, 2)
        return attributes

    # ------------------------------------------------------------------
    # Synchronous path — direct litellm.completion() calls
    # ------------------------------------------------------------------

    def log_success_event(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        tracer = trace.get_tracer("graph.orchestrator")
        attributes = self._build_success_attributes(kwargs, response_obj, start_time, end_time)
        with tracer.start_as_current_span("llm.call", attributes=attributes) as span:
            span.set_status(Status(StatusCode.OK))

    def log_failure_event(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        tracer = trace.get_tracer("graph.orchestrator")
        attributes = self._build_failure_attributes(kwargs, start_time, end_time)
        exception = kwargs.get("exception")
        with tracer.start_as_current_span("llm.call", attributes=attributes) as span:
            if exception:
                span.record_exception(exception)
            span.set_status(
                Status(StatusCode.ERROR, str(exception) if exception else "LLM call failed")
            )

    # ------------------------------------------------------------------
    # Asynchronous path — litellm.acompletion() and proxy-routed calls
    # ------------------------------------------------------------------

    async def async_log_success_event(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        tracer = trace.get_tracer("graph.orchestrator")
        attributes = self._build_success_attributes(kwargs, response_obj, start_time, end_time)
        with tracer.start_as_current_span(
            "llm.call",
            context=get_proxy_parent_context(),
            attributes=attributes,
        ) as span:
            span.set_status(Status(StatusCode.OK))

    async def async_log_failure_event(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        tracer = trace.get_tracer("graph.orchestrator")
        attributes = self._build_failure_attributes(kwargs, start_time, end_time)
        exception = kwargs.get("exception")
        with tracer.start_as_current_span(
            "llm.call",
            context=get_proxy_parent_context(),
            attributes=attributes,
        ) as span:
            if exception:
                span.record_exception(exception)
            span.set_status(
                Status(StatusCode.ERROR, str(exception) if exception else "LLM call failed")
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_usage(response_obj: Any) -> dict[str, Any]:
        """Pull usage stats out of whatever shape LiteLLM returns."""
        if response_obj is None:
            return {}
        if isinstance(response_obj, dict):
            return response_obj.get("usage") or {}

        # Pydantic model (ChatCompletion, ModelResponse, etc.)
        model_dump = getattr(response_obj, "model_dump", None)
        if callable(model_dump):
            try:
                data = model_dump()
                if isinstance(data, dict):
                    return data.get("usage") or {}
            except Exception:
                pass

        usage_attr = getattr(response_obj, "usage", None)
        if usage_attr is not None:
            if isinstance(usage_attr, dict):
                return usage_attr
            inner_dump = getattr(usage_attr, "model_dump", None)
            if callable(inner_dump):
                try:
                    dumped = inner_dump()
                    if isinstance(dumped, dict):
                        return dumped
                    return {}
                except Exception:
                    pass
            return vars(usage_attr) if hasattr(usage_attr, "__dict__") else {}

        return {}


# Module-level singleton registered by register_otel_callback()
otel_callback_instance = OtelLiteLLMCallback()
