-- Migration 012: Add context_extraction_log (ACE-owned)
-- Idempotency ledger for the offline mining job (ACE Epic 1, ticket 1.2): the
-- trace reader anti-joins against this table to find workflows not yet mined,
-- and the mining runner inserts a row on success.
-- ACE owns this table so the orchestrator's workflows schema stays untouched;
-- workflow_id is a soft reference (no FK) so the ledger survives orchestrator
-- storage changes and a future move to event-driven completion signals.
-- Idempotency is handled by the migration runner (schema_migrations tracking table),
-- so this file runs exactly once.
CREATE TABLE IF NOT EXISTS context_extraction_log (
    workflow_id  TEXT PRIMARY KEY,
    extracted_at TEXT NOT NULL  -- ISO 8601 timestamp
);
