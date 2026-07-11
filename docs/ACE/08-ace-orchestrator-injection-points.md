# ACE — Context Injection Design: Insertion Points in the Planner/Code Generator Flow

## What injection actually means here

Topics 1–7 covered the learning side of ACE: how signals are collected, evaluated, reflected into context items, and stored. Injection is the other half — how stored context items get *used*. A context item that sits in the store but never reaches the LLM is inert.

In `ngb-agent-orchestrator` there are exactly two places where an LLM makes consequential decisions: the **planner** (Goose running `plan.yaml`) and the **code generator** (Goose running `generate_code.yaml`). Everything else in the graph is deterministic Python. Context injection means: at the right moment before each Goose invocation, retrieve relevant context items and pass them in.

---

## The injection model: parameters, not prompts

The critical architectural fact is that **Goose receives its instructions entirely through recipe files and recipe parameters**. The Python nodes (`generate_plan`, `run_goose`) don't talk to an LLM directly — they shell out to Goose with a `--params` list. The LLM only sees what the recipe template renders.

This means injection doesn't require modifying any LangGraph node logic to insert text into a prompt. It means **adding a new recipe parameter** and **rendering it in the recipe template**. The Python side computes the parameter value before calling Goose.

This is good news architecturally. The injection boundary is clean: Python computes *what* to inject (retrieval), the recipe template controls *where* it appears in the LLM's context window (formatting and placement).

---

## Confidence scores: Python filters, LLMs read labels

Before describing the injection points, an important design rule that applies everywhere.

Each context item in the store has a numeric confidence score (0.0–1.0). **The LLM is the wrong consumer of raw confidence numbers.** A score of `0.87` vs `0.91` carries no actionable meaning to an LLM — it has no calibrated sense of what the difference implies. The number looks precise but is cognitively inert.

Confidence scores have two jobs, both handled by Python before the LLM sees anything:

**Job 1 — Filtering.** The retrieval function excludes items below a threshold. The LLM never sees low-confidence items; Python already filtered them.

**Job 2 — Tiering.** Python maps scores to qualitative labels before injection:

| Confidence range | Label |
|---|---|
| ≥ 0.90 | `[ESTABLISHED]` — well-validated across multiple workflows |
| 0.70–0.89 | `[PATTERN]` — observed consistently, apply where relevant |
| 0.50–0.69 | `[TENTATIVE]` — limited evidence, use as a weak prior |
| < 0.50 | excluded entirely |

The LLM understands natural language tiers. Raw scores stay in the database and the retrieval layer — they never appear in the rendered recipe prompt.

---

## Injection point 1 — The planner (`generate_plan` → `plan.yaml`)

**Where in the Python graph:** `generate_plan` node, before the `goose run` subprocess call. This node already assembles the `--params` list and writes `clarifications_path` as a temp file. A new `context_items_path` parameter follows the same pattern.

**Placement in the recipe:** Before Step 2 (Fetch JIRA Ticket) — i.e., before the LLM sees the task at all. This is the correct placement because:

- The planner reasons *with priors already active*, which is how a skilled engineer works: check team conventions and past experience before reading the new ticket, not after drafting the solution.
- If context items arrive after the planner has already analysed the repo and formed an initial plan, the LLM has to revise against already-anchored conclusions. LLMs tend to minimally adjust rather than fully reconstruct, and the revision costs extra turns.

**Retrieval key:** ticket content + inferred task type (both available in state after `fetch_ticket` — this node runs after `fetch_ticket` in the work planner subgraph).

**Rendered form in the recipe:**

```
## Prior workflow context

The following patterns were learned from previous similar workflows.
Apply them where relevant — they are guidance, not constraints.

[ESTABLISHED] Approach: SQLite schema changes require a new migration file in
state/migrations/ with a sequential prefix. Do not use inline ALTER TABLE.

[PATTERN] Concern: When modifying the state machine, check migration
compatibility before implementing node changes. Appeared in 3 prior workflows.

[TENTATIVE] Test coverage: Tests for state store changes should include
migration rollback scenarios.
```

The framing "guidance, not constraints" is deliberate. Without it, the LLM may treat high-confidence items as hard requirements, suppressing legitimate exploration of novel approaches.

---

## Injection point 2 — The code generator (`run_goose` → `generate_code.yaml`)

**Where in the Python graph:** `run_goose` node, before the `goose run` subprocess call. This node already passes `pr_comments` and `existing_branch` as parameters for the PR re-run case. A `context_items` parameter slots in alongside them.

**Placement in the recipe:** Step 1, immediately after `get_developer_rules` and before any file reading. The generate-code recipe already establishes the convention: load all context first, then act. ACE context items belong in that same initialisation block.

**Retrieval key:** `work_plan_data` tasks and `files_likely_affected` (available in state at execution time — more specific than ticket content, since the plan has already decomposed the work).

**Rendered form in the recipe:**

```
## Prior workflow context

[ESTABLISHED] Implementation: In this codebase, SQLite schema changes require
a new migration file in state/migrations/ with a sequential prefix.

[PATTERN] Test coverage: Tests for state store changes must include both the
happy path and migration rollback.
```

---

## Injection point 3 — The PR re-run path

The PR re-run path (`await_pr_approval` → `commented` → `generate_code`) already passes `pr_comments` as a discrete parameter with its own high-priority framing block ("⚠️ PR REVIEW MODE — Address Comments Before Anything Else").

When ACE is integrated, **`pr_comments` and `context_items` must remain separate parameters** — not merged into a single block. The reasons:

- Human PR comments are direct, specific, and about *this* execution. They carry near-certain relevance and implicit authority.
- ACE context items are probabilistic — they have a confidence tier and may or may not apply.
- Conflating them lets a `[TENTATIVE]` ACE item compete with a direct human instruction for the LLM's attention.

The recipe renders them in priority order: human feedback first (mandatory framing), learned patterns second (advisory framing). On re-runs, retrieval also uses PR comment content as an additional retrieval key — the code generator gets both standing patterns *and* patterns specifically relevant to the feedback type.

---

## What you are not injecting, and why

**LangGraph state.** You could put context items in `OrchestratorState` as a field. The problem: LangGraph state is checkpointed to SQLite. Large context blobs make checkpoints heavier and state harder to inspect. Temp files passed as recipe parameters keep context out of durable state, consistent with the existing `clarifications_path` pattern.

**The MCP server.** The `get_developer_rules` MCP tool delivers static rules. Making context items a second MCP tool would mean retrieval happens inside the Goose session — at LLM invocation time, in a subprocess, with no Python control over what gets retrieved or formatted. Keeping retrieval in Python before the subprocess call gives full control over filtering, labelling, logging, and testability. The LLM's role is purely consumptive.

---

## The retrieval function

Both injection points require the same new Python function — the first genuinely new code required by ACE:

```python
def retrieve_context_items(
    task_type: str,
    ticket_content: str,
    work_plan: dict | None = None,
    top_k: int = 10,
) -> str:
    """Query the context item store and return a formatted string for recipe injection."""
```

It queries the context store (designed in Topic 11), scores items by semantic similarity and confidence, maps scores to tier labels, formats the top-k results as the structured text block shown above, and returns a string. The calling node writes that string to a temp file and passes the path as a `--params` value — exactly as `clarifications_path` works today.

---

## Injection design summary

| Injection point | Python hook | New parameter | Recipe placement | Retrieval key |
|---|---|---|---|---|
| Planner | `generate_plan`, before `goose run` | `context_items_path` | Before Step 2 (Fetch Ticket) | ticket content + task type |
| Code generator | `run_goose`, before `goose run` | `context_items_path` | Step 1, after `get_developer_rules` | work plan tasks + files affected |
| PR re-run | `run_goose` (alongside existing `pr_comments`) | separate `context_items_path` | same position, below `pr_comments` block | task type + PR comment content |

---

## Context loading references

### Papers and web docs
- ACE paper (HTML): https://arxiv.org/html/2510.04618v3

### Local files
- `ace-context-loading-sources.md`
- `00-ace-primer-roadmap.md`
- `07-ace-orchestrator-current-state.md`

### Orchestrator code anchors
- `/Users/romulo/Projects/ngb-agent-orchestrator/orchestrator/work_planner/nodes/generate_plan.py`
- `/Users/romulo/Projects/ngb-agent-orchestrator/orchestrator/code_generator/nodes/run_goose.py`
- `/Users/romulo/Projects/ngb-agent-orchestrator/orchestrator/work_planner/recipes/plan.yaml`
- `/Users/romulo/Projects/ngb-agent-orchestrator/orchestrator/code_generator/recipes/generate_code.yaml`
- `/Users/romulo/Projects/ngb-agent-orchestrator/mcp_server/server.py`
