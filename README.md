# <img src="docs/cleo-icon.svg" width="34" height="34" alt="Cleo" align="center" /> Cleo — Cloud Efficiency Optimizer `BETA`

**Cleo** is your AI assistant for cloud cost intelligence. Ask questions about your AWS, Azure, and GCP spend in plain English and get live answers straight from CloudHealth — no dashboards, no SQL, no manual reports.

> *"What were my top 5 services by cost last month?"*  
> *"Which team spent the most in August?"*  
> *"Show me Azure spend trends for Q3."*

---

## 📋 What You Need Before Starting

| Requirement | Why |
|---|---|
| **Python 3.11 or newer** | Cleo is written in Python |
| **A CloudHealth account** | Where your cloud billing data lives |
| **Internet access** | To connect to CloudHealth |
| **4 GB RAM minimum** | 8 GB+ recommended if using local AI models |

**Optional — to use a local AI model (no API key needed):**

| Platform | What you need |
|---|---|
| Apple Silicon Mac (M1/M2/M3/M4) | Nothing extra — Cleo installs it automatically |
| Windows / Linux | Docker Desktop (for Docker install path) |

---

## ⚡ Quickstart (3 steps)

### Step 1 — Download Cleo

Open a terminal and run:

```bash
git clone https://github.com/yourorg/cleo.git
cd cleo
```

> **Don't have git?** Download the ZIP from the GitHub page → click **Code → Download ZIP**, then unzip it and open the folder in your terminal.

---

### Step 2 — Start Cleo

```bash
python3 cleo_server.py
```

That's it. Cleo will:
- Create a Python virtual environment automatically (`.venv/`)
- Install all required packages automatically
- Start the web interface at **http://localhost:8080**

> On **Windows**, use `python cleo_server.py` instead of `python3`.

You should see output like:
```
⚙️  Setting up isolated virtual environment in .venv ...
📦 Installing required packages from requirements.txt ...
🍎 Apple Silicon detected. Auto-installing 'mlx-lm' for local Qwen support...
✅ Setup complete! Starting Cleo Server...

INFO:     Uvicorn running on http://127.0.0.1:8080
```

---

### Step 3 — Open the Web UI

Open your browser and go to:

```
http://localhost:8080
```

You'll see the Cleo chat interface. Before you can ask questions, you need to connect your CloudHealth account (see below).

---

## 🔐 Connecting CloudHealth (Authentication)

Cleo needs to connect to your CloudHealth account to access your billing data. This is a one-time setup.

### How to Authenticate

1. In the Cleo web interface, click the **"Connect CloudHealth"** button (top right, shown as a status pill).

2. Your browser will open the **CloudHealth login page**. Log in with your CloudHealth credentials.

3. After logging in, click **Approve** when CloudHealth asks for permission.

4. Cleo **automatically completes authorization** and connects in the background — no manual code copying or token pasting is required!

5. The status pill will turn **green** and show **"CloudHealth Connected"**.

### What Cleo Stores

| File | What's in it | Where |
|---|---|---|
| `~/.cleo/oauth_tokens.json` | Your CloudHealth access token (auto-refreshed) | Your home folder |
| `~/.cleo/config.json` | AI engine API keys (if you use cloud AI) | Your home folder |
| `~/.cleo/sessions.json` | Your chat history | Your home folder |

> **`~`** means your home folder: `/Users/yourname/` on Mac, `C:\Users\yourname\` on Windows.

Your token is automatically refreshed on every run. **You only need to log in once** — unless your token expires (typically after many months of inactivity).

**To re-authenticate:** Delete `~/.cleo/oauth_tokens.json` and reconnect via the UI.

---

## 🧠 Choosing an AI Engine

Cleo can answer your questions using several AI engines. You choose in the **Settings panel** (click the ⚙️ gear icon in the UI).

### Option A — Local AI (Free, No API Key)

Run AI entirely on your machine. Recommended for privacy and zero ongoing cost.

| Model | Platform | RAM Needed | Speed |
|---|---|---|---|
| **Qwen2.5-7B** (recommended) | Apple Silicon Mac | ~5 GB | Fast |
| **Qwen2.5-3B** (lighter) | Apple Silicon Mac | ~2 GB | Very fast |
| **Qwen2.5-7B via Ollama** | Windows / Linux / Mac | ~5 GB | Fast |

**On Apple Silicon Mac:** Cleo installs `mlx-lm` automatically. Just select a local model in the UI and click **Download** — Cleo pulls the model from Hugging Face (one-time download, ~4 GB).

**On Windows/Linux:** Use Docker (see Docker section below).

### Option B — Cloud AI (Requires API Key)

| Engine | Where to get a key | Env variable |
|---|---|---|
| **Google Gemini** | [aistudio.google.com](https://aistudio.google.com) | `GEMINI_API_KEY` |
| **OpenAI GPT-4o** | [platform.openai.com](https://platform.openai.com) | `OPENAI_API_KEY` |
| **Anthropic Claude** | [console.anthropic.com](https://console.anthropic.com) | `ANTHROPIC_API_KEY` |

**How to add your API key:**

Option 1 — Enter it in the Cleo UI (Settings → API Keys). Cleo saves it to `~/.cleo/config.json`.

Option 2 — Create a `.env` file in the `cleo/` folder:

```bash
cp .env.example .env
# Then edit .env and add your key:
GEMINI_API_KEY=AIza...
```

---

## 💬 Asking Questions

Once CloudHealth is connected, type any question in the chat box.

### Select Your Tenant First

On first use (or after `/reset`), Cleo will show you a list of tenants/organizations from your CloudHealth account. **Type the number** next to the tenant you want to query.

```
👋 Welcome to Cleo!

1. Example Enterprise  `crn:1001:organization/100000000001`
2. Production Workloads  `crn:1001:organization/100000000002`

Type a number to select:
```

### Example Questions

**Cost summaries:**
- *"What were my top 10 cloud services by cost last month?"*
- *"Show me total AWS spend for the last 3 months."*
- *"Compare August spend to July — what changed?"*

**By service or region:**
- *"How much did we spend on EC2 in us-east-1 this month?"*
- *"What is our Azure Virtual Machines spend trend?"*
- *"Break down GCP costs by service."*

**Forecasting:**
- *"What is our projected spend for September based on current usage?"*
- *"Compare last month's cost to this month's forecast."*

**Multi-cloud:**
- *"Show me AWS vs Azure spend side by side for Q3."*
- *"Which cloud provider cost us the most in 2026?"*

**Channel customers (for partners/resellers):**
- *"Which channel customers spent the most last month?"*
- *"List all active channel customers."*

### Special Commands

| Command | What it does |
|---|---|
| `/reset` | Switch to a different tenant / organization |

---

## 🐳 Optional: Docker Installation (Windows / Linux / Servers)

Running via standard Python (`python3 cleo_server.py`) is the default and recommended method. If you prefer running Cleo inside a container without setting up a local Python environment:

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed and running

### Start with Docker

```bash
git clone https://github.com/yourorg/cleo.git
cd cleo
docker compose up
```

Docker will:
1. Pull the Ollama container (local LLM server)
2. Download the Qwen2.5-7B model (~4 GB, one-time)
3. Start the Cleo web server

Then open: **http://localhost:8080**

### Stop Docker

```bash
docker compose down
```

---

## 🔄 Auto-Updates

Cleo checks GitHub automatically every time it starts. If a newer version is available, a **yellow banner** appears at the top of the web UI:

```
🚀 Cleo update available (a1b2c3d → e4f5g6h). Update available — click 'Update' to pull the latest code.
[ Update now ]  [ Dismiss ]
```

Click **Update now** — Cleo runs `git pull` in the background and tells you to restart.

```
✅ Update applied. Restart Cleo (Ctrl+C → python3 cleo_server.py) to load the new version.
```

Then restart:
```bash
# Press Ctrl+C to stop Cleo, then:
python3 cleo_server.py
```

> **Note:** The update banner only appears if Cleo was installed via `git clone`. If you downloaded a ZIP, pull the latest ZIP from GitHub instead.

To manually check for updates via the API:
```bash
curl http://localhost:8080/api/update/check
```



```
cleo/
├── cleo_server.py       ← Main entry point. Run this to start Cleo.
├── cleo_agent.py        ← AI brain: connects CloudHealth, calls AI, handles tools
├── cleo_ui.html         ← The web chat interface
├── cleo_logger.py       ← Logging system
├── cleo_memory.py       ← Conversation memory across sessions
├── cleo_finops_refs.py  ← FinOps knowledge base (cost optimization rules)
├── auth/
│   └── oauth.py         ← CloudHealth OAuth 2.0 authentication handler
├── workflows/
│   └── cleo-v2.json     ← n8n workflow (for n8n users — optional)
├── credentials.example.json  ← Template for n8n credentials
├── .env.example         ← Template for API keys
├── requirements.txt     ← Python package dependencies
├── Dockerfile           ← For Docker deployment
└── docker-compose.yml   ← Docker stack (Cleo + Ollama)
```

---

## ⚙️ Configuration Reference

### Files Cleo Creates (in `~/.cleo/`)

| File | Purpose | Safe to delete? |
|---|---|---|
| `oauth_tokens.json` | CloudHealth login tokens | Yes — you'll need to re-authenticate |
| `config.json` | AI engine API keys, preferences | Yes — you'll need to re-enter API keys |
| `sessions.json` | Chat history | Yes — history will be cleared |
| `pkce_verifier.json` | Temporary OAuth state | Yes — auto-recreated |

### Environment Variables

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY` | OpenAI API key |
| `ANTHROPIC_API_KEY` | Anthropic Claude API key |
| `GEMINI_API_KEY` | Google Gemini API key |
| `CLEO_VERBOSE=1` | Enable detailed debug logging |

---

## 🔁 Optional: Agentic Workflow & Chat Integrations (n8n, Teams, Slack, Google Chat)

For enterprise automation, Cleo includes an optional pre-built **n8n workflow** (`workflows/cleo-v2.json`) that enables agentic FinOps pipelines. This allows you to integrate Cleo directly into your team collaboration platforms:
- **Microsoft Teams** (interactive query bots, periodic summaries)
- **Slack** (slash commands, FinOps alerts to `#cloud-costs`)
- **Google Chat** (webhook bots, automated reports)
- **Scheduled Anomaly Monitoring** (periodic spend digests & threshold alerts)

### Prerequisites (if using n8n)

- [n8n](https://n8n.io) running locally or in the cloud
- Cleo server running (`python3 cleo_server.py`)

### Setup

1. In n8n, go to **Settings → Credentials** and import `credentials.example.json`.
   - Update the API URLs to match your mlx-lm ports (default: `1234` for 7B, `1235` for 3B).

2. Go to **Workflows → Import** and import `workflows/cleo-v2.json`.

3. Activate the workflow.

4. Send a message to the Cleo Chat trigger. n8n will route it through the classifier → fast or deep agent → response.

### How It Works

```
User Message
    ↓
Classify: Simple or Complex?
    ↓                    ↓
Fast Agent           Deep Agent
(Qwen2.5-3B)       (Qwen2.5-7B)
    ↓                    ↓
CloudHealth MCP    CloudHealth MCP
    ↓                    ↓
        Answer
```

> **Note:** Cleo server must be running at `http://127.0.0.1:8080` for n8n to connect.

---

## 🛠️ Troubleshooting

### "Port already in use" — automatic fallback

Cleo tries to start on port **8080** by default. If that port is already taken by another program, Cleo **automatically tries** the next free port from this list:

```
8080 → 8081 → 8082 → 8083 → 8090 → 8888 → 9090 → 9191
```

You'll see which port was chosen at startup:

```
⚠️  Port 8080 is in use. Starting Cleo on port 8081 instead.
🌐 Cleo Web UI → http://127.0.0.1:8081
```

To force a specific port:
```bash
PORT=9090 python3 cleo_server.py
```

### CloudHealth says "Unauthorized" or "Not connected"
- Click **Connect CloudHealth** in the UI and go through the login again.
- Make sure you are logging in with a CloudHealth account that has **MCP access enabled**. Contact your CloudHealth admin if unsure.

### "No module named..." error
The automatic setup failed. Try manually:
```bash
python3 -m venv .venv
source .venv/bin/activate    # Mac/Linux
.venv\Scripts\activate       # Windows
pip install -r requirements.txt
python3 cleo_server.py
```

### Local model is slow or not loading
- Check you have at least **5 GB free RAM** for Qwen2.5-7B, or **2 GB** for Qwen2.5-3B.
- On Apple Silicon, make sure you're running native Python (not Rosetta). Check with `python3 -c "import platform; print(platform.machine())"` — it should say `arm64`.
- Switch to a cloud AI engine (Gemini/OpenAI) while the model downloads.

### Questions return "$0" or "No data"
- Make sure you selected a tenant after connecting (type the number shown in the welcome message).
- Type `/reset` to re-select a tenant.
- Check that CloudHealth MCP is **connected** (green pill in the top right).

---

## 🔒 Security

- Cleo runs entirely on your local machine — no data is sent to any third party except:
  - **CloudHealth** (to fetch your billing data)
  - **Your chosen AI engine** (if you use a cloud AI like Gemini/OpenAI/Claude)
- All credentials are stored in `~/.cleo/` on your machine with `600` file permissions (only your user can read them).
- No API keys or tokens are ever committed to this repository.

---

## 📞 Support

- **Issues:** Open a GitHub issue with your error message and OS version.
- **Logs:** Run with `CLEO_VERBOSE=1 python3 cleo_server.py` and share the terminal output.
