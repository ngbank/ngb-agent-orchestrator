# ACE — Learning Pipeline Design: Evaluator/Reflector/Curator Loop over Workflow Traces

## The pipeline's job in one sentence

The learning pipeline reads a completed workflow trace from SQLite, decides whether it produced useful signal, extracts that signal as a structured context item candidate, and writes the candidate to the store — either directly or through a staging area for review.

This is the piece that closes the loop. Topic 7 identified where signals are produced. Topic 8 identified where context items are consumed. This topic designs what happens in between.

---

## Three components, one direction of flow

The pipeline has three sequential components. They are logically distinct even if you implement them as a single LLM call in the first version.

**Evaluator** — answers: *did this trace produce signal worth learning from?*

It reads the plan trace and outcome and returns a verdict. Not every workflow run is a useful training example. Runs that succeeded trivially on the first try with no concerns may carry little new information. Runs that failed due to environment problems (network error, CI outage) rather than agent reasoning carry misleading signal. The evaluator's job is to triage: proceed, skip, or flag for manual review.

**Reflector** — answers: *what specifically should be learned from this trace?*

It reads the full trace — plan, outcome, clarification history, PR comments — and produces one or more raw context item candidates. Each candidate is a structured object: a pattern type, a description of the learned behaviour, the evidence it came from, and an initial confidence estimate. The Reflector is the LLM-heavy component — it's doing interpretation, not just extraction.

**Curator** (ACE paper term) — answers: *how does this candidate change the store?*

It receives Reflector candidates and applies them to the context item store: create a new item, merge with an existing similar item (increasing confidence), or flag a contradiction with an existing item. The Curator also enforces quality — if the Reflector produces a run-specific fact rather than a generalisable pattern, the Curator reformulates or discards it before anything reaches the store. On failure paths, the Curator additionally audits existing items against the failure signal (see below).

---

## When the pipeline runs

There are two valid trigger points, and you'll likely use both.

**Trigger 1 — Post-completion hook in the live graph.** After `await_pr_approval` resolves to a terminal state, a new `run_learning_pipeline` node runs the pipeline over the just-completed trace. This is the live learning loop.

**Trigger 2 — Offline job.** A separate script queries `workflows` for rows in terminal states that haven't been processed by the pipeline yet. This is the trace-first adoption path from Topic 6, and also the recovery path when the live hook fails.

In practice: start with the offline job for the historical extraction pass. Add the post-completion hook to the live graph once the pipeline is validated.

**Avoiding double-processing:** Add a `context_extracted_at` column to `workflows` (nullable). The offline job filters `WHERE context_extracted_at IS NULL`. The live hook sets it after completion.

---

## When the pipeline runs: all terminal paths

The `run_learning_pipeline` node must run on **all** terminal paths — `approved`, `rejected`, and `failed`. The graph topology:

```
await_pr_approval
  → approved  → run_learning_pipeline → END
  → rejected  → run_learning_pipeline → END
  → commented → generate_code (existing re-run loop)

generate_code (code_generator)
  → persist_results: status FAILED → run_learning_pipeline → END
```

Running only on the `approved` path would discard the most informative signal category: what went wrong and why. Rejected and failed workflows are exactly where the pipeline earns its value.

---

## The evaluator in detail

The evaluator reads: `work_plan.status`, `code_generation_summary.status`, build/test results, `clarification_history` (length and content), `pr_comments` (presence), and the terminal status that triggered the run.

Triage rules:

| Signal pattern | Verdict |
|---|---|
| Plan `pass`, execution `success`, no clarifications, no PR comments | Skip — trivial success, low information content |
| Plan `concerns` or `blocked`, then execution `success` | Proceed — concerns were resolved; the resolution is signal |
| Any clarification rounds present | Proceed — explicit human correction always produces signal |
| PR comments present | Proceed — post-execution human feedback always produces signal |
| Terminal status `rejected` | Proceed (failure path strategy) |
| Terminal status `failed`, exec_error set | Flag — may be environment failure, not agent reasoning |
| Terminal status `failed`, plan was `pass` | Proceed (failure path strategy) — plan was overconfident |

**The evaluator can be rule-based.** Start with rules — they're transparent, testable, and don't require an LLM call on every run. Promote to a prompt-based evaluator later if the rule set becomes too complex.

**Item generation rate as a health signal.** Once the store is populated, a high skip rate (trivial successes) is not a problem — it means the store is doing its job and the agent is succeeding on the first try. A sustained skip rate on a *cold or empty store* is a warning sign: the evaluator thresholds may be miscalibrated and failing to pick up new knowledge. Item generation rate is a leading indicator of pipeline health (see Topic 13 — Measurement).

---

## The Reflector in detail

The Reflector receives the full trace and produces one or more candidates. Each candidate:

```python
{
  "pattern_type": "approach" | "concern" | "test_coverage" | "implementation",
  "scope": "task_type" | "file_pattern" | "codebase_wide",
  "description": "When modifying the state machine, check migration compatibility first.",
  "evidence": [
    {"workflow_id": "abc-123", "signal_source": "clarification_round_1", "detail": "..."}
  ],
  "initial_confidence": 0.75,
  "suggested_tier": "PATTERN"
}
```

The Reflector prompt instructs the LLM to identify decisions that *could have gone differently* — places where the agent was uncertain, where a human corrected it, or where the outcome diverged from the plan — and produce a generalisable pattern for each.

**Key Reflector constraint:** The Reflector must generate *generalisable* patterns, not run-specific facts. "The AOS-41 migration needed a `retry_count` column" is not a context item. "SQLite schema changes in this codebase require a new migration file with a sequential prefix" is a context item. The Reflector prompt must enforce this distinction explicitly. However, enforcement is a shared responsibility — the Curator is the quality gate (see below).

---

## The Curator in detail

The Curator operates in two modes depending on which terminal path triggered the pipeline.

### Success/correction path (approved, or failed-but-evaluated)

The Curator receives Reflector candidates and applies one of three operations per candidate:

**Create new item.** No existing item is sufficiently similar. Write the candidate as a new row with `confidence = initial_confidence`, `occurrence_count = 1`.

**Merge with existing item.** An existing item is semantically similar. Increment `occurrence_count`, add the new evidence to `provenance`, update confidence via weighted average. The description of the higher-confidence item wins unless new evidence suggests a refinement.

**Flag contradiction.** An existing item says the opposite. Neither is automatically deleted — both are flagged for manual review. Contradictions usually mean the pattern is context-dependent and needs a scope refinement.

**Quality enforcement.** Before applying any operation, the Curator checks: does this description reference a specific ticket key, branch name, or run artifact? If so, it reformulates to remove the specificity or discards. The Reflector generates broadly; the Curator enforces generalisability before persistence.

### Failure path (rejected, failed)

On the failure path, the Curator has an additional job: **retrospective auditing of existing items against the failure**.

Two questions it asks:

1. **Missing coverage.** Was there a pattern the store *should have* surfaced but didn't? If the failure can be traced to a known gap in the context (something the agent didn't know), the Curator creates a new item from the failure signal.

2. **Active harm.** Was there a context item that *was injected* and led the agent toward the wrong approach? This is the more dangerous case. The Curator identifies items whose guidance correlates with the failure mechanism, and reduces their confidence or flags them for review. Without this, the store can accumulate items that are confidently wrong — learned from early runs under conditions that no longer hold, silently degrading performance.

This is why the failure-path Curator is a distinct prompt from the success-path Curator. The instructions are different enough that merging them would compromise both.

---

## Failure isolation

The learning pipeline must never break the workflow completion path. The workflow `COMPLETED` or `FAILED` status is set by `persist_results` / `await_pr_approval` before the pipeline node runs. A pipeline failure therefore cannot corrupt the workflow record.

Safe pattern: wrap the entire pipeline in a try/except, log failures to `audit_log` with action `learning_pipeline_failed`, and continue. The offline job retries unprocessed rows (`context_extracted_at IS NULL`) on its next run.

---

## Staging vs direct write

For the historical extraction pass: route all Reflector output to `context_items_staged` first, promote after manual review. This is your best opportunity to calibrate confidence thresholds before they affect live operation.

For the live pipeline, a reasonable middle path: write directly for items where `initial_confidence ≥ 0.80` AND a matching item already exists with `occurrence_count ≥ 2` (reinforcing known patterns is low risk); stage everything else until the pipeline is validated.

---

## Context loading references

### Papers and web docs
- ACE paper (HTML): https://arxiv.org/html/2510.04618v3
- ACE paper (abstract): https://arxiv.org/abs/2510.04618

### Local files
- `ace-context-loading-sources.md`
- `00-ace-primer-roadmap.md`
- `01-ace-what-is-it.md`
- `03-ace-learning-loop.md`
- `07-ace-orchestrator-current-state.md`
- `08-ace-orchestrator-injection-points.md`

### Orchestrator code anchors
- `/Users/romulo/Projects/ngb-agent-orchestrator/orchestrator/builder.py`
- `/Users/romulo/Projects/ngb-agent-orchestrator/orchestrator/nodes/await_pr_approval.py`
- `/Users/romulo/Projects/ngb-agent-orchestrator/orchestrator/code_generator/nodes/persist_results.py`
- `/Users/romulo/Projects/ngb-agent-orchestrator/state/sqlite_workflow_repository.py`
