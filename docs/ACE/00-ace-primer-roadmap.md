# ACE Primer — Roadmap
This document is the source of truth for your interactive ACE learning flow.
## Session operating protocol (just-in-time generation)
Topics are not pre-generated. They are taught first, then written only after you explicitly advance.
Rules:
- Do not pre-create any topic markdown files.
- Status in the curriculum table is the source of truth.
- A topic can be marked covered only after its file is generated.
- When a topic file is generated, update the Document column to a markdown link: `[filename.md](filename.md)`.
### Command behavior
When you say `begin` (or equivalent):
1. Read this roadmap and `ace-context-loading-sources.md`.
2. Find the first topic with status `⬜ Not started`.
3. Generate the lesson in-chat only (no file write yet).
4. Ask comprehension questions and handle discussion.
When you say `move to topic` (or equivalent):
1. Generate the markdown file for the current topic.
2. Update this roadmap status for that topic to `✅ Covered`.
3. Stop and wait for your next command.
## How to resume a session
1. Read this roadmap first.
2. Read `ace-context-loading-sources.md` to load canonical references.
3. Find the first topic in the curriculum marked `⬜ Not started`.
4. Follow the command behavior above.
## Writing guidelines for topic documents
These files should teach progressively, not act as static reference dumps.
- **Narrative first.** Explain why a concept exists before defining it.
- **Problem → mechanism → tradeoff.** Each section should follow this flow.
- **Simple to complex.** Start with the minimal mental model, then layer details.
- **Examples for understanding.** Include concrete examples only when they clarify the concept.
- **Framework-focused.** Tie concepts back to your context engineering objective.
- **No checklist dumping.** Prefer concise explanatory prose over long bullet inventories.
- **File naming.** Topic files must be named with a two-digit topic number prefix: `NN-filename.md` (e.g. `09-ace-orchestrator-learning-pipeline.md`). The number must match the curriculum row number.
## Cross-session context requirements
Every topic document must include a `## Context loading references` section so a future agent in a new session can recover context quickly.
That section must include:
- at least one relevant paper/web documentation link,
- at least one relevant GitHub repository link,
- relevant local file paths (orchestrator docs/code anchors) to inspect.
Use `ace-context-loading-sources.md` as the baseline index and then add topic-specific references.
## Canonical source index
Primary cross-session source index:
- `ace-context-loading-sources.md`
## Curriculum
| #   | Topic                                                                                                   | Document                                                         | Status        |
| --- | ------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------- | ------------- |
| 1   | What ACE is — problem, core loop, and mental model                                                      | [01-ace-what-is-it.md](01-ace-what-is-it.md)                                       | ✅ Covered     |
| 2   | ACE memory model — playbook/skillbook structure, metadata, provenance                                   | [02-ace-memory-model.md](02-ace-memory-model.md)                                   | ✅ Covered     |
| 3   | Learning loop internals — evaluate, reflect, curate, and update ops                                     | [03-ace-learning-loop.md](03-ace-learning-loop.md)                                 | ✅ Covered     |
| 4   | Retrieval and injection — selecting context for the next run                                            | [04-ace-retrieval-and-injection.md](04-ace-retrieval-and-injection.md)             | ✅ Covered     |
| 5   | Curation quality — deduplication, harmful signal handling, decay, pruning                               | [05-ace-curation-quality.md](05-ace-curation-quality.md)                           | ✅ Covered     |
| 6   | Trace-first adoption — mining historical runs before live integration                                   | [06-ace-trace-learning.md](06-ace-trace-learning.md)                               | ✅ Covered     |
| 7   | Orchestrator integration map — where learning signals already exist in current `ngb-agent-orchestrator` | [07-ace-orchestrator-current-state.md](07-ace-orchestrator-current-state.md)       | ✅ Covered     |
| 8   | Context injection design — exact insertion points in planner/code generator flow                        | [08-ace-orchestrator-injection-points.md](08-ace-orchestrator-injection-points.md) | ✅ Covered     |
| 9   | Learning pipeline design — evaluator/reflector/curator loop over workflow traces                        | [09-ace-orchestrator-learning-pipeline.md](09-ace-orchestrator-learning-pipeline.md) | ✅ Covered   |
| 10  | PR-stage feedback loop — harvesting engineer review conversations into context items                    | [10-ace-orchestrator-pr-feedback-loop.md](10-ace-orchestrator-pr-feedback-loop.md) | ✅ Covered     |
| 11  | Persistence design — context item schema, provenance links, and migration plan                          | [11-ace-orchestrator-data-model.md](11-ace-orchestrator-data-model.md)             | ✅ Covered     |
| 12  | Integration safety model — quality gates, rollback, and blast-radius control                            | [12-ace-orchestrator-guardrails.md](12-ace-orchestrator-guardrails.md)            | ✅ Covered     |
| 13  | Measurement framework — leading/lagging signals for context quality                                     | [13-ace-measurement.md](13-ace-measurement.md)                                    | ✅ Covered     |
| 14  | End-to-end rollout blueprint — phased adoption plan for your framework                                  | [14-ace-rollout-blueprint.md](14-ace-rollout-blueprint.md)                        | ✅ Covered     |

## Supplemental deep-dive track (evaluation epistemology)

This section is intentionally separate from the base primer.
Use it as an on-demand deep dive while implementing code, then return here when you want stronger scientific grounding for evaluation design.

### How to use this track

- Do not block base-primer progress on these topics.
- Enter this track only when you request a deep dive explicitly.
- Keep status independent from the base curriculum status above.

### Supplemental curriculum

| #   | Topic                                                                                                   | Document                                              | Status        |
| --- | ------------------------------------------------------------------------------------------------------- | ----------------------------------------------------- | ------------- |
| 90  | Evaluation epistemology — claims, evidence types, independence, and falsifiability in ACE             | `90-ace-eval-epistemology.md`                        | ⬜ Not started |
| 91  | Confidence calibration science — reliability diagrams, ECE/Brier, and practical thresholding           | `91-ace-confidence-calibration.md`                   | ⬜ Not started |
| 92  | Corroboration and confidence inflation detection — causal confounders, echo loops, and anti-patterns  | `92-ace-corroboration-and-inflation.md`              | ⬜ Not started |
| 93  | Validation methodology — offline labeling, ROC/PR tradeoffs, ablations, and segment generalization    | `93-ace-eval-validation-methodology.md`              | ⬜ Not started |
| 94  | Standards and governance mapping — how to anchor ACE guardrails to formal assurance practices          | `94-ace-standards-and-governance-mapping.md`         | ⬜ Not started |

## Supplemental implementation track (code-level integration)

This section is intentionally separate from the base primer and from the evaluation-epistemology track.
Use it as an implementation-first path while building orchestrator integrations.

### How to use this track

- Prioritize this track when you want to write code now.
- Use the progressive execution order below (value first, sophistication later).
- Keep status independent from base and epistemology tracks.

### Recommended execution order (gentle sophistication)

This implementation sequence is designed for teams that are new to evaluation concepts and want fast proof of value before adding advanced learning controls.

1. Wave A - Fast path to visible value
	- Goal: get low-risk context injection running with clear telemetry.
	- Topics: `80`, `82`, `83`, `88`

2. Wave B - Better retrieval and persistence foundations
	- Goal: improve relevance and make item lifecycle durable.
	- Topics: `81`, `85`

3. Wave C - Safe promotion and rollback controls
	- Goal: prevent fragile or harmful context from becoming active behavior.
	- Topics: `86`, `89`

4. Wave D - Asynchronous learning sophistication
	- Goal: add evaluator/reflector/curator workers after the runtime path is stable.
	- Topics: `84`

5. Wave E - High-signal external feedback ingestion
	- Goal: fold PR review signal into the loop once core mechanics are trusted.
	- Topics: `87`

6. Optional theory deepening (run in parallel as needed)
	- Goal: progressively strengthen evaluation epistemology and calibration rigor.
	- Topics: `90`-`94`

### Implementation curriculum

| #   | Topic                                                                                                   | Document                                              | Status        |
| --- | ------------------------------------------------------------------------------------------------------- | ----------------------------------------------------- | ------------- |
| 80  | Integration bootstrap — feature flags, config surfaces, and minimal wiring in planner/code generator   | `80-ace-code-bootstrap.md`                            | ⬜ Not started |
| 81  | Retrieval adapter implementation — query contracts, ranking hooks, and fallback behavior               | `81-ace-code-retrieval-adapter.md`                   | ⬜ Not started |
| 82  | Injection contract implementation — planner/code generator prompt assembly and context budgets         | `82-ace-code-injection-contract.md`                  | ⬜ Not started |
| 83  | Trace capture instrumentation — event schema, provenance links, and run-level correlation IDs          | `83-ace-code-trace-instrumentation.md`               | ⬜ Not started |
| 84  | Learning pipeline workers — evaluator/reflector/curator job boundaries and idempotent retries         | `84-ace-code-learning-workers.md`                    | ⬜ Not started |
| 85  | Persistence integration — schema migration, repositories, and staged-to-active promotion transactions  | `85-ace-code-persistence-integration.md`             | ⬜ Not started |
| 86  | Guardrails in code — rollback switches, item suspension flows, and safe-mode fallbacks                | `86-ace-code-guardrails.md`                          | ⬜ Not started |
| 87  | PR feedback ingestion implementation — webhook/API ingestion, normalization, and dedupe keys          | `87-ace-code-pr-feedback-ingestion.md`               | ⬜ Not started |
| 88  | Metrics and observability wiring — utilization events, dashboards, and alerting thresholds            | `88-ace-code-observability.md`                       | ⬜ Not started |
| 89  | End-to-end dry-run and cutover — shadow mode, canary enablement, and rollback rehearsal               | `89-ace-code-cutover-playbook.md`                    | ⬜ Not started |
