-- Initial schema for workflow state tracking
-- This is the consolidated baseline schema (merged from migrations 001-008).
-- It is idempotent and can be run multiple times safely.

-- Workflows table: tracks workflow execution state for each JIRA ticket run
CREATE TABLE IF NOT EXISTS workflows (
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
    work_plan TEXT,              -- JSON blob containing the work plan
    pr_url TEXT,                 -- Pull request URL when available
    created_at TEXT NOT NULL,    -- ISO 8601 timestamp
    updated_at TEXT NOT NULL,    -- ISO 8601 timestamp
    execution_summary TEXT,      -- JSON blob, nullable
    usage_summary TEXT,          -- JSON blob, nullable
    retry_count INTEGER NOT NULL DEFAULT 0,
    clarification_history TEXT   -- JSON blob, nullable
);

-- Audit log table: append-only log of all workflow state changes
CREATE TABLE IF NOT EXISTS audit_log (
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    actor TEXT NOT NULL,         -- Who/what performed the action
    action TEXT NOT NULL,        -- Action performed (e.g., "status_change")
    reason TEXT,                 -- Optional reason for the action
    created_at TEXT NOT NULL,    -- ISO 8601 timestamp
    FOREIGN KEY (workflow_id) REFERENCES workflows(id)
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_workflows_ticket_key ON workflows(ticket_key);
CREATE INDEX IF NOT EXISTS idx_workflows_status ON workflows(status);
CREATE INDEX IF NOT EXISTS idx_audit_log_workflow_id ON audit_log(workflow_id);
