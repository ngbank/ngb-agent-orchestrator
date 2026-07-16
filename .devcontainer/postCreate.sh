#!/usr/bin/env bash
# postCreate.sh
#
# Runs once when the Codespace / dev container is created. Installs pyenv
# (setup-env.sh requires it on PATH, even for stages other than --python),
# then delegates the rest of the environment bring-up to setup-env.sh itself
# so the container and local dev flows never drift.
#
# Deliberately skipped here (left as manual follow-ups, see the summary at
# the end): --env (needs an interactive `az login`) and --docker (a full
# image build isn't needed just to open the repo).

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

echo "==> Installing pyenv build dependencies..."
sudo apt-get update -y
sudo apt-get install -y --no-install-recommends \
    build-essential libssl-dev zlib1g-dev libbz2-dev \
    libreadline-dev libsqlite3-dev libncursesw5-dev \
    xz-utils tk-dev libxml2-dev libxmlsec1-dev libffi-dev liblzma-dev

if [[ ! -d "$HOME/.pyenv" ]]; then
    echo "==> Installing pyenv..."
    curl -fsSL https://pyenv.run | bash
fi

export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$HOME/.local/bin:$PATH"
eval "$(pyenv init -)"

for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
    [[ -f "$rc" ]] || continue
    grep -q 'PYENV_ROOT' "$rc" || cat >> "$rc" <<'EOF'

export PYENV_ROOT="$HOME/.pyenv"
[[ -d "$PYENV_ROOT/bin" ]] && export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init -)"
export PATH="$HOME/.local/bin:$PATH"
EOF
done

echo "==> Running setup-env.sh (python, deps, goose)..."
./setup-env.sh --python --deps --goose

cat <<'EOF'

================================================================
 Dev container ready. Remaining manual steps:

   az login && ./setup-env.sh --env   # pull secrets from Key Vault into .env
   ./setup-env.sh --docker            # build the ngb-orchestrator:dev image

 (docker-in-docker and the azure-cli are already installed as devcontainer
 features, so both stages will work as soon as you run them.)
================================================================
EOF
