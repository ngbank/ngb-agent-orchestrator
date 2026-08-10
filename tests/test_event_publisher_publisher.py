"""Unit tests for orchestrator/event_publisher/publisher.py and events.py."""

import json
import sys
from unittest.mock import MagicMock

import pytest

from orchestrator.event_publisher import publisher as publisher_module
from orchestrator.event_publisher.events import ExecutionStatusEvent


@pytest.fixture(autouse=True)
def fake_azure_servicebus():
    """Inject a lightweight fake azure.servicebus into sys.modules for the duration of each test.

    This avoids a hard dependency on the installed azure-servicebus wheel in
    tests that exercise the lazy-import path inside EventPublisher.publish().
    """
    mock_module = MagicMock()
    mock_module.ServiceBusMessage = MagicMock(side_effect=lambda body, **kw: MagicMock(body=body))
    mock_module.ServiceBusClient = MagicMock()

    original_azure = sys.modules.get("azure")
    original_sb = sys.modules.get("azure.servicebus")

    if original_azure is None:
        sys.modules["azure"] = MagicMock()
    sys.modules["azure.servicebus"] = mock_module

    yield mock_module

    # Restore
    if original_sb is None:
        sys.modules.pop("azure.servicebus", None)
    else:
        sys.modules["azure.servicebus"] = original_sb
    if original_azure is None:
        sys.modules.pop("azure", None)


# ---------------------------------------------------------------------------
# ExecutionStatusEvent
# ---------------------------------------------------------------------------


class TestExecutionStatusEventToJson:
    def test_required_fields_present(self):
        event = ExecutionStatusEvent(
            execution_id="exec-1",
            event_type="execution.started",
            orchestrator_workflow_id="wf-1",
            status="RUNNING",
        )
        payload = json.loads(event.to_json())
        assert payload["executionId"] == "exec-1"
        assert payload["eventType"] == "execution.started"
        assert payload["orchestratorWorkflowId"] == "wf-1"
        assert payload["status"] == "RUNNING"
        assert "eventId" in payload  # auto-generated

    def test_optional_fields_omitted_when_none(self):
        event = ExecutionStatusEvent(
            execution_id="exec-1",
            event_type="approval.pending",
            orchestrator_workflow_id="wf-1",
        )
        payload = json.loads(event.to_json())
        assert "status" not in payload
        assert "prUrl" not in payload
        assert "errorMessage" not in payload
        assert "ticketId" not in payload
        assert "workPlan" not in payload

    def test_optional_fields_included_when_set(self):
        event = ExecutionStatusEvent(
            execution_id="exec-1",
            event_type="execution.completed",
            orchestrator_workflow_id="wf-1",
            status="SUCCEEDED",
            pr_url="https://github.com/org/repo/pull/42",
            error_message=None,
            ticket_id="STAG-100",
            work_plan={"status": "pass", "tasks": []},
        )
        payload = json.loads(event.to_json())
        assert payload["prUrl"] == "https://github.com/org/repo/pull/42"
        assert payload["ticketId"] == "STAG-100"
        assert payload["workPlan"] == {"status": "pass", "tasks": []}

    def test_event_id_is_unique(self):
        e1 = ExecutionStatusEvent(execution_id="x", event_type="e", orchestrator_workflow_id="y")
        e2 = ExecutionStatusEvent(execution_id="x", event_type="e", orchestrator_workflow_id="y")
        assert e1.event_id != e2.event_id


# ---------------------------------------------------------------------------
# EventPublisher
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_publisher_singleton():
    """Ensure each test starts with a fresh singleton state."""
    original = publisher_module._publisher_instance
    publisher_module._publisher_instance = None
    yield
    publisher_module._publisher_instance = original


class TestGetPublisherNoOp:
    def test_returns_noop_when_env_var_absent(self, monkeypatch):
        monkeypatch.delenv("AZURE_SERVICE_BUS_CONNECTION_STRING", raising=False)
        p = publisher_module.get_publisher()
        assert isinstance(p, publisher_module._NoopPublisher)

    def test_returns_noop_when_env_var_empty(self, monkeypatch):
        monkeypatch.setenv("AZURE_SERVICE_BUS_CONNECTION_STRING", "  ")
        p = publisher_module.get_publisher()
        assert isinstance(p, publisher_module._NoopPublisher)

    def test_noop_publish_does_not_raise(self, monkeypatch):
        monkeypatch.delenv("AZURE_SERVICE_BUS_CONNECTION_STRING", raising=False)
        p = publisher_module.get_publisher()
        event = ExecutionStatusEvent(execution_id="x", event_type="e", orchestrator_workflow_id="y")
        p.publish(event)  # must not raise


class TestEventPublisherPublish:
    def _make_publisher(self, mock_sender):
        p = publisher_module.EventPublisher(
            connection_string="Endpoint=sb://fake.servicebus.windows.net/;SharedAccessKeyName=k;SharedAccessKey=s=",
            topic="execution-events",
        )
        p._sender = mock_sender
        return p

    def test_sends_message_with_json_body(self, fake_azure_servicebus):
        mock_sender = MagicMock()
        p = self._make_publisher(mock_sender)

        event = ExecutionStatusEvent(
            execution_id="exec-1",
            event_type="execution.started",
            orchestrator_workflow_id="wf-1",
            status="RUNNING",
        )

        p.publish(event)

        mock_sender.send_messages.assert_called_once()
        sent_body = mock_sender.send_messages.call_args[0][0].body
        payload = json.loads(sent_body)
        assert payload["executionId"] == "exec-1"
        assert payload["eventType"] == "execution.started"

    def test_publish_failure_does_not_raise(self):
        mock_sender = MagicMock()
        mock_sender.send_messages.side_effect = RuntimeError("network error")
        p = self._make_publisher(mock_sender)

        event = ExecutionStatusEvent(execution_id="x", event_type="e", orchestrator_workflow_id="y")

        p.publish(event)  # must not raise

    def test_get_publisher_creates_event_publisher_when_conn_string_set(self, monkeypatch):
        monkeypatch.setenv(
            "AZURE_SERVICE_BUS_CONNECTION_STRING",
            "Endpoint=sb://fake.servicebus.windows.net/;SharedAccessKeyName=k;SharedAccessKey=s=",
        )
        monkeypatch.setenv("AZURE_SERVICE_BUS_EXECUTION_EVENTS_TOPIC", "my-topic")

        # Singleton is None (reset by autouse fixture)
        p = publisher_module.get_publisher()
        assert isinstance(p, publisher_module.EventPublisher)
        assert p._topic == "my-topic"

    def test_get_publisher_defaults_topic_when_not_set(self, monkeypatch):
        monkeypatch.setenv(
            "AZURE_SERVICE_BUS_CONNECTION_STRING",
            "Endpoint=sb://fake.servicebus.windows.net/;SharedAccessKeyName=k;SharedAccessKey=s=",
        )
        monkeypatch.delenv("AZURE_SERVICE_BUS_EXECUTION_EVENTS_TOPIC", raising=False)

        p = publisher_module.get_publisher()
        assert isinstance(p, publisher_module.EventPublisher)
        assert p._topic == "execution-events"
