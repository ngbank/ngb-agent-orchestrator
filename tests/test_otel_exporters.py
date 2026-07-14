"""Tests for OTel exporters (multi-export, local JSON, redaction)."""

import json
import os
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from opentelemetry.sdk.trace.export import SpanExportResult

from otel.exporters import LocalJsonFileExporter, MultiExporter, create_exporter
from otel.redaction import redact_attributes, should_redact

DEFAULT_TEST_WORKFLOW_ID = "test-workflow-123"


@pytest.fixture
def logs_base_dir():
    """Create a temporary base LOGS_DIR for exporter tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        original_logs_dir = os.environ.get("LOGS_DIR")
        os.environ["LOGS_DIR"] = tmpdir

        # NGB_WORKFLOW_ID is intentionally unset — routing comes from span
        # attributes now. Strip it so leaked env doesn't mask bugs.
        original_workflow_id = os.environ.pop("NGB_WORKFLOW_ID", None)

        yield Path(tmpdir)

        if original_logs_dir is not None:
            os.environ["LOGS_DIR"] = original_logs_dir
        elif "LOGS_DIR" in os.environ:
            del os.environ["LOGS_DIR"]

        if original_workflow_id is not None:
            os.environ["NGB_WORKFLOW_ID"] = original_workflow_id


@pytest.fixture
def temp_logs_dir(logs_base_dir):
    """Backwards-compatible fixture: returns the per-workflow log dir for the
    default test workflow id used by ``MockSpan``."""
    return logs_base_dir / DEFAULT_TEST_WORKFLOW_ID


class MockSpan:
    """Mock OTel ReadableSpan for testing."""

    def __init__(
        self, name="test.span", attributes=None, events=None, workflow_id=DEFAULT_TEST_WORKFLOW_ID
    ):
        self.name = name
        self.context = MagicMock()
        self.context.trace_id = 0x123456789
        self.context.span_id = 0x987654321
        self.parent = None
        self.start_time = 1000000000
        self.end_time = 1000001000
        self.status = MagicMock()
        self.status.status_code = MagicMock()
        self.status.status_code.name = "OK"
        self.status.description = None
        merged: dict = dict(attributes or {})
        # Allow callers to override workflow.id via attributes, or omit it
        # entirely by passing workflow_id=None.
        if "workflow.id" not in merged and workflow_id is not None:
            merged["workflow.id"] = workflow_id
        self.attributes = merged
        self.events = events or []
        self.resource = MagicMock()
        self.resource.attributes = {"service.name": "test-service"}


class TestLocalJsonFileExporter:
    """Tests for LocalJsonFileExporter."""

    def test_export_writes_json_lines(self, temp_logs_dir):
        """Test that exporter writes spans as JSON lines."""
        exporter = LocalJsonFileExporter()

        # Create mock spans
        span1 = MockSpan(name="test.span1", attributes={"key1": "value1"})
        span2 = MockSpan(name="test.span2", attributes={"key2": "value2"})

        # Export
        result = exporter.export([span1, span2])

        # Verify result
        assert result == SpanExportResult.SUCCESS

        # Verify JSON file was created and contains lines
        json_path = temp_logs_dir / "otel.jsonl"
        assert json_path.exists()

        with json_path.open("r") as f:
            lines = f.readlines()

        assert len(lines) == 2

        # Verify each line is valid JSON
        span_data1 = json.loads(lines[0])
        span_data2 = json.loads(lines[1])

        assert span_data1["name"] == "test.span1"
        assert span_data1["attributes"]["key1"] == "value1"
        assert span_data2["name"] == "test.span2"
        assert span_data2["attributes"]["key2"] == "value2"

    def test_export_appends_to_existing_file(self, temp_logs_dir):
        """Test that exporter appends to existing JSON file."""
        # Create initial file with one line
        json_path = temp_logs_dir / "otel.jsonl"
        json_path.parent.mkdir(parents=True, exist_ok=True)
        with json_path.open("w") as f:
            f.write('{"name": "existing.span"}\n')

        exporter = LocalJsonFileExporter()

        # Export new span
        span = MockSpan(name="new.span")
        exporter.export([span])

        # Verify file has 2 lines
        with json_path.open("r") as f:
            lines = f.readlines()

        assert len(lines) == 2
        first = json.loads(lines[0])
        second = json.loads(lines[1])

        assert first["name"] == "existing.span"
        assert second["name"] == "new.span"

    def test_export_empty_list(self, temp_logs_dir):
        """Test that exporter handles empty span list."""
        exporter = LocalJsonFileExporter()

        result = exporter.export([])

        assert result == SpanExportResult.SUCCESS
        # File should not exist if no spans were exported
        # (or exist but be empty)

    def test_export_failure_logs_error(self, temp_logs_dir, caplog, monkeypatch):
        """Export failures are logged, not printed."""
        exporter = LocalJsonFileExporter()
        span = MockSpan(name="test.span")

        def _boom(_workflow_id):
            raise OSError("disk full")

        monkeypatch.setattr("otel.exporters._otel_json_path_for", _boom)

        with caplog.at_level("ERROR", logger="otel.exporters"):
            result = exporter.export([span])

        assert result == SpanExportResult.FAILURE
        assert any(
            record.levelname == "ERROR" and "disk full" in record.getMessage()
            for record in caplog.records
        )

    def test_export_routes_by_workflow_id_attribute(self, logs_base_dir):
        """Spans land in LOGS_DIR/<workflow.id>/otel.jsonl based on the span attribute.

        Regression: previously the path was derived from the
        ``NGB_WORKFLOW_ID`` env var, which was never set on the dispatcher
        process, so every span fell back to ``unknown/otel.jsonl``.
        """
        exporter = LocalJsonFileExporter()

        span = MockSpan(name="wf.span", workflow_id="wf-abc")

        result = exporter.export([span])

        assert result == SpanExportResult.SUCCESS
        json_path = logs_base_dir / "wf-abc" / "otel.jsonl"
        assert json_path.exists()
        with json_path.open() as f:
            lines = f.readlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["name"] == "wf.span"

        # Nothing should have been written to the unknown bucket.
        assert not (logs_base_dir / "unknown" / "otel.jsonl").exists()

    def test_export_splits_mixed_batch_by_workflow_id(self, logs_base_dir):
        """A single export() batch with multiple workflow ids splits per file."""
        exporter = LocalJsonFileExporter()

        spans = [
            MockSpan(name="a.1", workflow_id="wf-a"),
            MockSpan(name="b.1", workflow_id="wf-b"),
            MockSpan(name="a.2", workflow_id="wf-a"),
        ]

        result = exporter.export(spans)

        assert result == SpanExportResult.SUCCESS

        path_a = logs_base_dir / "wf-a" / "otel.jsonl"
        path_b = logs_base_dir / "wf-b" / "otel.jsonl"
        assert path_a.exists()
        assert path_b.exists()

        names_a = [json.loads(line)["name"] for line in path_a.read_text().splitlines()]
        names_b = [json.loads(line)["name"] for line in path_b.read_text().splitlines()]

        assert names_a == ["a.1", "a.2"]
        assert names_b == ["b.1"]

    def test_export_falls_back_to_unknown_when_attribute_missing(self, logs_base_dir):
        """Spans without a workflow.id attribute land in unknown/otel.jsonl."""
        exporter = LocalJsonFileExporter()

        # workflow_id=None tells MockSpan not to inject the attribute.
        span = MockSpan(name="orphan.span", workflow_id=None)
        assert "workflow.id" not in span.attributes

        result = exporter.export([span])

        assert result == SpanExportResult.SUCCESS
        json_path = logs_base_dir / "unknown" / "otel.jsonl"
        assert json_path.exists()
        with json_path.open() as f:
            lines = f.readlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["name"] == "orphan.span"

    def test_export_treats_empty_workflow_id_as_unknown(self, logs_base_dir):
        """An empty-string workflow.id attribute is treated as missing."""
        exporter = LocalJsonFileExporter()

        span = MockSpan(name="empty.wf", attributes={"workflow.id": ""}, workflow_id=None)

        result = exporter.export([span])

        assert result == SpanExportResult.SUCCESS
        assert (logs_base_dir / "unknown" / "otel.jsonl").exists()


class TestMultiExporter:
    """Tests for MultiExporter."""

    def test_multi_exporter_calls_all_exporters(self):
        """Test that MultiExporter calls all configured exporters."""
        # Create mock exporters
        mock_exporter1 = MagicMock()
        mock_exporter1.export.return_value = SpanExportResult.SUCCESS
        mock_exporter2 = MagicMock()
        mock_exporter2.export.return_value = SpanExportResult.SUCCESS

        multi = MultiExporter([mock_exporter1, mock_exporter2])

        # Create mock span
        span = MockSpan()

        # Export
        result = multi.export([span])

        # Verify both exporters were called
        mock_exporter1.export.assert_called_once_with([span])
        mock_exporter2.export.assert_called_once_with([span])
        assert result == SpanExportResult.SUCCESS

    def test_multi_exporter_shutdown(self):
        """Test that MultiExporter shuts down all exporters."""
        mock_exporter1 = MagicMock()
        mock_exporter2 = MagicMock()

        multi = MultiExporter([mock_exporter1, mock_exporter2])
        multi.shutdown()

        mock_exporter1.shutdown.assert_called_once()
        mock_exporter2.shutdown.assert_called_once()

    def test_multi_exporter_force_flush(self):
        """Test that MultiExporter flushes all exporters."""
        mock_exporter1 = MagicMock()
        mock_exporter1.force_flush.return_value = True
        mock_exporter2 = MagicMock()
        mock_exporter2.force_flush.return_value = True

        multi = MultiExporter([mock_exporter1, mock_exporter2])
        result = multi.force_flush(timeout_millis=1000)

        assert result is True
        mock_exporter1.force_flush.assert_called_once_with(1000)
        mock_exporter2.force_flush.assert_called_once_with(1000)


class TestRedaction:
    """Tests for span redaction controls."""

    def test_should_redact_debug_mode_disables(self):
        """Test that OTEL_DEBUG_LOCAL=true disables redaction."""
        original = os.environ.get("OTEL_DEBUG_LOCAL")

        os.environ["OTEL_DEBUG_LOCAL"] = "true"
        try:
            assert should_redact() is False
        finally:
            if original:
                os.environ["OTEL_DEBUG_LOCAL"] = original
            elif "OTEL_DEBUG_LOCAL" in os.environ:
                del os.environ["OTEL_DEBUG_LOCAL"]

    def test_should_redact_otlp_exporter_defaults_true(self):
        """Test that redaction defaults to True regardless of exporter type."""
        for key in ["OTEL_REDACT_PAYLOADS", "OTEL_DEBUG_LOCAL", "OTEL_EXPORTERS"]:
            os.environ.pop(key, None)

        try:
            # Default: redact is True (secure by default)
            assert should_redact() is True
        finally:
            pass

    def test_redact_attributes_removes_sensitive_fields(self):
        """Test that redaction removes sensitive LLM fields."""
        attributes = {
            "llm.request.api_key": "sk-secret-key",
            "llm.request.prompt": "Secret prompt text",
            "llm.response.content": "Response with sensitive data",
            "safe_field": "This should remain",
            "user.input": "User provided input",
            "workflow.id": "workflow-123",
        }

        # Enable redaction
        original = os.environ.get("OTEL_REDACT_PAYLOADS")
        os.environ["OTEL_REDACT_PAYLOADS"] = "true"

        try:
            redacted = redact_attributes(attributes)

            # Sensitive fields should be redacted
            assert redacted["llm.request.api_key"] == "<redacted>"
            assert redacted["llm.request.prompt"] == "<redacted>"
            assert redacted["llm.response.content"] == "<redacted>"
            assert redacted["user.input"] == "<redacted>"

            # Safe fields should remain
            assert redacted["safe_field"] == "This should remain"
            assert redacted["workflow.id"] == "workflow-123"
        finally:
            if original:
                os.environ["OTEL_REDACT_PAYLOADS"] = original
            elif "OTEL_REDACT_PAYLOADS" in os.environ:
                del os.environ["OTEL_REDACT_PAYLOADS"]

    def test_redact_attributes_debug_mode_preserves_fields(self):
        """Test that debug mode preserves sensitive fields."""
        attributes = {
            "llm.request.api_key": "sk-secret-key",
            "llm.request.prompt": "Secret prompt text",
            "workflow.id": "workflow-123",
        }

        original_debug = os.environ.get("OTEL_DEBUG_LOCAL")
        os.environ["OTEL_DEBUG_LOCAL"] = "true"

        try:
            redacted = redact_attributes(attributes)

            # In debug mode, all fields should be preserved
            assert redacted["llm.request.api_key"] == "sk-secret-key"
            assert redacted["llm.request.prompt"] == "Secret prompt text"
            assert redacted["workflow.id"] == "workflow-123"
        finally:
            if original_debug:
                os.environ["OTEL_DEBUG_LOCAL"] = original_debug
            elif "OTEL_DEBUG_LOCAL" in os.environ:
                del os.environ["OTEL_DEBUG_LOCAL"]


class TestCreateExporter:
    """Tests for create_exporter backend routing and validation."""

    def _install_http_exporter_stub(self, monkeypatch):
        module = types.ModuleType("opentelemetry.exporter.otlp.proto.http.trace_exporter")
        factory = MagicMock(name="OTLPSpanExporter")
        module.OTLPSpanExporter = factory
        monkeypatch.setitem(
            sys.modules, "opentelemetry.exporter.otlp.proto.http.trace_exporter", module
        )
        return factory

    def test_create_exporter_betterstack_uses_bearer_header(self, monkeypatch):
        mock_http_exporter = self._install_http_exporter_stub(monkeypatch)

        monkeypatch.setenv("OTEL_EXPORTERS", "betterstack")
        monkeypatch.delenv("OTEL_BETTERSTACK_INSECURE", raising=False)
        monkeypatch.setenv(
            "OTEL_BETTERSTACK_ENDPOINT", "https://s2532474.eu-fsn-3.betterstackdata.com"
        )
        monkeypatch.setenv("OTEL_BETTERSTACK_SOURCE_TOKEN", "token-123")

        exporter = create_exporter()

        assert isinstance(exporter, MultiExporter)
        mock_http_exporter.assert_called_once_with(
            endpoint="https://s2532474.eu-fsn-3.betterstackdata.com/v1/traces",
            headers={"Authorization": "Bearer token-123"},
            session=None,
        )

    def test_create_exporter_betterstack_insecure_disables_tls_verification(self, monkeypatch):
        mock_http_exporter = self._install_http_exporter_stub(monkeypatch)

        monkeypatch.setenv("OTEL_EXPORTERS", "betterstack")
        monkeypatch.setenv("OTEL_BETTERSTACK_SOURCE_TOKEN", "token-123")
        monkeypatch.setenv("OTEL_BETTERSTACK_INSECURE", "true")

        create_exporter()

        session = mock_http_exporter.call_args.kwargs["session"]
        assert session is not None
        assert type(session).__name__ == "_InsecureSession"

    def test_create_exporter_betterstack_preserves_explicit_path(self, monkeypatch):
        mock_http_exporter = self._install_http_exporter_stub(monkeypatch)

        monkeypatch.setenv("OTEL_EXPORTERS", "betterstack")
        monkeypatch.delenv("OTEL_BETTERSTACK_INSECURE", raising=False)
        monkeypatch.setenv(
            "OTEL_BETTERSTACK_ENDPOINT", "https://in-otel.logs.betterstack.com/v1/traces"
        )
        monkeypatch.setenv("OTEL_BETTERSTACK_SOURCE_TOKEN", "token-123")

        create_exporter()

        assert mock_http_exporter.call_args.kwargs["endpoint"] == (
            "https://in-otel.logs.betterstack.com/v1/traces"
        )

    def test_create_exporter_elastic_uses_apikey_header(self, monkeypatch):
        mock_http_exporter = self._install_http_exporter_stub(monkeypatch)

        monkeypatch.setenv("OTEL_EXPORTERS", "elastic")
        monkeypatch.setenv("OTEL_ELASTIC_ENDPOINT", "https://elastic.example.local/v1/traces")
        monkeypatch.setenv("OTEL_ELASTIC_API_KEY", "api-key-123")

        exporter = create_exporter()

        assert isinstance(exporter, MultiExporter)
        mock_http_exporter.assert_called_once_with(
            endpoint="https://elastic.example.local/v1/traces",
            headers={"Authorization": "ApiKey api-key-123"},
        )

    def test_create_exporter_betterstack_requires_source_token(self, monkeypatch):
        self._install_http_exporter_stub(monkeypatch)

        monkeypatch.setenv("OTEL_EXPORTERS", "betterstack")
        monkeypatch.delenv("OTEL_BETTERSTACK_SOURCE_TOKEN", raising=False)

        with pytest.raises(ValueError, match="OTEL_BETTERSTACK_SOURCE_TOKEN"):
            create_exporter()

    def test_create_exporter_elastic_requires_endpoint(self, monkeypatch):
        self._install_http_exporter_stub(monkeypatch)

        monkeypatch.setenv("OTEL_EXPORTERS", "elastic")
        monkeypatch.delenv("OTEL_ELASTIC_ENDPOINT", raising=False)
        monkeypatch.setenv("OTEL_ELASTIC_API_KEY", "api-key-123")

        with pytest.raises(ValueError, match="OTEL_ELASTIC_ENDPOINT"):
            create_exporter()

    def test_create_exporter_elastic_requires_api_key(self, monkeypatch):
        self._install_http_exporter_stub(monkeypatch)

        monkeypatch.setenv("OTEL_EXPORTERS", "elastic")
        monkeypatch.setenv("OTEL_ELASTIC_ENDPOINT", "https://elastic.example.local/v1/traces")
        monkeypatch.delenv("OTEL_ELASTIC_API_KEY", raising=False)

        with pytest.raises(ValueError, match="OTEL_ELASTIC_API_KEY"):
            create_exporter()

    def test_create_exporter_betterstack_console_combination(self, monkeypatch):
        mock_http_exporter = self._install_http_exporter_stub(monkeypatch)

        monkeypatch.setenv("OTEL_EXPORTERS", "betterstack,console")
        monkeypatch.setenv("OTEL_BETTERSTACK_SOURCE_TOKEN", "token-123")

        exporter = create_exporter()

        assert isinstance(exporter, MultiExporter)
        assert len(exporter.exporters) == 3
        mock_http_exporter.assert_called_once()
