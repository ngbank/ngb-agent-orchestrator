"""OTel span redaction for sensitive data.

This module provides utilities to redact sensitive fields from OTel spans
before exporting to remote endpoints. Redaction is controlled by the
OTEL_REDACT_PAYLOADS and OTEL_DEBUG_LOCAL environment variables.

Sensitive fields redacted:
  - LLM API keys and credentials
  - Full prompt/completion text (replaced with token counts)
  - User input payloads
  - Tool call responses

Debug mode (OTEL_DEBUG_LOCAL=true) bypasses redaction for local troubleshooting.
"""

import os
from typing import Any


def should_redact() -> bool:
    """Check if redaction should be enabled.

    Returns True if:
      - OTEL_REDACT_PAYLOADS=true (explicit enable), OR
      - OTEL_EXPORTER_TYPE is "otlp" (remote export)

    Returns False if:
      - OTEL_DEBUG_LOCAL=true (debug mode disables redaction), OR
      - Explicitly disabled with OTEL_REDACT_PAYLOADS=false
    """
    # Debug mode disables redaction
    if os.getenv("OTEL_DEBUG_LOCAL", "").lower() in ("true", "1", "yes"):
        return False

    # Check explicit redaction setting
    redact_payloads = os.getenv("OTEL_REDACT_PAYLOADS", "").lower()
    if redact_payloads in ("true", "1", "yes"):
        return True
    if redact_payloads in ("false", "0", "no"):
        return False

    # Default: enable redaction for OTLP (remote) exporter, disable for others
    exporter_type = os.getenv("OTEL_EXPORTER_TYPE", "console").lower()
    return exporter_type in ("otlp", "multi")


def redact_attributes(attributes: dict[str, Any]) -> dict[str, Any]:
    """Redact sensitive fields from OTel span attributes.

    Args:
        attributes: The span attributes dict.

    Returns:
        A new dict with sensitive fields redacted (if redaction is enabled).
    """
    if not should_redact() or not attributes:
        return attributes

    redacted = dict(attributes)

    # Redact LLM-related sensitive fields
    sensitive_keys = {
        "llm.request.api_key",
        "llm.request.credentials",
        "llm.request.auth",
        "llm.request.prompt",
        "llm.request.messages",
        "llm.response.content",
        "llm.response.text",
        "llm.tool_use.input",
        "llm.tool_use.output",
        "user.input",
        "user.payload",
    }

    for key in sensitive_keys:
        if key in redacted:
            redacted[key] = "<redacted>"

    # Redact nested attributes with "request" or "response" in the key
    for key in list(redacted.keys()):
        if (
            "request" in key.lower() or "response" in key.lower() or "payload" in key.lower()
        ) and isinstance(redacted[key], (str, dict)):
            if isinstance(redacted[key], str) and len(str(redacted[key])) > 100:
                redacted[key] = f"<redacted: {len(str(redacted[key]))} chars>"
            elif isinstance(redacted[key], dict):
                redacted[key] = "<redacted>"

    return redacted


def redact_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Redact sensitive fields from OTel span events.

    Args:
        events: The list of span events.

    Returns:
        A new list with sensitive fields redacted (if redaction is enabled).
    """
    if not should_redact() or not events:
        return events

    redacted_events = []
    for event in events:
        redacted_event = dict(event)
        if "attributes" in redacted_event:
            redacted_event["attributes"] = redact_attributes(redacted_event.get("attributes", {}))
        redacted_events.append(redacted_event)

    return redacted_events
