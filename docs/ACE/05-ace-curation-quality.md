# ACE — Curation Quality: Deduplication, Harmful Signal, Decay, and Pruning

> **Amended by Topic 15 (Injection-time synthesizer).**
> The deduplication and contradiction sections below describe the curator's *original* scope. Under the current design, semantic consolidation of LLM-paraphrased items moves out of the curator entirely and happens at injection time via an inference-driven synthesizer. The curator's `merge` step is retained only for exact-duplicate dedup, and contradiction detection populates a `conflicts_with` flag on both items rather than setting a blocking `status='conflicted'`. Read the amendments at the end of the two affected sections below, and Topic 15 for the full rationale. The harmful-signal, decay, and pruning sections are unaffected.

## Why curation exists

The learning loop adds to the memory stores continuously. The reflector produces candidates; the evaluator scores them. But neither component owns the *health* of what's already in the store. Without active maintenance, the stores degrade: near-duplicate lessons accumulate, bad signal from accidental successes sneaks in, and lessons written against an old version of the codebase quietly misdirect the agent on every relevant future run.

The curator treats the store as a living artifact. It answers: *given everything we've accumulated, what deserves to stay, in what form, and at what weight?*

Four problem classes drive its design.

## 1. Deduplication

**Problem.** Over many workflow runs, similar situations generate similar lessons. A playbook might accumulate a dozen items all saying variations of "be careful with SQL migration ordering" — each worded differently, with slightly different situation keys, generated from different runs. Retrieving several of them wastes token budget without providing additional guidance. The signal is diluted.

**Mechanism.** The curator periodically computes pairwise similarity across items in the same store. Items above a similarity threshold become candidates for one of two strategies:

- **Merge** — produce a single consolidated item. The merged item inherits the union of situation keys, the higher confidence score, and a `provenance` list pointing to all source items.
- **Subsume** — if one item is a specialization of the other (not just similar but narrower in scope), the general item survives and the specific case becomes a variant annotation on it.

**Tradeoff.** Overly aggressive deduplication destroys nuance. Two items can have similar prose but legitimately different scopes — one for greenfield migrations, one for production schema changes. Merging them produces an item that matches everywhere but guides precisely nowhere. The similarity threshold and merge strategy both require tuning, and the merge output should be treated as a candidate requiring review rather than an automatic write.

**Distinction from the diversity filter.** The curator deduplicates at write/maintenance time, operating globally across the store. The diversity filter (Topic 4) deduplicates at retrieval time, operating on a candidate set relative to a specific incoming task. They are complementary: the curator keeps the store healthy long-term; the diversity filter handles residual clustering in any given retrieval.

**Amendment (Topic 15).** Under the synthesizer model, the curator's `merge` and `subsume` strategies are retained *only* for exact or near-literal duplicates (repeat mining runs on the same workflow). Semantic consolidation of paraphrase variants — the very case the tradeoff paragraph above warned about — is no longer attempted at write time. Paraphrase variants co-exist in staging under separate `occurrence_count` counters; the synthesizer collapses them contextually when it renders the injection block for a specific ticket. The similarity threshold stays conservative for the same reason: false merges are more expensive than duplicated storage.

## 2. Harmful signal handling

**Problem.** Not all successful outcomes reflect good behavior. The agent may have succeeded by coincidence, or via a shortcut that passed automated checks but introduced debt caught by a human reviewer after merge. If the reflector ingests that outcome uncritically, a bad lesson enters the store and begins influencing every future run that resembles that situation — context poisoning.

**Mechanism.** The curator applies filters at two points:

- **Write-time confidence gate.** Items below a minimum confidence threshold from the reflector go to a *staging area*, not the live store. They accumulate evidence across multiple runs before promotion.
- **Contradiction detection.** If a new candidate directly contradicts an existing item — same situation key, opposite guidance — it is flagged for human review rather than auto-merged.
- **Retrospective penalty.** If downstream feedback (PR rejections, CI failures on code generated using this pattern) indicates an item contributed to a bad outcome, its confidence score is penalized. Enough penalties trigger re-review.

**The deliberate asymmetry.** The staging-area pattern introduces latency — a genuine lesson learned today won't help until it's promoted. This is intentional. A false positive in the live store actively misdirects the agent on every relevant run until someone notices and removes it. A delayed true positive means the agent learns slightly slower. It is safer to build confidence across multiple corroborating runs than to eagerly store a bad context item. The asymmetry favors caution on writes.

**Amendment (Topic 15).** Contradiction handling no longer sets `status='conflicted'` on either item. Instead, the curator populates a symmetric `conflicts_with` array on both items (each holding the other's ID) and leaves them in normal staged state. Retrieval passes `conflicts_with` through to the synthesizer, which surfaces both angles in its rendered document rather than silently choosing. This changes contradiction from a blocking event to an injection-time rendering signal — humans still resolve contradictions eventually (via the TUI conflict queue, Epic 5), but the pipeline no longer stalls on them.

## 3. Decay

**Problem.** Items are written at a point in time. The codebase evolves, tooling changes, team conventions shift. An item accurate a year ago may be actively misleading today. Without decay, old items retain full confidence and compete equally — or win outright, because they've accumulated more historical endorsement.

**Mechanism.** Each item carries a `last_validated` timestamp and a `decay_score` computed from it. Two decay modes work in combination:

- **Passive decay** — the score decrements on a schedule (e.g., halves every N days regardless of usage). Orphaned items that are never retrieved fade automatically.
- **Active revalidation** — retrieval or usage events reset the decay clock. Items regularly retrieved and acted upon stay fresh organically.

**The evergreen pin.** Some items remain valid even when no similar task has run recently — stable codebases receive less activity, so high-quality items generated for that code never get revalidated and decay passively. An evergreen pin exempts an item from passive decay. Critically, the pin should carry a **review cadence**, not a permanent exemption. Without a review cadence, pinned items become silent technical debt — they never decay, but they also never get questioned as the codebase evolves around them. A pin means "exempt from passive decay *and reviewed every N days*."

**Tradeoff.** Decay is a proxy for relevance, not a direct measure. The pin addresses the most obvious failure case, but review cadence discipline is required to prevent the pin from becoming a mechanism for avoiding hard curation decisions.

## 4. Pruning

**Problem.** Decayed items don't need to stay in the store indefinitely. They consume index space, add to deduplication overhead, and introduce noise in similarity searches.

**Mechanism.** Pruning triggers when an item's `decay_score` falls below a hard floor *and* it has had zero retrieval hits in a defined window (e.g., 90 days). Before deletion:

1. Archive the item with full provenance (for audit and potential recovery).
2. Check for downstream references — does another item cite this one in its `provenance` chain?
3. Hard-delete only after both checks pass.

A **soft-retire** state is a safer default: the item stays in the index but is excluded from active retrieval. Hard pruning runs on a much longer cadence than decay scoring — it is a maintenance operation, not a live-pipeline step.

## The curator as a background process

The curator does not run inline with the workflow. It runs as a periodic background process operating on the store asynchronously:

- **Store reads are isolated** — the live store the agent retrieves from is not locked during curation. The curator operates on a working copy or with optimistic locking.
- **Output is a diff** — the curator produces proposed changes (merges, confidence adjustments, staging promotions, retirements) that are auto-applied or queued for review depending on impact magnitude.
- **It must be idempotent** — it may re-process the same items across runs without producing cumulative side effects.

## Mapping to the orchestrator

In the `ngb-agent-orchestrator` context:

- The **staging area** maps to a separate collection or table (`context_items_staged` vs. `context_items`), promoted by a periodic job.
- **Deduplication** uses the same vector index as retrieval but in pairwise batch mode — not a per-request operation.
- **Decay scoring** is a materialized column updated by a nightly job, or computed on-read and cached.
- **Contradiction detection** requires an LLM call per pair — the most expensive curator operation; rate-limit or batch it.

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
