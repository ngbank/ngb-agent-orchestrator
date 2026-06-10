"""OTel span exporter factory.

Selects the appropriate exporter based on ``OTEL_EXPORTER_TYPE`` env var:

  - ``console``  (default) — prints spans to stdout for Day-0 debugging.
  - ``otlp``     — sends spans to a local OTel Collector via gRPC.
                   Requires ``OTEL_EXPORTER_OTLP_ENDPOINT`` (default:
                   ``http://localhost:4317``).
  - ``multi``    — emits to multiple sinks (local JSON file + console/OTLP).

Switching exporters requires only a config/env change — no code changes.

Redaction can be controlled via:
  - OTEL_REDACT_PAYLOADS: explicitly enable/disable redaction
  - OTEL_DEBUG_LOCAL: disable redaction for local debugging (see graph/otel/redaction.py)
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from threading import Lock
from typing import Sequence

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SpanExporter, SpanExportResult

from graph.otel.redaction import redact_attributes, redact_events

_JSON_EXPORT_LOCK = Lock()


def _otlp_endpoint() -> str:
    return os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")


def _otel_json_path() -> Path:
    """Get path to local OTel JSON export file."""
    default = Path(tempfile.gettempdir()) / "ngb-agent-orchestrator"
    base = Path(os.getenv("LOGS_DIR", str(default)))
    workflow_id = os.getenv("NGB_WORKFLOW_ID", "unknown")
    path = base / workflow_id
    path.mkdir(parents=True, exist_ok=True)
    return path / "otel.json"


class LocalJsonFileExporter(SpanExporter):
    """OTel span exporter that writes spans as JSON lines to a local file.

    Each span is serialized to JSON with a minimal set of fields (name, context,
    attributes, events, status) and appended to the export file as a single line.
    This format is human-readable and easy to parse for analysis.
    """

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        """Export spans to local JSON file."""
        if not spans:
            return SpanExportResult.SUCCESS

        try:
            json_path = _otel_json_path()
            with _JSON_EXPORT_LOCK:
                with json_path.open("a", encoding="utf-8") as f:
                    for span in spans:
                        span_dict = _span_to_dict(span, apply_redaction=False)
                        line = json.dumps(
                            span_dict, default=str, separators=(",", ":"), ensure_ascii=True
                        )
                        f.write(line + "\n")
            return SpanExportResult.SUCCESS
        except Exception as exc:
            print(f"Error exporting spans to JSON file: {exc}", flush=True)
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        """No-op shutdown."""
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """No-op force flush."""
        return True


def _span_to_dict(span: ReadableSpan, apply_redaction: bool = True) -> dict:
    """Convert an OTel ReadableSpan to a JSON-serializable dict.

    Args:
        span: The span to convert.
        apply_redaction: If True, apply redaction to sensitive fields (for OTLP export).
    """
    attributes = dict(span.attributes) if span.attributes else {}
    if apply_redaction:
        attributes = redact_attributes(attributes)

    events = (
        [
            {
                "name": event.name,
                "timestamp": event.timestamp,
                "attributes": dict(event.attributes) if event.attributes else {},
            }
            for event in span.events
        ]
        if span.events
        else []
    )

    if apply_redaction:
        events = redact_events(events)

    return {
        "name": span.name,
        "span_id": span.context.span_id,
        "trace_id": span.context.trace_id,
        "parent_span_id": span.parent.span_id if span.parent else None,
        "start_time": span.start_time,
        "end_time": span.end_time,
        "duration_ms": (span.end_time - span.start_time) / 1_000_000 if span.end_time else None,
        "status": {
            "status_code": span.status.status_code.name if span.status else None,
            "description": span.status.description if span.status else None,
        },
        "attributes": attributes,
        "events": events,
        "resource": (
            dict(span.resource.attributes) if span.resource and span.resource.attributes else {}
        ),
    }


class MultiExporter(SpanExporter):
    """Span exporter that fans out to multiple exporters.

    Allows emitting to multiple sinks (e.g., local JSON file + console/OTLP)
    in a single configuration. Useful for combining local debugging with
    remote monitoring.
    """

    def __init__(self, exporters: list[SpanExporter]) -> None:
        """Initialize with a list of exporters to fan out to.

        Args:
            exporters: List of SpanExporter instances to export to.
        """
        self.exporters = exporters

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        """Export to all configured exporters."""
        all_success = True
        for exporter in self.exporters:
            result = exporter.export(spans)
            if result != SpanExportResult.SUCCESS:
                all_success = False
        return SpanExportResult.SUCCESS if all_success else SpanExportResult.FAILURE

    def shutdown(self) -> None:
        """Shutdown all exporters."""
        for exporter in self.exporters:
            exporter.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """Force flush all exporters."""
        all_success = True
        for exporter in self.exporters:
            if not exporter.force_flush(timeout_millis):
                all_success = False
        return all_success


def create_exporter() -> SpanExporter:
    """Instantiate and return the configured span exporter.

    Controlled by ``OTEL_EXPORTER_TYPE`` environment variable.

    Returns:
        A ``SpanExporter`` instance ready to attach to a tracer provider.

    Raises:
        ValueError: If ``OTEL_EXPORTER_TYPE`` is set to an unknown value.
    """
    exporter_type = os.getenv("OTEL_EXPORTER_TYPE", "console").lower().strip()

    if exporter_type == "console":
        return ConsoleSpanExporter()

    if exporter_type == "otlp":
        # Lazily imported so the OTLP gRPC dependency is only required when
        # explicitly configured — keeps the console-only path dependency-free.
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )
        except ImportError as exc:
            raise ImportError(
                "OTLP exporter requires 'opentelemetry-exporter-otlp-proto-grpc'. "
                "Install it with: pip install opentelemetry-exporter-otlp-proto-grpc"
            ) from exc

        endpoint = _otlp_endpoint()
        return OTLPSpanExporter(endpoint=endpoint, insecure=True)

    if exporter_type == "multi":
        # Multi-export: local JSON file + console output
        return MultiExporter(
            [
                LocalJsonFileExporter(),
                ConsoleSpanExporter(),
            ]
        )

    raise ValueError(
        f"Unknown OTEL_EXPORTER_TYPE={exporter_type!r}. "
        "Valid values: 'console', 'otlp', 'multi'."
    )
