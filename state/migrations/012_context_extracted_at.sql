-- Migration 012: Add workflows.context_extracted_at
-- Idempotency marker for the offline mining job (ACE Epic 1, ticket 1.2): the
-- trace reader filters on `context_extracted_at IS NULL` to find workflows
-- still eligible for extraction, and the mining runner sets it on success.
-- Idempotency is handled by the migration runner (schema_migrations tracking table),
-- so this file runs exactly once.
ALTER TABLE workflows ADD COLUMN context_extracted_at TEXT;
