-- Migration 017: Trim the Curator to a quality-gate + exact-dedup safety net.
--
-- Semantic consolidation of context items moves from write time (Curator) to
-- read time (injection-time synthesizer). See
-- docs/ACE/15-ace-injection-synthesizer.md for the full rationale.
--
-- Schema changes:
--
-- 1. Add `conflicts_with` JSON array column to both tables. Populated by the
--    Curator when it detects opposing guidance on similar subjects, replacing
--    the previous `status='conflicted'` blocking behaviour. The array holds
--    ids of other items that contradict this one; retrieval passes these ids
--    through to the synthesizer so it can present both angles explicitly.
--
-- 2. Drop `occurrence_count`. Under the trimmed Curator the merge path no
--    longer increments this counter — every row would be stuck at 1 forever,
--    inviting incorrect retrieval-scoring queries. Evidence-count is derivable
--    from `len(provenance)` at read time; any future cross-workflow strength
--    signal will use a semantically distinct column name.
--
-- Idempotency is handled by the migration runner (schema_migrations tracking
-- table), so this file runs exactly once. SQLite ALTER TABLE DROP COLUMN
-- requires SQLite ≥ 3.35 (2021); the project's Python 3.13 stdlib bundles
-- SQLite ≥ 3.40 which is well above that threshold.

ALTER TABLE context_items        ADD COLUMN conflicts_with TEXT NOT NULL DEFAULT '[]';
ALTER TABLE context_items_staged ADD COLUMN conflicts_with TEXT NOT NULL DEFAULT '[]';

ALTER TABLE context_items        DROP COLUMN occurrence_count;
ALTER TABLE context_items_staged DROP COLUMN occurrence_count;
