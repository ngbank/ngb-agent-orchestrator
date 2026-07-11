# ACE — Learning Loop Internals: Evaluate, Reflect, Curate, and Update

## Why go deeper after Topic 1

Topic 1 introduced the four-step loop as a mental model. This topic covers the internals — what each step consumes, what it produces, where it fails, and how they connect as a testable pipeline.

## Evaluate — turning a run trace into a quality signal

The evaluator receives the run trace (execution summary, PRs, tool calls, agent messages, intermediate state) and produces a **scored outcome with evidence** — not a lesson, just a judgment.

Three evaluation strategies in increasing signal quality:

**Rule-based** — deterministic checks: did the plan require N+ clarification rounds? Did the run complete without retries? Did a PR get produced? Fast, cheap, shallow. Tells you whether things happened, not whether they were good.

**LLM-graded** — a separate LLM reviews the trace against a rubric. More nuanced, catches reasoning failures. Risk: the grader can rationalize mistakes, especially if given the agent's narrative summary as input.

**Human-signal-derived** — scores from external human judgment: PR review comments, change requests, approval vs. rejection. Strongest signal because it is independent of the agent's self-report. Lowest volume, highest latency — arrives asynchronously after engineer review.

In practice, combine all three. Rule-based runs immediately. LLM grading runs after completion. Human signal arrives async and triggers a retroactive update to existing items.

Evaluator output structure:
```
run_id:        exec-summary-312
outcome_score: 0.72
evidence:      [plan required 3 clarifications, PR approved with 2 change requests, no test failures]
signal_source: [rule-based, pr-review]
```

**Critical design note:** Feed the reflector structured signals — tool call sequences, clarification round counts, error traces, PR review comments — not the agent's narrative execution summary. The summary is written to make the run look coherent and biases the reflector toward run-specific lessons. Raw signals force derivation from *what happened*, not from *how the agent described what happened*. If your execution summary is prose, a structured signal extractor is needed between the run and the reflector.

## Reflect — distilling a score into generalizable lessons

The reflector receives the quality score and structured evidence. Its job: extract generalizable lessons, not run-specific observations.

This is an LLM operation. The prompt drives toward the general case explicitly — the failure mode is overfitting:

- Weak: "when the PR description says X, do Y"
- Strong: "when the task description omits rollback scope, ask a clarification before planning"

The reflector produces **candidate context items** — unvalidated, not yet stored:
```
draft_situation:   task description omits rollback scope
draft_action:      ask clarification about rollback before generating migration plan
candidate_confidence: 0.61
source_run:        exec-summary-312
```

Clarification loop length is a valuable signal here. Long clarification = the original context was insufficient to plan without help. Reflect on *what information was missing* that caused the back-and-forth, and generate a playbook item for that context gap.

## Curate — deciding what survives

The curator receives candidates and existing stored items. Four operations:

**1. Deduplication** — match candidates against existing items by situation/task key and semantic similarity. Near-duplicate content gets merged: union `source_runs`, recalculate confidence, do not create a new item.

**2. Contradiction detection** — if a candidate contradicts an existing item (same situation, opposite action), flag both. Resolution: prefer higher confidence, prefer more recent, or escalate to human review. Never silently overwrite — that destroys the audit trail.

**3. Confidence adjustment** — a low-confidence candidate matching a high-confidence existing item requires interpretation:
- If candidate confidence is low due to thin evidence → weak corroboration → nudge existing confidence up slightly
- If candidate confidence is low due to mixed or contradictory evidence → ambiguity signal → nudge existing confidence down

Defaulting to a small downward adjustment on low-confidence match is the safer choice. Poisoning context is worse than being slow to reinforce a good lesson.

**4. Decay recalculation** — for every existing item whose situation matched something in the trace (even with no new candidate), update `last_seen` and recalculate `decay_score`.

**Staging area** — candidates below a minimum confidence threshold are not promoted to the store immediately. They wait for corroboration from future runs. Without a staging area, every low-confidence candidate pollutes context immediately.

## Update — persisting what survived

Update is mechanical: write promoted items, merge updates, log provenance chains. Design concern: write ordering must leave the store in a valid state on partial failure — never in a corrupted one.

The update step also triggers downstream indexing for retrieval: embedding generation, tag index updates.

## The loop as a pipeline

```
Trace (execution summary, PR, structured signals)
  ↓
Evaluate  →  scored outcome + evidence
  ↓
Reflect   →  candidate context items (unvalidated)
  ↓
Curate    →  promoted items + staging queue + updated decay scores
  ↓
Update    →  persistent playbook/skillbook write
```

Each step is independently testable. Build and validate Evaluate before Reflect exists. Build Reflect before Curate exists.

## The asynchronous complication

PR review signal arrives days after the run completes. The loop must handle two passes:

1. Run completes → rule-based + LLM evaluate immediately → preliminary update
2. PR review arrives (via webhook) → evaluator re-scores with human signal → reflector may generate new candidates → curator re-runs → items updated with revised confidence

The `pr_url` persisted in the execution summary is the hook for wiring this async path. Topic 10 covers the full PR feedback loop design.

## Context loading references

### Papers and web docs
- ACE paper (HTML): https://arxiv.org/html/2510.04618v3
- ACE paper (abstract): https://arxiv.org/abs/2510.04618

### Implementation repositories
- SDK-oriented implementation: https://github.com/kayba-ai/agentic-context-engine
- Reference/paper-style implementation: https://github.com/ace-agent/ace

### Local orchestrator files
- `ace-context-loading-sources.md`
- `00-ace-primer-roadmap.md`
- `01-ace-what-is-it.md`
- `02-ace-memory-model.md`
- `/Users/romulo/Projects/ngb-agent-orchestrator/orchestrator/work_planner/nodes/generate_plan.py`
- `/Users/romulo/Projects/ngb-agent-orchestrator/orchestrator/code_generator/nodes/run_goose.py`
- `/Users/romulo/Projects/ngb-agent-orchestrator/dispatcher/work_plan_formatter.py`
