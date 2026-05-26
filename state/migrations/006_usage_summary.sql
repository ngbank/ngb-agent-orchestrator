-- Migration 006: Add usage_summary column for storing per-stage LLM token usage and turn count.
-- Idempotency is handled by the migration runner (schema_migrations tracking table),
-- so this file runs exactly once.
ALTER TABLE workflows ADD COLUMN usage_summary TEXT;  -- JSON blob, nullable
