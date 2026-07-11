# ACE — Memory Model: Playbook, Skillbook, Metadata, and Provenance

## Why the memory model matters

The core loop is only as good as what it writes and reads. Unstructured lesson storage makes retrieval unreliable. Missing metadata makes curation impossible. Missing provenance makes auditing and decay uncomputable. The memory model is the schema contract between the learning loop and the retrieval layer — get it wrong and the system degrades silently.

## The two stores

**Playbook** — situational procedural knowledge. Answers: *given this type of situation, what should I do?*

```
id:          play-0042
situation:   task involves a DB schema migration
action:      verify a rollback script exists before generating migration code
source_runs: [exec-summary-291, exec-summary-305]
confidence:  0.87
created_at:  2026-03-12
last_seen:   2026-05-01
decay_score: 0.91
tags:        [database, migration, safety]
```

**Skillbook** — execution pattern knowledge. Answers: *given this type of work, how should I do it?*

```
id:          skill-0017
task_type:   generate LangGraph node
pattern:     use TypedDict for state, name node function after its role, wire edges before testing
source_runs: [exec-summary-188, exec-summary-204, exec-summary-219]
confidence:  0.93
created_at:  2026-02-20
last_seen:   2026-05-10
decay_score: 0.97
tags:        [langgraph, node-generation, pattern]
```

Playbook items are keyed on **situation** (a condition to match). Skillbook items are keyed on **task type** (a kind of work). Retrieval uses these keys differently.

## Metadata fields and their purpose

| Field | Purpose |
|---|---|
| `id` | Stable identifier for deduplication and cross-reference |
| `situation` / `task_type` | Retrieval key — what triggers this item |
| `action` / `pattern` | The lesson content |
| `source_runs` | Provenance — execution summary IDs that produced this lesson |
| `confidence` | Curation quality signal — strength of evidence |
| `created_at` | Age signal — when the item was first written |
| `last_seen` | Recency signal — when a new run last validated this item |
| `decay_score` | Composite freshness score — drives pruning decisions |
| `tags` | Faceted retrieval — domain and concern filtering |

None of these fields are cosmetic. Each is used by curation logic, retrieval logic, or both.

## Provenance design

The execution summary ID is the canonical `source_run` reference — not the PR URL. The execution summary is the stable root of a run; PRs are leaf artifacts that may not exist if a step failed. In a multi-step execution, one plan may produce multiple PRs — all should trace back to a single provenance reference. The PR URL is worth storing as an additional detail field (useful for auditing and the PR feedback loop) but not as the primary provenance anchor.

Provenance enables three things you cannot do without it:

1. **Auditing** — trace unexpected agent behaviour back to the context item, then to the run that generated it.
2. **Cascading invalidation** — if a source run is later identified as low quality (e.g. its PR was reverted), confidence on all items citing it can be automatically reduced.
3. **Deduplication by origin** — two items from the same run are one weak signal, not two independent confirmations.

## Confidence is computed, not asserted

Confidence is derived from:
- **Frequency** — how many independent runs produced corroborating signal
- **Consistency** — did those runs agree, or was the signal noisy
- **Outcome quality** — did runs following this lesson produce better results

A lesson observed once has low confidence regardless of how obvious it seems. At early adoption with low run volume, require at least two or three independent source runs before injecting an item into context.

### Curation merge rule

When two items share the same situation/task key and similar content but different source runs, the curator merges them into one item, unions the `source_runs` lists, and recalculates confidence upward. The merged item is one lesson with multiple independent confirmations — the source run list must be preserved on the merged item, not discarded, to retain full audit capability.

## The decay problem

Lessons go stale as codebases evolve and conventions change. The `decay_score` model:

```
decay_score = recency_weight × (days_since_last_seen / max_age) + confidence_weight × confidence
```

Items below a threshold are candidates for pruning. Items above threshold but not recently seen are candidates for re-validation on the next relevant run.

The critical scenario `last_seen` solves: a lesson created 8 months ago with high initial confidence but never encountered since. `created_at` looks old; raw `confidence` looks high. Without `last_seen` you cannot distinguish "validated across 50 recent runs" from "validated once in October and never triggered again." The latter should decay aggressively even if its confidence score appears healthy.

Decay is the part most implementations skip, and it is how context stores accumulate silently wrong information over time.

## Mapping to the orchestrator

The orchestrator currently persists state in `state_store.py` — ephemeral to the run. The ACE memory model is the design for a *persistent, cross-run* version alongside that state. The playbook and skillbook would live where the planner (`generate_plan.py`) can read before generating a plan, and where a future evaluator can write after a run completes.

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
- `/Users/romulo/Projects/ngb-agent-orchestrator/state/state_store.py`
- `/Users/romulo/Projects/ngb-agent-orchestrator/orchestrator/work_planner/nodes/generate_plan.py`
