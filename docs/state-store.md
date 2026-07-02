# State Store

The orchestrator uses a SQLite database to track workflow state, store WorkPlans and execution summaries, and maintain an append-only audit log.

---

## Database Location

Default path: `$XDG_STATE_HOME/ngb-agent-orchestrator/db/local.db` (or `~/.local/state/ngb-agent-orchestrator/db/local.db` when `XDG_STATE_HOME` is unset).

This sits alongside the run-logs directory (`$XDG_STATE_HOME/ngb-agent-orchestrator/logs/`) so the host CLI and the containerised server share one persistent state root.

Override with the `DB_PATH` environment variable:
```bash
DB_PATH=/path/to/custom.db
```

The legacy `./state/` directory and any `.db` files in it remain gitignored. See [`docs/configuration.md`](configuration.md#migrating-from-statelocaldb) for instructions on migrating an existing `./state/local.db`.

---

## Audit Log Durability

All workflow state mutations are persisted atomically with their corresponding audit log entries. This ensures that:

- **No orphaned audit entries**: If a workflow update fails, the audit entry is also rolled back.
- **No missing audit entries**: If an audit entry fails to create, the entire transaction is rolled back and the workflow state is unchanged.
- **Transaction safety**: Each mutation (status change, work plan update, etc.) and its audit entry are committed in a single `BEGIN IMMEDIATE ... COMMIT` transaction.

This is enforced in `SQLiteWorkflowRepository` — all write methods (`create_workflow`, `update_status`, `update_work_plan`, `update_code_generation_summary`, etc.) perform both the state update and audit log creation in one atomic block.

Example transaction for `update_status()`:
```python
conn.execute("BEGIN IMMEDIATE")
try:
    # Update workflow state
    conn.execute("UPDATE workflows SET status = ?, ... WHERE id = ?", ...)
    # Create audit entry
    _create_audit_log(conn, workflow_id, actor, action, reason)
    # Commit atomically
    conn.commit()
except Exception:
    # Rollback on any error — neither update nor audit entry is persisted
    conn.rollback()
    raise
```

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
| `action` | TEXT | Action name (e.g. `workflow_created`, `status_change`, `code_generation_summary_stored`) |
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
