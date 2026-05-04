# State Store

The orchestrator uses a SQLite database to track workflow state, store WorkPlans and execution summaries, and maintain an append-only audit log.

---

## Database Location

Default path: `state/local.db` (relative to the project root).

Override with the `DB_PATH` environment variable:
```bash
DB_PATH=/path/to/custom.db
```

The `state/` directory and `.db` files are gitignored.

---

## Schema

### `workflows`

One row per workflow run. A single JIRA ticket can have multiple workflow rows (one per run).

| Column | Type | Description |
|---|---|---|
| `id` | TEXT (UUID) | Primary key |
| `ticket_key` | TEXT | JIRA ticket key (e.g. `AOS-41`) |
| `status` | TEXT | See status enum below |
| `work_plan` | TEXT | WorkPlan JSON (nullable) |
| `execution_summary` | TEXT | Execution summary JSON (nullable, added in migration 003) |
| `pr_url` | TEXT | Pull request URL (nullable) |
| `created_at` | TEXT | ISO 8601 timestamp |
| `updated_at` | TEXT | ISO 8601 timestamp |

**Status values**: `pending` | `in_progress` | `pending_approval` | `approved` | `rejected` | `completed` | `failed`

### `audit_log`

Append-only record of every state change and significant action.

| Column | Type | Description |
|---|---|---|
| `id` | TEXT (UUID) | Primary key |
| `workflow_id` | TEXT | Foreign key → `workflows.id` |
| `actor` | TEXT | Who/what performed the action (e.g. `system`, `dispatcher`, username) |
| `action` | TEXT | Action name (e.g. `workflow_created`, `status_change`, `execution_summary_stored`) |
| `reason` | TEXT | Human-readable reason (nullable) |
| `created_at` | TEXT | ISO 8601 timestamp |

### `schema_migrations`

Tracks which SQL migration files have been applied. Added in migration runner upgrade (AOS-41).

| Column | Type | Description |
|---|---|---|
| `name` | TEXT | Migration filename (e.g. `001_initial_schema.sql`) |
| `applied_at` | TEXT | ISO 8601 timestamp |

---

## Migrations

Migration files live in `state/migrations/` and are run automatically on first use via `run_migrations()`. Each file runs **exactly once** — the migration runner tracks applied migrations in the `schema_migrations` table.

| File | Description |
|---|---|
| `001_initial_schema.sql` | `workflows` and `audit_log` tables, indexes |
| `002_approval_statuses.sql` | Adds status CHECK constraint via copy-rename |
| `003_execution_summary.sql` | Adds `execution_summary` column to `workflows` |

To add a new migration, create `state/migrations/004_your_description.sql`. It will be applied automatically on the next run.

---

## Python API

All functions are in `state/state_store.py` and exported from `state/__init__.py`.

### `create_workflow(ticket_key, work_plan=None, status=PENDING, workflow_id=None) → str`

Creates a new workflow record and audit log entry. Returns the UUID.

### `update_status(workflow_id, status, pr_url=None, actor="system", reason=None)`

Updates workflow status and creates an audit log entry.

### `update_work_plan(workflow_id, work_plan, actor="system", reason=None)`

Stores the WorkPlan JSON dict (serialised to JSON) and creates an audit log entry.

### `update_execution_summary(workflow_id, execution_summary, actor="system")`

Stores the execution summary JSON dict and creates an audit log entry with `action="execution_summary_stored"`.

### `get_workflow(workflow_id) → dict | None`

Returns a workflow dict with `work_plan` and `execution_summary` deserialised from JSON, and `status` as a `WorkflowStatus` enum.

### `get_workflow_by_ticket(ticket_key) → list[dict]`

Returns all workflows for a ticket, newest first.

### `get_audit_log(workflow_id) → list[dict]`

Returns the full audit log for a workflow, oldest first.

### `run_migrations()`

Applies any unapplied migration files. Called automatically by the dispatcher on startup.

---

## Usage Example

```python
from state import create_workflow, update_status, get_workflow, WorkflowStatus

# Create a workflow
workflow_id = create_workflow(ticket_key="AOS-41")

# Update status
update_status(workflow_id, WorkflowStatus.IN_PROGRESS, actor="dispatcher")

# Retrieve it
workflow = get_workflow(workflow_id)
print(workflow["status"])          # WorkflowStatus.IN_PROGRESS
print(workflow["work_plan"])       # None (not yet set)
```
