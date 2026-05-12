-- Migration 005: Extend workflows status constraint to include 'pending_workplan_clarification'
-- SQLite does not support ALTER TABLE ... MODIFY COLUMN, so we recreate the table.
-- Idempotent: wrapped via migration runner tracking; runs exactly once.

CREATE TABLE IF NOT EXISTS workflows_new (
    id TEXT PRIMARY KEY,
    ticket_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
        'pending',
        'in_progress',
        'pending_workplan_clarification',
        'pending_approval',
        'approved',
        'rejected',
        'completed',
        'failed',
        'cancelled'
    )),
    work_plan TEXT,
    pr_url TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    execution_summary TEXT
);

-- Copy existing rows
INSERT OR IGNORE INTO workflows_new SELECT * FROM workflows;

-- Drop old table and rename
DROP TABLE IF EXISTS workflows;
ALTER TABLE workflows_new RENAME TO workflows;

-- Recreate indexes
CREATE INDEX IF NOT EXISTS idx_workflows_ticket_key ON workflows(ticket_key);
CREATE INDEX IF NOT EXISTS idx_workflows_status ON workflows(status);
