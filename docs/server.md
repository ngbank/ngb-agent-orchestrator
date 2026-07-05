# Orchestrator HTTP Server

The orchestrator ships an optional FastAPI server that exposes the
[`WorkflowService`](architecture.md#orchestratorworkflow_service)
as REST endpoints, plus two Server-Sent Events (SSE) streams for
following workflow events and log output in real time. The CLI continues
to work against the in-process `LocalWorkflowService` and is **not**
affected by the server.

The `HttpWorkflowService` client (B3, AOS-143) routes the dispatcher
through this server when `ORCHESTRATOR_MODE=remote`. See
[docs/configuration.md](configuration.md#dispatcher--orchestrator-transport)
for the env-var contract.

---

## Local vs remote topology

The dispatcher always talks to a `WorkflowService` Protocol — the
transport is selected once at startup via `ORCHESTRATOR_MODE`. There are
no other code paths to flip.

```mermaid
flowchart LR
    subgraph local["ORCHESTRATOR_MODE=local (default)"]
        CLI1["dispatcher / TUI"] --> LS1["LocalWorkflowService<br/>(in-process)"]
        LS1 --> SQL1[("SQLite<br/>~/.local/state/ngb-agent-orchestrator/db/local.db")]
        LS1 --> LG1["LangGraph<br/>nodes"]
    end

    subgraph remote["ORCHESTRATOR_MODE=remote"]
        CLI2["dispatcher / TUI"] --> HC["HttpWorkflowService<br/>(httpx + SSE)"]
        HC -->|HTTPS + bearer| API["FastAPI app<br/>orchestrator/server"]
        API --> LS2["LocalWorkflowService<br/>(in server process)"]
        LS2 --> SQL2[("SQLite<br/>(server-owned)")]
        LS2 --> LG2["LangGraph<br/>nodes"]
    end
```

The HTTP layer is a thin transport: every behaviour lives in
`LocalWorkflowService`, so the local and remote modes have identical
semantics for the operations they both expose.

---

## Running the server

There are four ways to run the server. They all boot the same FastAPI
app (`orchestrator/server/app.py`); pick the one that matches what you
are doing right now.

### When to use which

| Situation | Use | Lifetime |
|---|---|---|
| Quick debugging — want stdout in your face, will Ctrl-C when done | `orchestrator-server` | foreground; dies with terminal |
| Hot reload while editing server code | `uvicorn orchestrator.server.app:app --reload` | foreground; dies with terminal |
| Want it running in the background, isolated, prod-like (recommended) | `orchestrator-server-ctl start` | container; survives terminal close |

### 1 — Foreground console script

```bash
# Install editable + deps once
.venv/bin/python -m pip install -e .
.venv/bin/python -m pip install -r requirements.txt

# Boot via console-script (reads ORCHESTRATOR_HOST/PORT/LOG_LEVEL/RELOAD)
orchestrator-server
```

`Ctrl-C` stops it. Closing the terminal stops it.

### 2 — Foreground uvicorn with `--reload`

```bash
uvicorn orchestrator.server.app:app --host 0.0.0.0 --port 8080 --reload
```

Restarts the server on file changes — useful when editing
`orchestrator/server/` itself.

### 3 — Container, via `orchestrator-server-ctl` (recommended for local dev)

The repo ships a thin Bash wrapper at
[`bin/orchestrator-server-ctl`](../bin/orchestrator-server-ctl) around
`docker compose` (see [Running with Docker](#running-with-docker) below
for what's actually inside the image / compose file). `bin/` is placed
on `$PATH` by `.envrc`, so any direnv-allowed shell can call the helper
bare:

```bash
orchestrator-server-ctl start          # docker compose up -d --build; ~10s readiness probe
orchestrator-server-ctl start --no-build   # skip the rebuild, just (re)start the existing image
orchestrator-server-ctl status         # container state + /healthz probe
orchestrator-server-ctl logs           # tail container logs
orchestrator-server-ctl logs -f        # follow them
orchestrator-server-ctl restart
orchestrator-server-ctl stop           # docker compose down
```

The container survives the terminal that launched it for free — `docker
compose up -d` hands it off to the Docker Engine daemon, a separate
long-running process tree from your shell. There's no PID file or
`nohup`/`disown` involved; that's only needed for detaching *native*
child processes, and a container was never a child of your shell to
begin with.

Requires `docker compose` to be available (`docker compose version`
succeeds). The org standardises on **Docker Engine** across Windows and
macOS — on macOS that means [Colima](https://github.com/abiosoft/colima)
(Docker Engine can't run natively on macOS, and Docker Desktop / Podman
are not supported paths). See
[Container runtime](#container-runtime) below for setup, including the
`docker-compose` plugin wire-up and corporate TLS trust.
`./setup-env.sh --docker` checks that `docker compose` resolves and
prints a fix hint if it doesn't.

### 4 — Container, via `docker compose` directly

Same underlying mechanism as option 3, useful when you want raw compose
output or flags `orchestrator-server-ctl` doesn't expose. See [Running
with Docker](#running-with-docker) below.

### `orchestrator-server` vs `orchestrator-server-ctl`

They live at different layers:

- **`orchestrator-server`** is the Python console script (registered by
    `pip install -e .` via [`pyproject.toml`](../pyproject.toml)) that
    invokes `orchestrator.server.app:run()` and boots uvicorn in the
    **foreground**. This is what actually runs inside the container
    (it's the Dockerfile's `CMD`).
- **`orchestrator-server-ctl`** is a Bash lifecycle wrapper
    (`start` / `stop` / `restart` / `status` / `logs`) around `docker
    compose`, which builds the image (containing `orchestrator-server`
    as its entrypoint) and runs it as a container.

Once running:

- Liveness: `GET http://localhost:8080/healthz`
- OpenAPI schema: `GET http://localhost:8080/openapi.json`
- Swagger UI: `GET http://localhost:8080/docs`

---

## Running with Docker

The repo ships a multi-stage `Dockerfile` (Python 3.12-slim, non-root
`orchestrator` user, default `CMD ["orchestrator-server"]`,
`HEALTHCHECK` against `/healthz`) and a `docker-compose.yml` that
bind-mounts the host's XDG state directory into the container for logs and
overlays a Docker-managed named volume on the `db/` subdirectory for the
SQLite database.

The host directory is `${XDG_STATE_HOME:-$HOME/.local/state}/ngb-agent-orchestrator`.
It is mounted into the container at
`/home/orchestrator/.local/state/ngb-agent-orchestrator`, which is exactly
where the in-container code resolves its XDG default. Per-workflow logs
appear on the host under `logs/<workflow_id>/`.

> **Why a named volume for the DB?** SQLite's file-locking and fsync
> semantics are not reliable over macOS bind mounts (Colima virtiofs,
> Docker Desktop gRPC-FUSE) and produce `disk I/O error` /
> `database disk image is malformed` under load. The named volume lives
> on the VM's ext4 filesystem and behaves like a native disk. The host
> CLI does not need direct DB access because the dispatcher talks to the
> server over HTTP (`ORCHESTRATOR_MODE=remote`); to inspect the DB, use
> `docker cp` to pull `local.db` out of the container.

> **Consequence for macOS users:** the local-mode CLI
> (`ORCHESTRATOR_MODE` unset) writes to a *separate* host-side DB at
> `~/.local/state/ngb-agent-orchestrator/db/local.db` and will not see
> workflows created by the containerised server (and vice versa). While
> the container is running, always drive it via `ORCHESTRATOR_MODE=remote`
> so the CLI, TUI, and server all share the container's DB over HTTP.
> The two DBs were never truly safe to share across the host↔VM boundary
> — the previous shared-file layout worked until it silently corrupted.

> **Linux note:** the container runs as UID 1001. Files created under the
> bind-mounted logs directory inherit that ownership. On macOS / Colima this is
> remapped automatically by the VM's user namespace.

### Container runtime

The org standardises on **Docker Engine** across Windows and macOS.

- **Windows:** Docker Engine via WSL2 (org-managed provisioning).
- **Linux:** native Docker Engine (`apt install docker-ce` or equivalent).
- **macOS:** Docker Engine can't run natively — use
    [Colima](https://github.com/abiosoft/colima), which runs Docker
    Engine inside a lightweight Linux VM (Apple Virtualization framework).
    Docker Desktop, Podman, and OrbStack are **not** supported paths.

#### macOS setup with Colima

```bash
# Install runtime + CLI + compose plugin
brew install colima docker docker-compose docker-buildx docker-credential-helper

# Wire brew's compose/buildx binaries into Docker's plugin dir so
# `docker compose` (subcommand) resolves — not just `docker-compose`
mkdir -p ~/.docker/cli-plugins
ln -sf /opt/homebrew/opt/docker-compose/lib/docker/cli-plugins/docker-compose ~/.docker/cli-plugins/docker-compose
ln -sf /opt/homebrew/opt/docker-buildx/bin/docker-buildx ~/.docker/cli-plugins/docker-buildx

# Start the VM (adjust CPU/RAM/disk to taste)
colima start --cpu 4 --memory 8 --disk 60 --vm-type vz --mount-type virtiofs

# Point Docker CLI at Colima's context
export DOCKER_CONTEXT=colima            # persist this in ~/.zshrc

# Verify
docker compose version
docker run --rm hello-world
```

Auto-start on login (optional): `brew services start colima`.

Colima registers itself as a Docker context (`docker context ls`), so
the CLI, `orchestrator-server-ctl`, and `docker-compose.yml` all talk to
the same daemon without extra config.

> **Corporate TLS (Zscaler / MITM proxy):** if `docker pull` fails with
> `x509: certificate signed by unknown authority`, the VM doesn't trust
> the corp root CA. Export the CA from the macOS System keychain
> (`security find-certificate -a -c "Zscaler" -p /Library/Keychains/System.keychain`),
> drop the PEM under `$HOME` so it's visible through the virtiofs mount,
> then inside the VM: `sudo cp <cert>.pem /usr/local/share/ca-certificates/<cert>.crt && sudo update-ca-certificates && sudo systemctl restart docker`.
> Repeat after `colima delete` — the cert lives inside the VM, not on
> the host.

> **Stale plugin symlinks:** if you previously used OrbStack or Docker
> Desktop, `~/.docker/cli-plugins/` may contain broken symlinks to
> `/Applications/OrbStack.app/...`. `docker compose` will report
> `unknown command: docker compose` even after installing the plugin.
> Delete the stale entries and re-run the `ln -sf` commands above.

### Quick start with compose

```bash
docker compose up --build       # build + run in the foreground
docker compose up -d            # detached
docker compose logs -f orchestrator
docker compose down             # stop (host state dir persists)
```

The compose file reads `.env` from the project root, so the same secret
material that powers local CLI runs (Key Vault output, GitHub App, etc.)
applies to the containerised server.

> If your `.env` still defines `DB_PATH` or `LOGS_DIR` from earlier setups,
> remove them so the in-container code resolves the shared XDG path.

### Bare `docker run`

```bash
docker build -t ngb-orchestrator:dev .
docker run --rm -p 8080:8080 \
    --env-file .env \
    -v "${XDG_STATE_HOME:-$HOME/.local/state}/ngb-agent-orchestrator:/home/orchestrator/.local/state/ngb-agent-orchestrator" \
    -v orchestrator-db:/home/orchestrator/.local/state/ngb-agent-orchestrator/db \
    ngb-orchestrator:dev
```

> **Prefer `orchestrator-server-ctl` / `docker compose` over this.** Unlike
> Python's `load_dotenv()` (and unlike Compose's own `env_file` handling),
> `docker run --env-file` does not strip quotes from values — `JIRA_URL="https://..."`
> in `.env` gets passed through *including the quote characters*, which
> breaks anything that parses it as a URL. `docker-compose.yml` also sets
> `GOOSE_MCP_PYTHON=python` to override `.env`'s host-absolute venv path
> (which doesn't exist in the container); a bare `docker run` needs the
> same override (`-e GOOSE_MCP_PYTHON=python`) or the `generate_code`
> recipe's MCP extension will fail to start.

### Layout inside the image

| Path | Purpose |
|---|---|
| `/home/orchestrator/.local/state/ngb-agent-orchestrator/db/local.db` | SQLite DB — backed by a Docker named volume (`orchestrator-db`) to keep SQLite off the macOS bind-mount virtiofs/gRPC-FUSE layer |
| `/home/orchestrator/.local/state/ngb-agent-orchestrator/logs/<workflow_id>/` | Per-workflow `workflow.log`, token usage, and `otel.jsonl` — bind-mounted to the host XDG state dir |
| `/app/config/` | Read-only config baked into the image (recipes and the WorkPlan schema ship inside the installed `orchestrator` package) |
| `/usr/local/bin/orchestrator-server` | Console script (the default `CMD`) |

### Smoke test

```bash
curl http://localhost:8080/healthz
# → {"status":"ok"}
```

### Pointing the dispatcher at it

```bash
export ORCHESTRATOR_MODE=remote
export ORCHESTRATOR_URL=http://localhost:8080
# export ORCHESTRATOR_TOKEN=<bearer>   # if ORCHESTRATOR_API_TOKEN is set on the server

dispatcher --list
```

The read / cancel / start / `read_logs` / `stream_events` surface works
end-to-end. Approval, clarification, retry, and PR-comment commands are
not yet exposed over HTTP — fall back to `ORCHESTRATOR_MODE=local` for
those.

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
| `POST` | `/workflows/{id}/approve-plan` | Approve a paused WorkPlan and resume |
| `POST` | `/workflows/{id}/reject-plan` | Reject a paused WorkPlan and resume |
| `POST` | `/workflows/{id}/clarification` | Submit clarification answers and resume |
| `POST` | `/workflows/{id}/retry` | Retry a failed / interrupted workflow |
| `POST` | `/workflows/{id}/approve-pr` | Approve the workflow's PR and mark COMPLETED |
| `POST` | `/workflows/{id}/reject-pr` | Reject the workflow's PR and mark REJECTED |
| `POST` | `/workflows/{id}/comment-pr` | Post review comments on the PR and resume |
| `GET` | `/workflows/{id}/history` | Return the node traversal history |
| `GET` | `/workflows/{id}/audit-log` | Return the audit log entries |
| `GET` | `/workflows/{id}/events` | **SSE** — stream workflow lifecycle events |
| `GET` | `/workflows/{id}/logs` | **SSE** — stream captured workflow log content |
| `POST` | `/admin/clear-db` | **Admin** — wipe all workflows + checkpoints |
| `POST` | `/admin/workflows/{id}/mark-interrupted` | **Admin** — mark in-flight workflow FAILED |

Mutating routes that drive the LangGraph state machine are now
**fire-and-forget**: they enqueue the work on the server's
`BackgroundDispatcher` and return `202 Accepted` immediately with a
snapshot of the workflow row. The client follows the actual lifecycle
via `GET /workflows/{id}/events` (SSE). See
[Fire-and-forget mutations](#fire-and-forget-mutations) below.

Mutating routes return `404` when the workflow id is unknown and `409`
when the workflow is in an incompatible state for that action (e.g.
`retry` against a non-retryable workflow), or when the background
dispatcher already has an in-flight job for that workflow. Admin routes
have a stricter auth posture — see [Admin endpoints](#admin-endpoints)
below.

### Fire-and-forget mutations

The following routes are non-blocking: they return `202 Accepted` with a
`WorkflowRunResponse` snapshot, queue the graph drive on the
`BackgroundDispatcher` thread pool (size: `ORCHESTRATOR_BACKGROUND_WORKERS`,
default `4`), and the worker thread updates the workflow row as the
graph progresses.

- `POST /workflows`
- `POST /workflows/{id}/approve-plan`
- `POST /workflows/{id}/reject-plan`
- `POST /workflows/{id}/clarification`
- `POST /workflows/{id}/retry`
- `POST /workflows/{id}/approve-pr`
- `POST /workflows/{id}/reject-pr`
- `POST /workflows/{id}/comment-pr`

At most one job per workflow id may be in flight; a second submission
while one is already queued returns `409 Conflict`. If the worker
thread raises, the dispatcher transitions the workflow to `FAILED`
with the actor recorded as `background-dispatcher`.

Clients should subscribe to `GET /workflows/{id}/events` after
submitting to observe `node_start` / `node_end` / `interrupt` /
`completed` / `failed` / `cancelled` events. The dispatcher CLI does
this automatically (see `--detach` in [docs/workflows.md](workflows.md)
to opt out).

### `POST /workflows`

```json
{
    "ticket_key": "AOS-141",
    "dry_run": false,
    "workflow_id": null
}
```

Returns `202 Accepted` with a `WorkflowRunResponse` snapshot (workflow
id, current status, empty execution summary). The graph drive runs on
the background dispatcher; subscribe to
`GET /workflows/{id}/events` to observe the lifecycle.

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

### Approval / clarification / retry

All four routes are fire-and-forget and return a `WorkflowRunResponse`
snapshot on `202 Accepted`, `404` when the workflow id is unknown, and
`409` when the workflow is in an incompatible state for the action (or
when another job is already in flight for the same workflow). The
actual graph drive runs on the background dispatcher — watch the
event stream to observe completion.

| Route | Body |
|---|---|
| `POST /workflows/{id}/approve-plan` | – |
| `POST /workflows/{id}/reject-plan` | `{"reason": "optional"}` |
| `POST /workflows/{id}/clarification` | `{"answers": [{"concern": "...", "answer": "..."}, ...]}` |
| `POST /workflows/{id}/retry` | – |

`concern` text in clarification answers must be non-empty; an empty list
of answers is allowed but the server will surface whatever the
underlying graph state requires.

### PR review flow

Same response/error shape as the approval routes.

| Route | Body |
|---|---|
| `POST /workflows/{id}/approve-pr` | – |
| `POST /workflows/{id}/reject-pr` | `{"reason": "optional"}` |
| `POST /workflows/{id}/comment-pr` | `{"comments": "non-empty review text"}` |

### `GET /workflows/{id}/history`

Returns the node traversal history, oldest first. Each entry has:

```json
{
    "step": 3,
    "node": "generate_code",
    "outcome": "ok",
    "result_keys": ["code_generation_summary"],
    "error": null
}
```

`outcome` is one of `ok`, `error`, `interrupted`. `404` when the
workflow id is unknown.

### `GET /workflows/{id}/audit-log`

Returns the audit log entries for the workflow, oldest first:

```json
{
    "workflow_id": "wf-1",
    "actor": "dispatcher",
    "action": "status_change",
    "timestamp": "2026-06-22T00:00:00",
    "details": {"to": "pending_approval"}
}
```

`404` when the workflow id is unknown.

### `GET /workflows/{id}/events` (SSE)

Live stream of workflow lifecycle events derived from LangGraph state
history. The response uses the standard SSE wire format
(`text/event-stream`) and one event per JSON payload:

```
id: 4
data: {"seq": 4, "kind": "node_end", "node": "plan", "data": {"result_keys": ["work_plan"]}}

```

`kind` is one of `node_start`, `node_end`, `interrupt`, `failed`. When
the workflow reaches a terminal status the server emits a final
`stream_end` event and closes the connection:

```
data: {"seq": 12, "kind": "stream_end", "node": null, "data": {"final_status": "completed"}}
```

**Replay / reconnect** — clients can resume after a disconnect by passing
the last seen sequence number either:

- as the `after_seq` query parameter, or
- via the standard `Last-Event-ID` header (set automatically by browser
    `EventSource`).

The query parameter takes precedence when both are present.

Heartbeats (`: ping\n\n` SSE comment frames) are sent every 15s of idle
time so proxies do not close the connection.

`404 Not Found` is returned synchronously when the workflow id is
unknown — before the stream is opened.

### `GET /workflows/{id}/logs` (SSE)

Live stream of the captured workflow log. Each event carries a JSON payload
with the stream name, the byte offset of the chunk within `workflow.log`, and
the chunk content:

```
id: 1024
data: {"stage": "workflow", "offset": 0, "end_offset": 1024, "content": "..."}
```

Query parameters:

- `stage` — optional stream name. `workflow` is the canonical stream; when
    omitted, `workflow` is followed.
- `after_offset` — skip bytes already delivered. Can also be supplied via
    `Last-Event-ID`.

Same heartbeat (15s) and terminal-`stream_end`/close semantics as
`/events`. The trailing event has no `id:` and looks like:

```
data: {"stage": "workflow", "kind": "stream_end", "final_status": "completed"}
```

#### Consuming with `curl`

```bash
curl -N "http://localhost:8080/workflows/$WF_ID/events"
curl -N "http://localhost:8080/workflows/$WF_ID/logs?stage=workflow&after_offset=4096"
```

`-N` disables curl's output buffering so frames render immediately.

---

## Auth stub

Authentication is a **placeholder** intended for early environments. It
will be replaced by a production-grade scheme in a follow-up epic.

| `ORCHESTRATOR_API_TOKEN` | Behaviour |
|---|---|
| unset / empty | Auth disabled — every `/workflows*` request allowed; warning logged at startup |
| any non-empty value | `/workflows*` requires `Authorization: Bearer <token>` |

`/healthz` and OpenAPI endpoints are intentionally left open so load
balancers and tooling can probe the service without credentials.

## Admin endpoints

`/admin/*` routes (`clear-db`, `mark-interrupted`) follow a stricter
posture than the rest of the API. They are destructive enough that an
open development server must never expose them:

| `ORCHESTRATOR_API_TOKEN` | Behaviour for `/admin/*` |
|---|---|
| unset / empty | All admin routes return `503 Service Unavailable` — admin is **disabled**, not just unauthenticated |
| set, request has no/wrong `Authorization` header | `401 Unauthorized` |
| set, request carries matching `Bearer <token>` | Request proceeds |

In production, set `ORCHESTRATOR_API_TOKEN` to a value known only to
trusted operators. The dispatcher reads the same value from
`ORCHESTRATOR_API_TOKEN` (see
[docs/configuration.md](configuration.md#dispatcher--orchestrator-transport))
when running in `ORCHESTRATOR_MODE=remote`.

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
