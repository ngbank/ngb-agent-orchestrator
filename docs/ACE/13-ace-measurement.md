# ACE — Measurement Framework: Leading and Lagging Signals for Context Quality

## Why measurement is a first-class component of ACE

ACE can continuously improve behavior, but it can also continuously amplify mistakes. Without a measurement layer, both trajectories can look similar in the short term: outputs may look faster, reviewers may approve more often, and cycle time may drop. None of those alone prove that context quality improved.

This topic exists to separate true learning from misleading noise. The goal is to measure whether injected context is actually used, whether it improves outcomes, and whether it remains safe over time.

## Minimal mental model

Use a three-layer measurement stack:

1. Retrieval quality: was relevant context selected and injected?
2. Behavioral utilization: did the planner/code generator actually use injected context?
3. Outcome impact: did that usage improve review and delivery outcomes without increasing risk?

If layer 1 looks good but layer 3 worsens, retrieval quality is not the bottleneck. If layer 3 improves without layer 2 movement, external confounders may be driving the gain.

## Metric classes

### Leading metrics (early signal)

These move quickly and help detect issues before outcome regressions become expensive.

- **Context utilization rate.** Of runs where context was injected, what share shows evidence of use?
- **High-confidence item reinforcement rate.** Of high-confidence items with valid opportunities, what share is revalidated?
- **Contradiction emergence rate.** How often new evidence conflicts with active context?
- **Promotion latency.** How long staged items take to become active (or rejected).
- **Evaluator proceed/skip ratio.** Whether the learning pipeline is extracting too little or too much signal.

### Lagging metrics (outcome truth)

These are slower but harder to game.

- PR rework rounds per workflow
- First-pass approval rate
- Time to accepted PR
- Failure recurrence rate for known patterns
- Human override frequency of active ACE items

### Guardrail metrics (safety signal)

These detect context poisoning and over-aggressive promotion.

- Harmful suggestion incidence
- Rollback trigger rate
- Blast radius of suspended items
- Confidence inflation without independent corroboration

## Context utilization rate: what it should mean

A correct definition is not "was context injected?" It is "was injected context used."

$$
\text{Context Utilization Rate} = \frac{\text{runs with evidence of context use}}{\text{runs with context injected}}
$$

Evidence of use has two forms:

- **Explicit utilization:** planner/code generator cites specific context item IDs and maps them to decisions.
- **Behavioral utilization:** produced artifacts/actions align with injected guidance in a traceable way even without explicit citation.

Best practice is to capture provisional utilization online during orchestration and validate it in the ACE loop. Online gives detail; offline validation reduces false positives.

## Reinforcement rate: avoiding task-mix bias

A raw reinforcement fraction across all runs is misleading because items are scope-specific.

Use opportunity-adjusted reinforcement:

$$
R_i = \frac{\text{reinforced events}_i}{\text{eligible runs}_i}
$$

Where an eligible run is one where the item could reasonably apply by scope:

- `task_type` match
- `file_pattern` overlap
- `codebase_wide` always eligible

Aggregate with support weighting:

$$
R_{overall} = \frac{\sum_i \text{reinforced events}_i}{\sum_i \text{eligible runs}_i}
$$

Interpretation rules:

- Low reinforcement + low eligibility: inconclusive.
- Low reinforcement + high eligibility: likely stale or overconfident item.
- High reinforcement + high eligibility: robust item.

Pair this with an exposure metric: percentage of high-confidence items with at least N eligible runs in the window.

## Contradiction emergence rate: denominator choice

There is no special meaning to "per 100" beyond readability.

$$
\text{Contradiction Rate}_{k} = k \cdot \frac{\text{new contradictions}}{\text{learning events}}
$$

Pick k for your scale:

- low volume: per 10 or 20
- medium volume: per 100
- high volume: per 1,000

What matters is consistency across time windows and publishing raw counts alongside normalized rates.

## Confounding control

Many metric movements come from exogenous effects, not ACE quality:

- easier ticket mix
- reviewer strictness drift
- release pressure windows
- codebase subsystem churn

Controls:

- segment by task family and code area
- compare ACE-on vs ACE-shadow cohorts
- report metric deltas per segment, not only global averages
- require guardrail metrics to remain stable before claiming success

A common anti-pattern is celebrating improved first-pass approvals while contradiction and rollback indicators worsen. That should be treated as a warning, not a win.

## Instrumentation contract in orchestrator

To make these metrics reliable, define explicit telemetry in planner/code generator outputs:

- used_context_item_ids
- context_to_decision_map (item -> decision line)
- unused_retrieved_item_ids with reason (`out_of_scope`, `conflict_with_hard_policy`, `low_confidence`)

This supports explainability, utilization scoring, and failure-path auditing.

## Tradeoffs

- More instrumentation improves metric quality but increases prompt and logging overhead.
- Faster promotion improves adaptability but can inflate contradiction and rollback rates.
- Strong confounder controls improve trust in results but delay reporting cadence.

A healthy measurement framework favors trustworthy trend interpretation over fast but ambiguous metrics.

## Context loading references

### Papers and web docs
- ACE paper (HTML): https://arxiv.org/html/2510.04618v3
- LangGraph docs: https://langchain-ai.github.io/langgraph/

### Implementation repositories
- SDK-oriented ACE implementation: https://github.com/kayba-ai/agentic-context-engine
- Reference ACE implementation: https://github.com/ace-agent/ace

### Local files and code anchors
- `ace-context-loading-sources.md`
- `00-ace-primer-roadmap.md`
- `09-ace-orchestrator-learning-pipeline.md`
- `10-ace-orchestrator-pr-feedback-loop.md`
- `11-ace-orchestrator-data-model.md`
- `12-ace-orchestrator-guardrails.md`
- `/Users/romulo/Projects/ngb-agent-orchestrator/orchestrator/work_planner/nodes/generate_plan.py`
- `/Users/romulo/Projects/ngb-agent-orchestrator/orchestrator/code_generator/nodes/run_goose.py`
- `/Users/romulo/Projects/ngb-agent-orchestrator/state/sqlite_workflow_repository.py`
