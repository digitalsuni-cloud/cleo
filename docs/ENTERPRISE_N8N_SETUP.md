# 🏢 Cleo Enterprise Integration Guide (Fina Architecture)

Production-grade deployment blueprint for integrating Cleo into enterprise collaboration platforms (**Slack, Microsoft Teams, Google Chat**) using **n8n** and **CloudHealth MCP**, modeled directly after the **Fina** FinOps architecture.

---

## 🏛️ Architecture Overview

```
Slack / Teams Message (@cleo in channel or DM)
        ↓  (HTTPS Webhook via Cloud Armor / WAF)
n8n Ingress Trigger  (Verifies X-Slack-Signature)
        ↓
[1] Immediate Acknowledgment  ──→  Posts ":robot_face: Cleo is analyzing... :loading:" in thread
        ↓                           (Beats Slack's 3-second webhook timeout)
[2] Thread-Isolated Session   ──→  Keyed on thread_ts (Prevents multi-user collision)
        ↓
[3] Query Classifier          ──→  Fast Lookup vs. Deep Reasoning
        ↓
[4] CloudHealth MCP Engine    ──→  Calls http://cleo:8080/mcp over internal VPC
        ↓
[5] In-Place Message Update   ──→  Replaces placeholder with live FinOps table & charts
```

---

## 🔑 Key Enterprise Patterns Ported from Fina

### 1. Two-Step In-Place Slack Reply Pattern
* **The Problem:** Slack requires an HTTP `200 OK` or acknowledgement within **3 seconds**. If a multi-cloud query, variance calculation, or LLM synthesis takes 5–15 seconds, Slack triggers retry storms or shows an ugly *"operation timed out"* error.
* **The Fina Solution:**
  1. The workflow triggers on `app_mention`.
  2. Node `Initial Reply` immediately replies in the active thread:
     `":robot_face: Cleo is analyzing CloudHealth data... :loading:"` (completes in < 400 ms).
  3. The agent executes tools and synthesizes the answer.
  4. The final node calls `chat.update` (`Update Slack Placeholder in-place`) to swap the placeholder message with the final formatted Markdown report in-place. Zero channel clutter.

### 2. Thread-Isolated Session Concurrency
* In a shared channel (e.g. `#cloud-finops` with 200 engineers), multiple teammates will interact with `@cleo` simultaneously.
* In the enterprise workflow ([`workflows/cleo-enterprise.json`](../workflows/cleo-enterprise.json)), the session key is strictly scoped to the message thread:
  ```javascript
  sessionId = $node["Slack Trigger (@cleo)"].json["thread_ts"] || $node["Slack Trigger (@cleo)"].json["ts"];
  ```
* **Result:** Engineer A asking about AWS EC2 and Engineer B asking about Azure SQL in separate threads will **never** collide, overwrite each other's session state, or cross-contaminate memory.

### 3. Isolated Internal MCP Networking
* Cleo's FastAPI server (`cleo`) is **never exposed to the public internet**.
* `cleo` and `n8n` run on the same private Docker network (`cleo-enterprise-net`) or Kubernetes VPC.
* n8n connects to Cleo internally via:
  ```
  http://cleo:8080/mcp
  ```
* Inbound traffic from Slack is received exclusively through n8n's public webhook endpoint.

---

## 🚀 Quick Deployment: Production Docker Stack

### Step 1: Prepare Environment Configuration

```bash
cp .env.enterprise.example .env.enterprise
```

Edit `.env.enterprise` and set your production values:

```bash
# 1. Domain & Ingress
WEBHOOK_URL=https://cleo-bot.yourcompany.com
N8N_ENCRYPTION_KEY=your-32-byte-hex-encryption-key

# 2. Database (PostgreSQL)
POSTGRES_USER=n8n_admin
POSTGRES_PASSWORD=your-secure-postgres-password
POSTGRES_DB=n8n

# 3. FinOps AI Engine
AI_ENGINE=gemini
GEMINI_API_KEY=AIzaSy...

# 4. Global Enterprise Tenant (Optional)
# Pre-connects all channel queries to your master CloudHealth org:
CLEO_DEFAULT_ORG_ID=crn:1001:organization/100000000001
CLEO_DEFAULT_ORG_NAME=Master Production Org
```

### Step 2: Mount Enterprise CloudHealth Credentials

Create the secrets directory and copy your authenticated `oauth_tokens.json`:

```bash
mkdir -p secrets
cp ~/.cleo/oauth_tokens.json secrets/oauth_tokens.json
chmod 600 secrets/oauth_tokens.json
```

### Step 3: Launch the Stack

```bash
docker compose -f docker-compose.enterprise.yml --env-file .env.enterprise up -d
```

Verify services are healthy:
```bash
docker compose -f docker-compose.enterprise.yml ps
```

---

## 💬 Slack App Configuration (Step-by-Step)

### 1. Create Slack App
1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From scratch**.
2. Name: `cleo` (or `Cleo FinOps`).
3. Select your enterprise workspace.

### 2. Configure OAuth Scopes
Navigate to **OAuth & Permissions → Scopes → Bot Token Scopes**:
* `app_mentions:read` — Receive mentions when employees tag `@cleo`.
* `chat:write` — Post and update messages in channels.
* `channels:history` — Read message context in public channels.
* `groups:history` — Read message context in private channels.
* `im:history` — Read direct message history (for private DMs).
* `im:write` — Send direct messages.

### 3. Install App to Workspace
1. Click **Install to Workspace** (or request approval from Slack admins).
2. Copy the **Bot User OAuth Token** (`xoxb-...`).
3. Go to **Basic Information** and copy the **Signing Secret**.

### 4. Configure Credentials in n8n
1. Open n8n (`https://cleo-bot.yourcompany.com`).
2. Go to **Settings → Credentials → + Add Credential**.
3. Search for **Slack API**:
   * **Access Token:** Paste `xoxb-...`
   * Name: `Cleo Slack API`
4. Click **Save**.

### 5. Enable Event Subscriptions
1. In n8n, open and **Activate** `workflows/cleo-enterprise.json`.
2. Click the `Slack Trigger (@cleo)` node and copy the **Production Webhook URL**:
   `https://cleo-bot.yourcompany.com/webhook/cleo-slack/webhook`
3. In Slack App settings → **Event Subscriptions**:
   * Toggle **Enable Events: On**.
   * **Request URL:** Paste your production webhook URL. Slack will automatically verify the challenge.
   * Under **Subscribe to bot events**, add:
     - `app_mention`
     - `message.im`
4. Click **Save Changes**.

---

## 🛡️ Network Security & Cloud Armor / WAF Rules

If deploying behind Google Cloud Load Balancer (Cloud Armor) or AWS WAF, follow Fina's ingress policy:

| Priority | Match Condition | Action | Rationale |
|---|---|---|---|
| **100** | Path matches `/webhook/*` | **ALLOW** | Slack sends webhooks from dynamic IP pools; security is enforced by verifying `X-Slack-Signature` in n8n. |
| **1000** | Corporate IP / VPN / Zscaler | **ALLOW** | Restricts n8n Web UI and workflow editor access to corporate employees only. |
| **Default** | All other traffic | **DENY** | Blocks unauthorized internet scans. |

---

## 🧪 Testing in Slack

1. Invite Cleo to your FinOps channel:
   ```
   /invite @cleo
   ```
2. Ask a question in the channel:
   > `@cleo What were our top 5 cloud services by cost last month?`
3. **Expected Behavior:**
   - Cleo immediately replies in-thread: `:robot_face: Cleo is analyzing CloudHealth data... :loading:` (< 500 ms).
   - 4 seconds later, the placeholder message updates in-place with the live Markdown table and dollar figures.
   - Any follow-up questions asked in the thread maintain conversation memory without polluting the rest of the channel.
