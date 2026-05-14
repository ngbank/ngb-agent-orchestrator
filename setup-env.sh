#!/usr/bin/env bash
# setup-env.sh
#
# Sets up the local development environment for ngb-agent-orchestrator.
#
# Stages (all run by default; pass flags to run specific stages only):
#   --python   Install Python 3.13.x via pyenv and pin .python-version
#   --deps     Create/update the venv and install pip dependencies
#   --env      Fetch secrets from 1Password and generate .env
#   --clean    Remove venv/ and .env, then run all stages from scratch
#
# Examples:
#   ./setup-env.sh                   # run all stages
#   ./setup-env.sh --clean           # wipe and rebuild everything
#   ./setup-env.sh --deps            # reinstall dependencies only
#   ./setup-env.sh --python --deps   # reinstall Python + deps, skip .env
#   ./setup-env.sh --env             # refresh .env from 1Password only
#
# Prerequisites for --env:
#   export OP_SA_TOKEN="<your-service-account-token>"
#
# First-time 1Password setup (run once):
#   ./scripts/bootstrap-1password.sh

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PYTHON_MAJOR="3.13"
OP_VAULT="NG Bank"
OP_ITEM="NGB Agent Orchestrator"

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
DO_ENV=false
DO_CLEAN=false

if [[ $# -eq 0 ]]; then
    DO_PYTHON=true
    DO_DEPS=true
    DO_ENV=true
else
    for arg in "$@"; do
        case "$arg" in
            --python) DO_PYTHON=true ;;
            --deps)   DO_DEPS=true ;;
            --env)    DO_ENV=true ;;
            --clean)  DO_CLEAN=true ;;
            --help|-h) usage ;;
            *) error "Unknown flag: $arg. Valid flags: --python, --deps, --env, --clean" ;;
        esac
    done
fi

# --clean enables all stages and wipes artifacts first
if $DO_CLEAN; then
    DO_PYTHON=true
    DO_DEPS=true
    DO_ENV=true
fi

VENV_DIR="$(pwd)/venv"

# ---------------------------------------------------------------------------
# Clean (runs before all other stages)
# ---------------------------------------------------------------------------
if $DO_CLEAN; then
    info "Cleaning environment artifacts..."
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
    command -v op &>/dev/null \
        || error "'op' CLI (1Password) is not installed or not on PATH. Install from: https://developer.1password.com/docs/cli/"
    [[ -z "${OP_SA_TOKEN:-}" ]] \
        && error "OP_SA_TOKEN is not set. Export your 1Password service account token and retry."
    export OP_SERVICE_ACCOUNT_TOKEN="$OP_SA_TOKEN"
fi

if $DO_PYTHON; then
    command -v pyenv &>/dev/null \
        || error "'pyenv' is not installed or not on PATH. Install from: https://github.com/pyenv/pyenv#installation"
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
# Stage: env
# ---------------------------------------------------------------------------
if $DO_ENV; then
    info "Fetching secrets from 1Password (vault: '${OP_VAULT}', item: '${OP_ITEM}')..."

    op_read() {
        op read "op://${OP_VAULT}/${OP_ITEM}/$1" 2>/dev/null \
            || error "Failed to read field '$1' from 1Password. Ensure the vault/item/field exist (run scripts/bootstrap-1password.sh first)."
    }

    JIRA_URL=$(op_read "JIRA_URL")
    JIRA_EMAIL=$(op_read "JIRA_EMAIL")
    JIRA_API_TOKEN=$(op_read "JIRA_API_TOKEN")
    AZURE_API_KEY=$(op_read "AZURE_API_KEY")
    ANTHROPIC_API_KEY=$(op_read "ANTHROPIC_API_KEY")
    GOOSE_MCP_PYTHON="${VENV_DIR}/bin/python"

    success "Secrets retrieved."
    info "Generating .env from .env.example..."

    # Escape characters that would break sed's | delimiter (& and \)
    escape_sed() {
        printf '%s\n' "$1" | sed 's/[&\]/\\&/g'
    }

    sed \
        -e "s|__JIRA_URL__|$(escape_sed "$JIRA_URL")|g" \
        -e "s|__JIRA_EMAIL__|$(escape_sed "$JIRA_EMAIL")|g" \
        -e "s|__JIRA_API_TOKEN__|$(escape_sed "$JIRA_API_TOKEN")|g" \
        -e "s|__AZURE_API_KEY__|$(escape_sed "$AZURE_API_KEY")|g" \
        -e "s|__ANTHROPIC_API_KEY__|$(escape_sed "$ANTHROPIC_API_KEY")|g" \
        -e "s|__GOOSE_MCP_PYTHON__|$(escape_sed "$GOOSE_MCP_PYTHON")|g" \
        .env.example > .env

    success ".env file generated."

    info "Allowing direnv to load .env..."
    direnv allow .
    success "direnv configured. The .env will be loaded automatically on shell entry."
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "================================================================"
echo " Done!"
$DO_PYTHON && echo " Python:  $(pyenv version-name 2>/dev/null || echo 'n/a')"
$DO_DEPS  && echo " Venv:    ${VENV_DIR}"
$DO_ENV   && echo " .env:    $(pwd)/.env"
echo "================================================================"
$DO_ENV && echo " Re-enter this directory (or run 'direnv reload') to activate."
echo "================================================================"
