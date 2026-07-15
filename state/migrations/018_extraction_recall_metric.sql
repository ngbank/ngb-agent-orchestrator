-- Migration 018: Per-comment Reflector recall metric (ACE-owned).
-- Adds two nullable counters to context_extraction_log so each mined workflow
-- records how much of its PR-review feedback the Reflector actually cited:
--   comment_units       — number of reviewer comment paragraphs shown to the
--                         Reflector (numbered pr_comment_N units in the payload)
--   comment_units_cited — how many distinct units appear in candidate evidence
-- Recall = comment_units_cited / comment_units, aggregated across the corpus.
-- NULL on both columns means the Reflector never ran for that workflow (the
-- Evaluator verdict was skip/flag), as opposed to 0 which means "ran and cited
-- nothing". Motivation: AOS-272 — infra/tooling critiques were silently
-- dropped and there was no measurement to detect it.
--
-- Idempotency is handled by the migration runner (schema_migrations tracking
-- table), so this file runs exactly once. SQLite ALTER TABLE ADD COLUMN is
-- irreversible without a table rebuild, so no down-migration is provided.

ALTER TABLE context_extraction_log ADD COLUMN comment_units       INTEGER;
ALTER TABLE context_extraction_log ADD COLUMN comment_units_cited INTEGER;
