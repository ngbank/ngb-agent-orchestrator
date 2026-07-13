-- Migration 016: Applicability dimensions on context items.
-- Adds three orthogonal nullable columns to both context_items and
-- context_items_staged so mined items can be pinned to a specific project
-- (typically the JIRA project short-name, e.g. 'AOS'), repo, or platform
-- (python|dotnet|jvm|...). NULL on any column means "applies to all values
-- on that axis" — the safe default that preserves existing rows without a
-- backfill.
--
-- The `project` column is deliberately not named `project_key`: it is a
-- scope tag, not a foreign key. Values are free-form strings sourced from
-- the workflow context (currently JIRA project short-names, but the schema
-- does not couple to JIRA).
--
-- Motivation and semantics: see docs/ACE/11-ace-orchestrator-data-model.md
-- (applicability dimensions section) and AOS-268. Retrieval-time filtering
-- against these columns lands with Epic 4 tickets — this migration only
-- widens the storage shape.
--
-- Column vocabulary aligns with config/project-setup.json, which already
-- uses `platform` for python / dotnet / jvm.
--
-- Idempotency is handled by the migration runner (schema_migrations tracking
-- table), so this file runs exactly once. SQLite ALTER TABLE ADD COLUMN is
-- irreversible without a table rebuild, so no down-migration is provided.

ALTER TABLE context_items ADD COLUMN project  TEXT;
ALTER TABLE context_items ADD COLUMN repo     TEXT;
ALTER TABLE context_items ADD COLUMN platform TEXT;

ALTER TABLE context_items_staged ADD COLUMN project  TEXT;
ALTER TABLE context_items_staged ADD COLUMN repo     TEXT;
ALTER TABLE context_items_staged ADD COLUMN platform TEXT;

CREATE INDEX IF NOT EXISTS idx_context_items_applicability
    ON context_items(project, repo, platform);
CREATE INDEX IF NOT EXISTS idx_context_items_staged_applicability
    ON context_items_staged(project, repo, platform);
