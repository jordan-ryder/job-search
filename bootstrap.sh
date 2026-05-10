#!/usr/bin/env bash
# Create .venv with uv and install requirements.
# Source this script (`. ./bootstrap.sh`) to leave the venv active in your shell;
# running it normally still creates+installs the venv but won't activate it.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

if ! command -v uv >/dev/null 2>&1; then
    echo "uv not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
    exit 1
fi

if [[ ! -d .venv ]]; then
    echo "creating venv at .venv"
    uv venv
else
    echo "venv already exists at .venv"
fi

uv pip install --python .venv/bin/python -r requirements.txt

# If sourced, activate; if executed, just report the path.
if (return 0 2>/dev/null); then
    # shellcheck disable=SC1091
    source .venv/bin/activate
    echo "done. python -> $(command -v python)"
else
    echo "done. activate with: source .venv/bin/activate"
fi
