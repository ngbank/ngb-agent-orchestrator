-- Migration 013: Add workflows.rejection_reason column.
-- Previously the rejection reason only lived in audit_log.reason, requiring a
-- JOIN to recover it for the ACE learning extraction query (topic 07). Writing
-- it alongside the status change in update_status() makes it a first-class
-- workflow field and removes the need for that JOIN.
-- Idempotency is handled by the migration runner (schema_migrations tracking table),
-- so this file runs exactly once.
ALTER TABLE workflows ADD COLUMN rejection_reason TEXT;
