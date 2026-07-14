# ACE — Persistence Design: Context Item Schema, Provenance Links, and Migration Plan

## Why context items need their own tables

The `workflows` table tracks execution state — one row per run, status transitions, and the artifacts those runs produced. Context items have fundamentally different lifecycle properties:

- A workflow row is created once and updated in place. A context item is created from one or many runs, reinforced across subsequent runs, merged with similar items, and decays over time.
- A workflow row is owned by a single run. A context item's provenance links to *multiple* runs.
- A workflow row is never deleted in normal operation. A context item is actively managed — promoted, demoted, merged, pruned.

Two tables are needed: a **live store** (`context_items`) and a **staging table** (`context_items_staged`) that acts as the quality gate before items reach live retrieval.

---

## The `context_items` schema

```sql
CREATE TABLE context_items (
    id               TEXT PRIMARY KEY,           -- UUID
    pattern_type     TEXT NOT NULL,              -- 'approach'|'concern'|'test_coverage'|'implementation'
    scope            TEXT NOT NULL,              -- 'task_type'|'file_pattern'|'codebase_wide'
    scope_value      TEXT,                       -- e.g. 'state_machine_change', 'state/migrations/**'
    description      TEXT NOT NULL,             -- The rendered pattern text (human + LLM readable)
    confidence       REAL NOT NULL DEFAULT 0.5, -- 0.0–1.0; drives filtering and tier labelling
    last_validated   TEXT NOT NULL,             -- ISO 8601; set to SOURCE DATE, not extraction date
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'active', -- 'active'|'staged'|'deprecated'|'conflicted'
    provenance       TEXT NOT NULL DEFAULT '[]',     -- JSON array of evidence links (see below)
    conflicts_with   TEXT NOT NULL DEFAULT '[]',     -- JSON array of item ids that give opposing guidance (AOS-273)
    project          TEXT,                       -- applicability: project short-name (typically JIRA project key), or NULL = all
    repo             TEXT,                       -- applicability: repo short name, or NULL = all
    platform         TEXT                        -- applicability: runtime tag (python|dotnet|jvm|...), or NULL = all
);
```

> **Amendment (AOS-273).** The original schema carried an `occurrence_count INTEGER` column intended to be incremented every time a Curator merge fired. Under the trimmed Curator (see [`15-ace-injection-synthesizer.md`](15-ace-injection-synthesizer.md) and [`../ace/README.md`](../../ace/README.md)) merges no longer happen for semantic paraphrase variants — only for exact-dedup on the same pattern subject — so that counter would sit at 1 on almost every row and mislead any consumer that treated it as an evidence-strength signal. Migration 017 drops `occurrence_count` and adds `conflicts_with`; evidence count is now derived from `len(provenance)` via the `ContextItem.evidence_count` property. Any future cross-workflow strength signal (AOS-278, Epic 10) will use a semantically distinct column with its own audit trail.

**`pattern_type` and `scope`/`scope_value`** drive retrieval filtering. A `codebase_wide` item with `pattern_type = 'approach'` is a candidate for every workflow. A `file_pattern` item with `scope_value = 'state/migrations/**'` is only a candidate when `files_likely_affected` overlaps with that pattern.

**Applicability dimensions (`project`, `repo`, `platform`)** are orthogonal to `scope` and added by migration 016 (AOS-268). They narrow *where* a pattern applies along dimensions retrieval can filter cheaply. `NULL` on any column means "applies to any value on that axis" — the safe default that keeps pre-existing rows correct without a backfill. `project` is a scope tag (typically the JIRA project short-name like `"AOS"`), deliberately not named `project_key` because it is not a foreign key. `platform` uses the same vocabulary as `config/project-setup.json`'s `platform` field (`python`, `dotnet`, `jvm`, ...). The Reflector emits these fields when a pattern would be **wrong** or **irrelevant** for a different value on the axis; when in doubt it leaves them `NULL` and the review UI can tighten scope on promotion.

**`confidence`** is the Curator's primary decision variable. It is never shown raw to the LLM — the injection layer maps it to a tier label. It drives: whether an item appears in retrieval at all (threshold filter), which tier label it receives (`[ESTABLISHED]` / `[PATTERN]` / `[TENTATIVE]`), and how aggressively the Curator merges vs creates when a similar candidate arrives.

**`last_validated`** is the decay anchor. The critical rule: set this to the **source date** of the workflow trace (the workflow's `created_at`), not the extraction date. Why this matters even for freshly extracted items: if you run a historical extraction pass today against a workflow that completed in January, setting `last_validated` to today makes the decay model think the item was validated recently. It won't flag for re-validation for another cycle — even though the underlying evidence is six months old. The item looks fresh when it's actually stale. The decay clock must start from when the evidence actually happened.

**`status`** enables soft deletion and conflict management. Items are never hard-deleted in normal operation — `deprecated` and `conflicted` statuses keep the record while removing it from retrieval. This preserves the audit trail and allows recovery if a deprecation was wrong.

**`provenance`** is a JSON array linking each item back to the workflow(s) it was learned from. This is the evidence chain — critical for understanding why an item exists, validating it against future runs, and providing attribution.

**Schema evolution caveat:** The JSON-in-column approach makes the provenance format a hidden contract. Adding a new field (e.g., `model_version`) means either backfilling all existing rows or accepting mixed shapes that every reader must handle. A normalised `context_item_provenance` table with explicit foreign-key columns is the right long-term design. The JSON column is the correct bootstrap — but it's the first thing to migrate once the provenance structure stabilises.

---

## The provenance entry structure

```json
{
  "workflow_id": "abc-123",
  "ticket_key": "AOS-41",
  "signal_source": "clarification_round_1",
  "signal_detail": "Reviewer corrected inline ALTER TABLE — required migration file",
  "workflow_date": "2026-05-15T14:32:00Z",
  "contributed_confidence": 0.15
}
```

`signal_source` encodes where in the trace the signal came from: `clarification_round_N`, `pr_comment_round_N`, `execution_outcome`, `pr_rejection`, `human_review`.

`contributed_confidence` is the delta this evidence event contributed. This makes the Curator's merge logic auditable — you can reconstruct how confidence built up across evidence events.

`workflow_date` (not extraction date) ensures the decay model can compute staleness correctly even when reading provenance long after extraction.

---

## The `context_items_staged` schema

Identical to `context_items` plus three columns:

```sql
CREATE TABLE context_items_staged (
    -- all context_items columns, plus:
    review_notes TEXT,    -- human reviewer annotations (e.g. scope narrowing)
    promoted_at  TEXT,    -- set when promoted to context_items; NULL if not yet
    rejected_at  TEXT     -- set when rejected from staging; NULL otherwise
);
```

Staged items hold `status = 'staged'`. Items are never hard-deleted from staging — `rejected_at` marks rejection while preserving the record.

---

## Automated promotion rules

Human review of staged items is not the only path to promotion. Four automated rules, in priority order:

**1. Independent corroboration.** When the Curator processes a new trace and finds a candidate that semantically matches an existing staged item, it promotes the staged item rather than creating a new live item. A single independent corroboration from a different workflow, different ticket, different engineer is sufficient evidence that the pattern generalises. This is the primary automated promotion path — the staging queue is essentially "waiting for a second opinion."

**2. Confidence floor + evidence count.** Items with `initial_confidence ≥ 0.85` AND `evidence_count ≥ 3` (i.e. `len(provenance) ≥ 3`) promote automatically. High initial confidence from a strong single signal (e.g., a direct rejection reason) combined with repetition is sufficient without waiting for an independent trace.

**3. Non-contradiction window.** If a staged item has been in staging for N days and no workflow has produced contradicting evidence, promote at reduced confidence. Absence of contradiction is weak positive signal — workable for stable codebase conventions.

**4. Failure-path validation.** If the Curator's failure-path audit identifies a staged item as missing context that contributed to a failure, promote it immediately with high confidence. A failure caused by a gap that was already in staging is the clearest possible evidence the item belongs in the live store.

**The residual manual case:** Only **conflicted items** — where a staged item contradicts a live item or another staged item — require human review. Automated resolution of contradictions risks silently removing valid patterns. This is the narrow, correct scope for manual intervention.

---

## Human promotion and confidence calculation

When a human reviews and promotes a staged item, the approval is treated as an additional evidence event appended to `provenance`:

```json
{
  "workflow_id": null,
  "signal_source": "human_review",
  "signal_detail": "Manually promoted by reviewer",
  "workflow_date": "2026-06-08T...",
  "contributed_confidence": 0.20
}
```

The promoted item's confidence becomes `min(initial_confidence + 0.20, 1.0)`. The `0.20` is a fixed weight — the human is making a binary decision, not providing a probability estimate, so a fixed contribution is honest about what the signal is. An engineer approving an item at `0.55` produces `0.75` (`[PATTERN]`); approving at `0.75` produces `0.90` (`[ESTABLISHED]` boundary).

**Edge case — human approves with scope narrowing.** If the reviewer annotates "applies only to state store changes, not recipe changes," the promotion function reads `review_notes` and updates `scope`/`scope_value` before writing to the live store.

**Edge case — human approves a low-confidence item.** `0.30 + 0.20 = 0.50`, the bottom of `[TENTATIVE]`. Human approval doesn't override weak evidence — it supplements it. The item enters the live store at minimum viable confidence, where it will either be reinforced by subsequent traces or decay out naturally.

---

## Migration plan

The existing migration sequence ends at `011_rename_execution_summary.sql`. New migrations in order:

| File | Change | Notes |
|---|---|---|
| `012_context_extraction_log.sql` | Creates ACE-owned `context_extraction_log(workflow_id PK, extracted_at)` | Idempotency ledger for the mining job; anti-join keeps ACE writes out of `workflows` |
| `013_rejection_reason.sql` | `ALTER TABLE workflows ADD COLUMN rejection_reason TEXT` | Moves rejection reason from audit_log to first-class field |
| `014_context_items.sql` | Creates `context_items` and `context_items_staged` with indexes | Main context store |
| `015_pr_comments_json.sql` | Declarative only — no data transform | Marks the column as expecting JSON format going forward |
| `016_context_item_applicability.sql` | Adds `project`, `repo`, `platform` columns + composite index on both context-item tables | Applicability dimensions (AOS-268); nullable, no backfill — NULL means "applies everywhere". `project` (not `project_key`) is a scope tag, not a foreign key |

**On migration 015:** The learning pipeline handles the format transition by checking whether `pr_comments` parses as valid JSON. If it does not, the workflow is skipped and deferred until a separate one-time backfill script transforms old rows. The schema migration declares intent; the data migration transforms the rows; the pipeline only processes rows that meet the new contract. This avoids dual-format handling entirely and keeps the classifier unambiguous.

**Indexes for `context_items`:**

```sql
CREATE INDEX idx_context_items_pattern_type ON context_items(pattern_type);
CREATE INDEX idx_context_items_scope        ON context_items(scope, scope_value);
CREATE INDEX idx_context_items_confidence   ON context_items(confidence);
CREATE INDEX idx_context_items_status       ON context_items(status);
CREATE INDEX idx_context_items_last_validated ON context_items(last_validated);
CREATE INDEX idx_context_items_applicability  ON context_items(project, repo, platform);
```

The retrieval function filters on `pattern_type`, `scope`, `status`, and `confidence` on every query. The decay job filters on `last_validated`. Without indexes these become full table scans as the store grows.

---

## What the retrieval query looks like

```sql
SELECT id, description, confidence, pattern_type, scope, scope_value
FROM context_items
WHERE status = 'active'
  AND confidence >= 0.50
  AND (
      scope = 'codebase_wide'
      OR (scope = 'task_type'    AND scope_value = :task_type)
      OR (scope = 'file_pattern' AND :file_path LIKE scope_value)
  )
  AND (project     IS NULL OR project     = :project)
  AND (repo        IS NULL OR repo        = :repo)
  AND (platform    IS NULL OR platform    = :platform)
ORDER BY confidence DESC
LIMIT :top_k
```

The three trailing predicates enforce applicability: a row with `platform = 'python'` is only a candidate when the current workflow is on Python; a row with `platform IS NULL` matches any workflow. Retrieval-side wiring lands with Epic 4 — migration 016 only widens the storage shape.

SQL handles scope filtering; Python handles semantic ranking and tier-label formatting before the result reaches the recipe.

---

## What does NOT go in this schema

**Embeddings.** For the first version, keyword-based similarity is sufficient — the context store will be small enough. Add a vector store when keyword retrieval starts missing relevant items.

**Full trace content.** Source traces stay in the `workflows` table. `provenance` links back by `workflow_id`. No need to copy trace content into the context store — it's already in the same database.

---

## Context loading references

### Papers and web docs
- ACE paper (HTML): https://arxiv.org/html/2510.04618v3

### Local files
- `ace-context-loading-sources.md`
- `00-ace-primer-roadmap.md`
- `05-ace-curation-quality.md`
- `06-ace-trace-learning.md`
- `07-ace-orchestrator-current-state.md`
- `09-ace-orchestrator-learning-pipeline.md`

### Orchestrator code anchors
- `/Users/romulo/Projects/ngb-agent-orchestrator/state/migrations/` (all files)
- `/Users/romulo/Projects/ngb-agent-orchestrator/state/sqlite_workflow_repository.py`
- `/Users/romulo/Projects/ngb-agent-orchestrator/state/workflow_repository.py`
