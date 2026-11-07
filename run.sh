#!/usr/bin/env bash
# ==============================================================================
# Cleo Launcher for macOS & Linux
# ==============================================================================
set -e

# Resolve Cleo directory
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

# Auto-pull latest updates from GitLab (skip if CLEO_NO_UPDATE=1 or --no-update)
if [ "${CLEO_NO_UPDATE:-0}" != "1" ] && [ "${1:-}" != "--no-update" ]; then
    if [ -d "$ROOT_DIR/.git" ] && command -v git > /dev/null 2>&1; then
        echo "🔄 Checking for Cleo updates from GitLab..."
        if git fetch --tags --force origin; then
            CURRENT_BRANCH=$(git symbolic-ref --short -q HEAD 2>/dev/null || true)
            if [ -n "$CURRENT_BRANCH" ]; then
                if ! git pull --ff-only origin "$CURRENT_BRANCH"; then
                    if [ -z "$(git status --porcelain 2>/dev/null)" ]; then
                        echo "🔄 Fast-forward unavailable; syncing branch to origin/$CURRENT_BRANCH..."
                        git reset --hard origin/"$CURRENT_BRANCH"
                    fi
                fi
            fi

            LATEST_TAG=$(git tag -l "v*" --sort=-v:refname 2>/dev/null | head -n 1)
            if [ -n "$LATEST_TAG" ]; then
                if ! git merge-base --is-ancestor "$LATEST_TAG" HEAD 2>/dev/null; then
                    echo "🔄 Updating Cleo to release $LATEST_TAG..."
                    git checkout "$LATEST_TAG" || git merge --ff-only "$LATEST_TAG" || true
                fi
            fi
            echo "✓ Cleo is up to date."
        else
            echo "⚠️  Could not reach GitLab; continuing with local version."
        fi
        echo ""
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
