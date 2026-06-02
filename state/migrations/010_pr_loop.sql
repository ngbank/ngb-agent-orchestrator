-- Migration 010: Add PR review loop support.
-- Adds pr_comments and pr_approval_decision columns, and extends the status
-- CHECK constraint to include pending_pr_approval and pr_commented.
-- SQLite does not support ALTER TABLE ... MODIFY COLUMN, so we recreate the table.

-- Create the new table with extended status constraint and new columns
CREATE TABLE IF NOT EXISTS workflows_new (
    id TEXT PRIMARY KEY,
    ticket_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
        'pending',
        'in_progress',
        'pending_workplan_clarification',
        'pending_approval',
        'pending_pr_approval',
        'pr_commented',
        'approved',
        'rejected',
        'completed',
        'failed',
        'cancelled'
    )),
    work_plan TEXT,
    pr_url TEXT,
    pr_comments TEXT,
    pr_approval_decision TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    execution_summary TEXT,
    usage_summary TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    clarification_history TEXT
);

-- Copy existing rows explicitly mapping old columns to new columns.
-- New columns (pr_comments, pr_approval_decision) default to NULL.
INSERT OR IGNORE INTO workflows_new (
    id, ticket_key, status, work_plan, pr_url,
    pr_comments, pr_approval_decision,
    created_at, updated_at,
    execution_summary, usage_summary, retry_count, clarification_history
)
SELECT
    id, ticket_key, status, work_plan, pr_url,
    NULL, NULL,
    created_at, updated_at,
    execution_summary, usage_summary, retry_count, clarification_history
FROM workflows;

-- Drop the old table and rename the new one into place
DROP TABLE IF EXISTS workflows;
ALTER TABLE workflows_new RENAME TO workflows;

-- Recreate indexes
CREATE INDEX IF NOT EXISTS idx_workflows_ticket_key ON workflows(ticket_key);
CREATE INDEX IF NOT EXISTS idx_workflows_status ON workflows(status);
