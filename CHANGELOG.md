# Changelog

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

---

### Download

| Platform | File | SHA-256 |
|----------|------|---------|
| macOS Apple Silicon | `Cleo_1.0.0_arm64.dmg` | `9782d612ce3ca04e676dbd918cc4b17ff0f1c2513bf05823fbd2c3070fa14d6f` |
