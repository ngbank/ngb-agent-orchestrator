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
├── models.py       # ContextItem, ProvenanceEntry, CandidateItem dataclasses (ticket 1.6)
├── config.py       # feature flags, thresholds, tier boundaries (ticket 4.2)
├── repository/     # SQLite repositories over context_items / context_items_staged (ticket 1.6)
├── pipeline/       # trace reading, evaluation, reflection, curation (Epic 2)
├── retrieval/      # retrieve_context_items() and prompt-block rendering (ticket 4.1)
├── cli/            # `ace` entrypoint, mirrors dispatcher/run.py (Epic 3)
└── tui/            # `ace-tui` entrypoint, mirrors dispatcher/tui/ (Epic 3)
```

## Boundary rule

`orchestrator/` may import from `ace/`; `ace/` never imports from `orchestrator/` graph code — it
reads the workflow DB through its own trace reader and shares only the `state/` migration/DB layer.
