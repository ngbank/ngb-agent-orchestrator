#!/usr/bin/env bash
# setup-env.sh
#
# Sets up the local development environment for ngb-agent-orchestrator.
#
# Stages (all run by default; pass flags to run specific stages only):
#   --python   Install Python 3.13.x via pyenv and pin .python-version
#   --deps     Create/update the .venv and install pip dependencies
#   --goose    Install the pinned Goose CLI version (from .goose-version) to ~/.local/bin
#   --env      Generate .env from .env.example and sync secrets from Azure Key Vault
#   --docker   Build the orchestrator-server container image (ngb-orchestrator:dev)
#   --clean    Remove .venv/ (and legacy venv/) and .env, then run all stages from scratch
#
# Modifier flags:
#   --goose-force  When installing goose, overwrite an existing binary even if its
#                  version does not match .goose-version. Without this, --goose
#                  aborts if a wrong version is already on PATH.
#
# Examples:
#   ./setup-env.sh                   # run all stages (including --docker)
#   ./setup-env.sh --clean           # wipe and rebuild everything
#   ./setup-env.sh --deps            # reinstall dependencies only
#   ./setup-env.sh --python --deps   # reinstall Python + deps, skip .env
#   ./setup-env.sh --env             # keep existing .env values where present, fill missing from Key Vault
#   ./setup-env.sh --env --env-overwrite  # re-pull and overwrite all managed values from Key Vault
#   ./setup-env.sh --env --env-keep       # explicit keep mode (same as default)
#   ./setup-env.sh --docker          # (re)build the container image only
#
# Prerequisites for --env:
#   az login   # REQUIRED before running --env locally
#
# Prerequisites for --docker:
#   docker (or podman) available on PATH and a running daemon/machine

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PYTHON_MAJOR="3.13"
DEFAULT_AZURE_KEYVAULT_NAME="agent-os-kv"
DOCKER_IMAGE_TAG="ngb-orchestrator:dev"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
info()    { echo "[INFO]  $*"; }
success() { echo "[OK]    $*"; }
error()   { echo "[ERROR] $*" >&2; exit 1; }

usage() {
    grep '^#' "$0" | grep -v '^#!/' | sed 's/^# \{0,1\}//'
    exit 0
}

# ---------------------------------------------------------------------------
# Parse flags
# ---------------------------------------------------------------------------
DO_PYTHON=false
DO_DEPS=false
DO_GOOSE=false
DO_ENV=false
DO_DOCKER=false
DO_CLEAN=false
ENV_SYNC_MODE="keep"
GOOSE_FORCE=false

if [[ $# -eq 0 ]]; then
    DO_PYTHON=true
    DO_DEPS=true
    DO_GOOSE=true
    DO_ENV=true
    DO_DOCKER=true
else
    for arg in "$@"; do
        case "$arg" in
            --python) DO_PYTHON=true ;;
            --deps)   DO_DEPS=true ;;
            --goose)  DO_GOOSE=true ;;
            --goose-force) DO_GOOSE=true; GOOSE_FORCE=true ;;
            --env)    DO_ENV=true ;;
            --env-keep) DO_ENV=true; ENV_SYNC_MODE="keep" ;;
            --env-overwrite) DO_ENV=true; ENV_SYNC_MODE="overwrite" ;;
            --docker) DO_DOCKER=true ;;
            --clean)  DO_CLEAN=true ;;
            --help|-h) usage ;;
            *) error "Unknown flag: $arg. Valid flags: --python, --deps, --goose, --goose-force, --env, --env-keep, --env-overwrite, --docker, --clean" ;;
        esac
    done
fi

# --clean enables all stages and wipes artifacts first
if $DO_CLEAN; then
    DO_PYTHON=true
    DO_DEPS=true
    DO_GOOSE=true
    DO_ENV=true
    DO_DOCKER=true
fi

VENV_DIR="$(pwd)/.venv"

# ---------------------------------------------------------------------------
# Clean (runs before all other stages)
# ---------------------------------------------------------------------------
if $DO_CLEAN; then
    info "Cleaning environment artifacts..."
    [[ -d "$(pwd)/venv" ]] && { rm -rf "$(pwd)/venv"; info "Removed $(pwd)/venv"; }
    [[ -d "$VENV_DIR" ]] && { rm -rf "$VENV_DIR"; info "Removed $VENV_DIR"; }
    [[ -f ".env" ]]      && { rm -f  ".env";       info "Removed .env"; }
    success "Clean complete."
fi

# ---------------------------------------------------------------------------
# Prerequisite checks (scoped to active stages)
# ---------------------------------------------------------------------------
info "Checking prerequisites..."

if $DO_ENV; then
    command -v direnv &>/dev/null \
        || error "'direnv' is not installed or not on PATH. Install from: https://direnv.net/docs/installation.html"
    command -v az &>/dev/null \
        || error "'az' CLI is not installed or not on PATH. Install from: https://learn.microsoft.com/cli/azure/install-azure-cli"
    if ! az account show &>/dev/null; then
        info "Azure CLI is not authenticated. Running 'az login'..."
        az login || error "Azure login failed. Authenticate and retry."
    fi
fi

if $DO_PYTHON; then
    command -v pyenv &>/dev/null \
        || error "'pyenv' is not installed or not on PATH. Install from: https://github.com/pyenv/pyenv#installation"
fi

if $DO_GOOSE; then
    command -v curl &>/dev/null \
        || error "'curl' is not installed or not on PATH. Install curl before running --goose."
    [[ -f .goose-version ]] \
        || error ".goose-version file is missing. It must exist at the repo root and contain the pinned goose version (e.g. 1.33.1)."
fi

if $DO_DOCKER; then
    # Prefer docker; fall back to podman (which ships a docker-compatible CLI on macOS).
    if command -v docker &>/dev/null; then
        DOCKER_BIN="docker"
    elif command -v podman &>/dev/null; then
        DOCKER_BIN="podman"
        info "Using podman as a docker substitute."
    else
        error "Neither 'docker' nor 'podman' is on PATH. Install Docker Desktop or Podman before running --docker."
    fi
    # Verify the daemon / VM is reachable before attempting a build.
    if ! "$DOCKER_BIN" info &>/dev/null; then
        if [[ "$DOCKER_BIN" == "podman" ]]; then
            error "Podman is installed but its machine is not running. Start it with: podman machine start"
        else
            error "Docker daemon is not reachable. Start Docker Desktop and retry."
        fi
    fi
    # orchestrator-server-ctl and docker-compose.yml both drive the container
    # via `docker compose`. Docker Desktop bundles this as a built-in plugin,
    # but Podman's docker-compatible CLI does not — it shells out to a
    # separate `docker-compose` binary on PATH, which isn't installed by
    # default. Check the actual capability (not just the binary) so this
    # works regardless of which provider satisfies it.
    if ! "$DOCKER_BIN" compose version &>/dev/null; then
        error "'docker compose' is not available via ${DOCKER_BIN}. Install with: brew install docker-compose"
    fi
fi

success "Prerequisites satisfied."

# ---------------------------------------------------------------------------
# Stage: python
# ---------------------------------------------------------------------------
if $DO_PYTHON; then
    info "Checking for Python ${PYTHON_MAJOR}.x..."

    INSTALLED_VERSION=$(pyenv versions --bare 2>/dev/null | grep -E "^${PYTHON_MAJOR}\.[0-9]+$" | sort -t. -k3 -n | tail -1 || true)

    if [[ -z "$INSTALLED_VERSION" ]]; then
        info "No Python ${PYTHON_MAJOR}.x found. Resolving latest available version..."
        LATEST_VERSION=$(pyenv install --list 2>/dev/null | grep -E "^\s+${PYTHON_MAJOR}\.[0-9]+$" | tr -d ' ' | sort -t. -k3 -n | tail -1 || true)
        [[ -z "$LATEST_VERSION" ]] \
            && error "No Python ${PYTHON_MAJOR}.x available in pyenv. Run 'pyenv update' and retry."
        info "Installing Python ${LATEST_VERSION} (this may take a few minutes)..."
        pyenv install "$LATEST_VERSION"
        INSTALLED_VERSION="$LATEST_VERSION"
    fi

    success "Using Python ${INSTALLED_VERSION}."
    pyenv local "$INSTALLED_VERSION"
fi

# Resolve the absolute path to the pyenv-managed python binary.
# We do this outside the --python stage so --deps alone also benefits
# (pyenv local may already be set from a previous run).
PYTHON_BIN="$(pyenv which python 2>/dev/null || true)"
[[ -z "$PYTHON_BIN" ]] && error "Cannot resolve python binary via pyenv. Run with --python first."

# ---------------------------------------------------------------------------
# Stage: deps
# ---------------------------------------------------------------------------
if $DO_DEPS; then
    if [[ ! -d "$VENV_DIR" ]]; then
        info "Creating virtual environment at $VENV_DIR..."
        "$PYTHON_BIN" -m venv "$VENV_DIR"
    fi

    info "Installing/updating dependencies..."
    "$VENV_DIR/bin/pip" install --quiet --upgrade pip
    "$VENV_DIR/bin/pip" install --quiet -r requirements.txt
    "$VENV_DIR/bin/pip" install --quiet -r requirements-dev.txt
    "$VENV_DIR/bin/pip" install --quiet -e .

    info "Installing pre-commit hooks..."
    "$VENV_DIR/bin/pre-commit" install
    success "Virtual environment ready."
fi

# ---------------------------------------------------------------------------
# Stage: goose
# ---------------------------------------------------------------------------
# Install the pinned goose CLI version to ~/.local/bin. This must match the
# version installed inside the container (both read from .goose-version) so
# that ORCHESTRATOR_MODE=local and ORCHESTRATOR_MODE=remote run identical
# tooling. See AOS-205.
if $DO_GOOSE; then
    GOOSE_VERSION_PINNED="$(tr -d '[:space:]' < .goose-version)"
    [[ -n "$GOOSE_VERSION_PINNED" ]] \
        || error ".goose-version is empty. Populate it with the pinned version (e.g. 1.33.1)."

    GOOSE_BIN_DIR="${HOME}/.local/bin"
    GOOSE_BIN="${GOOSE_BIN_DIR}/goose"

    installed_version=""
    if command -v goose &>/dev/null; then
        # `goose --version` prints " <version>" (leading space). Normalize.
        installed_version="$(goose --version 2>/dev/null | tr -d '[:space:]' || true)"
    fi

    if [[ "$installed_version" == "$GOOSE_VERSION_PINNED" ]]; then
        success "Goose ${GOOSE_VERSION_PINNED} already installed."
    else
        if [[ -n "$installed_version" ]] && ! $GOOSE_FORCE; then
            error "Goose ${installed_version} is already on PATH but .goose-version pins ${GOOSE_VERSION_PINNED}. Rerun with --goose-force to overwrite (or uninstall the existing binary first)."
        fi
        info "Installing goose ${GOOSE_VERSION_PINNED} to ${GOOSE_BIN_DIR}..."
        mkdir -p "$GOOSE_BIN_DIR"
        CONFIGURE=false GOOSE_BIN_DIR="$GOOSE_BIN_DIR" \
            curl -fsSL "https://github.com/aaif-goose/goose/releases/download/v${GOOSE_VERSION_PINNED}/download_cli.sh" | bash \
            || error "Goose installer failed. Check the URL and your network connection."
        actual="$("$GOOSE_BIN" --version 2>/dev/null | tr -d '[:space:]' || true)"
        [[ "$actual" == "$GOOSE_VERSION_PINNED" ]] \
            || error "Goose install verification failed: expected ${GOOSE_VERSION_PINNED}, got ${actual:-<none>}."
        success "Goose ${GOOSE_VERSION_PINNED} installed at ${GOOSE_BIN}."
        case ":${PATH}:" in
            *:"${GOOSE_BIN_DIR}":*) ;;
            *) info "Note: ${GOOSE_BIN_DIR} is not on your PATH. Add it to your shell profile to run 'goose' directly." ;;
        esac
    fi
fi

# ---------------------------------------------------------------------------
# Stage: env
# ---------------------------------------------------------------------------
if $DO_ENV; then
    info "Generating .env from .env.example (mode: ${ENV_SYNC_MODE})..."

    quote_env_value() {
        local value="$1"
        local normalized
        normalized="${value//$'\r'/}"
        normalized="${normalized//$'\n'/\\n}"
        normalized="${normalized//\\/\\\\}"
        normalized="${normalized//\"/\\\"}"
        normalized="${normalized//\$/\\$}"
        printf '"%s"\n' "$normalized"
    }

    # Check if AZURE_KEYVAULT_NAME is injected in shell environment (e.g., AKS deployment)
    # If set, it overrides the value from .env.example; otherwise use template default
    if [[ -n "${AZURE_KEYVAULT_NAME:-}" ]]; then
        KEYVAULT_NAME="${AZURE_KEYVAULT_NAME}"
        info "Using Azure Key Vault from shell environment: ${KEYVAULT_NAME}"
        KEYVAULT_NAME_Q=$(quote_env_value "$KEYVAULT_NAME")
        OVERRIDE_KEYVAULT=true
    else
        # Extract KEYVAULT_NAME from .env.example if available, otherwise use default
        if [[ -f ".env.example" ]]; then
            KEYVAULT_NAME=$(grep -E '^AZURE_KEYVAULT_NAME=' .env.example | head -1 | cut -d'=' -f2- | tr -d '"' || true)
        fi
        if [[ -z "$KEYVAULT_NAME" ]]; then
            KEYVAULT_NAME="$DEFAULT_AZURE_KEYVAULT_NAME"
        fi
        OVERRIDE_KEYVAULT=false
        info "Using Azure Key Vault from template: ${KEYVAULT_NAME}"
    fi

    get_existing_env_value() {
        local var_name="$1"
        [[ -f ".env" ]] || return 0
        local line
        line=$(grep -E "^${var_name}=" .env | tail -1 || true)
        [[ -n "$line" ]] || return 0
        line="${line#${var_name}=}"
        if [[ "$line" == '"'*'"' ]]; then
            line="${line#\"}"
            line="${line%\"}"
        fi
        printf '%s\n' "$line"
    }

    akv_read() {
        az keyvault secret show \
            --vault-name "$KEYVAULT_NAME" \
            --name "$1" \
            --query value \
            -o tsv 2>/dev/null \
            || error "Failed to read secret '$1' from Azure Key Vault '${KEYVAULT_NAME}'."
    }

    resolve_secret_value() {
        local env_name="$1"
        local secret_name="$2"
        local existing=""

        if [[ "$ENV_SYNC_MODE" == "keep" ]]; then
            existing=$(get_existing_env_value "$env_name")
            if [[ -n "$existing" ]]; then
                printf '%s\n' "$existing"
                return
            fi
        fi

        akv_read "$secret_name"
    }

    JIRA_URL=$(resolve_secret_value "JIRA_URL" "JIRA-URL")
    JIRA_OAUTH_CLIENT_ID=$(resolve_secret_value "JIRA_OAUTH_CLIENT_ID" "JIRA-OAUTH-CLIENT-ID")
    JIRA_OAUTH_CLIENT_SECRET=$(resolve_secret_value "JIRA_OAUTH_CLIENT_SECRET" "JIRA-OAUTH-CLIENT-SECRET")
    AZURE_API_KEY=$(resolve_secret_value "AZURE_API_KEY" "AZURE-API-KEY")
    ANTHROPIC_API_KEY=$(resolve_secret_value "ANTHROPIC_API_KEY" "ANTHROPIC-API-KEY")
    OTEL_BETTERSTACK_ENDPOINT=$(resolve_secret_value "OTEL_BETTERSTACK_ENDPOINT" "OTEL-BETTERSTACK-ENDPOINT")
    OTEL_BETTERSTACK_SOURCE_TOKEN=$(resolve_secret_value "OTEL_BETTERSTACK_SOURCE_TOKEN" "OTEL-BETTERSTACK-SOURCE-TOKEN")
    GITHUB_APP_ID=$(resolve_secret_value "GITHUB_APP_ID" "GITHUB-APP-ID")
    GITHUB_APP_PRIVATE_KEY=$(resolve_secret_value "GITHUB_APP_PRIVATE_KEY" "GITHUB-APP-PRIVATE-KEY")
    GITHUB_APP_INSTALLATION_ID=$(resolve_secret_value "GITHUB_APP_INSTALLATION_ID" "GITHUB-APP-INSTALLATION-ID")

    GOOSE_MCP_PYTHON="${VENV_DIR}/bin/python"

    JIRA_URL_Q=$(quote_env_value "$JIRA_URL")
    JIRA_OAUTH_CLIENT_ID_Q=$(quote_env_value "$JIRA_OAUTH_CLIENT_ID")
    JIRA_OAUTH_CLIENT_SECRET_Q=$(quote_env_value "$JIRA_OAUTH_CLIENT_SECRET")
    AZURE_API_KEY_Q=$(quote_env_value "$AZURE_API_KEY")
    ANTHROPIC_API_KEY_Q=$(quote_env_value "$ANTHROPIC_API_KEY")
    OTEL_BETTERSTACK_ENDPOINT_Q=$(quote_env_value "$OTEL_BETTERSTACK_ENDPOINT")
    OTEL_BETTERSTACK_SOURCE_TOKEN_Q=$(quote_env_value "$OTEL_BETTERSTACK_SOURCE_TOKEN")
    GITHUB_APP_ID_Q=$(quote_env_value "$GITHUB_APP_ID")
    GITHUB_APP_PRIVATE_KEY_Q=$(quote_env_value "$GITHUB_APP_PRIVATE_KEY")
    GITHUB_APP_INSTALLATION_ID_Q=$(quote_env_value "$GITHUB_APP_INSTALLATION_ID")
    GOOSE_MCP_PYTHON_Q=$(quote_env_value "$GOOSE_MCP_PYTHON")

    # Export bash variables so Python script can access them
    export JIRA_URL JIRA_OAUTH_CLIENT_ID JIRA_OAUTH_CLIENT_SECRET AZURE_API_KEY ANTHROPIC_API_KEY
    export OTEL_BETTERSTACK_ENDPOINT OTEL_BETTERSTACK_SOURCE_TOKEN
    export GITHUB_APP_ID GITHUB_APP_PRIVATE_KEY GITHUB_APP_INSTALLATION_ID
    export GOOSE_MCP_PYTHON

    # Escape characters that would break sed's | delimiter (& and \)
    escape_sed() {
        printf '%s\n' "$1" | sed 's/[&\]/\\&/g'
    }

    # Build sed command dynamically; only substitute AZURE_KEYVAULT_NAME if shell env overrides it
    if $OVERRIDE_KEYVAULT; then
        # Use bash substitution for the vault name (no newlines expected)
        sed_expr="s|^AZURE_KEYVAULT_NAME=.*|AZURE_KEYVAULT_NAME=$(escape_sed "$KEYVAULT_NAME_Q")|g"
        sed -e "$sed_expr" .env.example > .env.tmp
        mv .env.tmp .env
    else
        cp .env.example .env
    fi

    # Use Python for template substitution to avoid sed issues with multiline values
    python3 << 'PYTHON_EOF'
import os

env_file = ".env"
replacements = {
    "__JIRA_URL__": os.environ.get("JIRA_URL", ""),
    "__JIRA_OAUTH_CLIENT_ID__": os.environ.get("JIRA_OAUTH_CLIENT_ID", ""),
    "__JIRA_OAUTH_CLIENT_SECRET__": os.environ.get("JIRA_OAUTH_CLIENT_SECRET", ""),
    "__AZURE_API_KEY__": os.environ.get("AZURE_API_KEY", ""),
    "__ANTHROPIC_API_KEY__": os.environ.get("ANTHROPIC_API_KEY", ""),
    "__OTEL_BETTERSTACK_ENDPOINT__": os.environ.get("OTEL_BETTERSTACK_ENDPOINT", ""),
    "__OTEL_BETTERSTACK_SOURCE_TOKEN__": os.environ.get("OTEL_BETTERSTACK_SOURCE_TOKEN", ""),
    "__GITHUB_APP_ID__": os.environ.get("GITHUB_APP_ID", ""),
    "__GITHUB_APP_PRIVATE_KEY__": os.environ.get("GITHUB_APP_PRIVATE_KEY", ""),
    "__GITHUB_APP_INSTALLATION_ID__": os.environ.get("GITHUB_APP_INSTALLATION_ID", ""),
    "__GOOSE_MCP_PYTHON__": os.environ.get("GOOSE_MCP_PYTHON", ""),
}

with open(env_file, "r") as f:
    content = f.read()

for placeholder, value in replacements.items():
    if placeholder in content:
        # Quote multiline values with escaped newlines for shell parsing.
        # The Key Vault secret for GITHUB_APP_PRIVATE_KEY may come back already
        # escaped (literal "\n" with no real newlines).  Normalize first so the
        # escape step below is idempotent — otherwise the existing backslashes
        # get double-escaped and python-dotenv yields a corrupted PEM.
        if value:
            normalized = value
            if "\n" not in normalized and "\\n" in normalized:
                normalized = normalized.replace("\\n", "\n")
            quoted_value = (
                normalized.replace("\\", "\\\\")
                .replace('"', '\\"')
                .replace("\n", "\\n")
            )
            quoted_value = f'"{quoted_value}"'
        else:
            quoted_value = '""'
        content = content.replace(placeholder, quoted_value)

with open(env_file, "w") as f:
    f.write(content)
PYTHON_EOF

    success ".env file generated."

    extract_env_keys() {
        local file_path="$1"
        grep -E '^[A-Z][A-Z0-9_]*=' "$file_path" | cut -d'=' -f1 | sort -u
    }

    missing_keys=$(comm -23 <(extract_env_keys .env.example) <(extract_env_keys .env) || true)
    if [[ -n "$missing_keys" ]]; then
        error "Generated .env is missing keys from .env.example:\n${missing_keys}"
    fi
    success "Validated: all keys from .env.example are present in .env."

    info "Allowing direnv to load .env..."
    direnv allow .
    success "direnv configured. The .env will be loaded automatically on shell entry."
fi

# ---------------------------------------------------------------------------
# Stage: docker
# ---------------------------------------------------------------------------
if $DO_DOCKER; then
    info "Building container image '${DOCKER_IMAGE_TAG}' with ${DOCKER_BIN}..."

    # Detect buildx with a non-default driver: the resulting image stays in the
    # build cache unless --load is passed. Plain `docker build` and podman both
    # load into the local image store by default.
    DOCKER_BUILD_ARGS=(build -t "$DOCKER_IMAGE_TAG")
    if [[ "$DOCKER_BIN" == "docker" ]] && docker buildx version &>/dev/null; then
        DOCKER_BUILD_ARGS+=(--load)
    fi
    DOCKER_BUILD_ARGS+=(.)

    "$DOCKER_BIN" "${DOCKER_BUILD_ARGS[@]}" \
        || error "Container build failed. Inspect the output above."
    success "Image '${DOCKER_IMAGE_TAG}' built."
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "================================================================"
echo " Done!"
$DO_PYTHON && echo " Python:  $(pyenv version-name 2>/dev/null || echo 'n/a')"
$DO_DEPS   && echo " Venv:    ${VENV_DIR}"
$DO_GOOSE  && echo " Goose:   $(goose --version 2>/dev/null | tr -d '[:space:]' || echo 'n/a') (pinned: $(cat .goose-version 2>/dev/null || echo 'n/a'))"
$DO_ENV    && echo " .env:    $(pwd)/.env"
$DO_DOCKER && echo " Image:   ${DOCKER_IMAGE_TAG} (built with ${DOCKER_BIN:-docker})"
echo "================================================================"
$DO_ENV && echo " Re-enter this directory (or run 'direnv reload') to activate."
echo "================================================================"
