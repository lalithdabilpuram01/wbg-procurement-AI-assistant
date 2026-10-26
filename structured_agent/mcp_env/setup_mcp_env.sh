#!/usr/bin/env bash
# Creates the isolated Python environment for the Structured Data MCP server.
#
# The shared Anaconda environment is only used as the base interpreter (Python 3.12.2); no package is installed
# into it. Run from procurement-intelligence/:
#     bash structured_agent/mcp_env/setup_mcp_env.sh            # install from the lock file (reproducible)
#     bash structured_agent/mcp_env/setup_mcp_env.sh --resolve  # re-resolve requirements.txt and rewrite the lock
set -euo pipefail

BASE_PYTHON="${BASE_PYTHON:-/opt/anaconda3/bin/python3.12}"
VENV_DIR="${VENV_DIR:-.venv-structured-mcp}"
ENV_DIR="structured_agent/mcp_env"

"$BASE_PYTHON" -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip==25.2

if [[ "${1:-}" == "--resolve" ]]; then
    "$VENV_DIR/bin/python" -m pip install --quiet -r "$ENV_DIR/requirements.txt"
    "$VENV_DIR/bin/python" -m pip freeze --exclude-editable > "$ENV_DIR/requirements.lock.txt"
else
    "$VENV_DIR/bin/python" -m pip install --quiet -r "$ENV_DIR/requirements.lock.txt"
fi

"$VENV_DIR/bin/python" -m pip check
"$VENV_DIR/bin/python" -c "import anyio, mcp, fastmcp; print('anyio', anyio.__version__ if hasattr(anyio, '__version__') else 'ok', '| fastmcp', fastmcp.__version__)"
echo "MCP environment ready: $VENV_DIR"
