#!/usr/bin/env bash
# ==============================================================================
# Cleo Launcher for macOS & Linux
# ==============================================================================
set -e

# Resolve Cleo directory
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

# 1. Prefer Python in virtual environment if available
if [ -f "$ROOT_DIR/.venv/bin/python3" ]; then
    PYTHON_CMD="$ROOT_DIR/.venv/bin/python3"
elif [ -f "$ROOT_DIR/.venv/bin/python" ]; then
    PYTHON_CMD="$ROOT_DIR/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_CMD="python"
else
    echo "❌ Error: Python 3 is not installed or not in PATH."
    echo "Please install Python 3.10+ from https://www.python.org/downloads/ or via your package manager."
    exit 1
fi

# 2. Run Cleo Server
exec "$PYTHON_CMD" "$ROOT_DIR/cleo_server.py" "$@"
