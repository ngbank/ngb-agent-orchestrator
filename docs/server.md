# Orchestrator HTTP Server

The orchestrator ships an optional FastAPI server that exposes the
non-streaming subset of [`WorkflowService`](architecture.md#orchestratorworkflow_service)
as REST endpoints. The CLI continues to work against the in-process
`LocalWorkflowService` and is **not** affected by the server.

Streaming events / logs (B2), the `HttpWorkflowService` client (B3), and
packaging polish (B4) are tracked in separate tickets and are **not** part
of this skeleton.

---

## Running the server

```bash
# Install editable + deps once
.venv/bin/python -m pip install -e .
.venv/bin/python -m pip install -r requirements.txt

# Boot via console-script (reads ORCHESTRATOR_HOST/PORT/LOG_LEVEL/RELOAD)
orchestrator-server

# Or directly with uvicorn
uvicorn orchestrator.server.app:app --host 0.0.0.0 --port 8080
```

Once running:

- Liveness: `GET http://localhost:8080/healthz`
- OpenAPI schema: `GET http://localhost:8080/openapi.json`
- Swagger UI: `GET http://localhost:8080/docs`

---

## Endpoints

All `/workflows*` routes require a bearer token when
`ORCHESTRATOR_API_TOKEN` is set; see [Auth](#auth-stub) below.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/healthz` | Liveness probe — always 200, never auth-gated |
| `POST` | `/workflows` | Start a new workflow from a JIRA ticket |
| `GET` | `/workflows` | List workflows (optionally filter by `ticket_key`, `status`, `limit`) |
| `GET` | `/workflows/{id}` | Fetch a full workflow record |
| `POST` | `/workflows/{id}/cancel` | Cancel an in-flight workflow |

### `POST /workflows`

```json
{
    "ticket_key": "AOS-141",
    "dry_run": false,
    "workflow_id": null
}
```

Returns `201 Created` with a `WorkflowRunResponse` (workflow id, final
status, optional execution summary). When the planner pauses at
`await_approval` the response carries `"interrupted": true`.

### `GET /workflows`

Query parameters:

- `ticket_key` — filter to one ticket
- `status` — one of the `WorkflowStatus` values (`pending`, `in_progress`,
    `pending_approval`, `completed`, …). Unknown values return `400`.
- `limit` — 1..500 (default 50)

### `GET /workflows/{id}`

Returns the full `WorkflowDetailResponse` (work plan, execution summary,
clarification history, usage summary, retry count). `404` when unknown.

### `POST /workflows/{id}/cancel`

Optional JSON body:

```json
{
    "reason": "operator request",
    "actor": "ops-bot"
}
```

Returns:

- `204 No Content` on success
- `404 Not Found` when the workflow id does not exist
- `409 Conflict` when the workflow is already terminal

---

## Auth stub

Authentication is a **placeholder** intended for early environments. It
will be replaced by a production-grade scheme in a follow-up epic.

| `ORCHESTRATOR_API_TOKEN` | Behaviour |
|---|---|
| unset / empty | Auth disabled — every request allowed; warning logged at startup |
| any non-empty value | `/workflows*` requires `Authorization: Bearer <token>` |

`/healthz` and OpenAPI endpoints are intentionally left open so load
balancers and tooling can probe the service without credentials.

---

## OpenTelemetry

Per-request HTTP spans are emitted when
`opentelemetry-instrumentation-fastapi` is installed:

```bash
pip install opentelemetry-instrumentation-fastapi
```

The server boots normally when the package is missing — the
instrumentation step is best-effort and only logs an info-level skip.
All other workflow spans (`workflow.run`, `graph.node.<name>`,
`llm.call`, …) continue to be emitted by the service layer regardless of
the HTTP transport. See [docs/configuration.md](configuration.md#opentelemetry-day-0-tracing)
for the full span reference.

---

## Architecture

```
HTTP client
        │
        ▼
FastAPI app  (orchestrator/server/app.py)
        │   require_bearer_token  (auth.py)
        │   get_service           (deps.py)
        ▼
WorkflowService  (Protocol from orchestrator/workflow_service)
        │
        ▼
LocalWorkflowService  → SQLite + LangGraph
```

The HTTP layer never touches `state/`, `orchestrator.builder`, or
LangGraph directly. Every behavioural detail lives in
`LocalWorkflowService`, so swapping the implementation (e.g. to a remote
backend or a fake in tests) requires no route changes.
