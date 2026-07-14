# ACE — Agentic Context Engine

Learns behavioral context items and ontology relationships from workflow traces, then (from Epic 4
onward) injects them into planner / code generator prompts. Mirrors the layout of `dispatcher/`.

Design docs, rollout phases, and the full epic/ticket breakdown live in
[`docs/ACE/`](../docs/ACE) — start with
[`00-ace-primer-roadmap.md`](../docs/ACE/00-ace-primer-roadmap.md) and
[`ace-implementation-plan.md`](../docs/ACE/ace-implementation-plan.md).

**Status:** scaffold only (Epic 1). Modules below are placeholders until their owning tickets land.

## Layout

```
ace/
├── models.py       # ContextItem, ProvenanceEntry, CandidateItem dataclasses
├── config.py       # feature flags, thresholds, tier boundaries (ticket 4.2)
├── repository/     # SQLite repositories over context_items / context_items_staged
├── pipeline/       # trace reading, evaluation, reflection, curation (Epic 2)
├── retrieval/      # retrieve_context_items() and prompt-block rendering (ticket 4.1)
├── cli/            # `ace` entrypoint, mirrors dispatcher/run.py (Epic 3)
└── tui/            # `ace-tui` entrypoint, mirrors dispatcher/tui/ (Epic 3)
```

## Boundary rule

`orchestrator/` may import from `ace/`; `ace/` never imports from `orchestrator/` graph code — it
reads the workflow DB through its own trace reader and shares only the `state/` migration/DB layer.

## Curator semantics (post AOS-273)

The Curator (`ace/pipeline/curator.py`) is deliberately small and does only three things at
mine time:

- **quality gate** — reformulate descriptions by stripping run-specific artifacts (ticket keys,
  branch names, commit hashes); discard if the cleaned text is too short.
- **exact-dedup safety net** — when a new candidate matches an existing pending item in the same
  `pattern_type` above the Jaccard threshold with compatible polarity, append a
  `ProvenanceEntry` to the existing row. **Confidence is not recomputed**; there is no
  occurrence counter.
- **contradiction flag** — when polarity is opposing, write both rows as normal pending staged
  items and populate `conflicts_with` symmetrically. Neither row is blocked with a
  `status='conflicted'` — the read-time synthesizer decides how to surface the pair.

Semantic consolidation of paraphrase variants moved to read time (see
[`docs/ACE/15-ace-injection-synthesizer.md`](../docs/ACE/15-ace-injection-synthesizer.md) and
AOS-274). `confidence` is stable-after-creation modulo human review and time-based decay;
`provenance` is reserved for workflow-evidence events (never used as a strength counter). Any
future cross-workflow strength signal (AOS-278, Epic 10) will use a semantically distinct column
name with its own audit trail.
