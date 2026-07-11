# ACE — Trace-First Adoption: Mining Historical Runs Before Live Integration

## Why start with traces

The natural instinct when adopting ACE is to wire it live: instrument the running orchestrator, hook the evaluator and reflector into the workflow, and let it learn from new runs going forward. The problem with this approach is that you're starting from an empty store on day one. The agent gets no benefit from the framework until enough runs have accumulated to produce useful context items — and you've taken on the full integration complexity before having any evidence the framework produces good signal.

A better adoption strategy inverts the order: **mine what already happened before wiring anything live.** Historical workflow runs already exist in your state store and PR history. They contain plans, execution steps, tool outputs, and outcomes. That's the same signal the live learning loop would produce — it's just sitting in the past, unprocessed.

Trace-first adoption means: run the evaluator and reflector over historical traces first, populate the stores before any live integration, and arrive at day one of live operation with a store that already knows something about your codebase and team.

## What a trace contains

A historical trace is a serialized record of one workflow run. In your orchestrator, this roughly means:

- The incoming task description
- The generated work plan (steps, tool assignments, reasoning)
- Per-step execution results (tool outputs, intermediate state)
- The final outcome (PR URL, success/failure signal)
- Any available downstream feedback (PR review comments, CI results)

Not every orchestrator persists all of these. The minimum viable trace for learning is: **task + plan + outcome**. Tool-level execution detail and PR feedback are valuable enhancements but not blockers for an initial extraction pass.

## The extraction pass

Running the evaluator/reflector over historical traces is structurally identical to running them over live workflow output — the inputs are the same shape. The difference is that historical traces are a batch, not a stream. This means you can:

1. **Process offline, not in the critical path.** Run the extraction as a one-time job. No latency impact on live workflows.
2. **Tune before committing.** You can inspect the raw reflector output before any item reaches the live store. This is your best opportunity to calibrate confidence thresholds, taxonomy labels, and merger logic before they matter.
3. **Backfill with volume.** A few hundred historical runs will produce more initial context items than months of live operation could, simply because the events already happened.

The extraction job doesn't require a fully productionized pipeline. A script that reads from the state store, runs each trace through the reflector prompt, collects candidates, and writes them to the staging area is enough to start.

## The cold-start problem it solves

Without trace-first, ACE has a cold-start problem with an uncomfortable shape. The first runs under live integration produce no benefit (empty store) while incurring the full cost of the learning loop overhead. If early runs happen to be unusual or atypical, the first items entering the store may be unrepresentative of normal workflow patterns. The store is biased by what happened to run first, not by what runs most commonly.

Historical traces fix this because they're a representative sample of your actual workflow distribution — if you've run 500 workflows, the common task types are already proportionally represented. The initial store reflects the real shape of your workload.

The key assumption here is that historical traces are representative of future workload. This claim can be violated by **temporal distribution shift**: if the codebase, tooling, or workflow types have changed significantly since those runs, the store is pre-populated with signal that's already decaying. A phased approach helps: weight recent traces more heavily during extraction, and treat the oldest traces as low-confidence staging candidates rather than promoting them directly.

## Signal quality in historical traces

Not all historical traces are equally useful. Three quality axes to apply during extraction:

**Outcome clarity.** Runs with unambiguous outcomes (clean merge, CI pass; or explicit rollback/rejection) produce cleaner signal than ambiguous ones (PR open but not merged, run abandoned mid-execution). Prioritize traces with resolved outcomes.

**Temporal recency.** Older traces reflect an older codebase. Apply a recency filter — traces older than a threshold contribute lower initial confidence or go to staging rather than the live store. Critically, set `last_validated` to the **trace date**, not the extraction date. Using the extraction date would make every historical item appear freshly validated regardless of age, suppressing decay on stale items at precisely the moment the decay model most needs to flag them.

**Coverage breadth.** You want traces that span your task type taxonomy. If the historical data is heavily skewed toward one workflow type, the store will be skewed too — and for any task type outside that dominant type, coverage is effectively zero. The cold-start problem isn't solved; it's displaced. Use taxonomy-aware sampling: cap traces per task type and prioritize breadth over depth in the initial extraction pass.

## PR history as a secondary trace source

Workflow execution traces capture *what the agent did*. PR review conversations capture *what engineers thought about what the agent did*. These are different signals, and both are valuable.

PR review comments are especially high-value because they represent explicit human feedback — the kind the system would otherwise have to wait months of live operation to collect. Mining PR history lets you pre-populate the retrospective feedback channel before the live loop even opens.

The extraction is more complex: you need to parse review comments, associate them with the specific plan steps or code changes they target, and classify whether they indicate a pattern to avoid or a pattern to adopt. This is a separate extraction job from the workflow trace pass. Topic 10 covers the PR feedback loop in detail — but trace-first adoption should include at least a lightweight pass over recent PR reviews, even if the classification is rough.

## What trace-first adoption gives you at go-live

When you finally wire the live learning loop, you start with:

- A populated store reflecting your real workload distribution
- Initial confidence scores calibrated against real historical outcomes
- A tuned reflector/evaluator configuration validated against known data
- A baseline to measure against — you can compare live retrieval hit rates against what the historical-trace store would have predicted

That last point matters operationally: you can measure whether live operation is improving the store faster than the historical baseline would suggest, which is an early signal of whether the learning loop is working correctly.

## Practical sequencing for your orchestrator

Given the orchestrator uses LangGraph and persists state to a state store, the trace extraction sequence is:

1. Write a one-time extraction script that reads completed workflow runs from the state store
2. For each run, construct the same input the reflector would receive from a live run
3. Run the reflector prompt; write candidates to `context_items_staged`
4. Run a manual review pass over the staged items to calibrate confidence thresholds
5. Promote items above the threshold to the live store
6. Only then wire the live learning loop

Steps 1–5 can happen entirely before any live code change to the orchestrator graph. This is the correct order: validate the signal quality before paying the integration cost.

## Context loading references

### Papers and web docs
- ACE paper (HTML): https://arxiv.org/html/2510.04618v3
- ACE paper (abstract): https://arxiv.org/abs/2510.04618

### Implementation repositories
- SDK-oriented implementation: https://github.com/kayba-ai/agentic-context-engine
- Reference/paper-style implementation: https://github.com/ace-agent/ace

### Local files
- `ace-context-loading-sources.md`
- `00-ace-primer-roadmap.md`
- `01-ace-what-is-it.md`
- `02-ace-memory-model.md`
- `03-ace-learning-loop.md`
- `04-ace-retrieval-and-injection.md`
- `05-ace-curation-quality.md`
- `/Users/romulo/Projects/ngb-agent-orchestrator/state/state_store.py`
- `/Users/romulo/Projects/ngb-agent-orchestrator/orchestrator/code_generator/nodes/run_goose.py`
