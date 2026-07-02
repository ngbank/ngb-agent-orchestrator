-- Migration 011: Rename workflows.execution_summary to workflows.code_generation_summary
-- Aligns the column with the generate_code stage naming (AOS-194) now that the
-- "execute" terminology has been fully retired (AOS-195).
-- Idempotency is handled by the migration runner (schema_migrations tracking table),
-- so this file runs exactly once.
ALTER TABLE workflows RENAME COLUMN execution_summary TO code_generation_summary;
