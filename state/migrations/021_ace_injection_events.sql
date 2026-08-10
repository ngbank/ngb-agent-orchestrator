-- Migration 021: Record every ACE context-injection event.
--
-- Events retain the rendered block identity and exact injection metadata for
-- offline utilization analysis. Blocks are never deleted as part of event
-- retention, preserving event-to-provenance joins.

CREATE TABLE ace_injection_events (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id        TEXT    NOT NULL,
    ticket_key         TEXT,
    injection_point    TEXT    NOT NULL,
    synthesizer        TEXT    NOT NULL,
    block_cache_key    TEXT,
    retrieved_item_ids TEXT    NOT NULL DEFAULT '[]',
    rendered_length    INTEGER NOT NULL,
    created_at         TEXT    NOT NULL
);

CREATE INDEX idx_ace_injection_events_workflow_id
    ON ace_injection_events(workflow_id);
CREATE INDEX idx_ace_injection_events_ticket_key
    ON ace_injection_events(ticket_key);
CREATE INDEX idx_ace_injection_events_created_at
    ON ace_injection_events(created_at);
CREATE INDEX idx_ace_injection_events_block_cache_key
    ON ace_injection_events(block_cache_key);
