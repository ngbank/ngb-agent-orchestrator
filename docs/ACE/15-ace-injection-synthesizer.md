# ACE — Injection-Time Synthesizer: Merging at Read Time, Not Write Time

## Why this topic exists

Topic 4 (retrieval and injection) described how retrieved items reach the planner: a top-K selection, a diversity filter, and a formatted block appended to the prompt. Topic 5 (curation quality) described how the curator maintains store health by deduplicating similar items so that the retrieval block does not repeat itself.

Both topics assume the shape of the injected context is a **flat list of items with tier labels** — the direct output of retrieval formatted for the model. Under that model, the curator has to consolidate paraphrase variants at write time, because retrieval cannot fix a duplicated store.

Early Epic 2 mining runs invalidated that assumption. This topic describes the architectural shift that replaces the flat-list injection model with an inference-driven synthesizer, and explains what it means for the surrounding pieces.

## The mining-side observation that forced the change

Reflector-generated context items paraphrase heavily. Two workflows that surface the same rule — for example, "protocols and their implementations belong in a dedicated service package, not in CLI modules" — produce descriptions that share only a handful of tokens. Measured on a real run (staged items from workflow `1aa2c314`), the Jaccard overlap between semantically identical LLM outputs sits around 0.25 on descriptions of ~12 tokens drawn from a domain vocabulary of ~30 words.

The curator's merge threshold is 0.35. Lowering it to catch paraphrase variants triggers false merges between genuinely distinct rules that happen to share vocabulary ("service package" appears in dozens of unrelated contexts). Any merge that does succeed flattens phrasing — scope conditions, rationale, and boundaries live in the words the LLM chose, and the merge weighted-mean replaces one phrasing with another. Merge-at-storage cannot simultaneously consolidate LLM paraphrases and preserve nuance.

Two things follow from this:

1. The curator's `merge` logic is structurally incapable of the job it was scoped for. It stays useful only for exact-duplicate dedup (repeat mining runs on the same workflow).
2. Consolidation has to happen somewhere else in the pipeline — either later (retrieval time) or on the read side (injection time).

## The design shift

**Storage stays fragmented.** Paraphrase variants co-exist as separate staged items, each with `occurrence_count = 1` in most cases. The Reflector's phrasing is preserved verbatim. The curator's remaining responsibilities shrink to quality-gate reformulation, exact-duplicate dedup, and a lightweight contradiction *flag* (a `conflicts_with` list, not a blocking status).

**Retrieval returns raw items, not a rendered block.** The retrieval adapter (Epic 4 / AOS-235) is refactored to return `list[ContextItem]` filtered by applicability (repo, project, platform — Epic 2 / AOS-268), confidence tier, and top-K. It does not format.

**A new synthesizer stage renders a structured document.** Between retrieval and injection, an LLM call takes the retrieved items plus the ticket context and produces a compact markdown document with a fixed section shape:

- **Development rules** — hard rules the code generator must follow.
- **Architectural approach** — conventions on where things live, what abstractions to use, when to introduce new modules.
- **Testing approach** — expectations for how work is verified.
- **Known pitfalls** — negative patterns worth calling out explicitly.

The synthesizer prompt instructs the model to cite source item IDs inline, prefer higher-confidence and higher-`occurrence_count` inputs when they conflict, preserve alternate phrasings under a "notes" sub-bullet when scope conditions genuinely differ, and surface both sides of any `conflicts_with` pair rather than silently choosing.

**Injection consumes the synthesizer output**, not raw items. The planner and code generator recipes receive the synthesized document via the same `context_items_path` temp-file parameter contract already established for the flat-list model — the substitution is transparent at the recipe layer.

## Why merging at read time works better than merging at write time

Three properties of the read-time merge dissolve the write-time problems:

**Nuance is preserved because the merge is context-aware.** At storage time the curator has no idea which ticket will retrieve two similar items. It must either merge (losing scope conditions) or keep them separate (accepting dilution). At injection time the synthesizer knows the ticket context and can decide: for a greenfield feature, the general form of a rule is enough; for a production-touching change, the strict form applies. The same two items can render differently for different tickets without the store having to represent both outcomes.

**Paraphrase variance becomes an asset.** Multiple phrasings of the same rule from different Reflector runs give the synthesizer more surface area to work from. If a rule surfaced through three workflows with three descriptions, the LLM has three angles to draw the strongest formulation from. Under merge-at-storage, two of those three phrasings were destined to be discarded on the weighted-mean step.

**The injected block is structured, not flat.** A section-headed document ("Development rules / Architectural approach / …") composes better in the planner's attention than a bullet list of tier-labelled items. The planner receives guidance in the shape it needs to consume, not the shape retrieval happened to produce.

## Tradeoffs

**One extra LLM call per injection.** Retrieval used to be a SQL + Python-side rank operation. The synthesizer adds a model call before the planner runs. This is mitigated by caching: cache key is `hash((ticket_key, applicability_filter_predicate, corpus_snapshot_id, recipe_target))` where `corpus_snapshot_id` is the max `updated_at` across matching staged items. Cache invalidation is implicit — when the store changes, the snapshot ID changes, and the cache key changes.

**Non-determinism at injection.** The same set of retrieved items can render slightly differently across runs. This is acceptable given that the retrieved *set* is deterministic given the filter and store snapshot; the LLM's phrasing choices vary but the source items don't. The provenance manifest records which source item IDs contributed to which section, so utilization telemetry (Epic 4 / AOS-239) still works.

**Prompt quality becomes load-bearing.** The synthesizer's output quality directly gates injection quality. A bad prompt produces a bad document from good source items. This is a hazard the flat-list model did not have — retrieval formatting was a template, not a model call.

## What this changes across ACE topics

**Topic 4 (retrieval and injection).** The "returns a formatted block" model in the "When retrieval happens" section is superseded. Retrieval returns raw items; the synthesizer renders. The diversity filter section still applies — the synthesizer is not a replacement for retrieval-time relevance scoring, only for retrieval-time formatting.

**Topic 5 (curation quality).** The deduplication section overstates the curator's responsibility. Under the synthesizer model, `merge` handles exact duplicates only; semantic consolidation moves to injection. Contradiction detection becomes a flag population (`conflicts_with` array on the item), not a `status='conflicted'` write. The staging-area pattern, harmful-signal handling, decay, and pruning are unaffected.

**Topic 8 (orchestrator injection points).** The insertion points are the same. What flows through them changes: `context_items_path` now points at synthesizer output, not a formatted retrieval block.

**Topic 11 (data model).** Add a `conflicts_with` JSON column to `context_items` and `context_items_staged` so the flag has a home. Add a `context_block_cache` table for synthesizer output caching.

## Reference implementations: how they handle deduplication

Two reference implementations are cited throughout the ACE curriculum: `ace-agent/ace` (paper-style) and `kayba-ai/agentic-context-engine` (SDK-oriented). Both were reviewed while designing this topic. Both handle deduplication at **write time**, not read time, and both use **embedding-based similarity**, not lexical.

### `ace-agent/ace`

Two-layer approach — LLM curator plus an optional `BulletpointAnalyzer`.

- **Curator prompt suppression.** The curator receives the entire current playbook plus the new reflection and is prompted to *"avoid redundancy — if similar advice already exists, only add new content that is a perfect complement."* MERGE / UPDATE / DELETE operations exist in the schema but are stubbed as TODO — the curator only emits ADD.
- **`BulletpointAnalyzer` (opt-in, off by default).** Embeds every bullet with `sentence-transformers` (`all-mpnet-base-v2`) into FAISS, computes a full pairwise cosine similarity matrix, groups any pair above a **0.90** threshold, then makes a per-group LLM call to compose a merged bullet. Combined `helpful` / `harmful` counters are preserved.

Source: [`ace/prompts/curator.py`](https://github.com/ace-agent/ace/blob/main/ace/prompts/curator.py), [`ace/core/bulletpoint_analyzer.py`](https://github.com/ace-agent/ace/blob/main/ace/core/bulletpoint_analyzer.py).

### `kayba-ai/agentic-context-engine`

Three-layer approach — separate `DeduplicateStep` in the pipeline that feeds a report into a `SkillManager` LLM which returns typed operations.

- **`SimilarityDetector`.** Cosine on embeddings (LiteLLM `text-embedding-3-small` by default, local `all-MiniLM-L6-v2` fallback). Default threshold **0.85**, section-scoped (`within_section_only=True`), lazy thread-safe model loading.
- **`DeduplicationManager.get_similarity_report()`.** Formats a natural-language report of similar pairs. The manager doesn't decide anything — it feeds the report into the next `SkillManager` prompt.
- **SkillManager returns typed operations.** Four operation types — `MERGE`, `DELETE`, `KEEP`, `UPDATE`. The standout is `KEEP`: it records a *`differentiation`* (why the two skills legitimately coexist) and stores a persistent `SimilarityDecision` so future dedup runs skip the pair.
- **`DeduplicateStep`.** Runs every `interval=10` samples (skips otherwise, since O(n²) similarity would be expensive per-sample). Explicitly decoupled from the SkillManager: *"The SkillManager only produces output; deduplication runs at a configurable interval."*

Source: [`ace/deduplication/detector.py`](https://github.com/kayba-ai/agentic-context-engine/blob/main/ace/deduplication/detector.py), [`ace/deduplication/manager.py`](https://github.com/kayba-ai/agentic-context-engine/blob/main/ace/deduplication/manager.py), [`ace/deduplication/operations.py`](https://github.com/kayba-ai/agentic-context-engine/blob/main/ace/deduplication/operations.py), [`ace/steps/deduplicate.py`](https://github.com/kayba-ai/agentic-context-engine/blob/main/ace/steps/deduplicate.py).

### Comparison

| Aspect | `ace-agent/ace` | `kayba-ai/agentic-context-engine` | Our design (post AOS-273 / -274) |
|---|---|---|---|
| Similarity signal | Semantic (mpnet, cosine) | Semantic (OpenAI or MiniLM, cosine) | Lexical (Jaccard) — exact-dedup only |
| Threshold | 0.90 (embedding) | 0.85 (embedding) | 0.35 (Jaccard) |
| Merge decision | LLM merge prompt per group | LLM-authored typed op (MERGE / DELETE / KEEP / UPDATE) | Deterministic weighted-mean (exact dupes only) |
| Frequency | Per-sample (opt-in) | Every 10 samples | Per-candidate (cheap because Jaccard) |
| Scoping | None | Within section only | Within `pattern_type` only |
| Sticky "keep separate" record | No | **Yes** (`SimilarityDecision`) | No (see AOS-276 for follow-up) |
| Consolidation timing | Write time | Write time | **Read time** (synthesizer) |
| Default state | OFF (`BulletpointAnalyzer`) / ON (curator prompt) | ON | ON (exact-dedup); read-time synthesizer flag-gated |

## How this diverges from the reference implementations

Two aspects of this design are genuine departures from both references. They are deliberate, not accidental — but they should be owned rather than hidden.

**Divergence 1: We consolidate at read time, not write time.** Neither reference implementation renders context items through an LLM at injection time. Both consolidate at write time (either via the curator's own LLM call or a separate dedup step) and inject the consolidated store as a flat block.

The reason we diverge: their playbook is per-training-run and single-domain — one consolidated shape fits every retrieval. Our corpus is cross-project and cross-repo — the "right" consolidation depends on the ticket context (repo, project, platform, ticket summary). Read-time consolidation is *what enables* contextual rendering. Write-time consolidation forecloses it, because you'd have to freeze one shape at storage time.

Cost profile shifts as a result: they pay the LLM cost once per merge event (amortized over many retrievals); we pay once per (ticket, filter, corpus-snapshot) combination. The synthesizer cache (`hash((ticket_key, applicability_filter, corpus_snapshot_id, recipe_target))`) is what makes this viable — without caching, per-injection LLM calls would be prohibitive.

**Divergence 2: We use lexical similarity for exact-dedup, not embeddings for semantic dedup.** Both references default to sentence-transformers or OpenAI embeddings. We stayed with Jaccard on tokens — but only after narrowing the Curator's job to exact-duplicate detection (AOS-273). Under that narrowed job, Jaccard is honest: false negatives are covered by the extraction-log idempotency guarantee, false positives are limited by the `pattern_type` scoping and the conservative 0.35 threshold. If AOS-273 ever expanded to include semantic similarity, we would need to switch to embeddings — but AOS-274's read-time synthesizer removes the incentive to expand.

**What we adopted from the references.** Section/type scoping (kayba's `within_section_only=True`) matches our `pattern_type` filter in AOS-273. The Reflector / Curator / synthesizer separation aligns with kayba's principle that consolidation is a distinct pipeline concern from writing. Both influences are called out in AOS-273 and AOS-274.

**What we deferred.** Kayba's `SimilarityDecision` pair-cache is architecturally attractive but speculative in our context — filed as **AOS-276** under Epic 10 (AOS-275) to pick up if the synthesizer surfaces evidence we need it. Batching the Curator's similarity pass (both references do this) is deferred to **AOS-277** in the same epic, contingent on us ever introducing embedding-based similarity to the Curator.

## Boundary with the ontology track (Epics 7–8)

Ontology injection (Epic 8 / AOS-216) faces a similar rendering question — turning a subgraph selection into a prompt block. The same synthesizer principle likely applies but with a different prompt shape: ontology is authoritative (canonical, framed as constraints), while context items are probabilistic (framed as guidance). This topic stays scoped to context items; extending synthesis to ontology is a follow-up under Epic 8.

## Sequencing implications for Epic 4

The Epic 4 ticket order changes:

1. **AOS-235** — Retrieval adapter, refactored to return `list[ContextItem]` (no formatting).
2. **AOS-236** — Config surface + feature flags, including `ace_synthesizer_enabled` (default OFF; fallback = flat-list format).
3. **AOS-274 (new)** — Synthesizer module, prompt template, cache table, telemetry surface.
4. **AOS-237** — Planner injection using synthesizer output.
5. **AOS-238** — Code generator injection using synthesizer output.
6. **AOS-239** — Utilization telemetry, extended with `synthesizer_input_ids` and `synthesizer_output_section_ids`.

## Context loading references

### Papers and web docs
- ACE paper (HTML): https://arxiv.org/html/2510.04618v3

### Reference implementations (dedup comparison)
- `ace-agent/ace` (paper-style): https://github.com/ace-agent/ace — see `ace/core/bulletpoint_analyzer.py` and `ace/prompts/curator.py`.
- `kayba-ai/agentic-context-engine` (SDK): https://github.com/kayba-ai/agentic-context-engine — see `ace/deduplication/`, `ace/steps/deduplicate.py`, `ace/protocols/deduplication.py`.

### Local orchestrator files
- `docs/ACE/04-ace-retrieval-and-injection.md`
- `docs/ACE/05-ace-curation-quality.md`
- `docs/ACE/08-ace-orchestrator-injection-points.md`
- `docs/ACE/11-ace-orchestrator-data-model.md`
- `docs/ACE/ace-implementation-plan.md`
- `ace/pipeline/curator.py`
- `ace/retrieval/` (target module for synthesizer)

### JIRA anchors
- Parent epic: **AOS-212** (ACE Epic 4 — Retrieval and injection).
- Synthesizer ticket: **AOS-274**.
- Curator scope trim (motivated by this shift): **AOS-273**.
- Applicability dimensions on staged items (retrieval filter): **AOS-268** (done).
- Optional follow-ups epic: **AOS-275** (Epic 10) — hosts speculative alignments with reference implementations.
  - **AOS-276** — Pair-decision cache (kayba's `SimilarityDecision` analogue), contingent on synthesizer evidence.
  - **AOS-277** — Batched Curator dedup pass, contingent on embedding-based similarity being introduced.
