-- Migration 014: Add context_items and context_items_staged (ACE-owned)
-- Live store and staging quality-gate for ACE context items, per the schema
-- in docs/ACE/11-ace-orchestrator-data-model.md. ACE owns these tables so
-- the orchestrator's workflows schema stays untouched. provenance is a JSON
-- array of evidence links back to the workflow(s) an item was learned from;
-- see the design doc for the entry structure and rationale for the JSON
-- column as a bootstrap (not a normalised table) for the first version.
-- Idempotency is handled by the migration runner (schema_migrations tracking table),
-- so this file runs exactly once.

CREATE TABLE IF NOT EXISTS context_items (
    id               TEXT PRIMARY KEY,                -- UUID
    pattern_type     TEXT NOT NULL,                    -- 'approach'|'concern'|'test_coverage'|'implementation'
    scope            TEXT NOT NULL,                    -- 'task_type'|'file_pattern'|'codebase_wide'
    scope_value      TEXT,                             -- e.g. 'state_machine_change', 'state/migrations/**'
    description      TEXT NOT NULL,                    -- The rendered pattern text (human + LLM readable)
    confidence       REAL NOT NULL DEFAULT 0.5,        -- 0.0-1.0; drives filtering and tier labelling
    occurrence_count INTEGER NOT NULL DEFAULT 1,
    last_validated   TEXT NOT NULL,                    -- ISO 8601; set to SOURCE DATE, not extraction date
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'active',    -- 'active'|'staged'|'deprecated'|'conflicted'
    provenance       TEXT NOT NULL DEFAULT '[]'         -- JSON array of evidence links
);

CREATE INDEX IF NOT EXISTS idx_context_items_pattern_type    ON context_items(pattern_type);
CREATE INDEX IF NOT EXISTS idx_context_items_scope           ON context_items(scope, scope_value);
CREATE INDEX IF NOT EXISTS idx_context_items_confidence      ON context_items(confidence);
CREATE INDEX IF NOT EXISTS idx_context_items_status          ON context_items(status);
CREATE INDEX IF NOT EXISTS idx_context_items_last_validated  ON context_items(last_validated);

-- Identical to context_items plus review_notes, promoted_at, rejected_at.
-- Staged items hold status = 'staged'. Rows are never hard-deleted;
-- rejected_at marks rejection while preserving the record for audit.
CREATE TABLE IF NOT EXISTS context_items_staged (
    id               TEXT PRIMARY KEY,
    pattern_type     TEXT NOT NULL,
    scope            TEXT NOT NULL,
    scope_value      TEXT,
    description      TEXT NOT NULL,
    confidence       REAL NOT NULL DEFAULT 0.5,
    occurrence_count INTEGER NOT NULL DEFAULT 1,
    last_validated   TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'staged',
    provenance       TEXT NOT NULL DEFAULT '[]',
    review_notes     TEXT,                              -- human reviewer annotations (e.g. scope narrowing)
    promoted_at      TEXT,                               -- set when promoted to context_items; NULL if not yet
    rejected_at      TEXT                                -- set when rejected from staging; NULL otherwise
);
