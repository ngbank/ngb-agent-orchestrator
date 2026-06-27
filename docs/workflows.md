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
5. Resolve the target repository URL and clone it to a temp directory
6. Invoke the Goose plan recipe in that cloned repository to generate a WorkPlan
7. Validate and store the WorkPlan
8. Post the WorkPlan as a JIRA comment
9. Clean up temp clone directories
10. Suspend at the approval gate — print instructions and exit

Output:
```
🚀 Starting workflow for ticket: AOS-41
⏸️  WorkPlan is ready for review.
   Workflow ID: b04fd4e0-1edc-4f95-8489-da914470b58d

   To approve:  python -m dispatcher.run --approve-plan --ticket AOS-41
   To reject:   python -m dispatcher.run --reject --ticket AOS-41 --reason "your reason"
```

### Dry Run

Preview what would happen without touching JIRA or the database:

```bash
python -m dispatcher.run --ticket AOS-41 --dry-run
```

### Detached Submission (remote mode only)

In remote mode (`ORCHESTRATOR_MODE=remote`) every mutation call is
fire-and-forget — the orchestrator server returns `202 Accepted`
immediately and runs the graph drive on a background worker. By default
the dispatcher CLI subscribes to the `/workflows/{id}/events` SSE stream
and prints `node_start` / `node_end` / `interrupt` / `completed` /
`failed` events as they arrive, so the operator sees the same kind of
feedback they got from the old synchronous calls.

Use `--detach` to submit the request and exit immediately without
streaming the lifecycle. The workflow keeps running on the server; you
can check on it later with `--list`, `--history`, or the TUI.

```bash
# Submit and return as soon as the server enqueues the work.
python -m dispatcher.run --ticket AOS-41 --detach

# Same for any mutation route.
python -m dispatcher.run --approve-plan --workflow-id <uuid> --detach
```

Press `Ctrl-C` during a non-detached follow to detach from the stream
without affecting the server-side workflow. `--detach` is rejected (exit
code `2`) when used against a `local` service since local invocations are
already synchronous and have no SSE stream to skip.

The TUI consumes the same surface: selecting an `in_progress` workflow
shows a live log tail in the detail pane, backed by
`WorkflowService.read_logs(after_offset=...)` — which maps to the
`GET /workflows/{id}/logs` SSE endpoint in remote mode and to direct log
file reads in local mode. See [TUI Live Log Tailing](tui.md#live-log-tailing)
for the keybindings and tuning knobs.

---

## Approving or Rejecting

After reviewing the WorkPlan comment on JIRA:

```bash
# Approve — resumes the graph, triggers the execute phase
python -m dispatcher.run --approve-plan --ticket AOS-41

# Reject — terminates the workflow, no code changes made
python -m dispatcher.run --reject --ticket AOS-41 --reason "scope too broad"
```

You can also target a specific workflow by ID (useful when multiple runs exist for the same ticket):

```bash
python -m dispatcher.run --approve-plan --workflow-id b04fd4e0-1edc-4f95-8489-da914470b58d
python -m dispatcher.run --reject  --workflow-id b04fd4e0-... --reason "needs more detail"
```

### What Happens on Approval

1. Workflow status → `APPROVED`
2. The LangGraph graph resumes from the checkpoint
3. The `generate_code` node is invoked:
   - Runs `goose run --recipe recipes/generate.yaml`
  - The code-generator subgraph fetches a GitHub App token before cloning
  - Goose creates a feature branch (`feature/{TICKET}+{slug}`), implements tasks, runs tests, commits
  - A follow-up graph node pushes the branch and opens or updates the PR using GitHub App auth
  - PR title: `[TICKET-KEY] <WorkPlan summary>`
  - PR description: filled from `.github/pull_request_template.md` if present, otherwise a minimal body
  - Execution summary JSON is updated with `pr_url` after the PR node succeeds and then stored in SQLite
4. Dispatcher posts execution summary (with PR link) as a JIRA comment
5. Workflow status → `COMPLETED` (or `FAILED` on error)

### What Happens on Rejection

1. Workflow status → `REJECTED`
2. Rejection reason is written to the audit log
3. Graph terminates — no code changes are made

---

## WorkPlan Clarification Loop

When the plan recipe generates a WorkPlan with `status: "concerns"` or `"blocked"`, the workflow pauses instead of failing. The workflow status transitions to `pending_workplan_clarification`.

The dispatcher prints the concerns to the CLI, then suspends:

```
⏸️  WorkPlan needs clarification (round 1/3)
   Status: concerns
   Workflow ID: b04fd4e0-...

   Concerns identified:
     1. External dependency on third-party API
     2. Which database engine should we use?

   To clarify:  dispatcher --clarify --ticket AOS-36
```

To provide answers and regenerate the plan:

```bash
dispatcher --clarify --ticket AOS-36

# or by workflow ID
dispatcher --clarify --workflow-id b04fd4e0-1edc-4f95-8489-da914470b58d
```

### Viewing Clarification History

When reviewing a workflow's history, you can optionally include the full clarification Q&A:

```bash
dispatcher --history --ticket AOS-36 --show-clarifications
```

This prints each clarification round (questions, risks, and answers) after the node traversal table. By default, clarification history is hidden to keep the history output concise.

The CLI will prompt for an answer to each question interactively. After all answers are collected, the graph resumes and the plan recipe re-runs with the answers as context.

- If the regenerated plan passes, the workflow proceeds to the approval gate as normal.
- If the regenerated plan still has concerns, the workflow pauses again for another round.
- A maximum of **3 clarification rounds** is enforced. If exceeded, the workflow fails with an error — start a fresh workflow with a clearer ticket description.



## Retrying a Failed Workflow

When a workflow ends in `failed` status — Goose crash, model error, transient JIRA outage,
network blip — you can resume it from the node that failed without re-running successful
upstream stages:

```bash
# Retry the most recent failed workflow for a ticket
python -m dispatcher.run --retry --ticket AOS-41

# Retry by explicit workflow ID
python -m dispatcher.run --retry --workflow-id <uuid>
```

How resume works:
- The graph's checkpoint history is walked to find the snapshot immediately before the
  failed node was about to run.
- That checkpoint is rewound (the previous `error` and `failed_node` fields are cleared).
- `graph.invoke(None, ...)` resumes execution from that point.
- The same `workflow_id` is reused; `retry_count` is incremented and an audit log entry
  is recorded.

Resume granularity:
- A failure inside the `work_planner` subgraph (e.g. `generate_plan`, `validate_plan`,
  `post_to_jira`) rewinds to before the entire `work_planner` subgraph — the subgraph
  re-runs from `validate_input`. This is intentional: the subgraph runs without its
  own checkpointer, so it is atomic from the parent graph's perspective.
- A failure in `generate_code` rewinds to before `generate_code` only — the plan and
  approval are preserved.

Only `failed` workflows are retryable. Attempting `--retry` on any other status returns
an error. Workflows that ended with `partial` execution status (build pass, tests fail)
are marked `completed`, not `failed`, and are NOT considered retryable — they should be
finished manually.

### Recovering Interrupted Workflows

A workflow can also end up stuck in `in_progress` if the dispatcher process is killed
mid-run (Ctrl-C, terminal close, SIGKILL, OOM). To handle this:

- **On Ctrl-C**: the dispatcher installs a `KeyboardInterrupt` handler that records the
  node that was about to run as `failed_node` and transitions the workflow to `failed`
  before exiting. The workflow is then retryable like any other failure.
- **On harder kills** (SIGKILL, OOM, terminal close): the signal handler can't run, so
  the workflow stays in `in_progress`. `--retry` therefore also accepts workflows in
  `in_progress` state as a safety net. When retrying an `in_progress` workflow:
  - If `failed_node` was recorded, retry resumes from there.
  - Otherwise the resume point is derived from `snapshot.next` — the node LangGraph
    was about to execute when the workflow stopped.
  - A warning is printed because there is no way to verify another process isn't still
    running the workflow; if you retry while another dispatcher is alive, you may get
    duplicate work.

---

## Workflow Lifecycle

```
pending
  │
  ▼
in_progress  ──────────────────────────────────────────►  failed
  │                                                       │
  │                                            ◄──────────┘ (--retry)
  ▼ (plan status: concerns/blocked/questions?)
pending_workplan_clarification  ──── (on clarify) ────►  in_progress (loop)
  │ (plan OK after clarification)
  ▼
pending_approval  ──── rejected ──────────────────────►  rejected
  │
  ▼ approved
approved
  │
  ▼
completed  (or failed if generate_code errors)
```

| Status | Description |
|---|---|
| `pending` | Workflow created, not yet started |
| `in_progress` | Planning phase executing (resumable via `--retry` if interrupted) |
| `pending_workplan_clarification` | WorkPlan has questions/concerns; waiting for reviewer answers |
| `pending_approval` | WorkPlan posted; waiting for developer decision |
| `approved` | Developer approved; generate phase starting |
| `rejected` | Developer rejected; no code changes made |
| `completed` | All stages finished successfully |
| `failed` | Unrecoverable error occurred (resumable via `--retry`) |

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

---

## Workflow Logs

Use `--logs` to print captured plan/execute logs for a workflow:

```bash
dispatcher --ticket AOS-41 --logs
dispatcher --workflow-id <uuid> --logs
```

Log paths are workflow-pinned:

- Default base directory is XDG state: `$XDG_STATE_HOME/ngb-agent-orchestrator/logs`.
- If `XDG_STATE_HOME` is unset, fallback is `~/.local/state/ngb-agent-orchestrator/logs`.
- `LOGS_DIR` can be set to override the base directory explicitly.
