# NGB Agent Orchestrator

A local AI agent harness that turns JIRA tickets into committed code. Given a ticket key, the system plans the work using an LLM, posts the plan back to JIRA for human review, waits for approval, then executes the plan by generating code changes in a feature branch — all from the command line.

---

## How It Works

```
dispatcher run --ticket AOS-41
        │
        ▼
  ┌─────────────┐    ┌─────────────┐    ┌──────────────┐    ┌─────────────────┐
  │ Fetch Ticket │───▶│ Plan (Goose)│───▶│ Await Approval│───▶│ Execute (Goose) │
  │   (JIRA)    │    │  plan.yaml  │    │  CLI gate    │    │ execute.yaml    │
  └─────────────┘    └─────────────┘    └──────────────┘    └─────────────────┘
        │                   │                   │                    │
        ▼                   ▼                   ▼                    ▼
   Validate input     WorkPlan JSON         Suspend until       Feature branch
   Check duplicate    → posted to JIRA      approve/reject      + commit + summary
   Create DB record   → stored in SQLite    via CLI             stored in SQLite
```

1. **Plan phase** — Goose fetches the ticket, analyses the repo, and produces a structured `WorkPlan` JSON (tasks, files affected, risks). The planner posts it as a JIRA comment so the developer can review it in context.
2. **Approval gate** — the LangGraph workflow suspends. The developer runs `dispatcher --approve` or `--reject` from the terminal.
3. **Execute phase** — on approval, Goose reads the WorkPlan, creates a feature branch, implements each task, runs the test suite, and commits. A JSON execution summary (build/test status, files changed, commit SHA) is persisted to SQLite.

See [docs/architecture.md](docs/architecture.md) for a full sequence diagram and component reference.

---

## Components

| Component | Description |
|---|-----------|
| `dispatcher/run.py` | CLI entry point — orchestrates the full lifecycle |
| `graph/` | LangGraph state machine — nodes, edges, approval interrupt |
| `recipes/plan.yaml` | Goose recipe: JIRA ticket → WorkPlan JSON |
| `recipes/execute.yaml` | Goose recipe: WorkPlan → feature branch + commit |
| `state/` | SQLite persistence — workflows, audit log, migrations |
| `schemas/work_plan_v1.json` | JSON schema contract for WorkPlan documents |
| `mcp_server/server.py` | MCP server: resolves JIRA project key → Git repo URL |
| `config/litellm.yaml` | LiteLLM proxy config (routes Goose → your LLM provider) |
| `config/project-repo-mapping.md` | Maps JIRA project keys to target Git repository URLs |

---

## Environment Setup

### Prerequisites

- Python 3.12+
- [Goose CLI](https://github.com/block/goose) (`~/.local/bin/goose`)
- `acli` (Atlassian CLI) configured with JIRA credentials
- A JIRA account on `mirandags.atlassian.net`
- An LLM provider API key (Anthropic, OpenAI, or Azure)

### Installation

```bash
# 1. Clone and enter the repo
git clone <repository-url>
cd ngb-agent-orchestrator

# 2. Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies and register the CLI
pip install -r requirements.txt
pip install -e .                      # registers the `dispatcher` CLI command
pip install -r requirements-dev.txt   # for development (pre-commit, pytest, etc.)

# 4. Set up environment variables
cp .env.example .env
# Edit .env — see docs/configuration.md for all required variables

# 5. (Recommended) Auto-load .env with direnv
brew install direnv
echo 'eval "$(direnv hook zsh)"' >> ~/.zshrc  # or ~/.bashrc
direnv allow .

# 6. Install pre-commit hooks
pre-commit install

# 7. Verify setup
dispatcher --help
```

### Running the LiteLLM Proxy

Goose routes all model calls through a local LiteLLM proxy. Start it before running any recipe:

```bash
# In a dedicated terminal
source venv/bin/activate
litellm --config config/litellm.yaml --port 4000
```

### Registering the MCP Server (Repo Lookup)

The `mcp_server/server.py` tool lets Goose resolve a JIRA project key to its target Git repository URL. It must be registered in your local Goose config — this is a **one-time per-machine setup** and is not stored in the repo.

Add the following block to `~/.config/goose/config.yaml` under the `extensions:` key:

```yaml
extensions:
  repo-lookup:
    enabled: true
    type: stdio
    name: repo-lookup
    description: Resolves a JIRA project key to its Git repository URL using config/project-repo-mapping.md
    display_name: Repo Lookup
    cmd: /path/to/ngb-agent-orchestrator/venv/bin/python
    args:
      - -m
      - mcp_server.server
    env_keys: []
    bundled: false
```

Replace `/path/to/ngb-agent-orchestrator` with the absolute path to your local clone. Restart Goose after editing.

To add a new project, append a row to [config/project-repo-mapping.md](config/project-repo-mapping.md) — no server restart is needed as the file is read on every tool call.

See [docs/mcp-server.md](docs/mcp-server.md) for full details including how to run the server standalone for testing.

### Running Your First Workflow

```bash
# Run the full pipeline for a JIRA ticket
dispatcher --ticket AOS-41

# After reviewing the WorkPlan comment on JIRA, approve or reject:
dispatcher --approve --ticket AOS-41
dispatcher --reject  --ticket AOS-41 --reason "scope too broad"

# List all workflows (optionally filter by ticket)
dispatcher --list
dispatcher --list --ticket AOS-41

# Show which nodes executed and in what order
dispatcher --history --ticket AOS-41
dispatcher --history --workflow-id <uuid>
```

---

## Documentation

| Topic | File |
|---|---|
| Architecture & flow diagram | [docs/architecture.md](docs/architecture.md) |
| Environment variables & credentials | [docs/configuration.md](docs/configuration.md) |
| Running workflows, approval, lifecycle | [docs/workflows.md](docs/workflows.md) |
| Goose recipes (plan & execute) | [docs/recipes.md](docs/recipes.md) |
| SQLite state store & migrations | [docs/state-store.md](docs/state-store.md) |
| MCP server setup & repo mapping | [docs/mcp-server.md](docs/mcp-server.md) |
| Development guide (tests, pre-commit, contributing) | [docs/development.md](docs/development.md) |

---

## References

- [Confluence: Agent Harness](https://mirandags.atlassian.net/wiki/spaces/AOS/pages/2752553)
- [Confluence: Agentic Workflow Architecture](https://mirandags.atlassian.net/wiki/spaces/AOS/pages/2850817)
