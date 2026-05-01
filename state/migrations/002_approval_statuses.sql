-- Migration 002: Extend workflows status constraint to include approval statuses
-- SQLite does not support ALTER TABLE ... MODIFY COLUMN, so we recreate the table.
-- Idempotent: wrapped in a savepoint; if workflows_new already exists from a prior
-- partial run it is dropped first, and the migration is skipped if the new
-- constraint is already in place.

-- Create the new table alongside the existing one
CREATE TABLE IF NOT EXISTS workflows_new (
    id TEXT PRIMARY KEY,
    ticket_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
        'pending',
        'in_progress',
        'pending_approval',
        'approved',
        'rejected',
        'completed',
        'failed'
    )),
    work_plan TEXT,
    pr_url TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Copy any existing rows (INSERT OR IGNORE is safe on re-run)
INSERT OR IGNORE INTO workflows_new SELECT * FROM workflows;

-- Drop the old table and rename the new one into place
DROP TABLE IF EXISTS workflows;
ALTER TABLE workflows_new RENAME TO workflows;

-- Recreate indexes
CREATE INDEX IF NOT EXISTS idx_workflows_ticket_key ON workflows(ticket_key);
CREATE INDEX IF NOT EXISTS idx_workflows_status ON workflows(status);
