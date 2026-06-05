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

For the current column definitions and status values, refer to the latest migration file in [`state/migrations/`](../state/migrations/).

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

To add a new migration, create `state/migrations/00N_description.sql` (next number in sequence). It will be applied automatically on the next run.

---

## Python API

The public API is defined by the `WorkflowRepository` protocol in `state/workflow_repository.py` and exported from `state/__init__.py`. The SQLite implementation lives in `state/sqlite_workflow_repository.py`.

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
