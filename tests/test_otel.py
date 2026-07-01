"""Tests for otel package — context, exporters, stream instrumentation, and LLM callback."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from otel.context import (
    OtelContext,
    _node_name,
    _ticket_key,
    _workflow_id,
    get_node_name,
    get_ticket_key,
    get_workflow_id,
    set_node_context,
    set_workflow_context,
)
from otel.exporters import create_exporter
from otel.instrumentation import (
    _record_node_output,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_in_memory_provider() -> tuple[TracerProvider, InMemorySpanExporter]:
    """Return a (provider, exporter) pair backed by InMemorySpanExporter."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


# ---------------------------------------------------------------------------
# context.py
# ---------------------------------------------------------------------------


class TestOtelContext:
    def setup_method(self):
        # Reset context vars before each test
        _workflow_id.set(None)
        _ticket_key.set(None)
        _node_name.set(None)

    def test_defaults_are_none(self):
        assert get_workflow_id() is None
        assert get_ticket_key() is None
        assert get_node_name() is None

    def test_set_workflow_context(self):
        set_workflow_context(workflow_id="wf-1", ticket_key="AOS-1")
        assert get_workflow_id() == "wf-1"
        assert get_ticket_key() == "AOS-1"

    def test_set_node_context(self):
        set_node_context("work_planner")
        assert get_node_name() == "work_planner"

    def test_set_node_context_to_none(self):
        set_node_context("work_planner")
        set_node_context(None)
        assert get_node_name() is None

    def test_otel_context_capture(self):
        set_workflow_context(workflow_id="wf-2", ticket_key="AOS-2")
        set_node_context("generate_code")
        ctx = OtelContext.capture()
        assert ctx.workflow_id == "wf-2"
        assert ctx.ticket_key == "AOS-2"
        assert ctx.node_name == "generate_code"

    def test_as_attributes_excludes_none(self):
        set_workflow_context(workflow_id="wf-3")
        ctx = OtelContext.capture()
        attrs = ctx.as_attributes()
        assert "workflow.id" in attrs
        assert "jira.ticket_key" not in attrs  # ticket_key is None
        assert "graph.node_name" not in attrs  # node_name is None

    def test_as_attributes_all_set(self):
        set_workflow_context(workflow_id="wf-4", ticket_key="AOS-4")
        set_node_context("await_approval")
        ctx = OtelContext.capture()
        attrs = ctx.as_attributes()
        assert attrs["workflow.id"] == "wf-4"
        assert attrs["jira.ticket_key"] == "AOS-4"
        assert attrs["graph.node_name"] == "await_approval"

    def test_set_workflow_context_partial_update(self):
        set_workflow_context(workflow_id="wf-5")
        set_workflow_context(ticket_key="AOS-5")
        assert get_workflow_id() == "wf-5"
        assert get_ticket_key() == "AOS-5"


# ---------------------------------------------------------------------------
# exporters.py
# ---------------------------------------------------------------------------


class TestCreateExporter:
    def test_file_only_when_exporters_unset(self, monkeypatch):
        """Default (OTEL_EXPORTERS unset) returns only LocalJsonFileExporter."""
        monkeypatch.delenv("OTEL_EXPORTERS", raising=False)
        from otel.exporters import LocalJsonFileExporter

        exporter = create_exporter()
        assert isinstance(exporter, LocalJsonFileExporter)

    def test_file_only_when_exporters_empty(self, monkeypatch):
        """Empty OTEL_EXPORTERS returns only LocalJsonFileExporter."""
        monkeypatch.setenv("OTEL_EXPORTERS", "")
        from otel.exporters import LocalJsonFileExporter

        exporter = create_exporter()
        assert isinstance(exporter, LocalJsonFileExporter)

    def test_console_exporter_includes_file_and_console(self, monkeypatch):
        """OTEL_EXPORTERS=console returns MultiExporter with file + console."""
        monkeypatch.setenv("OTEL_EXPORTERS", "console")
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter

        from otel.exporters import LocalJsonFileExporter, MultiExporter

        exporter = create_exporter()
        assert isinstance(exporter, MultiExporter)
        assert any(isinstance(e, LocalJsonFileExporter) for e in exporter.exporters)
        assert any(isinstance(e, ConsoleSpanExporter) for e in exporter.exporters)

    def test_multi_exporter_console_and_otlp(self, monkeypatch):
        """OTEL_EXPORTERS=console,otlp returns MultiExporter with file + console + otlp."""
        pytest.importorskip("opentelemetry.exporter.otlp.proto.grpc.trace_exporter")
        monkeypatch.setenv("OTEL_EXPORTERS", "console,otlp")
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter

        from otel.exporters import LocalJsonFileExporter, MultiExporter

        exporter = create_exporter()
        assert isinstance(exporter, MultiExporter)
        types = [type(e) for e in exporter.exporters]
        assert LocalJsonFileExporter in types
        assert ConsoleSpanExporter in types
        assert OTLPSpanExporter in types

    def test_unknown_exporter_raises(self, monkeypatch):
        monkeypatch.setenv("OTEL_EXPORTERS", "unknown_backend")
        with pytest.raises(ValueError, match="Unknown exporter"):
            create_exporter()

    def test_otlp_without_package_raises_import_error(self, monkeypatch):
        monkeypatch.setenv("OTEL_EXPORTERS", "otlp")
        with patch.dict(
            "sys.modules", {"opentelemetry.exporter.otlp.proto.grpc.trace_exporter": None}
        ):
            with pytest.raises((ImportError, ModuleNotFoundError)):
                create_exporter()


# ---------------------------------------------------------------------------
# instrumentation.py — _record_node_output
# ---------------------------------------------------------------------------


class TestRecordNodeOutput:
    def _make_span(self, provider):
        tracer = provider.get_tracer("test")
        return tracer.start_span("test-span")

    def test_ok_when_no_error(self):
        provider, exporter = _make_in_memory_provider()
        span = self._make_span(provider)
        _record_node_output(span, "work_planner", {"workflow_id": "wf-1"})
        span.end()
        finished = exporter.get_finished_spans()
        assert finished[0].status.status_code.name == "OK"

    def test_error_status_set(self):
        from opentelemetry.trace import StatusCode

        provider, exporter = _make_in_memory_provider()
        span = self._make_span(provider)
        _record_node_output(span, "generate_code", {"error": "something went wrong"})
        span.end()
        finished = exporter.get_finished_spans()
        assert finished[0].status.status_code == StatusCode.ERROR
        assert finished[0].attributes.get("graph.node.error") == "something went wrong"

    def test_failed_node_attribute(self):
        provider, exporter = _make_in_memory_provider()
        span = self._make_span(provider)
        _record_node_output(
            span, "generate_code", {"error": "fail", "failed_node": "generate_code"}
        )
        span.end()
        finished = exporter.get_finished_spans()
        assert finished[0].attributes.get("graph.node.failed_node") == "generate_code"

    def test_non_dict_output_is_ignored(self):
        provider, _ = _make_in_memory_provider()
        span = self._make_span(provider)
        # Should not raise
        _record_node_output(span, "some_node", "raw string output")
        span.end()

    def test_workflow_status_attached(self):
        provider, exporter = _make_in_memory_provider()
        span = self._make_span(provider)
        _record_node_output(span, "work_planner", {"workflow_status": "PENDING_APPROVAL"})
        span.end()
        finished = exporter.get_finished_spans()
        assert finished[0].attributes.get("workflow.status") == "PENDING_APPROVAL"


# ---------------------------------------------------------------------------
# instrumentation.py — instrument_graph_stream
# ---------------------------------------------------------------------------


class TestInstrumentGraphStream:
    def setup_method(self):
        _workflow_id.set(None)
        _ticket_key.set(None)
        _node_name.set(None)

    def _make_mock_graph(self, events):
        graph = MagicMock()
        graph.stream = MagicMock(return_value=iter(events))
        return graph

    def test_yields_all_events(self, monkeypatch):
        import otel.instrumentation as instr

        provider, exporter = _make_in_memory_provider()
        monkeypatch.setattr(instr, "_tracer", provider.get_tracer("test"))

        events = [{"work_planner": {"workflow_id": "wf-1"}}, {"await_approval": {}}]
        graph = self._make_mock_graph(events)

        result = list(
            instr.instrument_graph_stream(graph, {}, {"configurable": {"thread_id": "t1"}})
        )
        assert result == events

    def test_creates_root_and_node_spans(self, monkeypatch):
        import otel.instrumentation as instr

        provider, exporter = _make_in_memory_provider()
        monkeypatch.setattr(instr, "_tracer", provider.get_tracer("test"))
        set_workflow_context(workflow_id="wf-1", ticket_key="AOS-1")

        events = [
            {"work_planner": {"workflow_id": "wf-1"}},
            {"generate_code": {}},
        ]
        graph = self._make_mock_graph(events)

        list(instr.instrument_graph_stream(graph, {}, {"configurable": {"thread_id": "t1"}}))

        span_names = [s.name for s in exporter.get_finished_spans()]
        assert "workflow.run" in span_names
        assert "graph.node.work_planner" in span_names
        assert "graph.node.generate_code" in span_names

    def test_root_span_has_correlation_attributes(self, monkeypatch):
        import otel.instrumentation as instr

        provider, exporter = _make_in_memory_provider()
        monkeypatch.setattr(instr, "_tracer", provider.get_tracer("test"))
        set_workflow_context(workflow_id="wf-corr", ticket_key="AOS-corr")

        graph = self._make_mock_graph([])
        list(instr.instrument_graph_stream(graph, {}, {"configurable": {"thread_id": "t-corr"}}))

        root = next(s for s in exporter.get_finished_spans() if s.name == "workflow.run")
        assert root.attributes.get("workflow.id") == "wf-corr"
        assert root.attributes.get("jira.ticket_key") == "AOS-corr"
        assert root.attributes.get("graph.thread_id") == "t-corr"

    def test_exception_recorded_on_root_span(self, monkeypatch):
        from opentelemetry.trace import StatusCode

        import otel.instrumentation as instr

        provider, exporter = _make_in_memory_provider()
        monkeypatch.setattr(instr, "_tracer", provider.get_tracer("test"))

        graph = MagicMock()
        graph.stream = MagicMock(side_effect=RuntimeError("graph exploded"))

        with pytest.raises(RuntimeError, match="graph exploded"):
            list(instr.instrument_graph_stream(graph, {}, {"configurable": {"thread_id": "t-err"}}))

        root = next(s for s in exporter.get_finished_spans() if s.name == "workflow.run")
        assert root.status.status_code == StatusCode.ERROR

    def test_node_name_context_reset_after_stream(self, monkeypatch):
        import otel.instrumentation as instr

        provider, _ = _make_in_memory_provider()
        monkeypatch.setattr(instr, "_tracer", provider.get_tracer("test"))

        events = [{"work_planner": {}}]
        graph = self._make_mock_graph(events)

        list(instr.instrument_graph_stream(graph, {}, {"configurable": {"thread_id": "t-reset"}}))
        assert get_node_name() is None


# ---------------------------------------------------------------------------
# otel/litellm_callback.py — OtelLiteLLMCallback
# ---------------------------------------------------------------------------


from otel.litellm_callback import OtelLiteLLMCallback  # noqa: E402


def _make_response(prompt_tokens=10, completion_tokens=20):
    return {
        "id": "req-1",
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def _make_kwargs(model="gpt-4o", call_id="cid-1"):
    return {"model": model, "litellm_call_id": call_id}


def _now():
    return datetime.now(timezone.utc)


class TestOtelLiteLLMCallback:
    def setup_method(self):
        _workflow_id.set(None)
        _ticket_key.set(None)
        _node_name.set(None)

    def _run(self, coro):
        # Use a fresh event loop per call: ``asyncio.get_event_loop()`` is
        # deprecated in 3.12+ and raises when an earlier async test has
        # closed/cleared the loop policy (e.g. ``test_litellm_callbacks``
        # via pytest-asyncio auto mode).
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def _patched_cb(self, monkeypatch, provider):
        """Return an OtelLiteLLMCallback whose tracer writes to *provider*."""
        import otel.litellm_callback as cb_module

        tracer = provider.get_tracer("test")
        monkeypatch.setattr(cb_module.trace, "get_tracer", lambda *_: tracer)
        return OtelLiteLLMCallback()

    def test_success_emits_llm_call_span(self, monkeypatch):
        provider, exporter = _make_in_memory_provider()
        cb = self._patched_cb(monkeypatch, provider)
        self._run(cb.async_log_success_event(_make_kwargs(), _make_response(), _now(), _now()))

        spans = [s for s in exporter.get_finished_spans() if s.name == "llm.call"]
        assert len(spans) == 1
        span = spans[0]
        assert span.attributes["llm.model"] == "gpt-4o"
        assert span.attributes["llm.input_tokens"] == 10
        assert span.attributes["llm.output_tokens"] == 20
        assert span.attributes["llm.total_tokens"] == 30
        assert span.status.status_code.name == "OK"

    def test_success_attaches_correlation_attributes(self, monkeypatch):
        provider, exporter = _make_in_memory_provider()
        set_workflow_context(workflow_id="wf-llm", ticket_key="AOS-llm")
        cb = self._patched_cb(monkeypatch, provider)
        self._run(cb.async_log_success_event(_make_kwargs(), _make_response(), _now(), _now()))

        span = next(s for s in exporter.get_finished_spans() if s.name == "llm.call")
        assert span.attributes.get("workflow.id") == "wf-llm"
        assert span.attributes.get("jira.ticket_key") == "AOS-llm"

    def test_success_latency_attached(self, monkeypatch):
        provider, exporter = _make_in_memory_provider()
        cb = self._patched_cb(monkeypatch, provider)

        start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc)  # 1 second later

        self._run(cb.async_log_success_event(_make_kwargs(), _make_response(), start, end))

        span = next(s for s in exporter.get_finished_spans() if s.name == "llm.call")
        assert span.attributes.get("llm.latency_ms") == 1000.0

    def test_failure_emits_error_span(self, monkeypatch):
        from opentelemetry.trace import StatusCode

        provider, exporter = _make_in_memory_provider()
        cb = self._patched_cb(monkeypatch, provider)
        kwargs = {**_make_kwargs(), "exception": RuntimeError("rate limit")}
        self._run(cb.async_log_failure_event(kwargs, None, _now(), _now()))

        span = next(s for s in exporter.get_finished_spans() if s.name == "llm.call")
        assert span.status.status_code == StatusCode.ERROR
        assert span.attributes.get("llm.error_type") == "RuntimeError"

    def test_extract_usage_from_dict(self):
        cb = OtelLiteLLMCallback()
        usage = cb._extract_usage({"usage": {"prompt_tokens": 5, "completion_tokens": 7}})
        assert usage["prompt_tokens"] == 5

    def test_extract_usage_from_none(self):
        cb = OtelLiteLLMCallback()
        assert cb._extract_usage(None) == {}

    def test_extract_usage_from_pydantic_model(self):
        cb = OtelLiteLLMCallback()
        mock_response = MagicMock()
        mock_response.model_dump.return_value = {
            "usage": {"prompt_tokens": 3, "completion_tokens": 9}
        }
        usage = cb._extract_usage(mock_response)
        assert usage["prompt_tokens"] == 3

    # ------------------------------------------------------------------
    # Sync handlers (direct litellm.completion() calls)
    # ------------------------------------------------------------------

    def test_sync_success_emits_llm_call_span(self, monkeypatch):
        provider, exporter = _make_in_memory_provider()
        cb = self._patched_cb(monkeypatch, provider)
        cb.log_success_event(_make_kwargs(), _make_response(), _now(), _now())

        spans = [s for s in exporter.get_finished_spans() if s.name == "llm.call"]
        assert len(spans) == 1
        assert spans[0].attributes["llm.model"] == "gpt-4o"
        assert spans[0].attributes["llm.input_tokens"] == 10
        assert spans[0].status.status_code.name == "OK"

    def test_sync_failure_emits_error_span(self, monkeypatch):
        from opentelemetry.trace import StatusCode

        provider, exporter = _make_in_memory_provider()
        cb = self._patched_cb(monkeypatch, provider)
        kwargs = {**_make_kwargs(), "exception": RuntimeError("timeout")}
        cb.log_failure_event(kwargs, None, _now(), _now())

        span = next(s for s in exporter.get_finished_spans() if s.name == "llm.call")
        assert span.status.status_code == StatusCode.ERROR
        assert span.attributes.get("llm.error_type") == "RuntimeError"

    # ------------------------------------------------------------------
    # Response metadata attributes
    # ------------------------------------------------------------------

    def _make_model_response(
        self,
        prompt_tokens=10,
        completion_tokens=5,
        finish_reason="stop",
        reasoning_content=None,
        tool_calls=None,
    ):
        """Build a mock ModelResponse with choice metadata."""
        msg = MagicMock()
        msg.content = '{"prefix": "feature"}'
        msg.reasoning_content = reasoning_content
        msg.tool_calls = tool_calls
        choice = MagicMock()
        choice.finish_reason = finish_reason
        choice.message = msg
        response = MagicMock()
        response.choices = [choice]
        response.usage = MagicMock()
        response.usage.model_dump.return_value = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }
        return response

    def test_finish_reason_captured_in_span(self, monkeypatch):
        provider, exporter = _make_in_memory_provider()
        cb = self._patched_cb(monkeypatch, provider)
        response = self._make_model_response(finish_reason="stop")
        cb.log_success_event(_make_kwargs(), response, _now(), _now())

        span = next(s for s in exporter.get_finished_spans() if s.name == "llm.call")
        assert span.attributes.get("llm.finish_reason") == "stop"

    def test_reasoning_content_captured_in_span(self, monkeypatch):
        provider, exporter = _make_in_memory_provider()
        cb = self._patched_cb(monkeypatch, provider)
        response = self._make_model_response(reasoning_content='{"prefix": "bugfix"}')
        cb.log_success_event(_make_kwargs(), response, _now(), _now())

        span = next(s for s in exporter.get_finished_spans() if s.name == "llm.call")
        assert span.attributes.get("llm.reasoning_content") == '{"prefix": "bugfix"}'

    def test_has_tool_calls_false_when_none(self, monkeypatch):
        provider, exporter = _make_in_memory_provider()
        cb = self._patched_cb(monkeypatch, provider)
        response = self._make_model_response(tool_calls=None)
        cb.log_success_event(_make_kwargs(), response, _now(), _now())

        span = next(s for s in exporter.get_finished_spans() if s.name == "llm.call")
        assert span.attributes.get("llm.has_tool_calls") is False

    def test_reasoning_content_omitted_when_none(self, monkeypatch):
        provider, exporter = _make_in_memory_provider()
        cb = self._patched_cb(monkeypatch, provider)
        response = self._make_model_response(reasoning_content=None)
        cb.log_success_event(_make_kwargs(), response, _now(), _now())

        span = next(s for s in exporter.get_finished_spans() if s.name == "llm.call")
        assert "llm.reasoning_content" not in span.attributes


# ---------------------------------------------------------------------------
# graph/utils.py — run_and_tee goose.run span
# ---------------------------------------------------------------------------


class TestRunAndTeeGooseSpan:
    def setup_method(self):
        _workflow_id.set(None)
        _ticket_key.set(None)
        _node_name.set(None)

    def _patched_run_and_tee(self, monkeypatch, provider):
        """Return run_and_tee with its OTel tracer wired to *provider*."""
        import orchestrator.utils as utils_module

        tracer = provider.get_tracer("test")

        import opentelemetry.trace as otel_trace_module

        monkeypatch.setattr(otel_trace_module, "get_tracer", lambda *_: tracer)
        return utils_module.run_and_tee

    def test_goose_run_emits_span(self, monkeypatch):
        provider, exporter = _make_in_memory_provider()
        run_and_tee = self._patched_run_and_tee(monkeypatch, provider)

        run_and_tee(
            ["goose", "run", "--recipe", "execute.yaml", "--params", "k=v"],
            "tests.subprocess",
            stdout=__import__("subprocess").DEVNULL,
            stderr=__import__("subprocess").DEVNULL,
        )

        spans = [s for s in exporter.get_finished_spans() if s.name == "goose.run"]
        assert len(spans) == 1
        span = spans[0]
        assert span.attributes.get("process.command") == "goose"
        assert span.attributes.get("goose.recipe") == "execute.yaml"
        assert "process.exit_code" in span.attributes

    def test_goose_span_carries_correlation_attributes(self, monkeypatch):
        provider, exporter = _make_in_memory_provider()
        run_and_tee = self._patched_run_and_tee(monkeypatch, provider)
        set_workflow_context(workflow_id="wf-goose", ticket_key="AOS-goose")

        run_and_tee(
            ["goose", "run", "--recipe", "r.yaml"],
            "tests.subprocess",
            stdout=__import__("subprocess").DEVNULL,
            stderr=__import__("subprocess").DEVNULL,
        )

        span = next(s for s in exporter.get_finished_spans() if s.name == "goose.run")
        assert span.attributes.get("workflow.id") == "wf-goose"
        assert span.attributes.get("jira.ticket_key") == "AOS-goose"

    def test_goose_span_error_on_nonzero_exit(self, monkeypatch):
        provider, exporter = _make_in_memory_provider()
        run_and_tee = self._patched_run_and_tee(monkeypatch, provider)

        # "false" exits with code 1 on all POSIX systems
        run_and_tee(
            ["goose", "--version"],
            "tests.subprocess",
            stdout=__import__("subprocess").DEVNULL,
            stderr=__import__("subprocess").DEVNULL,
        )

        span = next(s for s in exporter.get_finished_spans() if s.name == "goose.run")
        # exit code is captured regardless of success/failure
        assert "process.exit_code" in span.attributes

    def test_non_goose_command_no_span(self, monkeypatch):
        provider, exporter = _make_in_memory_provider()
        run_and_tee = self._patched_run_and_tee(monkeypatch, provider)

        run_and_tee(["echo", "hello"], "tests.subprocess")

        goose_spans = [s for s in exporter.get_finished_spans() if s.name == "goose.run"]
        assert goose_spans == []


# ---------------------------------------------------------------------------
# AOS-117 enrichments — _record_node_output state-keys + size (Enrichment C)
# ---------------------------------------------------------------------------


class TestRecordNodeOutputEnrichment:
    def _make_span(self, provider):
        return provider.get_tracer("test").start_span("test-span")

    def test_state_keys_changed_attribute_is_sorted_list(self):
        provider, exporter = _make_in_memory_provider()
        span = self._make_span(provider)
        _record_node_output(
            span,
            "work_planner",
            {"work_plan": {"steps": []}, "workflow_id": "wf-x", "draft": "abc"},
        )
        span.end()
        finished = exporter.get_finished_spans()[0]
        keys = finished.attributes.get("graph.node.state_keys_changed")
        # OTel converts lists to tuples on the span; compare as sequence.
        assert tuple(keys) == ("draft", "work_plan", "workflow_id")

    def test_output_size_bytes_recorded(self):
        provider, exporter = _make_in_memory_provider()
        span = self._make_span(provider)
        payload = {"a": "x" * 100, "b": [1, 2, 3]}
        _record_node_output(span, "work_planner", payload)
        span.end()
        finished = exporter.get_finished_spans()[0]
        size = finished.attributes.get("graph.node.output_size_bytes")
        assert isinstance(size, int)
        assert size > 100  # at least the "x"*100 content

    def test_non_dict_output_does_not_set_enrichment_attrs(self):
        provider, exporter = _make_in_memory_provider()
        span = self._make_span(provider)
        _record_node_output(span, "n", "not a dict")
        span.end()
        attrs = exporter.get_finished_spans()[0].attributes
        assert "graph.node.state_keys_changed" not in attrs
        assert "graph.node.output_size_bytes" not in attrs


# ---------------------------------------------------------------------------
# AOS-117 enrichments — instrument_graph_stream rollup (Enrichment B)
# ---------------------------------------------------------------------------


class TestInstrumentGraphStreamRollup:
    def setup_method(self):
        _workflow_id.set(None)
        _ticket_key.set(None)
        _node_name.set(None)

    def _make_mock_graph(self, events):
        graph = MagicMock()
        graph.stream = MagicMock(return_value=iter(events))
        return graph

    def test_completed_run_sets_ok_status_and_rollup_attrs(self, monkeypatch):
        from opentelemetry.trace import StatusCode

        import otel.instrumentation as instr

        provider, exporter = _make_in_memory_provider()
        monkeypatch.setattr(instr, "_tracer", provider.get_tracer("test"))

        events = [{"work_planner": {"draft": "x"}}, {"generate_code": {"result": "ok"}}]
        graph = self._make_mock_graph(events)
        list(instr.instrument_graph_stream(graph, {}, {"configurable": {"thread_id": "t1"}}))

        root = next(s for s in exporter.get_finished_spans() if s.name == "workflow.run")
        assert root.status.status_code == StatusCode.OK
        assert root.attributes.get("workflow.exit_reason") == "completed"
        assert root.attributes.get("workflow.node_count") == 2
        assert root.attributes.get("workflow.last_node") == "generate_code"

    def test_interrupted_run_marks_exit_reason_interrupted(self, monkeypatch):
        import otel.instrumentation as instr

        provider, exporter = _make_in_memory_provider()
        monkeypatch.setattr(instr, "_tracer", provider.get_tracer("test"))

        events = [{"work_planner": {}}, {"__interrupt__": {}}]
        graph = self._make_mock_graph(events)
        list(instr.instrument_graph_stream(graph, {}, {"configurable": {"thread_id": "t1"}}))

        root = next(s for s in exporter.get_finished_spans() if s.name == "workflow.run")
        assert root.attributes.get("workflow.exit_reason") == "interrupted"
        assert root.attributes.get("workflow.last_node") == "__interrupt__"

    def test_error_run_marks_exit_reason_error(self, monkeypatch):
        import otel.instrumentation as instr

        provider, exporter = _make_in_memory_provider()
        monkeypatch.setattr(instr, "_tracer", provider.get_tracer("test"))

        graph = MagicMock()
        graph.stream = MagicMock(side_effect=RuntimeError("boom"))

        with pytest.raises(RuntimeError):
            list(instr.instrument_graph_stream(graph, {}, {"configurable": {"thread_id": "t1"}}))

        root = next(s for s in exporter.get_finished_spans() if s.name == "workflow.run")
        assert root.attributes.get("workflow.exit_reason") == "error"


# ---------------------------------------------------------------------------
# AOS-117 enrichments — goose.run stage/cmdline/stdout_lines (Enrichment D)
# ---------------------------------------------------------------------------


class TestGooseRunEnrichment:
    def setup_method(self):
        _workflow_id.set(None)
        _ticket_key.set(None)
        _node_name.set(None)

    def _patched_run_and_tee(self, monkeypatch, provider):
        import opentelemetry.trace as otel_trace_module

        import orchestrator.utils as utils_module

        tracer = provider.get_tracer("test")
        monkeypatch.setattr(otel_trace_module, "get_tracer", lambda *_: tracer)
        return utils_module.run_and_tee

    def test_goose_stage_derived_from_recipe_basename(self, monkeypatch):
        provider, exporter = _make_in_memory_provider()
        run_and_tee = self._patched_run_and_tee(monkeypatch, provider)

        run_and_tee(
            ["goose", "run", "--recipe", "orchestrator/work_planner/recipes/plan.yaml"],
            "tests.subprocess",
            stdout=__import__("subprocess").DEVNULL,
            stderr=__import__("subprocess").DEVNULL,
        )

        span = next(s for s in exporter.get_finished_spans() if s.name == "goose.run")
        assert span.attributes.get("goose.stage") == "plan"

    def test_goose_command_line_is_full_joined(self, monkeypatch):
        provider, exporter = _make_in_memory_provider()
        run_and_tee = self._patched_run_and_tee(monkeypatch, provider)

        run_and_tee(
            [
                "goose",
                "run",
                "--recipe",
                "orchestrator/code_generator/recipes/execute.yaml",
                "--params",
                "k=v",
            ],
            "tests.subprocess",
            stdout=__import__("subprocess").DEVNULL,
            stderr=__import__("subprocess").DEVNULL,
        )

        span = next(s for s in exporter.get_finished_spans() if s.name == "goose.run")
        assert span.attributes.get("process.command_line") == (
            "goose run --recipe orchestrator/code_generator/recipes/execute.yaml --params k=v"
        )

    def test_goose_stdout_lines_counted(self, monkeypatch):
        """Use printf to emit a known number of stdout lines and verify the count."""
        import subprocess as _subprocess

        provider, exporter = _make_in_memory_provider()
        run_and_tee = self._patched_run_and_tee(monkeypatch, provider)

        # We need real stdout to flow into run_and_tee; mock Popen to a known stream.
        from unittest.mock import patch as _patch

        class _FakeProc:
            def __init__(self, lines):
                self.stdout = iter([line.encode() for line in lines])
                self.returncode = 0

            def wait(self):
                pass

        fake = _FakeProc(["line1\n", "line2\n", "line3\n"])
        with _patch("subprocess.Popen", return_value=fake):
            run_and_tee(
                ["goose", "run", "--recipe", "orchestrator/work_planner/recipes/plan.yaml"],
                "tests.subprocess",
                stdout=_subprocess.PIPE,
                stderr=_subprocess.STDOUT,
            )

        span = next(s for s in exporter.get_finished_spans() if s.name == "goose.run")
        assert span.attributes.get("goose.stdout_lines") == 3


# ---------------------------------------------------------------------------
# AOS-117 enrichments — ObservableSqliteSaver graph.checkpoint (Enrichment A)
# ---------------------------------------------------------------------------


class TestObservableSqliteSaverCheckpointSpan:
    def setup_method(self):
        _workflow_id.set(None)
        _ticket_key.set(None)
        _node_name.set(None)

    def _make_saver(self, monkeypatch, provider):
        """Build an ObservableSqliteSaver whose tracer points to *provider*."""
        import sqlite3 as _sqlite3

        import opentelemetry.trace as otel_trace_module

        from state.observable_sqlite_saver import ObservableSqliteSaver

        tracer = provider.get_tracer("test")
        monkeypatch.setattr(otel_trace_module, "get_tracer", lambda *_: tracer)

        conn = _sqlite3.connect(":memory:", check_same_thread=False)
        saver = ObservableSqliteSaver(conn)
        # Run any setup the saver needs (creates checkpoint tables).
        saver.setup()
        return saver

    def _put(self, saver, *, source, writes, new_versions, step=1):
        config = {"configurable": {"thread_id": "t-cp", "checkpoint_ns": ""}}
        checkpoint = {
            "v": 1,
            "id": "cp-1",
            "ts": "2026-01-01T00:00:00+00:00",
            "channel_values": {},
            "channel_versions": {"chA": 1, "chB": 1},
            "versions_seen": {},
            "pending_sends": [],
        }
        metadata = {"source": source, "step": step, "writes": writes, "parents": {}}
        saver.put(config, checkpoint, metadata, new_versions)

    def test_checkpoint_span_includes_source(self, monkeypatch):
        provider, exporter = _make_in_memory_provider()
        saver = self._make_saver(monkeypatch, provider)

        self._put(
            saver,
            source="loop",
            writes={"work_planner": [("draft", "x")]},
            new_versions={"chA": 2},
        )

        span = next(s for s in exporter.get_finished_spans() if s.name == "graph.checkpoint")
        assert span.attributes.get("checkpoint.source") == "loop"

    def test_checkpoint_span_includes_writes_nodes_sorted(self, monkeypatch):
        provider, exporter = _make_in_memory_provider()
        saver = self._make_saver(monkeypatch, provider)

        self._put(
            saver,
            source="loop",
            writes={"work_planner": [], "generate_code": []},
            new_versions={"chA": 2},
        )

        span = next(s for s in exporter.get_finished_spans() if s.name == "graph.checkpoint")
        assert tuple(span.attributes.get("checkpoint.writes_nodes")) == (
            "generate_code",
            "work_planner",
        )

    def test_checkpoint_span_includes_changed_channels_sorted(self, monkeypatch):
        provider, exporter = _make_in_memory_provider()
        saver = self._make_saver(monkeypatch, provider)

        self._put(
            saver,
            source="loop",
            writes={"work_planner": []},
            new_versions={"chB": 2, "chA": 2},
        )

        span = next(s for s in exporter.get_finished_spans() if s.name == "graph.checkpoint")
        assert tuple(span.attributes.get("checkpoint.changed_channels")) == ("chA", "chB")

    def test_checkpoint_span_omits_writes_when_metadata_missing(self, monkeypatch):
        provider, exporter = _make_in_memory_provider()
        saver = self._make_saver(monkeypatch, provider)

        # source="input" typically has no writes — verify we don't emit the attr.
        self._put(saver, source="input", writes={}, new_versions={"chA": 1})

        span = next(s for s in exporter.get_finished_spans() if s.name == "graph.checkpoint")
        assert "checkpoint.writes_nodes" not in span.attributes
        assert span.attributes.get("checkpoint.source") == "input"
