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

ARG PYTHON_VERSION=3.13

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
COPY ace ./ace
COPY orchestrator ./orchestrator
COPY otel ./otel
COPY state ./state
COPY mcp_server ./mcp_server
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

# Goose CLI and git — required by the repo_setup / work_planner / generate_code
# graph nodes, which clone the target repo and shell out to `goose run --recipe
# ...`. Goose is installed via the project's official prebuilt-binary script
# (no cargo/rust build needed in the image).
#
# The version is pinned via .goose-version (single source of truth shared with
# the host installer in setup-env.sh) rather than the moving `stable` tag,
# because upstream releases have shipped regressions that trigger degenerate
# reasoning loops with some models. Override at build time with
# `--build-arg GOOSE_VERSION=<x.y.z>`.
ARG GOOSE_VERSION=
COPY .goose-version /tmp/.goose-version
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates bzip2 git libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && GOOSE_VERSION="${GOOSE_VERSION:-$(cat /tmp/.goose-version)}" \
    && export CONFIGURE=false GOOSE_BIN_DIR=/usr/local/bin \
    && curl -fsSL "https://github.com/aaif-goose/goose/releases/download/v${GOOSE_VERSION}/download_cli.sh" | bash \
    && installed="$(goose --version | tr -d ' ')" \
    && if [ "${installed}" != "${GOOSE_VERSION}" ]; then \
         echo "goose version mismatch: expected ${GOOSE_VERSION}, got ${installed}" >&2; \
         exit 1; \
       fi \
    && rm /tmp/.goose-version

# Non-root user.
RUN groupadd --system --gid 1001 orchestrator \
    && useradd  --system --uid 1001 --gid orchestrator --create-home orchestrator

# Copy installed packages + console scripts from the builder.
COPY --from=builder /install /usr/local

WORKDIR /app

# Bring in the package source so the working directory mirrors the repo
# layout. orchestrator/utils.py and mcp_server/server.py resolve config/
# relative to the installed package tree (Path(__file__).resolve().parents[1]),
# not the process cwd, so it must also be reachable as a sibling of the
# installed packages in site-packages — symlink it there instead of copying
# twice so the two locations can't drift.
COPY --from=builder /build/config ./config
RUN python3 -c "\
import os, site; \
os.symlink('/app/config', os.path.join(site.getsitepackages()[0], 'config'))"

# Pre-create the XDG state dir under the orchestrator user's $HOME so a host
# bind-mount lands on an owned, writable directory. The orchestrator resolves
# DB + logs to ~/.local/state/ngb-agent-orchestrator by default.
#
# We also pre-create the `db/` subdirectory: docker-compose mounts a named
# volume there (see docker-compose.yml for why). On first mount of an empty
# named volume, Docker copies the image's content + ownership at the mount
# target into the volume. Without this pre-created directory the volume would
# be created as root-owned and the non-root `orchestrator` user could not
# write to it.
RUN mkdir -p /home/orchestrator/.local/state/ngb-agent-orchestrator/db \
    && chown -R orchestrator:orchestrator /app /home/orchestrator/.local

USER orchestrator

EXPOSE 8080

# Lightweight healthcheck — no extra packages needed.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3).status == 200 else 1)" \
    || exit 1

CMD ["orchestrator-server"]
