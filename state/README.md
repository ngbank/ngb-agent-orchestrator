# State Store

SQLite-based state tracking system for workflow execution. Each workflow record maps to one JIRA ticket run and stores status, timestamps, work plan (JSON blob), and PR URL.

## Features

- **Persistent State**: SQLite database for reliable state storage
- **Migration-Based Schema**: Version-controlled schema changes
- **Idempotent Migrations**: Safe to run multiple times
- **Audit Trail**: Append-only audit log for complete traceability
- **Environment Configuration**: Flexible database path configuration

## Quick Start

### Setup

The database is automatically initialized when you first import the module. By default, it creates `state/local.db`.

```python
from state import state_store

# Database is ready to use!
```

### Configuration

Set the database path via environment variable (optional):

```bash
# .env file
DB_PATH=state/local.db
```

If not set, defaults to `state/local.db`.

## Usage

### Create a Workflow

```python
from state import create_workflow

# Create a new workflow for a JIRA ticket
workflow_id = create_workflow(
    ticket_key="AOS-35",
    work_plan={
        "tasks": [
            {"id": 1, "title": "Create schema", "status": "completed"},
            {"id": 2, "title": "Implement API", "status": "in-progress"}
        ],
        "metadata": {
            "priority": "P0",
            "estimate": "S"
        }
    },
    status="pending"
)

print(f"Created workflow: {workflow_id}")
```

### Update Workflow Status

```python
from state import update_status

# Update status (creates audit log entry)
update_status(
    workflow_id=workflow_id,
    status="in_progress",
    actor="copilot",
    reason="Started implementation"
)

# Update with PR URL
update_status(
    workflow_id=workflow_id,
    status="completed",
    pr_url="https://github.com/org/repo/pull/123",
    actor="copilot",
    reason="PR created and merged"
)
```

### Retrieve Workflow

```python
from state import get_workflow, get_workflow_by_ticket

# Get by workflow ID
workflow = get_workflow(workflow_id)
print(f"Status: {workflow['status']}")
print(f"Work Plan: {workflow['work_plan']}")

# Get all workflows for a ticket
workflows = get_workflow_by_ticket("AOS-35")
for wf in workflows:
    print(f"{wf['id']}: {wf['status']}")
```

### View Audit Log

```python
from state import get_audit_log

# Get complete audit trail
audit_log = get_audit_log(workflow_id)
for entry in audit_log:
    print(f"{entry['created_at']}: {entry['action']} by {entry['actor']}")
    if entry['reason']:
        print(f"  Reason: {entry['reason']}")
```

## Schema

### workflows table

```sql
CREATE TABLE workflows (
    id TEXT PRIMARY KEY,           -- UUID
    ticket_key TEXT NOT NULL,      -- JIRA ticket (e.g., "AOS-35")
    status TEXT NOT NULL,          -- Current status
    work_plan TEXT,                -- JSON blob
    pr_url TEXT,                   -- Pull request URL
    created_at TEXT NOT NULL,      -- ISO 8601 timestamp
    updated_at TEXT NOT NULL       -- ISO 8601 timestamp
);
```

### audit_log table

```sql
CREATE TABLE audit_log (
    id TEXT PRIMARY KEY,           -- UUID
    workflow_id TEXT NOT NULL,     -- References workflows.id
    actor TEXT NOT NULL,           -- Who performed the action
    action TEXT NOT NULL,          -- Action type
    reason TEXT,                   -- Optional reason
    created_at TEXT NOT NULL,      -- ISO 8601 timestamp
    FOREIGN KEY (workflow_id) REFERENCES workflows(id)
);
```

## Migration Management

Migrations are automatically run when the module is imported. To run manually:

```python
from state import run_migrations

run_migrations()
```

Migrations are idempotent - running them multiple times is safe and won't cause errors.

## Testing

Run the test suite:

```bash
pytest tests/test_state_store.py -v
```

Test coverage includes:
- Workflow creation and retrieval
- Status updates
- Audit log creation
- JSON serialization/deserialization
- Migration idempotency
- Error handling

## Design Decisions

### Append-Only Audit Log

The audit log is append-only by design. There are no delete operations exposed in the API, ensuring a complete historical record of all workflow changes.

### JSON Blob for Work Plans

Work plans are stored as JSON blobs for flexibility. This allows storing complex nested structures without defining rigid schemas upfront.

### UTC Timestamps

All timestamps use UTC timezone to avoid ambiguity across different environments.

### SQLite Choice

SQLite was chosen for:
- Zero configuration
- No separate server process
- ACID compliance
- Good performance for the expected load
- Easy backup (single file)

### Limitations

- **Concurrency**: SQLite has limited concurrency. For high-concurrency scenarios, consider PostgreSQL.
- **File-based**: Database is a single file. Ensure proper backup procedures.
- **No Multi-Master**: Cannot replicate across multiple nodes without additional tooling.

## File Structure

```
state/
├── __init__.py                    # Module exports
├── state_store.py                 # Core implementation
├── migrations/                    # SQL migrations
│   ├── __init__.py
│   └── 001_initial_schema.sql    # Initial schema
└── local.db                       # Database file (gitignored)
```

## API Reference

### create_workflow(ticket_key, work_plan=None, status="pending")

Create a new workflow record.

**Parameters:**
- `ticket_key` (str): JIRA ticket key
- `work_plan` (dict, optional): Work plan dictionary (will be JSON-serialized)
- `status` (str, optional): Initial status (default: "pending")

**Returns:** str - Workflow ID (UUID)

### update_status(workflow_id, status, pr_url=None, actor="system", reason=None)

Update workflow status and optionally PR URL. Creates audit log entry.

**Parameters:**
- `workflow_id` (str): Workflow UUID
- `status` (str): New status value
- `pr_url` (str, optional): Pull request URL
- `actor` (str, optional): Who performed the update (default: "system")
- `reason` (str, optional): Reason for the update

**Returns:** None

### get_workflow(workflow_id)

Retrieve workflow by ID.

**Parameters:**
- `workflow_id` (str): Workflow UUID

**Returns:** dict or None - Workflow data with deserialized work_plan

### get_workflow_by_ticket(ticket_key)

Retrieve all workflows for a given ticket, ordered by created_at descending.

**Parameters:**
- `ticket_key` (str): JIRA ticket key

**Returns:** list[dict] - List of workflow dictionaries

### get_audit_log(workflow_id)

Retrieve audit log entries for a workflow, ordered by created_at ascending.

**Parameters:**
- `workflow_id` (str): Workflow UUID

**Returns:** list[dict] - List of audit log entries

### run_migrations()

Run database migrations. Idempotent - safe to run multiple times.

**Returns:** None
