-- Add CHECK constraint for valid workflow statuses
-- SQLite does not support ALTER TABLE ADD CONSTRAINT, so we recreate the table.
-- Valid statuses: pending, in_progress, completed, failed

BEGIN;

-- Create new table with CHECK constraint
CREATE TABLE IF NOT EXISTS workflows_new (
    id TEXT PRIMARY KEY,
    ticket_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending', 'in_progress', 'completed', 'failed')),
    work_plan TEXT,
    pr_url TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Copy existing data
INSERT INTO workflows_new SELECT * FROM workflows;

-- Swap tables
DROP TABLE workflows;
ALTER TABLE workflows_new RENAME TO workflows;

-- Recreate indexes
CREATE INDEX IF NOT EXISTS idx_workflows_ticket_key ON workflows(ticket_key);
CREATE INDEX IF NOT EXISTS idx_workflows_status ON workflows(status);

COMMIT;
