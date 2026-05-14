# Configuration

All runtime configuration is managed through a `.env` file at the project root. **Never commit this file.**

```bash
cp .env.example .env
# Edit .env with your credentials
```

---

## Environment Variables

### JIRA

| Variable | Required | Example | Description |
|---|---|---|---|
| `JIRA_URL` | Yes | `https://mirandags.atlassian.net` | Your JIRA Cloud instance URL |
| `JIRA_EMAIL` | Yes | `user@example.com` | Your Atlassian account email |
| `JIRA_API_TOKEN` | Yes | `ATATxxx...` | JIRA API token (see below) |

### LiteLLM SDK (model routing)

No proxy server is required. Set `GOOSE_MODEL` to a LiteLLM model string — the provider is inferred automatically from the prefix and the matching credentials are picked up from the environment.

| Model string prefix | Provider | Credentials used |
|---|---|---|
| `anthropic/…` | Anthropic | `ANTHROPIC_API_KEY` |
| `azure/…` | Azure AI Foundry / Azure OpenAI | `AZURE_API_KEY`, `AZURE_API_BASE`, `AZURE_API_VERSION` |
| `openai/…` or bare name | OpenAI | `OPENAI_API_KEY` |

### LLM Provider (one required)

| Variable | Provider | Example |
|---|---|---|
| `ANTHROPIC_API_KEY` | Anthropic | `sk-ant-...` |
| `OPENAI_API_KEY` | OpenAI | `sk-...` |
| `AZURE_API_KEY` | Azure AI Foundry | — |
| `AZURE_API_BASE` | Azure AI Foundry | `https://your-resource.cognitiveservices.azure.com` |
| `AZURE_API_VERSION` | Azure AI Foundry | `2024-12-01-preview` |

### Goose

| Variable | Required | Example | Description |
|---|---|---|---|
| `GOOSE_MODEL` | Yes | `azure/gpt-4.1` | LiteLLM model string — provider inferred from prefix |

### Database

| Variable | Required | Default | Description |
|---|---|---|---|
| `DB_PATH` | No | `state/local.db` | Path to the SQLite database |

### Optional

| Variable | Default | Description |
|---|---|---|
| `DEFAULT_PROJECT_KEY` | `AOS` | Default JIRA project for commands that accept a project |
| `LOG_LEVEL` | `INFO` | Logging verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

---

## Obtaining JIRA Credentials

1. Log in to [id.atlassian.com](https://id.atlassian.com/manage-profile/security/api-tokens)
2. Click **Create API token**
3. Give it a label (e.g. `ngb-agent-orchestrator`)
4. Copy the token into `JIRA_API_TOKEN` in your `.env`

The `JIRA_EMAIL` must exactly match your Atlassian account email.

---

## LiteLLM SDK — Model Routing

Model routing is handled in-process by the LiteLLM Python SDK. Set `GOOSE_MODEL` to a LiteLLM model string; `graph/utils.goose_env()` parses the prefix and injects the correct provider env vars before shelling out to Goose — no proxy server or config file is needed.

Examples:

| `GOOSE_MODEL` value | Connects to |
|---|---|
| `anthropic/claude-3-5-sonnet-20241022` | Anthropic API |
| `azure/gpt-4.1` | Azure AI Foundry (using `AZURE_API_*` vars) |
| `openai/gpt-4o` or `gpt-4o` | OpenAI API |

To switch providers, update `GOOSE_MODEL` in `.env` and ensure the matching API key is set.

---

## Verifying Setup

```bash
# Check the dispatcher can connect to JIRA and find required config
python -m dispatcher.run --ticket AOS-1 --dry-run
```
