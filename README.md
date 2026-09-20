# Cleo — CloudHealth FinOps AI Agent

A standalone terminal AI agent that talks directly to CloudHealth via MCP to answer
cloud cost and billing questions. No external tooling required — just Python and a
CloudHealth account.

```
═══════════════════════════════════════════════════
  🤖  Cleo — CloudHealth FinOps Agent
═══════════════════════════════════════════════════

✅  CloudHealth token ready.

Select your AI Engine:
  1. Local Model (Qwen 32B via MLX) — free, runs on your Mac
  2. OpenAI  (GPT-4o)
  3. Anthropic (Claude Sonnet)
  4. Google   (Gemini 1.5 Pro)

🔌  Connecting to CloudHealth MCP...
✅  mcp-server v0.0.1 (protocol 2025-11-25)

🛠️   5 tools: list_channel_customers, list_managed_orgs, execute_datasource_query, ...

👤  You: who are the top 3 channel customers by spend last month?
🧠  Thinking...
🛠️   execute_datasource_query(...)
🤖  Cleo:
| # | Customer | Spend (Aug 2026) |
|---|----------|-----------------|
| 1 | Acme Corp | $142,300 |
...
```

---

## Requirements

- Python 3.11+
- A CloudHealth account with MCP access
- (Optional) Apple Silicon Mac for the local model

## Installation

```bash
git clone https://github.com/yourorg/cleo
cd cleo
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

For the local model (Apple Silicon only):
```bash
pip install mlx-lm
```

## First-Time Setup

### 1. CloudHealth Authentication

On first run, Cleo opens your browser to the CloudHealth login page.
Log in and click **Approve**. An authorization code will be displayed —
copy it and paste it back into the terminal.

> **Note**: The callback page shows "Google Antigravity" branding because
> CloudHealth's MCP service uses a shared public OAuth client. This is
> expected and safe — it's simply the page where CloudHealth delivers the
> authorization code.

Your token is saved to `~/.cleo/oauth_tokens.json` and automatically
refreshed on every subsequent run. You will not need to log in again unless
your refresh token expires (typically months).

### 2. AI Engine Setup

| Engine | What you need |
|--------|--------------|
| Local MLX | Apple Silicon Mac, `pip install mlx-lm`, ~20 GB free RAM |
| OpenAI GPT-4o | `OPENAI_API_KEY` environment variable (or enter when prompted) |
| Anthropic Claude | `ANTHROPIC_API_KEY` (or enter when prompted) |
| Google Gemini | `GEMINI_API_KEY` (or enter when prompted) |

API keys are saved to `~/.cleo/config.json` after first entry. You can also
put them in a `.env` file — see `.env.example`.

## Running

```bash
python cleo_agent.py
```

That's it. Cleo handles auth, model loading, and MCP connection automatically.

## Example Questions

- *"What were my top 5 cloud services by cost last month?"*
- *"Which channel customers spent the most in August 2026?"*
- *"Show me a breakdown of AWS vs Azure spend for Q3."*
- *"List all active channel customers."*
- *"What are the managed orgs in my account?"*

## Config Files

| File | Purpose |
|------|---------|
| `~/.cleo/oauth_tokens.json` | CloudHealth OAuth token (auto-managed) |
| `~/.cleo/config.json` | AI engine API keys (saved on first entry) |

To re-authenticate with CloudHealth, delete `~/.cleo/oauth_tokens.json` and run again.

## Architecture

```
cleo_agent.py
│
├── Auth         OAuth 2.0 PKCE → ~/.cleo/oauth_tokens.json
├── MCPClient    Streamable HTTP (POST /mcp, JSON-RPC 2.0)
├── AIClient     MLX | OpenAI | Anthropic | Gemini
└── Agent Loop   Tool-calling loop (up to 10 iterations)
```

**MCP Transport**: CloudHealth uses the Streamable HTTP MCP transport
(MCP spec 2025-03-26). All tool calls are plain `POST /mcp` JSON-RPC requests.
