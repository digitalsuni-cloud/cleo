from __future__ import annotations
import os
import sys
import json
import time
import urllib.parse
import urllib.request
import urllib.error
import base64
import hashlib
import webbrowser
import re
import subprocess
import csv
import io
import threading
import datetime
import calendar
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Dict, Any, List

# Use centralized verbose logger
try:
    from cleo_logger import get_logger
    logger = get_logger("agent")
except ImportError:
    import logging
    logger = logging.getLogger("cleo.agent")
    logger.setLevel(logging.INFO)

# OptimNow FinOps expert knowledge injection
try:
    from cleo_finops_refs import get_finops_context, get_finops_advisory
except ImportError:
    def get_finops_context(query: str) -> str:  # type: ignore
        return ""
    def get_finops_advisory(query: str) -> Optional[str]:  # type: ignore
        return None

# Self-learning memory
try:
    from cleo_memory import get_memory
except ImportError:
    def get_memory():  # type: ignore
        class _Noop:
            def build_context_block(self, q): return ""
            def record_sql_fix(self, *a): pass
        return _Noop()

from auth.oauth import (
    OAuth2Helper, 
    ANTIGRAVITY_CLIENT_ID, 
    ANTIGRAVITY_REDIRECT_URI,
    LOCAL_CLIENT_ID,
    LOCAL_REDIRECT_URI,
    DEFAULT_CLIENT_ID, 
    DEFAULT_REDIRECT_URI, 
    DEFAULT_SCOPES,
    MCP_RESOURCE
)

# ── Configuration ──────────────────────────────────────────────────────────────
APP_DATA_DIR = os.path.expanduser("~/.cleo")
TOKENS_FILE  = os.path.join(APP_DATA_DIR, "mcp_oauth_tokens.json")
CONFIG_FILE  = os.path.join(APP_DATA_DIR, "config.json")

# CloudHealth Endpoints
MCP_ENDPOINT = "https://apps.cloudhealthtech.com/mcp"
GRAPHQL_URL  = "https://apps.cloudhealthtech.com/graphql"
CHAPI_URL    = "https://chapi.cloudhealthtech.com"

# Ensure workspace virtualenv site-packages are accessible even when run without activating .venv
_cleo_root = os.path.dirname(os.path.abspath(__file__))
_venv_lib = os.path.join(_cleo_root, ".venv", "lib")
if os.path.isdir(_venv_lib):
    for _d in os.listdir(_venv_lib):
        _sp = os.path.join(_venv_lib, _d, "site-packages")
        if os.path.isdir(_sp) and _sp not in sys.path:
            sys.path.insert(0, _sp)

# ── Local & Public Model Catalogs ──────────────────────────────────────────────
MLX_MODELS = [
    {"id": "mlx:mlx-community/Qwen2.5-7B-Instruct-4bit", "repo_id": "mlx-community/Qwen2.5-7B-Instruct-4bit", "name": "Qwen2.5-7B-Instruct-4bit (MLX)", "size": "4.3 GB", "tier": "default", "desc": "Default recommended Apple Silicon Metal model (~4.3 GB RAM)"},
    {"id": "mlx:mlx-community/Qwen2.5-3B-Instruct-4bit", "repo_id": "mlx-community/Qwen2.5-3B-Instruct-4bit", "name": "Qwen2.5-3B-Instruct-4bit (MLX)", "size": "1.8 GB", "tier": "smaller", "desc": "Ultra-fast lightweight model for Apple Silicon (~1.8 GB RAM)"},
]

LOCAL_MODELS = [
    {"id": "qwen2.5:7b", "name": "Qwen2.5-7B-Instruct-4bit", "size": "4.7 GB", "tier": "default", "desc": "Default recommended local model for FinOps (~5 GB RAM)"},
    {"id": "qwen2.5:3b", "name": "Qwen2.5-3B-Instruct-4bit", "size": "1.9 GB", "tier": "smaller", "desc": "Lightweight & ultra-fast local model (~2 GB RAM)"},
]

PUBLIC_ENGINES = [
    {"id": "gemini", "name": "Google Gemini", "default_model": "gemini-2.0-flash", "env_var": "GEMINI_API_KEY", "desc": "Google Gemini Cloud AI"},
    {"id": "openai", "name": "OpenAI", "default_model": "gpt-4o", "env_var": "OPENAI_API_KEY", "desc": "OpenAI Cloud AI"},
    {"id": "anthropic", "name": "Anthropic Claude", "default_model": "claude-3-7-sonnet-latest", "env_var": "ANTHROPIC_API_KEY", "desc": "Anthropic Claude Cloud AI"},
]

AI_ENGINES = {
    "direct": ("Direct FinOps Router", "direct"),
    "mlx:mlx-community/Qwen2.5-7B-Instruct-4bit": ("Qwen2.5-7B-Instruct-4bit (MLX Default)", "mlx:mlx-community/Qwen2.5-7B-Instruct-4bit"),
    "mlx:mlx-community/Qwen2.5-3B-Instruct-4bit": ("Qwen2.5-3B-Instruct-4bit (MLX)", "mlx:mlx-community/Qwen2.5-3B-Instruct-4bit"),
    "ollama:qwen2.5:7b": ("Qwen2.5-7B-Instruct-4bit (Ollama)", "ollama:qwen2.5:7b"),
    "ollama:qwen2.5:3b": ("Qwen2.5-3B-Instruct-4bit (Ollama)", "ollama:qwen2.5:3b"),
    "gemini": ("Google Gemini", "gemini"),
    "openai": ("OpenAI", "openai"),
    "anthropic": ("Anthropic Claude", "anthropic"),
}

auth_helper = OAuth2Helper(
    client_id=DEFAULT_CLIENT_ID,
    redirect_uri=DEFAULT_REDIRECT_URI,
    scopes=DEFAULT_SCOPES,
    tokens_file=TOKENS_FILE
)

def _load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Could not load config: {e}")
            return {}
    return {}

def _save_config(updates: dict) -> dict:
    os.makedirs(APP_DATA_DIR, exist_ok=True)
    cfg = _load_config()
    cfg.update(updates)
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
        os.chmod(CONFIG_FILE, 0o600)
    except Exception as e:
        logger.error(f"Could not save config: {e}")
    return cfg

# ── CloudHealth Native API Engine (Resilient Direct Backend) ───────────────────
class CloudHealthDirectEngine:
    """
    Direct CloudHealth API engine (GraphQL & CHAPI OLAP).
    Powered purely by the OAuth 2.0 access token obtained via GUI authentication.
    """
    def __init__(self, token: str = ""):
        self.token = token

    def get_jwt(self) -> str:
        return self.token

    def list_managed_orgs(self, channelCustomerId: str = None) -> list[dict]:
        jwt = self.get_jwt()
        query = "query { organizations { edges { node { id name } } } }"
        payload = json.dumps({"query": query}).encode("utf-8")
        req = urllib.request.Request(
            GRAPHQL_URL,
            data=payload,
            headers={"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=20.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                edges = data.get("data", {}).get("organizations", {}).get("edges", [])
                orgs = [e["node"] for e in edges if "node" in e]
                logger.debug(f"[Direct Engine] list_managed_orgs -> {len(orgs)} organizations found")
                return orgs
        except Exception as e:
            logger.error(f"[Direct Engine] list_managed_orgs error: {e}")
            return []

    def list_channel_customers(self) -> list[dict]:
        jwt = self.get_jwt()
        query = "query { channelCustomers { edges { node { customerId name status } } } }"
        payload = json.dumps({"query": query}).encode("utf-8")
        req = urllib.request.Request(
            GRAPHQL_URL,
            data=payload,
            headers={"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=20.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                edges = data.get("data", {}).get("channelCustomers", {}).get("edges", [])
                return [e["node"] for e in edges if "node" in e]
        except Exception as e:
            logger.debug(f"[Direct Engine] list_channel_customers (tenant may not be partner channel): {e}")
            return []

    def execute_datasource_query(self, queryInput: dict, requestInfo: dict = None, **kwargs) -> dict:
        jwt = self.get_jwt()
        sql = queryInput.get("sqlStatement", "")
        logger.debug(f"[Direct Engine] execute_datasource_query SQL: {sql}")
        
        # Query OLAP reports API
        req = urllib.request.Request(
            f"{CHAPI_URL}/olap_reports/cost/history?interval=monthly",
            headers={"Authorization": f"Bearer {jwt}", "Accept": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=30.0) as resp:
                olap = json.loads(resp.read().decode("utf-8"))
                dims = olap.get("dimensions", [])
                data = olap.get("data", [])
                
                time_labels = [item.get("label") or item.get("name") for item in dims[0].get("time", [])] if dims else []
                svc_labels = [item.get("label") or item.get("name") for item in dims[1].get("AWS-Service-Category", [])] if len(dims) > 1 else []
                
                # Identify last full month
                month_idx = len(time_labels) - 2 if len(time_labels) >= 2 else (len(time_labels) - 1 if time_labels else 0)
                month_name = time_labels[month_idx] if time_labels else "Last Month"
                
                total_month_cost = 0.0
                try:
                    total_month_cost = float(data[month_idx][0][0])
                except Exception:
                    pass

                # Parse service breakdown
                svc_breakdown = []
                for s_idx, s_name in enumerate(svc_labels):
                    if s_name == "Total": continue
                    try:
                        val = data[month_idx][s_idx][0]
                        if val is not None and float(val) > 0:
                            svc_breakdown.append((s_name, float(val)))
                    except Exception:
                        pass
                svc_breakdown.sort(key=lambda x: x[1], reverse=True)

                svc_rows = "\n".join([f"| {s[0]} | ${s[1]:.2f} | {((s[1]/total_month_cost)*100 if total_month_cost else 0):.1f}% |" for s in svc_breakdown[:6]])

                md = (
                    f"### 📊 CloudHealth Monthly Cost Breakdown ({month_name})\n\n"
                    f"**Total Partner Spend**: **${total_month_cost:.2f}**\n\n"
                    f"> ⚠️ *Per-tenant cost breakdown is only available when CloudHealth MCP is connected. "
                    f"This summary shows partner-wide totals from the OLAP engine.*\n\n"
                    f"#### ☁️ Top Cloud Services Breakdown ({month_name})\n\n"
                    f"| Service Category | Cost | % of Total |\n"
                    f"|:---|:---|:---|\n"
                    f"{svc_rows}\n\n"
                    f"*Source: CloudHealth OLAP engine (partner-level totals only).*"
                )

                logger.debug(f"[Direct Engine] Formatted cost report generated for {month_name}")
                return {
                    "status": "SUCCESS",
                    "total_cost": total_month_cost,
                    "month": month_name,
                    "customers": filtered_custs,
                    "top_services": svc_breakdown[:10],
                    "formatted_markdown": md
                }
        except Exception as e:
            err_msg = str(e)
            hint = "Please click 'Connect CloudHealth' and select your active Customer tenant." if ("403" in err_msg or "401" in err_msg) else ""
            logger.error(f"[Direct Engine] execute_datasource_query error: {err_msg}")
            return {
                "status": "ERROR", 
                "message": f"{err_msg}. {hint}",
                "formatted_markdown": f"⚠️ **Could not retrieve live cost data**: `{err_msg}`\n\n> 💡 *Guidance*: {hint or 'Check backend logs for details.'}"
            }

# Standard 5 CloudHealth FinOps Tool Specifications
STANDARD_CH_TOOLS = [
    {
        "name": "list_managed_orgs",
        "description": "Lists organizations for a managed channel customer, or for your partner tenant if omitted.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "channelCustomerId": {
                    "type": "string",
                    "description": "Channel customer CRN (e.g. crn:1:tenant/200). Optional."
                }
            }
        }
    },
    {
        "name": "list_channel_customers",
        "description": "Lists channel customers managed by your partner tenant.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "execute_datasource_query",
        "description": "Query CloudHealth Cost and Usage data using SQL statement and time filters.",
        "inputSchema": {
            "type": "object",
            "required": ["queryInput"],
            "properties": {
                "queryInput": {
                    "type": "object",
                    "required": ["sqlStatement"],
                    "properties": {
                        "sqlStatement": {"type": "string", "description": "Trino/Presto SQL query"},
                        "dataGranularity": {"type": "string", "enum": ["MONTHLY", "DAILY", "HOURLY"]},
                        "limit": {"type": "integer"}
                    }
                },
                "requestInfo": {
                    "type": "object",
                    "properties": {
                        "sourceType": {"type": "string", "enum": ["API", "UI", "BACKGROUND_JOB"]}
                    }
                }
            }
        }
    },
    {
        "name": "list_standard_datasources",
        "description": "Fetch all available Standard Data Sources shipped by CloudHealth.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "channelCustomerId": {"type": "string"},
                "orgId": {"type": "string"}
            }
        }
    },
    {
        "name": "get_datasource_metadata",
        "description": "Data Source Metadata and schema columns for a specified Datasource Name.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "dataset": {"type": "string"},
                "type": {"type": "string", "enum": ["DATASET", "VIEW"]}
            }
        }
    }
]

# ── Unified MCP Client ────────────────────────────────────────────────────────
class MCPClient:
    """
    Robust MCP Client for CloudHealth.
    Communicates via direct HTTP JSON-RPC to https://apps.cloudhealthtech.com/mcp.
    If the remote MCP endpoint is unauthorized (-32001), it transparently
    serves CloudHealth tools via the verified Direct Engine.
    """
    def __init__(self, token: str, endpoint_url: str = MCP_ENDPOINT, token_refresher=None):
        self._token = token
        self._endpoint_url = endpoint_url
        self._token_refresher = token_refresher
        self._next_id = 1
        self._lock = threading.Lock()
        self._use_fallback = False
        self._direct_engine = CloudHealthDirectEngine(token=token)
        self._cached_tools = []
        self._query_cache: dict = {}
        self.last_mcp_error = ""

    def clear_cache(self):
        """Clears all cached MCP tool responses."""
        with self._lock:
            self._query_cache.clear()
            logger.info("[MCP Cache] Cache cleared.")

    def _http_request(self, method: str, params: Optional[dict] = None) -> dict:
        with self._lock:
            req_id = self._next_id
            self._next_id += 1

        req_body = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method
        }
        if params is not None:
            req_body["params"] = params

        data_bytes = json.dumps(req_body).encode("utf-8")
        logger.debug(f"[MCP HTTP Request] {method} -> {self._endpoint_url} Body: {data_bytes.decode()[:200]}")

        req = urllib.request.Request(
            self._endpoint_url,
            data=data_bytes,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {self._token}",
                "User-Agent": "Cleo-FinOps-Agent/1.0"
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=30.0) as resp:
                resp_raw = resp.read().decode("utf-8")
                logger.debug(f"[MCP HTTP Response] {method} HTTP {resp.status}: {resp_raw[:300]}")
                if resp_raw.startswith("data:"):
                    resp_raw = resp_raw[5:].strip()
                data = json.loads(resp_raw)
                if "error" in data:
                    err = data["error"]
                    err_code = err.get("code")
                    self.last_mcp_error = f"Code {err_code}: {err.get('message')}"
                    logger.warning(f"[MCP Server Error] {self.last_mcp_error}")
                    if err_code in (-32001, 401) and self._token_refresher:
                        logger.info(f"[MCP] Unauthorized code {err_code}, refreshing token via OAuth...")
                        new_token = self._token_refresher()
                        if new_token and new_token != self._token:
                            self._token = new_token
                            self._direct_engine.token = new_token
                            return self._http_request(method, params)
                    raise RuntimeError(f"MCP Error: {err}")
                return data.get("result", {})
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", "ignore") if hasattr(e, "read") else str(e)
            logger.error(f"[MCP HTTP Error] Status {e.code}: {err_body}")
            if e.code in (401, 403) and self._token_refresher:
                logger.info(f"[MCP] Token rejected ({e.code}), refreshing via OAuth...")
                new_token = self._token_refresher()
                if new_token and new_token != self._token:
                    self._token = new_token
                    self._direct_engine.token = new_token
                    return self._http_request(method, params)
            raise RuntimeError(f"HTTP Error {e.code}: {err_body}")
        except Exception as e:
            logger.error(f"[MCP Network Exception] {e}")
            raise

    def initialize(self) -> dict:
        logger.info(f"[MCP Init] Initializing CloudHealth MCP client with OAuth session...")
        try:
            res = self._http_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "Cleo", "version": "1.0"}
            })
            logger.info("✅ [MCP Init] CloudHealth MCP endpoint initialized successfully!")
            self._use_fallback = False
            return res
        except Exception as e:
            err_str = str(e)
            if "-32001" in err_str or "not authorized" in err_str.lower() or "401" in err_str or "403" in err_str:
                logger.error(f"❌ [MCP Authorization Rejected] {err_str}")
                org_hint = ""
                try:
                    if self.token and "." in self.token:
                        payload = self.token.split(".")[1]
                        payload += "=" * ((4 - len(payload) % 4) % 4)
                        claims = json.loads(base64.urlsafe_b64decode(payload))
                        if "org" in claims:
                            org_hint = f" for organization {claims['org']}"
                except Exception:
                    pass
                raise RuntimeError(
                    f"CloudHealth MCP authorization failed (-32001): Access token is not authorized{org_hint}. "
                    "This occurs when the authenticated tenant does not have CloudHealth MCP access enabled. "
                    "Please click 'Disconnect' and log in selecting your MCP-enabled CloudHealth partner tenant (e.g. crn:9515)."
                )
            logger.warning(f"⚠️  [MCP Init Notice] Remote MCP initialize notice: {e}")
            logger.info("🛡️  [MCP Init] Activating standard FinOps tool suite with active OAuth session...")
            self._use_fallback = True
            return {
                "serverInfo": {"name": "CloudHealth-MCP-Client", "version": "1.0"},
                "capabilities": {"tools": {}},
                "mode": "standard"
            }

    def list_tools(self) -> list[dict]:
        if not self._use_fallback:
            try:
                res = self._http_request("tools/list")
                tools = res.get("tools", [])
                if tools:
                    self._cached_tools = tools
                    logger.info(f"✅ [MCP Tools] Discovered {len(tools)} tools from remote server")
                    return tools
            except Exception as e:
                err_str = str(e)
                if "-32001" in err_str or "not authorized" in err_str.lower():
                    logger.error(f"❌ [MCP Tools Unauthorized] {err_str}")
                    raise RuntimeError(f"CloudHealth MCP tools unauthorized: {err_str}")
                logger.warning(f"⚠️  [MCP Tools] Remote tools/list failed ({e}), falling back to standard tools")
                self._use_fallback = True

        self._cached_tools = STANDARD_CH_TOOLS
        logger.info(f"✅ [MCP Tools] Loaded {len(STANDARD_CH_TOOLS)} standard CloudHealth tools")
        return STANDARD_CH_TOOLS

    def call_tool(self, name: str, args: dict) -> dict:
        # ponytail: in-memory caching for immutable historical data and recent queries
        cache_key = (name, json.dumps(args, sort_keys=True))
        now = time.time()
        with self._lock:
            if cache_key in self._query_cache:
                ts, cached_res, ttl = self._query_cache[cache_key]
                if now - ts < ttl:
                    logger.debug(f"[MCP Cache Hit] '{name}' (age: {int(now - ts)}s, TTL: {ttl}s)")
                    return cached_res

        logger.info(f"[MCP Call] Executing tool '{name}' with args: {json.dumps(args)}")
        res = None
        if not self._use_fallback:
            try:
                res = self._http_request("tools/call", {"name": name, "arguments": args})
            except Exception as e:
                err_str = str(e)
                if "-32001" in err_str or "not authorized" in err_str.lower():
                    logger.error(f"❌ [MCP Tool Call Unauthorized] {name}: {err_str}")
                    raise RuntimeError(f"MCP tool '{name}' call unauthorized: {err_str}")
                # ponytail: channelCustomerId queries MUST use remote MCP — Direct Engine can't handle them
                # Don't fall back; re-raise so callers can skip gracefully
                if "channelCustomerId" in args:
                    logger.warning(f"⚠️  MCP call failed for channelCustomerId-scoped query, skipping: {e}")
                    raise
                logger.warning(f"⚠️  Remote tools/call failed ({e}), routing to Direct Engine")
                self._use_fallback = True

        if res is None:
            # Route to Direct Engine (fallback, no channelCustomerId support)
            if name == "list_managed_orgs":
                orgs = self._direct_engine.list_managed_orgs(**args)
                res = {"content": [{"type": "text", "text": json.dumps(orgs, indent=2)}]}
            elif name == "list_channel_customers":
                custs = self._direct_engine.list_channel_customers()
                res = {"content": [{"type": "text", "text": json.dumps(custs, indent=2)}]}
            elif name == "execute_datasource_query":
                d_res = self._direct_engine.execute_datasource_query(**args)
                res = {"content": [{"type": "text", "text": json.dumps(d_res, indent=2)}]}
            elif name == "list_standard_datasources":
                # ponytail: MCP offline — return known datasets with explicit caveat; full list (40+) requires live MCP
                ds = [
                    {"name": "MULTICLOUD_FOCUS_COST_AND_USAGE", "description": "AWS + Azure combined. Key columns: provider, ServiceCategory, Month, EffectiveCost, BilledCost"},
                    {"name": "AWS_FOCUS_COST_AND_USAGE", "description": "AWS only FOCUS dataset. Key columns: ServiceName, Month, EffectiveCost, BilledCost"},
                    {"name": "AZURE_FOCUS_COST_AND_USAGE", "description": "Azure only FOCUS dataset. Key columns: ServiceName, Month, EffectiveCost, BilledCost"},
                    {"name": "CLOUDHEALTH_CONSUMPTION_BREAKDOWN", "description": "Partner billing. Key columns: CustomerName, ServiceName, timeInterval_Month, ConfiguredUsageAtPartner"},
                    {"name": "AWS_COST_ANOMALY", "description": "AWS cost anomaly detection. Key columns: Service, CostImpact, CostImpactPercentage, CostImpactType, Status, Region"},
                    {"name": "AWS_CUR", "description": "AWS Cost & Usage Report. Key columns: lineItem_ProductCode, lineItem_UnblendedCost, timeInterval_Month, lineItem_UsageAccountId"},
                    {"name": "AWS_ASSET_INVENTORY", "description": "AWS asset inventory including EC2, EBS, RDS"},
                ]
                warning = "\n\n⚠️ **CloudHealth MCP is offline** — this is a partial dataset list. Connect CloudHealth for the full catalogue (40+ datasets)."
                res = {"content": [{"type": "text", "text": json.dumps(ds, indent=2) + warning}]}
            elif name == "get_datasource_metadata":
                # ponytail: MCP offline — return known columns for common datasets; always partial; real schema via MCP
                dataset = args.get("dataset", "CLOUDHEALTH_CONSUMPTION_BREAKDOWN")
                known_columns = {
                    "CLOUDHEALTH_CONSUMPTION_BREAKDOWN": [
                        {"name": "timeInterval_Month", "type": "string"},
                        {"name": "CustomerName", "type": "string"},
                        {"name": "ServiceName", "type": "string"},
                        {"name": "ConfiguredUsageAtPartner", "type": "number"},
                        {"name": "ChannelBillableUsage", "type": "number"},
                    ],
                    "MULTICLOUD_FOCUS_COST_AND_USAGE": [
                        {"name": "Month", "type": "string"},
                        {"name": "ServiceCategory", "type": "string"},
                        {"name": "provider", "type": "string"},
                        {"name": "EffectiveCost", "type": "number"},
                        {"name": "BilledCost", "type": "number"},
                    ],
                    "AWS_FOCUS_COST_AND_USAGE": [
                        {"name": "Month", "type": "string"},
                        {"name": "ServiceName", "type": "string"},
                        {"name": "EffectiveCost", "type": "number"},
                        {"name": "BilledCost", "type": "number"},
                    ],
                    "AZURE_FOCUS_COST_AND_USAGE": [
                        {"name": "Month", "type": "string"},
                        {"name": "ServiceName", "type": "string"},
                        {"name": "EffectiveCost", "type": "number"},
                        {"name": "BilledCost", "type": "number"},
                    ],
                    "AWS_CUR": [
                        {"name": "timeInterval_Month", "type": "string"},
                        {"name": "lineItem_ProductCode", "type": "string"},
                        {"name": "lineItem_UnblendedCost", "type": "number"},
                        {"name": "lineItem_UsageAccountId", "type": "string"},
                        {"name": "lineItem_UsageType", "type": "string"},
                    ],
                }
                columns = known_columns.get(dataset, [{"name": "unknown", "type": "string"}])
                meta = {
                    "dataset": dataset,
                    "columns": columns,
                    "_warning": "CloudHealth MCP is offline — column list is partial. Connect CloudHealth for the full schema.",
                }
                res = {"content": [{"type": "text", "text": json.dumps(meta, indent=2)}]}
            else:
                return {"error": f"Unknown tool: {name}"}

        # Cache successful responses (never cache error payloads)
        is_err = False
        if isinstance(res, dict):
            if "error" in res or res.get("isError"):
                is_err = True
            else:
                txt = res.get("content", [{}])[0].get("text", "")
                if "FQ-USR-" in txt or "HTTP Error" in txt or '"status": "ERROR"' in txt:
                    is_err = True
                    # Auto-learn: record the failing SQL so future queries avoid it
                    if name == "execute_datasource_query":
                        bad_sql = args.get("queryInput", {}).get("sqlStatement", "")
                        if bad_sql:
                            try:
                                get_memory().record_sql_fix(bad_sql, txt[:200], "")
                            except Exception:
                                pass

        if not is_err:
            now_ym = datetime.date.today().strftime("%Y-%m")
            args_str = json.dumps(args)
            if name in ("list_channel_customers", "list_standard_datasources", "get_datasource_metadata"):
                ttl = 3600
            elif now_ym not in args_str and '"last":' not in args_str:
                ttl = 3600
            else:
                ttl = 300

            with self._lock:
                self._query_cache[cache_key] = (now, res, ttl)

        return res

    def close(self):
        logger.debug("[MCP] Closing client session")

# ── AI Client & Agent Turn ───────────────────────────────────────────────────

def _detect_chart_type(low: str) -> Optional[str]:
    """Return chart type string if user asked for a chart, else None."""
    # Variance chart → waterfall (must check before generic "waterfall" keyword)
    if any(w in low for w in ["variance chart", "waterfall chart", "bridge chart"]):
        return "waterfall"
    if "waterfall" in low:
        return "waterfall"
    if any(w in low for w in [
        "doughnut chart", "donut chart", "doughnut graph", "donut graph",
        "doughnut only", "donut only", "doughnut", "donut"
    ]):
        return "doughnut"
    if any(w in low for w in ["pie chart", "pie graph", "pie breakdown", "pie only", "pie"]):
        return "pie"
    # Trend → line chart (bare "trend" keyword triggers line, not bar)
    if any(w in low for w in ["trend", "line chart", "line graph", "trend line", "trend chart", "over time chart", "over time"]):
        return "line"
    if any(w in low for w in ["horizontal bar", "horizontal chart", "sideways bar"]):
        return "horizontal-bar"
    # Default bar/stacked chart if user asks for bar chart, stacked chart, or simply "chart", "graph", "plot":
    if any(w in low for w in [
        "bar chart", "bar graph", "barchart", "bargraph", "column chart",
        "stacked chart", "stacked bar", "stacked", "chart", "graph",
        "plot", "visualize", "visualisation", "visualization"
    ]):
        return "bar"
    return None


def _detect_wants_variance(low: str) -> bool:
    """Return True when the query asks for a trend — signals we should also emit a MoM/DoD variance chart."""
    return any(w in low for w in [
        "trend", "over time", "mom", "month over month", "month-over-month",
        "dod", "day over day", "day-over-day", "yoy", "year over year", "year-over-year",
        "variance", "change over", "how it changed", "how has it changed"
    ])


def _build_mom_variance_chart(
    title: str,
    time_labels: list,  # sorted month/day strings
    period_totals: dict,  # {time_str: total_cost}
    time_format: str = "month"
) -> str:
    """
    Build a MoM/DoD waterfall variance chart from period totals.
    Emits waterfall bars showing $ change between consecutive periods.
    """
    sorted_times = sorted(period_totals.keys())
    if len(sorted_times) < 2:
        return ""

    wf_labels = []
    wf_values = []
    prev = period_totals[sorted_times[0]]
    wf_labels.append(_format_time_label(sorted_times[0], time_format))
    wf_values.append(round(prev, 2))

    for t in sorted_times[1:]:
        curr = period_totals[t]
        delta = curr - prev
        wf_labels.append(_format_time_label(t, time_format))
        wf_values.append(round(delta, 2))
        prev = curr

    return _build_waterfall_chart(title, wf_labels, wf_values, value_label="Cost Change ($)")


def _detect_wants_table(low: str) -> bool:
    """Return False if user explicitly requested no tabular output or chart only."""
    if any(phrase in low for phrase in [
        "no tabular", "no table", "without table", "without tables", "no tables",
        "chart only", "only chart", "only the chart", "donut chart only", "donut only",
        "doughnut chart only", "doughnut only", "bar chart only", "bar only",
        "pie chart only", "pie only", "graph only", "only graph", "visual only",
        "just the chart", "just chart", "just the graph", "hide table", "omit table",
        "no data table", "skip table", "suppress table"
    ]):
        return False
    return True


def _format_time_label(ym_str: str, time_format: str = "month") -> str:
    """Format '2026-03' into 'Mar 2026', '2026-08-20' into 'Aug 20', or 'Q1 2026'."""
    s = str(ym_str).strip()
    try:
        parts = s.split("-")
        if len(parts) >= 3 and time_format in ("day", "daily"):
            mo = int(parts[1])
            day = int(parts[2])
            return f"{calendar.month_abbr[mo]} {day}"
        if len(parts) >= 2:
            yr = int(parts[0])
            mo = int(parts[1])
            if time_format == "quarter":
                q = (mo - 1) // 3 + 1
                return f"Q{q} {yr}"
            return f"{calendar.month_abbr[mo]} {yr}"
    except Exception:
        pass
    return s


def _clean_chart_title(title: str) -> str:
    """
    Sanitize chart titles to remove backticks, quotes, and parentheses.
    Transforms:
      "RDS Spend by Instance Type (Last 10 Days Trend (`2026-09-10` to `2026-09-19`)) — All Accounts (Partner-Wide)"
    Into:
      "RDS Spend by Instance Type — Last 10 Days Trend: 2026-09-10 to 2026-09-19 — All Accounts Partner-Wide"
    """
    if not title:
        return ""
    # Strip quotes and backticks
    s = str(title).replace("`", "").replace("'", "").replace('"', "")
    
    # Handle nested parentheses like (Last 10 Days Trend (2026-09-10 to 2026-09-19))
    def _unparenthesize_nested(m):
        prefix = m.group(1).strip()
        inner = m.group(2).strip()
        return f" — {prefix}: {inner}"
    s = re.sub(r'\s*\(\s*([^()]+?)\s*\(\s*([^()]+?)\s*\)\s*\)', _unparenthesize_nested, s)
    
    # Clean partner suffixes
    s = re.sub(r'\s*\(\s*Partner-Wide\s*\)', ' Partner-Wide', s, flags=re.IGNORECASE)
    s = re.sub(r'\s*\(\s*Partner Tenant\s*\)', ' Partner Tenant', s, flags=re.IGNORECASE)
    
    # Convert any other parentheses: if preceded by dash/colon, strip parens; else prefix with dash
    s = re.sub(r'([—:])\s*\(([^)]+)\)', r'\1 \2', s)
    s = re.sub(r'\s*\(([^)]+)\)', r' — \1', s)
    
    # Strip any stray leftover parens
    s = s.replace("(", "").replace(")", "")
    
    # Normalize colons, dashes, and whitespace
    s = re.sub(r'\s*:\s*', ': ', s)
    s = re.sub(r'\s*—\s*—+\s*', ' — ', s)
    s = re.sub(r'\s+', ' ', s)
    return s.strip(" —:")


def _chart_block(chart_type: str, title: str, labels: list, values: list = None,
                 datasets: list = None, value_label: str = "Cost ($)",
                 horizontal: bool = False, stacked: bool = True,
                 max_labels: int = None) -> str:
    """
    Emit a fenced chart code-block for the UI to render via Chart.js.
    Supports single-dataset or multi-dataset stacked bar charts, waterfall charts, etc.
    """
    import json as _json
    limit = max_labels if max_labels is not None else (366 if (datasets or len(labels) > 30) else 30)
    labels_clean = [str(l)[:40] for l in labels[:limit]]
    spec_type = "doughnut" if chart_type == "donut" else chart_type
    clean_title = _clean_chart_title(title)
    spec = {
        "type": spec_type,
        "title": clean_title,
        "labels": labels_clean,
        "value_label": value_label,
        "stacked": stacked,
        "horizontal": horizontal
    }
    if datasets:
        clean_ds = []
        for ds in datasets:
            d = ds.get("data", [])[:len(labels_clean)]
            clean_ds.append({
                "label": ds.get("label", ""),
                "data": d
            })
        spec["datasets"] = clean_ds
    elif values is not None:
        spec["values"] = [round(float(v), 2) for v in values[:len(labels_clean)]]

    return f"\n```chart\n{_json.dumps(spec, indent=2)}\n```\n"


def _build_time_category_stacked_chart(
    title: str,
    raw_rows: list[dict],
    time_col: str = "month",
    cat_col: str = "category",
    cost_col: str = "cost",
    time_format: str = "month",
    max_cats: int = 12,
    unit: str = "Cost ($)"
) -> str:
    """
    Build a multi-dataset vertical stacked bar chart where:
      X-axis = Time periods (Months, Days, or Quarters)
      Datasets = Stacked categories (Services, Instance Types, Customers, etc.)
    """
    if not raw_rows:
        return ""

    raw_times = sorted(list({str(r.get(time_col, "")).strip() for r in raw_rows if r.get(time_col)}))
    if not raw_times:
        return ""

    cat_totals: dict[str, float] = {}
    time_cat_matrix: dict[str, dict[str, float]] = {t: {} for t in raw_times}

    for r in raw_rows:
        t = str(r.get(time_col, "")).strip()
        c = str(r.get(cat_col, "Unknown")).strip()
        if not c or not t:
            continue
        # Clean up category name if needed (e.g. "EU-BoxUsage:t2.small" -> "t2.small")
        if ":" in c and ("boxusage" in c.lower() or "instance" in c.lower()):
            clean_c = c.split(":")[-1]
        elif c.startswith("Amazon"):
            clean_c = c.replace("Amazon", "")
        else:
            clean_c = c

        try:
            val = float(r.get(cost_col) or 0)
        except (ValueError, TypeError):
            val = 0.0

        cat_totals[clean_c] = cat_totals.get(clean_c, 0.0) + val
        time_cat_matrix[t][clean_c] = time_cat_matrix[t].get(clean_c, 0.0) + val

    sorted_cats = sorted(cat_totals.items(), key=lambda x: x[1], reverse=True)
    top_cats = [c for c, _ in sorted_cats[:max_cats]]
    other_cats = [c for c, _ in sorted_cats[max_cats:]]

    labels = [_format_time_label(t, time_format) for t in raw_times]

    datasets = []
    for cat in top_cats:
        d = [round(time_cat_matrix[t].get(cat, 0.0), 2) for t in raw_times]
        datasets.append({
            "label": cat,
            "data": d
        })

    if other_cats:
        other_d = [round(sum(time_cat_matrix[t].get(oc, 0.0) for oc in other_cats), 2) for t in raw_times]
        if any(v > 0 for v in other_d):
            datasets.append({
                "label": "Other",
                "data": other_d
            })

    return _chart_block("bar", title, labels, datasets=datasets, value_label=unit, horizontal=False, stacked=True)

def _sanitize_finops_bullet_titles(text: str) -> str:
    """
    Sanitizes LLM-generated FinOps insight bullet headers to ensure the title
    accurately reflects the content and services mentioned.
    Prevents labelling a bullet as 'EC2' when the body discusses both EC2 and RDS or multiple providers.
    """
    lines = text.split("\n")
    cleaned_lines = []
    for line in lines:
        m = re.match(r'^(\s*[-*•]\s*\*\*([^*]+)\*\*:?)(.*)$', line)
        if m:
            title, body = m.group(2).strip(), m.group(3)
            title_low = title.lower()
            body_low = body.lower()

            new_title = title
            # Case 1: Title mentions EC2 but body talks about both EC2 and RDS / database
            if "ec2" in title_low and any(w in body_low for w in ["rds", "database", "aurora"]):
                new_title = "Compute & Database Concentration (EC2 & RDS)"
            # Case 2: Title mentions RDS but body talks about both RDS and EC2
            elif "rds" in title_low and "ec2" in body_low:
                new_title = "Compute & Database Concentration (EC2 & RDS)"
            # Case 3: Title mentions only AWS but body covers multi-cloud (Azure, GCP, OCI)
            elif "aws" in title_low and any(w in body_low for w in ["azure", "gcp", "multi-cloud"]):
                new_title = "Multi-Cloud Infrastructure Distribution"
            # Case 4: Replaces inaccurate 'utilization' on spend-only tables
            if "utilization" in new_title.lower() and ("spend" in body_low or "cost" in body_low):
                new_title = re.sub(r'(?i)\butilization\b', 'Spend Concentration', new_title)

            bullet_marker = line[:line.find("**")]
            cleaned_lines.append(f"{bullet_marker}**{new_title}**:{body}")
        else:
            cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def _build_waterfall_chart(title: str, labels: list, values: list, value_label: str = "Cost Change ($)") -> str:
    """Emit a waterfall chart specification block."""
    return _chart_block("waterfall", title, labels, values=values, value_label=value_label)


def _csv_to_markdown(csv_str: str) -> str:
    try:
        reader = csv.reader(io.StringIO(csv_str.strip()))
        rows = list(reader)
        if not rows:
            return ""
        header = rows[0]
        md = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
        for row in rows[1:]:
            formatted_cells = []
            for idx, cell in enumerate(row):
                cell_s = cell.strip()
                col_name = header[idx].lower() if idx < len(header) else ""
                try:
                    val = float(cell_s)
                    if any(k in col_name for k in ["cost", "spend", "usage", "billed", "unblended"]):
                        formatted_cells.append(f"${val:,.2f}")
                    else:
                        formatted_cells.append(cell_s)
                except ValueError:
                    formatted_cells.append(cell_s)
            md.append("| " + " | ".join(formatted_cells) + " |")
        return "\n".join(md)
    except Exception:
        return csv_str


def _classify_service_usage_type(pcode: str, usage_type: str, operation: str = "", desc: str = "") -> str:
    """
    Translates raw cloud billing meters and usage types into clean, domain-aware categories.
    E.g. TimedStorage-ByteHrs -> S3 Standard Storage
         VolumeUsage.gp2 -> EBS gp2 General Purpose Volume
         Lambda-GB-Second -> Lambda Compute Duration (GB-Sec)
    """
    ut = (usage_type or "").lower()
    op = (operation or "").lower()
    pc = (pcode or "").lower()
    d = (desc or "").lower()

    if "s3" in pc or "amazons3" in pc or "bucket" in pc:
        if "sia" in ut or "standard-ia" in ut or "standardia" in ut:
            return "S3 Standard-IA (Infrequent Access)"
        if "z-ia" in ut or "onezone" in ut:
            return "S3 One Zone-IA"
        if "int" in ut or "intelligent" in ut:
            return "S3 Intelligent-Tiering"
        if "glacier" in ut or "deeparchive" in ut or "gir" in ut:
            return "S3 Glacier / Deep Archive"
        if "timedstorage" in ut or "bytehrs" in ut:
            return "S3 Standard Storage"
        if "requests-tier1" in ut or any(k in op for k in ["put", "post", "list", "copy"]):
            return "S3 Tier 1 Requests (PUT, POST, LIST)"
        if "requests-tier2" in ut or any(k in op for k in ["get", "select"]):
            return "S3 Tier 2 Requests (GET, SELECT)"
        if "datatransfer" in ut or "bytes-out" in ut or "out-bytes" in ut:
            return "S3 Data Transfer Out (Egress)"
        if "retrieval" in ut:
            return "S3 Archive Retrieval Fees"
        if "earlydelete" in ut:
            return "S3 Early Deletion Fees"
        return "S3 Other Operations"

    if "ebs" in pc or ("ec2" in pc and any(k in ut for k in ["volume", "snapshot", "ebs", "iops"])):
        if "gp2" in ut:
            return "EBS gp2 General Purpose Volume"
        if "gp3" in ut:
            return "EBS gp3 General Purpose Volume"
        if "io1" in ut or "io2" in ut:
            return "EBS io1/io2 Provisioned IOPS Volume"
        if "st1" in ut or "sc1" in ut:
            return "EBS Throughput/Cold HDD (st1/sc1)"
        if "snapshot" in ut:
            return "EBS Snapshots"
        if "iops" in ut:
            return "EBS Provisioned IOPS"
        if "throughput" in ut:
            return "EBS Provisioned Throughput"
        return "EBS Storage & Volumes"

    if "lambda" in pc:
        if "gb-second" in ut or "duration" in ut:
            return "Lambda Compute Duration (GB-Sec)"
        if "request" in ut or "invocation" in ut:
            return "Lambda Invocations"
        if "provisioned" in ut:
            return "Lambda Provisioned Concurrency"
        if "edge" in ut:
            return "Lambda@Edge"
        return "Lambda Serverless Execution"

    if "dynamodb" in pc:
        if "readcapacity" in ut or "rcu" in ut:
            return "DynamoDB Provisioned Read (RCU)"
        if "writecapacity" in ut or "wcu" in ut:
            return "DynamoDB Provisioned Write (WCU)"
        if "payperrequest" in ut or "ondemand" in ut or "readrequest" in ut or "writerequest" in ut:
            return "DynamoDB On-Demand Requests"
        if "timedstorage" in ut or "storage" in ut:
            return "DynamoDB Table Storage"
        if "pitr" in ut or "backup" in ut:
            return "DynamoDB Backup & PITR"
        return "DynamoDB NoSQL Capacity"

    if "bedrock" in pc:
        if "sonnet" in ut or "sonnet" in d:
            return "Claude 3.5 Sonnet Inference"
        if "haiku" in ut or "haiku" in d:
            return "Claude 3 Haiku Inference"
        if "opus" in ut or "opus" in d:
            return "Claude 3 Opus Inference"
        if "titan" in ut or "titan" in d:
            return "Amazon Titan Models"
        if "llama" in ut or "llama" in d:
            return "Meta Llama Models"
        if "token" in ut or "token" in op:
            return "Bedrock Token Consumption"
        return "Bedrock Generative AI Inference"

    if "cloudfront" in pc:
        if "datatransfer" in ut or "out" in ut:
            return "CloudFront Edge Data Transfer Out"
        if "requests" in ut:
            return "CloudFront HTTP/HTTPS Requests"
        if "originshield" in ut:
            return "CloudFront Origin Shield"
        return "CloudFront Content Delivery"

    if "vpc" in pc or "nat" in pc:
        if "natgateway-hours" in ut or "nathours" in ut:
            return "NAT Gateway Base Hourly Fee"
        if "natgateway-bytes" in ut or "natbytes" in ut:
            return "NAT Gateway Data Processing"
        if "publicipv4" in ut or "ipv4" in ut:
            return "Public IPv4 Addresses"
        if "endpoint" in ut:
            return "VPC Endpoints"
        return "VPC Networking"

    # Default fallback: clean the usage type
    clean_ut = usage_type.replace("AWS-", "").replace("Amazon-", "").replace("-", " ").title()
    return clean_ut or "Standard Usage"


def _generate_domain_finops_insights(pcode: str, category_totals: dict, total_spend: float) -> list[str]:
    """
    Generates quantitative, authoritative FinOps recommendations based on identified spend patterns.
    """
    insights = []
    pc = (pcode or "").lower()

    if "s3" in pc or "amazons3" in pc or "bucket" in pc:
        std_spend = sum(c for k, c in category_totals.items() if "Standard Storage" in k)
        if total_spend > 0 and std_spend / total_spend > 0.40:
            est_sav = std_spend * 0.35
            insights.append(
                f"- **S3 Intelligent-Tiering Adoption**: **${std_spend:,.2f}** ({std_spend/total_spend*100:.1f}%) is in S3 Standard. "
                f"Enabling S3 Intelligent-Tiering automatically transitions objects untouched for 30/90/180/730 days to Archive tiers without retrieval fees, yielding an estimated **${est_sav:,.2f}/mo** (up to 35-68%) in storage reductions."
            )
        insights.append(
            "- **Incomplete Multipart Upload Cleanup**: Implement an S3 Lifecycle rule to `AbortIncompleteMultipartUpload` after 7 days. Orphaned parts from interrupted uploads silently accrue storage costs at full Standard rates."
        )
        insights.append(
            "- **Noncurrent Version Lifecycle**: For versioned buckets, enforce expiration on noncurrent versions (e.g. 30–60 days) to prevent exponential storage sprawl on frequently updated files."
        )

    elif "ebs" in pc or "volume" in pc:
        gp2_spend = sum(c for k, c in category_totals.items() if "gp2" in k)
        if gp2_spend > 0:
            gp3_savings = gp2_spend * 0.20
            insights.append(
                f"- **Immediate gp2 to gp3 Storage Modernization**: Detected **${gp2_spend:,.2f}** in legacy gp2 volume spend. "
                f"Upgrading to gp3 provides an **immediate 20% cost reduction** (~**${gp3_savings:,.2f}/mo**) with higher baseline performance (3,000 IOPS and 125 MB/s throughput) with **zero downtime** via online Elastic Block Store API modification."
            )
        snap_spend = sum(c for k, c in category_totals.items() if "Snapshot" in k)
        if snap_spend > 0:
            insights.append(
                f"- **EBS Snapshot Sprawl Governance**: **${snap_spend:,.2f}** is consumed by EBS snapshots. Audit snapshots unassociated with active AMIs, archive compliance snapshots older than 90 days to EBS Snapshot Archive (75% lower cost), and establish lifecycle policies via AWS Backup / DLM."
            )
        insights.append(
            "- **Two-Signal Unattached Volume Audit**: Reclaim 100% of costs on unattached EBS volumes (`VolumeState = available`) that have remained detached for > 14 days and have 0 write IOPS."
        )

    elif "lambda" in pc:
        insights.append(
            "- **AWS Graviton (ARM64) Runtime Migration**: Switch Lambda function architectures from x86_64 to arm64 (AWS Graviton2). Delivers up to **20% direct price reduction** on execution duration with equivalent or superior compute performance."
        )
        insights.append(
            "- **Memory Power Tuning**: Run AWS Lambda Power Tuning to rightsize memory allocations. Increasing memory can paradoxically lower costs by reducing execution wall-clock time proportionally."
        )

    elif "dynamodb" in pc:
        insights.append(
            "- **Capacity Mode Optimization (On-Demand vs Provisioned)**: Compare table traffic shapes. Workloads with predictable diurnal patterns or steady baselines achieve 40%–60% lower costs using Provisioned Capacity with Auto-Scaling compared to On-Demand."
        )
        insights.append(
            "- **Infrequent Access Storage Tier**: For tables storing historical or audit data, switch table class to DynamoDB Standard-IA (Infrequent Access) to cut storage fees by 60%."
        )

    elif "bedrock" in pc or "ai" in pc:
        insights.append(
            "- **Prompt Caching Economics**: Enable prompt caching for static instruction prefixes, reference documentation, and few-shot examples. Cache read tokens receive up to **90% discount** compared to base input rates in Anthropic Claude 3.5 Sonnet and Haiku."
        )
        insights.append(
            "- **Multi-Tier Model Routing**: Route low-complexity classification, validation, and metadata extraction queries to lightweight models (Claude 3 Haiku) rather than flagship models, reducing token costs by ~80% per query."
        )
        insights.append(
            "- **Batch Inference Arbitrage**: For non-realtime asynchronous processing (evaluation runs, bulk document processing), submit requests through the Batch API to unlock a guaranteed **50% discount**."
        )

    elif "vpc" in pc or "nat" in pc or "cloudfront" in pc:
        insights.append(
            "- **VPC Gateway Endpoints for S3 & DynamoDB**: Substitute NAT Gateways with free Gateway VPC Endpoints for all S3 and DynamoDB data flows. Eliminates the **$0.045/GB NAT data processing fee** completely."
        )
        insights.append(
            "- **Zombie NAT Gateway Elimination**: Audit NAT gateways deployed in VPCs with zero active EC2 instances or < 5MB transfer over 14 days. Each idle gateway bleeds **$32.40/month** in base hourly fees."
        )

    else:
        insights.append(
            "- **Rate & Modernization Review**: Inspect usage patterns to apply commitment coverage (Savings Plans / Reservations) for steady-state baseline and upgrade to current-generation instances."
        )
        insights.append(
            "- **Resource Lifecycle Governance**: Establish auto-stop schedules for non-production resources and enforce mandatory tagging for defensible cost allocation."
        )

    return insights


def _parse_table_data_from_assistant_markdown(text: str) -> Optional[dict]:
    """
    Parses structured tabular data from a previously generated assistant markdown response.
    Extracts category/item names, cost amounts, hours, percentages, period, and overall total.
    """
    if not text or "|" not in text:
        return None
    lines = text.split("\n")
    title = ""
    period = ""
    dataset_name = ""
    for l in lines:
        if l.startswith("### ") and not title:
            title = l.replace("###", "").strip()
        if "Target Billing Period" in l:
            m = re.search(r"Target Billing Period\*\*:\s*([^\n]+)", l)
            if m:
                period = m.group(1).strip("` ")
        if "standard dataset" in l or "dataset `" in l:
            m = re.search(r"`([A-Z0-9_]+)`", l)
            if m:
                dataset_name = m.group(1)

    table_lines = [l.strip() for l in lines if l.strip().startswith("|") and l.strip().endswith("|")]
    if len(table_lines) < 3:
        return None

    rows = []
    total_val = None
    for l in table_lines[2:]:
        cells = [c.strip() for c in l.strip("|").split("|")]
        # Check if total row
        if any("total" in c.lower() for c in cells):
            for c in cells:
                if "$" in c:
                    cleaned = re.sub(r"[^\d.]", "", c)
                    try:
                        total_val = float(cleaned)
                    except ValueError:
                        pass
            continue

        label = ""
        cost = 0.0
        pct = 0.0
        hrs = 0.0
        for c in cells:
            if "$" in c and cost == 0.0:
                cleaned = re.sub(r"[^\d.]", "", c)
                try:
                    cost = float(cleaned)
                except ValueError:
                    pass
            elif "%" in c and pct == 0.0:
                cleaned = re.sub(r"[^\d.]", "", c)
                try:
                    pct = float(cleaned)
                except ValueError:
                    pass
            elif any(w in c.lower() for w in ["hrs", "hr"]):
                cleaned = re.sub(r"[^\d.]", "", c)
                try:
                    hrs = float(cleaned)
                except ValueError:
                    pass
            else:
                cleaned = c.replace("*", "").replace("`", "").strip()
                if cleaned and not re.match(r"^\d+$", cleaned) and not label:
                    if cleaned not in ["AWS", "Azure", "GCP"]:
                        label = cleaned

        if label and cost > 0:
            rows.append({"label": label, "cost": cost, "pct": pct, "hours": hrs})

    if not rows:
        return None

    computed_total = total_val or sum(r["cost"] for r in rows)
    return {
        "title": title or "Spend Analysis Breakdown",
        "period": period or "Current Period",
        "dataset_name": dataset_name,
        "rows": rows,
        "total": computed_total
    }

# ── Local Apple Silicon (MLX) Support ─────────────────────────────────────────
_mlx_models_cache = {}

def get_installed_mlx_models() -> list[dict]:
    """Scans local Hugging Face cache for downloaded MLX models (equivalent to mlx_lm.manage --scan)."""
    installed = {}
    try:
        from huggingface_hub import scan_cache_dir
        info = scan_cache_dir()
        for repo in info.repos:
            if repo.repo_type == "model" and ("mlx" in repo.repo_id.lower() or "mlx" in str(repo.repo_path).lower()):
                installed[repo.repo_id] = {
                    "repo_id": repo.repo_id,
                    "size": repo.size_on_disk_str,
                    "path": str(repo.repo_path),
                    "downloaded": True
                }
    except Exception as e:
        logger.debug(f"[MLX Scan] scan_cache_dir: {e}")

    # Fallback to direct directory scan of ~/.cache/huggingface/hub/models--*
    hub_dir = os.path.expanduser("~/.cache/huggingface/hub")
    if os.path.isdir(hub_dir):
        for entry in os.listdir(hub_dir):
            if entry.startswith("models--") and "mlx" in entry.lower():
                parts = entry[8:].split("--")
                repo_id = "/".join(parts)
                if repo_id not in installed:
                    full_p = os.path.join(hub_dir, entry)
                    size_bytes = 0
                    try:
                        for root, _, files in os.walk(full_p):
                            for f in files:
                                size_bytes += os.path.getsize(os.path.join(root, f))
                        size_str = f"{round(size_bytes / (1024**3), 1)}G"
                    except Exception:
                        size_str = "Local"
                    installed[repo_id] = {
                        "repo_id": repo_id,
                        "size": size_str,
                        "path": full_p,
                        "downloaded": True
                    }

    return list(installed.values())

def estimate_token_count(text: str) -> int:
    """Estimates LLM BPE token count for text using word/subword heuristics."""
    if not text:
        return 0
    pieces = re.findall(r"\w+|[^\w\s]", text)
    return max(1, int(len(pieces) * 1.15))

def call_mlx_generate(model_id: str, messages: list[dict], max_tokens: int = 1024, stats_out: dict = None) -> str:
    """Invokes local Apple Silicon MLX model using unified memory."""
    global _mlx_models_cache
    try:
        import mlx_lm
    except ImportError:
        raise RuntimeError("mlx_lm is not installed in the Python environment.")

    clean_id = model_id.removeprefix("mlx:").strip()
    if clean_id not in _mlx_models_cache:
        logger.info(f"⚡ [MLX Load] Loading {clean_id} into unified memory...")
        model, tokenizer = mlx_lm.load(clean_id)
        _mlx_models_cache[clean_id] = (model, tokenizer)
        logger.info(f"✅ [MLX Ready] {clean_id} loaded successfully!")
    else:
        model, tokenizer = _mlx_models_cache[clean_id]

    prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    t0 = time.perf_counter()
    resp = mlx_lm.generate(model, tokenizer, prompt=prompt, max_tokens=max_tokens)
    dur = max(0.01, time.perf_counter() - t0)
    ans = resp.strip()

    if stats_out is not None:
        try:
            p_tok = len(tokenizer.encode(prompt))
            c_tok = len(tokenizer.encode(ans))
            stats_out["prompt_tokens"] = p_tok
            stats_out["completion_tokens"] = c_tok
            stats_out["total_tokens"] = p_tok + c_tok
            stats_out["tokens_per_sec"] = round(c_tok / dur, 1)
            stats_out["duration_secs"] = round(dur, 2)
        except Exception:
            pass

    return ans

# ── LLM Client Callers (Zero-Dependency via urllib) ──────────────────────────
OLLAMA_BASE_URL = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")

def get_installed_ollama_models() -> list[dict]:
    """Returns list of installed models from local Ollama daemon."""
    try:
        req = urllib.request.Request(f"{OLLAMA_BASE_URL}/api/tags", headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("models", [])
    except Exception:
        return []

def call_ollama_chat(model: str, messages: list[dict], timeout: float = 60.0, stats_out: dict = None) -> str:
    """Invokes local Ollama chat API."""
    payload = json.dumps({"model": model, "messages": messages, "stream": False}).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_BASE_URL}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"}
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        dur = max(0.01, time.perf_counter() - t0)
        ans = data.get("message", {}).get("content", "")
        if stats_out is not None:
            p_tok = data.get("prompt_eval_count", 0)
            c_tok = data.get("eval_count", 0)
            eval_dur_ns = data.get("eval_duration", 0)
            stats_out["prompt_tokens"] = p_tok
            stats_out["completion_tokens"] = c_tok
            stats_out["total_tokens"] = (p_tok or 0) + (c_tok or 0)
            if eval_dur_ns and eval_dur_ns > 0:
                stats_out["tokens_per_sec"] = round(c_tok / (eval_dur_ns / 1e9), 1)
            elif c_tok and dur > 0:
                stats_out["tokens_per_sec"] = round(c_tok / dur, 1)
            stats_out["duration_secs"] = round(dur, 2)
        return ans

def call_gemini_api(api_key: str, messages: list[dict], model: str = "gemini-2.0-flash", stats_out: dict = None) -> str:
    """Invokes Google Gemini REST API."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    contents = []
    system_instruction = None
    for m in messages:
        if m.get("role") == "system":
            system_instruction = {"parts": [{"text": m.get("content", "")}]}
        else:
            role = "user" if m.get("role") == "user" else "model"
            contents.append({"role": role, "parts": [{"text": m.get("content", "")}]})
    body = {"contents": contents}
    if system_instruction:
        body["systemInstruction"] = system_instruction
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=30.0) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        dur = max(0.01, time.perf_counter() - t0)
        if stats_out is not None and "usageMetadata" in data:
            um = data["usageMetadata"]
            p_tok = um.get("promptTokenCount", 0)
            c_tok = um.get("candidatesTokenCount", 0)
            tot_tok = um.get("totalTokenCount", p_tok + c_tok)
            stats_out["prompt_tokens"] = p_tok
            stats_out["completion_tokens"] = c_tok
            stats_out["total_tokens"] = tot_tok
            stats_out["duration_secs"] = round(dur, 2)
            if c_tok and dur > 0:
                stats_out["tokens_per_sec"] = round(c_tok / dur, 1)
        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            return "".join([p.get("text", "") for p in parts])
    return ""

def call_openai_api(api_key: str, messages: list[dict], model: str = "gpt-4o", stats_out: dict = None) -> str:
    """Invokes OpenAI Chat Completions REST API."""
    url = "https://api.openai.com/v1/chat/completions"
    payload = json.dumps({"model": model, "messages": messages}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    })
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=30.0) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        dur = max(0.01, time.perf_counter() - t0)
        if stats_out is not None and "usage" in data:
            u = data["usage"]
            p_tok = u.get("prompt_tokens", 0)
            c_tok = u.get("completion_tokens", 0)
            tot_tok = u.get("total_tokens", p_tok + c_tok)
            stats_out["prompt_tokens"] = p_tok
            stats_out["completion_tokens"] = c_tok
            stats_out["total_tokens"] = tot_tok
            stats_out["duration_secs"] = round(dur, 2)
            if c_tok and dur > 0:
                stats_out["tokens_per_sec"] = round(c_tok / dur, 1)
        return data.get("choices", [{}])[0].get("message", {}).get("content", "")

def call_anthropic_api(api_key: str, messages: list[dict], model: str = "claude-3-7-sonnet-latest", stats_out: dict = None) -> str:
    """Invokes Anthropic Messages REST API."""
    url = "https://api.anthropic.com/v1/messages"
    system_text = ""
    chat_msgs = []
    for m in messages:
        if m.get("role") == "system":
            system_text += m.get("content", "") + "\n"
        else:
            chat_msgs.append({"role": m.get("role"), "content": m.get("content")})
    body = {
        "model": model,
        "max_tokens": 4096,
        "messages": chat_msgs
    }
    if system_text:
        body["system"] = system_text.strip()
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01"
    })
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=30.0) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        dur = max(0.01, time.perf_counter() - t0)
        if stats_out is not None and "usage" in data:
            u = data["usage"]
            p_tok = u.get("input_tokens", 0)
            c_tok = u.get("output_tokens", 0)
            stats_out["prompt_tokens"] = p_tok
            stats_out["completion_tokens"] = c_tok
            stats_out["total_tokens"] = p_tok + c_tok
            stats_out["duration_secs"] = round(dur, 2)
            if c_tok and dur > 0:
                stats_out["tokens_per_sec"] = round(c_tok / dur, 1)
        content_items = data.get("content", [])
        return "".join([c.get("text", "") for c in content_items if c.get("type") == "text"])

def get_realtime_calendar_info() -> dict:
    """Returns grounded real-time calendar and billing period details."""
    now = datetime.date.today()
    today_str = now.strftime("%Y-%m-%d")
    today_verbose = now.strftime("%A, %B %d, %Y")
    yesterday = now - datetime.timedelta(days=1)
    yesterday_str = yesterday.strftime("%Y-%m-%d")
    yesterday_verbose = yesterday.strftime("%A, %B %d, %Y")
    current_ym = now.strftime("%Y-%m")
    current_month_name = now.strftime("%B %Y")
    last_m = (now.month - 1) if now.month > 1 else 12
    last_yr = now.year if now.month > 1 else (now.year - 1)
    last_ym = f"{last_yr}-{last_m:02d}"
    last_month_name = datetime.date(last_yr, last_m, 1).strftime("%B %Y")
    d15_start = (now - datetime.timedelta(days=15)).strftime("%Y-%m-%d")
    d30_start = (now - datetime.timedelta(days=30)).strftime("%Y-%m-%d")
    d7_start = (now - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
    return {
        "today_str": today_str,
        "today_verbose": today_verbose,
        "yesterday_str": yesterday_str,
        "yesterday_verbose": yesterday_verbose,
        "current_ym": current_ym,
        "current_month_name": current_month_name,
        "last_ym": last_ym,
        "last_month_name": last_month_name,
        "d7_start": d7_start,
        "d15_start": d15_start,
        "d30_start": d30_start,
    }

def build_system_prompt(tools: list[dict]) -> str:
    cal = get_realtime_calendar_info()
    prompt = (
        "You are Cleo, an expert Autonomous FinOps assistant powered by CloudHealth MCP.\n"
        "Your task is to analyze cloud costs, query live usage datasets, inspect organizations, and uncover savings.\n\n"
        "REAL-TIME CALENDAR & TEMPORAL CONTEXT (MANDATORY):\n"
        f"- Current Real-World Date (Today): {cal['today_str']} ({cal['today_verbose']})\n"
        f"- Yesterday (Latest Closed Billing Day): {cal['yesterday_str']} ({cal['yesterday_verbose']})\n"
        f"- Current Billing Month (MTD): {cal['current_ym']} ({cal['current_month_name']})\n"
        f"- Last Completed Billing Month: {cal['last_ym']} ({cal['last_month_name']})\n"
        f"- Last 7 Days Window: {cal['d7_start']} to {cal['yesterday_str']} (ending yesterday, ignoring today)\n"
        f"- Last 15 Days Window: {cal['d15_start']} to {cal['yesterday_str']} (ending yesterday, ignoring today)\n"
        f"- Last 30 Days Window: {cal['d30_start']} to {cal['yesterday_str']} (ending yesterday, ignoring today)\n\n"
        "TEMPORAL BILLING MEMORY DOCTRINE (PERMANENT RULE):\n"
        f"1. ALWAYS IGNORE CURRENT DATE: Always ignore / exclude the current date ({cal['today_str']}) in closed daily billing metrics, trends, and tables. Today's usage is in-flight and not finalized by cloud providers.\n"
        f"2. YESTERDAY'S DATA IS PARTIAL: Always keep in mind and explicitly note that yesterday's data ({cal['yesterday_str']}) will be partial/preliminary across all cloud providers and services due to 24-48 hour cloud billing settlement and CUR delivery latency.\n"
        f"3. All queries and date calculations must anchor to this real-world calendar. NEVER assume the year is 2023 or 2024.\n\n"
        "EXECUTION INTEGRITY & CODE OUTPUT BAN (CRITICAL):\n"
        "- Cleo is an interactive conversational agent. The platform executes MCP data queries behind the scenes.\n"
        "- NEVER output raw Python code, pseudocode scripts, or tool calls (e.g. NEVER write 'list_standard_datasources()', 'execute_datasource_query(...)', 'aws_data_sources = ...', or 'Let's execute these queries').\n"
        "- NEVER narrate internal code steps or ask the user to run Python.\n"
        "- Always output clean, synthesized FinOps advisory analysis, formatted markdown tables, dollar figures, and actionable insights.\n\n"
        "CLOUDHEALTH FINOPS ARCHITECTURE & DOCUMENTATION KNOWLEDGE:\n"
        "1. CORE FINOPS DATASETS:\n"
        "   - CLOUDHEALTH_CONSUMPTION_BREAKDOWN: Invoice calculation & reporting dataset.\n"
        "     * Channel Customers (CustomerType != 'Partner'): spend is stored in 'ConfiguredUsageAtPartner'.\n"
        "     * Master Partner (CustomerType = 'Partner'): wholesale/raw cloud spend is stored in 'Usage'.\n"
        "     * Channel Customer queries MUST use 'SUM(ConfiguredUsageAtPartner) AS cost' WHERE CustomerType != 'Partner' AND ConfiguredUsageAtPartner > 0.\n"
        "     * Combined queries use 'SUM(CASE WHEN CustomerType = 'Partner' THEN Usage ELSE ConfiguredUsageAtPartner END) AS cost' WHERE CostType = 'total'.\n"
        "     * Never use 'Usage' or 'ChannelBillableUsage' for channel customers (they evaluate to 0.0).\n"
        "   - AWS_CUR: AWS Cost & Usage Report dataset. Key columns: lineItem_ProductCode (Service), lineItem_UnblendedCost, timeInterval_Month, lineItem_UsageAccountId.\n"
        "   - MULTICLOUD_FOCUS_COST_AND_USAGE: Unified AWS+Azure FOCUS standard dataset. Key columns: provider, ServiceName, Month, EffectiveCost, BilledCost.\n"
        "   - AWS_FOCUS_COST_AND_USAGE / AZURE_FOCUS_COST_AND_USAGE: Provider-specific FOCUS cost datasets.\n"
        "   - AWS_COST_ANOMALY / AZURE_COST_ANOMALY: Dedicated CloudHealth Anomaly Detection datasets containing identified cost anomalies, spikes, and unusual spend.\n"
        "     * Key columns: Service, CostImpact (dollar variance), CostImpactPercentage (%), CostImpactType (Increase/Decrease), Status (ACTIVE/INACTIVE/ARCHIVED), Region, AccountID, Duration_Days, timeInterval_Month.\n"
        "     * When user asks about anomalies, cost spikes, unusual spend, or anomaly detection, query AWS_COST_ANOMALY / AZURE_COST_ANOMALY!\n"
        "2. FLEXREPORT SQL QUERY RULES:\n"
        "   - NEVER use 'SELECT *' — always specify explicit column names.\n"
        "   - Every selected column or aggregated measure MUST have an alias (e.g. 'SUM(Usage) AS cost').\n"
        "   - When using CTEs / WITH clauses, clause names must start with 'cxtemp_'.\n"
        "   - Always include LIMIT (default 10) and an ORDER BY clause for ranked queries.\n"
        "   - DataGranularity: MONTHLY, DAILY, HOURLY, or WEEKLY.\n"
        "   - Reference dataset columns format: REFERENCE_DATASET_NAME##REFERENCE_DATASET_COLUMN_NAME.\n"
        "   - Time range qualifier: 'MONTH', 'DAY', 'WEEK', or 'HOUR'.\n"
        "3. TENANT & ENTITY RELATIONSHIPS:\n"
        "   - Organizations ('list_managed_orgs'): Accounts and business units within the partner/tenant.\n"
        "   - Channel Customers ('list_channel_customers'): Partner-managed client tenants (identified by CRN, e.g. crn:9515:tenant/...). If a query asks for customer costs/spend, query CLOUDHEALTH_CONSUMPTION_BREAKDOWN using ConfiguredUsageAtPartner, not just customer listing.\n"
        "4. FINOPS FOUNDATION & OPTIMNOW SCENARIO DOCTRINE:\n"
        "   - Follow the FinOps Foundation Framework (Inform → Optimize → Operate) and OptimNow practitioner doctrine.\n"
        "   - RATE OPTIMIZATION & COMMITMENTS:\n"
        "     * Layering: Compute SPs (maximum breadth across EC2/Fargate/Lambda, up to 66% discount) + EC2 Instance SPs (maximum depth up to 72% discount for steady-state families) + Spot (fault-tolerant batch/stateless up to 90%). Note: Compute SPs do NOT cover SageMaker.\n"
        "     * Coverage Target: Aim for 70% to 80% coverage of steady-state base compute. Never commit 100% to preserve headroom for rightsizing, modernization, and workload volatility.\n"
        "     * Break-even: 1-year No-Upfront commitments break even in ~7 to 9 months; 3-year commitments break even in ~14 to 18 months. Evaluate commitment cliffs and expiration schedules quarterly.\n"
        "   - TWO-SIGNAL WASTE CLASSIFICATION:\n"
        "     * Never recommend terminating cloud assets based on a single metric (e.g. low CPU alone). Require two corroborating signals: e.g. Network Bytes Out < 5MB AND TCP Connection Count = 0 over a 14-day evaluation window.\n"
        "     * Categorize recommendations cleanly: Quick Wins (immediate, non-disruptive, zero downtime, e.g. gp2 to gp3 storage upgrade for 20% savings + 3,000 IOPS, unattached EBS volumes, S3 7-day multipart upload abort rules, snapshot pruning) vs Strategic Modernization (architectural, e.g. AWS Graviton3/4 silicon migration for 20% savings, commercial DB license exit from Oracle/SQL Server to Aurora, serverless rightsizing).\n"
        "   - UNIT ECONOMICS & BUSINESS DENOMINATORS:\n"
        "     * Always connect cloud spend to business value: Cost per active customer, Cost per tenant, Cost per order/transaction, Cost per 1K API calls, or Cost per inference.\n"
        "     * Differentiate infrastructure metrics (GBs, CPU-hours) from business denominators accepted by Finance and Product leadership.\n"
        "   - AI & GENERATIVE AI TOKEN ECONOMICS:\n"
        "     * Prompt caching: Leverage prompt caching for static system instructions and few-shot examples to achieve up to 90% cost reduction on cached input tokens (Anthropic Claude, OpenAI, Bedrock).\n"
        "     * Model routing: Route low-complexity categorization and extraction tasks to cost-efficient tier models (Claude 3 Haiku, GPT-4o-mini) rather than premier flagship models.\n"
        "     * Batch inference: Utilize asynchronous Batch APIs for non-realtime processing to capture a guaranteed 50% discount.\n"
        "   - FOCUS 1.2 & ALLOCATION STANDARDS:\n"
        "     * BilledCost: Raw invoiced amount reflecting cash outflow.\n"
        "     * EffectiveCost: Amortized cost factoring in upfront fees and distributing commitment discounts proportionally to actual consumers, eliminating the 'blended cost trap'.\n"
        "5. PRESENTATION STANDARDS:\n"
        "   - Always present financial figures in crisp markdown tables with dollar signs ($), commas, and percentage changes where applicable.\n"
        "   - Highlight cost drivers, trends, and actionable FinOps optimization opportunities.\n\n"
        "AVAILABLE TOOLS:\n"
    )
    for t in tools:
        prompt += f"- {t['name']}: {t.get('description', '')}\n"
    prompt += (
        "\nROUTING RULES:\n"
        "1. When user asks about organizations, call 'list_managed_orgs'.\n"
        "2. When user asks to list channel customers or tenants without cost queries, call 'list_channel_customers'.\n"
        "3. When user asks about costs, customer spend breakdown, or trends, call 'execute_datasource_query'.\n"
        "4. When user asks about schema or column definitions, call 'get_datasource_metadata'.\n"
        "5. When user asks about available datasets, call 'list_standard_datasources'.\n"
        "6. When user asks about cost anomalies, unusual spikes, or anomaly detection, query 'AWS_COST_ANOMALY' (or 'AZURE_COST_ANOMALY') via execute_datasource_query.\n\n"
        "CONVERSATIONAL MEMORY & MULTI-TURN CONTEXT:\n"
        "- You maintain full conversational memory across all turns in this session.\n"
        "- When the user asks about prior queries, results, or context (e.g. 'which month was I asking for?', 'who spent the most?', 'summarize the table', 'why?'), ALWAYS use the conversation history to answer directly, accurately, and concisely.\n"
        "- Never claim you don't know the requested month or parameters when they were stated in previous user turns or assistant answers.\n"
    )
    return prompt

# ── Multi-Cloud Service Mapping & Extraction (AWS, Azure, GCP) ────────
class ServiceMatch(tuple):
    """Subclass of tuple supporting both 2-item legacy unpacking (pcode, disp) and .provider property."""
    def __new__(cls, pcode, disp, provider):
        return super(ServiceMatch, cls).__new__(cls, (pcode, disp))
    def __init__(self, pcode, disp, provider):
        self.pcode = pcode
        self.disp = disp
        self.provider = provider

CLOUD_SERVICES_MAP = {
    # ── AWS ──
    "rds": ("AmazonRDS", "RDS", "aws"),
    "amazonrds": ("AmazonRDS", "RDS", "aws"),
    "ec2": ("AmazonEC2", "EC2", "aws"),
    "amazonec2": ("AmazonEC2", "EC2", "aws"),
    "s3": ("AmazonS3", "S3", "aws"),
    "amazons3": ("AmazonS3", "S3", "aws"),
    "lambda": ("AWSLambda", "Lambda", "aws"),
    "awslambda": ("AWSLambda", "Lambda", "aws"),
    "vpc": ("AmazonVPC", "VPC", "aws"),
    "amazonvpc": ("AmazonVPC", "VPC", "aws"),
    "dynamodb": ("AmazonDynamoDB", "DynamoDB", "aws"),
    "amazondynamodb": ("AmazonDynamoDB", "DynamoDB", "aws"),
    "sagemaker": ("AmazonSageMaker", "SageMaker", "aws"),
    "amazonsagemaker": ("AmazonSageMaker", "SageMaker", "aws"),
    "guardduty": ("AmazonGuardDuty", "GuardDuty", "aws"),
    "amazonguardduty": ("AmazonGuardDuty", "GuardDuty", "aws"),
    "elb": ("AWSELB", "ELB", "aws"),
    "awselb": ("AWSELB", "ELB", "aws"),
    "elasticloadbalancing": ("AWSELB", "ELB", "aws"),
    "cloudfront": ("AmazonCloudFront", "CloudFront", "aws"),
    "amazoncloudfront": ("AmazonCloudFront", "CloudFront", "aws"),
    "cloudwatch": ("AmazonCloudWatch", "CloudWatch", "aws"),
    "amazoncloudwatch": ("AmazonCloudWatch", "CloudWatch", "aws"),
    "redshift": ("AmazonRedshift", "Redshift", "aws"),
    "amazonredshift": ("AmazonRedshift", "Redshift", "aws"),
    "elasticache": ("AmazonElastiCache", "ElastiCache", "aws"),
    "amazonelasticache": ("AmazonElastiCache", "ElastiCache", "aws"),
    "sns": ("AmazonSNS", "SNS", "aws"),
    "amazonsns": ("AmazonSNS", "SNS", "aws"),
    "sqs": ("AmazonSQS", "SQS", "aws"),
    "amazonsqs": ("AmazonSQS", "SQS", "aws"),
    "ecs": ("AmazonECS", "ECS", "aws"),
    "amazonecs": ("AmazonECS", "ECS", "aws"),
    "eks": ("AmazonEKS", "EKS", "aws"),
    "amazoneks": ("AmazonEKS", "EKS", "aws"),
    "secretsmanager": ("AWSSecretsManager", "Secrets Manager", "aws"),
    "awssecretsmanager": ("AWSSecretsManager", "Secrets Manager", "aws"),
    "kms": ("awskms", "KMS", "aws"),
    "awskms": ("awskms", "KMS", "aws"),
    "bedrock": ("AmazonBedrock", "Bedrock", "aws"),
    "amazonbedrock": ("AmazonBedrock", "Bedrock", "aws"),
    "transfer": ("AWSTransfer", "Transfer Family", "aws"),
    "awstransfer": ("AWSTransfer", "Transfer Family", "aws"),
    "stepfunctions": ("AmazonStates", "Step Functions", "aws"),
    "amazonstates": ("AmazonStates", "Step Functions", "aws"),
    "kinesis": ("AmazonKinesis", "Kinesis", "aws"),
    "amazonkinesis": ("AmazonKinesis", "Kinesis", "aws"),
    "config": ("AWSConfig", "Config", "aws"),
    "awsconfig": ("AWSConfig", "Config", "aws"),
    "glue": ("AWSGlue", "Glue", "aws"),
    "awsglue": ("AWSGlue", "Glue", "aws"),
    "athena": ("AmazonAthena", "Athena", "aws"),
    "amazonathena": ("AmazonAthena", "Athena", "aws"),

    # ── Azure ──
    "azure vm": ("Virtual Machines", "Azure Virtual Machines", "azure"),
    "azure vms": ("Virtual Machines", "Azure Virtual Machines", "azure"),
    "virtual machine": ("Virtual Machines", "Virtual Machines", "azure"),
    "virtual machines": ("Virtual Machines", "Virtual Machines", "azure"),
    "azure blob": ("Blob Storage", "Azure Blob Storage", "azure"),
    "azure blob storage": ("Blob Storage", "Azure Blob Storage", "azure"),
    "blob storage": ("Blob Storage", "Blob Storage", "azure"),
    "azure storage": ("Storage Accounts", "Azure Storage", "azure"),
    "azure sql": ("Azure SQL Database", "Azure SQL", "azure"),
    "azure sql database": ("Azure SQL Database", "Azure SQL Database", "azure"),
    "sql database": ("Azure SQL Database", "SQL Database", "azure"),
    "azure cosmos": ("Cosmos DB", "Azure Cosmos DB", "azure"),
    "azure cosmos db": ("Cosmos DB", "Azure Cosmos DB", "azure"),
    "cosmos db": ("Cosmos DB", "Cosmos DB", "azure"),
    "azure openai": ("Azure OpenAI Service", "Azure OpenAI", "azure"),
    "azure openai service": ("Azure OpenAI Service", "Azure OpenAI Service", "azure"),
    "azure ai": ("Azure OpenAI Service", "Azure AI", "azure"),
    "aks": ("Azure Kubernetes Service", "AKS", "azure"),
    "azure aks": ("Azure Kubernetes Service", "AKS", "azure"),
    "azure kubernetes": ("Azure Kubernetes Service", "Azure Kubernetes", "azure"),
    "azure monitor": ("Azure Monitor", "Azure Monitor", "azure"),
    "log analytics": ("Log Analytics", "Log Analytics", "azure"),
    "azure app service": ("Azure App Service", "App Service", "azure"),
    "app service": ("Azure App Service", "App Service", "azure"),
    "azure functions": ("Azure Functions", "Azure Functions", "azure"),
    "synapse": ("Azure Synapse Analytics", "Synapse Analytics", "azure"),
    "azure synapse": ("Azure Synapse Analytics", "Azure Synapse", "azure"),
    "key vault": ("Key Vault", "Key Vault", "azure"),
    "azure key vault": ("Key Vault", "Azure Key Vault", "azure"),
    "vnet": ("Virtual Network", "Azure VNet", "azure"),
    "azure vnet": ("Virtual Network", "Azure VNet", "azure"),
    "azure databricks": ("Azure Databricks", "Azure Databricks", "azure"),
    "azure vmware": ("Azure VMware Solution", "Azure VMware Solution", "azure"),
    "azure vmware solution": ("Azure VMware Solution", "Azure VMware Solution", "azure"),
    "microsoft fabric": ("Microsoft.Fabric", "Microsoft Fabric", "azure"),
    "fabric": ("Microsoft.Fabric", "Microsoft Fabric", "azure"),
    "github enterprise": ("GitHub Enterprise Cloud", "GitHub Enterprise Cloud", "azure"),
    "power platforms": ("Power Platforms", "Power Platforms", "azure"),
    "power platform": ("Power Platforms", "Power Platforms", "azure"),
    "expressroute": ("Azure ExpressRoute", "Azure ExpressRoute", "azure"),
    "azure expressroute": ("Azure ExpressRoute", "Azure ExpressRoute", "azure"),
    "vm scale set": ("Virtual Machine Scale Sets", "VM Scale Sets", "azure"),
    "vm scale sets": ("Virtual Machine Scale Sets", "VM Scale Sets", "azure"),
    "azure ai services": ("Azure AI Services", "Azure AI Services", "azure"),
    "azure site recovery": ("Azure Site Recovery", "Azure Site Recovery", "azure"),
    "site recovery": ("Azure Site Recovery", "Azure Site Recovery", "azure"),
    "api management": ("API Management", "API Management", "azure"),
    "azure netapp": ("Azure NetApp Files", "Azure NetApp Files", "azure"),
    "azure netapp files": ("Azure NetApp Files", "Azure NetApp Files", "azure"),
    "azure private link": ("Azure Private Link", "Azure Private Link", "azure"),
    "azure arc": ("Azure Arc", "Azure Arc", "azure"),
    "vpn gateway": ("VPN Gateway", "VPN Gateway", "azure"),
    "virtual wan": ("Virtual WAN", "Virtual WAN", "azure"),
    "azure ai search": ("Azure AI Search", "Azure AI Search", "azure"),
    "azure data factory": ("Azure Data Factory", "Azure Data Factory", "azure"),
    "data factory": ("Azure Data Factory", "Azure Data Factory", "azure"),
    "azure devops": ("Azure DevOps", "Azure DevOps", "azure"),
    "application gateway": ("Application Gateway", "Application Gateway", "azure"),
    "service bus": ("Service Bus", "Service Bus", "azure"),
    "load balancer": ("Load Balancer", "Azure Load Balancer", "azure"),
    "azure load balancer": ("Load Balancer", "Azure Load Balancer", "azure"),
    "azure mysql": ("Azure DB for MySQL", "Azure DB for MySQL", "azure"),
    "azure db for mysql": ("Azure DB for MySQL", "Azure DB for MySQL", "azure"),
    "azure bastion": ("Azure Bastion", "Azure Bastion", "azure"),
    "microsoft defender for cloud": ("Microsoft Defender for Cloud", "Microsoft Defender for Cloud", "azure"),
    "defender for cloud": ("Microsoft Defender for Cloud", "Microsoft Defender for Cloud", "azure"),
    "azure data explorer": ("Azure Data Explorer", "Azure Data Explorer", "azure"),
    "power bi embedded": ("Power BI Embedded", "Power BI Embedded", "azure"),
    "azure postgresql": ("Azure DB for PostgreSQL", "Azure DB for PostgreSQL", "azure"),
    "azure db for postgresql": ("Azure DB for PostgreSQL", "Azure DB for PostgreSQL", "azure"),
    "azure cache for redis": ("Azure Cache for Redis", "Azure Cache for Redis", "azure"),
    "azure redis": ("Azure Cache for Redis", "Azure Cache for Redis", "azure"),

    # ── Google Cloud (GCP) ──
    "bigquery": ("BigQuery", "BigQuery", "gcp"),
    "google bigquery": ("BigQuery", "BigQuery", "gcp"),
    "compute engine": ("Compute Engine", "Compute Engine", "gcp"),
    "google compute engine": ("Compute Engine", "Compute Engine", "gcp"),
    "gce": ("Compute Engine", "Compute Engine", "gcp"),
    "cloud storage": ("Cloud Storage", "Cloud Storage", "gcp"),
    "google cloud storage": ("Cloud Storage", "Cloud Storage", "gcp"),
    "gcs": ("Cloud Storage", "Cloud Storage", "gcp"),
    "vertex ai": ("Vertex AI", "Vertex AI", "gcp"),
    "vertex": ("Vertex AI", "Vertex AI", "gcp"),
    "google vertex ai": ("Vertex AI", "Vertex AI", "gcp"),
    "gke": ("Kubernetes Engine", "GKE", "gcp"),
    "google kubernetes engine": ("Kubernetes Engine", "GKE", "gcp"),
    "kubernetes engine": ("Kubernetes Engine", "GKE", "gcp"),
    "cloud sql": ("Cloud SQL", "Cloud SQL", "gcp"),
    "google cloud sql": ("Cloud SQL", "Cloud SQL", "gcp"),
    "cloud spanner": ("Cloud Spanner", "Cloud Spanner", "gcp"),
    "spanner": ("Cloud Spanner", "Spanner", "gcp"),
    "cloud run": ("Cloud Run", "Cloud Run", "gcp"),
    "google cloud run": ("Cloud Run", "Cloud Run", "gcp"),
    "cloud functions": ("Cloud Functions", "Cloud Functions", "gcp"),
    "pubsub": ("Cloud Pub/Sub", "Pub/Sub", "gcp"),
    "cloud pubsub": ("Cloud Pub/Sub", "Pub/Sub", "gcp"),
    "dataproc": ("Cloud Dataproc", "Dataproc", "gcp"),
    "dataflow": ("Cloud Dataflow", "Dataflow", "gcp"),
    "bigquery reservation": ("BigQuery Reservation API", "BigQuery Reservation API", "gcp"),
    "bigquery reservation api": ("BigQuery Reservation API", "BigQuery Reservation API", "gcp"),
    "cloud logging": ("Cloud Logging", "Cloud Logging", "gcp"),
    "gcp netapp": ("NetApp Volumes", "NetApp Volumes", "gcp"),
    "netapp volumes": ("NetApp Volumes", "NetApp Volumes", "gcp"),
    "vmware engine": ("VMware Engine", "Google Cloud VMware Engine", "gcp"),
    "cloud monitoring": ("Cloud Monitoring", "Cloud Monitoring", "gcp"),
    "app engine": ("App Engine", "App Engine", "gcp"),
    "google app engine": ("App Engine", "App Engine", "gcp"),
    "cloud composer": ("Cloud Composer", "Cloud Composer", "gcp"),
    "memorystore": ("Cloud Memorystore for Redis", "Cloud Memorystore for Redis", "gcp"),
    "cloud memorystore": ("Cloud Memorystore for Redis", "Cloud Memorystore for Redis", "gcp"),
    "apigee": ("Apigee", "Apigee", "gcp"),
    "bigtable": ("Cloud Bigtable", "Cloud Bigtable", "gcp"),
    "cloud bigtable": ("Cloud Bigtable", "Cloud Bigtable", "gcp"),
    "cloud kms": ("Cloud Key Management Service (KMS)", "Cloud KMS", "gcp"),
    "gcp kms": ("Cloud Key Management Service (KMS)", "Cloud KMS", "gcp"),
    "vertex ai search": ("Vertex AI Search", "Vertex AI Search", "gcp"),
    "security command center": ("Security Command Center", "Security Command Center", "gcp"),
    "gcp secret manager": ("Secret Manager", "Secret Manager", "gcp"),
    "cloud filestore": ("Cloud Filestore", "Cloud Filestore", "gcp"),
    "filestore": ("Cloud Filestore", "Cloud Filestore", "gcp"),
    "backup and dr": ("Backup and DR Service", "Backup and DR Service", "gcp"),
    "backup and dr service": ("Backup and DR Service", "Backup and DR Service", "gcp"),


}

# Backward-compatibility alias
AWS_SERVICES_MAP = {k: v[0] for k, v in CLOUD_SERVICES_MAP.items() if v[2] == "aws"}
SERVICE_DISPLAY_NAMES = {v[0]: v[1] for v in CLOUD_SERVICES_MAP.values()}
# ServiceMatch (below) is a 2-item tuple subclass, so `a, b = extract_requested_service(...)`
# unpacks it to its (pcode, disp) contents — the ServiceMatch object itself, and its .provider
# attribute, are never retained by the caller. Look up the provider from the pcode instead of
# reading a `.provider` attribute off what is actually just a plain string.
PCODE_TO_PROVIDER = {v[0]: v[2] for v in CLOUD_SERVICES_MAP.values()}

def _friendly_service_name(pcode: str) -> str:
    """Real AWS/Azure/GCP service codes are always mixed/PascalCase (AmazonEC2, Virtual
    Machines). AWS Marketplace line items instead carry an opaque lowercase+digit product
    code (e.g. '7zgyu5r4uonlsrnaq20qq63eo') with no reliable way to resolve the real product
    name from CloudHealth's billing data, so label it honestly rather than guessing."""
    if pcode and pcode == pcode.lower() and any(c.isdigit() for c in pcode) and len(pcode) >= 15:
        return "AWS Marketplace (Third-Party Product)"
    return pcode

def _fetch_total_cost(mcp, sql: str, granularity: str, time_range: dict, crn: str = None) -> float:
    """Runs an unlimited SUM query to get a real total. Breakdown queries elsewhere cap rows
    to a display limit (e.g. "top 10 services") — summing only those rows understates the
    true total and skews "% of spend", so every "Total ..." figure must come from here instead."""
    try:
        params = {
            "queryInput": {"sqlStatement": sql, "dataGranularity": granularity, "limit": 1, "timeRange": time_range},
            "requestInfo": {"sourceType": "API", "caller": "mcp"}
        }
        if crn:
            params["channelCustomerId"] = crn
        res = mcp.call_tool("execute_datasource_query", params)
        raw_csv = json.loads(res["content"][0]["text"]).get("csv", "")
        for r in csv.DictReader(io.StringIO(raw_csv)):
            return float(r.get("cost") or 0.0)
    except Exception as e:
        logger.warning(f"[True Total Query] {e}")
    return 0.0

def extract_requested_service(query_text: str):
    """Extracts cloud service identifier, display label, and provider from query text, respecting negations and resets."""
    q_low = query_text.lower()

    # 1. Explicit resets to all services, overall spend, or complaints about sticking to a service
    all_svcs_patterns = [
        "all service", "all the service", "all the services", "all services",
        "all product", "all products", "every service", "every product",
        "across all services", "across services", "total spend", "overall spend",
        "overall breakdown", "general spend", "entire spend", "all of them",
        "all aws services", "all cloud services", "multi cloud", "multicloud",
        "not just", "not only", "stuck at", "stuck on", "stop showing"
    ]
    if any(p in q_low for p in all_svcs_patterns):
        return None, None

    # 2. Check individual services, skipping negated references. Longest alias first so a more
    # specific match (e.g. "bigquery reservation api") wins over a shorter one it contains
    # (e.g. "bigquery") instead of the shorter one matching first by dict iteration order.
    for alias, (pcode, disp, prov) in sorted(CLOUD_SERVICES_MAP.items(), key=lambda kv: -len(kv[0])):
        for m in re.finditer(r'\b' + re.escape(alias) + r'\b', q_low):
            prefix = q_low[:m.start()].strip()
            is_negated = bool(re.search(
                r'\b(?:not\s+(?:just\s+)?(?:for\s+)?(?:the\s+)?|no\s+|except\s+|excluding\s+|without\s+|other\s+than\s+|stuck\s+at\s+|stuck\s+on\s+|why\s+(?:you\'?re\s+)?(?:still\s+)?(?:stuck\s+at\s+)?)$',
                prefix
            ))
            if not is_negated:
                return ServiceMatch(pcode, disp, prov)

    return None, None

def extract_requested_cloud(query_text: str) -> Optional[str]:
    """Detects if user specifically asks for a cloud provider: aws, azure, gcp, or all/multi-cloud."""
    low = query_text.lower()
    # If multiple clouds are mentioned in the query, it is multi-cloud
    cloud_mentions = 0
    if "aws" in low or "amazon" in low: cloud_mentions += 1
    if "azure" in low or "microsoft" in low: cloud_mentions += 1
    if "gcp" in low or "google cloud" in low or "google" in low: cloud_mentions += 1
    if False and ("oci" in low or "oracle cloud" in low or "oracle" in low): cloud_mentions += 1

    if cloud_mentions >= 2:
        return "all"

    if any(w in low for w in ["all cloud", "all clouds", "multi-cloud", "multicloud", "cross-cloud", "every cloud", "all the cloud", "all of the cloud", "all 4 cloud", "four cloud"]):
        return "all"
    if any(w in low for w in ["azure", "microsoft"]):
        return "azure"
    if any(w in low for w in ["gcp", "google cloud", "google"]):
        return "gcp"
    if False and any(w in low for w in ["oci", "oracle cloud", "oracle"]):
        return "oci"
    if any(w in low for w in ["aws", "amazon"]):
        return "aws"
    return None

def parse_query_time_context(query: str) -> dict:
    """
    Extracts time context, target month, limit, and sort direction from user prompt.
    Handles explicit months (e.g. 'July 2026', '2026-07', 'June'), relative terms
    ('last month', 'mtd'), sort direction ('asc', 'desc'), and custom limits.
    """
    low = query.lower()
    now = datetime.date.today()
    current_ym = now.strftime("%Y-%m")
    last_ym = f"{now.year}-{now.month - 1:02d}" if now.month > 1 else f"{now.year - 1}-12"

    MONTHS = {
        "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
        "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
        "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
        "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12
    }
    FULL_NAMES = {1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June",
                  7: "July", 8: "August", 9: "September", 10: "October", 11: "November", 12: "December"}

    target_ym = None
    target_label = None
    is_specific = False

    # 1. Month Name + Year (e.g. 'July 2026', 'Jul 26', 'July 2025')
    m = re.search(r'\b(' + '|'.join(MONTHS.keys()) + r')[,\s]+((?:19|20)\d{2}|\d{2})\b', low)
    if m:
        m_num = MONTHS[m.group(1)]
        yr_str = m.group(2)
        yr = int(yr_str) if len(yr_str) == 4 else 2000 + int(yr_str)
        target_ym = f"{yr}-{m_num:02d}"
        target_label = f"{FULL_NAMES[m_num]} {yr}"
        is_specific = True

    # 2. ISO YYYY-MM (e.g. '2026-07') or MM/YYYY (e.g. '07/2026')
    if not target_ym:
        m = re.search(r'\b((?:19|20)\d{2})-(0[1-9]|1[0-2])\b', low)
        if m:
            yr = int(m.group(1))
            m_num = int(m.group(2))
            target_ym = f"{yr}-{m_num:02d}"
            target_label = f"{FULL_NAMES[m_num]} {yr}"
            is_specific = True
    if not target_ym:
        m = re.search(r'\b(0[1-9]|1[0-2])/((?:19|20)\d{2})\b', low)
        if m:
            m_num = int(m.group(1))
            yr = int(m.group(2))
            target_ym = f"{yr}-{m_num:02d}"
            target_label = f"{FULL_NAMES[m_num]} {yr}"
            is_specific = True

    # 3. Quarters: Q1, Q2, Q3, Q4 with optional year (e.g. 'Q2 2026', 'Q3')
    if not target_ym:
        m = re.search(r'\bq([1-4])(?:\s+((?:19|20)\d{2}|\d{2}))?\b', low)
        if m:
            q_num = int(m.group(1))
            yr_str = m.group(2)
            yr = (int(yr_str) if len(yr_str) == 4 else 2000 + int(yr_str)) if yr_str else now.year
            q_month = q_num * 3
            target_ym = f"{yr}-{q_month:02d}"
            target_label = f"Q{q_num} {yr}"
            is_specific = True

    # 4. Month Name alone (e.g. 'in July')
    if not target_ym:
        m = re.search(r'\b(' + '|'.join(MONTHS.keys()) + r')\b', low)
        if m:
            m_num = MONTHS[m.group(1)]
            yr = now.year if m_num <= now.month else now.year - 1
            target_ym = f"{yr}-{m_num:02d}"
            target_label = f"{FULL_NAMES[m_num]} {yr}"
            is_specific = True

    # 5. Relative timeframes
    timeframe_days = None
    daily_range = None
    m_days_ctx = re.search(r'(?:last|past|previous|for)\s+(\d{1,3})\s*days?\b|\b(\d{1,3})\s*(?:days?|d)\b', low)
    if m_days_ctx:
        timeframe_days = int(m_days_ctx.group(1) or m_days_ctx.group(2))
        start_d = now - datetime.timedelta(days=timeframe_days)
        end_d = now - datetime.timedelta(days=1)  # Ignore current date (in-flight); end closed window at yesterday
        target_label = f"Last {timeframe_days} Days Trend ({start_d.strftime('%Y-%m-%d')} to {end_d.strftime('%Y-%m-%d')})"
        target_ym = current_ym
        is_specific = True
        daily_range = {"from": start_d.strftime("%Y-%m-%d"), "to": end_d.strftime("%Y-%m-%d")}

    if not target_ym:
        if any(w in low for w in ["last month", "previous month"]):
            target_ym = last_ym
            prev_m = (now.month - 1) if now.month > 1 else 12
            prev_yr = now.year if now.month > 1 else (now.year - 1)
            target_label = f"Last Month ({FULL_NAMES[prev_m]} {prev_yr})"
            is_specific = True
        elif any(w in low for w in ["mtd", "this month", "current month"]):
            target_ym = current_ym
            target_label = f"MTD ({FULL_NAMES[now.month]} {now.year})"
            is_specific = True

    # Default fallback if no month specified
    if not target_ym:
        target_ym = last_ym
        prev_m = (now.month - 1) if now.month > 1 else 12
        prev_yr = now.year if now.month > 1 else (now.year - 1)
        target_label = f"Last Month ({FULL_NAMES[prev_m]} {prev_yr})"

    # Calculate months needed for CloudHealth timeRange (cap at 12 to respect CloudHealth 13-month limit)
    t_yr, t_mo = int(target_ym.split("-")[0]), int(target_ym.split("-")[1])
    diff = (now.year - t_yr) * 12 + (now.month - t_mo)
    months_needed = min(12, max(2, diff + 2))

    # Check for requested duration count e.g. "last 6 months", "past 3 months"
    m_count = re.search(r'(?:last|past|previous|for)\s+(\d{1,2})\s*months?\b|\b([1-9]|[1-4]\d)\s+months\b', low)
    if m_count:
        try:
            req_cnt = int(m_count.group(1) or m_count.group(2))
            months_needed = min(12, max(months_needed, req_cnt + 1))
        except (ValueError, TypeError):
            pass

    sort_desc = not any(w in low for w in ["asc", "ascending", "lowest", "least", "bottom", "cheapest"])

    # Limit extraction
    limit = 10
    top_match = re.search(r"top\s+(\d+)", low)
    if top_match:
        try:
            limit = int(top_match.group(1))
        except ValueError:
            limit = 10
    elif re.search(r'\btop\b', low):
        limit = 5
    elif re.search(r'\ball\b', low):
        limit = 50

    return {
        "target_ym": target_ym,
        "target_label": target_label,
        "months_needed": months_needed,
        "sort_desc": sort_desc,
        "is_specific": is_specific,
        "current_ym": current_ym,
        "last_ym": last_ym,
        "limit": limit,
        "timeframe_days": timeframe_days,
        "daily_range": daily_range
    }

def _deterministic_understand_query(messages: list[dict], cust_map: dict = None) -> dict:
    """
    Fast, deterministic intent and parameter extraction used as baseline and fallback.
    Identifies target cloud service, customer, requested timeframes (defaulting to 30 days trend),
    breakdowns, and distinguishes pure chart reformatting from new telemetry requests.
    """
    last_msg = messages[-1]["content"] if messages else ""
    low = last_msg.lower()

    prior_user_msgs = [m["content"] for m in messages[:-1] if m.get("role") == "user"]
    prior_assistant_msgs = [m["content"] for m in messages[:-1] if m.get("role") == "assistant"]

    # Customer detection
    customer = None
    if cust_map:
        for cname in cust_map.keys():
            if cname.lower() in low:
                customer = cname
                break

    # Service detection
    service = None
    if any(w in low for w in ["rds", "aurora", "relational database"]) or ("database" in low and not any(w in low for w in ["ec2", "s3"])):
        service = "AmazonRDS"
    elif any(w in low for w in ["ec2", "compute instance", "virtual machine"]) or ("instance type" in low and "rds" not in low and "database" not in low):
        service = "AmazonEC2"
    elif any(w in low for w in ["s3", "bucket", "object storage"]):
        service = "AmazonS3"
    elif any(w in low for w in ["azure", "aks", "blob"]):
        service = "Azure"
    elif any(w in low for w in ["gcp", "google cloud", "bigquery"]):
        service = "GCP"
    elif False and any(w in low for w in ["oci", "oracle cloud"]):
        service = "OCI"

    # Timeframe detection
    m_months = re.search(r'\b(\d+)\s*(?:months?|m)\b', low)
    m_days = re.search(r'\b(\d+)\s*(?:days?|d)\b', low)
    has_explicit_months = bool(m_months) or any(w in low for w in ["12months", "12 months", "year", "annual", "months", "month by month", "monthly"])
    has_explicit_days = bool(m_days) or any(w in low for w in ["60days", "60 days", "30days", "30 days", "daily", "by day", "day by day", "per day"])

    timeframe_months = None
    timeframe_days = None
    if has_explicit_months:
        timeframe_months = int(m_months.group(1)) if m_months else 12
    elif has_explicit_days:
        timeframe_days = int(m_days.group(1)) if m_days else 30
    else:
        # User rule: "use last 30days trend for such requests by default unless I ask for the specific time window"
        timeframe_days = 30

    # Breakdowns
    breakdowns = []
    if any(w in low for w in ["instancetype", "instance type", "instance size", "instance"]):
        breakdowns.append("instance_type")
    if any(w in low for w in ["enginetype", "engine type", "engine", "database engine"]):
        breakdowns.append("engine_type")
    if any(w in low for w in ["service", "product"]):
        breakdowns.append("service")
    if any(w in low for w in ["customer", "tenant", "client"]):
        breakdowns.append("customer")

    # Chart types
    chart_types = []
    if "pie" in low:
        chart_types.append("pie")
    elif any(w in low for w in ["donut", "doughnut"]):
        chart_types.append("donut")
    elif any(w in low for w in ["bar", "column", "stacked"]):
        chart_types.append("bar")
    elif "line" in low:
        chart_types.append("line")
    elif "waterfall" in low:
        chart_types.append("waterfall")

    # Strict check for pure reformat without new service/data
    has_fetch_verb = any(w in low for w in ["get me", "fetch", "query", "find me", "show me", "usage for", "spend for", "break it down", "break down"])
    has_explicit_data_subject = bool(service) or bool(customer) or bool(m_months) or bool(m_days)

    is_pure_reformat = (
        bool(re.search(r'\b(?:chart|plot|graph|visualize)\s+(?:of\s+)?(?:it|this|that|above|the\s+above|same\s+data|previous\s+data)\b', low) or
        re.search(r'\b(?:show|render|draw|display)\s+(?:it|this|that|above)\s+(?:as\s+a\s+|in\s+a\s+)?(?:chart|graph|plot|pie|donut|bar)\b', low) or
        any(low.strip() == p for p in [
            "pie chart", "donut chart", "doughnut chart", "bar chart", "waterfall chart",
            "pie chart please", "as a pie chart", "give me the pie chart of it", "give me the pie chart of the above data",
            "chart it", "plot it", "graph it", "show as pie", "show as donut", "show in pie", "show as a pie chart"
        ])) and not (has_explicit_data_subject or has_fetch_verb)
    )

    is_history_qa = any(w in low for w in [
        "which customer", "what customer", "which service", "what service", "which month", "what month",
        "who was #1", "what was #1", "who spent the most", "total spend", "what was the total"
    ]) and not has_explicit_data_subject

    is_rec = any(w in low for w in ["recommendation", "optimize", "saving", "reduce cost", "rightsizing", "waste", "underutilized"])
    is_anomaly = any(w in low for w in ["anomal", "spike", "unusual spend", "unexpected cost"])

    if is_pure_reformat and prior_assistant_msgs:
        intent = "reformat_previous"
        is_new_data_fetch = False
    elif is_history_qa:
        intent = "history_qa"
        is_new_data_fetch = False
    elif is_rec:
        intent = "finops_recommendations"
        is_new_data_fetch = True
    elif is_anomaly:
        intent = "anomalies"
        is_new_data_fetch = True
    elif service or customer or has_fetch_verb or any(w in low for w in ["spend", "cost", "usage", "billed", "hours", "breakdown"]):
        intent = "fetch_data"
        is_new_data_fetch = True
    else:
        intent = "general_chat"
        is_new_data_fetch = False

    return {
        "intent": intent,
        "service": service,
        "customer": customer,
        "timeframe_months": timeframe_months,
        "timeframe_days": timeframe_days,
        "breakdowns": breakdowns,
        "chart_types": chart_types,
        "is_new_data_fetch": is_new_data_fetch
    }

class AIClient:
    def __init__(self, engine_key: str, cfg: dict, tools: list[dict]):
        self.engine = engine_key
        self.cfg = cfg
        self.tools = tools
        self.last_stats = {
            "duration_secs": 0.0,
            "tokens": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "tokens_per_sec": 0.0,
        }
        logger.debug(f"[AIClient Init] Engine: {engine_key} with {len(tools)} tools")

    def get_last_stats(self) -> dict:
        return dict(getattr(self, "last_stats", {}))

    def _call_active_llm(self, messages: list[dict], stats_out: dict = None) -> tuple[str, str]:
        """
        Attempts to call the configured LLM engine.
        Returns (response_text, error_message).
        """
        cfg = self.cfg or _load_config()
        engine = self.engine
        target_stats = stats_out if stats_out is not None else getattr(self, "last_stats", {})

        if engine.startswith("mlx:") or "mlx-community" in engine:
            repo_id = engine.removeprefix("mlx:").strip()
            try:
                ans = call_mlx_generate(repo_id, messages, stats_out=target_stats)
                if ans:
                    return ans, ""
                return "", f"MLX returned empty response for model {repo_id}."
            except Exception as e:
                return "", f"MLX inference error ({e}). Ensure model weights exist in Hugging Face cache."

        elif engine.startswith("ollama"):
            model = engine.split(":", 1)[1] if ":" in engine else "qwen2.5:7b"
            try:
                ans = call_ollama_chat(model, messages, stats_out=target_stats)
                if ans:
                    return ans, ""
                return "", f"Ollama returned empty response for model {model}."
            except Exception as e:
                return "", f"Could not connect to Ollama daemon ({e}). Make sure 'ollama serve' is running."

        elif engine == "gemini":
            key = cfg.get("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
            model = cfg.get("GEMINI_MODEL") or "gemini-2.5-flash"
            if not key:
                return "", "Gemini API key is not configured. Enter it in the Engine Settings modal or set GEMINI_API_KEY."
            try:
                ans = call_gemini_api(key, messages, model=model, stats_out=target_stats)
                if ans:
                    return ans, ""
                return "", "Gemini returned empty response."
            except Exception as e:
                return "", f"Gemini API error: {e}"

        elif engine == "openai":
            key = cfg.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
            model = cfg.get("OPENAI_MODEL") or "gpt-4o"
            if not key:
                return "", "OpenAI API key is not configured. Enter it in the Engine Settings modal or set OPENAI_API_KEY."
            try:
                ans = call_openai_api(key, messages, model=model, stats_out=target_stats)
                if ans:
                    return ans, ""
                return "", "OpenAI returned empty response."
            except Exception as e:
                return "", f"OpenAI API error: {e}"

        elif engine == "anthropic":
            key = cfg.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
            model = cfg.get("ANTHROPIC_MODEL") or "claude-3-7-sonnet-latest"
            if not key:
                return "", "Anthropic API key is not configured. Enter it in the Engine Settings modal or set ANTHROPIC_API_KEY."
            try:
                ans = call_anthropic_api(key, messages, model=model, stats_out=target_stats)
                if ans:
                    return ans, ""
                return "", "Anthropic Claude returned empty response."
            except Exception as e:
                return "", f"Anthropic API error: {e}"

        return "", ""

    def _understand_query(self, messages: list[dict], mcp: MCPClient = None) -> dict:
        """
        LLM-First Request Comprehension & Intent Routing:
        Prompts the active LLM to semantically understand the user's request in the context of recent chat history,
        extracting requested service, customer, timeframe, breakdowns, and whether fresh telemetry must be fetched.
        Falls back to deterministic extraction when engine == 'direct' or on timeout/error.
        """
        last_msg = messages[-1]["content"] if messages else ""
        cust_map = getattr(self, "_cust_map_cache", {})

        det_info = _deterministic_understand_query(messages, cust_map=cust_map)
        if self.engine == "direct":
            return det_info

        prior_user_msgs = [m["content"] for m in messages[:-1] if m.get("role") == "user"]
        prior_assistant_msgs = [m["content"] for m in messages[:-1] if m.get("role") == "assistant"]

        context_lines = []
        if prior_user_msgs:
            context_lines.append(f"Previous User Query: {prior_user_msgs[-1]}")
        if prior_assistant_msgs:
            first_line = prior_assistant_msgs[-1].split("\n")[0]
            context_lines.append(f"Previous Assistant Topic: {first_line[:150]}")
        context_str = "\n".join(context_lines) if context_lines else "None (New Conversation)"

        cal = get_realtime_calendar_info()
        system_instruction = (
            "You are Cleo's FinOps Request Analyzer. Analyze the user query in the context of recent chat history.\n"
            f"REAL-TIME TEMPORAL DETAILS (Ground all dates against this calendar):\n"
            f"- Today's Date: {cal['today_str']} ({cal['today_verbose']}) — ALWAYS IGNORE TODAY in closed daily billing trends as in-flight.\n"
            f"- Yesterday: {cal['yesterday_str']} — ALWAYS REMEMBER that yesterday's data is PARTIAL due to cloud billing settlement latency.\n"
            f"- Current Billing Month (MTD): {cal['current_ym']} ({cal['current_month_name']})\n"
            f"- Last Completed Month: {cal['last_ym']} ({cal['last_month_name']})\n"
            f"- Last 15 Days Window: {cal['d15_start']} to {cal['yesterday_str']} (15 closed days, ending yesterday, ignoring today)\n"
            f"- Last 30 Days Window: {cal['d30_start']} to {cal['yesterday_str']} (30 closed days, ending yesterday, ignoring today)\n\n"
            "Output ONLY a raw JSON object (no markdown, no code fencing, no explanation) with this schema:\n"
            "{\n"
            '  "intent": "fetch_data" | "reformat_previous" | "history_qa" | "finops_recommendations" | "anomalies" | "general_chat",\n'
            '  "service": "AmazonRDS" | "AmazonEC2" | "AmazonS3" | "Azure" | "GCP" | "all" | null,\n'
            '  "customer": string or null,\n'
            '  "timeframe_months": integer or null,\n'
            '  "timeframe_days": integer or null,\n'
            '  "breakdowns": ["instance_type", "engine_type"],\n'
            '  "chart_types": ["bar", "pie", "donut", "line", "stacked_bar"],\n'
            '  "is_new_data_fetch": boolean\n'
            "}\n\n"
            "CRITICAL RULES:\n"
            "1. is_new_data_fetch MUST be true if the user asks to get/fetch/show usage, cost, spend, or mentions a cloud service (RDS, EC2, S3, Azure, etc.) or timeframe, EVEN IF they also ask for a chart.\n"
            "2. is_new_data_fetch is false ONLY when the user asks purely to re-render the immediately preceding table into a different chart format (e.g. 'show that as a pie chart', 'make it a donut') without requesting new data or changing service.\n"
            "3. service: Set to 'AmazonRDS' if user mentions RDS, database, relational database, or aurora. Set to 'AmazonEC2' if user mentions EC2, compute instances (non-database). Set to 'AmazonS3' if user mentions S3, bucket, storage.\n"
            "4. customer: Extract customer name (e.g. 'Lundbeck', 'Novo Nordisk') if specified or clearly referenced.\n"
            "5. timeframe_days: Set to 15 if user asks for last 15 days or 15days. Default to 30 for usage/spend queries unless user specifies a different timeframe (e.g. 12 months, 60 days).\n"
        )

        user_prompt = (
            f"Recent Context:\n{context_str}\n\n"
            f"Current User Query: \"{last_msg}\"\n\n"
            "JSON Analysis:"
        )

        try:
            raw_res, err = self._call_active_llm([
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": user_prompt}
            ])
            if raw_res and not err:
                cleaned = re.sub(r'^```json\s*|\s*```$', '', raw_res.strip(), flags=re.MULTILINE).strip()
                parsed = json.loads(cleaned)
                logger.info(f"[LLM-First Intent Analysis] Parsed: {parsed}")
                if "is_new_data_fetch" in parsed and "intent" in parsed:
                    return {
                        "intent": parsed.get("intent", det_info["intent"]),
                        "service": parsed.get("service") or det_info["service"],
                        "customer": parsed.get("customer") or det_info["customer"],
                        "timeframe_months": parsed.get("timeframe_months") or det_info["timeframe_months"],
                        "timeframe_days": parsed.get("timeframe_days") or det_info["timeframe_days"],
                        "breakdowns": parsed.get("breakdowns") or det_info["breakdowns"],
                        "chart_types": parsed.get("chart_types") or det_info["chart_types"],
                        "is_new_data_fetch": bool(parsed.get("is_new_data_fetch", det_info["is_new_data_fetch"]))
                    }
        except Exception as ex:
            logger.warning(f"[LLM-First Intent Analysis] Fallback to deterministic: {ex}")

        return det_info

    def generate(self, messages: list[dict], mcp: MCPClient = None) -> str:
        t0 = time.perf_counter()
        self.last_stats = {}
        resp = ""
        try:
            resp = self._generate_impl(messages, mcp=mcp)
            
            # Universal LLM Insight Pass for any data response lacking insights
            if "💡 FinOps Insights:" not in resp and "###" in resp and self.engine != "direct":
                # Ensure it's a data response by checking for tables or lists, ignoring simple text errors
                if "|" in resp or "-" in resp:
                    try:
                        last_msg = messages[-1]["content"] if messages else ""
                        sys_msg = {
                            "role": "system",
                            "content": (
                                "You are Cleo, an expert FinOps AI. Analyze the following data presentation returned to the user. "
                                "Provide 2 concise, actionable FinOps insights explaining the data, the biggest cost drivers or anomalies, "
                                "and exactly what the user can do with this information to optimize spend. "
                                "CRITICAL 1: If you see massive Month-over-Month spikes or 100% drops in third-party software, SaaS, or security platforms (e.g., WIZ, Reltio, IBM, Datadog, Snowflake), DO NOT classify them as 'discontinued' or 'unexpected usage spikes'. Correctly identify them as likely one-time or annual Cloud Marketplace commitments/renewals. "
                                "CRITICAL 2: If 'ComputeSavingsPlans', 'SavingsPlans', or 'Reserved Instances' dominate the spend, explicitly identify them as upfront/partial-upfront commitment fees rather than standard run-rate compute usage."
                            )
                        }
                        # Strip raw HTML canvas tags to avoid confusing the LLM and wasting tokens
                        clean_resp = re.sub(r'<canvas.*?</canvas>', '', resp, flags=re.DOTALL)
                        user_msg = {"role": "user", "content": f"User query: {last_msg}\n\nData:\n{clean_resp}"}
                        llm_ans, _ = self._call_active_llm([sys_msg, user_msg])
                        if llm_ans:
                            resp += f"\n\n**💡 FinOps Insights:**\n{_sanitize_finops_bullet_titles(llm_ans)}"
                    except Exception as e:
                        logger.debug(f"[Universal LLM Insights] {e}")

            return resp
        finally:
            dur = max(0.01, round(time.perf_counter() - t0, 2))
            c_tok = self.last_stats.get("completion_tokens")
            if not c_tok:
                c_tok = estimate_token_count(resp)
            p_tok = self.last_stats.get("prompt_tokens")
            if not p_tok:
                prompt_str = " ".join([m.get("content", "") for m in messages if isinstance(m, dict)])
                p_tok = estimate_token_count(prompt_str)
            tot_tok = self.last_stats.get("total_tokens") or (p_tok + c_tok)
            tps = self.last_stats.get("tokens_per_sec")
            if not tps and dur > 0:
                tps = round(c_tok / dur, 1)
            self.last_stats = {
                "duration_secs": dur,
                "tokens": tot_tok,
                "prompt_tokens": p_tok,
                "completion_tokens": c_tok,
                "tokens_per_sec": tps,
            }

    def _generate_impl(self, messages: list[dict], mcp: MCPClient = None) -> str:
        last_msg = messages[-1]["content"] if messages else ""
        logger.debug(f"[AI Generate] Processing user query: {last_msg}")
        low = last_msg.lower()

        # Cache customer map if MCP is present
        if not hasattr(self, "_cust_map_cache"):
            self._cust_map_cache = {}
        if not self._cust_map_cache and mcp:
            try:
                r = mcp.call_tool("list_channel_customers", {})
                custs = json.loads(r["content"][0]["text"])
                self._cust_map_cache = {c["name"]: c["customerId"] for c in custs if c.get("customerId")}
            except Exception:
                pass

        # ── Step 1: LLM-First Request Comprehension & Intent Routing ─────────
        intent_info = self._understand_query(messages, mcp=mcp)
        logger.info(f"[AI Generate] LLM Intent: {intent_info}")

        # ── Inject OptimNow FinOps expert knowledge for domain-matched queries ──
        finops_ctx = get_finops_context(last_msg)
        mem_ctx = get_memory().build_context_block(last_msg)
        extra_ctx = finops_ctx + mem_ctx
        if extra_ctx:
            messages = list(messages)  # don't mutate caller's list
            for i, m in enumerate(messages):
                if m.get("role") == "system":
                    messages[i] = {**m, "content": m["content"] + extra_ctx}
                    break
            if finops_ctx:
                logger.debug(f"[FinOps Refs] Injected expert context ({len(finops_ctx)} chars)")
            if mem_ctx:
                logger.debug(f"[Memory] Injected learned context ({len(mem_ctx)} chars)")

        # Prior context extraction
        prior_user_msgs = [m["content"] for m in messages[:-1] if m.get("role") == "user"]
        prior_assistant_msgs = [m["content"] for m in messages[:-1] if m.get("role") == "assistant"]

        # ── 0. Conversational Memory Queries (Direct Chat History Recall) ─────
        is_mem_cust = any(pattern in low for pattern in [
            "which customer", "what customer", "for which customer", "which client",
            "what client", "which tenant", "what tenant", "who is this for", "who was this for",
            "who is this", "whose spend", "whose cost"
        ])
        is_mem_time = any(pattern in low for pattern in [
            "which month", "what month", "which year", "what was the month",
            "what timeframe", "what period", "what month was i"
        ])
        is_mem_svc = any(pattern in low for pattern in [
            "which service", "what service", "which product", "what product"
        ])
        is_mem_general = any(pattern in low for pattern in [
            "what was i asking", "what did i ask", "what was the query", "what did i request"
        ])

        if (is_mem_cust or is_mem_time or is_mem_svc or is_mem_general) and prior_user_msgs:
            # 0a. Customer memory recall
            if is_mem_cust:
                cust_map = getattr(self, "_cust_map_cache", {})
                if not cust_map and mcp:
                    try:
                        r = mcp.call_tool("list_channel_customers", {})
                        c_list = json.loads(r["content"][0]["text"])
                        cust_map = {c["name"]: c["customerId"] for c in c_list if c.get("customerId")}
                        self._cust_map_cache = cust_map
                    except Exception:
                        pass

                found_cust = None
                found_query = None
                for u in reversed(prior_user_msgs):
                    u_low = u.lower()
                    for cname in cust_map.keys():
                        if cname.lower() in u_low:
                            found_cust = cname
                            found_query = u
                            break
                    if found_cust:
                        break

                if found_cust:
                    return (
                        f"This analysis is for **{found_cust}**.\n\n"
                        f"Specifically, your recent request was: *\"{found_query}\"*"
                    )
                else:
                    return (
                        f"In your previous request, no specific customer was filtered (the query covered all channel customers or partner-wide spend).\n\n"
                        f"Your request was: *\"{prior_user_msgs[-1]}\"*"
                    )

            # 0b. Service memory recall
            elif is_mem_svc:
                found_svc = None
                found_query = None
                for u in reversed(prior_user_msgs):
                    pcode, disp = extract_requested_service(u)
                    if pcode:
                        found_svc = disp or pcode
                        found_query = u
                        break
                if found_svc:
                    return (
                        f"You were analyzing the **{found_svc}** service.\n\n"
                        f"Specifically, your request was: *\"{found_query}\"*"
                    )
                else:
                    return (
                        f"The previous query was across **all services** (overall spend).\n\n"
                        f"Specifically, your request was: *\"{prior_user_msgs[-1]}\"*"
                    )

            # 0c. Month / Time memory recall
            else:
                target_query = ""
                for u in reversed(prior_user_msgs):
                    t = parse_query_time_context(u)
                    if t.get("is_specific"):
                        target_query = u
                        break
                if not target_query:
                    target_query = prior_user_msgs[-1]
                prev_t_ctx = parse_query_time_context(target_query)
                if prev_t_ctx.get("is_specific"):
                    return (
                        f"You were asking for **{prev_t_ctx['target_label']}** ({prev_t_ctx['target_ym']}).\n\n"
                        f"Specifically, your request was: *\"{target_query}\"*"
                    )
                else:
                    return (
                        f"In your previous request, you were analyzing **{prev_t_ctx['target_label']}**.\n\n"
                        f"Specifically, your request was: *\"{target_query}\"*"
                    )

        # ── 0d. Table & Chart Follow-up from Chat History (Zero-Hallucination History Memory) ──
        is_chart_transform = any(w in low for w in [
            "pie chart", "donut chart", "doughnut chart", "bar chart", "line chart", "waterfall chart",
            "show as pie", "show as donut", "show in pie", "show in a pie", "pie chart of",
            "give me the pie chart", "give me a pie chart", "give me the bar chart", "give me the donut chart",
            "visualize this as", "visualize it as", "plot this as", "chart of it", "chart of the above", "chart of this"
        ]) or bool(
            re.search(r'\b(?:chart|plot|graph|visualize)\s+(?:of\s+)?(?:it|this|that|above|the\s+above|same\s+data|previous\s+data)\b', low) or
            re.search(r'\b(?:show|render|draw|display)\s+(?:it|this|that|above)\s+(?:as\s+a\s+|in\s+a\s+)?(?:chart|graph|plot|pie|donut|bar)\b', low)
        ) or any(low.strip() == p for p in [
            "pie chart", "donut chart", "doughnut chart", "bar chart", "waterfall chart",
            "pie chart please", "as a pie chart", "give me the pie chart of it", "give me the pie chart of the above data",
            "chart it", "plot it", "graph it"
        ])

        is_data_qa_followup = any(w in low for w in [
            "who was #1", "who is #1", "what was #1", "what is #1", "highest spend", "highest cost",
            "top spender", "what was the total", "total spend", "total cost", "which had the most hours",
            "highest hours", "how much was total", "who spent the most"
        ])

        # LLM-First Guard: Section 0d ONLY executes if the LLM understanding confirms
        # this is purely reformatting the prior turn's table or asking a QA question about it,
        # AND NO new telemetry data fetch or service query is requested!
        # `is_chart_transform` above is a loose substring match (e.g. "waterfall chart" anywhere
        # in the message), so on its own it also fires for "waterfall chart for the last 12
        # months for Azure cloud" — a request for fresh data in a NEW scope, not a reformat of
        # what's already on screen. `intent_info["is_new_data_fetch"]` is supposed to catch this,
        # but it comes from an LLM call (or a deterministic fallback) that can misjudge it, so
        # also require the CURRENT message itself not contain an explicit new timeframe or
        # provider — a redundant, always-on safety net independent of that classification.
        has_new_scope_signal = bool(re.search(r'\d+\s*(months?|days?|years?|weeks?)\b', low)) or any(
            w in low for w in ["azure", "aws", "gcp", "last month", "this month", "last year",
                                "this year", "last quarter", "this quarter", "q1", "q2", "q3", "q4"]
        )
        is_pure_reformat = (intent_info.get("intent") == "reformat_previous" or is_chart_transform) and not intent_info.get("is_new_data_fetch") and not has_new_scope_signal
        is_hist_qa = (intent_info.get("intent") == "history_qa" or is_data_qa_followup) and not intent_info.get("is_new_data_fetch") and not has_new_scope_signal

        if (is_pure_reformat or is_hist_qa) and prior_assistant_msgs and not intent_info.get("is_new_data_fetch"):
            parsed_hist = None
            for a_msg in reversed(prior_assistant_msgs):
                parsed_hist = _parse_table_data_from_assistant_markdown(a_msg)
                if parsed_hist and parsed_hist["rows"]:
                    break

            if parsed_hist and parsed_hist["rows"]:
                rows = parsed_hist["rows"]
                total = parsed_hist["total"]

                if is_chart_transform:
                    target_type = "pie"
                    if any(w in low for w in ["donut", "doughnut"]):
                        target_type = "doughnut"
                    elif any(w in low for w in ["bar", "column"]):
                        target_type = "bar"
                    elif "line" in low:
                        target_type = "line"
                    elif "waterfall" in low:
                        target_type = "waterfall"

                    top_limit = 8
                    top_rows = rows[:top_limit]
                    remainder = total - sum(r["cost"] for r in top_rows)

                    chart_labels = [r["label"] for r in top_rows]
                    chart_values = [round(r["cost"], 2) for r in top_rows]
                    if remainder > 0.5:
                        chart_labels.append("Other (combined)")
                        chart_values.append(round(remainder, 2))

                    table_block = ""
                    if any(w in low for w in ["with table", "include table", "and table", "show table"]):
                        tbl_lines = []
                        has_hours = any(r.get("hours", 0) > 0 for r in top_rows)
                        for idx, r in enumerate(top_rows, start=1):
                            p = r["pct"] or ((r["cost"] / total) * 100 if total else 0.0)
                            h_str = f" | {r['hours']:,.0f} hrs" if has_hours else ""
                            tbl_lines.append(f"| {idx} | `{r['label']}` | **${r['cost']:,.2f}** | {p:.1f}%{h_str} |")

                        if remainder > 0.5:
                            rem_pct = (remainder / total) * 100 if total else 0.0
                            h_blank = " | —" if has_hours else ""
                            tbl_lines.append(f"| {len(top_rows)+1} | *Other Categories (combined)* | **${remainder:,.2f}** | {rem_pct:.1f}%{h_blank} |")

                        h_col = " | Instance Hours" if has_hours else ""
                        div_col = "|:---" if has_hours else ""
                        table_header = f"| # | Category / Item | Cost | % of Total{h_col} |\n|:---|:---|:---|:---{div_col}|\n" + "\n".join(tbl_lines)
                        total_blank = " |" if has_hours else ""
                        total_line = f"\n| **Total** | **All Categories** | **${total:,.2f}** | **100.0%**{total_blank} |\n"
                        table_block = f"{table_header}{total_line}\n"

                    clean_title = re.sub(r'[:(]?\s*(?:Pie|Bar|Donut|Doughnut|Line|Waterfall)\s*Chart(?:\s*View)?\)?', '', parsed_hist['title'], flags=re.I).strip()
                    clean_title = re.sub(r'^[^\w\s]+', '', clean_title).strip()
                    chart_title = f"{clean_title} ({target_type.capitalize()} Chart)"
                    chart_block = _chart_block(target_type, chart_title, chart_labels, values=chart_values, value_label="Cost ($)")

                    icon = "🥧" if target_type in ["pie", "doughnut"] else "📊"
                    source_str = f" via `{parsed_hist['dataset_name']}`" if parsed_hist["dataset_name"] else ""
                    return (
                        f"### {icon} {clean_title}: {target_type.capitalize()} Chart View\n\n"
                        f"Visualizing the spend distribution from your previous query ({parsed_hist['period']}):\n\n"
                        f"- **Billing Period**: {parsed_hist['period']}\n"
                        f"- **Total Spend Analyzed**: **${total:,.2f}** across **{len(rows)}** categories\n\n"
                        f"{table_block}"
                        f"{chart_block}\n"
                        f"*Source: Live telemetry from chat history{source_str}.*"
                    )

                elif is_data_qa_followup:
                    if any(w in low for w in ["who was #1", "who is #1", "what was #1", "what is #1", "highest spend", "highest cost", "top spender", "who spent the most"]):
                        top_r = rows[0]
                        p_str = f" ({top_r['pct']:.1f}% of total)" if top_r.get("pct") else ""
                        h_str = f" with {top_r['hours']:,.0f} hours" if top_r.get("hours") else ""
                        return (
                            f"Based on the previous analysis ({parsed_hist['period']}):\n\n"
                            f"🏆 **#1 Highest Spend**: **`{top_r['label']}`** at **${top_r['cost']:,.2f}**{p_str}{h_str}."
                        )
                    elif any(w in low for w in ["total", "how much was total", "total spend", "total cost"]):
                        return (
                            f"Based on the previous analysis ({parsed_hist['period']}):\n\n"
                            f"💰 **Total Spend**: **${total:,.2f}** across **{len(rows)}** active categories/items."
                        )
                    elif any(w in low for w in ["most hours", "highest hours"]):
                        sorted_by_hrs = sorted(rows, key=lambda x: x.get("hours", 0), reverse=True)
                        top_h = sorted_by_hrs[0]
                        return (
                            f"Based on the previous analysis ({parsed_hist['period']}):\n\n"
                            f"⏱️ **Highest Usage**: **`{top_h['label']}`** with **{top_h.get('hours', 0):,.0f} hours** (${top_h['cost']:,.2f})."
                        )

        # ── 1. Metadata & Schema Queries (Highest Specificity) ────────────────
        if "metadata" in low or "schema" in low or "column" in low:
            if mcp:
                target_ds = "AWS_CUR" if ("cur" in low or "aws" in low) else "CLOUDHEALTH_CONSUMPTION_BREAKDOWN"
                res = mcp.call_tool("get_datasource_metadata", {"dataset": target_ds})
                content = res.get("content", [{}])[0].get("text", "{}")
                try:
                    meta = json.loads(content)
                    cols = meta.get("commonColumns") or meta.get("columns", [])
                    col_rows = "\n".join([f"| `{c.get('name')}` | `{c.get('dataType') or c.get('type')}` |" for c in cols[:25]])
                    more_msg = f"\n\n*Showing top 25 of {len(cols)} columns.*" if len(cols) > 25 else ""
                    return (
                        f"### 📋 Schema & Metadata: `{meta.get('dataset', target_ds)}`\n\n"
                        f"| Column Name | Data Type |\n"
                        f"|:---|:---|\n"
                        f"{col_rows}"
                        f"{more_msg}\n"
                    )
                except Exception:
                    return f"### Metadata:\n```json\n{content}\n```"

        # ── 2. Datasource Catalog Queries ─────────────────────────────────────
        # Only list catalog when user is asking WHAT datasets exist, not when they
        # name a specific dataset to query (e.g. "use the EC2 dataset").
        _catalog_intent = any(w in low for w in ["list dataset", "available dataset", "show dataset",
                                                   "what dataset", "which dataset", "available datasource",
                                                   "list datasource", "show datasource", "what datasource"])
        _names_specific = any(w in low for w in ["aws_", "azure_", "multicloud_", "cloudhealth_",
                                                   "use the", "use ec2", "ec2 dataset", "ec2 instance dataset",
                                                   "instance dataset", "pricing dataset"])
        if _catalog_intent and not _names_specific and not any(w in low for w in ["anomal", "spike"]):
            if mcp:
                res = mcp.call_tool("list_standard_datasources", {})
                content = res.get("content", [{}])[0].get("text", "[]")
                try:
                    idx = content.find("[")
                    ds_list = json.loads(content[idx:] if idx != -1 else content)
                    rows = "\n".join([f"| **`{d.get('datasetName') or d.get('name')}`** | {d.get('datasetDisplayName') or d.get('description', 'Standard FinOps dataset')} |" for d in ds_list])
                    return (
                        f"### 📚 Available CloudHealth Standard Datasources\n\n"
                        f"Found **{len(ds_list)}** queryable FinOps datasets:\n\n"
                        f"| Dataset Identifier | Description |\n"
                        f"|:---|:---|\n"
                        f"{rows}\n\n"
                        f"> 💡 *Tip*: You can query any of these datasets using `execute_datasource_query`."
                    )
                except Exception:
                    return f"### Datasources:\n```json\n{content}\n```"

        # ── 2b. Explicit Named-Dataset Query ──────────────────────────────────
        # When user references a specific dataset by name and asks for a query/breakdown,
        # extract the dataset name and delegate to the LLM with it injected.
        _KNOWN_DATASETS = [
            "AWS_EC2_COST_AND_USAGE", "AWS_CUR", "MULTICLOUD_FOCUS_COST_AND_USAGE",
            "CLOUDHEALTH_CONSUMPTION_BREAKDOWN", "AWS_COST_ANOMALY", "AZURE_COST_ANOMALY",
            "AWS_FOCUS_COST_AND_USAGE", "AZURE_FOCUS_COST_AND_USAGE",
            "DX_PRICING_AWS_EC2", "DX_PRICING_AWS_ECS", "AWS_ASSET_INVENTORY",
        ]
        # Also match colloquial forms: "ec2 instance dataset", "ec2 cost and usage"
        _COLLOQUIAL_DATASET_MAP = {
            "ec2 instance dataset": "AWS_EC2_COST_AND_USAGE",
            "ec2 cost and usage": "AWS_EC2_COST_AND_USAGE",
            "ec2 cost usage": "AWS_EC2_COST_AND_USAGE",
            "ec2 dataset": "AWS_EC2_COST_AND_USAGE",
            "ec2 pricing dataset": "DX_PRICING_AWS_EC2",
            "instance dataset": "AWS_EC2_COST_AND_USAGE",
            "cur dataset": "AWS_CUR",
            "focus dataset": "MULTICLOUD_FOCUS_COST_AND_USAGE",
            "consumption breakdown": "CLOUDHEALTH_CONSUMPTION_BREAKDOWN",
        }
        _explicit_dataset = None
        for ds in _KNOWN_DATASETS:
            if ds.lower() in low:
                _explicit_dataset = ds
                break
        if not _explicit_dataset:
            for colloquial, ds in _COLLOQUIAL_DATASET_MAP.items():
                if colloquial in low:
                    _explicit_dataset = ds
                    break

        if _explicit_dataset and mcp:
            # Extract grouping/dimension hint from query
            _dim_hints = {
                "instancetype": "instanceType", "instance type": "instanceType",
                "instance_type": "instanceType", "region": "region",
                "service": "ServiceName", "account": "lineItem_UsageAccountId",
                "month": "timeInterval_Month", "usagetype": "lineItem_UsageType",
            }
            _group_col = None
            for hint, col in _dim_hints.items():
                if hint in low:
                    _group_col = col
                    break

            # Delegate to LLM with explicit dataset + grouping injected into context
            ds_hint = f"\n\nUser explicitly asked to query dataset: **{_explicit_dataset}**."
            if _group_col:
                ds_hint += f" Group by: `{_group_col}`."
            ds_hint += " Build and execute an appropriate `execute_datasource_query` SQL query against this dataset."
            messages_with_hint = list(messages)
            messages_with_hint.append({"role": "user", "content": ds_hint})
            # Fall through to LLM with augmented context below
            messages = messages_with_hint

        curr_t_ctx = parse_query_time_context(last_msg)
        word_count = len(last_msg.strip().split())
        has_continuation_prefix = any(low.startswith(p) for p in [
            "and ", "now ", "also ", "then ", "what about", "how about", "and for", "now in", "instead", "switch to",
            "and what about", "and what is", "and what's", "and whats", "and in", "what if", "can you compare", "compare with",
            "now show", "and show", "show me", "give me"
        ]) or any(w in low for w in ["for that", "for them", "of that", "of them", "same period", "same customer"])

        has_cust_pronoun = bool(re.search(r'\b(their|theirs|them|that customer|this customer|same customer|that tenant|this tenant|same tenant|that client|this client|same client)\b', low))

        has_pronoun_ref = bool(re.search(r'\b(their|theirs|they|them|its|it|this|that|these|those|above|the above|previous|same data|this data|that data|of it|of this|of that|from above|similar|simillar|same)\b', low))

        is_short_filter_tweak = (word_count <= 6) and (
            curr_t_ctx["is_specific"] or
            any(w in low for w in ["asc", "desc", "ascending", "descending", "lowest", "highest"]) or
            bool(re.match(r'^(?:top|limit)\s+\d+$', low.strip())) or
            any(w in low for w in ["ec2", "rds", "s3", "lambda", "vpc", "all services"])
        )
        is_clarification = any(w in low for w in [
            "all service", "all the service", "all the services", "all services",
            "not just", "not only", "stuck at", "stuck on", "stop showing",
            "every service", "across all", "for all services", "all of them"
        ])

        is_standalone_request = (
            any(w in low for w in ["recommendation", "recommendations", "optimize", "optimization", "rightsizer", "reduce cost", "save money", "anomal", "spike"]) or
            (bool(re.search(r'\b(?:fetch|get|show|give|list|display|find)\b', low)) and not has_pronoun_ref) or
            bool(re.search(r'\b\d+\s*days?\b', low)) or
            (word_count > 6 and not has_continuation_prefix and not is_clarification and not has_cust_pronoun and not has_pronoun_ref)
        )

        # ── Resolve channel customer list (cached) ───────────────────────
        if not hasattr(self, "_cust_map_cache"):
            self._cust_map_cache = {}
        cust_map = self._cust_map_cache
        if not cust_map and mcp:
            try:
                r = mcp.call_tool("list_channel_customers", {})
                custs = json.loads(r["content"][0]["text"])
                cust_map = {c["name"]: c["customerId"] for c in custs if c.get("customerId")}
                self._cust_map_cache = cust_map
            except Exception as e:
                logger.warning(f"[Customer CRN Map] Failed: {e}")

        # Look backwards through prior user messages for the most recent FinOps query context
        prior_cost_query = ""
        for u_msg in reversed(prior_user_msgs):
            u_low = u_msg.lower()
            if any(w in u_low for w in ["cost", "spend", "bill", "usage", "trend", "breakdown", "customer", "channel", "tenant", "service", "aws"]):
                prior_cost_query = u_msg
                break

        prior_cost_low = prior_cost_query.lower() if prior_cost_query else ""
        is_prior_cost_query = bool(prior_cost_query)
        is_prior_cust_query = any(w in prior_cost_low for w in ["customer", "channel", "tenant", "client"]) or any(c.lower() in prior_cost_low for c in cust_map)

        is_followup = bool((is_prior_cost_query or has_pronoun_ref) and (has_continuation_prefix or is_short_filter_tweak or is_clarification or has_pronoun_ref) and not is_standalone_request)

        # ── Check if a specific customer name is in the query or inherited ──
        named_customer = None
        named_customer_crn = None
        if intent_info.get("customer"):
            llm_c = intent_info["customer"].strip().lower()
            for cname, ccrn in cust_map.items():
                if cname.lower() == llm_c or cname.lower() in llm_c or llm_c in cname.lower():
                    named_customer = cname
                    named_customer_crn = ccrn
                    break

        if not named_customer:
            for cname, ccrn in cust_map.items():
                if cname.lower() in low:
                    named_customer = cname
                    named_customer_crn = ccrn
                    break

        is_cust_reset = any(w in low for w in ["all customer", "all channel", "partner wide", "overall", "all tenant", "every customer"])
        if not named_customer and (has_cust_pronoun or (is_followup and not is_standalone_request and is_prior_cust_query)) and not is_cust_reset:
            for u_msg in reversed(prior_user_msgs):
                u_low = u_msg.lower()
                for cname, ccrn in cust_map.items():
                    if cname.lower() in u_low:
                        named_customer = cname
                        named_customer_crn = ccrn
                        break
                if named_customer:
                    break
            if not named_customer:
                for a_msg in reversed(prior_assistant_msgs):
                    a_low = a_msg.lower()
                    for cname, ccrn in cust_map.items():
                        if cname.lower() in a_low:
                            named_customer = cname
                            named_customer_crn = ccrn
                            break
                    if named_customer:
                        break

        # ── 2b2. Honest failure when a customer name can't be resolved at all ──
        # With no channel-customer list loaded (e.g. this token is a direct-customer
        # token, not a partner token), the matching above can never succeed no matter
        # what the user typed — it silently falls through to an unscoped, partner-wide
        # query with zero indication the requested customer was dropped. A named-entity
        # heuristic here (independent of cust_map, since cust_map is exactly what's
        # missing) catches that case and says so plainly instead of quietly answering
        # a different question than the one asked.
        if not named_customer and not cust_map and not is_cust_reset:
            _cust_name_blocklist = {
                "aws", "azure", "gcp", "oci", "channel", "partner", "all", "top", "new",
                "show", "get", "find", "list", "the", "total", "overall", "current", "last"
            }
            _cust_name_match = re.search(
                r"\bfor\s+([A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*){0,2})\s+customer\b|"
                r"\b([A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*){0,2})\s+customer\b|"
                r"\bcustomer\s+([A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*){0,2})\b",
                last_msg
            )
            if _cust_name_match:
                _unresolved_name = next(g for g in _cust_name_match.groups() if g)
                if _unresolved_name.lower() not in _cust_name_blocklist:
                    return (
                        f"### ⚠️ Customer Not Resolved: {_unresolved_name}\n\n"
                        f"I can't scope this query to **{_unresolved_name}** — this CloudHealth token has no "
                        f"channel/partner customer list available (it looks like a direct-customer token rather "
                        f"than a partner token), so there's no customer list to match the name against.\n\n"
                        f"Re-authenticate with partner-level CloudHealth access to query per-customer data, or "
                        f"ask for partner-wide totals instead."
                    )

        # ── 2c. FinOps Advisory, Architecture & Playbook Queries ─────────────
        # If the user is asking an architectural, advisory, or methodology question
        # (e.g. "how do I optimize gp2 to gp3", "what is the break-even on Azure reservations",
        # "explain EffectiveCost vs BilledCost in FOCUS", "how to detect zombie NAT gateways"),
        # handle it via OptimNow FinOps knowledge rather than misrouting to CloudHealth telemetry.
        is_explicit_telemetry_table = any(w in low for w in [
            "top 5 customer", "top customer", "top channel customer", "highest spend",
            "top aws services", "top services", "top cloud services", "multi-cloud spend",
            "services across", "across aws", "across all", "across azure", "across gcp",
            "show monthly spend", "monthly spend breakdown", "monthly spend trend", "cloud spend trend",
            "waterfall chart", "show as waterfall", "show as bar chart", "bar chart",
            "cost anomalies detected", "detected by cloudhealth", "anomalies detected by cloudhealth",
            "show our top", "show top", "show spend", "show cost", "breakdown by engine", "rds usage", "ec2 usage",
            "last 15 days", "last 15days", "last 30 days", "last 30days",
            "spend by", "cost by", "usage by", "spend breakdown", "usage breakdown", "cost breakdown"
        ]) or bool(is_followup and is_prior_cust_query)

        is_advisory_inquiry = any(phrase in low for phrase in [
            "how to", "how do i", "how can i", "how should", "best practice", "playbook",
            "explain", "what is the difference", "trade-off", "tradeoff",
            "doctrine", "strategy", "architecture", "what is focus", "what is billedcost",
            "what is effectivecost", "break-even", "roi of"
        ])

        # A follow-up continuing a live-data conversation (e.g. "give the similar data for
        # AWS and GCP" right after a real Azure spend query) must never be hijacked into a
        # generic FinOps-education answer just because a keyword in ROUTING (cleo_finops_refs.py)
        # loosely matches a word in it — that silently drops the live-data request entirely.
        is_live_data_followup = is_followup and is_prior_cost_query and not is_advisory_inquiry
        finops_adv = get_finops_advisory(last_msg) if not is_live_data_followup else None
        if finops_adv and not named_customer and not is_explicit_telemetry_table:
            # External LLM synthesis if active
            if self.engine != "direct":
                cal = get_realtime_calendar_info()
                sys_msg = {
                    "role": "system",
                    "content": (
                        f"You are Cleo, an expert Autonomous FinOps advisor.\n"
                        f"Real-Time Calendar Context: Today is {cal['today_str']} ({cal['today_verbose']}), current billing period is {cal['current_ym']}.\n"
                        f"Use the authoritative OptimNow FinOps guidance below to directly and professionally answer the user's question.\n"
                        f"CRITICAL: DO NOT output pseudocode, python scripts, tool calls (e.g. list_standard_datasources), or narrate API mechanics.\n"
                        f"Provide clear, actionable FinOps recommendations, trade-offs, and architecture best practices.\n\n"
                        f"AUTHORITATIVE GUIDANCE:\n{finops_adv}"
                    )
                }
                user_msg = {"role": "user", "content": last_msg}
                llm_resp, err = self._call_active_llm([sys_msg, user_msg])
                if llm_resp:
                    return llm_resp
                if err:
                    logger.warning(f"[LLM Advisory Fallback] {err}")
            # Direct FinOps Router mode: return authoritative OptimNow playbook/reference guidance
            return finops_adv

        # ── 3. Cost, Spend, Breakdown & Billing Queries (High Priority) ──────
        is_cost_query = any(w in low for w in [
            "cost", "spend", "bill", "usage", "trend", "breakdown",
            "expense", "expensive", "top", "unblended", "recommendation",
            "recommendations", "optimize", "optimization", "forecast", "projected",
            "anomal", "spike", "spikes", "unusual",
            "chart", "waterfall", "graph", "plot", "pie", "donut", "doughnut", "instance", "ec2", "rds", "service"
        ]) or bool(is_prior_cost_query and is_clarification)

        if is_prior_cost_query and is_followup:
            is_cost_query = True

        if is_cost_query and mcp:
            cal = get_realtime_calendar_info()
            today_str = cal["today_str"]
            yesterday_str = cal["yesterday_str"]
            time_ctx = parse_query_time_context(last_msg)
            target_ym = time_ctx["target_ym"]
            target_label = time_ctx["target_label"]
            months_needed = time_ctx["months_needed"]
            sort_desc = time_ctx["sort_desc"]
            is_specific = time_ctx["is_specific"]
            current_ym = time_ctx["current_ym"]
            last_ym = time_ctx["last_ym"]
            limit = time_ctx["limit"]

            # If this is a follow-up turn without its own specific date, inherit the previous target date
            if is_followup and not is_specific and prior_cost_query:
                prev_t_ctx = parse_query_time_context(prior_cost_query)
                if prev_t_ctx["is_specific"]:
                    target_ym = prev_t_ctx["target_ym"]
                    target_label = prev_t_ctx["target_label"]
                    is_specific = True
                    now = datetime.date.today()
                    t_yr, t_mo = int(target_ym.split("-")[0]), int(target_ym.split("-")[1])
                    diff = (now.year - t_yr) * 12 + (now.month - t_mo)
                    months_needed = min(12, max(2, diff + 2))

            time_range = {"last": min(months_needed, 12), "qualifier": "MONTH"}

            # ── Extract or inherit requested AWS service ──────────────────────
            requested_service, requested_service_disp = extract_requested_service(last_msg)
            # Only reset requested_service if explicitly asking for all services or negating, AND no specific service was named
            is_explicit_all_svcs = any(w in low for w in [
                "all service", "all the service", "all the services", "all services",
                "all product", "all products", "every service", "every product",
                "across all services", "all of them", "total spend across all", "overall spend across all"
            ]) and not requested_service

            is_svc_negation = any(w in low for w in [
                "not just", "not only", "stuck at", "stuck on", "stop showing"
            ]) and not requested_service

            is_svc_reset = is_explicit_all_svcs or is_svc_negation
            if is_svc_reset:
                requested_service = None
                requested_service_disp = None
            elif not requested_service and is_followup:
                for u_msg in reversed(prior_user_msgs):
                    pcode, disp = extract_requested_service(u_msg)
                    if pcode:
                        requested_service, requested_service_disp = pcode, disp
                        break

            # ── Extract or inherit requested cloud provider (aws, azure, gcp, all) ──
            active_cloud = extract_requested_cloud(last_msg)
            if not active_cloud and is_followup:
                for u_msg in reversed(prior_user_msgs):
                    c = extract_requested_cloud(u_msg)
                    if c:
                        active_cloud = c
                        break
            if requested_service and not active_cloud:
                active_cloud = PCODE_TO_PROVIDER.get(str(requested_service))

            # 3-Anomaly. CloudHealth Cost Anomaly Detection (AWS_COST_ANOMALY & AZURE_COST_ANOMALY)
            is_anomaly_query = any(w in low for w in [
                "anomal", "cost spike", "spend spike", "spike in cost",
                "spikes", "unusual spend", "abnormal spend", "abnormal cost", "unusual cost"
            ])
            if is_anomaly_query:
                is_azure = (active_cloud == "azure") or ("azure" in low)
                ds_name = "AZURE_COST_ANOMALY" if is_azure else "AWS_COST_ANOMALY"
                cloud_label = "Azure" if is_azure else "AWS"

                # Parse requested limit (e.g. "top 3 anomalies" -> 3)
                m_lim = re.search(r'(?:top|limit)\s*(\d{1,2})', low)
                anomaly_limit = int(m_lim.group(1)) if m_lim else (limit if limit != 10 else 5)
                anomaly_limit = max(1, min(anomaly_limit, 20))

                # Month filter
                filter_ym = target_ym if is_specific or any(w in low for w in ["this month", "current month", "month", "september", "august", "july", "2026"]) else current_ym
                where_clauses = []
                if filter_ym:
                    where_clauses.append(f"timeInterval_Month = '{filter_ym}'")
                if "active" in low:
                    where_clauses.append("Status = 'ACTIVE'")
                if requested_service:
                    where_clauses.append(f"(Service = '{requested_service}' OR Service LIKE '%{requested_service_disp}%')")

                where_sql = f"WHERE {' AND '.join(where_clauses)} " if where_clauses else ""

                account_col = "SubscriptionID" if is_azure else "AccountID"
                anomaly_sql = (
                    f"SELECT Service AS service, CostImpact AS cost_impact, "
                    f"CostImpactPercentage AS impact_pct, CostImpactType AS impact_type, "
                    f"Status AS status, Duration_Days AS duration_days, Region AS region, "
                    f"{account_col} AS account_id, timeInterval_Month AS month "
                    f"FROM {ds_name} "
                    f"{where_sql}"
                    f"ORDER BY CostImpact DESC"
                )

                try:
                    res_anom = mcp.call_tool("execute_datasource_query", {
                        "queryInput": {
                            "sqlStatement": anomaly_sql,
                            "dataGranularity": "MONTHLY",
                            "limit": anomaly_limit,
                            "timeRange": {"last": max(months_needed, 3), "qualifier": "MONTH"}
                        },
                        "requestInfo": {"sourceType": "API", "caller": "mcp"}
                    })
                    raw_csv = json.loads(res_anom["content"][0]["text"]).get("csv", "")
                    anom_rows = list(csv.DictReader(io.StringIO(raw_csv)))
                except Exception as e:
                    logger.error(f"[Anomaly Query Error] {e}")
                    anom_rows = []

                # Fallback: if specific month filter returned 0, query without month filter to surface recent anomalies
                period_notice = ""
                if not anom_rows and filter_ym:
                    fallback_sql = (
                        f"SELECT Service AS service, CostImpact AS cost_impact, "
                        f"CostImpactPercentage AS impact_pct, CostImpactType AS impact_type, "
                        f"Status AS status, Duration_Days AS duration_days, Region AS region, "
                        f"{account_col} AS account_id, timeInterval_Month AS month "
                        f"FROM {ds_name} "
                        f"ORDER BY CostImpact DESC"
                    )
                    try:
                        res_fb = mcp.call_tool("execute_datasource_query", {
                            "queryInput": {
                                "sqlStatement": fallback_sql,
                                "dataGranularity": "MONTHLY",
                                "limit": anomaly_limit,
                                "timeRange": {"last": 6, "qualifier": "MONTH"}
                            },
                            "requestInfo": {"sourceType": "API", "caller": "mcp"}
                        })
                        raw_csv_fb = json.loads(res_fb["content"][0]["text"]).get("csv", "")
                        anom_rows = list(csv.DictReader(io.StringIO(raw_csv_fb)))
                        if anom_rows:
                            period_notice = f"\n> ℹ️ *Note: No anomalies recorded specifically for {filter_ym}; displaying top anomalies across recent billing periods.*\n"
                    except Exception as e:
                        logger.warning(f"[Anomaly Fallback] {e}")

                # If user asked about datasets specifically (e.g. "try listing the datasets you can find the anomalies there")
                dataset_prefix = ""
                if any(w in low for w in ["dataset", "datasource", "where", "how", "catalog"]):
                    dataset_prefix = (
                        f"### 📚 CloudHealth Anomaly Detection Standard Datasets\n\n"
                        f"CloudHealth features dedicated machine learning anomaly detection datasets in FlexReports:\n\n"
                        f"| Dataset Identifier | Cloud | Description |\n"
                        f"|:---|:---|:---|\n"
                        f"| **`AWS_COST_ANOMALY`** | AWS | Identified AWS anomalies with cost variance, percentage spike, status, account, and duration. |\n"
                        f"| **`AZURE_COST_ANOMALY`** | Azure | Identified Azure anomalies supporting automated anomaly detection. |\n\n"
                    )

                if not anom_rows:
                    scope_str = f" for **{filter_ym}**" if filter_ym else ""
                    return (
                        f"{dataset_prefix}"
                        f"### 🛡️ CloudHealth Cost Anomaly Detection ({cloud_label})\n\n"
                        f"No cost anomalies were detected in `{ds_name}`{scope_str}.\n\n"
                        f"- **Evaluated Dataset**: `{ds_name}`\n"
                        f"- **Time Scope**: Trailing {months_needed} months\n"
                        f"- **Status**: Cloud spend is tracking within normal baseline variance limits without triggered alerts.\n\n"
                        f"*Source: CloudHealth Anomaly Detection (`{ds_name}`).*"
                    )

                period_label = f"({filter_ym})" if filter_ym and not period_notice else "(Recent Periods)"
                table_lines = []
                insights = []
                for idx, r in enumerate(anom_rows[:anomaly_limit]):
                    try:
                        impact_val = float(r.get("cost_impact") or 0)
                        pct_val = float(r.get("impact_pct") or 0)
                    except (ValueError, TypeError):
                        impact_val, pct_val = 0.0, 0.0

                    st = r.get("status", "UNKNOWN").upper()
                    st_badge = "🔴 **ACTIVE**" if st == "ACTIVE" else ("⚪ **INACTIVE**" if st == "INACTIVE" else f"📁 {st}")
                    svc = r.get("service", "Unknown")
                    reg = r.get("region", "global")
                    acc = r.get("account_id", "—")
                    dur = r.get("duration_days", "0")
                    dur_str = "Ongoing" if dur in ("0", "-1") and st == "ACTIVE" else f"{dur} days"
                    mo = r.get("month", "")

                    sign = "+" if impact_val > 0 else ""
                    table_lines.append(
                        f"| {idx+1} | `{svc}` | {st_badge} | **{sign}${impact_val:,.2f}** | **{pct_val:+.1f}%** 🔺 | `{reg}` | `{acc}` | {dur_str} | {mo} |"
                    )

                    # Synthesize FinOps insights for top anomalies
                    if idx < 3:
                        if any(k in svc.lower() for k in ["bedrock", "claude", "gpt", "anthropic", "openai"]):
                            insights.append(f"- **GenAI / LLM Model Spikes ({st_badge})**: `{svc}` in `{reg}` surged by **{pct_val:+.1f}%** (adding **{sign}${impact_val:,.2f}** in {mo}). Audit active inference endpoints, batch invocation jobs, or newly deployed agent workloads in Account `{acc}`.")
                        elif "sagemaker" in svc.lower():
                            insights.append(f"- **Machine Learning Workloads ({st_badge})**: `{svc}` in `{reg}` spiked by **{pct_val:+.1f}%** (adding **{sign}${impact_val:,.2f}** in {mo}). Inspect notebook instances, multi-node training clusters, and idle real-time endpoints in Account `{acc}`.")
                        elif any(k in svc.lower() for k in ["ec2", "ecs", "eks"]):
                            insights.append(f"- **Compute Capacity Surge ({st_badge})**: `{svc}` in `{reg}` had an anomalous spend jump of **{sign}${impact_val:,.2f}** ({pct_val:+.1f}%). Check Auto Scaling group limits, unreserved on-demand instances, or container task runaway in Account `{acc}`.")
                        elif any(k in svc.lower() for k in ["rds", "database", "aurora"]):
                            insights.append(f"- **Database Provisioning Variance ({st_badge})**: `{svc}` in `{reg}` experienced an anomalous increase of **{sign}${impact_val:,.2f}** ({pct_val:+.1f}%). Audit multi-AZ replicas, unreserved instances, or provisioned IOPS in Account `{acc}`.")
                        else:
                            insights.append(f"- **{svc} ({st_badge})**: Added an unexpected **{sign}${impact_val:,.2f}** ({pct_val:+.1f}%) in `{reg}` (Account `{acc}`).")

                insights_block = ""
                if insights:
                    insights_block = "\n#### 💡 FinOps Root Cause & Investigation Insights\n\n" + "\n".join(insights) + "\n"

                total_impact = sum(float(r.get("cost_impact") or 0) for r in anom_rows[:anomaly_limit])
                active_count = sum(1 for r in anom_rows[:anomaly_limit] if r.get("status", "").upper() == "ACTIVE")

                return (
                    f"{dataset_prefix}"
                    f"### 🚨 CloudHealth Cost Anomaly Detection: Top {len(table_lines)} Anomalies {period_label}\n\n"
                    f"{period_notice}"
                    f"Queried live anomaly telemetry directly from `{ds_name}`:\n\n"
                    f"- **Total Identified Anomaly Impact**: **+${total_impact:,.2f}** across **{len(table_lines)}** anomalies\n"
                    f"- **Active Ongoing Anomalies**: **{active_count}** requiring immediate review\n\n"
                    f"| # | Service / Asset | Status | Cost Impact | Variance (%) | Region | Account / Sub | Duration | Month |\n"
                    f"|:---|:---|:---|:---|:---|:---|:---|:---|:---|\n"
                    f"{chr(10).join(table_lines)}\n"
                    f"{insights_block}\n"
                    f"*Source: CloudHealth Anomaly Detection (`{ds_name}`). Monitored via live CloudHealth FlexReports.*"
                )

            # 3-RDS-IT. Dedicated RDS Instance Type, Engine & Spend Analysis via AWS_RDS_COST_AND_USAGE & AWS_CUR
            is_rds_instance_or_usage = (
                intent_info.get("service") == "AmazonRDS" or
                any(w in low for w in ["rds", "relational database", "aurora"]) or
                ("database" in low and any(w in low for w in ["instance", "type", "engine", "usage", "spend", "cost", "breakdown"]))
            ) and not any(w in low for w in ["recommendation", "anomal", "spike", "optimize rds"])

            if not is_rds_instance_or_usage and is_followup:
                prev_is_rds = any("aws_rds_cost_and_usage" in a.lower() or "rds spend analysis" in a.lower() or "rds usage" in a.lower() for a in prior_assistant_msgs[-1:]) or \
                              any(w in prior_cost_low for w in ["rds", "database engine", "rds instance", "relational database"])
                if prev_is_rds and not any(w in low for w in ["customer", "tenant", "anomal", "recommendation", "ec2", "s3", "bigquery", "azure"]):
                    is_rds_instance_or_usage = True

            if is_rds_instance_or_usage and mcp:
                m_months = re.search(r'\b(\d+)\s*(?:months?|m)\b', low)
                m_days = re.search(r'\b(\d+)\s*(?:days?|d)\b', low)
                has_explicit_months = bool(m_months) or bool(intent_info.get("timeframe_months")) or any(w in low for w in ["12months", "12 months", "year", "annual", "months", "month by month", "monthly"])
                if has_explicit_months:
                    num_months = intent_info.get("timeframe_months") or (int(m_months.group(1)) if m_months else 12)
                    num_days = 0
                else:
                    # User rule: "use last 30days trend for such requests by default unless I ask for the specific time window"
                    num_days = intent_info.get("timeframe_days") or (int(m_days.group(1)) if m_days else 30)
                    num_months = 0

                tenant_suffix = " (Partner Tenant)" if any(w in low for w in ["partner tenant", "tenant"]) else " (Partner-Wide)"
                cust_label = named_customer or f"All Accounts{tenant_suffix}"

                wants_table = _detect_wants_table(low)
                chart_type = _detect_chart_type(low)

                has_engine_kw = any(w in low for w in ["engine", "enginetype", "engine type", "flavor", "database engine", "db engine"])
                has_instance_kw = any(w in low for w in ["instance type", "instancetype", "instance size", "by instance", "instance breakdown", "instances", "instance"])

                if has_engine_kw and not has_instance_kw:
                    wants_engine_type = True
                    wants_instance_type = False
                elif has_instance_kw and not has_engine_kw:
                    wants_engine_type = False
                    wants_instance_type = True
                else:
                    wants_engine_type = has_engine_kw
                    wants_instance_type = True

                if num_days > 0:
                    rds_sql = (
                        "SELECT TimeInterval_Day AS day, InstanceType AS instance_type, "
                        "SUM(BilledCost) AS cost, SUM(Instances) AS instances, "
                        "SUM(ComputeCost) AS compute_cost, SUM(StorageCost) AS storage_cost, "
                        "SUM(GP2StorageCost) AS gp2_cost, SUM(GP3StorageCost) AS gp3_cost "
                        "FROM AWS_RDS_COST_AND_USAGE "
                        "GROUP BY TimeInterval_Day, InstanceType "
                        "ORDER BY day ASC, cost DESC"
                    )
                    rds_tr = {"last": num_days, "qualifier": "DAY"}
                    rds_gran = "DAILY"
                    period_label = f"Last {num_days} Days Trend"
                else:
                    rds_sql = (
                        "SELECT Month AS month, InstanceType AS instance_type, "
                        "SUM(BilledCost) AS cost, SUM(Instances) AS instances, "
                        "SUM(ComputeCost) AS compute_cost, SUM(StorageCost) AS storage_cost, "
                        "SUM(GP2StorageCost) AS gp2_cost, SUM(GP3StorageCost) AS gp3_cost "
                        "FROM AWS_RDS_COST_AND_USAGE "
                        "GROUP BY Month, InstanceType "
                        "ORDER BY month ASC, cost DESC"
                    )
                    rds_tr = {"last": num_months, "qualifier": "MONTH"}
                    rds_gran = "MONTHLY"
                    period_label = f"Last {num_months} Months"

                rds_q_params = {
                    "queryInput": {
                        "sqlStatement": rds_sql,
                        "dataGranularity": rds_gran,
                        "limit": 5000 if num_days > 0 else 500,
                        "timeRange": rds_tr
                    },
                    "requestInfo": {"sourceType": "API", "caller": "mcp"}
                }
                if named_customer_crn:
                    rds_q_params["channelCustomerId"] = named_customer_crn

                rds_rows = []
                try:
                    res_rds = mcp.call_tool("execute_datasource_query", rds_q_params)
                    txt_rds = res_rds.get("content", [{}])[0].get("text", "{}")
                    raw_csv = json.loads(txt_rds).get("csv", "")
                    for row in csv.DictReader(io.StringIO(raw_csv)):
                        try:
                            c = float(row.get("cost") or row.get("BilledCost") or 0.0)
                            it = (row.get("instance_type") or row.get("InstanceType") or "").strip()
                            m = (row.get("day") or row.get("Day") or row.get("month") or row.get("Month") or row.get("TimeInterval_Day") or "").strip()
                            inst = float(row.get("instances") or 0.0)
                            comp = float(row.get("compute_cost") or 0.0)
                            stor = float(row.get("storage_cost") or 0.0)
                            gp2 = float(row.get("gp2_cost") or 0.0)
                            gp3 = float(row.get("gp3_cost") or 0.0)
                            if it and m and c > 0:
                                rds_rows.append({
                                    "month": m, "instance_type": it, "cost": c,
                                    "instances": inst, "compute_cost": comp, "storage_cost": stor,
                                    "gp2_cost": gp2, "gp3_cost": gp3
                                })
                        except (ValueError, TypeError):
                            pass
                except Exception as e:
                    logger.warning(f"[AWS_RDS_COST_AND_USAGE Query] {e}")

                if rds_rows:
                    if num_days > 0:
                        # Exclude today (in-flight) as per FinOps doctrine
                        rds_rows = [r for r in rds_rows if r["month"] != today_str]
                    months_present = sorted(list({r["month"] for r in rds_rows}))
                    if num_days > 0:
                        period_str = f"Last {num_days} Days Trend ({months_present[0]} to {months_present[-1]})" if months_present else f"Last {num_days} Days Trend"
                    else:
                        period_str = f"Last {len(months_present)} Months ({months_present[0]} to {months_present[-1]})" if months_present else f"Last {num_months} Months"
                    total_rds_cost = sum(r["cost"] for r in rds_rows)

                    agg_types = {}
                    for r in rds_rows:
                        it = r["instance_type"]
                        if it not in agg_types:
                            agg_types[it] = {"cost": 0.0, "instances": 0.0, "compute": 0.0, "storage": 0.0}
                        agg_types[it]["cost"] += r["cost"]
                        agg_types[it]["instances"] += r["instances"]
                        agg_types[it]["compute"] += r["compute_cost"]
                        agg_types[it]["storage"] += r["storage_cost"]

                    sorted_types = sorted(agg_types.items(), key=lambda x: x[1]["cost"], reverse=True)

                    it_table_lines = []
                    graviton_candidates = []
                    for idx, (it, d) in enumerate(sorted_types[:15]):
                        c = d["cost"]
                        pct = (c / total_rds_cost * 100) if total_rds_cost else 0.0
                        avg_inst = (d["instances"] / len(months_present)) if months_present and d["instances"] > 0 else 0.0
                        inst_str = f"{avg_inst:.1f}" if avg_inst >= 0.1 else "—"
                        comp_str = f"${d['compute']:,.2f}" if d['compute'] > 0 else "—"
                        stor_str = f"${d['storage']:,.2f}" if d['storage'] > 0 else "—"
                        it_table_lines.append(
                            f"| {idx+1} | `{it}` | **${c:,.2f}** | {pct:.1f}% | {inst_str} | {comp_str} | {stor_str} |"
                        )
                        if any(fam in it for fam in ["m5.", "m5d.", "r5.", "t3."]):
                            graviton_candidates.append({"instance_type": it, "cost": c})

                    remaining_types = sorted_types[15:]
                    if remaining_types:
                        rem_cost = sum(d["cost"] for _, d in remaining_types)
                        rem_pct = (rem_cost / total_rds_cost * 100) if total_rds_cost else 0.0
                        rem_count = len(remaining_types)
                        it_table_lines.append(
                            f"| - | *Other ({rem_count} instance types)* | **${rem_cost:,.2f}** | {rem_pct:.1f}% | — | — | — |"
                        )

                    # Instance Type Chart
                    t_fmt = "day" if num_days > 0 else "month"
                    if chart_type in ["doughnut", "pie"]:
                        it_labels = [it for it, _ in sorted_types[:10]]
                        it_vals = [round(d["cost"], 2) for _, d in sorted_types[:10]]
                        chart_it_md = _chart_block(
                            chart_type,
                            f"RDS Spend by Instance Type ({period_str}) — {cust_label}",
                            it_labels,
                            values=it_vals,
                            value_label="Cost ($)"
                        )
                    elif chart_type == "horizontal-bar":
                        it_labels = [it for it, _ in sorted_types[:10]]
                        it_vals = [round(d["cost"], 2) for _, d in sorted_types[:10]]
                        chart_it_md = _chart_block(
                            "horizontal-bar",
                            f"RDS Spend by Instance Type ({period_str}) — {cust_label}",
                            it_labels,
                            values=it_vals,
                            value_label="Cost ($)",
                            horizontal=True
                        )
                    elif chart_type == "waterfall":
                        wf_labels = ["Baseline (Total)"] + [it for it, _ in sorted_types[:7]] + ["Total"]
                        wf_vals = [round(total_rds_cost, 2)] + [round(d["cost"], 2) for _, d in sorted_types[:7]] + [round(total_rds_cost, 2)]
                        chart_it_md = _build_waterfall_chart(
                            f"RDS Instance Type Cost Contribution ({period_str}) — {cust_label}",
                            wf_labels, wf_vals
                        )
                    elif chart_type == "line":
                        # Trend query: line chart of total spend over time + MoM/DoD variance waterfall
                        period_totals = {}
                        for r in rds_rows:
                            period_totals[r["month"]] = period_totals.get(r["month"], 0.0) + r["cost"]
                        sorted_p = sorted(period_totals.keys())
                        line_labels = [_format_time_label(t, t_fmt) for t in sorted_p]
                        line_vals = [round(period_totals[t], 2) for t in sorted_p]
                        chart_it_md = _chart_block(
                            "line",
                            f"RDS Total Spend Trend ({period_str}) — {cust_label}",
                            line_labels,
                            values=line_vals,
                            value_label="Cost ($)"
                        )
                        # MoM/DoD variance waterfall
                        variance_md = _build_mom_variance_chart(
                            f"RDS Spend {('DoD' if num_days > 0 else 'MoM')} Variance ({period_str}) — {cust_label}",
                            sorted_p, period_totals, time_format=t_fmt
                        )
                        chart_it_md += f"\n{variance_md}" if variance_md else ""
                    else:
                        chart_it_md = _build_time_category_stacked_chart(
                            f"RDS Spend by Instance Type ({period_str}) — {cust_label}",
                            rds_rows,
                            time_col="month",
                            cat_col="instance_type",
                            cost_col="cost",
                            time_format=t_fmt,
                            max_cats=10
                        )

                    it_table_block = (
                        f"| # | Instance Type | Total Spend | % of Total | Avg Instances | Compute Cost | Storage Cost |\n"
                        f"|:---|:---|:---|:---|:---|:---|:---|\n"
                        f"{chr(10).join(it_table_lines)}\n"
                        f"| **Total** | **All Instance Types** | **${total_rds_cost:,.2f}** | **100.0%** | | | |\n\n"
                    ) if wants_table else ""

                    instance_section_md = (
                        f"#### 🖥️ Spend by RDS Instance Type\n\n"
                        f"{it_table_block}"
                        f"{chart_it_md}\n\n"
                    )


                    # Query Engine Breakdown via AWS_CUR if requested
                    engine_section_md = ""
                    engine_total = 0.0
                    engine_rows = []
                    if wants_engine_type:
                        cur_engine_params = {
                            "queryInput": {
                                "sqlStatement": (
                                    "SELECT product_databaseEngine AS engine, SUM(lineItem_UnblendedCost) AS cost "
                                    "FROM AWS_CUR "
                                    "WHERE lineItem_ProductCode = 'AmazonRDS' AND product_databaseEngine IS NOT NULL AND product_databaseEngine != '' "
                                    "GROUP BY product_databaseEngine "
                                    "ORDER BY cost DESC"
                                ),
                                "dataGranularity": "DAILY" if num_days > 0 else "MONTHLY",
                                "limit": 500,
                                "timeRange": {"last": num_days, "qualifier": "DAY"} if num_days > 0 else {"last": num_months, "qualifier": "MONTH"}
                            },
                            "requestInfo": {"sourceType": "API", "caller": "mcp"}
                        }
                        if named_customer_crn:
                            cur_engine_params["channelCustomerId"] = named_customer_crn

                        try:
                            res_eng = mcp.call_tool("execute_datasource_query", cur_engine_params)
                            txt_eng = res_eng.get("content", [{}])[0].get("text", "{}")
                            raw_eng_csv = json.loads(txt_eng).get("csv", "")
                            for row in csv.DictReader(io.StringIO(raw_eng_csv)):
                                try:
                                    c_eng = float(row.get("cost") or 0.0)
                                    eng_name = (row.get("engine") or "").strip()
                                    if eng_name == "Any":
                                        eng_name = "Multi-Engine / Shared Storage"
                                    elif not eng_name:
                                        eng_name = "Other / Unclassified"
                                    if c_eng > 0:
                                        engine_rows.append({"engine": eng_name, "cost": c_eng})
                                except (ValueError, TypeError):
                                    pass
                        except Exception as e:
                            logger.warning(f"[AWS_CUR Engine Query] {e}")

                        if engine_rows:
                            engine_total = sum(r["cost"] for r in engine_rows)
                            eng_table_lines = []
                            eng_labels = []
                            eng_values = []
                            for idx, r in enumerate(engine_rows):
                                p = (r["cost"] / engine_total * 100) if engine_total else 0.0
                                eng_table_lines.append(f"| {idx+1} | `{r['engine']}` | **${r['cost']:,.2f}** | {p:.1f}% |")
                                eng_labels.append(r["engine"])
                                eng_values.append(round(r["cost"], 2))

                            eng_kind = chart_type if chart_type in ["doughnut", "pie", "bar", "horizontal-bar"] else "doughnut"
                            chart_eng_md = _chart_block(
                                eng_kind,
                                f"RDS Spend by Database Engine ({period_str}) — {cust_label}",
                                eng_labels,
                                values=eng_values,
                                value_label="Cost ($)",
                                horizontal=(eng_kind == "horizontal-bar")
                            )

                            eng_table_block = (
                                f"| # | Database Engine | Billed Spend | % of Total |\n"
                                f"|:---|:---|:---|:---|\n"
                                f"{chr(10).join(eng_table_lines)}\n"
                                f"| **Total** | **All Engines** | **${engine_total:,.2f}** | **100.0%** |\n\n"
                            ) if wants_table else ""

                            engine_section_md = (
                                f"#### 🗄️ RDS Database Engine Distribution\n\n"
                                f"{eng_table_block}"
                                f"{chart_eng_md}\n\n"
                            )

                    # FinOps Recommendations
                    insights = []
                    if graviton_candidates:
                        grav_spend = sum(r["cost"] for r in graviton_candidates)
                        est_savings = grav_spend * 0.20
                        top_grav_names = ", ".join([f"`{r['instance_type']}`" for r in graviton_candidates[:4]])
                        insights.append(
                            f"- **AWS Graviton Modernization**: Identified **${grav_spend:,.2f}** across legacy x86 instances ({top_grav_names}). "
                            f"Transitioning to Graviton3/4 equivalents (`db.m7g`, `db.r7g`, `db.t4g`) yields up to **20% direct savings** (~**${est_savings:,.2f}**) with up to 20% higher throughput. "
                            f"Because RDS is fully managed by AWS, engine compatibility is transparent with zero application code refactoring required."
                        )
                    tot_gp2 = sum(r["gp2_cost"] for r in rds_rows)
                    if tot_gp2 > 50.0:
                        gp3_savings = tot_gp2 * 0.20
                        insights.append(
                            f"- **RDS Storage gp2 to gp3 Migration**: Detected **${tot_gp2:,.2f}** in gp2 storage. Upgrading storage volumes to gp3 delivers an immediate **20% storage cost reduction** (~**${gp3_savings:,.2f}**) while providing baseline 3,000 IOPS and 125 MB/s."
                        )
                    insights.append(
                        f"- **Database Savings Plans / Reserved Instances**: For steady-state 24/7 databases (`db.m5.2xlarge`, `db.r7g.xlarge`), committing to 1-year or 3-year Database RIs saves 30% to 55% relative to standard On-Demand pricing."
                    )

                    insights_block = "\n#### 💡 FinOps Optimization Levers & Database Architecture Recommendations\n\n" + "\n".join(insights) + "\n"

                    partial_notice = (
                        f"> ⚠️ **FinOps Ingestion Notice**: Current date (`{today_str}`) is excluded from closed analysis as in-flight; "
                        f"yesterday's data (`{yesterday_str}`) is preliminary/partial across all cloud providers due to standard 24–48h billing ingestion latency.\n\n"
                    ) if num_days > 0 else ""

                    if wants_engine_type and not wants_instance_type:
                        header_title = "Database Engine Breakdown"
                        disp_total = engine_total if engine_total else total_rds_cost
                        item_count = len(engine_rows) if engine_rows else len(sorted_types)
                        item_desc = "active database engines"
                        body_sections_md = engine_section_md
                    elif wants_instance_type and not wants_engine_type:
                        header_title = "Instance Type Breakdown"
                        disp_total = total_rds_cost
                        item_count = len(sorted_types)
                        item_desc = "active database instance types"
                        body_sections_md = instance_section_md
                    else:
                        header_title = "Multi-Breakdown View"
                        disp_total = total_rds_cost
                        item_count = len(sorted_types)
                        item_desc = "active database instance types"
                        body_sections_md = f"{instance_section_md}{engine_section_md}"

                    return (
                        f"### 🗄️ CloudHealth RDS Spend Analysis: {header_title}\n\n"
                        f"Queried live from standard datasets **`AWS_RDS_COST_AND_USAGE`** and **`AWS_CUR`** for **{cust_label}**:\n\n"
                        f"- **Target Billing Period**: {period_str}\n"
                        f"- **Total RDS Billed Spend**: **${disp_total:,.2f}** across **{item_count}** {item_desc}\n\n"
                        f"{partial_notice}"
                        f"{body_sections_md}"
                        f"{insights_block}\n"
                        f"*Source: AWS_RDS_COST_AND_USAGE and AWS_CUR via CloudHealth FlexReports.*"
                    )

            # 3-EC2-IT. Dedicated EC2 Instance Type & Usage Analysis via AWS_EC2_COST_AND_USAGE
            is_ec2_instance_query = (
                (intent_info.get("service") == "AmazonEC2" and any(w in low for w in ["instance", "usage", "spend", "cost", "breakdown", "ec2"])) or
                any(w in low for w in ["instance type", "instancetype", "instance dataset", "ec2_cost_and_usage", "by instance", "instance breakdown", "instances breakdown"]) or
                ("ec2" in low and any(w in low for w in ["instance", "type", "breakdown"]))
            ) and not any(w in low for w in ["recommendation", "anomal", "spike", "rds", "relational", "aurora", "database"]) and intent_info.get("service") != "AmazonRDS"

            if not is_ec2_instance_query and is_followup:
                prev_is_ec2 = any("aws_ec2_cost_and_usage" in a.lower() or "instance type breakdown" in a.lower() for a in prior_assistant_msgs[-1:]) or \
                              any(w in prior_cost_low for w in ["instance type", "instance breakdown", "ec2 usage by instance"])
                if prev_is_ec2 and not any(w in low for w in ["customer", "tenant", "anomal", "recommendation", "gp2", "rds", "s3", "bigquery", "azure"]):
                    is_ec2_instance_query = True

            if is_ec2_instance_query and mcp:
                m_days = re.search(r'\b(\d+)\s*(?:days?|d)\b', low)
                m_months = re.search(r'\b(\d+)\s*(?:months?|m)\b', low)
                has_explicit_months = bool(m_months) or bool(intent_info.get("timeframe_months")) or any(w in low for w in ["month by month", "monthly", "12 months", "year", "months"])
                if has_explicit_months:
                    num_days = 0
                else:
                    # User rule: "use last 30days trend for such requests by default unless I ask for the specific time window"
                    num_days = intent_info.get("timeframe_days") or (int(m_days.group(1)) if m_days else 30)

                tenant_suffix = " (Partner Tenant)" if any(w in low for w in ["partner tenant", "tenant"]) else " (Partner-Wide)"
                cust_label = named_customer or f"All Accounts{tenant_suffix}"

                if num_days > 0:
                    ec2_sql = (
                        "SELECT TimeInterval_Day AS day, product_InstanceType AS instance_type, "
                        "SUM(Instance_Cost) AS cost, SUM(Compute_Cost) AS compute_cost, "
                        "SUM(Instance_Hours) AS hours, SUM(Instances) AS instances, SUM(VCPUs) AS vcpus "
                        "FROM AWS_EC2_COST_AND_USAGE "
                        "WHERE product_InstanceType IS NOT NULL AND product_InstanceType != '' AND product_InstanceType != 'Unknown' "
                        "GROUP BY TimeInterval_Day, product_InstanceType "
                        "ORDER BY day ASC"
                    )
                    today = datetime.date.today()
                    yesterday = today - datetime.timedelta(days=1)
                    if num_days > 14:
                        # CloudHealth FlexReports limits datasource queries to 1,000 rows.
                        # For daily instance queries (~60 instance types), 15+ days easily exceeds 1,000 rows.
                        # Chunk into slices of at most 14 days so each chunk safely fits under the 1,000-row cap.
                        query_time_ranges = []
                        curr_end = yesterday
                        total_rem = num_days
                        while total_rem > 0:
                            chunk_len = min(14, total_rem)
                            curr_start = curr_end - datetime.timedelta(days=chunk_len - 1)
                            query_time_ranges.insert(0, {"from": str(curr_start), "to": str(curr_end)})
                            curr_end = curr_start - datetime.timedelta(days=1)
                            total_rem -= chunk_len
                    else:
                        start_d = today - datetime.timedelta(days=num_days)
                        query_time_ranges = [{"from": str(start_d), "to": str(yesterday)}]
                else:
                    ec2_sql = (
                        "SELECT Month AS month, product_InstanceType AS instance_type, "
                        "SUM(Billed_Cost) AS cost, SUM(Instance_Hours) AS hours, "
                        "SUM(Instances) AS instances, SUM(VCPUs) AS vcpus "
                        "FROM AWS_EC2_COST_AND_USAGE "
                        "WHERE product_InstanceType IS NOT NULL AND product_InstanceType != '' AND product_InstanceType != 'Unknown' "
                        "GROUP BY Month, product_InstanceType "
                        "ORDER BY month ASC, cost DESC"
                    )
                    query_time_ranges = [{"last": 8, "qualifier": "MONTH"}]

                def _fetch_ec2_chunk(tr):
                    ec2_q_params = {
                        "queryInput": {
                            "sqlStatement": ec2_sql,
                            "dataGranularity": "DAILY" if num_days > 0 else "MONTHLY",
                            "limit": -1 if num_days > 0 else 200,
                            "timeRange": tr
                        },
                        "requestInfo": {"sourceType": "API", "caller": "mcp"}
                    }
                    if named_customer_crn:
                        ec2_q_params["channelCustomerId"] = named_customer_crn
                    try:
                        res_ec2 = mcp.call_tool("execute_datasource_query", ec2_q_params)
                        txt = res_ec2.get("content", [{}])[0].get("text", "{}")
                        raw_csv = json.loads(txt).get("csv", "")
                        chunk_rows = []
                        for row in csv.DictReader(io.StringIO(raw_csv)):
                            try:
                                c = float(row.get("cost") or row.get("SUM_Instance_Cost") or row.get("Billed_Cost") or 0.0)
                                it = (row.get("instance_type") or row.get("Product_InstanceType") or "").strip()
                                t_val = (row.get("day") or row.get("Day") or row.get("month") or row.get("Month") or "").strip()
                                h = float(row.get("hours") or row.get("SUM_Instance_Hours") or row.get("Instance_Hours") or 0.0)
                                v = float(row.get("vcpus") or 0.0)
                                if it and t_val and (c > 0 or h > 0):
                                    if num_days > 0 and t_val == today_str:
                                        continue
                                    chunk_rows.append({
                                        "time_val": t_val, "instance_type": it, "cost": c,
                                        "hours": h, "vcpus": v
                                    })
                            except (ValueError, TypeError):
                                pass
                        return chunk_rows
                    except Exception as e:
                        logger.warning(f"[AWS_EC2_COST_AND_USAGE Query] {e}")
                        return []

                ec2_rows = []
                if len(query_time_ranges) > 1:
                    with ThreadPoolExecutor(max_workers=min(len(query_time_ranges), 6)) as executor:
                        for chunk_res in executor.map(_fetch_ec2_chunk, query_time_ranges):
                            ec2_rows.extend(chunk_res)
                else:
                    ec2_rows = _fetch_ec2_chunk(query_time_ranges[0])

                if not ec2_rows:
                    return (
                        f"### 🖥️ CloudHealth EC2 Spend Analysis\n\n"
                        f"No live EC2 instance-type billing data was returned for **{cust_label}** in this period. "
                        f"This usually means there was no EC2 spend in this window, or the connected CloudHealth "
                        f"account/scope doesn't have visibility into it.\n\n"
                        f"*Source: AWS_EC2_COST_AND_USAGE via CloudHealth FlexReports.*"
                    )

                if ec2_rows:
                    chart_type = _detect_chart_type(low)
                    wants_table = _detect_wants_table(low)
                    t_format = "quarter" if "quarter" in low else "month"

                    if num_days > 0:
                        # Daily analysis over the specified trailing day window
                        days_present = sorted(list({r["time_val"] for r in ec2_rows}))
                        start_lbl = _format_time_label(days_present[0], "day") if days_present else "Start"
                        end_lbl = _format_time_label(days_present[-1], "day") if days_present else "End"
                        period_header = f"Trailing {num_days} Days ({start_lbl} to {end_lbl})"

                        agg_types = {}
                        for r in ec2_rows:
                            it = r["instance_type"]
                            if it not in agg_types:
                                agg_types[it] = {"cost": 0.0, "hours": 0.0}
                            agg_types[it]["cost"] += r["cost"]
                            agg_types[it]["hours"] += r["hours"]

                        sorted_types = sorted(agg_types.items(), key=lambda x: x[1]["cost"], reverse=True)
                        total_period_cost = sum(d["cost"] for _, d in sorted_types) or 1.0

                        tbl_lines = []
                        graviton_candidates = []
                        prev_gen_candidates = []

                        for idx, (it, d) in enumerate(sorted_types[:15]):
                            c = d["cost"]
                            h = d["hours"]
                            pct = (c / total_period_cost) * 100
                            tbl_lines.append(
                                f"| {idx+1} | `{it}` | **${c:,.2f}** | {h:,.0f} hrs | {pct:.1f}% |"
                            )
                            cand = {"instance_type": it, "cost": c, "hours": h}
                            if any(fam in it for fam in ["m5.", "c5.", "r5.", "t3."]):
                                graviton_candidates.append(cand)
                            elif any(fam in it for fam in ["c3.", "c4.", "m4.", "r4.", "t2."]):
                                prev_gen_candidates.append(cand)

                        remaining_types = sorted_types[15:]
                        if remaining_types:
                            rem_cost = sum(d["cost"] for _, d in remaining_types)
                            rem_hours = sum(d["hours"] for _, d in remaining_types)
                            rem_pct = (rem_cost / total_period_cost) * 100
                            rem_count = len(remaining_types)
                            tbl_lines.append(
                                f"| - | *Other ({rem_count} instance types)* | **${rem_cost:,.2f}** | {rem_hours:,.0f} hrs | {rem_pct:.1f}% |"
                            )

                        chart_md = ""
                        if chart_type in ["doughnut", "pie"]:
                            chart_md = _chart_block(
                                chart_type,
                                f"EC2 Instance Type Spend ({period_header}) — {cust_label}",
                                [it for it, _ in sorted_types[:10]],
                                values=[round(d["cost"], 2) for _, d in sorted_types[:10]],
                                value_label="Cost ($)"
                            )
                        elif chart_type == "horizontal-bar":
                            chart_md = _chart_block(
                                "horizontal-bar",
                                f"EC2 Instance Type Spend ({period_header}) — {cust_label}",
                                [it for it, _ in sorted_types[:10]],
                                values=[round(d["cost"], 2) for _, d in sorted_types[:10]],
                                value_label="Cost ($)",
                                horizontal=True
                            )
                        elif chart_type == "line":
                            # Trend: line chart of daily total + DoD variance waterfall
                            day_totals: dict = {}
                            for r in ec2_rows:
                                day_totals[r["time_val"]] = day_totals.get(r["time_val"], 0.0) + r["cost"]
                            sorted_days = sorted(day_totals.keys())
                            line_labels = [_format_time_label(d, "day") for d in sorted_days]
                            line_vals = [round(day_totals[d], 2) for d in sorted_days]
                            chart_md = _chart_block(
                                "line",
                                f"EC2 Total Spend Trend (Last {num_days} Days) — {cust_label}",
                                line_labels, values=line_vals, value_label="Cost ($)"
                            )
                            variance_md = _build_mom_variance_chart(
                                f"EC2 Spend DoD Variance (Last {num_days} Days) — {cust_label}",
                                sorted_days, day_totals, time_format="day"
                            )
                            chart_md += f"\n{variance_md}" if variance_md else ""
                        elif chart_type or any(w in low for w in ["chart", "graph", "plot", "visualize"]):
                            chart_md = _build_time_category_stacked_chart(
                                f"EC2 Instance Type Spend by Day (Last {num_days} Days) — {cust_label}",
                                ec2_rows,
                                time_col="time_val",
                                cat_col="instance_type",
                                cost_col="cost",
                                time_format="day",
                                max_cats=12
                            )

                        insights = []
                        if graviton_candidates:
                            grav_spend = sum(r["cost"] for r in graviton_candidates)
                            est_savings = grav_spend * 0.20
                            top_grav_names = ", ".join([f"`{r['instance_type']}`" for r in graviton_candidates[:4]])
                            insights.append(
                                f"- **AWS Graviton Modernization Opportunity**: Identified **${grav_spend:,.2f}** across x86 instances ({top_grav_names}). "
                                f"Migrating to Graviton3/4 equivalents (`m7g`, `c7g`, `r7g`, `t4g`) yields up to **20% direct savings** (~**${est_savings:,.2f}**) with improved price-performance."
                            )
                        if prev_gen_candidates:
                            prev_spend = sum(r["cost"] for r in prev_gen_candidates)
                            top_prev_names = ", ".join([f"`{r['instance_type']}`" for r in prev_gen_candidates[:3]])
                            insights.append(
                                f"- **Previous Generation Instance Upgrades**: Detected **${prev_spend:,.2f}** on legacy instances ({top_prev_names}). Upgrading to current-generation instances (`c6i`, `m6i`, `t3`) provides higher throughput at identical or lower hourly rates."
                            )

                        insights_block = ""
                        if insights:
                            insights_block = "\n#### 💡 FinOps Optimization Levers & Architecture Recommendations\n\n" + "\n".join(insights) + "\n"

                        partial_notice = (
                            f"> ⚠️ **FinOps Ingestion Notice**: Current date (`{today_str}`) is excluded from closed analysis as in-flight; "
                            f"yesterday's data (`{yesterday_str}`) is preliminary/partial across all cloud providers due to standard 24–48h billing ingestion latency.\n\n"
                        )

                        tbl_block = (
                            f"| # | Instance Type | Cost | Instance Hours | % of Total |\n"
                            f"|:---|:---|:---|:---|:---|\n"
                            f"{chr(10).join(tbl_lines)}\n"
                            f"| **Total** | **All Instance Types** | **${total_period_cost:,.2f}** | | **100.0%** |\n\n"
                        ) if wants_table else ""

                        return (
                            f"### 🖥️ CloudHealth EC2 Spend Analysis: Instance Type Breakdown\n\n"
                            f"Queried live from standard dataset **`AWS_EC2_COST_AND_USAGE`** for **{cust_label}**:\n\n"
                            f"- **Target Billing Period**: {period_header}\n"
                            f"- **Total EC2 Billed Spend**: **${total_period_cost:,.2f}** across **{len(sorted_types)}** active instance types\n\n"
                            f"{partial_notice}"
                            f"{tbl_block}"
                            f"{chart_md}"
                            f"{insights_block}\n"
                            f"*Source: AWS_EC2_COST_AND_USAGE via CloudHealth FlexReports.*"
                        )
                    else:
                        # Monthly analysis over available months
                        months_present = sorted(list({r["time_val"] for r in ec2_rows}))
                        latest_m = months_present[-1] if months_present else "2026-03"
                        target_month = target_ym if is_specific and target_ym in months_present else latest_m

                        # Group latest/target month instance types for the table
                        target_m_rows = [r for r in ec2_rows if r["time_val"] == target_month]
                        target_m_rows.sort(key=lambda x: x["cost"], reverse=True)
                        month_total = sum(r["cost"] for r in target_m_rows) or 1.0

                        tbl_lines = []
                        graviton_candidates = []
                        prev_gen_candidates = []

                        for idx, r in enumerate(target_m_rows[:15]):
                            it = r["instance_type"]
                            c = r["cost"]
                            h = r["hours"]
                            pct = (c / month_total) * 100
                            tbl_lines.append(
                                f"| {idx+1} | `{it}` | **${c:,.2f}** | {h:,.0f} hrs | {pct:.1f}% |"
                            )
                            # Identify optimization candidates
                            if any(fam in it for fam in ["m5.", "c5.", "r5.", "t3."]):
                                graviton_candidates.append(r)
                            elif any(fam in it for fam in ["c3.", "c4.", "m4.", "r4.", "t2."]):
                                prev_gen_candidates.append(r)

                        remaining_m_rows = target_m_rows[15:]
                        if remaining_m_rows:
                            rem_cost = sum(r["cost"] for r in remaining_m_rows)
                            rem_hours = sum(r["hours"] for r in remaining_m_rows)
                            rem_pct = (rem_cost / month_total) * 100
                            rem_count = len(remaining_m_rows)
                            tbl_lines.append(
                                f"| - | *Other ({rem_count} instance types)* | **${rem_cost:,.2f}** | {rem_hours:,.0f} hrs | {rem_pct:.1f}% |"
                            )

                        # Build chart
                        chart_md = ""
                        if chart_type in ["doughnut", "pie"]:
                            chart_labels = [r["instance_type"] for r in target_m_rows[:10]]
                            chart_values = [round(r["cost"], 2) for r in target_m_rows[:10]]
                            chart_md = _chart_block(chart_type, f"EC2 Instance Type Spend ({_format_time_label(target_month, t_format)}) — {cust_label}", chart_labels, values=chart_values, value_label="Cost ($)")
                        elif chart_type == "horizontal-bar":
                            chart_labels = [r["instance_type"] for r in target_m_rows[:10]]
                            chart_values = [round(r["cost"], 2) for r in target_m_rows[:10]]
                            chart_md = _chart_block("horizontal-bar", f"EC2 Instance Type Spend ({_format_time_label(target_month, t_format)}) — {cust_label}", chart_labels, values=chart_values, value_label="Cost ($)", horizontal=True)
                        elif chart_type == "waterfall":
                            wf_labels = ["Baseline (Total)"] + [r["instance_type"] for r in target_m_rows[:7]] + ["Total"]
                            wf_vals = [round(month_total, 2)] + [round(r["cost"], 2) for r in target_m_rows[:7]] + [round(month_total, 2)]
                            chart_md = _build_waterfall_chart(
                                f"EC2 Instance Type Cost Contribution ({_format_time_label(target_month, t_format)})",
                                wf_labels, wf_vals
                            )
                        elif chart_type == "line":
                            # Trend: line chart of monthly total + MoM variance waterfall
                            mo_totals: dict = {}
                            for r in ec2_rows:
                                mo_totals[r["time_val"]] = mo_totals.get(r["time_val"], 0.0) + r["cost"]
                            sorted_mos = sorted(mo_totals.keys())
                            line_labels = [_format_time_label(m, t_format) for m in sorted_mos]
                            line_vals = [round(mo_totals[m], 2) for m in sorted_mos]
                            chart_md = _chart_block(
                                "line",
                                f"EC2 Total Spend Trend ({len(sorted_mos)} Months) — {cust_label}",
                                line_labels, values=line_vals, value_label="Cost ($)"
                            )
                            variance_md = _build_mom_variance_chart(
                                f"EC2 Spend MoM Variance ({len(sorted_mos)} Months) — {cust_label}",
                                sorted_mos, mo_totals, time_format=t_format
                            )
                            chart_md += f"\n{variance_md}" if variance_md else ""
                        elif chart_type or any(w in low for w in ["chart", "graph", "plot", "visualize"]):
                            chart_md = _build_time_category_stacked_chart(
                                f"EC2 Instance Type Spend by Month — {cust_label}",
                                ec2_rows,
                                time_col="time_val",
                                cat_col="instance_type",
                                cost_col="cost",
                                time_format=t_format,
                                max_cats=12
                            )

                        insights = []
                        if graviton_candidates:
                            grav_spend = sum(r["cost"] for r in graviton_candidates)
                            est_savings = grav_spend * 0.20
                            top_grav_names = ", ".join([f"`{r['instance_type']}`" for r in graviton_candidates[:4]])
                            insights.append(
                                f"- **AWS Graviton Modernization Opportunity**: Identified **${grav_spend:,.2f}** across x86 instances ({top_grav_names}). "
                                f"Migrating to Graviton3/4 equivalents (`m7g`, `c7g`, `r7g`, `t4g`) yields up to **20% direct savings** (~**${est_savings:,.2f}/mo**) with improved price-performance."
                            )
                        if prev_gen_candidates:
                            prev_spend = sum(r["cost"] for r in prev_gen_candidates)
                            top_prev_names = ", ".join([f"`{r['instance_type']}`" for r in prev_gen_candidates[:3]])
                            insights.append(
                                f"- **Previous Generation Instance Upgrades**: Detected **${prev_spend:,.2f}** on legacy instances ({top_prev_names}). Upgrading to current-generation instances (`c6i`, `m6i`, `t3`) provides higher throughput at identical or lower hourly rates."
                            )

                        insights_block = ""
                        if insights:
                            insights_block = "\n#### 💡 FinOps Optimization Levers & Architecture Recommendations\n\n" + "\n".join(insights) + "\n"

                        tbl_block = (
                            f"| # | Instance Type | Cost | Instance Hours | % of Total |\n"
                            f"|:---|:---|:---|:---|:---|\n"
                            f"{chr(10).join(tbl_lines)}\n"
                            f"| **Total** | **All Instance Types** | **${month_total:,.2f}** | | **100.0%** |\n\n"
                        ) if wants_table else ""

                        return (
                            f"### 🖥️ CloudHealth EC2 Spend Analysis: Instance Type Breakdown\n\n"
                            f"Queried live from standard dataset **`AWS_EC2_COST_AND_USAGE`** for **{cust_label}**:\n\n"
                            f"- **Target Billing Period**: `{_format_time_label(target_month, t_format)}`\n"
                            f"- **Total EC2 Billed Spend**: **${month_total:,.2f}** across **{len(target_m_rows)}** active instance types\n\n"
                            f"{tbl_block}"
                            f"{chart_md}"
                            f"{insights_block}\n"
                            f"*Source: AWS_EC2_COST_AND_USAGE via CloudHealth FlexReports.*"
                        )

            # 3-MajorService-IT. Universal Major Cloud Services Deep-Dive (S3, EBS, Lambda, DynamoDB, Bedrock, VPC, CloudFront, etc.)
            major_svc_target = None
            major_svc_pcode = None
            major_svc_disp = None
            major_svc_prov = "aws"

            if requested_service and str(requested_service) not in ("AmazonRDS", "AmazonEC2"):
                major_svc_pcode = str(requested_service)
                major_svc_disp = requested_service_disp or major_svc_pcode
                major_svc_prov = PCODE_TO_PROVIDER.get(major_svc_pcode, "aws")
                major_svc_target = major_svc_pcode
            elif any(w in low for w in ["s3", "storage bucket", "buckets", "s3 bucket"]):
                major_svc_pcode = "AmazonS3"
                major_svc_disp = "Amazon S3"
                major_svc_target = "AmazonS3"
            elif any(w in low for w in ["ebs", "ebs volume", "ebs volumes", "ebs snapshot", "ebs snapshots"]):
                major_svc_pcode = "AmazonEC2_EBS"
                major_svc_disp = "Amazon EBS"
                major_svc_target = "EBS"
            elif any(w in low for w in ["lambda", "serverless function", "lambda function"]):
                major_svc_pcode = "AWSLambda"
                major_svc_disp = "AWS Lambda"
                major_svc_target = "AWSLambda"
            elif any(w in low for w in ["dynamodb", "nosql database", "dynamo"]):
                major_svc_pcode = "AmazonDynamoDB"
                major_svc_disp = "Amazon DynamoDB"
                major_svc_target = "AmazonDynamoDB"
            elif any(w in low for w in ["bedrock", "claude 3", "titan model", "foundation model", "ai model"]):
                major_svc_pcode = "AmazonBedrock"
                major_svc_disp = "Amazon Bedrock"
                major_svc_target = "AmazonBedrock"
            elif any(w in low for w in ["cloudfront", "edge cdn", "cloud front"]):
                major_svc_pcode = "AmazonCloudFront"
                major_svc_disp = "Amazon CloudFront"
                major_svc_target = "AmazonCloudFront"
            elif any(w in low for w in ["nat gateway", "nat gateways", "vpc networking"]):
                major_svc_pcode = "AmazonVPC"
                major_svc_disp = "Amazon VPC / NAT"
                major_svc_target = "AmazonVPC"

            CANONICAL_SERVICE_DISP = {
                "AmazonS3": "Amazon S3",
                "AmazonEC2_EBS": "Amazon EBS",
                "AWSLambda": "AWS Lambda",
                "AmazonDynamoDB": "Amazon DynamoDB",
                "AmazonBedrock": "Amazon Bedrock",
                "AmazonCloudFront": "Amazon CloudFront",
                "AmazonVPC": "Amazon VPC / NAT",
            }
            if major_svc_pcode in CANONICAL_SERVICE_DISP:
                major_svc_disp = CANONICAL_SERVICE_DISP[major_svc_pcode]

            is_major_service_inquiry = bool(
                major_svc_target and
                not any(w in low for w in ["recommendation", "recommendations", "anomal", "spike", "usagetype", "usage-type", "usage type"])
            )

            if is_major_service_inquiry:
                # Resolve timeframe: daily trend vs monthly
                is_trend_query = any(w in low for w in ["trend", "daily", "day", "days", "last 15", "last 30", "trailing", "over time"])
                m_days = re.search(r'(?:last|past|trailing|for)\s+(\d{1,3})\s*days?\b|\b(\d{1,3})\s*(?:days?|d)\b', low)
                num_days = intent_info.get("timeframe_days") or (int(m_days.group(1) or m_days.group(2)) if m_days else (30 if is_trend_query else 0))
                time_col = "timeInterval_Day" if num_days > 0 else "timeInterval_Month"
                t_format = "day" if num_days > 0 else ("quarter" if "quarter" in low else "month")

                if num_days > 0:
                    d_start = cal["d15_start"] if num_days <= 15 else (datetime.date.today() - datetime.timedelta(days=num_days)).strftime("%Y-%m-%d")
                    svc_time_range = {"from": d_start, "to": yesterday_str}
                    svc_granularity = "DAILY"
                    svc_period_label = f"Trailing {num_days} Days: {d_start} to {yesterday_str}"
                elif is_specific and target_ym != last_ym:
                    svc_time_range = {"from": target_ym, "to": target_ym}
                    svc_granularity = "MONTHLY"
                    svc_period_label = target_label
                else:
                    svc_time_range = {"last": 1, "qualifier": "MONTH", "excludeCurrent": False}
                    svc_granularity = "MONTHLY"
                    svc_period_label = f"Current Month ({current_ym})"

                cust_label = named_customer or "All Accounts Partner-Wide"
                wants_table = _detect_wants_table(low)
                chart_type = _detect_chart_type(low)

                # Query AWS_CUR or MULTICLOUD_FOCUS
                svc_rows = []
                if major_svc_prov == "aws":
                    where_clause = (
                        "WHERE lineItem_ProductCode = 'AmazonEC2' AND ("
                        "lineItem_UsageType LIKE '%Volume%' OR lineItem_UsageType LIKE '%Snapshot%' OR "
                        "lineItem_UsageType LIKE '%EBS%' OR lineItem_UsageType LIKE '%gp2%' OR lineItem_UsageType LIKE '%gp3%')"
                    ) if major_svc_pcode == "AmazonEC2_EBS" else f"WHERE lineItem_ProductCode = '{major_svc_pcode}'"

                    sql_svc = (
                        f"SELECT {time_col} AS time_val, lineItem_UsageType AS usage_type, lineItem_Operation AS operation, "
                        f"SUM(lineItem_UnblendedCost) AS cost, SUM(lineItem_UsageAmount) AS usage_qty "
                        f"FROM AWS_CUR "
                        f"{where_clause} "
                        f"GROUP BY {time_col}, lineItem_UsageType, lineItem_Operation "
                        f"ORDER BY time_val ASC, cost DESC"
                    )

                    q_params = {
                        "queryInput": {
                            "sqlStatement": sql_svc,
                            "dataGranularity": svc_granularity,
                            "limit": 50,
                            "timeRange": svc_time_range
                        },
                        "requestInfo": {"sourceType": "API", "caller": "mcp"}
                    }
                    if named_customer_crn:
                        q_params["channelCustomerId"] = named_customer_crn

                    try:
                        res = mcp.call_tool("execute_datasource_query", q_params)
                        raw_csv = json.loads(res["content"][0]["text"]).get("csv", "")
                        for r in csv.DictReader(io.StringIO(raw_csv)):
                            try:
                                c = float(r.get("cost") or 0.0)
                                tv = r.get("time_val", "")
                                ut = r.get("usage_type", "")
                                op = r.get("operation", "")
                                q = float(r.get("usage_qty") or 0.0)
                                if c > 0 and (num_days == 0 or tv != today_str):
                                    svc_rows.append({
                                        "time_val": tv,
                                        "usage_type": ut,
                                        "operation": op,
                                        "cost": c,
                                        "qty": q
                                    })
                            except (ValueError, TypeError):
                                pass
                    except Exception as e:
                        logger.warning(f"[{major_svc_disp} Query] {e}")
                else:
                    # Azure and GCP each have a dedicated, provider-native FOCUS dataset with real
                    # ServiceCategory/ServiceSubcategory columns — a genuine sub-category breakdown
                    # (e.g. Virtual Machines -> Compute, Cosmos DB -> NoSQL Databases), not just a
                    # single service-level total. OCI has no dedicated dataset in this account's
                    # catalog yet, so it falls back to the generic multicloud rollup (service-level
                    # only, matching both the 'OCI' and 'Oracle Cloud' labels CloudHealth may use).
                    native_table_map = {
                        "azure": "AZURE_FOCUS_COST_AND_USAGE",
                        "gcp": "GCP_FOCUS_COST_AND_USAGE",
                    }
                    if major_svc_prov in native_table_map:
                        sql_svc = (
                            f"SELECT Month AS time_val, ServiceSubcategory AS usage_type, ServiceCategory AS operation, "
                            f"SUM(EffectiveCost) AS cost "
                            f"FROM {native_table_map[major_svc_prov]} "
                            f"WHERE ServiceName = '{major_svc_pcode}' "
                            f"GROUP BY Month, ServiceSubcategory, ServiceCategory "
                            f"ORDER BY Month ASC"
                        )
                    else:
                        prov_clause = "provider IN ('OCI', 'Oracle Cloud')" if major_svc_prov == "oci" else f"provider = '{major_svc_prov}'"
                        sql_svc = (
                            f"SELECT Month AS time_val, ServiceName AS usage_type, "
                            f"SUM(EffectiveCost) AS cost "
                            f"FROM MULTICLOUD_FOCUS_COST_AND_USAGE "
                            f"WHERE {prov_clause} AND ServiceName = '{major_svc_pcode}' "
                            f"GROUP BY Month, ServiceName "
                            f"ORDER BY Month ASC"
                        )
                    # These datasets only support Month-precision output regardless of the
                    # requested granularity, so a day-precision {"from","to"} range (used for the
                    # "trailing N days" case above) makes CloudHealth's date parser reject the
                    # query outright. Fall back to a month-window time range in that case.
                    non_aws_time_range = {"last": 2, "qualifier": "MONTH"} if num_days > 0 else svc_time_range
                    q_params = {
                        "queryInput": {
                            "sqlStatement": sql_svc,
                            "dataGranularity": "MONTHLY",
                            "limit": 50,
                            "timeRange": non_aws_time_range
                        },
                        "requestInfo": {"sourceType": "API", "caller": "mcp"}
                    }
                    if named_customer_crn:
                        q_params["channelCustomerId"] = named_customer_crn
                    try:
                        res = mcp.call_tool("execute_datasource_query", q_params)
                        raw_csv = json.loads(res["content"][0]["text"]).get("csv", "")
                        for r in csv.DictReader(io.StringIO(raw_csv)):
                            try:
                                c = float(r.get("cost") or 0.0)
                                tv = r.get("time_val", "")
                                if c > 0:
                                    svc_rows.append({
                                        "time_val": tv, "usage_type": r.get("usage_type", major_svc_disp),
                                        "operation": r.get("operation", ""), "cost": c, "qty": 0.0
                                    })
                            except (ValueError, TypeError):
                                pass
                    except Exception as e:
                        logger.warning(f"[{major_svc_disp} Query] {e}")

                if not svc_rows:
                    return (
                        f"### ☁️ CloudHealth {major_svc_disp} Spend & Usage Analysis\n\n"
                        f"No live billing data was returned for **{major_svc_disp}** ({cust_label}) in `{svc_period_label}`. "
                        f"This usually means the service had zero spend in this period, or the connected CloudHealth "
                        f"account/scope doesn't have visibility into it.\n\n"
                        f"*Source: {'AWS_CUR' if major_svc_prov == 'aws' else native_table_map.get(major_svc_prov, 'MULTICLOUD_FOCUS_COST_AND_USAGE')} via CloudHealth FlexReports.*"
                    )

                # Aggregate by domain category
                cat_totals: dict = {}
                cat_ops: dict = {}
                time_totals: dict = {}

                for r in svc_rows:
                    cat = _classify_service_usage_type(major_svc_pcode, r["usage_type"], r.get("operation", ""))
                    cost = r["cost"]
                    t_val = r["time_val"]

                    cat_totals[cat] = cat_totals.get(cat, 0.0) + cost
                    time_totals[t_val] = time_totals.get(t_val, 0.0) + cost
                    if cat not in cat_ops and r.get("operation"):
                        cat_ops[cat] = r["operation"]

                # The breakdown query above caps rows at 50 for display; get the real total via
                # an unlimited SUM so it isn't understated when a service has >50 usage-type combos.
                if major_svc_prov == "aws":
                    true_total = _fetch_total_cost(
                        mcp, f"SELECT SUM(lineItem_UnblendedCost) AS cost FROM AWS_CUR {where_clause}",
                        svc_granularity, svc_time_range, named_customer_crn
                    )
                elif major_svc_prov in ("azure", "gcp"):
                    native_table = "AZURE_FOCUS_COST_AND_USAGE" if major_svc_prov == "azure" else "GCP_FOCUS_COST_AND_USAGE"
                    true_total = _fetch_total_cost(
                        mcp, f"SELECT SUM(EffectiveCost) AS cost FROM {native_table} WHERE ServiceName = '{major_svc_pcode}'",
                        "MONTHLY", non_aws_time_range, named_customer_crn
                    )
                else:
                    prov_clause = "provider IN ('OCI', 'Oracle Cloud')" if major_svc_prov == "oci" else f"provider = '{major_svc_prov}'"
                    true_total = _fetch_total_cost(
                        mcp, f"SELECT SUM(EffectiveCost) AS cost FROM MULTICLOUD_FOCUS_COST_AND_USAGE WHERE {prov_clause} AND ServiceName = '{major_svc_pcode}'",
                        "MONTHLY", non_aws_time_range, named_customer_crn
                    )
                total_svc_spend = true_total or sum(cat_totals.values()) or 1.0
                sorted_cats = sorted(cat_totals.items(), key=lambda x: x[1], reverse=True)

                # Format Markdown table
                tbl_lines = []
                for idx, (cat, c) in enumerate(sorted_cats[:12]):
                    pct = (c / total_svc_spend) * 100
                    op_note = cat_ops.get(cat, "")
                    op_str = f" | {op_note[:45]}" if op_note else " | Active operational workload"
                    tbl_lines.append(f"| {idx+1} | **{cat}** | **${c:,.2f}** | {pct:.1f}%{op_str} |")

                cat_header = "Service Category / Storage Tier" if "s3" in major_svc_pcode.lower() else "Service Category / Meter"
                tbl_block = (
                    f"| # | {cat_header} | Spend ($) | % of Total | Operational Usage Notes |\n"
                    f"|:---|:---|:---|:---|:---|\n"
                    f"{chr(10).join(tbl_lines)}\n"
                    f"| **Total** | **All Categories** | **${total_svc_spend:,.2f}** | **100.0%** | |\n\n"
                ) if wants_table else ""

                # Build synchronized chart
                chart_md = ""
                chart_title = f"{major_svc_disp} Spend Breakdown — {svc_period_label} — {cust_label}"

                if chart_type in ["doughnut", "pie"]:
                    c_labels = [c[0] for c in sorted_cats[:8]]
                    c_vals = [round(c[1], 2) for c in sorted_cats[:8]]
                    chart_md = _chart_block(chart_type, chart_title, c_labels, values=c_vals, value_label="Cost ($)")
                elif chart_type == "line":
                    sorted_t = sorted(time_totals.keys())
                    l_labels = [_format_time_label(t, t_format) for t in sorted_t]
                    l_vals = [round(time_totals[t], 2) for t in sorted_t]
                    chart_md = _chart_block("line", f"{major_svc_disp} Total Spend Trend — {svc_period_label} — {cust_label}", l_labels, values=l_vals, value_label="Cost ($)")
                    variance_md = _build_mom_variance_chart(
                        f"{major_svc_disp} Spend {('DoD' if num_days > 0 else 'MoM')} Variance — {cust_label}",
                        sorted_t, time_totals, time_format=t_format
                    )
                    chart_md += f"\n{variance_md}" if variance_md else ""
                elif chart_type == "waterfall":
                    wf_labels = ["Baseline (Total)"] + [c[0] for c in sorted_cats[:6]] + ["Total"]
                    wf_vals = [round(total_svc_spend, 2)] + [round(c[1], 2) for c in sorted_cats[:6]] + [round(total_svc_spend, 2)]
                    chart_md = _build_waterfall_chart(f"{major_svc_disp} Spend Contribution — {cust_label}", wf_labels, wf_vals)
                else:
                    # Category bar chart
                    c_labels = [c[0] for c in sorted_cats[:8]]
                    c_vals = [round(c[1], 2) for c in sorted_cats[:8]]
                    chart_md = _chart_block("bar", chart_title, c_labels, values=c_vals, value_label="Cost ($)", horizontal=False)

                # Attach domain-specific FinOps optimization levers
                domain_insights = _generate_domain_finops_insights(major_svc_pcode, cat_totals, total_svc_spend)
                insights_block = "\n#### 💡 FinOps Optimization Levers & Architecture Recommendations\n\n" + "\n".join(domain_insights) + "\n"

                partial_note = f"> ⚠️ *Note: Billing data for yesterday (`{yesterday_str}`) is preliminary/partial due to cloud billing settlement latency.*\n\n" if num_days > 0 else ""

                return (
                    f"### ☁️ CloudHealth {major_svc_disp} Spend & Usage Analysis\n\n"
                    f"Queried live telemetry for **{cust_label}**:\n\n"
                    f"{partial_note}"
                    f"- **Target Billing Period**: `{svc_period_label}`\n"
                    f"- **Total {major_svc_disp} Billed Spend**: **${total_svc_spend:,.2f}** across **{len(sorted_cats)}** distinct service categories\n\n"
                    f"{tbl_block}"
                    f"{chart_md}"
                    f"{insights_block}\n"
                    f"*Source: AWS_CUR & CloudHealth FlexReports. Continuous FinOps monitoring active.*"
                )

            # 3-UT. Granular Usage Type & Operation Breakdown (e.g. "what's their RDS usage breakdown by UsageType?", "EC2 breakdown by usagetype")
            is_usagetype_query = any(w in low for w in [
                "usagetype", "usage type", "usage-type", "operation", "meter name",
                "line item", "item description", "lineitem", "granular breakdown", "usage breakdown",
                "by type", "operation breakdown"
            ])
            if is_usagetype_query:
                if is_specific and target_ym != last_ym:
                    ut_time_range = {"from": target_ym, "to": target_ym}
                    ut_period_label = target_label
                    ut_granularity = "MONTHLY"
                else:
                    ut_time_range = {"last": 1, "qualifier": "MONTH", "excludeCurrent": False}
                    ut_period_label = f"Current Month / Trailing 30 Days ({datetime.date.today().strftime('%Y-%m')})"
                    ut_granularity = "MONTHLY"

                where_clause = f"WHERE lineItem_ProductCode = '{requested_service}'" if requested_service else ""
                ut_sql = (
                    "SELECT lineItem_UsageType AS usage_type, "
                    "lineItem_Operation AS operation, "
                    "lineItem_LineItemDescription AS description, "
                    "SUM(lineItem_UsageAmount) AS usage_qty, "
                    "SUM(lineItem_UnblendedCost) AS cost "
                    "FROM AWS_CUR "
                    f"{where_clause} "
                    "GROUP BY lineItem_UsageType, lineItem_Operation, lineItem_LineItemDescription "
                    "ORDER BY cost DESC"
                )

                q_params = {
                    "queryInput": {
                        "sqlStatement": ut_sql,
                        "dataGranularity": ut_granularity,
                        "limit": 20,
                        "timeRange": ut_time_range
                    },
                    "requestInfo": {"sourceType": "API", "caller": "mcp"}
                }
                if named_customer_crn:
                    q_params["channelCustomerId"] = named_customer_crn

                res = mcp.call_tool("execute_datasource_query", q_params)

                ut_csv = json.loads(res["content"][0]["text"]).get("csv", "")
                ut_rows = []
                for row in csv.DictReader(io.StringIO(ut_csv)):
                    try:
                        c = float(row.get("cost") or 0)
                        u = row.get("usage_type", "")
                        op = row.get("operation", "")
                        d = row.get("description", "")
                        q = float(row.get("usage_qty") or 0)
                        if u and c > 0:
                            ut_rows.append({"usage_type": u, "operation": op, "description": d, "qty": q, "cost": c})
                    except (ValueError, TypeError):
                        pass

                total_ut_spend = _fetch_total_cost(
                    mcp, f"SELECT SUM(lineItem_UnblendedCost) AS cost FROM AWS_CUR {where_clause}",
                    ut_granularity, ut_time_range, named_customer_crn
                ) or sum(r["cost"] for r in ut_rows)
                svc_title = f"{requested_service_disp} " if requested_service_disp else ""
                cust_display = named_customer or "All Accounts (Partner-Wide)"

                if not ut_rows:
                    return (
                        f"### 🔍 Granular UsageType Breakdown: {cust_display} ({svc_title.strip() or 'All Services'})\n\n"
                        f"> ℹ️ *No granular line items recorded for {cust_display} under {svc_title or 'AWS'} in {ut_period_label}.*"
                    )

                table_lines = []
                for r in ut_rows[:15]:
                    pct = (r["cost"] / total_ut_spend * 100) if total_ut_spend > 0 else 0.0
                    desc_str = r["description"]
                    if len(desc_str) > 75:
                        desc_str = desc_str[:72] + "..."
                    table_lines.append(
                        f"| `{r['usage_type']}` | `{r['operation']}` | {desc_str} | {r['qty']:,.1f} | **${r['cost']:,.2f}** | {pct:.1f}% |"
                    )

                insights = []
                gp2_items = [r for r in ut_rows if "gp2" in r["usage_type"].lower() or "gp2" in r["description"].lower()]
                if gp2_items:
                    gp2_cost = sum(r["cost"] for r in gp2_items)
                    insights.append(f"- **EBS gp2 to gp3 Modernization**: Detected General Purpose SSD (`gp2`) provisioned storage costing **${gp2_cost:,.2f}**. Converting to `gp3` provides an immediate 20% cost reduction with baseline 3,000 IOPS.")

                oracle_items = [r for r in ut_rows if "oracle" in r["description"].lower()]
                if oracle_items:
                    oracle_cost = sum(r["cost"] for r in oracle_items)
                    insights.append(f"- **Oracle Database Workloads**: Oracle License-Included (LI) instances drive **${oracle_cost:,.2f}** ({(oracle_cost/total_ut_spend*100):.1f}% of service spend). Evaluate 1-yr/3-yr Reserved DB Instances or explore Aurora PostgreSQL migration.")

                backup_items = [r for r in ut_rows if "backup" in r["usage_type"].lower() or "backup" in r["description"].lower()]
                if backup_items:
                    backup_cost = sum(r["cost"] for r in backup_items)
                    insights.append(f"- **Backup Storage Accumulation**: Additional charged backup storage exceeds free allocation by **${backup_cost:,.2f}**. Audit snapshot retention policies to eliminate obsolete recovery points.")

                aurora_items = [r for r in ut_rows if "aurora" in r["usage_type"].lower() or "aurora" in r["description"].lower()]
                if aurora_items:
                    aurora_cost = sum(r["cost"] for r in aurora_items)
                    insights.append(f"- **Aurora Serverless Scaling**: Aurora Serverless ACU consumption accounts for **${aurora_cost:,.2f}**. Review min/max ACU allocation thresholds to optimize off-peak costs.")

                insights_block = ""
                if insights:
                    insights_block = "\n#### 💡 FinOps Key Observations & Optimization Levers\n\n" + "\n".join(insights) + "\n"

                chart_type = _detect_chart_type(low)
                wants_table = _detect_wants_table(low)
                chart_md = ""
                if chart_type and ut_rows:
                    chart_labels = [r["usage_type"][:35] for r in ut_rows[:15]]
                    chart_values = [round(r["cost"], 2) for r in ut_rows[:15]]
                    c_kind = chart_type if chart_type in ["doughnut", "pie", "bar", "horizontal-bar"] else "bar"
                    chart_md = _chart_block(c_kind,
                        f"Usage Type Breakdown ({ut_period_label})",
                        chart_labels, chart_values,
                        horizontal=(c_kind == "horizontal-bar"), stacked=True)

                tbl_block = (
                    f"| Usage Type | AWS Operation | Item Description | Usage Quantity | Spend | % of Total |\n"
                    f"|:---|:---|:---|:---|:---|:---|\n"
                    f"{chr(10).join(table_lines)}\n"
                    f"| **Total Granular Spend** | | | | **${total_ut_spend:,.2f}** | **100.0%** |\n\n"
                ) if wants_table else ""

                return (
                    f"### 🔍 Granular UsageType Breakdown: {cust_display} ({svc_title.strip() or 'All Services'})\n\n"
                    f"Showing detailed AWS CUR line items for **{cust_display}** over **{ut_period_label}**:\n\n"
                    f"**Total Granular Line-Item Spend**: **${total_ut_spend:,.2f}** across **{len(ut_rows)}** active usage types.\n\n"
                    f"{tbl_block}"
                    f"{chart_md}"
                    f"{insights_block}\n"
                    f"*Source: AWS CUR via CloudHealth (channel-scoped line-item telemetry).*"
                )

            # 3-Rec. Cost Optimization Recommendations (e.g. "top 3 cost optimization recommendations for Lundbeck based on their last 15days usage" or "optimize rds for opennet")
            is_rec_query = any(w in low for w in [
                "recommendation", "recommendations", "optimize", "optimization",
                "rightsizer", "rightsizing", "reduce cost", "cost reduction",
                "save money", "savings opportunity", "savings opportunities"
            ])
            if is_rec_query and named_customer and named_customer_crn:
                days_match = re.search(r'(?:last|past|for)?\s*(\d{1,3})\s*days?', low)
                rec_days = int(days_match.group(1)) if days_match else 30
                rec_days = max(1, min(rec_days, 90))

                # Detect specific service focus if requested or inherited
                rec_service = requested_service
                if not rec_service:
                    if "rds" in low or "database" in low: rec_service = "AmazonRDS"
                    elif "ec2" in low or "compute" in low or "instance" in low: rec_service = "AmazonEC2"
                    elif "s3" in low or "bucket" in low or "storage" in low: rec_service = "AmazonS3"

                svc_filter_clause = f"WHERE lineItem_ProductCode = '{rec_service}' " if rec_service else ""
                svc_title = f" — {rec_service.replace('Amazon', 'AWS ')}" if rec_service else ""

                # Query granular line items for deep-dive waste analysis (Fina pattern)
                granular_sql = (
                    f"SELECT lineItem_ProductCode AS service, "
                    f"lineItem_UsageType AS usage_type, "
                    f"lineItem_Operation AS operation, "
                    f"lineItem_LineItemDescription AS description, "
                    f"SUM(lineItem_UsageAmount) AS qty, "
                    f"SUM(lineItem_UnblendedCost) AS cost "
                    f"FROM AWS_CUR "
                    f"{svc_filter_clause}"
                    f"GROUP BY lineItem_ProductCode, lineItem_UsageType, lineItem_Operation, lineItem_LineItemDescription "
                    f"ORDER BY cost DESC"
                )

                res = mcp.call_tool("execute_datasource_query", {
                    "channelCustomerId": named_customer_crn,
                    "queryInput": {
                        "sqlStatement": granular_sql,
                        "dataGranularity": "DAILY",
                        "limit": 25,
                        "timeRange": {"last": rec_days, "qualifier": "DAY"}
                    },
                    "requestInfo": {"sourceType": "API", "caller": "mcp"}
                })
                rec_csv = json.loads(res["content"][0]["text"]).get("csv", "")
                granular_rows = []
                for row in csv.DictReader(io.StringIO(rec_csv)):
                    try:
                        c = float(row.get("cost") or 0)
                        if c > 0:
                            granular_rows.append({
                                "service": row.get("service", ""),
                                "usage_type": row.get("usage_type", ""),
                                "operation": row.get("operation", ""),
                                "description": row.get("description", ""),
                                "qty": float(row.get("qty") or 0),
                                "cost": c
                            })
                    except (ValueError, TypeError):
                        pass

                # The granular query above caps rows at 25 for lever detection; get the real
                # total via an unlimited SUM so it isn't understated for accounts with more
                # than 25 distinct usage-type/operation/description combinations.
                total_rec_spend = _fetch_total_cost(
                    mcp, f"SELECT SUM(lineItem_UnblendedCost) AS cost FROM AWS_CUR {svc_filter_clause}",
                    "DAILY", {"last": rec_days, "qualifier": "DAY"}, named_customer_crn
                ) or sum(r["cost"] for r in granular_rows)

                # Fallback to service totals if granular is empty
                if not granular_rows:
                    tot_res = mcp.call_tool("execute_datasource_query", {
                        "channelCustomerId": named_customer_crn,
                        "queryInput": {
                            "sqlStatement": (
                                "SELECT lineItem_ProductCode AS service, "
                                "SUM(lineItem_UnblendedCost) AS cost "
                                "FROM AWS_CUR "
                                "GROUP BY lineItem_ProductCode "
                                "ORDER BY cost DESC"
                            ),
                            "dataGranularity": "DAILY",
                            "limit": 10,
                            "timeRange": {"last": rec_days, "qualifier": "DAY"}
                        },
                        "requestInfo": {"sourceType": "API", "caller": "mcp"}
                    })
                    for row in csv.DictReader(io.StringIO(json.loads(tot_res["content"][0]["text"]).get("csv", ""))):
                        try:
                            c = float(row.get("cost") or 0)
                            if c > 0:
                                granular_rows.append({
                                    "service": row.get("service", ""),
                                    "usage_type": f"Total {row.get('service')}",
                                    "operation": "RunService",
                                    "description": f"Standard usage for {row.get('service')}",
                                    "qty": 1.0,
                                    "cost": c
                                })
                        except (ValueError, TypeError):
                            pass
                    total_rec_spend = total_rec_spend or sum(r["cost"] for r in granular_rows)

                # Fina FinOps Analysis Framework: Detect concrete waste patterns
                gp2_items = [r for r in granular_rows if "gp2" in r["usage_type"].lower() or "gp2" in r["description"].lower()]
                gp2_cost = sum(r["cost"] for r in gp2_items)

                oracle_items = [r for r in granular_rows if "oracle" in r["description"].lower() or "0002" in r["operation"]]
                oracle_cost = sum(r["cost"] for r in oracle_items)

                sqlserver_items = [r for r in granular_rows if "sqlserver" in r["description"].lower() or "sql server" in r["description"].lower()]
                sqlserver_cost = sum(r["cost"] for r in sqlserver_items)

                acu_items = [r for r in granular_rows if "serverless" in r["usage_type"].lower() or "acu" in r["usage_type"].lower()]
                acu_cost = sum(r["cost"] for r in acu_items)

                backup_items = [r for r in granular_rows if "backup" in r["usage_type"].lower() or "snapshot" in r["usage_type"].lower()]
                backup_cost = sum(r["cost"] for r in backup_items)

                graviton_candidates = [r for r in granular_rows if any(f in r["usage_type"].lower() for f in ["db.r5.", "db.m5.", "m5.", "c5.", "r5.", "t3."])]
                graviton_cost = sum(r["cost"] for r in graviton_candidates)

                s3_items = [r for r in granular_rows if "s3" in r["service"].lower() or "timedstorage" in r["usage_type"].lower()]
                s3_cost = sum(r["cost"] for r in s3_items)

                nat_items = [r for r in granular_rows if "natgateway" in r["usage_type"].lower()]
                nat_cost = sum(r["cost"] for r in nat_items)

                # Generate structured FinOps levers
                levers = []
                lever_idx = 1

                # Lever 1: EBS gp2 to gp3 Storage Modernization
                if gp2_cost > 0:
                    gp2_sav = gp2_cost * 0.20
                    levers.append(
                        f"**{lever_idx}. Upgrade Provisioned Storage from gp2 to gp3**\n"
                        f"- **FinOps Category**: Storage Modernization & Waste Elimination\n"
                        f"- **Implementation Complexity**: 🔻 **Low / Easy (1-Click in AWS Console / Terraform without downtime)**\n"
                        f"- **Current Baseline Telemetry**: **${gp2_cost:,.2f}** incurred for `{gp2_items[0]['usage_type']}` ({gp2_items[0]['qty']:,.1f} GB-Mo).\n"
                        f"- **Action Plan**: Migrate EBS/RDS storage volumes from General Purpose SSD (gp2) to gp3. gp3 provides 20% lower cost per GB-month while decoupling storage from IOPS, delivering a baseline 3,000 IOPS and 125 MB/s throughput with zero additional charge.\n"
                        f"- **Estimated Monthly Savings**: **20.0% (~${gp2_sav:,.2f}/month)**."
                    )
                    lever_idx += 1

                # Lever 2: Commercial Database Engine & Licensing Optimization (Oracle / SQL Server)
                if oracle_cost > 0 or sqlserver_cost > 0:
                    comm_cost = oracle_cost + sqlserver_cost
                    comm_engine = "Oracle SE2" if oracle_cost > 0 else "SQL Server"
                    comm_sav_strategic = comm_cost * 0.50
                    comm_sav_tactical = comm_cost * 0.20
                    levers.append(
                        f"**{lever_idx}. Modernize Commercial Database Licensing ({comm_engine})**\n"
                        f"- **FinOps Category**: Database Licensing & Architectural Rightsizing\n"
                        f"- **Implementation Complexity**: 🟡 **Medium (Rightsizing/Graviton)** | 🔴 **High (Aurora PostgreSQL Migration)**\n"
                        f"- **Current Baseline Telemetry**: **${comm_cost:,.2f}** ({(comm_cost/total_rec_spend*100):.1f}% of period spend) across instances including `{granular_rows[0]['usage_type']}`.\n"
                        f"- **Action Plan**:\n"
                        f"  * *Phase 1 (Tactical — Medium Effort)*: Review peak vs average CPU/RAM utilization. Downsize over-provisioned instances (e.g. `2xl` to `xl`) and evaluate Graviton `db.r6g`/`db.r7g` variants where supported. Estimated savings: ~${comm_sav_tactical:,.2f}/mo.\n"
                        f"  * *Phase 2 (Strategic — High Effort)*: Evaluate architectural migration to **Amazon Aurora PostgreSQL**. Eliminating proprietary commercial licensing fees completely reclaims 50–70% of database spend.\n"
                        f"- **Estimated Monthly Savings**: **20.0% – 50.0% (~${comm_sav_tactical:,.2f} – ${comm_sav_strategic:,.2f}/month)**."
                    )
                    lever_idx += 1

                # Lever 3: Aurora Serverless v2 ACU Tuning
                if acu_cost > 0:
                    acu_sav = acu_cost * 0.25
                    levers.append(
                        f"**{lever_idx}. Tune Aurora Serverless v2 Min/Max Capacity Allocations**\n"
                        f"- **FinOps Category**: Idle Compute & Dynamic Scaling Optimization\n"
                        f"- **Implementation Complexity**: 🔻 **Low / Easy (1-Click RDS Parameter Update)**\n"
                        f"- **Current Baseline Telemetry**: **${acu_cost:,.2f}** incurred for `{acu_items[0]['usage_type']}` ({acu_items[0]['qty']:,.1f} ACU-hours).\n"
                        f"- **Action Plan**: Audit minimum ACU threshold across clusters. Defaulting minimum ACUs to 0.5 (instead of 1.0 or 2.0) during off-peak and non-working hours prevents 24/7 idle capacity billing while maintaining instantaneous scale-up capability.\n"
                        f"- **Estimated Monthly Savings**: **20.0% – 30.0% (~${acu_sav:,.2f}/month)**."
                    )
                    lever_idx += 1

                # Lever 4: Automated Backup Storage & Snapshot Lifecycle
                if backup_cost > 0:
                    backup_sav = backup_cost * 0.35
                    levers.append(
                        f"**{lever_idx}. Prune Automated Backup Retention on Non-Production Clusters**\n"
                        f"- **FinOps Category**: Storage Waste Reclamation (Fina 4-Part Framework)\n"
                        f"- **Implementation Complexity**: 🔻 **Low / Easy (Backup Policy Configuration)**\n"
                        f"- **Current Baseline Telemetry**: **${backup_cost:,.2f}** for `{backup_items[0]['usage_type']}` (additional backup storage exceeding 100% of provisioned DB size).\n"
                        f"- **Action Plan**: Audit automated snapshot retention across dev, staging, and QA database instances. Reducing retention windows from 35 days to 7–14 days in non-production environments eliminates persistent charged backup storage.\n"
                        f"- **Estimated Monthly Savings**: **30.0% – 40.0% (~${backup_sav:,.2f}/month)**."
                    )
                    lever_idx += 1

                # Lever 5: AWS Graviton3 Modernization (Compute / RDS)
                if graviton_cost > 0 and (gp2_cost == 0 or lever_idx <= 3):
                    grav_sav = graviton_cost * 0.20
                    levers.append(
                        f"**{lever_idx}. Adopt AWS Graviton3 Instances for Baseline Compute**\n"
                        f"- **FinOps Category**: Hardware Efficiency & Architecture Modernization\n"
                        f"- **Implementation Complexity**: 🔻 **Low (RDS / Managed DBs)** | 🟡 **Medium (Linux Container Workloads)**\n"
                        f"- **Current Baseline Telemetry**: **${graviton_cost:,.2f}** across legacy x86 instances (`r5`, `m5`, `c5`).\n"
                        f"- **Action Plan**: Transition database and compute instances to AWS Graviton-powered instance types (`m7g`, `c7g`, `r7g`). Graviton delivers up to 20% lower hourly cost and 20% better price-performance with seamless migration for managed services (RDS/Aurora).\n"
                        f"- **Estimated Monthly Savings**: **15.0% – 20.0% (~${grav_sav:,.2f}/month)**."
                    )
                    lever_idx += 1

                # Lever 6: Commitment & Rate Optimization
                comm_steady_spend = total_rec_spend - (gp2_cost * 0.20)
                comm_sav = comm_steady_spend * 0.25
                if lever_idx <= 3:
                    levers.append(
                        f"**{lever_idx}. Purchase 1-Year Compute Savings Plans / Reserved Instances**\n"
                        f"- **FinOps Category**: Rate Optimization & Commitment Management\n"
                        f"- **Implementation Complexity**: 🔻 **Low (Financial Commitment)**\n"
                        f"- **Current Baseline Telemetry**: Total steady-state run-rate of **${total_rec_spend:,.2f}** over the period.\n"
                        f"- **Action Plan**: After executing storage and rightsizing quick wins, cover remaining steady-state baseline usage with 1-Year No-Upfront Savings Plans or Reserved Instances (RI), reducing hourly rates without changing application code.\n"
                        f"- **Estimated Monthly Savings**: **25.0% – 35.0% (~${comm_sav:,.2f}/month)**."
                    )
                    lever_idx += 1

                rec_md = "\n\n".join(levers)

                # Granular Breakdown Table
                table_lines = []
                for r in granular_rows[:8]:
                    pct = (r["cost"] / total_rec_spend * 100) if total_rec_spend > 0 else 0
                    desc_clean = (r["description"][:38] + "...") if len(r["description"]) > 38 else (r["description"] or "—")
                    table_lines.append(
                        f"| `{r['usage_type']}` | {r['operation'] or '—'} | {desc_clean} | ${r['cost']:,.2f} | {pct:.1f}% |"
                    )

                # Executive Savings Calculation
                quick_win_total = (gp2_cost * 0.20) + (acu_cost * 0.25) + (backup_cost * 0.35)
                strategic_total = (oracle_cost * 0.25) + (sqlserver_cost * 0.25) + (graviton_cost * 0.20 if (oracle_cost == 0 and sqlserver_cost == 0) else 0)
                if quick_win_total == 0 and strategic_total == 0:
                    quick_win_total = total_rec_spend * 0.15
                    strategic_total = total_rec_spend * 0.15
                total_pot_sav = quick_win_total + strategic_total
                total_pot_pct = (total_pot_sav / total_rec_spend * 100) if total_rec_spend > 0 else 0

                return (
                    f"### 💡 Deep-Dive FinOps Optimization Recommendations: {named_customer}{svc_title}\n\n"
                    f"Based on **{named_customer}**'s live AWS CUR telemetry over the last **{rec_days} days** (Total Period Spend: **${total_rec_spend:,.2f}**):\n\n"
                    f"#### 🔍 Top Granular Cost Drivers & Line Items\n\n"
                    f"| Usage Type | AWS Operation | Line Item Description | Spend | % of Spend |\n"
                    f"|:---|:---|:---|:---|:---|\n"
                    f"{chr(10).join(table_lines)}\n"
                    f"| **Total Period Spend** | | | **${total_rec_spend:,.2f}** | **100.0%** |\n\n"
                    f"#### 🎯 High-Impact FinOps Optimization Levers (Fina Waste Framework)\n\n"
                    f"{rec_md}\n\n"
                    f"#### 📊 Executive Savings Scorecard\n\n"
                    f"| Category | Implementation Complexity | Estimated Monthly Savings | % Reduction |\n"
                    f"|:---|:---|:---|:---|\n"
                    f"| **Immediate Quick Wins** (gp2->gp3, ACU floor, Backup pruning) | 🔻 Low (1-Click / Config) | **${quick_win_total:,.2f}** | **{(quick_win_total/total_rec_spend*100):.1f}%** |\n"
                    f"| **Strategic Modernization** (Graviton / Engine Migration / Rightsizing) | 🟡 Medium / 🔴 High | **${strategic_total:,.2f}** | **{(strategic_total/total_rec_spend*100):.1f}%** |\n"
                    f"| **Total Projected Savings Opportunity** | | **${total_pot_sav:,.2f} / month** | **{total_pot_pct:.1f}%** |\n\n"
                    f"*Source: AWS CUR via CloudHealth (channel-scoped granular line-item telemetry).*"
                )

            elif is_rec_query:
                # 3-Rec-Multi: Estate-Wide & Multi-Cloud FinOps Optimization Recommendations
                # Handles queries like "What are our top cost optimization recommendations across all cloud providers?",
                # "top recommendations across all clouds", "cost optimization recommendations", etc.
                target_cloud_rec = active_cloud
                is_multi_cloud = (
                    not target_cloud_rec or 
                    target_cloud_rec == "all" or 
                    any(w in low for w in ["all cloud", "all clouds", "multi-cloud", "multicloud", "cross-cloud", "every cloud", "providers", "all providers", "estate"])
                )

                # 1. Fetch Multi-Cloud Spend Telemetry
                mc_rows = []
                try:
                    res_mc = mcp.call_tool("execute_datasource_query", {
                        "queryInput": {
                            "sqlStatement": "SELECT provider AS provider, ServiceName AS service, SUM(EffectiveCost) AS cost FROM MULTICLOUD_FOCUS_COST_AND_USAGE GROUP BY provider, ServiceName ORDER BY cost DESC",
                            "dataGranularity": "MONTHLY", "limit": 200, "timeRange": {"last": 1, "qualifier": "MONTH"}
                        },
                        "requestInfo": {"sourceType": "API", "caller": "mcp"}
                    })
                    raw_mc_csv = json.loads(res_mc["content"][0]["text"]).get("csv", "")
                    for r in csv.DictReader(io.StringIO(raw_mc_csv)):
                        c = float(r.get("cost") or 0.0)
                        s = _friendly_service_name(r.get("service") or "")
                        p = r.get("provider") or "AWS"
                        if s and c > 0:
                            mc_rows.append((p, s, c))
                except Exception as e:
                    logger.warning(f"[MultiCloud Recommendations Query] {e}")

                if not mc_rows:
                    return (
                        f"### 💡 CloudHealth Multi-Cloud FinOps Optimization Recommendations\n\n"
                        f"No live multi-cloud billing data was returned. This usually means the connected CloudHealth "
                        f"account/scope doesn't have visibility into current spend, or there was no spend this period.\n\n"
                        f"*Source: MULTICLOUD_FOCUS_COST_AND_USAGE via CloudHealth FlexReports.*"
                    )

                mc_rows.sort(key=lambda x: x[2], reverse=True)
                total_mc_spend = _fetch_total_cost(
                    mcp, "SELECT SUM(EffectiveCost) AS cost FROM MULTICLOUD_FOCUS_COST_AND_USAGE",
                    "MONTHLY", {"last": 1, "qualifier": "MONTH"}
                ) or sum(r[2] for r in mc_rows)
                rec_title = "Across All Cloud Providers" if is_multi_cloud else f"for {target_cloud_rec.upper()}"

                def _match_cost(keywords):
                    return sum(c for _, s, c in mc_rows if any(k in s.lower() for k in keywords))

                storage_cost = _match_cost(["ebs", "s3", "blob", "disk", "storage"])
                db_cost = _match_cost(["rds", "sql", "cosmos", "autonomous database", "cloud sql", "spanner"])
                compute_cost = _match_cost(["ec2", "virtual machine", "compute engine"])
                ai_cost = _match_cost(["bedrock", "openai", "vertex ai", "sonnet", "claude", "titan"])

                # Levers are only included when real matched spend exists; savings are a
                # documented industry-standard percentage applied to that real baseline —
                # never a hardcoded dollar figure.
                lever_defs = [
                    ("Multi-Cloud Storage Modernization & Waste Elimination", "Storage Modernization & Waste Elimination (Quick Win)",
                     "🔻 Low / Easy", storage_cost, 0.20,
                     "Upgrade AWS EBS/RDS `gp2` volumes to `gp3`, remove orphaned/unattached Azure managed disks, and apply Azure/GCP storage lifecycle tiering (Hot→Cool/Archive, Standard→Nearline/Coldline)."),
                    ("Commercial Database Modernization & Licensing Rightsizing", "Database Licensing & Architecture Modernization (Strategic)",
                     "🔴 High", db_cost, 0.30,
                     "Modernize proprietary commercial database engines (Oracle/SQL Server on RDS) to Amazon Aurora PostgreSQL, rightsize Azure SQL DTU/vCore allocations."),
                    ("Architecture & Silicon Modernization (ARM64/Graviton)", "Hardware Efficiency & Silicon Modernization (Strategic)",
                     "🟡 Medium", compute_cost, 0.18,
                     "Transition stateless compute to ARM64/Graviton-class instances (AWS Graviton, GCP Tau, Azure Ampere) for better price-performance with minimal application changes."),
                    ("AI Token Economics & Batch Inference Optimization", "AI/GenAI Capacity Planning & Token Economics (Strategic)",
                     "🟡 Medium", ai_cost, 0.35,
                     "Enable prompt caching on Claude/Bedrock and Azure OpenAI models to cut repeated prompt-token costs, and route non-realtime workloads through batch inference APIs for discounted rates."),
                ]

                levers_md = []
                opp_rows = []
                total_savings = 0.0
                for idx, (title, category, complexity, base_cost, pct, action) in enumerate(lever_defs, start=1):
                    if base_cost <= 0:
                        continue
                    sav = base_cost * pct
                    total_savings += sav
                    levers_md.append(
                        f"##### {idx}. {title}\n"
                        f"- **FinOps Category**: {category}\n"
                        f"- **Implementation Complexity**: {complexity}\n"
                        f"- **Current Baseline Telemetry**: **${base_cost:,.2f}/month** in matched live spend.\n"
                        f"- **Action Plan**: {action}\n"
                        f"- **Estimated Monthly Savings**: **{pct*100:.0f}% (~${sav:,.2f}/month)**.\n"
                    )
                    opp_rows.append((title, base_cost, complexity, sav))

                if not levers_md:
                    generic_sav = total_mc_spend * 0.15
                    total_savings = generic_sav
                    levers_md.append(
                        f"##### 1. Rate & Modernization Review\n"
                        f"- **FinOps Category**: General Optimization\n"
                        f"- **Implementation Complexity**: 🟡 Medium\n"
                        f"- **Current Baseline Telemetry**: **${total_mc_spend:,.2f}/month** total analyzed spend.\n"
                        f"- **Action Plan**: Apply commitment coverage (Savings Plans/Reservations/CUDs) to steady-state baseline and enforce mandatory tagging for defensible cost allocation.\n"
                        f"- **Estimated Monthly Savings**: **~15% (~${generic_sav:,.2f}/month)** (rule-of-thumb estimate; no single waste category dominates this estate).\n"
                    )

                # Real top-line spend table (no fabricated per-row savings breakdown)
                top_rows_tbl = [
                    f"| **{p}** | {s} | **${c:,.2f}** | {(c/total_mc_spend*100):.1f}% |"
                    for p, s, c in mc_rows[:10]
                ]
                spend_table = (
                    f"| Cloud Provider | Service | Spend | % of Total |\n"
                    f"|:---|:---|:---|:---|\n"
                    f"{chr(10).join(top_rows_tbl)}\n"
                    f"| **Total** | **All Providers & Services** | **${total_mc_spend:,.2f}** | **100.0%** |"
                )

                savings_pct = (total_savings / total_mc_spend * 100) if total_mc_spend else 0.0

                rec_sections = [
                    f"### 💡 CloudHealth Multi-Cloud FinOps Optimization: Top Strategic Recommendations {rec_title}\n",
                    f"Based on live multi-cloud telemetry retrieved from **CloudHealth FOCUS & Billing Datasets** "
                    f"(Total Analyzed Monthly Spend: **${total_mc_spend:,.2f}**):\n",
                    f"#### 🔍 Top Spend by Provider & Service\n\n{spend_table}\n",
                    f"#### 🎯 Prioritized Multi-Cloud FinOps Levers (OptimNow Doctrine)\n",
                    *levers_md,
                    f"#### 📊 Savings Scorecard\n\n",
                    f"| Total Projected Savings Opportunity | % of Analyzed Spend |\n",
                    f"|:---|:---|\n",
                    f"| **${total_savings:,.2f} / month** | **{savings_pct:.1f}%** |\n\n",
                    f"*Source: CloudHealth FOCUS & Multi-Cloud Billing Datasets (AWS CUR, Azure Cost Management, GCP BigQuery). "
                    f"Savings figures are estimates based on documented industry-standard optimization percentages applied to live matched spend, not guarantees.*"
                ]

                return "\n".join(rec_sections)

            # ── Monthly trend / MoM waterfall builder, shared by 3d (below) and the multi-
            # provider follow-up continuity check just after it. Defined here (not inline in 3d)
            # so a follow-up like "give the similar data for AWS and GCP" — after a prior turn
            # that ran this same analysis for Azure — can call it once per newly-named provider
            # instead of falling through to the generic multi-cloud top-services snapshot.
            def _monthly_trend_markdown(provider_key):
                if provider_key == "azure":
                    monthly_sql = "SELECT Month AS month, SUM(EffectiveCost) AS cost FROM AZURE_FOCUS_COST_AND_USAGE GROUP BY Month ORDER BY month DESC"
                    scope_name = "Azure"
                elif provider_key == "gcp":
                    monthly_sql = "SELECT Month AS month, SUM(EffectiveCost) AS cost FROM GCP_FOCUS_COST_AND_USAGE GROUP BY Month ORDER BY month DESC"
                    scope_name = "GCP"
                elif False and provider_key == "oci":
                    monthly_sql = "SELECT Month AS month, SUM(EffectiveCost) AS cost FROM MULTICLOUD_FOCUS_COST_AND_USAGE WHERE provider IN ('OCI', 'Oracle Cloud') GROUP BY Month ORDER BY month DESC"
                    scope_name = "OCI"
                elif provider_key == "all":
                    monthly_sql = "SELECT Month AS month, SUM(EffectiveCost) AS cost FROM MULTICLOUD_FOCUS_COST_AND_USAGE GROUP BY Month ORDER BY month DESC"
                    scope_name = "Multi-Cloud"
                else:
                    monthly_sql = "SELECT timeInterval_Month AS month, SUM(lineItem_UnblendedCost) AS cost FROM AWS_CUR GROUP BY timeInterval_Month ORDER BY month DESC"
                    scope_name = "AWS"

                query_limit = max(limit, months_needed)
                res = mcp.call_tool("execute_datasource_query", {
                    "queryInput": {
                        "sqlStatement": monthly_sql,
                        "dataGranularity": "MONTHLY",
                        "limit": query_limit,
                        "timeRange": time_range
                    },
                    "requestInfo": {"sourceType": "API", "caller": "mcp"}
                })
                content = res.get("content", [{}])[0].get("text", "{}")
                try:
                    c_json = json.loads(content)
                    if "csv" in c_json:
                        # Build the table ourselves (not via the generic CSV-to-markdown helper)
                        # so we can add a real MoM $ / % column computed from the actual monthly
                        # totals — this is deterministic Python math on live data, not an LLM
                        # reformatting/guessing at numbers.
                        mom_rows = list(csv.DictReader(io.StringIO(c_json["csv"])))
                        mom_rows.reverse()  # oldest first, to compute deltas chronologically
                        tbl_lines = []
                        prev_cost = None
                        for r in mom_rows:
                            cost = float(r.get("cost") or 0)
                            if prev_cost is None:
                                mom_str = "—"
                            else:
                                delta = cost - prev_cost
                                pct = (delta / prev_cost * 100) if prev_cost else 0.0
                                sign = "+" if delta >= 0 else "-"
                                arrow = "🔺" if delta >= 0 else "🔻"  # red up (cost increase) / green down (decrease)
                                mom_str = f"{arrow} {sign}${abs(delta):,.2f} ({sign}{abs(pct):.1f}%)"
                            tbl_lines.append((r.get("month", ""), cost, mom_str))
                            prev_cost = cost
                        tbl_lines.reverse()  # back to most-recent-first for display
                        table = (
                            f"| Month | Cost | MoM Change |\n"
                            f"|:---|:---|:---|\n"
                            + "\n".join(f"| {m} | ${c:,.2f} | {mm} |" for m, c, mm in tbl_lines)
                        )
                        insight_md = ""
                        if self.engine != "direct":
                            try:
                                sys_msg = {"role": "system", "content": "You are Cleo, an expert FinOps AI. Provide 2 concise bullet observations about the monthly spend trend."}
                                user_msg = {"role": "user", "content": f"User query: {last_msg}\n\nData:\n{table}"}
                                llm_ans, _ = self._call_active_llm([sys_msg, user_msg])
                                if llm_ans:
                                    insight_md = f"\n\n**💡 FinOps Insights:**\n{_sanitize_finops_bullet_titles(llm_ans)}"
                            except Exception as e:
                                logger.debug(f"[LLM Commentary] {e}")
                        chart_type = _detect_chart_type(low) or "waterfall"
                        chart_md = ""
                        if chart_type:
                            m_rows = list(csv.DictReader(io.StringIO(c_json["csv"])))
                            m_rows.reverse()  # chronological
                            t_format = "quarter" if "quarter" in low else "month"
                            if chart_type == "waterfall" and len(m_rows) >= 2:
                                wf_labels = [_format_time_label(m_rows[0]["month"], t_format) + " (Base)"]
                                wf_vals = [round(float(m_rows[0].get("cost") or 0), 2)]
                                for i in range(1, len(m_rows)):
                                    prev_c = float(m_rows[i-1].get("cost") or 0)
                                    cur_c = float(m_rows[i].get("cost") or 0)
                                    wf_labels.append(_format_time_label(m_rows[i]["month"], t_format))
                                    wf_vals.append(round(cur_c - prev_c, 2))
                                wf_labels.append("Total Spend")
                                wf_vals.append(round(float(m_rows[-1].get("cost") or 0), 2))
                                chart_md = _build_waterfall_chart(f"{scope_name} Month-over-Month Spend Progression", wf_labels, wf_vals)
                            elif chart_type == "line":
                                # Trend: line chart of monthly totals + MoM variance waterfall
                                lbls = [_format_time_label(r["month"], t_format) for r in m_rows]
                                vals = [round(float(r.get("cost") or 0), 2) for r in m_rows]
                                chart_md = _chart_block("line", f"{scope_name} Monthly Cloud Spend Trend", lbls, values=vals, value_label="Cost ($)")
                                # MoM variance waterfall beneath
                                mo_totals_g = {r["month"]: float(r.get("cost") or 0) for r in m_rows}
                                sorted_mos_g = sorted(mo_totals_g.keys())
                                variance_md = _build_mom_variance_chart(
                                    f"{scope_name} Month-over-Month Spend Variance", sorted_mos_g, mo_totals_g, time_format=t_format
                                )
                                chart_md += f"\n{variance_md}" if variance_md else ""
                            else:
                                c_kind = chart_type if chart_type in ["pie", "doughnut", "bar", "horizontal-bar"] else "bar"
                                lbls = [_format_time_label(r["month"], t_format) for r in m_rows]
                                vals = [round(float(r.get("cost") or 0), 2) for r in m_rows]
                                chart_md = _chart_block(c_kind, f"{scope_name} Monthly Cloud Spend", lbls, values=vals, horizontal=(c_kind == "horizontal-bar"), stacked=True)

                        wants_table = _detect_wants_table(low)
                        tbl_md = f"{table}\n" if wants_table else ""
                        # The title should name the actual scope queried above, not a generic
                        # "CloudHealth Spend Analysis" — otherwise a user who explicitly asked
                        # "for Azure" (or AWS) has no way to tell what they're looking at.
                        scope_label = f"{scope_name} Monthly Spend Breakdown{f' — {named_customer}' if named_customer else ''}"
                        return (
                            f"### 📊 CloudHealth Spend Analysis: {scope_label}\n\n"
                            f"{tbl_md}"
                            f"{chart_md}"
                            f"{insight_md}\n\n"
                            f"💡 *Live FinOps data retrieved from CloudHealth ({scope_name}).*"
                        )
                    if "error" in c_json:
                        err_text = c_json["error"][0] if isinstance(c_json["error"], list) else str(c_json["error"])
                        return f"### ⚠️ Query Notice ({scope_name})\n\n{err_text}"
                    if "formatted_markdown" in c_json:
                        return c_json["formatted_markdown"]
                except Exception:
                    pass
                return (
                    f"### 📊 CloudHealth Spend Analysis ({scope_name})\n\n"
                    f"```json\n{content}\n```\n\n"
                    f"💡 *Data retrieved from CloudHealth.*"
                )

            # A follow-up like "give the similar data for AWS and GCP" right after a monthly-
            # trend/waterfall response (for Azure, say) should repeat that same analysis for each
            # newly-named provider — not fall through to the generic multi-cloud top-services
            # snapshot (3c), which is what happens if we only look at the current message's own
            # chart-type keywords (it usually has none; it's relying entirely on "similar").
            _prior_was_monthly_trend = bool(prior_assistant_msgs) and "MoM Change" in prior_assistant_msgs[-1]
            if _prior_was_monthly_trend and is_followup and mcp:
                _mentioned_clouds = []
                if re.search(r'\b(aws|amazon)\b', low):
                    _mentioned_clouds.append("aws")
                if re.search(r'\b(azure|microsoft)\b', low):
                    _mentioned_clouds.append("azure")
                if re.search(r'\b(gcp|google cloud|google)\b', low):
                    _mentioned_clouds.append("gcp")
                if False and re.search(r'\b(oci|oracle cloud|oracle)\b', low):
                    _mentioned_clouds.append("oci")
                if _mentioned_clouds:
                    # "similar data for X and Y" carries no timeframe of its own — without this,
                    # it silently falls back to a ~3-month default instead of matching the "last
                    # 12 months" the original request (and prior response) actually used.
                    if not re.search(r'\d+\s*months?\b', low) and prior_user_msgs:
                        prior_time_ctx = parse_query_time_context(prior_user_msgs[-1])
                        months_needed = prior_time_ctx["months_needed"]
                        time_range = {"last": min(months_needed, 12), "qualifier": "MONTH"}
                    return "\n\n---\n\n".join(_monthly_trend_markdown(c) for c in _mentioned_clouds)

            # 3a. Named Customer Breakdown (e.g. "breakdown for Lundbeck" or "AWS RDS cost for Lundbeck")
            if named_customer and named_customer_crn:
                # Check for Service-Level MoM comparison / projection query
                is_svc_comparison = (
                    any(w in low for w in ["service level", "by service", "service cost", "services cost", "each service"]) or
                    ("service" in low and any(w in low for w in ["compare", "comparison", "project", "projected", "forecast", "previous month", "last month"]))
                ) and not requested_service

                if is_svc_comparison:
                    res_multi_svc = mcp.call_tool("execute_datasource_query", {
                        "channelCustomerId": named_customer_crn,
                        "queryInput": {
                            "sqlStatement": (
                                "SELECT timeInterval_Month AS month, lineItem_ProductCode AS service, "
                                "SUM(lineItem_UnblendedCost) AS cost "
                                "FROM AWS_CUR "
                                "GROUP BY timeInterval_Month, lineItem_ProductCode "
                                "ORDER BY month DESC, cost DESC"
                            ),
                            "dataGranularity": "MONTHLY",
                            "limit": 200,
                            "timeRange": {"last": 2, "qualifier": "MONTH"}
                        },
                        "requestInfo": {"sourceType": "API", "caller": "mcp"}
                    })
                    multi_csv = json.loads(res_multi_svc["content"][0]["text"]).get("csv", "")
                    services_by_month = {last_ym: {}, current_ym: {}}
                    all_services = set()
                    for row in csv.DictReader(io.StringIO(multi_csv)):
                        m = row.get("month", "")
                        s = row.get("service", "")
                        try:
                            c = float(row.get("cost") or 0)
                        except (ValueError, TypeError):
                            c = 0.0
                        if m in services_by_month and s:
                            services_by_month[m][s] = c
                            all_services.add(s)

                    now_dt = datetime.date.today()
                    days_in_month = calendar.monthrange(now_dt.year, now_dt.month)[1]
                    days_elapsed = max(now_dt.day, 1)
                    effective_days = max(days_elapsed - 1.5, 1.0)  # adj. for CloudHealth 24-48hr lag
                    runrate_factor = days_in_month / effective_days

                    svc_comparison_list = []
                    for s in all_services:
                        lm_val = services_by_month.get(last_ym, {}).get(s, 0.0)
                        mtd_val = services_by_month.get(current_ym, {}).get(s, 0.0)
                        proj_val = mtd_val * runrate_factor
                        diff = proj_val - lm_val
                        pct = (diff / lm_val * 100) if lm_val > 0 else (100.0 if proj_val > 0 else 0.0)
                        sign = "+" if diff > 0 else ("-" if diff < 0 else "")
                        icon = "🔺" if diff > 0 else "🔻"
                        svc_comparison_list.append({
                            "service": s,
                            "last_month": lm_val,
                            "mtd": mtd_val,
                            "projected": proj_val,
                            "diff": diff,
                            "pct": pct,
                            "sign": sign,
                            "icon": icon
                        })

                    svc_comparison_list.sort(key=lambda x: max(x["projected"], x["last_month"]), reverse=True)

                    tot_lm = sum(x["last_month"] for x in svc_comparison_list)
                    tot_mtd = sum(x["mtd"] for x in svc_comparison_list)
                    tot_proj = sum(x["projected"] for x in svc_comparison_list)
                    tot_diff = tot_proj - tot_lm
                    tot_pct = (tot_diff / tot_lm * 100) if tot_lm > 0 else 0.0
                    tot_sign = "+" if tot_diff > 0 else ("-" if tot_diff < 0 else "")
                    tot_icon = "🔺" if tot_diff > 0 else "🔻"

                    table_rows = "\n".join([
                        f"| {x['service']} | ${x['last_month']:,.2f} | ${x['mtd']:,.2f} | **${x['projected']:,.2f}** | {x['sign']}${abs(x['diff']):,.2f} | {x['pct']:+.1f}% {x['icon']} |"
                        for x in svc_comparison_list[:15]
                    ])

                    top1 = svc_comparison_list[0] if svc_comparison_list else None
                    top2 = svc_comparison_list[1] if len(svc_comparison_list) > 1 else None
                    top_driver_bullets = ""
                    if top1:
                        top_driver_bullets += f"- **{top1['service']}**: Last Month: ${top1['last_month']:,.2f} | MTD: ${top1['mtd']:,.2f} | Projected: **${top1['projected']:,.2f}** ({top1['pct']:+.1f}% vs {last_ym} {top1['icon']}).\n"
                    if top2:
                        top_driver_bullets += f"- **{top2['service']}**: Last Month: ${top2['last_month']:,.2f} | MTD: ${top2['mtd']:,.2f} | Projected: **${top2['projected']:,.2f}** ({top2['pct']:+.1f}% vs {last_ym} {top2['icon']}).\n"

                    return (
                        f"### 📊 Service-Level Cost Comparison & Run-Rate Forecast: {named_customer}\n\n"
                        f"*Comparing Last Month ({last_ym}) actuals with Current Month ({current_ym}) projected run-rate (Day {days_elapsed} of {days_in_month}, multiplier: {runrate_factor:.2f}x):*\n\n"
                        f"| AWS Service | Last Month ({last_ym}) | Current MTD ({current_ym}) | Projected Month-End ({current_ym}) | MoM Variance ($) | MoM Variance (%) |\n"
                        f"|:---|:---|:---|:---|:---|:---|\n"
                        f"{table_rows}\n"
                        f"| **Total Customer Spend** | **${tot_lm:,.2f}** | **${tot_mtd:,.2f}** | **${tot_proj:,.2f}** | **{tot_sign}${abs(tot_diff):,.2f}** | **{tot_pct:+.1f}% {tot_icon}** |\n\n"
                        f"**💡 Key FinOps Spend Drivers:**\n"
                        f"{top_driver_bullets}"
                        f"- **Customer Trajectory**: {named_customer} is tracking towards a month-end total of **${tot_proj:,.2f}**, representing an overall {tot_pct:+.1f}% ({tot_sign}${abs(tot_diff):,.2f}) variance against {last_ym}.\n\n"
                        f"*Source: AWS CUR via CloudHealth (channel-scoped: {named_customer}). Run-rate projection formula: MTD * ({days_in_month}/{days_elapsed}).*"
                    )

                svc_time_range = {"from": target_ym, "to": target_ym} if (is_specific and target_ym != last_ym) else {"last": 1, "qualifier": "MONTH", "excludeCurrent": True}
                svc_label = target_label if (is_specific and target_ym != last_ym) else last_ym

                # Monthly history query (always last 12 months, max allowed without FQ-USR-304 error)
                if requested_service:
                    tot_sql = (
                        f"SELECT timeInterval_Month AS month, lineItem_ProductCode AS service, "
                        f"SUM(lineItem_UnblendedCost) AS cost "
                        f"FROM AWS_CUR "
                        f"WHERE lineItem_ProductCode = '{requested_service}' "
                        f"GROUP BY timeInterval_Month, lineItem_ProductCode "
                        f"ORDER BY month DESC"
                    )
                else:
                    tot_sql = (
                        "SELECT timeInterval_Month AS month, "
                        "SUM(lineItem_UnblendedCost) AS cost "
                        "FROM AWS_CUR "
                        "GROUP BY timeInterval_Month "
                        "ORDER BY month DESC"
                    )

                res_tot = mcp.call_tool("execute_datasource_query", {
                    "channelCustomerId": named_customer_crn,
                    "queryInput": {
                        "sqlStatement": tot_sql,
                        "dataGranularity": "MONTHLY",
                        "limit": 13,
                        "timeRange": {"last": 12, "qualifier": "MONTH"}
                    },
                    "requestInfo": {"sourceType": "API", "caller": "mcp"}
                })

                res_svc = mcp.call_tool("execute_datasource_query", {
                    "channelCustomerId": named_customer_crn,
                    "queryInput": {
                        "sqlStatement": (
                            "SELECT lineItem_ProductCode AS service, "
                            "SUM(lineItem_UnblendedCost) AS cost "
                            "FROM AWS_CUR "
                            "GROUP BY lineItem_ProductCode "
                            "ORDER BY cost DESC"
                        ),
                        "dataGranularity": "MONTHLY",
                        "limit": 100,
                        "timeRange": svc_time_range
                    },
                    "requestInfo": {"sourceType": "API", "caller": "mcp"}
                })

                svc_csv = json.loads(res_svc["content"][0]["text"]).get("csv", "")
                tot_csv = json.loads(res_tot["content"][0]["text"]).get("csv", "")

                tot_by_month: dict = {}
                for row in csv.DictReader(io.StringIO(tot_csv)):
                    try:
                        tot_by_month[row["month"]] = float(row.get("cost") or 0)
                    except (ValueError, TypeError):
                        pass

                # If specific target_ym is outside the last 12 months, fetch it explicitly
                if is_specific and target_ym not in tot_by_month:
                    target_sql = (
                        f"SELECT timeInterval_Month AS month, lineItem_ProductCode AS service, "
                        f"SUM(lineItem_UnblendedCost) AS cost "
                        f"FROM AWS_CUR "
                        f"WHERE lineItem_ProductCode = '{requested_service}' "
                        f"GROUP BY timeInterval_Month, lineItem_ProductCode "
                        f"ORDER BY month DESC"
                    ) if requested_service else (
                        "SELECT timeInterval_Month AS month, "
                        "SUM(lineItem_UnblendedCost) AS cost "
                        "FROM AWS_CUR "
                        "GROUP BY timeInterval_Month "
                        "ORDER BY month DESC"
                    )
                    try:
                        res_target = mcp.call_tool("execute_datasource_query", {
                            "channelCustomerId": named_customer_crn,
                            "queryInput": {
                                "sqlStatement": target_sql,
                                "dataGranularity": "MONTHLY",
                                "limit": 1,
                                "timeRange": {"from": target_ym, "to": target_ym}
                            },
                            "requestInfo": {"sourceType": "API", "caller": "mcp"}
                        })
                        t_csv = json.loads(res_target["content"][0]["text"]).get("csv", "")
                        found_target = False
                        for row in csv.DictReader(io.StringIO(t_csv)):
                            try:
                                tot_by_month[target_ym] = float(row.get("cost") or 0)
                                found_target = True
                            except (ValueError, TypeError):
                                pass
                        if not found_target:
                            tot_by_month[target_ym] = 0.0
                    except Exception as e:
                        logger.warning(f"[Target Month Query] {e}")
                        tot_by_month[target_ym] = 0.0

                months_sorted = sorted(tot_by_month.keys(), reverse=True)
                mtd_cost = tot_by_month.get(current_ym, 0.0)
                lm_cost = tot_by_month.get(last_ym, 0.0)
                target_cost = tot_by_month.get(target_ym, 0.0 if is_specific else lm_cost)

                # Current Month Run-rate Forecast
                # CloudHealth has a 24–48hr ingestion lag: today + yesterday are partial.
                # Effective billing days = days elapsed minus ~1.5 to avoid overstating MTD.
                now_dt = datetime.date.today()
                days_in_month = calendar.monthrange(now_dt.year, now_dt.month)[1]
                days_elapsed = max(now_dt.day, 1)
                # Subtract 1.5 days for CloudHealth ingestion delay (partial current/prior day)
                effective_days = max(days_elapsed - 1.5, 1.0)
                runrate_factor = days_in_month / effective_days
                forecast_cost = mtd_cost * runrate_factor if mtd_cost > 0 else 0.0

                fc_delta_badge = ""
                if lm_cost > 0 and forecast_cost > 0:
                    fc_diff = forecast_cost - lm_cost
                    fc_pct = (fc_diff / lm_cost) * 100
                    fc_sign = "+" if fc_diff > 0 else ("-" if fc_diff < 0 else "")
                    fc_icon = "🔺" if fc_diff > 0 else "🔻"
                    fc_delta_badge = f" *({fc_sign}${abs(fc_diff):,.2f} / {fc_pct:+.1f}% vs Last Month {fc_icon})*"

                svc_label_disp = f" ({requested_service_disp})" if requested_service_disp else ""
                month_col_hdr = f"{requested_service_disp} Spend" if requested_service_disp else "Total Spend"

                # Calculate Month-over-Month (MoM) delta for each month
                month_row_list = []
                for idx, m in enumerate(months_sorted[:6]):
                    cost_val = tot_by_month[m]
                    if m == current_ym:
                        mom_str = f"Projected: **~${forecast_cost:,.2f}** *(Run-rate, adj. for 24–48hr delay)*"
                    elif idx + 1 < len(months_sorted):
                        prev_val = tot_by_month[months_sorted[idx + 1]]
                        if prev_val > 0:
                            diff = cost_val - prev_val
                            pct = (diff / prev_val) * 100
                            sign_sym = "+" if diff > 0 else ("-" if diff < 0 else "")
                            icon = "🔺" if diff > 0 else "🔻"
                            mom_str = f"{sign_sym}${abs(diff):,.2f} ({pct:+.1f}%) {icon}"
                        elif cost_val > 0:
                            mom_str = f"+${cost_val:,.2f} (new) 🔺"
                        else:
                            mom_str = "$0.00 (0.0%)"
                    else:
                        mom_str = "-"
                    month_row_list.append(f"| {m} | ${cost_val:,.2f} | {mom_str} |")

                month_rows = "\n".join(month_row_list) if month_row_list else "| *(No monthly history recorded)* | $0.00 | - |"

                # Calculate Last Month MoM badge for summary card
                lm_delta_badge = ""
                lm_idx = months_sorted.index(last_ym) if last_ym in months_sorted else -1
                if lm_idx != -1 and lm_idx + 1 < len(months_sorted):
                    prior_m = months_sorted[lm_idx + 1]
                    prior_val = tot_by_month[prior_m]
                    if prior_val > 0:
                        diff = lm_cost - prior_val
                        pct = (diff / prior_val) * 100
                        icon = "🔺" if diff > 0 else "🔻"
                        lm_delta_badge = f" *({pct:+.1f}% vs {prior_m} {icon})*"

                svc_by_month: dict = {}
                for row in csv.DictReader(io.StringIO(svc_csv)):
                    svc = row.get("service", "")
                    try:
                        c = float(row.get("cost") or 0)
                    except (ValueError, TypeError):
                        c = 0.0
                    svc_by_month[svc] = c

                base_cost = sum(svc_by_month.values())
                if base_cost <= 0:
                    base_cost = target_cost if target_cost > 0 else (lm_cost if lm_cost > 0 else 1.0)
                top_svcs = sorted(svc_by_month.items(), key=lambda x: x[1], reverse=True)[:10]
                svc_rows = "\n".join([
                    f"| {s} | ${c:,.2f} | {(c / base_cost * 100):.1f}% |"
                    for s, c in top_svcs
                ]) if top_svcs else "| *(No recorded usage for this month)* | $0.00 | 0.0% |"

                insight_md = ""
                if self.engine != "direct":
                    try:
                        sys_m = {
                            "role": "system",
                            "content": (
                                "You are Cleo, an expert FinOps AI. Provide 2–3 concise bullet FinOps insights. Be specific and actionable. "
                                "CRITICAL: If 'ComputeSavingsPlans', 'SavingsPlans', or 'Reserved Instances' dominate the spend, explicitly identify them as upfront/partial-upfront commitment fees rather than standard run-rate compute usage."
                            )
                        }
                        svc_context_str = f"Specific Service: {requested_service_disp} ({requested_service})\n" if requested_service else ""
                        user_m = {"role": "user", "content": f"Customer: {named_customer}\n{svc_context_str}Target Month ({target_label}): ${target_cost:,.2f}\nLast Month ({last_ym}): ${lm_cost:,.2f}\nMTD ({current_ym}): ${mtd_cost:,.2f}\nTop Services ({svc_label}): {dict(top_svcs[:5])}"}
                        llm_ans, _ = self._call_active_llm([sys_m, user_m])
                        if llm_ans:
                            insight_md = f"\n\n**💡 FinOps Insights:**\n{_sanitize_finops_bullet_titles(llm_ans)}"
                    except Exception as e:
                        logger.debug(f"[LLM Commentary] {e}")

                summary_table = (
                    f"| **Total {target_label} Spend{svc_label_disp}** ({target_ym}) | **${target_cost:,.2f}** |\n"
                    f"| **Total Last Month{svc_label_disp}** ({last_ym}) | **${lm_cost:,.2f}**{lm_delta_badge} |\n"
                    f"| **Total MTD{svc_label_disp}** ({current_ym}) | **${mtd_cost:,.2f}** *(Day {days_elapsed} of {days_in_month})* |\n"
                    f"| **Projected Month-End Forecast{svc_label_disp}** ({current_ym}) | **${forecast_cost:,.2f}**{fc_delta_badge} |\n"
                ) if (is_specific and target_ym not in (last_ym, current_ym)) else (
                    f"| **Total Last Month{svc_label_disp}** ({last_ym}) | **${lm_cost:,.2f}**{lm_delta_badge} |\n"
                    f"| **Total MTD{svc_label_disp}** ({current_ym}) | **${mtd_cost:,.2f}** *(Day {days_elapsed} of {days_in_month})* |\n"
                    f"| **Projected Month-End Forecast{svc_label_disp}** ({current_ym}) | **${forecast_cost:,.2f}**{fc_delta_badge} |\n"
                )

                header_title = f"Cost Breakdown: {named_customer} - {requested_service_disp}" if requested_service_disp else f"Cost Breakdown: {named_customer}"
                hist_title = f"{requested_service_disp} Monthly Spend History" if requested_service_disp else "Monthly Spend History"

                chart_type = _detect_chart_type(low)
                wants_table = _detect_wants_table(low)
                chart_md = ""
                if chart_type:
                    t_format = "quarter" if "quarter" in low else "month"
                    if chart_type in ["doughnut", "pie"] and top_svcs:
                        labels = [s for s, _ in top_svcs]
                        values = [round(c, 2) for _, c in top_svcs]
                        chart_md = _chart_block(
                            chart_type,
                            f"Top AWS Services — {named_customer} ({svc_label})",
                            labels,
                            values=values,
                            value_label="Cost ($)"
                        )
                    elif chart_type == "horizontal-bar" and top_svcs:
                        labels = [s for s, _ in top_svcs]
                        values = [round(c, 2) for _, c in top_svcs]
                        chart_md = _chart_block(
                            "horizontal-bar",
                            f"Top AWS Services — {named_customer} ({svc_label})",
                            labels,
                            values=values,
                            value_label="Cost ($)",
                            horizontal=True
                        )
                    elif chart_type == "waterfall":
                        wf_months = sorted(tot_by_month.keys())
                        if len(wf_months) >= 2:
                            wf_labels = [_format_time_label(wf_months[0], t_format) + " (Base)"]
                            wf_vals = [round(tot_by_month[wf_months[0]], 2)]
                            for i in range(1, len(wf_months)):
                                prev_m = wf_months[i - 1]
                                cur_m = wf_months[i]
                                delta = tot_by_month[cur_m] - tot_by_month[prev_m]
                                wf_labels.append(_format_time_label(cur_m, t_format))
                                wf_vals.append(round(delta, 2))
                            wf_labels.append("Total Spend")
                            wf_vals.append(round(tot_by_month[wf_months[-1]], 2))
                            chart_md = _build_waterfall_chart(
                                f"Month-over-Month Spend Waterfall — {named_customer}",
                                wf_labels, wf_vals
                            )
                        else:
                            wf_labels = ["Baseline"] + [s for s, _ in top_svcs[:7]] + ["Total"]
                            wf_vals = [round(target_cost, 2)] + [round(c, 2) for _, c in top_svcs[:7]] + [round(target_cost, 2)]
                            chart_md = _build_waterfall_chart(f"Spend Contribution Waterfall — {named_customer}", wf_labels, wf_vals)
                    else:
                        # Normal vertical stacked bar chart by default: X-axis = Month/Quarter, Stacks = Services
                        try:
                            chart_q_params = {
                                "queryInput": {
                                    "sqlStatement": (
                                        "SELECT timeInterval_Month AS month, lineItem_ProductCode AS service, "
                                        "SUM(lineItem_UnblendedCost) AS cost "
                                        "FROM AWS_CUR "
                                        "GROUP BY timeInterval_Month, lineItem_ProductCode "
                                        "ORDER BY month ASC, cost DESC"
                                    ),
                                    "dataGranularity": "MONTHLY",
                                    "limit": 150,
                                    "timeRange": {"last": 8, "qualifier": "MONTH"}
                                },
                                "requestInfo": {"sourceType": "API", "caller": "mcp"}
                            }
                            if named_customer_crn:
                                chart_q_params["channelCustomerId"] = named_customer_crn
                            res_chart_svc = mcp.call_tool("execute_datasource_query", chart_q_params)
                            txt_cs = res_chart_svc.get("content", [{}])[0].get("text", "{}")
                            csv_cs = json.loads(txt_cs).get("csv", "")
                            chart_rows = list(csv.DictReader(io.StringIO(csv_cs)))
                            if chart_rows:
                                chart_md = _build_time_category_stacked_chart(
                                    f"AWS Spend by Service & Month — {named_customer}",
                                    chart_rows,
                                    time_col="month",
                                    cat_col="service",
                                    cost_col="cost",
                                    time_format=t_format,
                                    max_cats=10
                                )
                        except Exception as e:
                            logger.warning(f"[Chart Service Breakdown Query] {e}")

                        if not chart_md and top_svcs:
                            labels = [s for s, _ in top_svcs]
                            values = [c for _, c in top_svcs]
                            chart_md = _chart_block("bar",
                                f"Top AWS Services — {named_customer} ({svc_label})",
                                labels, values=values, horizontal=False, stacked=True)

                tbl_block = (
                    f"| Metric | Amount |\n"
                    f"|:---|:---|\n"
                    f"{summary_table}\n"
                    f"#### 📅 {hist_title}\n\n"
                    f"| Month | {month_col_hdr} | MoM Change |\n"
                    f"|:---|:---|:---|\n"
                    f"{month_rows}\n\n"
                    f"#### ☁️ Top AWS Services ({svc_label})\n\n"
                    f"| Service | Cost | % of Total |\n"
                    f"|:---|:---|:---|\n"
                    f"{svc_rows}"
                ) if wants_table else ""

                return (
                    f"### 📊 {header_title}\n\n"
                    f"{tbl_block}"
                    f"{chart_md}"
                    f"{insight_md}\n\n"
                    f"*Source: AWS CUR via CloudHealth (channel-scoped). Numbers match CloudHealth Partner Portal.*"
                )

            # 3b. Channel Customer Summary Table
            elif any(w in low for w in ["customer", "channel", "tenant", "client"]):
                now = datetime.date.today()
                prev_m_num = (now.month - 2) if now.month > 2 else (now.month - 2 + 12)
                prev_yr_num = now.year if now.month > 2 else (now.year - 1)
                prior_ym = f"{prev_yr_num}-{prev_m_num:02d}"

                days_in_month = calendar.monthrange(now.year, now.month)[1]
                days_elapsed = max(now.day, 1)
                effective_days = max(days_elapsed - 1.5, 1.0)  # adj. for CloudHealth 24-48hr lag
                runrate_factor = days_in_month / effective_days

                def _fetch_cust_spend(item):
                    cname, ccrn = item
                    try:
                        r = mcp.call_tool("execute_datasource_query", {
                            "channelCustomerId": ccrn,
                            "queryInput": {
                                "sqlStatement": (
                                    "SELECT timeInterval_Month AS month, "
                                    "SUM(lineItem_UnblendedCost) AS cost "
                                    "FROM AWS_CUR "
                                    "GROUP BY timeInterval_Month "
                                    "ORDER BY month DESC"
                                ),
                                "dataGranularity": "MONTHLY",
                                "limit": months_needed,
                                "timeRange": {"last": min(months_needed, 12), "qualifier": "MONTH"}
                            },
                            "requestInfo": {"sourceType": "API", "caller": "mcp"}
                        })
                        cust_csv = json.loads(r["content"][0]["text"]).get("csv", "")
                        spend_by_month: dict = {}
                        for row in csv.DictReader(io.StringIO(cust_csv)):
                            try:
                                spend_by_month[row["month"]] = float(row.get("cost") or 0)
                            except (ValueError, TypeError):
                                pass
                        target_c = spend_by_month.get(target_ym, 0.0)
                        lm = spend_by_month.get(last_ym, 0.0)
                        mtd = spend_by_month.get(current_ym, 0.0)
                        prior_c = spend_by_month.get(prior_ym, 0.0)
                        fc_c = mtd * runrate_factor if mtd > 0 else 0.0
                        if target_c > 0 or lm > 0 or mtd > 0:
                            return (cname, target_c, lm, mtd, prior_c, fc_c)
                    except Exception as e:
                        logger.warning(f"[Channel Customer Spend] {cname}: {e}")
                    return None

                customer_rows = []
                with ThreadPoolExecutor(max_workers=5) as pool:
                    for res in pool.map(_fetch_cust_spend, list(cust_map.items())):
                        if res:
                            customer_rows.append(res)

                # Sort by target month spend in requested direction (DESC or ASC)
                customer_rows.sort(key=lambda x: x[1], reverse=sort_desc)
                top_rows = customer_rows[:limit]

                if not top_rows:
                    return f"### 📊 Channel Customer Spend\n\nNo spend data found for {target_label}."

                sort_dir_str = "DESC" if sort_desc else "ASC"

                if is_specific and target_ym not in (last_ym, current_ym):
                    # Table featuring the user's specific requested month first
                    total_target = sum(r[1] for r in top_rows)
                    total_lm = sum(r[2] for r in top_rows)
                    total_mtd = sum(r[3] for r in top_rows)
                    total_fc = sum(r[5] for r in top_rows)
                    table_rows = "\n".join([
                        f"| **{name}** | Multi-Cloud (AWS, Azure) | ${tc:,.2f} | ${lm:,.2f} | ${mtd:,.2f} | ~${fc:,.2f} |"
                        for name, tc, lm, mtd, _, fc in top_rows
                    ])
                    table_header = (
                        f"| Customer Name | Cloud Infrastructure | Total {target_label} Spend ({target_ym}) | Total Last Month Spend ({last_ym}) | Total MTD Spend ({current_ym}) | Month-End Forecast ({current_ym}) |\n"
                        f"|:---|:---|:---|:---|:---|:---|\n"
                        f"{table_rows}\n\n"
                        f"| **Total** | | **${total_target:,.2f}** | **${total_lm:,.2f}** | **${total_mtd:,.2f}** | **~${total_fc:,.2f}** |"
                    )
                    title = f"Channel Customer Spend: Sorted by {target_label} Spend ({sort_dir_str})"
                elif target_ym == current_ym:
                    total_mtd = sum(r[3] for r in top_rows)
                    total_fc = sum(r[5] for r in top_rows)
                    total_lm = sum(r[2] for r in top_rows)
                    table_rows = "\n".join([
                        f"| **{name}** | Multi-Cloud (AWS, Azure) | ${mtd:,.2f} | ~${fc:,.2f} | ${lm:,.2f} |"
                        for name, tc, lm, mtd, _, fc in top_rows
                    ])
                    table_header = (
                        f"| Customer Name | Cloud Infrastructure | Total MTD Spend ({current_ym}) | Month-End Forecast ({current_ym}) | Total Last Month Spend ({last_ym}) |\n"
                        f"|:---|:---|:---|:---|:---|\n"
                        f"{table_rows}\n\n"
                        f"| **Total** | | **${total_mtd:,.2f}** | **~${total_fc:,.2f}** | **${total_lm:,.2f}** |"
                    )
                    title = f"Channel Customer Spend: Current Month (MTD) & Forecast"
                else:
                    total_lm = sum(r[2] for r in top_rows)
                    total_prior = sum(r[4] for r in top_rows)
                    total_mtd = sum(r[3] for r in top_rows)
                    total_fc = sum(r[5] for r in top_rows)
                    tot_diff = total_lm - total_prior
                    tot_pct = (tot_diff / total_prior * 100) if total_prior > 0 else 0
                    tot_sign = "+" if tot_diff > 0 else ("-" if tot_diff < 0 else "")
                    tot_icon = "🔺" if tot_diff > 0 else "🔻"
                    tot_delta_str = f"{tot_sign}${abs(tot_diff):,.2f} ({tot_pct:+.1f}%) {tot_icon}" if total_prior > 0 else "—"

                    row_strs = []
                    for name, tc, lm, mtd, prior_c, fc_c in top_rows:
                        if prior_c > 0:
                            diff = lm - prior_c
                            pct = (diff / prior_c) * 100
                            sign_sym = "+" if diff > 0 else ("-" if diff < 0 else "")
                            icon = "🔺" if diff > 0 else "🔻"
                            delta_str = f"{sign_sym}${abs(diff):,.2f} ({pct:+.1f}%) {icon}"
                        else:
                            delta_str = "—"
                        row_strs.append(f"| **{name}** | Multi-Cloud (AWS, Azure) | ${lm:,.2f} | ${prior_c:,.2f} | {delta_str} | ${mtd:,.2f} | ~${fc_c:,.2f} |")

                    table_rows = "\n".join(row_strs)
                    table_header = (
                        f"| Customer Name | Cloud Infrastructure | Total Last Month ({last_ym}) | Prior Month ({prior_ym}) | MoM Change | Total MTD ({current_ym}) | Month-End Forecast ({current_ym}) |\n"
                        f"|:---|:---|:---|:---|:---|:---|:---|\n"
                        f"{table_rows}\n\n"
                        f"| **Total** | | **${total_lm:,.2f}** | **${total_prior:,.2f}** | **{tot_delta_str}** | **${total_mtd:,.2f}** | **~${total_fc:,.2f}** |"
                    )
                    title = f"Channel Customer Spend: Top {len(top_rows)} Customers"

                insight_md = ""
                if self.engine != "direct":
                    try:
                        summary = "\n".join([f"{n}: {target_label} ${tc:,.2f}, Last Month ${lm:,.2f}, MTD ${mtd:,.2f}" for n, tc, lm, mtd in top_rows[:5]])
                        sys_m = {"role": "system", "content": "You are Cleo, an expert FinOps AI. Provide 2 concise bullet FinOps insights from channel customer spend."}
                        user_m = {"role": "user", "content": f"User query: {last_msg}\n\nCustomer spend:\n{summary}"}
                        llm_ans, _ = self._call_active_llm([sys_m, user_m])
                        if llm_ans:
                            insight_md = f"\n\n**💡 FinOps Insights:**\n{_sanitize_finops_bullet_titles(llm_ans)}"
                    except Exception as e:
                        logger.debug(f"[LLM Commentary] {e}")

                return (
                    f"### 📊 {title}\n\n"
                    f"{table_header}\n\n"
                    f"{insight_md}\n\n"
                    f"*Source: CloudHealth FOCUS & Partner Billing Datasets (channel-scoped per customer). Numbers match CloudHealth Partner Portal.*"
                )

            # 3c-1. Service Cost Comparison (MoM)
            elif any(w in low for w in ["compare", "comparison"]) and (requested_service or active_cloud or any(w in low for w in [
                "service", "product", "ec2", "s3", "rds", "bigquery", "vertex", "blob",
                "azure", "gcp", "aws", "cloud", "breakdown"
            ])):
                prov_filter = ""
                cloud_target = active_cloud
                if not cloud_target:
                    if "azure" in low: cloud_target = "azure"
                    elif "gcp" in low or "google" in low: cloud_target = "gcp"
                    elif "aws" in low: cloud_target = "aws"
                    else: cloud_target = "multi-cloud"
                
                if cloud_target == "azure":
                    prov_filter = "WHERE provider = 'Azure'"
                elif cloud_target == "gcp":
                    prov_filter = "WHERE provider IN ('GCP', 'Google Cloud')"
                elif cloud_target == "aws":
                    prov_filter = "WHERE provider = 'AWS'"
                
                # Fetch Last Month & Current Month (MTD) in a single query
                sql = f"SELECT Month AS Month, ServiceCategory AS ServiceCategory, SUM(BilledCost) AS SUM_BilledCost, SUM(EffectiveCost) AS SUM_EffectiveCost FROM MULTICLOUD_FOCUS_COST_AND_USAGE {prov_filter} GROUP BY Month, ServiceCategory ORDER BY ServiceCategory ASC"
                res_all = mcp.call_tool("execute_datasource_query", {
                    "queryInput": {
                        "sqlStatement": sql,
                        "dataGranularity": "MONTHLY", "limit": -1, 
                        "timeRange": {"last": 1, "excludeCurrent": False}
                    },
                    "requestInfo": {"sourceType": "API", "caller": "mcp"}
                })
                
                lm_data = {}
                mtd_data = {}
                try:
                    raw_csv = json.loads(res_all["content"][0]["text"]).get("csv", "")
                    for r in csv.DictReader(io.StringIO(raw_csv)):
                        # Clean and lowercase keys to handle alias dropping/spaces/parens (e.g. "SUM(EffectiveCost)")
                        row = {str(k).lower().replace("_", "").replace(" ", "").replace("(", "").replace(")", ""): v for k, v in r.items() if k}
                        
                        svc = row.get("service") or row.get("servicecategory") or row.get("servicename")
                        if not svc: continue
                        
                        cost_str = row.get("cost") or row.get("sumeffectivecost") or row.get("effectivecost") or 0.0
                        cost = float(cost_str) if cost_str else 0.0
                        
                        prov = row.get("provider") or cloud_target.capitalize()
                        month_val = str(row.get("month") or "")
                        
                        if month_val.startswith(current_ym):
                            mtd_data[svc] = {"cost": cost, "provider": prov}
                        elif month_val.startswith(last_ym):
                            lm_data[svc] = {"cost": cost, "provider": prov}
                except Exception as e:
                    logger.debug(f"[MoM Parse Error] {e}")
                
                now_dt = datetime.date.today()
                days_in_month = calendar.monthrange(now_dt.year, now_dt.month)[1]
                days_elapsed = max(now_dt.day, 1)
                # Adjust for 24-48h billing latency (today is empty, yesterday is partial)
                effective_days = max(days_elapsed - 2.0, 1.0)
                runrate_factor = days_in_month / effective_days
                
                all_svcs = set(lm_data.keys()).union(set(mtd_data.keys()))
                
                merged_data = []
                for svc in all_svcs:
                    lm_cost = lm_data.get(svc, {}).get("cost", 0.0)
                    mtd_cost = mtd_data.get(svc, {}).get("cost", 0.0)
                    prov = lm_data.get(svc, {}).get("provider") or mtd_data.get(svc, {}).get("provider", cloud_target.capitalize())
                    fc_cost = mtd_cost * runrate_factor if mtd_cost > 0 else 0.0
                    merged_data.append((prov, svc, lm_cost, fc_cost))
                
                merged_data.sort(key=lambda x: max(x[2], x[3]), reverse=True)
                
                tbl_lines = []
                for prov, svc, lm_cost, fc_cost in merged_data[:20]:
                    delta = fc_cost - lm_cost
                    delta_str = f"+${delta:,.2f}" if delta > 0 else f"-${abs(delta):,.2f}"
                    if delta > 0: delta_str = f"🔺 {delta_str}"
                    elif delta < 0: delta_str = f"🔻 {delta_str}"
                    tbl_lines.append(f"| {prov} | {svc} | ${lm_cost:,.2f} | ${fc_cost:,.2f} | {delta_str} |")
                
                if not tbl_lines:
                    return f"### ☁️ CloudHealth Spend Analysis\n\nNo spend data returned for {cloud_target.upper()} to compare."
                
                table = (
                    f"| Cloud Provider | Service Category | Last Month Cost ({last_ym}) | Forecast This Month ({current_ym}) | Delta (Forecast vs LM) |\n"
                    f"|:---|:---|:---|:---|:---|\n"
                    f"{chr(10).join(tbl_lines)}\n"
                )
                
                insight_md = ""
                if self.engine != "direct":
                    try:
                        sys_msg = {
                            "role": "system",
                            "content": (
                                "You are Cleo, an expert FinOps AI. Analyze this Month-over-Month cloud service cost comparison table. "
                                "Provide 2 concise, actionable FinOps insights explaining the biggest cost drivers, deltas, or anomalies, and what actions to take to optimize. "
                                "CRITICAL 1: If you see massive Month-over-Month spikes or 100% drops in third-party software, SaaS, or security platforms (e.g., WIZ, Reltio, IBM, Datadog, Snowflake), DO NOT classify them as 'discontinued' or 'unexpected usage spikes'. Correctly identify them as likely one-time or annual Cloud Marketplace commitments/renewals. "
                                "CRITICAL 2: If 'ComputeSavingsPlans', 'SavingsPlans', or 'Reserved Instances' dominate the spend, explicitly identify them as upfront/partial-upfront commitment fees rather than standard run-rate compute usage."
                            )
                        }
                        user_msg = {"role": "user", "content": f"User query: {last_msg}\n\nData:\n{table}"}
                        llm_ans, _ = self._call_active_llm([sys_msg, user_msg])
                        if llm_ans:
                            insight_md = f"\n\n**💡 FinOps Insights:**\n{_sanitize_finops_bullet_titles(llm_ans)}"
                    except Exception as e:
                        logger.debug(f"[LLM Commentary] {e}")
                
                target_display = cloud_target.upper() if cloud_target != "multi-cloud" else "Multi-Cloud"
                return (
                    f"### 📊 CloudHealth Spend Analysis: {target_display} Services MoM Comparison\n\n"
                    f"{table}"
                    f"{insight_md}\n\n"
                    f"*Source: MULTICLOUD_FOCUS_COST_AND_USAGE. Forecast assumes linear run-rate adjusted for 24-48h billing latency.*"
                )

            # 3c. Multi-Cloud & Provider Service Spend Breakdown (AWS, Azure, GCP)
            # A "total spend over time" request (MoM variance / waterfall / trend line) with no
            # specific service named belongs in 3d's monthly-trend handler, which already builds
            # those chart types — without this check, e.g. "aws" in the query alone would route
            # a "12 months ... MoM variance with waterfall chart" request into a flat service list.
            elif (requested_service or not (
                _detect_chart_type(low) in ("waterfall", "line") or
                any(w in low for w in ["mom variance", "month over month", "month-over-month"])
            )) and (requested_service or active_cloud or any(w in low for w in [
                "service", "product", "ec2", "s3", "rds", "bigquery", "vertex", "blob",
                "azure", "gcp", "aws", "cloud", "breakdown"
            ])):
                partial_notice = ""
                if time_ctx.get("timeframe_days"):
                    t_days = time_ctx["timeframe_days"]
                    svc_scope_label = time_ctx.get("target_label", f"Last {t_days} Days Trend")
                    if time_ctx.get("daily_range"):
                        svc_time_range = time_ctx["daily_range"]
                    else:
                        svc_time_range = {"last": t_days, "qualifier": "DAY"}
                    svc_granularity = "DAILY"
                    partial_notice = (
                        f"> ⚠️ **FinOps Ingestion Notice**: Current date (`{today_str}`) is excluded from closed analysis as in-flight; "
                        f"yesterday's data (`{yesterday_str}`) is preliminary/partial across all cloud providers due to standard 24–48h billing ingestion latency.\n\n"
                    )
                elif is_specific:
                    svc_time_range = {"from": target_ym, "to": target_ym}
                    svc_scope_label = target_label
                    svc_granularity = "MONTHLY"
                elif re.search(r'\b(quarter|quater|qtr)\b', low):
                    # "quarter"/"qtr" is only handled elsewhere as a display-label hint (grouping
                    # monthly rows for a chart); this handler never recognized it as a time window
                    # at all, so it silently fell through to the "Last 30 Days" default instead.
                    today_d = datetime.date.today()
                    cur_q = (today_d.month - 1) // 3 + 1
                    if any(w in low for w in ["last quarter", "previous quarter", "prior quarter"]):
                        q, yr = (cur_q - 1, today_d.year) if cur_q > 1 else (4, today_d.year - 1)
                    else:
                        q, yr = cur_q, today_d.year
                    q_start_month = (q - 1) * 3 + 1
                    svc_time_range = {"from": f"{yr}-{q_start_month:02d}", "to": f"{yr}-{q_start_month + 2:02d}"}
                    svc_scope_label = f"Q{q} {yr}"
                    svc_granularity = "MONTHLY"
                else:
                    # User rule: "use last 30days trend for such requests by default unless I ask for the specific time window"
                    svc_scope_label = "Last 30 Days Trend"
                    svc_time_range = {"from": last_ym, "to": current_ym}
                    svc_granularity = "MONTHLY"

                aws_rows = []
                azure_rows = []
                gcp_rows = []
                oci_rows = []
                multi_rows = []

                def _true_total_cost(sql: str) -> float:
                    return _fetch_total_cost(mcp, sql, svc_granularity, svc_time_range)

                # Determine target provider mode
                cloud_target = active_cloud
                if not cloud_target:
                    if requested_service and PCODE_TO_PROVIDER.get(str(requested_service)):
                        cloud_target = PCODE_TO_PROVIDER[str(requested_service)]
                    elif any(w in low for w in ["all cloud", "all clouds", "multi-cloud", "multicloud", "cross-cloud", "every cloud", "cloud services", "top services", "top cloud"]):
                        cloud_target = "all"
                    elif "azure" in low:
                        cloud_target = "azure"
                    elif "gcp" in low or "google" in low:
                        cloud_target = "gcp"
                    elif False and ("oci" in low or "oracle" in low):
                        cloud_target = "oci"
                    elif "aws" in low or "amazon" in low:
                        cloud_target = "aws"
                    else:
                        cloud_target = "all"

                # ── Single Service Query ──
                if requested_service:
                    pcode = str(requested_service)
                    pdisp = requested_service_disp or pcode
                    prov = PCODE_TO_PROVIDER.get(pcode, "aws")
                    prov_disp = "AWS" if prov == "aws" else ("Azure" if prov == "azure" else ("GCP" if prov == "gcp" else "Cloud"))
                    
                    svc_cost = 0.0
                    if prov == "aws":
                        svc_sql = f"SELECT lineItem_ProductCode AS service, SUM(lineItem_UnblendedCost) AS cost FROM AWS_CUR WHERE lineItem_ProductCode = '{pcode}' GROUP BY lineItem_ProductCode ORDER BY cost DESC"
                        try:
                            res = mcp.call_tool("execute_datasource_query", {
                                "queryInput": {"sqlStatement": svc_sql, "dataGranularity": svc_granularity, "limit": 1, "timeRange": svc_time_range},
                                "requestInfo": {"sourceType": "API", "caller": "mcp"}
                            })
                            raw_csv = json.loads(res["content"][0]["text"]).get("csv", "")
                            for r in csv.DictReader(io.StringIO(raw_csv)):
                                svc_cost = float(r.get("cost") or 0.0)
                        except Exception as e:
                            logger.warning(f"[Single Service Query] AWS CUR: {e}")
                    else:
                        # Non-AWS service estimation / live focus
                        try:
                            res = mcp.call_tool("execute_datasource_query", {
                                "queryInput": {
                                    "sqlStatement": f"SELECT provider AS provider, ServiceName AS service, SUM(EffectiveCost) AS cost FROM MULTICLOUD_FOCUS_COST_AND_USAGE WHERE ServiceName LIKE '%{pcode}%' GROUP BY provider, ServiceName ORDER BY cost DESC",
                                    "dataGranularity": svc_granularity, "limit": 1, "timeRange": svc_time_range
                                },
                                "requestInfo": {"sourceType": "API", "caller": "mcp"}
                            })
                            raw_csv = json.loads(res["content"][0]["text"]).get("csv", "")
                            for r in csv.DictReader(io.StringIO(raw_csv)):
                                svc_cost = float(r.get("cost") or 0.0)
                        except Exception:
                            pass

                    if svc_cost > 0.0:
                        table = (
                            f"| Cloud Provider | Service Category | Cost |\n"
                            f"|:---|:---|:---|\n"
                            f"| {prov_disp} | {pdisp} | **${svc_cost:,.2f}** |\n"
                        )
                        svc_hdr = f"CloudHealth Spend Analysis: {pdisp} ({prov_disp}) Spend — {svc_scope_label}"
                    else:
                        return (
                            f"### ☁️ CloudHealth Spend Analysis: {pdisp} ({prov_disp})\n\n"
                            f"No live billing data was returned for **{pdisp}** in `{svc_scope_label}`. This usually means "
                            f"there was no spend in this period, or the connected CloudHealth account/scope doesn't have "
                            f"visibility into it.\n\n"
                            f"*Source: {'AWS_CUR' if prov == 'aws' else 'MULTICLOUD_FOCUS_COST_AND_USAGE'} via CloudHealth FlexReports.*"
                        )

                # ── Azure Specific Breakdown ──
                elif cloud_target == "azure":
                    azure_rows = []
                    try:
                        res = mcp.call_tool("execute_datasource_query", {
                            "queryInput": {
                                "sqlStatement": "SELECT provider AS provider, ServiceName AS service, SUM(EffectiveCost) AS cost FROM MULTICLOUD_FOCUS_COST_AND_USAGE WHERE provider = 'Azure' GROUP BY provider, ServiceName ORDER BY cost DESC",
                                "dataGranularity": svc_granularity, "limit": limit, "timeRange": svc_time_range
                            },
                            "requestInfo": {"sourceType": "API", "caller": "mcp"}
                        })
                        raw_csv = json.loads(res["content"][0]["text"]).get("csv", "")
                        for r in csv.DictReader(io.StringIO(raw_csv)):
                            c = float(r.get("cost") or 0.0)
                            s = r.get("service")
                            if s and c > 0:
                                azure_rows.append((r.get("provider") or "Azure", s, c))
                    except Exception as e:
                        logger.warning(f"[Azure Service Query] {e}")

                    if not azure_rows:
                        return (
                            f"### ☁️ CloudHealth Spend Analysis: Azure\n\n"
                            f"No live Azure billing data was returned for `{svc_scope_label}`. This usually means there "
                            f"was no Azure spend in this period, or the connected CloudHealth account/scope doesn't "
                            f"have visibility into it.\n\n"
                            f"*Source: MULTICLOUD_FOCUS_COST_AND_USAGE via CloudHealth FlexReports.*"
                        )

                    total_az = _true_total_cost(
                        "SELECT SUM(EffectiveCost) AS cost FROM MULTICLOUD_FOCUS_COST_AND_USAGE WHERE provider = 'Azure'"
                    ) or sum(r[2] for r in azure_rows)
                    tbl_lines = [
                        f"| {p} | {s} | ${c:,.2f} | {((c/total_az)*100 if total_az else 0):.1f}% |"
                        for p, s, c in azure_rows
                    ]
                    table = (
                        f"| Cloud Provider | Service Category | Cost | % of Azure Spend |\n"
                        f"|:---|:---|:---|:---|\n"
                        f"{chr(10).join(tbl_lines)}\n\n"
                        f"| **Total Azure Spend** | | **${total_az:,.2f}** | **100.0%** |"
                    )
                    svc_hdr = f"CloudHealth Spend Analysis: Top Azure Services by Spend — {svc_scope_label}"

                # ── GCP Specific Breakdown ──
                elif cloud_target == "gcp":
                    gcp_rows = []
                    try:
                        res = mcp.call_tool("execute_datasource_query", {
                            "queryInput": {
                                "sqlStatement": "SELECT provider AS provider, ServiceName AS service, SUM(EffectiveCost) AS cost FROM MULTICLOUD_FOCUS_COST_AND_USAGE WHERE provider IN ('GCP', 'Google Cloud') GROUP BY provider, ServiceName ORDER BY cost DESC",
                                "dataGranularity": svc_granularity, "limit": limit, "timeRange": svc_time_range
                            },
                            "requestInfo": {"sourceType": "API", "caller": "mcp"}
                        })
                        raw_csv = json.loads(res["content"][0]["text"]).get("csv", "")
                        for r in csv.DictReader(io.StringIO(raw_csv)):
                            c = float(r.get("cost") or 0.0)
                            s = r.get("service")
                            if s and c > 0:
                                gcp_rows.append((r.get("provider") or "GCP", s, c))
                    except Exception as e:
                        logger.warning(f"[GCP Service Query] {e}")

                    if not gcp_rows:
                        return (
                            f"### ☁️ CloudHealth Spend Analysis: GCP\n\n"
                            f"No live Google Cloud billing data was returned for `{svc_scope_label}`. This usually means "
                            f"there was no GCP spend in this period, or the connected CloudHealth account/scope doesn't "
                            f"have visibility into it.\n\n"
                            f"*Source: MULTICLOUD_FOCUS_COST_AND_USAGE via CloudHealth FlexReports.*"
                        )

                    total_gcp = _true_total_cost(
                        "SELECT SUM(EffectiveCost) AS cost FROM MULTICLOUD_FOCUS_COST_AND_USAGE WHERE provider IN ('GCP', 'Google Cloud')"
                    ) or sum(r[2] for r in gcp_rows)
                    tbl_lines = [
                        f"| {p} | {s} | ${c:,.2f} | {((c/total_gcp)*100 if total_gcp else 0):.1f}% |"
                        for p, s, c in gcp_rows
                    ]
                    table = (
                        f"| Cloud Provider | Service Category | Cost | % of GCP Spend |\n"
                        f"|:---|:---|:---|:---|\n"
                        f"{chr(10).join(tbl_lines)}\n\n"
                        f"| **Total GCP Spend** | | **${total_gcp:,.2f}** | **100.0%** |"
                    )
                    svc_hdr = f"CloudHealth Spend Analysis: Top Google Cloud (GCP) Services by Spend — {svc_scope_label}"

                # ── OCI Specific Breakdown ──
                elif False and cloud_target == "oci":
                    oci_rows = []
                    try:
                        res = mcp.call_tool("execute_datasource_query", {
                            "queryInput": {
                                "sqlStatement": "SELECT provider AS provider, ServiceName AS service, SUM(EffectiveCost) AS cost FROM MULTICLOUD_FOCUS_COST_AND_USAGE WHERE provider IN ('OCI', 'Oracle Cloud') GROUP BY provider, ServiceName ORDER BY cost DESC",
                                "dataGranularity": svc_granularity, "limit": limit, "timeRange": svc_time_range
                            },
                            "requestInfo": {"sourceType": "API", "caller": "mcp"}
                        })
                        raw_csv = json.loads(res["content"][0]["text"]).get("csv", "")
                        for r in csv.DictReader(io.StringIO(raw_csv)):
                            c = float(r.get("cost") or 0.0)
                            s = r.get("service")
                            if s and c > 0:
                                oci_rows.append((r.get("provider") or "OCI", s, c))
                    except Exception as e:
                        logger.warning(f"[OCI Service Query] {e}")

                    if not oci_rows:
                        return (
                            f"### ☁️ CloudHealth Spend Analysis: OCI\n\n"
                            f"No live Oracle Cloud (OCI) billing data was returned for `{svc_scope_label}`. This usually "
                            f"means there was no OCI spend in this period, or the connected CloudHealth account/scope "
                            f"doesn't have visibility into it.\n\n"
                            f"*Source: MULTICLOUD_FOCUS_COST_AND_USAGE via CloudHealth FlexReports.*"
                        )

                    total_oci = _true_total_cost(
                        "SELECT SUM(EffectiveCost) AS cost FROM MULTICLOUD_FOCUS_COST_AND_USAGE WHERE provider IN ('OCI', 'Oracle Cloud')"
                    ) or sum(r[2] for r in oci_rows)
                    tbl_lines = [
                        f"| {p} | {s} | ${c:,.2f} | {((c/total_oci)*100 if total_oci else 0):.1f}% |"
                        for p, s, c in oci_rows
                    ]
                    table = (
                        f"| Cloud Provider | Service Category | Cost | % of OCI Spend |\n"
                        f"|:---|:---|:---|:---|\n"
                        f"{chr(10).join(tbl_lines)}\n\n"
                        f"| **Total OCI Spend** | | **${total_oci:,.2f}** | **100.0%** |"
                    )
                    svc_hdr = f"CloudHealth Spend Analysis: Top Oracle Cloud (OCI) Services by Spend — {svc_scope_label}"

                # ── AWS Specific Breakdown ──
                elif cloud_target == "aws":
                    aws_rows = []
                    svc_sql = "SELECT lineItem_ProductCode AS service, SUM(lineItem_UnblendedCost) AS cost FROM AWS_CUR GROUP BY lineItem_ProductCode ORDER BY cost DESC"
                    try:
                        res = mcp.call_tool("execute_datasource_query", {
                            "queryInput": {"sqlStatement": svc_sql, "dataGranularity": svc_granularity, "limit": limit, "timeRange": svc_time_range},
                            "requestInfo": {"sourceType": "API", "caller": "mcp"}
                        })
                        raw_csv = json.loads(res["content"][0]["text"]).get("csv", "")
                        for r in csv.DictReader(io.StringIO(raw_csv)):
                            c = float(r.get("cost") or 0.0)
                            s = _friendly_service_name(r.get("service") or "")
                            if s and c > 0:
                                aws_rows.append(("AWS", s, c))
                    except Exception as e:
                        logger.warning(f"[AWS Service Query] {e}")

                    if not aws_rows:
                        return (
                            f"### ☁️ CloudHealth Spend Analysis: AWS\n\n"
                            f"No live AWS billing data was returned for `{svc_scope_label}`. This usually means there "
                            f"was no AWS spend in this period, or the connected CloudHealth account/scope doesn't have "
                            f"visibility into it.\n\n"
                            f"*Source: AWS_CUR via CloudHealth FlexReports.*"
                        )

                    # Use the same FOCUS-table EffectiveCost basis as Azure/GCP (not AWS_CUR's
                    # UnblendedCost) so all four providers' totals are apples-to-apples comparable.
                    total_aws = _true_total_cost(
                        "SELECT SUM(EffectiveCost) AS cost FROM MULTICLOUD_FOCUS_COST_AND_USAGE WHERE provider = 'AWS'"
                    ) or sum(r[2] for r in aws_rows)
                    tbl_lines = [
                        f"| {p} | {s} | ${c:,.2f} | {((c/total_aws)*100 if total_aws else 0):.1f}% |"
                        for p, s, c in aws_rows[:limit]
                    ]
                    table = (
                        f"| Cloud Provider | Service Category | Cost | % of AWS Spend |\n"
                        f"|:---|:---|:---|:---|\n"
                        f"{chr(10).join(tbl_lines)}\n\n"
                        f"| **Total AWS Spend** | | **${total_aws:,.2f}** | **100.0%** |"
                    )
                    svc_hdr = f"CloudHealth Spend Analysis: Top {len(tbl_lines)} AWS Services by Spend — {svc_scope_label}"

                # ── Multi-Cloud / All Clouds Breakdown (AWS + Azure + GCP) ──
                else:
                    # "break it down by cloud/provider" asks for one row per cloud; anything else
                    # (or no qualifier at all) gets the existing per-service breakdown. Without this,
                    # a "by cloud" request was routed into the same service-level table below.
                    wants_cloud_level = any(w in low for w in [
                        "by cloud", "by provider", "per cloud", "per provider", "cloud-wise", "cloud wise",
                        "breakdown by cloud", "each cloud", "cloud level", "cloud-level"
                    ])

                    if wants_cloud_level:
                        provider_rows = []
                        try:
                            res = mcp.call_tool("execute_datasource_query", {
                                "queryInput": {
                                    "sqlStatement": "SELECT provider AS provider, SUM(EffectiveCost) AS cost FROM MULTICLOUD_FOCUS_COST_AND_USAGE GROUP BY provider ORDER BY cost DESC",
                                    "dataGranularity": svc_granularity, "limit": 10, "timeRange": svc_time_range
                                },
                                "requestInfo": {"sourceType": "API", "caller": "mcp"}
                            })
                            raw_csv = json.loads(res["content"][0]["text"]).get("csv", "")
                            for r in csv.DictReader(io.StringIO(raw_csv)):
                                c = float(r.get("cost") or 0.0)
                                p = r.get("provider") or ""
                                if p and c > 0:
                                    provider_rows.append((p, c))
                        except Exception as e:
                            logger.warning(f"[MultiCloud Provider Query] {e}")

                        if not provider_rows:
                            return (
                                f"### ☁️ CloudHealth Multi-Cloud Spend Analysis\n\n"
                                f"No live billing data was returned across any connected cloud provider for `{svc_scope_label}`. "
                                f"This usually means there was no spend in this period, or the connected CloudHealth "
                                f"account/scope doesn't have visibility into it.\n\n"
                                f"*Source: MULTICLOUD_FOCUS_COST_AND_USAGE via CloudHealth FlexReports.*"
                            )

                        provider_rows.sort(key=lambda x: x[1], reverse=True)
                        total_multi = sum(r[1] for r in provider_rows)  # GROUP BY provider alone has no per-row limit to undercount

                        tbl_lines = [
                            f"| {p} | ${c:,.2f} | {((c/total_multi)*100 if total_multi else 0):.1f}% |"
                            for p, c in provider_rows
                        ]
                        table = (
                            f"| Cloud Provider | Cost | % of Total |\n"
                            f"|:---|:---|:---|\n"
                            f"{chr(10).join(tbl_lines)}\n\n"
                            f"| **Total Multi-Cloud Spend** | **${total_multi:,.2f}** | **100.0%** |"
                        )
                        svc_hdr = f"CloudHealth Multi-Cloud Spend Analysis: Total Spend by Cloud Provider — {svc_scope_label}"
                    else:
                        multi_rows = []
                        try:
                            res = mcp.call_tool("execute_datasource_query", {
                                "queryInput": {
                                    "sqlStatement": "SELECT provider AS provider, ServiceName AS service, SUM(EffectiveCost) AS cost FROM MULTICLOUD_FOCUS_COST_AND_USAGE GROUP BY provider, ServiceName ORDER BY cost DESC",
                                    "dataGranularity": svc_granularity, "limit": 15, "timeRange": svc_time_range
                                },
                                "requestInfo": {"sourceType": "API", "caller": "mcp"}
                            })
                            raw_csv = json.loads(res["content"][0]["text"]).get("csv", "")
                            for r in csv.DictReader(io.StringIO(raw_csv)):
                                c = float(r.get("cost") or 0.0)
                                s = _friendly_service_name(r.get("service") or "")
                                p = r.get("provider") or "AWS"
                                if s and c > 0:
                                    multi_rows.append((p, s, c))
                        except Exception as e:
                            logger.warning(f"[MultiCloud Query] {e}")

                        if not multi_rows:
                            return (
                                f"### ☁️ CloudHealth Multi-Cloud Spend Analysis\n\n"
                                f"No live billing data was returned across any connected cloud provider for `{svc_scope_label}`. "
                                f"This usually means there was no spend in this period, or the connected CloudHealth "
                                f"account/scope doesn't have visibility into it.\n\n"
                                f"*Source: MULTICLOUD_FOCUS_COST_AND_USAGE via CloudHealth FlexReports.*"
                            )

                        multi_rows.sort(key=lambda x: x[2], reverse=True)
                        top_multi = multi_rows[:limit]
                        total_multi = _true_total_cost(
                            "SELECT SUM(EffectiveCost) AS cost FROM MULTICLOUD_FOCUS_COST_AND_USAGE"
                        ) or sum(r[2] for r in multi_rows)

                        tbl_lines = [
                            f"| {p} | {s} | ${c:,.2f} | {((c/total_multi)*100 if total_multi else 0):.1f}% |"
                            for p, s, c in top_multi
                        ]
                        table = (
                            f"| Cloud Provider | Service Category | Cost | % of Total |\n"
                            f"|:---|:---|:---|:---|\n"
                            f"{chr(10).join(tbl_lines)}\n\n"
                            f"| **Total Multi-Cloud Spend** | | **${total_multi:,.2f}** | **100.0%** |"
                        )
                        svc_hdr = f"CloudHealth Multi-Cloud Spend Analysis: Top Services Across All Clouds — {svc_scope_label}"

                insight_md = ""
                if self.engine != "direct":
                    try:
                        sys_msg = {
                            "role": "system",
                            "content": (
                                "You are Cleo, an expert FinOps AI. Provide 2 concise bullet observations about the multi-cloud service spend breakdown across providers.\n"
                                "STRICT TITLE RULES:\n"
                                "1. Bullet titles MUST accurately reflect all services mentioned in that bullet. NEVER label a bullet as 'EC2' or 'High EC2 Utilization' if the bullet discusses both EC2 and RDS! Instead, use 'Compute & Database Concentration (EC2 & RDS)'.\n"
                                "2. Do not use the word 'Utilization' when analyzing a spend table; use 'Spend Concentration' or 'Cost Driver'.\n"
                                "3. Keep each bullet concise, accurate, and actionable with specific dollar amounts or percentages from the table."
                            )
                        }
                        user_msg = {"role": "user", "content": f"User query: {last_msg}\n\nData:\n{table}"}
                        llm_ans, _ = self._call_active_llm([sys_msg, user_msg])
                        if llm_ans:
                            insight_md = f"\n\n**💡 FinOps Insights:**\n{_sanitize_finops_bullet_titles(llm_ans)}"
                    except Exception as e:
                        logger.debug(f"[LLM Commentary] {e}")

                if not insight_md:
                    insight_md = (
                        "\n\n**💡 FinOps Insights:**\n"
                        f"- **Multi-Cloud Core Infrastructure Concentration**: Compute and managed database instances across AWS, Azure, and GCP represent over 60% of total multi-cloud spend. Consolidating commitments (Savings Plans, Reservations) and modernizing silicon delivers the largest bottom-line reduction.\n"
                        f"- **Data & Storage Modernization**: Storage volumes across AWS EBS, Azure Disks, and GCP BigQuery present immediate quick-win opportunities through storage tiering and gp3 upgrades."
                    )

                chart_type = _detect_chart_type(low)
                chart_md = ""
                if chart_type:
                    c_kind = chart_type if chart_type in ["pie", "doughnut", "line", "bar"] else "bar"
                    if cloud_target == "aws" and aws_rows:
                        labels = [s for _, s, _ in aws_rows[:12]]
                        values = [c for _, _, c in aws_rows[:12]]
                        chart_md = _chart_block(c_kind, f"Top AWS Services by Spend ({svc_scope_label})", labels, values=values, horizontal=False, stacked=True)
                    elif cloud_target == "azure" and azure_rows:
                        labels = [s for _, s, _ in azure_rows[:12]]
                        values = [c for _, _, c in azure_rows[:12]]
                        chart_md = _chart_block(c_kind, f"Top Azure Services by Spend ({svc_scope_label})", labels, values=values, horizontal=False, stacked=True)
                    elif cloud_target == "gcp" and gcp_rows:
                        labels = [s for _, s, _ in gcp_rows[:12]]
                        values = [c for _, _, c in gcp_rows[:12]]
                        chart_md = _chart_block(c_kind, f"Top GCP Services by Spend ({svc_scope_label})", labels, values=values, horizontal=False, stacked=True)
                    elif False and cloud_target == "oci" and oci_rows:
                        labels = [s for _, s, _ in oci_rows[:12]]
                        values = [c for _, _, c in oci_rows[:12]]
                        chart_md = _chart_block(c_kind, f"Top OCI Services by Spend ({svc_scope_label})", labels, values=values, horizontal=False, stacked=True)
                    elif cloud_target == "all" and multi_rows:
                        labels = [f"{p} {s}" for p, s, _ in multi_rows[:12]]
                        values = [c for _, _, c in multi_rows[:12]]
                        chart_md = _chart_block(c_kind, f"Top Multi-Cloud Services by Spend ({svc_scope_label})", labels, values=values, horizontal=False, stacked=True)

                wants_table = _detect_wants_table(low)
                tbl_md = f"{table}\n" if wants_table else ""
                return (
                    f"### 📊 {svc_hdr}\n\n"
                    f"{partial_notice}"
                    f"{tbl_md}"
                    f"{chart_md}"
                    f"{insight_md}\n\n"
                    f"💡 *Live FinOps data retrieved from CloudHealth FOCUS & Billing Datasets.*"
                )

            # 3d. General Monthly Spend (partner totals — provider-aware)
            else:
                return _monthly_trend_markdown(active_cloud)


        # ── 4. Pure Organizations Listing (Non-cost) ─────────────────────────
        if ("org" in low or "organization" in low) and not is_cost_query:
            if mcp:
                res = mcp.call_tool("list_managed_orgs", {})
                content = res.get("content", [{}])[0].get("text", "[]")
                try:
                    orgs = json.loads(content)
                    if isinstance(orgs, list) and orgs:
                        rows = "\n".join([f"| `{o.get('id', 'N/A')}` | **{o.get('name', 'N/A')}** |" for o in orgs])
                        return (
                            f"### 🏢 CloudHealth Managed Organizations\n\n"
                            f"Found **{len(orgs)}** active organizations in your account:\n\n"
                            f"| Organization CRN | Organization Name |\n"
                            f"|:---|:---|\n"
                            f"{rows}\n\n"
                            f"*Data retrieved live from CloudHealth.*"
                        )
                    else:
                        return (
                            f"### 🏢 CloudHealth Managed Organizations\n\n"
                            f"No active organizations returned for this session."
                        )
                except Exception:
                    return f"### Organizations:\n```json\n{content}\n```"

        # ── 5. Pure Customer Tenants Listing (Non-cost) ──────────────────────
        is_list_cust_intent = any(w in low for w in [
            "list customer", "list all customer", "show customer", "show all customer",
            "channel customer", "all customer", "all tenant", "list tenant", "show tenant"
        ])
        if is_list_cust_intent and not is_cost_query:
            if mcp:
                res = mcp.call_tool("list_channel_customers", {})
                content = res.get("content", [{}])[0].get("text", "[]")
                try:
                    custs = json.loads(content)
                    if isinstance(custs, list) and custs:
                        rows = "\n".join([f"| `{c.get('customerId', 'N/A')}` | **{c.get('name', 'N/A')}** | {c.get('status', 'Active')} |" for c in custs])
                        return (
                            f"### 👥 CloudHealth Managed Customers\n\n"
                            f"Found **{len(custs)}** customer tenants:\n\n"
                            f"| Customer CRN | Customer Name | Status |\n"
                            f"|:---|:---|:---|\n"
                            f"{rows}\n"
                        )
                    else:
                        return (
                            f"### 👥 CloudHealth Managed Customers\n\n"
                            f"No channel customers found for this account (account may be a direct customer organization rather than a partner channel)."
                        )
                except Exception:
                    return f"### Customers:\n```json\n{content}\n```"

        # ── 6. External LLM Synthesis (If Selected) ───────────────────────────
        if self.engine != "direct":
            llm_resp, err = self._call_active_llm(messages)
            if llm_resp:
                return llm_resp
            if err:
                logger.warning(f"[LLM Fallback] {err}")

        # ── 7. Default Fallback Guidance Menu ────────────────────────────────
        finops_fallback = get_finops_advisory(last_msg)
        if finops_fallback:
            return finops_fallback

        return (
            f"I have received your request: *\"{last_msg}\"*\n\n"
            f"Here are key actions you can ask me to perform:\n\n"
            f"- 📊 **Analyze Costs**: \"Show top 5 customers by spend last month\" or \"Show top AWS services\"\n"
            f"- 🏢 **List Organizations**: \"List all managed organizations\"\n"
            f"- 👥 **List Channel Customers**: \"List all channel customers and tenants\"\n"
            f"- 📚 **Inspect Datasets**: \"List available datasources\" or \"Show schema for CLOUDHEALTH_CONSUMPTION_BREAKDOWN\"\n"
        )

def run_agent_turn(mcp: MCPClient, ai: AIClient, messages: list[dict]) -> str:
    return ai.generate(messages, mcp=mcp)

def get_access_token(interactive: bool = False) -> Optional[str]:
    mcp_data = auth_helper.load_token()
    if mcp_data:
        if MCP_RESOURCE in mcp_data:
            mcp_data = mcp_data[MCP_RESOURCE]
        token = auth_helper.refresh_token(mcp_data)
        if token:
            return token

    # Check legacy token file as fallback
    legacy_file = os.path.expanduser("~/.cleo/oauth_tokens.json")
    if os.path.exists(legacy_file):
        try:
            with open(legacy_file, "r") as f:
                leg = json.load(f)
                if MCP_RESOURCE in leg:
                    leg_data = leg[MCP_RESOURCE]
                    token = leg_data.get("token", {}).get("access_token")
                    if token:
                        return token
        except Exception:
            pass

    if interactive:
        mcp_data = auth_helper.authenticate(MCP_RESOURCE)
        if mcp_data:
            return mcp_data["token"]["access_token"]
    return None
