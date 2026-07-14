# ACE Implementation Plan — Epics and Tickets

This is the execution plan for building the ACE context engine inside `ngb-agent-orchestrator`.
It sequences the work so that each epic delivers something usable **and** exercises the next
layer of ACE understanding. It follows the rollout blueprint (topic 14): shadow learning with
human review first, injection second, automation third. A separate ontology-mining track
(Epics 7–8) follows the context-item work.

**Deliberate deviation from the roadmap's Wave A:** the implementation track suggests injection
first for fast visible value. This plan front-loads mining + human review instead, because
(a) the store must contain items before injection is useful, and (b) the goal is to build
confidence through manual review before any learned context influences runtime behavior. This is
the trace-first path from topic 06 and Phases 0–1 of the rollout blueprint.

---

## Two learning artifacts, two lifecycles

| | Context items | Ontology relationships |
|---|---|---|
| What they are | Probabilistic behavioral patterns ("migrations need a sequential prefix") | Amendments to the canonical domain model (`docs/ACE/ontology.yaml`) |
| Truth model | Confidence-scored, tiered, decays over time | Binary: canonical or not; no confidence score, no decay |
| Promotion | Manual first, then automated rules (corroboration, thresholds) | **Human approval only — never automated** |
| Store | `context_items` / `context_items_staged` tables | `ontology_candidates` table (staging) → `ontology.yaml` (canon) |
| Consumption | Tier-labelled prompt block via retrieval | Relevant ontology slice rendered into prompts |
| Epics | 1–6 | 7–8 |

Both artifacts are ultimately consumed by the planner/code generator through the same injection
mechanism (recipe parameters), but their curation pipelines stay separate.

---

## Module layout

New top-level package `ace/`, structurally mirroring `dispatcher/`:

```
ace/
  __init__.py
  models.py              # ContextItem, ProvenanceEntry, CandidateItem dataclasses
  config.py              # feature flags, thresholds, tier boundaries
  repository/
    context_item_repository.py   # SQLite repo over context_items / context_items_staged
    ontology_candidate_repository.py  # (Epic 7) SQLite repo over ontology_candidates
  pipeline/
    trace_reader.py      # extraction query -> TraceBundle
    evaluator.py         # rule-based triage
    reflector.py         # LLM candidate extraction
    curator.py           # create / merge / contradict; staging writes
    runner.py            # offline mining job (idempotent)
  ontology/              # (Epic 7)
    schema.py            # ontology.yaml parser/validator, entity/relationship models
    miner.py             # LLM relationship-candidate extraction from traces
    promoter.py          # approved candidate -> ontology.yaml amendment
  retrieval/
    retrieve.py          # retrieve_context_items(), scope filter, tier labels, formatting
    ontology_slice.py    # (Epic 8) relevant-subgraph selection + rendering
  cli/
    run.py               # `ace` entrypoint (mirrors dispatcher/run.py)
    commands/            # mine.py, items.py, promote.py, stats.py, ontology.py, common.py
  tui/
    app.py               # `ace-tui` entrypoint (mirrors dispatcher/tui/app.py)
    screens.py, widgets.py, modals.py, actions.py, action_registry.py
```

`pyproject.toml` additions:

```toml
[project.scripts]
ace = "ace.cli.run:run"
ace-tui = "ace.tui.app:run_tui"
```

and `ace*` added to `[tool.setuptools.packages.find] include`.

Boundary rule: `orchestrator/` may import from `ace/` (retrieval, pipeline node);
`ace/` never imports from `orchestrator/` graph code — it reads the workflow DB through its own
trace reader and shares only the `state/` migration/DB layer.

---

# Track 1 — Context items (Epics 1–6)

## Epic 1 — Foundations: schema and module scaffold

**Goal:** the context-item store exists, the `ace` package exists, nothing behavioral changes.
**ACE learning:** memory model (topic 02), persistence design (topic 11), impl topic 85.
**Rollout phase:** 0.

| # | Type | Ticket | Notes |
|---|------|--------|-------|
| 1.1 | chore | Scaffold `ace` package and packaging entries | Empty modules, pyproject scripts, packages.find; CI/pyright green |
| 1.2 | feat | Migration 012: `context_extraction_log` table | ACE-owned idempotency ledger for the mining job; keeps ACE writes out of `workflows` |
| 1.3 | feat | Migration 013: `workflows.rejection_reason` + write path | Write alongside status change in `update_status()`; removes the audit_log JOIN |
| 1.4 | feat | Migration 014: `context_items` + `context_items_staged` + indexes | Schema exactly per topic 11, incl. the 5 indexes |
| 1.5 | feat | `pr_comments` JSON refactor (migration 015) + one-time backfill script | Do BEFORE first extraction pass so the reader is single-format (topic 07 ordering note) |
| 1.6 | feat | Domain models + `ContextItemRepository` with tests | CRUD, staged ops (promote/reject with timestamps), provenance append, confidence update |

**Exit criteria:** migrations apply cleanly to a copy of `state/local.db`; repository round-trips
items with provenance; backfill converts all historical `pr_comments` rows.

## Epic 2 — Offline mining pipeline (shadow learning)

**Goal:** `ace mine` can be pointed at historical workflows and fills the staging table.
Nothing reaches runtime. This is Phase 1 shadow learning.
**ACE learning:** learning loop (topic 03), trace-first adoption (topic 06), pipeline design (topic 09), impl topics 83/84.

| # | Type | Ticket | Notes |
|---|------|--------|-------|
| 2.1 | feat | Trace reader: extraction query + `TraceBundle` model | The topic-07 query, minus the audit_log JOIN (uses 013 column); anti-joins `context_extraction_log` for eligibility |
| 2.2 | feat | Rule-based evaluator with tests | Encode the topic-09 triage table verbatim; verdicts: proceed / skip / flag |
| 2.3 | feat | Reflector: LLM candidate extraction | Candidate schema from topic 09; prompt enforces generalisability (no ticket keys/branches); structured output validation |
| 2.4 | feat | Curator: staging writes with create/merge/contradict | Keyword-similarity matching (no embeddings yet); quality gate reformulates/discards run-specific facts; ALL output goes to staging. **AOS-273 trims this scope**: `merge` handles exact duplicates only (semantic consolidation moves to the synthesizer, AOS-274 / topic 15); contradiction populates a `conflicts_with` array instead of a blocking `status='conflicted'` |
| 2.5 | feat | Offline mining runner | Batch over eligible workflows, per-row try/except, `learning_pipeline_failed` audit action, inserts `context_extraction_log` row on success; `--limit`, `--dry-run`, `--workflow-id` flags |
| 2.6 | chore | First historical extraction pass + calibration notes | Run against real DB; record item counts, dedup pressure, confidence distribution in a findings doc — this calibrates thresholds for Epic 5 |
| 2.7 | feat | Applicability dimensions (`project`, `repo`, `platform`) on context items | Migration 016 adds three nullable columns to `context_items` + staged (NULL = applies everywhere); Reflector emits them, Curator propagates, repository writes them; retrieval-side wiring deferred to Epic 4 (AOS-268) |

**Exit criteria:** full historical pass completes idempotently (second run processes 0 rows);
staged items have correct `last_validated` = source workflow date (not extraction date).

## Epic 3 — CLI/TUI review and manual promotion

**Goal:** you can review staged items and promote/reject them, with human review recorded as an
evidence event. This is the human-confidence loop.
**ACE learning:** curation quality (topic 05), human promotion rules (topic 11).

| # | Type | Ticket | Notes |
|---|------|--------|-------|
| 3.1 | feat | `ace` CLI scaffold + `ace mine` command | Structure mirroring `dispatcher/run.py`; wires runner from 2.5 |
| 3.2 | feat | `ace items` list/show commands | Filter by status/pattern_type/scope/confidence tier; `show` renders description + full provenance chain |
| 3.3 | feat | `ace promote` / `ace reject` commands | Promotion appends `human_review` evidence event (+0.20 fixed weight, min(·,1.0)); `--notes` for review annotations; notes-driven scope narrowing applied on promote; reject sets `rejected_at` (no hard delete) |
| 3.4 | feat | `ace-tui` app scaffold with staging queue screen | Mirror `dispatcher/tui` (app/screens/widgets/actions); table of staged items sortable by confidence/age/pattern_type |
| 3.5 | feat | TUI item detail + promote/reject/edit-scope modals | Detail pane shows provenance evidence chain; modal captures review notes; scope edit before promotion |
| 3.6 | feat | `ace stats` command | Counts by status/tier/pattern_type, staging queue age, item generation rate per mined workflow (leading health indicator, topic 13) |

**Exit criteria:** end-to-end manual flow works: `ace mine` → review in TUI → promote with notes
→ item appears in `context_items` with human evidence event and adjusted confidence.

## Epic 4 — Retrieval and injection (flagged, limited lanes)

**Goal:** promoted (active) items reach the planner and code generator prompts, behind feature flags,
with utilization logging. This is Phase 2 limited injection.
**ACE learning:** retrieval/injection (topics 04, 08, 15), impl topics 80/81/82/88.

**Sequencing note (2026-07-14):** Epic 4 was originally scoped around a flat-list injection block —
retrieval returned a formatted string, injection consumed it verbatim. Epic 2 mining runs showed
that LLM paraphrase variance defeats write-time merging (see AOS-273 for the analysis and
[15-ace-injection-synthesizer.md](15-ace-injection-synthesizer.md) for the design). Semantic
consolidation moves to an inference-driven synthesizer between retrieval and injection. The table
below reflects the revised sequence; AOS-274 is the new ticket.

| # | Type | Ticket | Notes |
|---|------|--------|-------|
| 4.1 | feat | `retrieve_context_items()` retrieval adapter (AOS-235) | Topic-11 SQL scope filter + Python keyword ranking; tier mapping (≥0.90 ESTABLISHED / 0.70 PATTERN / 0.50 TENTATIVE / <0.50 excluded); **returns `list[ContextItem]` — no formatting**; empty-store returns empty list (safe no-op). Applicability filter from AOS-268 |
| 4.2 | feat | ACE config surface + feature flags (AOS-236) | `ace_enabled` master flag, per-injection-point flags (planner/code_generator/pr_rerun), confidence threshold, top_k, and `ace_synthesizer_enabled` (default OFF; when OFF, fall back to legacy flat-list format for reversibility) |
| 4.3 | feat | Injection-time synthesizer (AOS-274) | `ace/retrieval/synthesizer.py` renders retrieved items into a structured markdown document (development_rules / architectural_approach / testing_approach / known_pitfalls) via LLM. Prompt at `ace/retrieval/prompts/synthesize.md`. Cache table `context_block_cache` keyed by `hash((ticket_key, applicability_filter, corpus_snapshot_id, recipe_target))`. See [15-ace-injection-synthesizer.md](15-ace-injection-synthesizer.md) |
| 4.4 | feat | Planner injection (AOS-237) | `generate_plan`: temp-file `context_items_path` param (clarifications_path pattern); render synthesizer output in `plan.yaml` before Fetch Ticket step with "guidance, not constraints" framing |
| 4.5 | feat | Code generator injection incl. PR re-run (AOS-238) | `run_goose`: same param pattern; `generate_code.yaml` Step 1 after `get_developer_rules`; on re-run keep `pr_comments` and synthesizer output as SEPARATE params, human feedback rendered first |
| 4.6 | feat | Utilization telemetry (AOS-239) | Log retrieval events (workflow_id, retrieved item IDs, synthesizer input IDs, synthesizer output section IDs, tiers) to audit_log/otel; this is the Phase-2→3 gate evidence |

**Exit criteria:** with flags on, a live workflow's rendered recipes contain a synthesized context
block; with flags off, zero behavior change; retrieved-item IDs and synthesizer inputs traceable
per workflow.

## Epic 5 — Live learning loop and automated promotion

**Goal:** the pipeline runs automatically at workflow completion, and well-evidenced items promote
without you. Manual review narrows to conflicts only. This is Phase 3.
**ACE learning:** curation quality (topic 05), guardrails (topic 12), impl topics 84/86.

| # | Type | Ticket | Notes |
|---|------|--------|-------|
| 5.1 | feat | `run_learning_pipeline` graph node on ALL terminal paths | approved/rejected/failed per topic 09 topology; try/except-isolated; never affects workflow terminal status |
| 5.2 | feat | Automated promotion rules | In priority order: independent corroboration (different workflow/ticket); confidence ≥0.85 + occurrences ≥3; non-contradiction window at reduced confidence; failure-path validation. Each promotion writes an evidence event naming the rule |
| 5.3 | feat | Failure-path curator mode | Distinct prompt: missing-coverage item creation + active-harm audit (was an injected item implicated? reduce confidence / flag) |
| 5.4 | feat | Decay and pruning job | `last_validated`-anchored decay; deprecation (soft) below floor; runnable via `ace decay`; never hard-deletes |
| 5.5 | feat | TUI conflict queue | Contradiction-flagged items become the manual review focus (topic 11: conflicts are the residual manual case); side-by-side conflicting items, resolve via scope refinement or deprecation |

**Exit criteria:** completed live workflow produces staged items with no manual step; a
corroborated staged item auto-promotes with auditable evidence event; pipeline failure leaves
workflow record intact.

## Epic 6 — Guardrails, measurement, and PR feedback ingestion

**Goal:** rollback controls, quality metrics, and the highest-value external signal.
Phases 3–4.
**ACE learning:** PR feedback loop (topic 10), guardrails (topic 12), measurement (topic 13), impl topics 87/88/89.

| # | Type | Ticket | Notes |
|---|------|--------|-------|
| 6.1 | feat | Suspension and safe mode | `ace suspend <item-id>` / scope-level suspension; safe-mode flag disables all learned-item injection instantly; suspended items skip retrieval but keep records |
| 6.2 | feat | Confidence-inflation watchdog | Track independent corroboration ratio (S_ind/N_elig); flag items whose confidence rises without independent support; cap + route to conflict queue |
| 6.3 | feat | Measurement report | `ace stats --report`: leading (generation rate, utilization, staging age) + lagging (clarification rounds per workflow, PR comment rounds, rejection rate) vs pre-ACE baseline |
| 6.4 | feat | PR feedback ingestion as evidence events | Normalize structured `pr_comments` rounds into reflector input with dedupe keys (topic 10); folds review conversations into the loop |
| 6.5 | docs | Operating runbook | Rollback procedure, safe-mode activation, review cadence, threshold recalibration process |

**Exit criteria:** safe mode verifiably zeroes injection in one command; stats report compares
against the Phase-0 baseline window.

---

# Track 2 — Ontology relationship mining (Epics 7–8)

Starts after Epic 3 at the earliest (it reuses the trace reader, mining runner pattern, CLI/TUI
shell, and review workflow muscle built in Track 1). Recommended start: after Epic 4, so Track 1
lessons about retrieval/injection inform ontology consumption design.

**Governing rule:** `docs/ACE/ontology.yaml` is canon. The miner only ever writes to
`ontology_candidates`. The ONLY path from candidate to canon is explicit human approval —
no corroboration rules, no confidence thresholds, no automated promotion, ever. Approved
candidates are applied to the YAML by the promoter and the file change is reviewed like any
other code change (committed on a branch, PR'd).

## Epic 7 — Ontology candidate mining and human-only curation

**Goal:** mining proposes new/changed entity relationships discovered in workflow traces;
you review them against the canonical model and approve or reject; approvals amend
`ontology.yaml`.

| # | Type | Ticket | Notes |
|---|------|--------|-------|
| 7.1 | feat | Ontology schema module: parser, validator, models | Load `ontology.yaml` into typed models (Domain, Entity, Relationship, cardinality); validate referential integrity (every target exists); round-trip serialization preserving comments/ordering. Also fixes existing inconsistencies surfaced by validation (e.g. `RewardTransactions` vs `RewardTransaction` naming, `1..0` cardinality) |
| 7.2 | feat | Migration 016: `ontology_candidates` table | Candidate = source_entity, relationship_name, target_entity, cardinality, rationale, kind (`new_relationship` / `modified_relationship` / `new_entity_hint`), provenance JSON (same evidence-event shape as context items), status (`proposed` / `approved` / `rejected`), reviewed_by/at, review_notes. Never hard-deleted |
| 7.3 | feat | Ontology miner pipeline | LLM pass over TraceBundles (reuses 2.1 reader + 2.5 runner pattern, separate `ontology_extracted_at` marker): given the canonical ontology + trace, propose relationships evidenced in the work but absent from canon; dedupe against canon AND existing candidates; all output → `ontology_candidates` as `proposed` |
| 7.4 | feat | `ace ontology` CLI commands | `mine`, `list`, `show` (candidate + evidence + affected canon slice), `approve`, `reject` with `--notes`; approval records reviewer identity |
| 7.5 | feat | TUI ontology review screen | New screen in `ace-tui`: candidate queue; detail view renders the candidate as a diff against the current canonical entity definition; approve/reject modals with notes |
| 7.6 | feat | Promoter: approved candidate → `ontology.yaml` amendment | Applies the relationship into the YAML via the 7.1 serializer; appends provenance to a `changelog` section or sidecar file; re-validates; leaves the file change for normal git review. Idempotent (re-running skips already-applied candidates) |
| 7.7 | chore | First ontology mining pass + findings | Run over historical traces; record candidate volume, precision impressions, prompt adjustments — calibrates whether trace signal is rich enough for ontology discovery |

**Exit criteria:** end-to-end flow works: `ace ontology mine` → review diff in TUI → approve →
`ontology.yaml` contains the new relationship with provenance recorded; rejected candidates never
touch the YAML; validator passes before and after every amendment.

## Epic 8 — Unified context consumption

**Goal:** the planner/code generator consume BOTH learned context items and the relevant ontology
slice through the established injection mechanism, each with its own framing and flag.

| # | Type | Ticket | Notes |
|---|------|--------|-------|
| 8.1 | feat | Ontology slice selector + renderer | Given ticket content / work plan, select the relevant subgraph (seed entities by keyword match, expand 1 hop); render as a compact prompt block ("Domain model — canonical entities and relationships"); canon carries no tier labels — it is authoritative, framed as constraints (unlike context items' "guidance, not constraints") |
| 8.2 | feat | Ontology injection at planner + code generator | Separate `ontology_path` recipe parameter alongside `context_items_path`; own feature flag; never merged into the context-items block — canonical facts must not compete with probabilistic patterns for authority |
| 8.3 | feat | Context budget management | Combined token budget across ontology slice + context items + pr_comments; priority order: human PR feedback > ontology slice > context items; truncation strategy per block |
| 8.4 | feat | Ontology utilization telemetry | Log injected entities/relationships per workflow, mirroring 4.5; enables asking "did the domain model actually change planner behavior?" |

**Exit criteria:** a live workflow's rendered recipe shows both blocks, correctly framed and
separately toggleable; disabling either flag removes exactly that block.

---

## Sequencing summary

```
Epic 1 (schema/scaffold) ──► Epic 2 (mining, staged-only) ──► Epic 3 (CLI/TUI review)
                                                                    │
                              you now USE items manually ◄──────────┘
                                                                    ▼
                              Epic 4 (flagged injection) ──► Epic 5 (live loop + auto-promotion)
                                                    │               ▼
                                                    │        Epic 6 (guardrails / measurement / PR signal)
                                                    ▼
                              Epic 7 (ontology mining + human-only review)
                                                    ▼
                              Epic 8 (unified consumption: items + ontology)
```

- After Epic 3 you have the complete manual loop: mine → review → promote.
- After Epic 4 promoted items influence real runs (reversible via flags).
- After Epic 5 the system learns continuously; you review only conflicts.
- Epic 6 makes it safe to trust and expand.
- Epic 7 extends mining to domain-model discovery with a strictly human gate.
- Epic 8 brings both artifact types into the agent's context, separately framed and flagged.

Epic 7 can start any time after Epic 3 if you want to parallelize; Epic 8 depends on both
Epic 4 (injection mechanism) and Epic 7 (approved ontology content). Epics 5/6 and 7/8 are
independent tracks after Epic 4.

Within each epic, tickets are ordered by dependency; 1.5 (pr_comments refactor) must land before
2.1 (trace reader) per the topic-07 ordering note.

## Baseline reminder (Phase 0)

Before enabling Epic 4 injection, freeze a 4–8 week baseline window of the metrics in 6.3
(clarification rounds, PR comment rounds, rejection rate, segment mix) from existing workflow
data — this is a query/notebook task, not new infrastructure, and can be folded into ticket 6.3
or done alongside Epic 2.
