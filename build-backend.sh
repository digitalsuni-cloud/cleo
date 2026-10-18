#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Detect target architecture
ARCH="$(uname -m)"
OS="$(uname -s)"

EXT=""
SEP=":"
if [ "$OS" = "Darwin" ]; then
    if [ "$ARCH" = "arm64" ]; then
        TARGET="aarch64-apple-darwin"
    else
        TARGET="x86_64-apple-darwin"
    fi
elif [ "$OS" = "Linux" ]; then
    if [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then
        TARGET="aarch64-unknown-linux-gnu"
    else
        TARGET="x86_64-unknown-linux-gnu"
    fi
else
    TARGET="x86_64-pc-windows-msvc"
    EXT=".exe"
    SEP=";"
fi

BIN_DIR="$SCRIPT_DIR/src-tauri/binaries"
mkdir -p "$BIN_DIR"
OUT_BIN="$BIN_DIR/cleo-server-$TARGET$EXT"

if [ -f "$OUT_BIN" ] && [ "${1:-}" != "--force" ]; then
    echo "✓ Backend binary already exists: $OUT_BIN (pass --force to rebuild)"
    exit 0
fi

echo "→ Building Cleo standalone backend for $TARGET..."

# Resolve or prepare PyInstaller
find_pyinstaller() {
    if [ -f "$SCRIPT_DIR/.venv/bin/pyinstaller" ]; then
        echo "$SCRIPT_DIR/.venv/bin/pyinstaller"
    elif [ -f "$SCRIPT_DIR/.venv/Scripts/pyinstaller.exe" ]; then
        echo "$SCRIPT_DIR/.venv/Scripts/pyinstaller.exe"
    elif [ -n "${VIRTUAL_ENV:-}" ] && [ -f "${VIRTUAL_ENV}/bin/pyinstaller" ]; then
        echo "${VIRTUAL_ENV}/bin/pyinstaller"
    elif command -v pyinstaller >/dev/null 2>&1; then
        echo "pyinstaller"
    elif python3 -c "import PyInstaller" 2>/dev/null; then
        echo "python3 -m PyInstaller"
    elif python -c "import PyInstaller" 2>/dev/null; then
        echo "python -m PyInstaller"
    else
        echo ""
    fi
}

PYINSTALLER="$(find_pyinstaller)"

if [ -z "$PYINSTALLER" ]; then
    echo "→ PyInstaller not detected. Attempting automatic environment setup..."

    # 1. If .venv exists, install PyInstaller and requirements into it
    if [ -f "$SCRIPT_DIR/.venv/bin/pip" ]; then
        echo "→ Installing PyInstaller and dependencies into .venv..."
        "$SCRIPT_DIR/.venv/bin/pip" install --quiet --upgrade pip 2>/dev/null || true
        "$SCRIPT_DIR/.venv/bin/pip" install --quiet -r "$SCRIPT_DIR/requirements.txt" pyinstaller 2>/dev/null || true
    # 2. If no .venv exists, try creating one
    elif command -v python3 >/dev/null 2>&1; then
        echo "→ Creating virtual environment at .venv..."
        if python3 -m venv "$SCRIPT_DIR/.venv" 2>/dev/null; then
            "$SCRIPT_DIR/.venv/bin/pip" install --quiet --upgrade pip 2>/dev/null || true
            "$SCRIPT_DIR/.venv/bin/pip" install --quiet -r "$SCRIPT_DIR/requirements.txt" pyinstaller 2>/dev/null || true
        fi
    fi

    # 3. Re-check if PyInstaller is available now
    PYINSTALLER="$(find_pyinstaller)"

    # 4. Fallback to user pip install if .venv creation was blocked
    if [ -z "$PYINSTALLER" ]; then
        if command -v pip3 >/dev/null 2>&1 || command -v pip >/dev/null 2>&1; then
            echo "→ Attempting pip install for PyInstaller..."
            python3 -m pip install --quiet --user pyinstaller 2>/dev/null || \
            python3 -m pip install --quiet --break-system-packages --user pyinstaller 2>/dev/null || \
            pip install --quiet pyinstaller 2>/dev/null || true
            PYINSTALLER="$(find_pyinstaller)"
        fi
    fi
fi

if [ -z "$PYINSTALLER" ]; then
    echo ""
    echo "❌ Error: PyInstaller is required to build the standalone backend sidecar."
    echo ""
    echo "To resolve on Ubuntu / Debian:"
    echo "  sudo apt update && sudo apt install -y python3-venv python3-pip"
    echo "  python3 -m venv .venv"
    echo "  .venv/bin/pip install -r requirements.txt pyinstaller"
    echo ""
    echo "To resolve on macOS / other systems:"
    echo "  pip install -r requirements.txt pyinstaller"
    echo ""
    exit 1
fi

echo "→ Using PyInstaller: $PYINSTALLER"

# Run PyInstaller with all required hidden imports and data files
$PYINSTALLER --noconfirm --clean --onefile \
  --name "cleo-server-$TARGET" \
  --distpath "$BIN_DIR" \
  --workpath "$SCRIPT_DIR/build/pyinstaller" \
  --add-data "cleo_ui.html${SEP}." \
  --add-data ".agents/skills/cloud-finops${SEP}.agents/skills/cloud-finops" \
  --hidden-import "uvicorn" \
  --hidden-import "uvicorn.logging" \
  --hidden-import "uvicorn.loops" \
  --hidden-import "uvicorn.loops.auto" \
  --hidden-import "uvicorn.protocols" \
  --hidden-import "uvicorn.protocols.http" \
  --hidden-import "uvicorn.protocols.http.auto" \
  --hidden-import "uvicorn.protocols.websockets" \
  --hidden-import "uvicorn.lifespan" \
  --hidden-import "uvicorn.lifespan.on" \
  --hidden-import "uvicorn.lifespan.off" \
  --hidden-import "uvicorn.lifespan.auto" \
  --hidden-import "fastapi" \
  --hidden-import "starlette" \
  --hidden-import "zstandard" \
  --hidden-import "cleo_agent" \
  --hidden-import "cleo_mcp" \
  --hidden-import "cleo_finops_refs" \
  --hidden-import "cleo_query" \
  --hidden-import "cleo_charts" \
  --hidden-import "cleo_llm" \
  --hidden-import "cleo_logger" \
  --hidden-import "cleo_memory" \
  --hidden-import "cleo_market_data" \
  --hidden-import "auth" \
  --hidden-import "auth.oauth" \
  --hidden-import "cryptography" \
  --hidden-import "cleo_crypto" \
  cleo_server.py

chmod +x "$OUT_BIN" 2>/dev/null || true
echo "✓ Backend built successfully: $OUT_BIN"
