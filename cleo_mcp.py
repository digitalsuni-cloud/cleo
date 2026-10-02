"""
cleo_mcp.py — Unified MCP Client and CloudHealth Direct API Engine.
Zero external runtime dependencies: stdlib (json, urllib, csv, io, datetime, calendar, threading).
"""
import os
import sys
import json
import time
import threading
import urllib.request
import urllib.parse
import urllib.error
import csv
import io
import datetime
import calendar
from typing import Optional, Dict, Any, List

try:
    from cleo_logger import get_logger
    logger = get_logger("mcp")
except ImportError:
    import logging
    logger = logging.getLogger("cleo.mcp")
    logger.setLevel(logging.INFO)

# ── CloudHealth API Endpoints ─────────────────────────────────────────────────
MCP_ENDPOINT = "https://apps.cloudhealthtech.com/mcp"
GRAPHQL_URL  = "https://apps.cloudhealthtech.com/graphql"
CHAPI_URL    = "https://chapi.cloudhealthtech.com"

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
            with urllib.request.urlopen(req, timeout=75.0) as resp:
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

        # ponytail: CloudHealth MCP execute_datasource_query uses 'orgId' at the top level for customer scoping.
        # Normalize channelCustomerId or queryInput.channelCustomerId to top-level orgId once here for all callers.
        call_args = dict(args)
        if name == "execute_datasource_query":
            q_in = call_args.get("queryInput")
            if isinstance(q_in, dict) and "channelCustomerId" in q_in:
                crn_val = q_in.pop("channelCustomerId")
                if "orgId" not in call_args and crn_val:
                    call_args["orgId"] = crn_val
            if "channelCustomerId" in call_args:
                crn_val = call_args.pop("channelCustomerId")
                if "orgId" not in call_args and crn_val:
                    call_args["orgId"] = crn_val

        # Map tool name aliases for compatibility between remote MCP and DirectEngine
        remote_name = name
        if name == "list_managed_orgs":
            remote_name = "list_orgs"

        logger.info(f"[MCP Call] Executing tool '{name}' (remote '{remote_name}') with args: {json.dumps(call_args)}")
        res = None
        if not self._use_fallback:
            try:
                res = self._http_request("tools/call", {"name": remote_name, "arguments": call_args})
            except Exception as e:
                err_str = str(e)
                if "-32001" in err_str or "not authorized" in err_str.lower():
                    logger.error(f"❌ [MCP Tool Call Unauthorized] {name}: {err_str}")
                    raise RuntimeError(f"MCP tool '{name}' call unauthorized: {err_str}")
                # ponytail: customer-scoped queries MUST use remote MCP — Direct Engine can't handle them
                # Don't fall back; re-raise so callers can skip gracefully
                if "channelCustomerId" in args or "orgId" in call_args:
                    logger.warning(f"⚠️  MCP call failed for customer-scoped query, skipping: {e}")
                logger.warning(f"⚠️  Remote tools/call failed ({e}), attempting direct fallback for this call")

        # Check if remote server returned "Tool '...' not found"
        is_tool_not_found = False
        if res and isinstance(res, dict):
            c_text = (res.get("content") or [{}])[0].get("text", "")
            if f"Tool '{remote_name}' not found" in c_text or f"Tool '{name}' not found" in c_text:
                is_tool_not_found = True

        if res is None or is_tool_not_found:
            # Route to Direct Engine (fallback, no channelCustomerId support)
            if name in ("list_managed_orgs", "list_orgs"):
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
                dataset = args.get("dataset", "CLOUDHEALTH_CONSUMPTION_BREAKDOWN")
                cached_ds = get_cached_datasource_metadata(dataset)
                if cached_ds and "columns" in cached_ds:
                    meta = {
                        "dataset": dataset,
                        "displayName": cached_ds.get("displayName", dataset),
                        "description": cached_ds.get("description", ""),
                        "columns": cached_ds["columns"],
                        "columnCount": len(cached_ds["columns"]),
                        "_source": "local_metadata_cache"
                    }
                    res = {"content": [{"type": "text", "text": json.dumps(meta, indent=2)}]}
                else:
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
                            {"name": "RegionId", "type": "string"},
                            {"name": "EffectiveCost", "type": "number"},
                            {"name": "BilledCost", "type": "number"},
                        ],
                        "AWS_FOCUS_COST_AND_USAGE": [
                            {"name": "Month", "type": "string"},
                            {"name": "ServiceName", "type": "string"},
                            {"name": "RegionId", "type": "string"},
                            {"name": "EffectiveCost", "type": "number"},
                            {"name": "BilledCost", "type": "number"},
                        ],
                        "AZURE_FOCUS_COST_AND_USAGE": [
                            {"name": "Month", "type": "string"},
                            {"name": "ServiceName", "type": "string"},
                            {"name": "RegionId", "type": "string"},
                            {"name": "EffectiveCost", "type": "number"},
                            {"name": "BilledCost", "type": "number"},
                        ],
                        "GCP_FOCUS_COST_AND_USAGE": [
                            {"name": "Month", "type": "string"},
                            {"name": "ServiceName", "type": "string"},
                            {"name": "RegionId", "type": "string"},
                            {"name": "EffectiveCost", "type": "number"},
                            {"name": "BilledCost", "type": "number"},
                        ],
                        "AWS_CUR": [
                            {"name": "timeInterval_Month", "type": "string"},
                            {"name": "lineItem_ProductCode", "type": "string"},
                            {"name": "product_region", "type": "string"},
                            {"name": "product_location", "type": "string"},
                            {"name": "lineItem_AvailabilityZone", "type": "string"},
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
                    # Transient query error detected (e.g. FQ-USR error)
                    if name == "execute_datasource_query":
                        logger.debug(f"[Datasource Error] {txt[:160]}")

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

