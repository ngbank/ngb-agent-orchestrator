-- Migration 007: Add retry_count column to track how many times a workflow has been retried.
-- Idempotency is handled by the migration runner (schema_migrations tracking table),
-- so this file runs exactly once.
ALTER TABLE workflows ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0;
