#!/usr/bin/env bash
# ==============================================================================
# Cleo Launcher for macOS & Linux
# ==============================================================================
set -e

# Resolve Cleo directory
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

# 1. Import Cleo release signing key (once, silently — enables verified auto-updates)
#    Safe to skip: if gpg is absent or the key is already trusted, this is a no-op.
_CLEO_KEY_FP="3DB30B754E26A1D0C4B0308AD8C920CBEA46CBF6"
_CLEO_ASC="$ROOT_DIR/cleo-release.asc"
if command -v gpg > /dev/null 2>&1 && [ -f "$_CLEO_ASC" ]; then
    if ! gpg --list-keys "$_CLEO_KEY_FP" > /dev/null 2>&1; then
        gpg --import "$_CLEO_ASC" > /dev/null 2>&1 && \
            echo "✓ Cleo release signing key imported (enables verified updates)." || true
    fi
fi

# 2. Auto-pull latest verified signed release (skip if CLEO_NO_UPDATE=1 or --no-update)
if [ "${CLEO_NO_UPDATE:-0}" != "1" ] && [ "${1:-}" != "--no-update" ]; then
    if [ -d "$ROOT_DIR/.git" ] && command -v git > /dev/null 2>&1; then
        git fetch --tags --quiet origin 2>/dev/null || true
        LATEST_TAG=$(git tag -l "v*" --sort=-v:refname 2>/dev/null | head -n 1)
        if [ -n "$LATEST_TAG" ]; then
            if ! git merge-base --is-ancestor "$LATEST_TAG" HEAD 2>/dev/null; then
                if command -v gpg > /dev/null 2>&1 && git verify-tag "$LATEST_TAG" > /dev/null 2>&1; then
                    echo "🔄 Updating Cleo to verified release $LATEST_TAG..."
                    git checkout "$LATEST_TAG" --quiet 2>/dev/null || git merge --ff-only "$LATEST_TAG" --quiet 2>/dev/null || true
                else
                    echo "⚠️  New release $LATEST_TAG found, but signature could not be verified. Skipping update."
                fi
            fi
        fi
    fi
fi

# 3. Prefer active system Python if available
if command -v python3 >/dev/null 2>&1 && python3 -c "import sys" >/dev/null 2>&1; then
    PYTHON_CMD="python3"
elif command -v python >/dev/null 2>&1 && python -c "import sys" >/dev/null 2>&1; then
    PYTHON_CMD="python"
elif [ -f "$ROOT_DIR/.venv/bin/python3" ]; then
    PYTHON_CMD="$ROOT_DIR/.venv/bin/python3"
elif [ -f "$ROOT_DIR/.venv/bin/python" ]; then
    PYTHON_CMD="$ROOT_DIR/.venv/bin/python"
else
    echo "❌ Error: Python 3 is not installed or not in PATH."
    echo "Please install Python 3.10+ from https://www.python.org/downloads/ or via your package manager."
    exit 1
fi

# 4. Run Cleo Server
exec "$PYTHON_CMD" "$ROOT_DIR/cleo_server.py" "$@"
