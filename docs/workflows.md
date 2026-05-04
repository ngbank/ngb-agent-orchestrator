# Workflows

This document covers how to run, approve, reject, and monitor workflows using the dispatcher CLI.

---

## Running a Workflow

```bash
# Start the full pipeline for a JIRA ticket
python -m dispatcher.run --ticket AOS-41
```

The dispatcher will:
1. Validate the ticket key format
2. Check for an existing active workflow for this ticket (blocks if one exists)
3. Create a workflow record in SQLite
4. Fetch the JIRA ticket via `acli`
5. Invoke the Goose plan recipe to generate a WorkPlan
6. Validate and store the WorkPlan
7. Post the WorkPlan as a JIRA comment
8. Suspend at the approval gate — print instructions and exit

Output:
```
🚀 Starting workflow for ticket: AOS-41
⏸️  WorkPlan is ready for review.
   Workflow ID: b04fd4e0-1edc-4f95-8489-da914470b58d

   To approve:  python -m dispatcher.run --approve --ticket AOS-41
   To reject:   python -m dispatcher.run --reject --ticket AOS-41 --reason "your reason"
```

### Dry Run

Preview what would happen without touching JIRA or the database:

```bash
python -m dispatcher.run --ticket AOS-41 --dry-run
```

---

## Approving or Rejecting

After reviewing the WorkPlan comment on JIRA:

```bash
# Approve — resumes the graph, triggers the execute phase
python -m dispatcher.run --approve --ticket AOS-41

# Reject — terminates the workflow, no code changes made
python -m dispatcher.run --reject --ticket AOS-41 --reason "scope too broad"
```

You can also target a specific workflow by ID (useful when multiple runs exist for the same ticket):

```bash
python -m dispatcher.run --approve --workflow-id b04fd4e0-1edc-4f95-8489-da914470b58d
python -m dispatcher.run --reject  --workflow-id b04fd4e0-... --reason "needs more detail"
```

### What Happens on Approval

1. Workflow status → `APPROVED`
2. The LangGraph graph resumes from the checkpoint
3. The `execute_plan` node is invoked:
   - Runs `goose run --recipe recipes/execute.yaml`
   - Goose creates a feature branch, implements tasks, runs tests, commits
   - Execution summary JSON is stored in SQLite
4. Workflow status → `COMPLETED` (or `FAILED` on error)

### What Happens on Rejection

1. Workflow status → `REJECTED`
2. Rejection reason is written to the audit log
3. Graph terminates — no code changes are made

---

## Workflow Lifecycle

```
pending
  │
  ▼
in_progress  ──────────────────────────────────────────►  failed
  │
  ▼
pending_approval  ──── rejected ──────────────────────►  rejected
  │
  ▼ approved
approved
  │
  ▼
completed  (or failed if execute_plan errors)
```

| Status | Description |
|---|---|
| `pending` | Workflow created, not yet started |
| `in_progress` | Planning phase executing |
| `pending_approval` | WorkPlan posted; waiting for developer decision |
| `approved` | Developer approved; execute phase starting |
| `rejected` | Developer rejected; no code changes made |
| `completed` | All stages finished successfully |
| `failed` | Unrecoverable error occurred |

Every status transition is recorded in the audit log with timestamp, actor, and reason.

---

## Duplicate Detection

The dispatcher blocks starting a new workflow if an active one already exists for the same ticket:

```bash
python -m dispatcher.run --ticket AOS-41
# ❌ Active workflow already exists for AOS-41 (status: pending_approval)
```

Completed and rejected workflows do not block new runs — each run creates a new workflow record.

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| Invalid ticket key format | Exit immediately, no DB record created |
| JIRA ticket not found | Exit with error message |
| Missing JIRA credentials | Exit with configuration error |
| Plan recipe fails | Workflow marked `failed` in SQLite |
| Execute recipe fails | Execution summary stored with `status: failed`, workflow marked `failed` |
| Keyboard interrupt (Ctrl+C) | Workflow marked `failed`, clean exit |
