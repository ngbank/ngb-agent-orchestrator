"""OTel span exporter factory.

Selects the appropriate exporter based on ``OTEL_EXPORTER_TYPE`` env var:

  - ``console``  (default) — prints spans to stdout for Day-0 debugging.
  - ``otlp``     — sends spans to a local OTel Collector via gRPC.
                   Requires ``OTEL_EXPORTER_OTLP_ENDPOINT`` (default:
                   ``http://localhost:4317``).

Switching exporters requires only a config/env change — no code changes.
"""

from __future__ import annotations

import os

from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SpanExporter


def _otlp_endpoint() -> str:
    return os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")


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

    raise ValueError(
        f"Unknown OTEL_EXPORTER_TYPE={exporter_type!r}. " "Valid values: 'console', 'otlp'."
    )
