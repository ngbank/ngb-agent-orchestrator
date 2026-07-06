# Goose Recipes

Recipes are YAML files co-located with their consumer subgraph under `orchestrator/<subgraph>/recipes/`. Each recipe declares parameters, extensions (tools the agent can use), LLM settings, and a natural-language prompt.

---

## Running a Recipe Directly

Recipes are invoked by the orchestrator automatically, but you can also run them manually:

```bash
# Ensure LiteLLM proxy is running first (see docs/configuration.md)

# Add Goose to PATH if needed
export PATH="$HOME/.local/bin:$PATH"

# Run the plan recipe directly
goose run --recipe orchestrator/work_planner/recipes/plan.yaml \
  --params ticket_key=AOS-41 \
  --params output_path=workplans/AOS-41-plan.json

# Run the generate recipe directly (pass an already-generated WorkPlan)
goose run --recipe orchestrator/code_generator/recipes/generate_code.yaml \
  --params ticket_key=AOS-41 \
  --params work_plan_path=workplans/AOS-41-plan.json \
  --params output_path=/tmp/AOS-41-exec-summary.json
```

Use `goose run --recipe orchestrator/work_planner/recipes/plan.yaml --explain` to see a recipe's parameters without running it.

---

## `orchestrator/work_planner/recipes/plan.yaml` — WorkPlan Generator

**Purpose**: Turn a JIRA ticket into a structured `WorkPlan` JSON document.

**Parameters**:

| Parameter | Required | Description |
|---|---|---|
| `ticket_key` | Yes | JIRA ticket key (e.g. `AOS-41`) |
| `output_path` | Yes | File path to write the WorkPlan JSON |

**What it does**:
1. Fetches the ticket with `acli jira workitem view {ticket_key}`
2. Explores the repository structure (README, directory listing, relevant files)
3. Generates a WorkPlan JSON with LLM (model set by `GOOSE_MODEL`, temperature 0.3)
4. Validates the JSON against `orchestrator/work_planner/schemas/work_plan_v1.json`
5. Writes the validated JSON to `output_path`

**Retry policy** (`retry:` block in the recipe):
- `max_retries: 1` — a stalled LLM stream can burn ~10 min per attempt; higher values silently triple the cost with no observed benefit (message history is reset on retry), so retries are capped at one.
- `checks[0]: test -s {{ output_path }}` — the first check requires a *non-empty* file (not just existing). Goose creates the output file before the stream completes, so an aborted stream leaves a 0-byte file; requiring non-empty surfaces the real failure on check[0] instead of masking it as a JSON-decode error on check[1].
- `checks[1]` — validates the JSON against the WorkPlan schema.

**WorkPlan status values**:
- `pass` — clear scope, ready to implement
- `concerns` — implementable but has risks or open questions
- `blocked` — missing critical information; capture details in `concerns`

**Status invariant**:
- If `concerns` is non-empty, status must not be `pass`.

**Safety constraints** (enforced in the prompt):
- Planning-only run: do not modify repository source files, tests, docs, config, migrations, or recipes
- Only write `output_path` and temporary validation files under `/tmp/`
- If unrelated issues are discovered, capture them in `concerns` instead of changing code

---

## `orchestrator/code_generator/recipes/generate_code.yaml` — WorkPlan Executor

**Purpose**: Implement an approved WorkPlan by making code changes in the local repository.

**Parameters**:

| Parameter | Required | Description |
|---|---|---|
| `ticket_key` | Yes | JIRA ticket key |
| `work_plan_path` | Yes | Path to the approved WorkPlan JSON file |
| `working_dir` | Yes | Absolute path to the cloned target repository |
| `output_path` | Yes | Path to write the execution summary JSON |
| `reasoning_path` | Yes | Path to write the pre-execution reasoning and execution diary (not committed) |

**What it does**:
1. **Loads developer rules** by calling `get_developer_rules()` via the MCP server — rules are injected into the agent context and honoured throughout
2. Writes pre-execution reasoning to `reasoning_path` (not committed)
3. Reads and parses the WorkPlan JSON
4. Verifies the working directory and remote origin
5. Creates a feature branch: `feature/{ticket_key}+{summary-slug}`
6. Implements each task in order — reads `files_likely_affected`, makes precise changes
7. Runs build and test checks:
   - Build: `python -m py_compile` on modified files
   - Tests: `python -m pytest tests/ -q --tb=short`
8. Commits all changes (governed by developer rules from Step 1)
9. Pushes the branch and creates a GitHub PR
10. Writes an execution diary and execution summary JSON to `output_path`

**Execution summary format**:
```json
{
  "ticket_key": "AOS-41",
  "branch": "feature/AOS-41-goose-execute-recipe",
  "build": "pass",
  "tests": "pass",
  "files_changed": ["graph/nodes/generate_code.py", "orchestrator/code_generator/recipes/generate_code.yaml"],
  "commit_sha": "a1b2c3d...",
  "status": "success"
}
```

**Status values**:
- `success` — build pass + tests pass
- `partial` — build pass + tests fail
- `failed` — build fail or unrecoverable error (see `error` field)

**Safety constraints** (enforced in the prompt):
- Loads developer rules via MCP at startup; all rules are mandatory
- Never runs `git reset --hard` or deletes branches
- Applies a mandatory scope audit before commit: every changed file must map to a WorkPlan task; unrelated edits must be removed and logged in the execution diary

---

## Creating a New Recipe

1. Copy an existing recipe as a template (place the new file alongside the recipe used by the subgraph that will own it):
   ```bash
   cp orchestrator/work_planner/recipes/plan.yaml orchestrator/work_planner/recipes/my-recipe.yaml
   ```

2. Update the top-level fields: `title`, `description`, `parameters`

3. Adjust `settings.temperature`:
   - Planning / research tasks: `0.3`
   - Code generation / precise edits: `0.1`

4. Write the `prompt` — use `{{ param_name }}` for parameter substitution

5. Test it manually before wiring into the graph:
   ```bash
   goose run --recipe orchestrator/work_planner/recipes/my-recipe.yaml --params key=value
   ```

**Recipe file structure**:
```yaml
version: "1.0.0"
title: "Recipe Title"
description: "What the recipe does"

parameters:
  - key: param_name
    input_type: string
    requirement: required
    description: "Parameter description"

extensions:
  - type: builtin
    name: developer    # grants file system + shell access
    timeout: 300
    bundled: true

settings:
  temperature: 0.3
  max_turns: 50

prompt: |
  Instructions here...
  Reference parameters with {{ param_name }}.
```

The model is chosen by the `GOOSE_MODEL` environment variable, not the recipe — recipes stay portable across model backends.
