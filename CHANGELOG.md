# Changelog

## v2.0.0 — 2026-10-19

**Major release introducing Over-The-Air (OTA) Dynamic Code Loading, enhanced multi-cloud datasets, and conversational continuity.**

### 🔄 Over-The-Air (OTA) Dynamic Code Loading
- **Zero-Rebuild Continuous Delivery**: Tauri sidecar dynamically checks and syncs the latest code from GitHub into `~/.cleo/src` on startup.
- **Dynamic Importer Precedence**: Reorders `sys.meta_path` so local/synced updates take precedence over frozen bundle modules while seamlessly falling back if offline.
- **Dynamic Web UI Serving**: `/newChat` and GUI endpoints serve the latest `cleo_ui.html` live from the OTA source folder.

### 💰 FinOps & Multi-Cloud Datasets
- **EBS Volume Type & Daily Drilldowns**: Added deep dimension support for `product_volumeType` with automatic daily granularity when exploring storage historical trends.
- **Expanded Datasets**: Integrated K8S, RDS, Data Transfer, Databricks, Datacenter, and native billing export datasets with automatic provider-priority routing.
- **Conversational Contextual Continuation**: Intelligent intent detection preserves query dimensions and timeframes across conversational turns.

### 🎨 UI & UX Improvements
- **Regenerate Icon Fix**: Corrected distorted arrowhead on assistant message action buttons with clean Lucide circular geometry.

## v1.0.0 — 2026-10-18

**First stable release of Cleo — Autonomous Multi-Cloud FinOps Copilot.**

### 🖥️ Desktop App (Tauri)
- Native macOS app via Tauri 2 — packaged as a signed `.app` + `.dmg`
- Bundled Python backend sidecar (`cleo-server`) — no separate install needed
- macOS app icon (cube on dark tile, macOS-style)
- Minimum macOS: **11.0 Big Sur** · Apple Silicon (ARM64) build

### 🤖 AI & Agent
- Gemini 3.5 Flash as primary cloud AI engine (Gemini Live for streaming)
- Local model support: **MLX** (Apple Silicon) and **Ollama** (cross-platform)
- Multi-turn chat context passed to LLM for ambiguous conversational follow-ups
- Direct LLM query pass-through for general FinOps advisory
- Auto-unload local models on engine switch and server shutdown
- Live query stop (interrupt streaming responses mid-flight)

### 💰 FinOps Queries
- AWS, Azure, GCP cost history with MTD / QTD / YTD / MoM support
- Multi-service queries with separate charts and unified insights
- Anomaly detection at **daily** granularity with Marketplace tags
- Forward-looking cost forecasting and budget projection engine
- ROI simulation with 4-tier break-even taxonomy
- `exclude_other` / `exclude_unallocated` filtering
- Azure Hybrid Benefit (AHB) query and TCO/Dev-Test insights
- Multi-cloud org cost comparison and cross-session drill-downs

### 📊 Charts & UI
- Stacked area, stacked bar, waterfall, line+variance, pie/doughnut
- Sharp/smooth curve toggle + GCP-style geometric point shapes
- Chart workbench with live market data
- Expanded modal chart view with correct centering
- Active vs. Selected model badge in UI

### 🔐 Security
- Owner-only data files and secret redaction in logs
- Hardened server surface: SRI, no-eval CSP, state check, input limits
- tarfile path traversal fix, Gemini key removed from URL
- PKCE OAuth (no client secret required)

### 🏗️ Infrastructure
- One-click launchers: `run` (macOS), `run.sh`, `run.bat`, `run.ps1`
- Docker + `docker-compose.yml` for server-mode deployment
- Auto-bootstrap venv with PEP 668 and corporate proxy fallback
- Corporate PyPI 403 block detection with mirror fallback
- Windows daemon connection timeout fix for Ollama

## 📦 File Details

| Architecture | Platform | File to Download | SHA-256 Checksum |
| :---: | :--- | :--- | :--- |
| ![macOS](https://raw.githubusercontent.com/digitalsuni-cloud/cpb-ch/main/public/badges/macos.svg) | Apple Silicon (ARM64) | [Cleo_1.0.0_arm64.dmg](https://github.com/digitalsuni-cloud/cleo/releases/download/v1.0.0/Cleo_1.0.0_arm64.dmg) | `9782d612ce3ca04e676dbd918cc4b17ff0f1c2513bf05823fbd2c3070fa14d6f` |
| ![Linux App](https://raw.githubusercontent.com/digitalsuni-cloud/cpb-ch/main/public/badges/linux-app.svg) | Linux x64 (AppImage) | [Cleo_1.0.0_amd64.AppImage](https://github.com/digitalsuni-cloud/cleo/releases/download/v1.0.0/Cleo_1.0.0_amd64.AppImage) | *Automated via GitHub Actions* |
| ![Linux Deb](https://raw.githubusercontent.com/digitalsuni-cloud/cpb-ch/main/public/badges/linux-deb.svg) | Linux x64 (DEB) | [Cleo_1.0.0_amd64.deb](https://github.com/digitalsuni-cloud/cleo/releases/download/v1.0.0/Cleo_1.0.0_amd64.deb) | *Automated via GitHub Actions* |
| ![Windows](https://raw.githubusercontent.com/digitalsuni-cloud/cpb-ch/main/public/badges/windows.svg) | Universal / Portable | [Cleo_1.0.0_x64.exe](https://github.com/digitalsuni-cloud/cleo/releases/download/v1.0.0/Cleo_1.0.0_x64.exe) | *Automated via GitHub Actions* |
| 📄 | Checksums Manifest | [SHA256SUMS.txt](https://github.com/digitalsuni-cloud/cleo/releases/download/v1.0.0/SHA256SUMS.txt) | *Complete checksum manifest* |

## 🛠 macOS Installation Note
This application is built for internal use and is unsigned. If you see an error saying the app is "damaged" or cannot be opened, run this command in your Terminal:

```bash
xattr -rd com.apple.quarantine "/Applications/Cleo.app"
```

## 🔐 Cryptographic Verification
Releases and tags are signed with Cleo's release signing key:
- **Public Key**: [`cleo-release.asc`](https://raw.githubusercontent.com/digitalsuni-cloud/cleo/main/cleo-release.asc)
- **Key Fingerprint**: `3DB3 0B75 4E26 A1D0 C4B0  308A D8C9 20CB EA46 CBF6`
- **Verify Tag**: `git verify-tag v1.0.0`
- **Verify Download**: `sha256sum -c SHA256SUMS.txt`

