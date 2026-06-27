# Development Guide

---

## Environment Setup

```bash
git clone <repository-url>
cd ngb-agent-orchestrator

python3 -m venv venv
source venv/bin/activate

pip install -r requirements.txt
pip install -r requirements-dev.txt
```

---

## Pre-commit Hooks

All code quality checks run automatically on every `git commit` via pre-commit. Install the hooks once after cloning:

```bash
pre-commit install
```

### Hooks

| Hook | What it checks |
|---|---|
| `trailing-whitespace` | Removes trailing whitespace |
| `end-of-file-fixer` | Ensures files end with a newline |
| `check-yaml` | Validates YAML files (recipe files, config) |
| `check-json` | Validates JSON files (schemas) |
| `check-merge-conflict` | Blocks accidentally committed conflict markers |
| `check-added-large-files` | Blocks files > 500 KB |
| `detect-private-key` | Blocks API keys and private keys |
| `black` | Enforces consistent Python formatting (line-length=100) |
| `isort` | Sorts Python imports (black-compatible profile) |
| `flake8` | Linting: unused imports, line length, undefined names |
| `mypy` | Type checking (`--ignore-missing-imports`) |
| `pytest` | Runs the full test suite |
| `check-sql-migrations` | Blocks bare `DROP TABLE` without `IF EXISTS` in migration files |

### Running Hooks Manually

```bash
# Run all hooks against all files
pre-commit run --all-files

# Run a specific hook
pre-commit run black --all-files
pre-commit run pytest --all-files
```

### Skipping Hooks (Emergency Only)

```bash
git commit --no-verify -m "message"
```

Use sparingly. The CI pipeline runs all checks independently.

---

## Running Tests

```bash
# Run all tests
python -m pytest tests/ -v

# Run with coverage
python -m pytest tests/ --cov=dispatcher --cov=orchestrator --cov=state --cov-report=term-missing

# Run a specific file
python -m pytest tests/test_state_store.py -v

# Run a specific test
python -m pytest tests/test_jira_client.py::TestJiraClient::test_get_ticket_success -v
```


The test suite covers:
- `test_dispatcher.py` — CLI entry point, workflow orchestration
- `test_graph_edges.py` — LangGraph routing functions
- `test_jira_client.py` — JIRA client (happy path, auth errors, not found)
- `test_state_store.py` — SQLite state store (CRUD, migrations, audit log)
- `test_tui.py` — TUI presentation helpers and Textual Pilot tests
- `test_tui_integration.py` — TUI smoke tests against a seeded database
- `test_work_plan_formatter.py` — WorkPlan → JIRA comment formatter
- `test_work_plan_validator.py` — WorkPlan JSON schema validation
Test files live in `tests/`. Run `python -m pytest tests/ -v` to see the full list.


---

## Code Style

- **Formatter**: `black` (line-length=100)
- **Import order**: `isort` with `profile = black`
- **Linter**: `flake8` (max-line-length=100, ignores E203/W503 for black compatibility)
- **Type checker**: `mypy` (--ignore-missing-imports)

Configuration lives in `pyproject.toml` (black, isort, mypy, pytest) and `.flake8`.

Auto-format before committing:
```bash
python -m black dispatcher/ orchestrator/ state/ tests/ scripts/
python -m isort dispatcher/ orchestrator/ state/ tests/ scripts/
```

---

## Project Structure

```
ngb-agent-orchestrator/
├── .github/
│   ├── copilot-instructions.md   # Copilot workflow rules
│   └── pull_request_template.md
├── config/
│   └── litellm.yaml              # LiteLLM proxy model routing
├── dispatcher/
│   ├── jira_client.py            # JIRA API client
│   ├── run.py                    # Main dispatcher entry point
│   ├── tui/                      # Textual TUI for workflow management
│   │   ├── app.py                # Main TUI App class
│   │   ├── widgets.py            # Workflow list, detail pane, status bar
│   │   ├── modals.py             # Input and confirmation dialogs
│   │   ├── actions.py            # Action wrappers over CLI handlers
│   │   └── screens.py            # Screen definitions
│   ├── work_plan_formatter.py    # WorkPlan → JIRA comment formatter
│   └── work_plan_validator.py    # WorkPlan JSON schema validator
├── docs/                         # Documentation (this folder)
├── orchestrator/
│   ├── builder.py                # Top-level LangGraph orchestrator
│   ├── state.py                  # OrchestratorState TypedDict
│   ├── nodes/
│   │   ├── await_approval.py     # Approval gate (LangGraph interrupt)
│   │   └── generate_code.py      # Generate code recipe node
│   ├── code_generator/
│   │   ├── nodes/                # Individual code-gen steps
│   │   └── recipes/
│   │       └── generate_code.yaml  # Goose execute recipe
│   └── work_planner/
│       ├── builder.py            # Work planner subgraph
│       ├── edges.py              # Routing functions
│       ├── state.py              # WorkPlannerState TypedDict
│       ├── nodes/                # Individual planner steps
│       ├── recipes/
│       │   └── plan.yaml         # Goose plan recipe
│       └── schemas/
│           └── work_plan_v1.json # WorkPlan JSON Schema
├── scripts/
│   └── check_sql_migrations.py   # Pre-commit SQL safety hook
├── state/
│   ├── __init__.py               # Public API exports
│   ├── migrations/               # SQL migration files
│   ├── sqlite_workflow_repository.py  # SQLite implementation
│   ├── workflow_repository.py    # WorkflowRepository protocol + module API
│   └── workflow_status.py        # WorkflowStatus enum
├── tests/                        # Test suite
├── .flake8                       # Flake8 configuration
├── .pre-commit-config.yaml       # Pre-commit hook definitions
├── pyproject.toml                # black / isort / mypy / pytest config
├── requirements.txt              # Runtime dependencies
└── requirements-dev.txt          # Development dependencies
```

---

## Adding a New Graph Node

1. Create `orchestrator/nodes/my_node.py` or `orchestrator/work_planner/nodes/my_node.py`
2. Define the node function: `def my_node(state: OrchestratorState) -> dict:`
3. Return only the state keys you want to update
4. Register it in `orchestrator/builder.py` or `orchestrator/work_planner/builder.py`
5. Add or update routing edges
6. Update `docs/architecture.md` if the flow changes

---

## Adding a New Migration

1. Create `state/migrations/00N_description.sql` (next number in sequence)
2. Write idempotent SQL — the runner will execute it exactly once
3. For destructive operations (e.g. column rename), follow the copy-rename pattern in `002_approval_statuses.sql`
4. `DROP TABLE` without `IF EXISTS` is blocked by the pre-commit hook

---

## Troubleshooting

### Virtual environment not activated

```bash
source venv/bin/activate   # macOS/Linux
```

You should see `(venv)` in your prompt.

### JIRA authentication fails (401)

1. Verify `JIRA_OAUTH_CLIENT_ID` and `JIRA_OAUTH_CLIENT_SECRET` are set and non-empty
2. Verify the OAuth client has permission to read/post in your JIRA project
3. If your JIRA uses a custom token endpoint, set `JIRA_OAUTH_TOKEN_URL` explicitly in `.env`
4. For Atlassian Cloud, ensure the app has Jira scopes such as `read:jira-work` (and `write:jira-work` if posting comments)

### Goose command not found

```bash
export PATH="$HOME/.local/bin:$PATH"
goose --version
```

If still not found, reinstall Goose following the [official instructions](https://github.com/block/goose).

### LiteLLM proxy not running

```bash
# In a separate terminal:
source venv/bin/activate
litellm --config config/litellm.yaml --port 4000
```

All Goose recipe invocations require the proxy to be running.

### Pre-commit hook failures after pulling

```bash
# Re-install hooks if .pre-commit-config.yaml changed
pre-commit install
pre-commit run --all-files
```
