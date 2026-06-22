"""OTel span exporter factory.

File logging (``LocalJsonFileExporter``) is always on -- spans are written
as JSON lines to ``LOGS_DIR/<workflow_id>/otel.jsonl`` regardless of any other
exporter configuration. ``workflow_id`` is read from each span's
``workflow.id`` attribute (set by ``otel.context.OtelContext``); spans
emitted outside any workflow context fall back to ``LOGS_DIR/unknown/otel.jsonl``.

Additional exporters are controlled by the ``OTEL_EXPORTERS`` env var, a
comma-separated list of zero or more of:

    - ``console``  -- prints spans to stdout.
    - ``otlp``     -- sends spans to a local OTel Collector via gRPC.
                                     Requires ``OTEL_EXPORTER_OTLP_ENDPOINT`` (default:
                                     ``http://localhost:4317``).

Supported combinations::

    OTEL_EXPORTERS=console        -- file + stdout
    OTEL_EXPORTERS=otlp           -- file + remote collector
    OTEL_EXPORTERS=console,otlp   -- file + stdout + remote collector
    OTEL_EXPORTERS=               -- file only (no forwarding)

Redaction can be controlled via:
    - OTEL_REDACT_PAYLOADS: explicitly enable/disable redaction (default: true)
    - OTEL_DEBUG_LOCAL: disable redaction for local debugging (see otel/redaction.py)
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path
from threading import Lock
from typing import Sequence

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SpanExporter, SpanExportResult

from orchestrator.paths import logs_base_dir
from otel.redaction import redact_attributes, redact_events

_JSON_EXPORT_LOCK = Lock()
_UNKNOWN_WORKFLOW_ID = "unknown"


def _otlp_endpoint() -> str:
    return os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")


def _logs_base_dir() -> Path:
    """Return the base directory for run logs."""
    return logs_base_dir()


def _otel_json_path_for(workflow_id: str) -> Path:
    """Return the ``otel.jsonl`` path for a specific workflow id, creating the dir."""
    path = _logs_base_dir() / workflow_id
    path.mkdir(parents=True, exist_ok=True)
    return path / "otel.jsonl"


def _span_workflow_id(span: ReadableSpan) -> str:
    """Extract the workflow id from a span's attributes, falling back to ``unknown``."""
    attrs = span.attributes or {}
    wf_id = attrs.get("workflow.id")
    if not wf_id:
        return _UNKNOWN_WORKFLOW_ID
    return str(wf_id)


class LocalJsonFileExporter(SpanExporter):
    """OTel span exporter that writes spans as JSON lines to a local file.

    Each span is serialized to JSON with a minimal set of fields (name, context,
    attributes, events, status) and appended to the export file as a single line.
    This format is human-readable and easy to parse for analysis.

    Spans are routed to ``LOGS_DIR/<workflow_id>/otel.jsonl`` based on the
    ``workflow.id`` attribute on each span. Spans without that attribute fall
    back to ``LOGS_DIR/unknown/otel.jsonl``. A single ``export()`` batch may
    contain spans for multiple workflows; they are grouped and written to the
    correct per-workflow file.
    """

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        """Export spans to per-workflow local JSON files."""
        if not spans:
            return SpanExportResult.SUCCESS

        groups: dict[str, list[ReadableSpan]] = defaultdict(list)
        for span in spans:
            groups[_span_workflow_id(span)].append(span)

        try:
            with _JSON_EXPORT_LOCK:
                for workflow_id, group_spans in groups.items():
                    json_path = _otel_json_path_for(workflow_id)
                    with json_path.open("a", encoding="utf-8") as f:
                        for span in group_spans:
                            span_dict = _span_to_dict(span, apply_redaction=False)
                            line = json.dumps(
                                span_dict,
                                default=str,
                                separators=(",", ":"),
                                ensure_ascii=True,
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
        "span_id": span.context.span_id if span.context else None,
        "trace_id": span.context.trace_id if span.context else None,
        "parent_span_id": span.parent.span_id if span.parent else None,
        "start_time": span.start_time,
        "end_time": span.end_time,
        "duration_ms": (
            (span.end_time - span.start_time) / 1_000_000
            if span.end_time is not None and span.start_time is not None
            else None
        ),
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

    ``LocalJsonFileExporter`` is always included -- file logging is unconditional.
    Additional exporters are read from the ``OTEL_EXPORTERS`` environment variable
    (comma-separated list of ``console`` and/or ``otlp``).

    Returns:
        A ``SpanExporter`` instance ready to attach to a tracer provider.
        Returns a ``MultiExporter`` when more than one exporter is active.

    Raises:
        ValueError: If ``OTEL_EXPORTERS`` contains an unknown exporter name.
        ImportError: If ``otlp`` is requested but the gRPC package is not installed.
    """
    exporters: list[SpanExporter] = [LocalJsonFileExporter()]

    raw = os.getenv("OTEL_EXPORTERS", "").strip()
    names = [n.strip().lower() for n in raw.split(",") if n.strip()]

    for name in names:
        if name == "console":
            exporters.append(ConsoleSpanExporter())
        elif name == "otlp":
            # Lazily imported so the OTLP gRPC dependency is only required when
            # explicitly configured — keeps the file-only path dependency-free.
            try:
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (  # noqa: E501  # pyright: ignore[reportMissingImports]
                    OTLPSpanExporter,
                )
            except ImportError as exc:
                raise ImportError(
                    "OTLP exporter requires 'opentelemetry-exporter-otlp-proto-grpc'. "
                    "Install it with: pip install opentelemetry-exporter-otlp-proto-grpc"
                ) from exc
            exporters.append(OTLPSpanExporter(endpoint=_otlp_endpoint(), insecure=True))
        else:
            raise ValueError(
                f"Unknown exporter {name!r} in OTEL_EXPORTERS. " "Valid values: 'console', 'otlp'."
            )

    if len(exporters) == 1:
        return exporters[0]
    return MultiExporter(exporters)
