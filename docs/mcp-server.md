# MCP Server: Repo Lookup + Developer Rules

The `mcp_server/server.py` module is a [Model Context Protocol](https://modelcontextprotocol.io) (MCP) server that exposes the following tools:

| Tool | Description |
|---|---|
| `get_repo_for_project(project_key)` | Returns the Git SSH URL for a given JIRA project key |
| `get_developer_rules()` | Returns the mandatory developer rules enforced on every execution session |

This allows Goose (and other MCP-compatible clients) to resolve which repository to clone before executing a WorkPlan.

---

## Project → Repository Mapping

The mapping is maintained in [`config/project-repo-mapping.md`](../config/project-repo-mapping.md) as a Markdown table:

```markdown
| Project Key | Repository URL                                          | Description        |
|-------------|---------------------------------------------------------|--------------------|
| AOS         | git@github.com-ngbank:ngbank/ngb-agent-orchestrator.git | Agent Orchestrator |
```

**To add a new project**, append a row. The server reads this file on every tool call — no restart required.

---

## Registering with Goose (one-time per machine)

Goose uses the [stdio MCP transport](https://modelcontextprotocol.io/docs/concepts/transports): it spawns the server as a child process and communicates via stdin/stdout. This registration is local to your machine and not stored in the repo.

Add the following block under `extensions:` in `~/.config/goose/config.yaml`:

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

Replace `/path/to/ngb-agent-orchestrator` with the absolute path to your local clone, e.g. `/Users/yourname/Projects/ngb-agent-orchestrator`.

Restart Goose after editing the config.

---

## Testing Standalone

Because the server uses stdio transport, running it directly in a terminal will just block waiting for MCP messages — not useful for manual testing. Instead, call the tool function directly via Python:

```bash
source venv/bin/activate

python3 - <<'EOF'
from mcp_server.server import get_repo_for_project

# Successful lookup
print(get_repo_for_project("AOS"))

# Case-insensitive
print(get_repo_for_project("aos"))

# Unknown project — raises ValueError with a helpful message
try:
    get_repo_for_project("UNKNOWN")
except ValueError as e:
    print(e)
EOF
```

Expected output:
```
git@github.com-ngbank:ngbank/ngb-agent-orchestrator.git
git@github.com-ngbank:ngbank/ngb-agent-orchestrator.git
No repository mapped for project 'UNKNOWN'. Known projects: AOS. Add an entry to config/project-repo-mapping.md.
```

---

## Architecture Note

The `_MAPPING_FILE` path is resolved relative to the server file itself (not the working directory), so the server will always find `config/project-repo-mapping.md` correctly regardless of where Goose launches it from.

---

## `get_developer_rules` — Developer Rules Tool

Returns a structured list of mandatory developer rules that the execute-plan agent must honour during every execution session.

### Rules returned

| ID | Rule | Command |
|---|---|---|
| DR-001 | Run pre-commit hooks before every commit | `pre-commit run --all-files` |
| DR-002 | Never commit directly to `main` or `master` | — |
| DR-003 | Feature branches must follow `feature/{TICKET-ID}+{summary-slug}` | — |
| DR-004 | Run the full test suite before committing | `python -m pytest tests/ -q --tb=short` |

### Return format

Each rule is a dict with:
- `id` — unique identifier (e.g. `"DR-001"`)
- `rule` — human-readable statement
- `command` — (optional) shell command to run to satisfy the rule
- `rationale` — why the rule exists

### Example call

```python
from mcp_server.server import get_developer_rules

rules = get_developer_rules()
for r in rules:
    print(r["id"], ":", r["rule"])
```

### How the execute recipe uses it

The execute recipe (`recipes/execute.yaml`) calls `get_developer_rules()` as **Step 0** — before any other action — and injects the returned rules into the agent's working context. The agent is then required to comply with all rules throughout the session, in particular running `pre-commit run --all-files` before every `git commit`.
