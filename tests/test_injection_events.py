"""Tests for best-effort ACE injection-event persistence."""

from unittest.mock import patch

from ace.telemetry.injection_events import record_injection_event
from state.sqlite_state_store import get_connection, run_migrations


def test_record_injection_event_persists_metadata():
    run_migrations()
    record_injection_event(
        workflow_id="workflow-1",
        ticket_key="AOS-239",
        injection_point="planner",
        synthesizer="synthesizer",
        block_cache_key="block-key",
        retrieved_item_ids=["item-1", "item-2"],
        rendered_length=42,
    )

    conn = get_connection()
    row = conn.execute("SELECT * FROM ace_injection_events").fetchone()
    conn.close()
    assert row["workflow_id"] == "workflow-1"
    assert row["ticket_key"] == "AOS-239"
    assert row["synthesizer"] == "synthesizer"
    assert row["block_cache_key"] == "block-key"
    assert row["retrieved_item_ids"] == '["item-1", "item-2"]'
    assert row["rendered_length"] == 42


def test_record_injection_event_swallows_database_errors(caplog):
    with patch(
        "ace.telemetry.injection_events.get_connection", side_effect=RuntimeError("offline")
    ):
        record_injection_event(
            workflow_id="workflow-1",
            ticket_key="AOS-239",
            injection_point="planner",
            synthesizer="flat_list",
            block_cache_key=None,
            retrieved_item_ids=[],
            rendered_length=0,
        )

    assert "Failed to persist ACE injection event" in caplog.text
