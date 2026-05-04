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

### LiteLLM Proxy

| Variable | Required | Example | Description |
|---|---|---|---|
| `LITELLM_MASTER_KEY` | Yes | `sk-local-master-key` | Auth key for the local proxy API |
| `LITELLM_MODEL` | Yes | `anthropic/claude-3-5-sonnet-20241022` | Model identifier sent to the provider |
| `LITELLM_BASE_URL` | Yes | `http://localhost:4000` | URL of the running LiteLLM proxy |

### LLM Provider (one required)

| Variable | Provider | Example |
|---|---|---|
| `ANTHROPIC_API_KEY` | Anthropic | `sk-ant-...` |
| `OPENAI_API_KEY` | OpenAI | `sk-...` |
| `AZURE_API_KEY` | Azure OpenAI | — |
| `AZURE_API_BASE` | Azure OpenAI | `https://...openai.azure.com/` |
| `AZURE_API_VERSION` | Azure OpenAI | `2024-02-01` |

### Goose

| Variable | Required | Example | Description |
|---|---|---|---|
| `GOOSE_PROVIDER` | Yes | `openai` | Provider name — set to `openai` to route through LiteLLM |
| `GOOSE_MODEL` | Yes | `azure-gpt4` | Model name matching a `model_name` in `config/litellm.yaml` |
| `GOOSE_BASE_URL` | Yes | `http://localhost:4000` | Point Goose at the LiteLLM proxy |

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

## LiteLLM Proxy Configuration

Model routing is defined in `config/litellm.yaml`. Each entry maps a `model_name` (what Goose and LangGraph call) to a provider and model:

```yaml
model_list:
  - model_name: azure-gpt4
    litellm_params:
      model: azure/gpt-4
      api_base: ...
      api_key: ...
```

Available model names (defined in `config/litellm.yaml`): `azure-gpt4` | `claude` | `gpt4o`

To add a new backend, add an entry to `config/litellm.yaml` and restart the proxy. No changes to recipes or application code are needed.

---

## Verifying Setup

```bash
# Check the dispatcher can connect to JIRA and find required config
python -m dispatcher.run --ticket AOS-1 --dry-run

# Check the LiteLLM proxy is reachable (replace sk-local-master-key with your LITELLM_MASTER_KEY)
curl http://localhost:4000/models -H "Authorization: Bearer sk-local-master-key"
```
