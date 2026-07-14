# ACE — Retrieval and Injection: Selecting Context for the Next Run

> **Superseded in part by Topic 15 (Injection-time synthesizer).**
> This topic describes retrieval and the flat-list injection block as originally designed. The rendering half of the story — how retrieved items reach the prompt — was later moved from a formatting step inside retrieval to a dedicated inference-driven synthesizer. Read Topic 15 for the current model; the retrieval, scoring, and diversity-filter concepts below still apply.

## Why retrieval is its own problem

A well-structured playbook and skillbook are only useful if retrieval selects the right items. Retrieval is not a search problem in the traditional sense — you're composing a context window that maximizes relevance while staying within token budget and avoiding distraction. These constraints are in tension.

## When retrieval happens

Retrieval runs **before the agent begins planning**, not during execution. The input is whatever is known at that moment: incoming task description, repository/domain metadata, and any structured task tags. This is a pre-run operation — it must complete before the first LLM call.

## Two retrieval keys, two strategies

**Playbook retrieval — similarity-based**
Embed the task description and run similarity search against situation keys. High-similarity matches are candidates for injection. Asks: "does this task resemble a situation where we've learned something important?"

**Skillbook retrieval — classification-based**
Classify the task into one or more task types and retrieve skillbook items tagged for those types. This is structurally different from playbook retrieval — it benefits from an explicit task type taxonomy rather than freeform similarity.

The agent doesn't look up skillbook keys directly. A classification step sits between the incoming task and the skillbook lookup. Three approaches:

1. **Closed taxonomy (enum-based)** — a fixed set of task type labels; a lightweight LLM call classifies the task at retrieval time. Simple and predictable, but requires manual taxonomy maintenance.
2. **Embedding similarity on task_type fields** — embed the task description and match against embedded task_type field values in the skillbook. No taxonomy maintenance, but less precise.
3. **Organically growing taxonomy** — the reflector proposes task type labels when generating skillbook candidates; the curator normalizes labels over time. Scales better long-term but requires drift management.

A hybrid is practical: start with a small hand-defined taxonomy, use an LLM classifier at retrieval time, and let the reflector extend the taxonomy as new task types emerge.

**Design principle: skillbook retrieval requires a classification step; playbook retrieval requires a similarity step.** They are architecturally different lookups.

In practice both stores are used together per run — a single injection may include playbook items (situational warnings) and skillbook items (execution patterns).

## The token budget problem

Every token used for injected context is a token not available for the task, the plan, or execution history. Naive top-K retrieval fails because: (a) near-duplicate items eat budget without adding coverage; (b) similarity is not the same as utility.

Better model:
1. **Retrieve a candidate set** (top-N, where N > K)
2. **Re-rank** by composite score: `relevance × confidence × decay_score`
3. **Diversity filter** — drop items semantically near-duplicate to a higher-ranked already-selected item
4. **Budget-fit** — inject highest-ranked items until token budget is consumed

## The diversity filter

The curator handles global deduplication — merging items that are near-duplicates of each other in the store. The diversity filter solves a different, local problem.

Three genuinely distinct playbook items can each be legitimately different (different specificity, framing, situation keys — correctly kept separate by the curator) yet all score highly against the same incoming task. Injecting all three burns tokens to say essentially the same thing in this specific context.

- **Curator asks**: are these two items the same thing in the store?
- **Diversity filter asks**: given this query, does adding this item tell the agent anything it doesn't already know from what's already been selected?

The second question can only be answered at retrieval time with knowledge of the specific task. The filter becomes more load-bearing as the store matures — as item density grows around common task types, the risk of high-similarity clustering consuming the entire token budget increases.

## Injection format and the planner prompt are coupled

Where and how items are inserted into the prompt affects whether the agent acts on them. Three patterns:

**System prompt injection** — items added as a structured section before the task. High attention weight, always present. Fixed position.

**Task-prefixed injection** — items prepended to the task description, framed as "relevant context for this task." Contextually salient, can feel noisy at high volume.

**Retrieval-on-demand** — items available as a tool the agent can call. Agent controls retrieval; risk that it doesn't retrieve what it needs before it knows what it doesn't know.

**Critical coupling:** injection format alone is insufficient. If the planner system prompt doesn't establish that injected playbook items are *binding pre-conditions* — not suggestions — the model will follow its default behaviour regardless of what context items say. The agent must be explicitly taught to act on retrieved context, not just receive it. Injection format and planner prompt design must be designed together. This is covered in detail in Topic 8.

## From flat-list injection to synthesized document (see Topic 15)

The sections above assume the injected block is a formatted list of retrieved items with tier labels — the direct output of retrieval, template-rendered. Early mining runs surfaced that this model breaks down for LLM-generated context items: paraphrase variants of the same rule share too few tokens to survive a curator merge without false positives, and any merge that does succeed flattens nuance. That failure mode is what forced consolidation to move from the curator (write time) to a synthesizer (read time).

Under the current model, retrieval still does everything above — scope filter, composite scoring, diversity filter, budget fit — but returns raw items rather than a rendered block. An LLM-driven synthesizer then renders those items into a structured document (development rules / architectural approach / testing approach / known pitfalls) using the ticket context as a rendering signal. The injection format described below is still where the document lands; the document just isn't a flat list anymore.

See Topic 15 for the synthesizer design, prompt shape, caching contract, and what changed on the curator side (Topic 5).

## Retrieval hygiene: two failure modes

**Over-retrieval** — too many items injected. The agent attends to all of them partially instead of the most important ones fully. Relevant items buried among less relevant ones degrade performance ("lost in the middle" problem). Prefer fewer, higher-quality items.

**Stale injection** — high similarity score, low decay score. The lesson was relevant once but may no longer apply. `decay_score` must be a meaningful weight in the re-ranking composite, not cosmetic. The asymmetry: a stale item actively misdirects the agent; a slightly under-confident item is a weaker nudge in the right direction. Weight `decay_score` more heavily than `confidence` at injection time.

## Mapping to the orchestrator

Playbook items inject at the planner (`generate_plan.py`) — they shape the plan. Skillbook items could inject at the code generator level, closer to where patterns are applied. The task classification call for skillbook retrieval would sit in the planner flow before plan generation. Topic 8 covers the exact insertion points.

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
- `03-ace-learning-loop.md`
- `/Users/romulo/Projects/ngb-agent-orchestrator/orchestrator/work_planner/nodes/generate_plan.py`
- `/Users/romulo/Projects/ngb-agent-orchestrator/orchestrator/work_planner/nodes/await_workplan_clarification.py`
- `/Users/romulo/Projects/ngb-agent-orchestrator/orchestrator/code_generator/nodes/run_goose.py`
