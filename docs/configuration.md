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
| `azure/…` | Azure OpenAI deployments | `AZURE_API_KEY`, `AZURE_API_BASE`, `AZURE_API_VERSION` |
| `foundry/…` | Azure AI Foundry MaaS (non-OpenAI models such as Kimi, Qwen, Llama) | `AZURE_API_KEY`, `AZURE_FOUNDRY_API_BASE` |
| `openai/…` or bare name | OpenAI | `OPENAI_API_KEY` |

### LLM Provider (one required)

| Variable | Provider | Example |
|---|---|---|
| `ANTHROPIC_API_KEY` | Anthropic | `sk-ant-...` |
| `OPENAI_API_KEY` | OpenAI | `sk-...` |
| `AZURE_API_KEY` | Azure AI Foundry | — |
| `AZURE_API_BASE` | Azure AI Foundry | `https://your-resource.cognitiveservices.azure.com` |
| `AZURE_API_VERSION` | Azure AI Foundry | `2024-12-01-preview` |
| `AZURE_FOUNDRY_API_BASE` | Azure AI Foundry MaaS (only for `foundry/…` models) | `https://your-resource.services.ai.azure.com/openai/v1` |

### Goose

| Variable | Required | Example | Description |
|---|---|---|---|
| `GOOSE_MODEL` | Yes | `azure/gpt-4.1` | LiteLLM model string — provider inferred from prefix |

### GitHub App Authentication

All GitHub operations in the execute flow use a short-lived GitHub App installation token fetched by the LangGraph code-generator subgraph. The token is fetched once, stored in subgraph state, and reused for clone, push, and PR creation.

| Variable | Required | Example | Description |
|---|---|---|---|
| `GITHUB_APP_ID` | Yes | `1234567` | GitHub App ID used as the JWT issuer |
| `GITHUB_APP_PRIVATE_KEY` | Yes | `<inline-pem-with-escaped-newlines>` | Full PEM content for the app private key, stored inline with `\n` escapes preserved |
| `GITHUB_APP_INSTALLATION_ID` | Yes | `98765432` | Installation ID for the target org or repository |

Notes:
- Keep the private key inline in `.env`; the loader normalizes literal `\n` sequences back to newlines at runtime.
- The orchestrator clones and pushes via HTTPS using `x-access-token`, then resets the local remote URL back to the public HTTPS form after push.
- `gh` is no longer required for PR creation or updates.

### Database

| Variable | Required | Default | Description |
|---|---|---|---|
| `DB_PATH` | No | `state/local.db` | Path to the SQLite database |

### OpenTelemetry (Day-0 Tracing)

Tracing is always enabled. Configure the exporter via environment variables — no code changes needed to switch.

| Variable | Default | Description |
|---|---|---|
| `OTEL_EXPORTERS` | *(empty)* | Comma-separated list of additional exporters: `console` (stdout) and/or `otlp` (remote collector). File logging is **always on** regardless of this value. Leave empty for file-only export. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | gRPC endpoint for OTLP exporter (only used when `OTEL_EXPORTERS` includes `otlp`) |
| `OTEL_SERVICE_NAME` | `ngb-agent-orchestrator` | Service name attached to all spans |
| `OTEL_DEBUG_LOCAL` | `false` | When `true`, disables redaction in local artifacts for troubleshooting (never enable in production) |
| `OTEL_REDACT_PAYLOADS` | `true` | Control redaction: `true` to enable, `false` to disable. Defaults to `true` (secure by default) — independent of exporter type. |

#### Day-0 Console Export (default)

No extra setup needed. Spans are printed to stdout alongside normal logs:

```bash
# Default — spans print to stdout
OTEL_EXPORTERS=console dispatcher --ticket AOS-109
```

#### Local JSON File Export (always on)

Spans are **always** written as JSON lines (NDJSON) to `LOGS_DIR/<workflow_id>/otel.jsonl` — no environment variable is needed to enable this. The `<workflow_id>` segment is read from each span's `workflow.id` attribute (set by `otel.context.OtelContext`), so a single batch with spans from multiple workflows is split into the correct per-workflow file. Spans emitted outside any workflow context fall back to `LOGS_DIR/unknown/otel.jsonl`. Each line is a valid JSON span object (the file itself is NDJSON, not a JSON array):

```json
{
    "name": "graph.node.work_planner",
    "trace_id": "0x123abc...",
    "span_id": "0x456def...",
    "start_time": 1234567890000000000,
    "end_time": 1234567891000000000,
    "duration_ms": 1000,
    "attributes": {
        "workflow.id": "abc-123",
        "jira.ticket_key": "AOS-109",
        "graph.node_name": "work_planner",
        "graph.node.state_keys_changed": ["draft", "work_plan"],
        "graph.node.output_size_bytes": 2048
    },
    "events": [],
    "status": {"status_code": "OK", "description": null}
}
```

This JSON format is machine-parseable for downstream analysis, dashboards, and debugging tools.

#### Span Types & Attributes

| Span | Emitted by | Key attributes (beyond `workflow.id` / `jira.ticket_key`) |
|---|---|---|
| `workflow.run` | `otel.instrumentation.instrument_graph_stream` (root span per run) | `graph.thread_id`, `workflow.node_count`, `workflow.last_node`, `workflow.exit_reason` (`completed` / `interrupted` / `error`) |
| `graph.node.<name>` | `otel.instrumentation` (one per stream event) | `graph.node_name`, `graph.node.state_keys_changed` (sorted keys, no values), `graph.node.output_size_bytes`, `graph.node.error` / `graph.node.failed_node` on failure, `workflow.status` when set |
| `graph.checkpoint` | `state.observable_sqlite_saver.ObservableSqliteSaver.put` | `checkpoint.step`, `checkpoint.source` (`input` / `loop` / `update` / `fork`), `checkpoint.changed_channels`, `checkpoint.writes_nodes`, `checkpoint.channel_count`, `graph.thread_id` |
| `goose.run` | `graph.utils.run_and_tee` (when `cmd[0] == "goose"`) | `process.command`, `process.command_line`, `process.exit_code`, `goose.recipe`, `goose.stage` (recipe basename, e.g. `plan` / `execute`), `goose.stdout_lines` |
| `llm.call` | `otel.litellm_callback.OtelLiteLLMCallback` (registered inside the LiteLLM proxy subprocess via `otel.litellm_proxy_setup`) | `llm.model`, `llm.input_tokens`, `llm.output_tokens`, `llm.total_tokens`, `llm.latency_ms`, `llm.error_type` on failure. Routed into `LOGS_DIR/<workflow_id>/otel.jsonl` via the proxy-side `LocalJsonFileExporter` (AOS-118), using `NGB_WORKFLOW_ID` / `NGB_TICKET_KEY` forwarded by `graph.utils.goose_session`. |

#### Local OTLP Export (optional)

Requires installing the gRPC exporter:
```bash
pip install opentelemetry-exporter-otlp-proto-grpc
```

Start a local OTel Collector (e.g. via Docker):
```bash
docker run -p 4317:4317 otel/opentelemetry-collector-contrib:latest
```

Then set:
```bash
OTEL_EXPORTERS=otlp
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
```

### Optional

| Variable | Default | Description |
|---|---|---|
| `DEFAULT_PROJECT_KEY` | `AOS` | Default JIRA project for commands that accept a project |
| `LOG_LEVEL` | `INFO` | Python logging verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. Affects all application and third-party logs |
| `LOGS_DIR` | `$XDG_STATE_HOME/ngb-agent-orchestrator/logs` (or `~/.local/state/ngb-agent-orchestrator/logs` when `XDG_STATE_HOME` is unset) | Base directory for run logs. Each workflow writes into a `{workflow_id}/` subdirectory containing stage logs, `llm_token_usage.jsonl`, and `otel.jsonl` (always written) |

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
| `azure/gpt-4.1` | Azure OpenAI deployment (using `AZURE_API_*` vars) |
| `foundry/Kimi-K2.6` | Azure AI Foundry MaaS deployment (using `AZURE_API_KEY` + `AZURE_FOUNDRY_API_BASE`) |
| `openai/gpt-4o` or `gpt-4o` | OpenAI API |

To switch providers, update `GOOSE_MODEL` in `.env` and ensure the matching API key is set.

---

## Verifying Setup

```bash
# Check the dispatcher can connect to JIRA and find required config
python -m dispatcher.run --ticket AOS-1 --dry-run
```
