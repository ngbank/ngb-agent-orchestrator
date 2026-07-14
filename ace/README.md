# ACE — Agentic Context Engine

Learns behavioral context items and ontology relationships from workflow traces, then injects
them into planner / code generator prompts. Mirrors the layout of `dispatcher/`.

Design docs and the full rollout breakdown live in [`docs/ACE/`](../docs/ACE) — start with
[`00-ace-primer-roadmap.md`](../docs/ACE/00-ace-primer-roadmap.md) and
[`ace-implementation-plan.md`](../docs/ACE/ace-implementation-plan.md).

**Status:** scaffold. Modules below are placeholders until they are wired up.

## Layout

```
ace/
├── models.py       # ContextItem, ProvenanceEntry, CandidateItem dataclasses
├── config.py       # feature flags, thresholds, tier boundaries
├── repository/     # SQLite repositories over context_items / context_items_staged
├── pipeline/       # trace reading, evaluation, reflection, curation
├── retrieval/      # retrieve_context_items() and prompt-block rendering
├── cli/            # `ace` entrypoint, mirrors dispatcher/run.py
└── tui/            # `ace-tui` entrypoint, mirrors dispatcher/tui/
```

## Boundary rule

`orchestrator/` may import from `ace/`; `ace/` never imports from `orchestrator/` graph code — it
reads the workflow DB through its own trace reader and shares only the `state/` migration/DB layer.

## Curator semantics

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

Semantic consolidation of paraphrase variants happens at read time (see
[`docs/ACE/15-ace-injection-synthesizer.md`](../docs/ACE/15-ace-injection-synthesizer.md)).
`confidence` is stable-after-creation modulo human review and time-based decay; `provenance` is
reserved for workflow-evidence events (never used as a strength counter). Any future
cross-workflow strength signal will use a semantically distinct column name with its own audit
trail.
