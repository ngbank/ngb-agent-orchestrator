"""Tests for OTel exporters (multi-export, local JSON, redaction)."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from opentelemetry.sdk.trace.export import SpanExportResult

from otel.exporters import LocalJsonFileExporter, MultiExporter
from otel.redaction import redact_attributes, should_redact


@pytest.fixture
def temp_logs_dir():
    """Create a temporary directory for logs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        original_logs_dir = os.environ.get("LOGS_DIR")
        original_workflow_id = os.environ.get("NGB_WORKFLOW_ID")

        os.environ["LOGS_DIR"] = tmpdir
        os.environ["NGB_WORKFLOW_ID"] = "test-workflow-123"

        yield Path(tmpdir) / "test-workflow-123"

        # Cleanup
        if original_logs_dir:
            os.environ["LOGS_DIR"] = original_logs_dir
        elif "LOGS_DIR" in os.environ:
            del os.environ["LOGS_DIR"]

        if original_workflow_id:
            os.environ["NGB_WORKFLOW_ID"] = original_workflow_id
        elif "NGB_WORKFLOW_ID" in os.environ:
            del os.environ["NGB_WORKFLOW_ID"]


class MockSpan:
    """Mock OTel ReadableSpan for testing."""

    def __init__(self, name="test.span", attributes=None, events=None):
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
        self.attributes = attributes or {}
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
        json_path = temp_logs_dir / "otel.json"
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
        json_path = temp_logs_dir / "otel.json"
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
    """Tests for span redaction (AOS-113: redaction controls)."""

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
