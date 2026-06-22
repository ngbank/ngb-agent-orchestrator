# syntax=docker/dockerfile:1.7
#
# Multi-stage build for the orchestrator HTTP server.
#
# Stage 1 (builder): installs runtime dependencies into an isolated prefix.
# Stage 2 (runtime): copies the prefix into a clean slim image, drops to a
# non-root user, and boots the `orchestrator-server` console script.
#
# Build:
#   docker build -t ngb-orchestrator:dev .
#
# Run (auth disabled, in-memory state):
#   docker run --rm -p 8080:8080 ngb-orchestrator:dev
#
# Run (persistent SQLite + logs via the host's XDG state dir):
#   docker run --rm -p 8080:8080 \
#     --env-file .env \
#     -v "${XDG_STATE_HOME:-$HOME/.local/state}/ngb-agent-orchestrator:/home/orchestrator/.local/state/ngb-agent-orchestrator" \
#     ngb-orchestrator:dev
#
# Smoke test:
#   curl http://localhost:8080/healthz

ARG PYTHON_VERSION=3.12

# ---------------------------------------------------------------------------
# Stage 1 — builder
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Build deps for any wheels that need compilation (cryptography, uvloop, …).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Install runtime deps first (cached layer) then the project itself.
COPY requirements.txt ./
RUN pip install --prefix=/install -r requirements.txt

COPY pyproject.toml README.md ./
COPY dispatcher ./dispatcher
COPY orchestrator ./orchestrator
COPY otel ./otel
COPY state ./state
COPY mcp_server ./mcp_server
COPY schemas ./schemas
COPY recipes ./recipes
COPY config ./config

RUN pip install --prefix=/install --no-deps .

# ---------------------------------------------------------------------------
# Stage 2 — runtime
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ORCHESTRATOR_HOST=0.0.0.0 \
    ORCHESTRATOR_PORT=8080

# Non-root user.
RUN groupadd --system --gid 1001 orchestrator \
    && useradd  --system --uid 1001 --gid orchestrator --create-home orchestrator

# Copy installed packages + console scripts from the builder.
COPY --from=builder /install /usr/local

WORKDIR /app

# Bring in the package source so the working directory mirrors the repo
# layout (recipes/, schemas/, config/ are read at runtime by some nodes).
COPY --from=builder /build/recipes ./recipes
COPY --from=builder /build/schemas ./schemas
COPY --from=builder /build/config ./config

# Pre-create the XDG state dir under the orchestrator user's $HOME so a host
# bind-mount lands on an owned, writable directory. The orchestrator resolves
# DB + logs to ~/.local/state/ngb-agent-orchestrator by default.
RUN mkdir -p /home/orchestrator/.local/state/ngb-agent-orchestrator \
    && chown -R orchestrator:orchestrator /app /home/orchestrator/.local

USER orchestrator

EXPOSE 8080

# Lightweight healthcheck — no extra packages needed.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3).status == 200 else 1)" \
    || exit 1

CMD ["orchestrator-server"]
