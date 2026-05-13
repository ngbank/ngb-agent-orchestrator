# Architecture

This document describes the components of the NGB Agent Orchestrator and how they fit together.

---

## Sequence Diagram

The full orchestration flow is captured in [`plan-recipe-flow.mmd`](plan-recipe-flow.mmd), and the current LangGraph topology (including nested work-planner internals) is captured in the root-level [`diagram.mmd`](../diagram.mmd). A high-level view:

```
User
 │
 ├─ python -m dispatcher.run --ticket TICKET-KEY
 │
 ▼
Dispatcher (dispatcher/run.py)
 │  Looks up or creates a workflow record in SQLite
 │  Builds and invokes the LangGraph orchestrator
 │
 ▼
LangGraph Graph (graph/)
 │
 ├── work_planner subgraph
 │    ├── validate_input        Validate ticket key format
 │    ├── check_duplicate       Reject if an active workflow exists
 │    ├── create_workflow_record  Create SQLite row (status=IN_PROGRESS)
 │    ├── fetch_ticket          Call JIRA via acli
 │    ├── generate_plan         Invoke Goose plan recipe → WorkPlan JSON
 │    ├── validate_plan         Validate WorkPlan against JSON schema
 │    ├── store_plan            Persist WorkPlan to SQLite
 │    └── post_to_jira          Post formatted WorkPlan as JIRA comment
 │
 ├── await_approval             ← graph suspends here (LangGraph interrupt)
 │    Marks workflow PENDING_APPROVAL in SQLite
 │    Prints instructions for approve/reject CLI
 │
 └── execute_plan
      Invokes Goose execute recipe:
        - Creates feature branch
        - Implements WorkPlan tasks
        - Runs build + test checks
        - Commits changes
      Persists execution summary to SQLite
      Updates status → COMPLETED or FAILED
```

---

## Component Reference

### `dispatcher/run.py`

The CLI entry point. Handles three modes:

- `--ticket KEY` — starts a new workflow
- `--approve --ticket KEY` — resumes a suspended workflow (approved)
- `--reject --ticket KEY --reason "..."` — resumes a suspended workflow (rejected)

Builds the LangGraph orchestrator and invokes it. On `GraphInterrupt` (the approval gate), it prints instructions and exits cleanly. The graph state is persisted to SQLite so it can be resumed later.

### `graph/`

LangGraph state machine. Two levels:

- **Top-level graph** (`graph/builder.py`): `work_planner → await_approval → execute_plan`
- **`work_planner` subgraph** (`graph/work_planner/`): seven sequential nodes for planning

State is defined in `graph/state.py` (`OrchestratorState`) and `graph/work_planner/state.py` (`WorkPlannerState`).

### `recipes/plan.yaml`

Goose recipe that produces a `WorkPlan` JSON document from a JIRA ticket. Parameters: `ticket_key`, `output_path`. See [docs/recipes.md](recipes.md) for full documentation.

### `recipes/execute.yaml`

Goose recipe that implements an approved WorkPlan. Parameters: `ticket_key`, `work_plan_path`, `output_path`. Creates a feature branch, implements tasks, runs checks, commits, and writes an execution summary JSON. See [docs/recipes.md](recipes.md).

### `state/`

SQLite persistence layer. See [docs/state-store.md](state-store.md) for schema and API reference.

### `schemas/work_plan_v1.json`

JSON Schema contract for WorkPlan documents. Validated by `dispatcher/work_plan_validator.py` before any WorkPlan is stored or executed. Fields:

| Field | Type | Description |
|---|---|---|
| `schema_version` | `"1.0"` | Fixed value |
| `ticket_key` | string | e.g. `"AOS-41"` |
| `summary` | string | One-sentence description |
| `approach` | string | Implementation strategy |
| `tasks` | array | Ordered list of `{id, description, files_likely_affected}` |
| `risks` | array | Identified risks (may be empty) |
| `questions_for_reviewer` | array | Open questions (may be empty) |
| `status` | `"pass"` \| `"concerns"` \| `"blocked"` | Planner confidence |

### `config/litellm.yaml`

LiteLLM proxy configuration. Maps model names (e.g. `azure-gpt4`) to provider API endpoints. Goose points at this proxy instead of a provider directly, so the model backend can be swapped without changing recipes. See [docs/configuration.md](configuration.md).

---

## Data Flow

```
JIRA ticket
    │  (acli jira workitem view)
    ▼
WorkPlan JSON  ─────────────────────────────────────────────────┐
    │  (written to /tmp, validated against schema)              │
    │  (posted as JIRA comment)                                 │
    │  (stored in SQLite workflows.work_plan)                   │
    ▼                                                           │
Developer approves via CLI                                      │
    │                                                           │
    ▼                                                           │
Goose execute recipe  ◀─────────────────────────────────────────┘
    │  (reads WorkPlan, creates branch, implements tasks)
    ▼
Execution Summary JSON
    │  (stored in SQLite workflows.execution_summary)
    ▼
Status → COMPLETED or FAILED
```

---

## Graph Checkpointing

The LangGraph graph uses `SqliteSaver` (backed by the same `state/local.db`) as its checkpointer. This means:

- The full graph state is serialised to SQLite at every node boundary.
- When `await_approval` calls `interrupt()`, the process can exit cleanly.
- Running `dispatcher.run --approve` rehydrates the graph from the checkpoint and resumes from exactly where it paused.
