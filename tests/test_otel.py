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
        set_node_context("execute_plan")
        ctx = OtelContext.capture()
        assert ctx.workflow_id == "wf-2"
        assert ctx.ticket_key == "AOS-2"
        assert ctx.node_name == "execute_plan"

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
        _record_node_output(span, "execute_plan", {"error": "something went wrong"})
        span.end()
        finished = exporter.get_finished_spans()
        assert finished[0].status.status_code == StatusCode.ERROR
        assert finished[0].attributes.get("graph.node.error") == "something went wrong"

    def test_failed_node_attribute(self):
        provider, exporter = _make_in_memory_provider()
        span = self._make_span(provider)
        _record_node_output(span, "execute_plan", {"error": "fail", "failed_node": "execute_plan"})
        span.end()
        finished = exporter.get_finished_spans()
        assert finished[0].attributes.get("graph.node.failed_node") == "execute_plan"

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
            {"execute_plan": {}},
        ]
        graph = self._make_mock_graph(events)

        list(instr.instrument_graph_stream(graph, {}, {"configurable": {"thread_id": "t1"}}))

        span_names = [s.name for s in exporter.get_finished_spans()]
        assert "workflow.run" in span_names
        assert "graph.node.work_planner" in span_names
        assert "graph.node.execute_plan" in span_names

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
        return asyncio.get_event_loop().run_until_complete(coro)

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
        import graph.utils as utils_module

        tracer = provider.get_tracer("test")

        import opentelemetry.trace as otel_trace_module

        monkeypatch.setattr(otel_trace_module, "get_tracer", lambda *_: tracer)
        return utils_module.run_and_tee

    def test_goose_run_emits_span(self, monkeypatch, tmp_path):
        provider, exporter = _make_in_memory_provider()
        run_and_tee = self._patched_run_and_tee(monkeypatch, provider)

        log_file = tmp_path / "test.log"
        with log_file.open("w") as lf:
            run_and_tee(
                ["goose", "run", "--recipe", "execute.yaml", "--params", "k=v"],
                lf,
                stdout=__import__("subprocess").DEVNULL,
                stderr=__import__("subprocess").DEVNULL,
            )

        spans = [s for s in exporter.get_finished_spans() if s.name == "goose.run"]
        assert len(spans) == 1
        span = spans[0]
        assert span.attributes.get("process.command") == "goose"
        assert span.attributes.get("goose.recipe") == "execute.yaml"
        assert "process.exit_code" in span.attributes

    def test_goose_span_carries_correlation_attributes(self, monkeypatch, tmp_path):
        provider, exporter = _make_in_memory_provider()
        run_and_tee = self._patched_run_and_tee(monkeypatch, provider)
        set_workflow_context(workflow_id="wf-goose", ticket_key="AOS-goose")

        log_file = tmp_path / "test.log"
        with log_file.open("w") as lf:
            run_and_tee(
                ["goose", "run", "--recipe", "r.yaml"],
                lf,
                stdout=__import__("subprocess").DEVNULL,
                stderr=__import__("subprocess").DEVNULL,
            )

        span = next(s for s in exporter.get_finished_spans() if s.name == "goose.run")
        assert span.attributes.get("workflow.id") == "wf-goose"
        assert span.attributes.get("jira.ticket_key") == "AOS-goose"

    def test_goose_span_error_on_nonzero_exit(self, monkeypatch, tmp_path):
        provider, exporter = _make_in_memory_provider()
        run_and_tee = self._patched_run_and_tee(monkeypatch, provider)

        log_file = tmp_path / "test.log"
        with log_file.open("w") as lf:
            # "false" exits with code 1 on all POSIX systems
            run_and_tee(
                ["goose", "--version"],
                lf,
                stdout=__import__("subprocess").DEVNULL,
                stderr=__import__("subprocess").DEVNULL,
            )

        span = next(s for s in exporter.get_finished_spans() if s.name == "goose.run")
        # exit code is captured regardless of success/failure
        assert "process.exit_code" in span.attributes

    def test_non_goose_command_no_span(self, monkeypatch, tmp_path):
        provider, exporter = _make_in_memory_provider()
        run_and_tee = self._patched_run_and_tee(monkeypatch, provider)

        log_file = tmp_path / "test.log"
        with log_file.open("w") as lf:
            run_and_tee(["echo", "hello"], lf)

        goose_spans = [s for s in exporter.get_finished_spans() if s.name == "goose.run"]
        assert goose_spans == []
