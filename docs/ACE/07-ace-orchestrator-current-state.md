# ACE — Orchestrator Integration Map: Where Learning Signals Already Exist

## The right framing

Before designing any new infrastructure, the first question to ask is: *what does the orchestrator already produce that ACE can consume?* Every run of `ngb-agent-orchestrator` generates a trail of structured artifacts. The ACE learning loop doesn't need new instrumentation — it needs to read what's already being written, route it through an evaluator and reflector, and write the output to the context store.

This document maps the orchestrator's current output to the four signal categories ACE requires.

---

## Signal category 1 — Plan signal (what the agent intended)

**Where it lives:** `workflows.work_plan` in SQLite. Also available in `OrchestratorState.work_plan_data` during a live run.

**What it contains:** A structured `WorkPlan` JSON with `tasks`, `approach`, `risks`, `questions_for_reviewer`, and a planner self-assessment: `status` (`pass` | `concerns` | `blocked`). Migration 009 consolidated `risks` and `questions_for_reviewer` into a unified `concerns` array, which has already rationalised the schema for extraction.

**ACE relevance:** This is the *plan trace* — the primary input to the evaluator. The `status` field is especially valuable: `concerns` and `blocked` are the planner saying "I wasn't confident here." That's a cheap proxy for a difficult task type, already computed on every run.

---

## Signal category 2 — Outcome signal (what actually happened)

**Where it lives:** `workflows.code_generation_summary` in SQLite (added in migration 003 as `execution_summary`, renamed in migration 011), written by the `persist_results` node. Also available in `OrchestratorState.code_generation_summary`.

**What it contains:** `status` (`success` | `partial` | failure), `branch`, `build` (pass/fail), `tests` (pass/fail), `pr_url`, commit SHA.

**ACE relevance:** This is the *outcome trace*. Paired with the work plan, it tells you whether the agent's plan materialised correctly. A `partial` status or failing build/test is a signal that the plan, the context, or the execution strategy had a problem.

**Key fact for trace-first adoption:** The `workflows` table already has hundreds of completed runs. `work_plan` and `code_generation_summary` together give you everything needed for a first extraction pass without touching any live code.

---

## Signal category 3 — Human feedback signal (what engineers thought)

Both pre-execution and post-execution human feedback are fully persisted to SQLite.

### Pre-execution feedback — `clarification_history` (migration 008)

Written by `update_clarification_history()`, called inside `await_workplan_clarification` after each Q&A round. Stored as an **append-only structured JSON array** in `workflows.clarification_history`. Each entry:

```json
{
  "round": 1,
  "concerns": ["list of planner concerns or questions from the previous plan"],
  "answers": [{"concern": "...", "answer": "..."}],
  "actor": "username",
  "timestamp": "2026-06-06T..."
}
```

This is the highest-quality signal in the system. It records exactly what the initial plan got wrong — `concerns` is the planner's uncertainty made explicit, and `answers` is an engineer's correction. It is already structured, timestamped, and round-indexed: nearly a pre-formatted training example for the reflector. **This is the correct starting point for an initial extraction pass.**

### Post-execution feedback — `pr_comments` (migration 010)

Written by `update_pr_comments()`, called inside `await_pr_approval` when the engineer's decision is `commented`. Currently stored as append-only text with a separator per round:

```
--- Review round 2026-06-06T... ---
The implementation handles the happy path but misses the edge case where...
```

**Planned improvement:** Refactor to a structured JSON array parallel to `clarification_history`, with `round`, `comments`, `actor`, and `timestamp` fields. The `update_pr_comments()` write path already receives `comments` and `actor` as discrete parameters; adding a round counter is a small, local change. The column type stays `TEXT` — no migration required, only a format change.

### Rejection reasons — `audit_log`

When a PR is rejected, the rejection `reason` lands in `audit_log.reason` via `update_status()`. It is not stored in `pr_comments`. The signal is recoverable via a JOIN on the audit log — it is not a gap, just a different read location. **Planned improvement:** add a `rejection_reason` column to `workflows` and write to it alongside the status change, making the learning extraction query simpler (no join) and making the reason a first-class workflow field.

**Implementation ordering note:** Both the `pr_comments` JSON refactor and the `rejection_reason` column addition should happen *before* the historical extraction pass. If you refactor while historical rows still have the old text format, the extractor must handle both. Doing it first keeps the extraction path clean and avoids a dual-format reader.

---

## Signal category 4 — Execution telemetry

**Audit log** (`audit_log` table): Append-only record of every status transition — timestamps, actor, action name. The `pr_comments_updated` and `clarification_history_updated` actions now appear here too, so the audit log doubles as a metadata index over those signals. Workflow duration derived from timestamps is a weak proxy for task difficulty.

**Token usage** (`workflows.usage_summary`, migration 006): Per-stage LLM token usage persisted by `update_usage_summary()`, written by `persist_results` on the happy path. Now in the main DB (not scattered in temp files), though this is the lowest-priority signal for an initial ACE integration.

---

## Signal map

| ACE signal type | Orchestrator artifact | Location | Structure | Quality |
|---|---|---|---|---|
| Plan trace | `work_plan` | `workflows.work_plan` | Structured JSON | High |
| Planner confidence | `work_plan.status`, `concerns` | `workflows.work_plan` | Enum + array | Medium (self-reported) |
| Outcome trace | `code_generation_summary` | `workflows.code_generation_summary` | Structured JSON | High |
| Pre-execution human feedback | `clarification_history` | `workflows.clarification_history` | Structured JSON array | Very high — process first |
| Post-execution feedback (comments) | `pr_comments` | `workflows.pr_comments` | Append-only text → planned JSON | High |
| Post-execution feedback (rejections) | rejection reason | `audit_log.reason` → planned `workflows.rejection_reason` | Free text → planned column | Medium |
| Workflow duration | status transition timestamps | `audit_log` | Timestamped events | Low |
| Token usage | `usage_summary` | `workflows.usage_summary` | Structured JSON | Low |

---

## The actual gap

Every signal the ACE learning loop needs is already produced and persisted to SQLite. The gap is not missing persistence — it's that **nothing reads these columns to produce context items**. There is no evaluator node, no reflector node, and no context item store.

This changes the adoption calculus: the historical extraction pass from Topic 6 can start now. The extraction query across all four signal types:

```sql
SELECT
    w.id,
    w.ticket_key,
    w.work_plan,
    w.code_generation_summary,
    w.clarification_history,
    w.pr_comments,
    a.reason AS rejection_reason
FROM workflows w
LEFT JOIN audit_log a
    ON a.workflow_id = w.id
    AND a.action = 'status_change'
    AND a.reason LIKE '%rejected%'
WHERE w.status IN ('completed', 'failed', 'rejected')
ORDER BY w.created_at DESC;
```

The only additive structural changes required are: (1) a `pr_comments` JSON format refactor, (2) a `rejection_reason` column on `workflows`, (3) an evaluator/reflector pass over completed rows, and (4) a context item store to write into. None of these are blocking the others from being scoped.

---

## Context loading references

### Papers and web docs
- ACE paper (HTML): https://arxiv.org/html/2510.04618v3
- ACE paper (abstract): https://arxiv.org/abs/2510.04618

### Local files
- `ace-context-loading-sources.md`
- `00-ace-primer-roadmap.md`
- `01-ace-what-is-it.md`
- `02-ace-memory-model.md`
- `03-ace-learning-loop.md`
- `04-ace-retrieval-and-injection.md`
- `05-ace-curation-quality.md`
- `06-ace-trace-learning.md`

### Orchestrator code anchors
- `/Users/romulo/Projects/ngb-agent-orchestrator/orchestrator/builder.py`
- `/Users/romulo/Projects/ngb-agent-orchestrator/orchestrator/state.py`
- `/Users/romulo/Projects/ngb-agent-orchestrator/orchestrator/work_planner/nodes/generate_plan.py`
- `/Users/romulo/Projects/ngb-agent-orchestrator/orchestrator/work_planner/nodes/await_workplan_clarification.py`
- `/Users/romulo/Projects/ngb-agent-orchestrator/orchestrator/nodes/await_pr_approval.py`
- `/Users/romulo/Projects/ngb-agent-orchestrator/orchestrator/code_generator/nodes/run_goose.py`
- `/Users/romulo/Projects/ngb-agent-orchestrator/orchestrator/code_generator/nodes/persist_results.py`
- `/Users/romulo/Projects/ngb-agent-orchestrator/state/sqlite_workflow_repository.py`
- `/Users/romulo/Projects/ngb-agent-orchestrator/state/migrations/`
- `/Users/romulo/Projects/ngb-agent-orchestrator/orchestrator/work_planner/recipes/plan.yaml`
- `/Users/romulo/Projects/ngb-agent-orchestrator/orchestrator/code_generator/recipes/generate_code.yaml`
