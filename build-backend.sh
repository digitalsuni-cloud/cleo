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
    if [ "$ARCH" = "aarch64" ]; then
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

# Resolve PyInstaller binary
PYINSTALLER="pyinstaller"
if command -v pyinstaller >/dev/null 2>&1; then
    PYINSTALLER="pyinstaller"
elif [ -f ".venv/bin/pyinstaller" ]; then
    PYINSTALLER=".venv/bin/pyinstaller"
elif [ -f ".venv/Scripts/pyinstaller.exe" ]; then
    PYINSTALLER=".venv/Scripts/pyinstaller.exe"
elif command -v python3 >/dev/null 2>&1; then
    PYINSTALLER="python3 -m PyInstaller"
elif command -v python >/dev/null 2>&1; then
    PYINSTALLER="python -m PyInstaller"
fi

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
  --hidden-import "uvicorn.protocols.websockets.auto" \
  --hidden-import "uvicorn.lifespans" \
  --hidden-import "uvicorn.lifespans.auto" \
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
  cleo_server.py

chmod +x "$OUT_BIN" 2>/dev/null || true
echo "✓ Backend built successfully: $OUT_BIN"
