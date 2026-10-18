#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Cleo — Autonomous Cloud FinOps Copilot — Release Build Script
# Following CHConfig Tauri build standards.
# Usage: ./build-release.sh
#
# What it does:
#   1. Reads version from package.json
#   2. Archives any existing .dmg / .app in release/Tauri/ → release/archive/<version>/
#   3. Builds the standalone Python backend binary via PyInstaller
#   4. Runs the Tauri production build
#   5. Copies the new .dmg and .app into release/Tauri/
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RELEASE_DIR="$SCRIPT_DIR/release/Tauri"
ARCHIVE_DIR="$SCRIPT_DIR/release/archive"
BUNDLE_BASE_ARM="$SCRIPT_DIR/src-tauri/target/aarch64-apple-darwin/release/bundle"
BUNDLE_BASE_INTEL="$SCRIPT_DIR/src-tauri/target/x86_64-apple-darwin/release/bundle"

# ── 1. Read version from package.json ────────────────────────────────────────
VERSION=$(python3 -c "import json; print(json.load(open('package.json'))['version'])")
echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║      Cleo FinOps Copilot  ·  Release Build  v$VERSION     ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# ── 2. Archive any existing release packages ──────────────────────────────────
shopt -s nullglob
EXISTING_DMGS=("$RELEASE_DIR"/*.dmg)
EXISTING_APPS=("$RELEASE_DIR"/*.app)

HAS_EXISTING=false
for f in "${EXISTING_DMGS[@]}" "${EXISTING_APPS[@]}"; do
  [ -e "$f" ] && HAS_EXISTING=true && break
done

if [ "$HAS_EXISTING" = true ]; then
  EXISTING_VERSION=$(basename "${EXISTING_DMGS[0]:-}" 2>/dev/null | sed -E 's/.*_([0-9]+\.[0-9]+\.[0-9]+)_.*/\1/' || echo "")
  [ -z "$EXISTING_VERSION" ] && EXISTING_VERSION="previous"
  ARCHIVE_TARGET="$ARCHIVE_DIR/${EXISTING_VERSION:-previous}"
  mkdir -p "$ARCHIVE_TARGET"
  echo "→ Archiving existing v${EXISTING_VERSION:-previous} packages to release/archive/${EXISTING_VERSION:-previous}/"
  for f in "$RELEASE_DIR"/*.dmg "$RELEASE_DIR"/*.app; do
    if [ -e "$f" ]; then
      TS=$(date +%Y%m%d_%H%M%S)
      DEST_DIR="$ARCHIVE_TARGET/$TS"
      mkdir -p "$DEST_DIR"
      mv -f "$f" "$DEST_DIR/"
    fi
  done
  echo "  Done."
else
  echo "→ No existing packages in release/ to archive."
fi

echo ""

# ── 3. Build Standalone Python Backend Sidecar ──────────────────────────────
echo "→ Building Python standalone backend sidecar..."
bash "$SCRIPT_DIR/build-backend.sh"
echo ""

# ── 4. Run the Tauri build ───────────────────────────────────────────────────
OS_TYPE="$(uname -s)"
echo "→ Detected OS: $OS_TYPE"

if [ ! -d "node_modules" ]; then
  echo "→ node_modules missing. Running npm install..."
  npm install
fi

if [ "$OS_TYPE" == "Darwin" ]; then
  echo "→ Ensuring Mac Rust targets are present..."
  rustup target add aarch64-apple-darwin || true

  echo "→ Building v$VERSION for Apple Silicon (arm64)..."
  npx @tauri-apps/cli build --target aarch64-apple-darwin

  mkdir -p "$RELEASE_DIR"

  # Copy ARM64 DMG & App
  DMG_SRC_ARM="$BUNDLE_BASE_ARM/dmg/Cleo_${VERSION}_aarch64.dmg"
  if [ -f "$DMG_SRC_ARM" ]; then
    cp -f "$DMG_SRC_ARM" "$RELEASE_DIR/Cleo_${VERSION}_arm64.dmg"
    echo "→ Copied: release/Tauri/Cleo_${VERSION}_arm64.dmg"
  fi

  APP_SRC_ARM="$BUNDLE_BASE_ARM/macos/Cleo.app"
  if [ -d "$APP_SRC_ARM" ]; then
    rm -rf "$RELEASE_DIR/Cleo.app"
    cp -R "$APP_SRC_ARM" "$RELEASE_DIR/Cleo.app"
    echo "→ Copied: release/Tauri/Cleo.app"
  fi

elif [ "$OS_TYPE" == "Linux" ]; then
  echo "→ Building v$VERSION (Linux)..."
  ARCH="$(uname -m)"
  npx @tauri-apps/cli build
  mkdir -p "$RELEASE_DIR"

  APPIMAGE_PATH="$SCRIPT_DIR/src-tauri/target/release/bundle/appimage"
  DEB_PATH="$SCRIPT_DIR/src-tauri/target/release/bundle/deb"
  shopt -s nullglob
  for f in "$APPIMAGE_PATH"/*.AppImage "$DEB_PATH"/*.deb; do
    if [ -f "$f" ]; then
      cp -f "$f" "$RELEASE_DIR/"
      echo "→ Copied: $RELEASE_DIR/$(basename "$f")"
    fi
  done
  shopt -u nullglob
fi

echo ""
echo "✓ Release v$VERSION is ready in: release/Tauri/"
echo ""
ls -lh "$RELEASE_DIR"/* 2>/dev/null || true
echo ""
