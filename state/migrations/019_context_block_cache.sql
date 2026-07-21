-- Migration 019: Add context_block_cache table for the injection-time synthesizer.
--
-- The synthesizer (ace/retrieval/synthesizer.py) renders a set of retrieved
-- ContextItems into a structured markdown document via one LLM call. To avoid
-- paying that cost on every workflow run the output is cached per unique
-- (ticket_key, applicability_filter, corpus_snapshot_id, recipe_target) tuple.
-- Corpus change invalidation is implicit: when any matching item's updated_at
-- advances, corpus_snapshot_id changes, producing a new cache key.
--
-- Columns:
--   cache_key            — SHA-256 hex of (ticket_key, filter_predicate,
--                          corpus_snapshot_id, recipe_target). PRIMARY KEY.
--   rendered_markdown    — The synthesizer's output document.
--   provenance_manifest  — JSON object mapping section name to list of source
--                          ContextItem ids, e.g.
--                          {"development_rules": ["id1","id2"], ...}.
--   ticket_key           — Stored for human-readable diagnostics; not used
--                          as a lookup key.
--   recipe_target        — "planner" | "code_generator" | "pr_rerun".
--   input_item_ids       — JSON array of ContextItem ids fed to the LLM.
--   created_at           — ISO-8601 UTC timestamp of first synthesis.
--
-- Idempotency is handled by the migration runner (schema_migrations tracking
-- table), so this file runs exactly once.

CREATE TABLE context_block_cache (
    cache_key           TEXT    PRIMARY KEY,
    rendered_markdown   TEXT    NOT NULL,
    provenance_manifest TEXT    NOT NULL DEFAULT '{}',
    ticket_key          TEXT,
    recipe_target       TEXT,
    input_item_ids      TEXT    NOT NULL DEFAULT '[]',
    created_at          TEXT    NOT NULL
);
