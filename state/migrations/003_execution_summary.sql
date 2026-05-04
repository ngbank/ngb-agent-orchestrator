-- Migration 003: Add execution_summary column for storing Goose execute recipe output
-- Idempotency is handled by the migration runner (schema_migrations tracking table),
-- so this file runs exactly once.
ALTER TABLE workflows ADD COLUMN execution_summary TEXT;  -- JSON blob, nullable
