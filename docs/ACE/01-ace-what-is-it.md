# ACE — What It Is: Problem, Core Loop, and Mental Model

## Why this exists

Every time an LLM agent runs a task, it starts from scratch. It has no memory of what worked last time, no awareness that a particular approach failed on a similar codebase, and no accumulated sense of team conventions. You compensate by writing large, explicit system prompts — but those prompts are static. They don't improve from experience. They don't adapt to new patterns. They're a hand-curated snapshot, frozen at the moment you wrote them.

The alternative — fine-tuning — is expensive, requires labelled data, and is difficult to iterate quickly. There is a gap between "static prompt" and "full retraining," and ACE lives in that gap.

## What ACE is

**ACE — Agentic Context Engineering** — is a framework for making an AI agent's context self-improving. The core premise: the most valuable signal for improving future runs already exists in past runs. ACE defines a structured loop to harvest that signal, evaluate it, and inject it back into the agent's context on the next invocation.

It is not a new model architecture. It is not fine-tuning. It is a **workflow layer** that wraps your existing agent, observes its traces, and feeds curated learnings forward.

## The core loop

ACE defines four operations that repeat continuously:

**1. Evaluate** — After a run completes, assess the output quality. Did it achieve the goal? Where did it struggle? Signal sources include LLM grading, rule-based checks, and external human feedback (e.g. PR review rejections). External human feedback is the strongest signal because it cannot rationalize its own mistakes — engineer review comments represent explicit judgment on output quality, not self-report.

**2. Reflect** — Extract generalizable lessons from the evaluation. Not "this specific diff was wrong" but "the agent tends to miss migration rollback steps when the PR description omits them." This is the distillation step — moving from a specific observation to a reusable rule.

**3. Curate** — Filter, deduplicate, and prioritize the reflected lessons before they enter permanent storage. This step exists because not every lesson is worth keeping. Without it: the context grows unboundedly (token pressure, retrieval noise), duplicate rules create redundancy, contradictory rules actively mislead the agent, and stale signal — lessons that were true months ago but no longer apply — poisons future runs.

**4. Update** — Write the surviving lessons into persistent context storage, tagged with provenance (source run, confidence, timestamp). This is a mechanical write step — the curation work happens before it.

```
Run → Evaluate → Reflect → Curate → Update → [stored context]
                                                      ↓
                                              next Run (richer context)
```

## Two memory structures

ACE separates learned context into two stores:

- **Playbook** — *what to do* in certain situations. Procedural, situational knowledge. Retrieved by situation type. Example: "When the task involves a DB schema migration, always check for a rollback script."
- **Skillbook** — *how to do* something well. Execution patterns and structural conventions. Retrieved by task type. Example: "When generating a LangGraph node, follow this structural pattern."

The separation exists because retrieval needs differ. You look up playbook items when you recognize a situation. You look up skillbook items when you're about to perform a specific kind of work.

## The mental model

ACE is an **outer learning loop** that wraps your existing agent's **inner execution loop**.

Your orchestrator already has an inner loop: plan → execute → output. ACE adds an outer loop: observe → reflect → curate → improve context → the next plan is better.

The agent itself doesn't change. The *context it receives* changes, run by run, guided by accumulated experience.

```
Outer loop (ACE):
  observe traces → reflect → curate → update stored context
                                              ↓
Inner loop (orchestrator):
  [enriched context] → plan → execute → output → traces
```

## Key tradeoffs

| Design choice | Benefit | Risk |
|---|---|---|
| Harvest every run | Maximum signal volume | Noise accumulates faster |
| Reflect with LLM | Generalizes well | Can rationalize bad output |
| Skip curation | Simpler pipeline | Context poisoning, contradictions, bloat |
| Aggressive pruning | Lean context | Discard rare but important lessons |

## Correction from discussion

"Crowdsourcing" maps to **Reflect + Curate together**, not to Update. Many runs contribute observations over time, Reflect distills them into generalizable lessons, and Curate selects the best signal from that crowd. Update is just the write step where surviving lessons land — it is mechanical, not selective.

## Context loading references

### Papers and web docs
- ACE paper (HTML): https://arxiv.org/html/2510.04618v3
- ACE paper (abstract): https://arxiv.org/abs/2510.04618

### Implementation repositories
- SDK-oriented implementation: https://github.com/kayba-ai/agentic-context-engine
- Reference/paper-style implementation: https://github.com/ace-agent/ace

### Local files
- Project note: `0 Designing a Context Engineering Framework.md`
- Source index: `ace-context-loading-sources.md`
- Roadmap: `00-ace-primer-roadmap.md`
