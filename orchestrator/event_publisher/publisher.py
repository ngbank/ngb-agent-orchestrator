"""Fire-and-forget publisher to Azure Service Bus execution-events topic.

Publishing is feature-flagged: when AZURE_SERVICE_BUS_CONNECTION_STRING is
unset or empty the publisher is a no-op. Publish failures are always logged and
never re-raised so graph execution is never blocked (per ADR-0001).
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from orchestrator.event_publisher.events import ExecutionStatusEvent

logger = logging.getLogger(__name__)

_ENV_CONNECTION_STRING = "AZURE_SERVICE_BUS_CONNECTION_STRING"
_ENV_TOPIC = "AZURE_SERVICE_BUS_EXECUTION_EVENTS_TOPIC"
_DEFAULT_TOPIC = "execution-events"

_publisher_instance: Optional["EventPublisher"] = None


class EventPublisher:
    """Thin wrapper around azure.servicebus that publishes execution-events messages.

    Lazily initialises the ServiceBusClient on first publish call so that the
    orchestrator starts up without network access to ASB.
    """

    def __init__(self, connection_string: str, topic: str) -> None:
        self._connection_string = connection_string
        self._topic = topic
        self._sender: object = None  # azure.servicebus.ServiceBusSender, or None

    def _get_sender(self) -> object:
        if self._sender is None:
            from azure.servicebus import ServiceBusClient

            client = ServiceBusClient.from_connection_string(self._connection_string)
            self._sender = client.get_topic_sender(topic_name=self._topic)
            logger.debug("ASB topic sender initialised: topic=%s", self._topic)
        return self._sender

    def publish(self, event: ExecutionStatusEvent) -> None:
        """Serialise event and send to the topic. Never raises."""
        try:
            from azure.servicebus import ServiceBusMessage

            sender = self._get_sender()
            message = ServiceBusMessage(event.to_json(), content_type="application/json")
            sender.send_messages(message)  # type: ignore[attr-defined]
            logger.debug(
                "ASB event published: eventType=%s executionId=%s eventId=%s",
                event.event_type,
                event.execution_id,
                event.event_id,
            )
        except Exception:
            logger.exception(
                "ASB publish failed (non-fatal): eventType=%s executionId=%s",
                event.event_type,
                event.execution_id,
            )

    def close(self) -> None:
        if self._sender is not None:
            try:
                self._sender.close()  # type: ignore[attr-defined]
            except Exception:
                logger.debug("ASB sender close error (ignored)")
            self._sender = None


class _NoopPublisher:
    """Placeholder used when the connection string is absent."""

    def publish(self, event: ExecutionStatusEvent) -> None:  # noqa: ARG002
        pass

    def close(self) -> None:
        pass


def get_publisher() -> "EventPublisher | _NoopPublisher":
    """Return the module-level publisher singleton, creating it on first call."""
    global _publisher_instance
    if _publisher_instance is None:
        connection_string = os.environ.get(_ENV_CONNECTION_STRING, "").strip()
        if not connection_string:
            logger.debug("AZURE_SERVICE_BUS_CONNECTION_STRING not set — ASB publishing disabled")
            return _NoopPublisher()
        topic = os.environ.get(_ENV_TOPIC, "").strip() or _DEFAULT_TOPIC
        _publisher_instance = EventPublisher(connection_string, topic)
    return _publisher_instance
