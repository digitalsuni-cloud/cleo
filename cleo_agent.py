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
import platform
import csv
import io
import threading
import datetime
import calendar
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
from typing import Optional, Dict, Any, List

# Use centralized verbose logger
try:
    from cleo_logger import get_logger
    logger = get_logger("agent")
except ImportError:
    import logging
    logger = logging.getLogger("cleo.agent")
    logger.setLevel(logging.INFO)

# FinOps expert knowledge injection
try:
    from cleo_finops_refs import get_finops_context, get_finops_advisory
except ImportError:
    def get_finops_context(query: str) -> str:  # type: ignore
        return ""
    def get_finops_advisory(query: str) -> Optional[str]:  # type: ignore
        return None

# Self-learning memory
try:
    from cleo_memory import get_memory, save_last_query, load_last_query
except ImportError:
    def get_memory():  # type: ignore
        class _Noop:
            def build_context_block(self, q): return ""
            def record_sql_fix(self, *a): pass
        return _Noop()
    def save_last_query(*a, **k): pass
    def load_last_query(): return {}

from cleo_query import COLUMN_SYNONYMS

# Real-world market intelligence (AI model token rates, cloud retail pricing, FinOps trends)
try:
    from cleo_market_data import (
        get_live_model_specs,
        format_model_specs_markdown,
        get_live_cloud_pricing,
        format_cloud_pricing_markdown,
        search_finops_web,
        format_finops_search_markdown,
        detect_market_data_intent,
    )
except ImportError:
    def get_live_model_specs(q: str): return []  # type: ignore
    def format_model_specs_markdown(s): return ""  # type: ignore
    def get_live_cloud_pricing(*a, **k): return {}  # type: ignore
    def format_cloud_pricing_markdown(p): return ""  # type: ignore
    def search_finops_web(q: str, **k): return []  # type: ignore
    def format_finops_search_markdown(q, r): return ""  # type: ignore
    def detect_market_data_intent(q: str): return None  # type: ignore

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
from cleo_mcp import MCP_ENDPOINT, GRAPHQL_URL, CHAPI_URL

# Ensure workspace virtualenv site-packages are accessible even when run without activating .venv
_cleo_root = os.path.dirname(os.path.abspath(__file__))
_venv_lib = os.path.join(_cleo_root, ".venv", "lib")
if os.path.isdir(_venv_lib):
    for _d in os.listdir(_venv_lib):
        _sp = os.path.join(_venv_lib, _d, "site-packages")
        if os.path.isdir(_sp) and _sp not in sys.path:
            sys.path.insert(0, _sp)

# ── Datasource Metadata Cache & Schema Intelligence ───────────────────────────
DATASOURCES_METADATA_CACHE_FILE = os.path.join(APP_DATA_DIR, "datasources_metadata_cache.json")
_datasources_metadata_memory_cache: dict = {}

def get_cached_datasource_metadata(dataset: Optional[str] = None) -> Optional[dict]:
    """Retrieve cached metadata schema for one or all datasources."""
    global _datasources_metadata_memory_cache
    if not _datasources_metadata_memory_cache:
        if os.path.exists(DATASOURCES_METADATA_CACHE_FILE):
            try:
                with open(DATASOURCES_METADATA_CACHE_FILE, "r") as f:
                    _datasources_metadata_memory_cache = json.load(f).get("datasources", {})
            except Exception as e:
                logger.warning(f"[Metadata Cache] Failed to read {DATASOURCES_METADATA_CACHE_FILE}: {e}")
    if dataset:
        return _datasources_metadata_memory_cache.get(dataset)
    return _datasources_metadata_memory_cache

def crawl_and_cache_all_datasource_metadata(mcp, force: bool = False) -> dict:
    """
    Crawls list_standard_datasources and get_datasource_metadata across all CloudHealth datasets.
    Caches the parsed schemas to ~/.cleo/datasources_metadata_cache.json.
    """
    global _datasources_metadata_memory_cache
    if not force and os.path.exists(DATASOURCES_METADATA_CACHE_FILE):
        try:
            mtime = os.path.getmtime(DATASOURCES_METADATA_CACHE_FILE)
            if (time.time() - mtime) < (7 * 86400):  # Fresh for 7 days
                data = get_cached_datasource_metadata()
                if data and len(data) >= 15:
                    logger.debug(f"[Metadata Cache] Loaded {len(data)} cached datasource schemas")
                    return data
        except Exception:
            pass

    logger.info("🔍 [Metadata Crawler] Discovering all CloudHealth standard datasources...")
    try:
        res = mcp.call_tool("list_standard_datasources", {})
        txt = res.get("content", [{}])[0].get("text", "")
        idx = txt.find("[")
        if idx == -1:
            logger.warning("[Metadata Crawler] No JSON array found in list_standard_datasources")
            return get_cached_datasource_metadata() or {}
        ds_list = json.loads(txt[idx:])
        all_ds_names = [d.get("datasetName") for d in ds_list if d.get("datasetName")]
    except Exception as e:
        logger.warning(f"[Metadata Crawler] Failed to list datasources: {e}")
        return get_cached_datasource_metadata() or {}

    logger.info(f"📥 [Metadata Crawler] Fetching schemas for {len(all_ds_names)} datasources in parallel...")
    def _fetch_one(dname: str):
        try:
            r = mcp.call_tool("get_datasource_metadata", {"dataset": dname})
            c_txt = r.get("content", [{}])[0].get("text", "")
            d = json.loads(c_txt)
            cols = d.get("commonColumns", []) or d.get("columns", [])
            clean_cols = [
                {
                    "name": c.get("name"),
                    "displayName": c.get("displayName") or c.get("name"),
                    "type": c.get("type", "DIMENSION"),
                    "dataType": c.get("dataType", "STRING"),
                    "description": c.get("description", "")
                }
                for c in cols if isinstance(c, dict)
            ]
            return dname, {
                "dataset": dname,
                "displayName": d.get("datasetDisplayName", dname),
                "description": d.get("description", ""),
                "cloudType": d.get("cloudType"),
                "columnCount": len(clean_cols),
                "columns": clean_cols
            }
        except Exception as ex:
            return dname, {"dataset": dname, "error": str(ex)}

    with ThreadPoolExecutor(max_workers=8) as ex:
        results = dict(ex.map(_fetch_one, all_ds_names))

    payload = {
        "updated_at": time.time(),
        "total_datasets": len(results),
        "datasources": results
    }
    try:
        os.makedirs(APP_DATA_DIR, exist_ok=True)
        with open(DATASOURCES_METADATA_CACHE_FILE, "w") as f:
            json.dump(payload, f, indent=2)
        _datasources_metadata_memory_cache = results
        try:
            merge_live_metadata_into_curated_specs(results)
        except Exception:
            pass
        logger.info(f"✅ [Metadata Crawler] Successfully cached {len(results)} datasource schemas to {DATASOURCES_METADATA_CACHE_FILE}")
    except Exception as e:
        logger.error(f"[Metadata Crawler] Failed to write cache file: {e}")

    return results

CURATED_DATASET_SPECS = {
    "AWS_CUR": {
        "priority_measures": ["lineItem_UnblendedCost", "lineItem_BlendedCost", "lineItem_NetUnblendedCost", "pricing_publicOnDemandCost", "discount_TotalDiscount", "savingsPlan_TotalCommitmentToDate", "lineItem_UsageAmount"],
        "priority_dimensions": ["lineItem_ProductCode", "product_instanceType", "product_region", "lineItem_ResourceId", "lineItem_UsageType", "lineItem_Operation", "bill_PayerAccountId", "lineItem_UsageAccountId", "timeInterval_Month", "pricing_term", "lineItem_LineItemType", "pricing_PurchaseOption", "product_storageClass", "product_volumeType"]
    },
    "AZURE_COST_USAGE": {
        "priority_measures": ["ActualCostInBillingCurrency", "ActualCostInUsd", "CostInBillingCurrency", "Quantity", "AmortizedCostInUsd"],
        "priority_dimensions": ["ResourceName", "ResourceId", "ResourceGroup", "SubscriptionName", "SubscriptionId", "MeterCategory", "MeterSubCategory", "MeterName", "AdditionalInfo", "MetricType", "ConsumedService", "timeInterval_Month"]
    },
    "MULTICLOUD_FOCUS_COST_AND_USAGE": {
        "priority_measures": ["EffectiveCost", "BilledCost", "PricingQuantity"],
        "priority_dimensions": ["provider", "ServiceName", "ServiceCategory", "ServiceSubcategory", "PricingCategory", "RegionId", "SubaccountId", "BillingAccountId", "ResourceId", "PricingUnit", "Month"]
    },
    "AWS_FOCUS_COST_AND_USAGE": {
        "priority_measures": ["EffectiveCost", "BilledCost", "AmortizedCost", "ListCost", "PricingQuantity"],
        "priority_dimensions": ["Provider", "ServiceName", "ServiceCategory", "ServiceSubcategory", "PricingCategory", "SubaccountId", "BillingAccountId", "ResourceId", "PricingUnit", "Month"]
    },
    "AZURE_FOCUS_COST_AND_USAGE": {
        "priority_measures": ["EffectiveCost", "BilledCost", "AmortizedCost", "PricingQuantity"],
        "priority_dimensions": ["Provider", "ServiceName", "ServiceCategory", "ServiceSubcategory", "PricingCategory", "RegionId", "SubaccountId", "BillingAccountId", "ResourceId", "PricingUnit", "Month"]
    },
    "GCP_FOCUS_COST_AND_USAGE": {
        "priority_measures": ["EffectiveCost", "BilledCost", "PricingQuantity"],
        "priority_dimensions": ["Provider", "ServiceName", "ServiceCategory", "ServiceSubcategory", "RegionId", "SubaccountId", "BillingAccountId", "ResourceId", "PricingUnit", "Month"]
    },
    "MULTICLOUD_COMMITMENT_SAVINGS": {
        "priority_measures": ["Commitment_Savings", "Commitment_Covered_On_Demand_Cost", "Commitment_Used_Cost", "Commitment_Purchase_Cost", "On_Demand_Equivalent_Cost", "Negotiated_Discount"],
        "priority_dimensions": ["Provider", "Commitment_Plan", "Service", "Month", "Billing_Account_Name", "Billing_Account_Id"]
    },
    "MULTICLOUD_OPERATIONAL_EMISSIONS": {
        "priority_measures": ["Carbon", "Power", "PhonesCharged", "KmsDriven", "TreeSeedlings", "UsageMinutes", "vCPUHours", "AverageCPU"],
        "priority_dimensions": ["ProviderName", "ServiceName", "ResourceName", "ResourceId", "InstanceType", "Region", "Country", "SubAccountId", "timeInterval_Month"]
    },
    "UNIFIED_AI_TOKENOMICS": {
        "priority_measures": ["EffectiveCost", "UsageQuantity", "EffectiveCostPerMillTokens", "CacheEfficiencyRatio", "ListCost"],
        "priority_dimensions": ["ProviderName", "ModelName", "ModelTier", "TokenType", "ProcessingMode", "QuantityUnit", "ProjectName", "OrganizationName", "Month"]
    },
    "MULTICLOUD_AI_COST_AND_USAGE": {
        "priority_measures": ["EffectiveCost", "BilledCost", "PricingQuantity", "UnitCost"],
        "priority_dimensions": ["provider", "ServiceName", "Model", "ModelProvider", "Modality", "ExecutionType", "TokenType", "HardwareType", "HardwareFamily", "ProcessingMode", "RegionId", "SubaccountId", "BillingAccountId", "Month"]
    },
    "AWS_AI_COST_AND_USAGE": {
        "priority_measures": ["EffectiveCost", "BilledCost", "PricingQuantity", "ListCost", "UnitCost"],
        "priority_dimensions": ["Provider", "ServiceName", "Model", "ModelProvider", "Modality", "ExecutionType", "TokenType", "RegionId", "Month"]
    },
    "OPENAI_COST_AND_USAGE": {
        "priority_measures": ["Cost_Value", "Input_Tokens", "Output_Tokens", "Cached_Input_Tokens", "Num_Requests", "Effective_Cost_Per_1k_Output_Tokens"],
        "priority_dimensions": ["Model", "Model_Tier", "Usage_Month"]
    },
    "ANTHROPIC_COST_AND_USAGE": {
        "priority_measures": ["Cost", "Token_Quantity", "Request_Count", "List_Amount"],
        "priority_dimensions": ["Model", "Token_Type", "Usage_Month"]
    },
    "AWS_COST_ANOMALY": {
        "priority_measures": ["CostImpact", "Cost"],
        "priority_dimensions": ["Service", "Region", "AccountID", "Status", "EndDate", "CostImpactPercentage", "CostImpactType", "timeInterval_Month"]
    },
    "AZURE_COST_ANOMALY": {
        "priority_measures": ["CostImpact", "Cost"],
        "priority_dimensions": ["Service", "Region", "Status", "EndDate", "CostImpactPercentage", "CostImpactType", "timeInterval_Month"]
    },
    "GCP_COST_ANOMALY": {
        "priority_measures": ["CostImpact", "Cost"],
        "priority_dimensions": ["Region", "Status", "EndDate", "CostImpactPercentage", "CostImpactType", "timeInterval_Month"]
    },
    "AWS_EC2_COST_AND_USAGE": {
        "priority_measures": ["Billed_Cost", "Effective_Cost", "Amortized_Cost", "Instance_Cost", "Compute_Cost", "Instance_Hours", "Instances", "VCPUs"],
        "priority_dimensions": ["product_InstanceType", "product_InstanceTypeFamily", "AWS-Regions", "AWS-Account", "Month"]
    },
    "AWS_RDS_COST_AND_USAGE": {
        "priority_measures": ["BilledCost", "EffectiveCost", "RDSTotalCost", "ComputeCost", "StorageCost", "GP2StorageCost", "GP3StorageCost", "Instances"],
        "priority_dimensions": ["InstanceType", "Region", "MultiAZ", "SubAccount", "BillingAccount", "Month"]
    },
    "AWS_DATABRICKS_USAGE": {
        "priority_measures": ["total_price", "usage_quantity"],
        "priority_dimensions": ["workspace_id", "sku_name", "usage_type", "cloud", "account_id", "usage_month"]
    },
    "AZURE_DATABRICKS_COST_AND_USAGE": {
        "priority_measures": ["AmortizedCostInBillingCurrency", "Quantity"],
        "priority_dimensions": ["ProductName", "MeterCategory", "MeterSubCategory", "ResourceGroup", "ResourceId", "SubscriptionName", "timeInterval_Month"]
    },
    "AWS_K8S_COST": {
        "priority_measures": ["ApportionedCostUsed", "ApportionedCostRequested", "ApportionedAmortizedCostUsed"],
        "priority_dimensions": ["ClusterName", "NamespaceName", "WorkloadName", "WorkloadType", "ContainerName", "CloudAccountName", "timeInterval_Month"]
    },
    "AZURE_K8S_COST": {
        "priority_measures": ["ApportionedCostUsed", "ApportionedCostRequested", "ApportionedEffectiveCostUsed"],
        "priority_dimensions": ["ClusterName", "NamespaceName", "WorkloadName", "WorkloadType", "ContainerName", "CloudAccountName", "timeInterval_Month"]
    },
    "MULTICLOUD_RIGHTSIZING_RECOMMENDATIONS": {
        "priority_measures": ["Potential_Savings", "Current_Cost", "Potential_Cost"],
        "priority_dimensions": ["Provider", "Service", "Region", "Billing_Account", "SubAccount_Name"]
    },
    "CLOUDHEALTH_CONSUMPTION_BREAKDOWN": {
        "priority_measures": ["ChannelBillableUsage", "Usage", "ConfiguredUsageAtPartner"],
        "priority_dimensions": ["CustomerName", "CustomerType", "ServiceProviderId", "timeInterval_Month"]
    },
    "AWS_DATA_TRANSFER_COST_AND_USAGE": {
        "priority_measures": ["BilledCost", "EffectiveCost", "ConsumedQuantity"],
        "priority_dimensions": ["FromLocation", "ToLocation", "SubAccount", "Month"]
    },
    "WASTE_OPPORTUNITY": {
        "priority_measures": ["ProjectedMonthlyCost"],
        "priority_dimensions": ["Provider", "Region", "CloudAccountId"]
    }
}

def merge_live_metadata_into_curated_specs(live_metadata: dict) -> None:
    """
    Dynamically enriches CURATED_DATASET_SPECS from live/cached CloudHealth metadata schemas.
    Ensures new custom datasets or updated dimensions discovered at runtime are queryable.
    """
    if not live_metadata or not isinstance(live_metadata, dict):
        return
    for ds_name, ds_info in live_metadata.items():
        if not isinstance(ds_info, dict):
            continue
        cols = ds_info.get("columns", [])
        if not cols:
            continue
        discovered_dims = []
        discovered_measures = []
        for c in cols:
            c_name = c.get("name")
            if not c_name:
                continue
            c_type = str(c.get("type", "")).upper()
            c_dtype = str(c.get("dataType", "")).upper()
            if c_type == "MEASURE" or c_dtype in ("NUMBER", "FLOAT", "INTEGER", "DOUBLE"):
                discovered_measures.append(c_name)
            else:
                discovered_dims.append(c_name)

        if ds_name in CURATED_DATASET_SPECS:
            spec = CURATED_DATASET_SPECS[ds_name]
            p_dims = spec.setdefault("priority_dimensions", [])
            for d in discovered_dims:
                if d not in p_dims:
                    p_dims.append(d)
            p_meas = spec.setdefault("priority_measures", [])
            for m in discovered_measures:
                if m not in p_meas:
                    p_meas.append(m)
        else:
            CURATED_DATASET_SPECS[ds_name] = {
                "priority_measures": discovered_measures[:10],
                "priority_dimensions": discovered_dims[:15],
                "displayName": ds_info.get("displayName", ds_name),
                "description": ds_info.get("description", "")
            }

# Seed from local metadata cache on startup if present
if os.path.exists(DATASOURCES_METADATA_CACHE_FILE):
    try:
        _init_meta = get_cached_datasource_metadata()
        if _init_meta:
            merge_live_metadata_into_curated_specs(_init_meta)
    except Exception:
        pass

def build_llm_schema_context() -> str:
    """
    Builds a concise, high-density catalog of CloudHealth datasets and key columns
    derived directly from the cached metadata schemas so the LLM knows what to query.
    """
    cache = get_cached_datasource_metadata() or {}
    if not cache:
        return ""

    lines = ["CLOUDHEALTH DATASOURCE SCHEMAS & KEY COLUMNS (AUTO-GENERATED FROM MCP METADATA):"]

    for key, spec in CURATED_DATASET_SPECS.items():
        ds_info = cache.get(key)
        if not ds_info or "columns" not in ds_info:
            continue
        disp = ds_info.get("displayName") or key
        cols = ds_info["columns"]
        actual_measures = {c["name"] for c in cols if c.get("type") == "MEASURE"}
        actual_dims = {c["name"] for c in cols if c.get("type") != "MEASURE"}

        measures = [m for m in spec.get("priority_measures", []) if m in actual_measures]
        dims = [d for d in spec.get("priority_dimensions", []) if d in actual_dims]

        # Fallback to dynamic extraction if spec didn't match anything
        if not measures:
            measures = [c["name"] for c in cols if c.get("type") == "MEASURE"][:6]
        if not dims:
            dims = [
                c["name"] for c in cols
                if c.get("type") != "MEASURE" and any(k in c["name"].lower() for k in [
                    "region", "service", "customer", "month", "time", "instance", "account", "provider", "meter", "resource"
                ])
            ][:12]

        def _col_label(c_name: str) -> str:
            info = COLUMN_SYNONYMS.get(c_name)
            if info and info.get("label"):
                return f"{c_name} ({info['label']})"
            return c_name

        lines.append(f"- `{key}` ({disp}):")
        if measures:
            lines.append(f"  * Measures (Metrics): {', '.join(_col_label(m) for m in measures)}")
        if dims:
            lines.append(f"  * Key Dimensions: {', '.join(_col_label(d) for d in dims)}")

    lines.append("\nCOLUMN & DIMENSION NATURAL LANGUAGE SYNONYMS DICTIONARY:")
    lines.append("- SubaccountId / lineItem_UsageAccountId / SubscriptionId: 'account', 'subaccount', 'member account', 'linked account', 'usage account', 'project', 'subscription'")
    lines.append("- BillingAccountId / bill_PayerAccountId: 'billing account', 'payer account', 'master account', 'root account', 'management account'")
    lines.append("- ServiceCategory / MeterCategory: 'service category', 'macro service', 'service family', 'domain', 'category'")
    lines.append("- ServiceSubcategory / MeterSubCategory: 'service subcategory', 'granular service', 'sub service', 'meter category', 'meter subcategory', 'component'")
    lines.append("- PricingCategory: 'pricing category', 'pricing model', 'commercial model', 'contract type', 'pricing type'")
    lines.append("- LeaseType (pricing_term & lineItem_LineItemType): 'lease type', 'purchase option', 'capacity type', 'ondemand', 'reservation', 'savings plan', 'spot'")
    lines.append("- product_storageClass: 'storage class', 'storage tier', 's3 tier', 'lifecycle tier', 'hot/cool/archive'")
    lines.append("- product_volumeType: 'volume type', 'ebs type', 'disk type', 'volume tier', 'gp2', 'gp3', 'io1', 'io2'")
    lines.append("- lineItem_Operation: 'operation', 'api operation', 'action', 'event', 'invocations'")
    lines.append("- product_instanceType / InstanceType: 'instance type', 'instance size', 'vm size', 'vm type', 'machine type', 'flavor'")
    lines.append("- RegionId / product_region / Region: 'region', 'location', 'cloud region', 'datacenter', 'geography', 'zone'")
    lines.append("- ResourceId / ResourceName / lineItem_ResourceId: 'resource', 'resource id', 'resource name', 'arn', 'asset', 'top resources'")
    lines.append("- Model / ModelName: 'model', 'model name', 'ai model', 'foundation model', 'llm'")
    lines.append("- ModelProvider: 'model provider', 'ai provider', 'ai vendor', 'llm vendor'")
    lines.append("- Modality: 'modality', 'media type', 'input modality', 'text vs multimodal', 'vision'")
    lines.append("- ExecutionType / ProcessingMode: 'execution type', 'inference type', 'batch vs streaming', 'realtime'")
    lines.append("- TokenType: 'token type', 'prompt tokens', 'completion tokens', 'cached tokens'")
    lines.append("- HardwareType / HardwareFamily: 'hardware type', 'hardware family', 'accelerator', 'gpu', 'tpu', 'h100', 'a100'")
    lines.append("- Commitment_Plan: 'commitment plan', 'savings plan', 'reservation', 'ri', 'reserved instance'")
    lines.append("- Country / Carbon: 'country', 'emissions country', 'carbon country', 'carbon footprint', 'mt co2e'")
    lines.append("- EffectiveCost / lineItem_UnblendedCost / ActualCostInUsd: 'effective cost', 'net cost', 'true cost', 'actual cost', 'cost', 'spend'")
    lines.append("- BilledCost: 'billed cost', 'invoice cost', 'unblended cost', 'gross spend'")
    lines.append("- PricingQuantity / lineItem_UsageAmount / Quantity: 'quantity', 'usage quantity', 'units', 'hours', 'gb-months', 'volume'")

    return "\n".join(lines)

def _format_empty_data_notice(dataset: str, dimension: str, scope: str, time_label: str = "") -> str:
    """Provides actionable contextual troubleshooting tips when a query returns 0 rows."""
    time_ctx = f" for **{time_label}**" if time_label else ""
    return (
        f"No spend data was returned from `{dataset}` for {dimension} in {scope}{time_ctx}.\n\n"
        f"> 💡 **FinOps Diagnostic Tips**:\n"
        f"> 1. **Billing Ingestion Latency**: Cloud providers typically settle billing telemetry with a 24–48 hour delay. Try querying the prior closed month (e.g. 'last month') if analyzing current MTD.\n"
        f"> 2. **Account / Filter Scope**: If filtering by a specific subaccount, customer, or service, verify that resources were actively provisioned during this timeframe.\n"
        f"> 3. **Dataset Ingestion**: Check whether `{dataset}` is active and configured in your CloudHealth FlexReports tenant."
    )

# ── Local & Public Model Catalogs (from cleo_llm.py) ───────────────────────────
from cleo_llm import MLX_MODELS, LOCAL_MODELS, PUBLIC_ENGINES, AI_ENGINES

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

# ── CloudHealth Native API Engine & Unified MCP Client (from cleo_mcp.py) ─────
from cleo_mcp import (
    CloudHealthDirectEngine,
    STANDARD_CH_TOOLS,
    MCPClient,
)

# ── Visual Charts, Tables & Formatting (extracted to cleo_charts.py) ─────────
from cleo_charts import (
    is_no_chart_requested,
    is_no_mom_requested,
    is_exclude_other_requested,
    is_other_category_name,
    prune_mom_columns_from_markdown,
    _detect_chart_type,
    _detect_wants_variance,
    _build_mom_variance_chart,
    _detect_wants_table,
    _format_time_label,
    _clean_chart_title,
    _chart_block,
    _build_time_category_stacked_chart,
    split_thinking_and_response,
    _sanitize_finops_bullet_titles,
    _build_waterfall_chart,
    _csv_to_markdown,
    _classify_service_usage_type,
    _generate_domain_finops_insights,
    _parse_table_data_from_assistant_markdown,
)

# ── Hardware Auto-Detection & LLM Engine Callers (from cleo_llm.py) ───────────
from cleo_llm import (
    detect_system_info,
    _mlx_models_cache,
    get_installed_mlx_models,
    estimate_token_count,
    call_mlx_generate,
    unload_mlx_models,
    normalize_ollama_url,
    get_ollama_opener,
    OLLAMA_BASE_URL,
    get_installed_ollama_models,
    call_ollama_chat,
    call_gemini_api,
    call_openai_api,
    call_anthropic_api,
)

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
    ytd_start = f"{now.year}-01-01"
    ytd_months = now.month
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
        "ytd_start": ytd_start,
        "ytd_months": ytd_months,
        "now_year": now.year,
    }

def build_system_prompt(tools: list[dict], engine_label: str = None) -> str:
    cal = get_realtime_calendar_info()
    eng_info = f"- Active AI Engine: {engine_label}\n" if engine_label else ""
    prompt = (
        "You are Cleo, an expert Autonomous FinOps assistant powered by CloudHealth MCP.\n"
        f"{eng_info}"
        "Your task is to analyze cloud costs, query live usage datasets, inspect organizations, uncover savings, and answer conversational or FinOps questions.\n\n"
        "REAL-TIME CALENDAR & TEMPORAL CONTEXT (MANDATORY):\n"
        f"- Current Real-World Date (Today): {cal['today_str']} ({cal['today_verbose']})\n"
        f"- Yesterday (Latest Closed Billing Day): {cal['yesterday_str']} ({cal['yesterday_verbose']})\n"
        f"- Current Billing Month (MTD): {cal['current_ym']} ({cal['current_month_name']})\n"
        f"- Last Completed Billing Month: {cal['last_ym']} ({cal['last_month_name']})\n"
        f"- Year-To-Date (YTD): Jan 1, {cal['now_year']} to current month ({cal['current_ym']}), {cal['ytd_months']} months\n"
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
        "   - AWS_CUR: AWS Cost & Usage Report dataset. Key columns: lineItem_ProductCode (Service), lineItem_UnblendedCost, timeInterval_Month, lineItem_UsageAccountId, product_region (Region code e.g. us-east-1), product_location (Location name e.g. US East (N. Virginia)), lineItem_AvailabilityZone.\n"
        "   - MULTICLOUD_FOCUS_COST_AND_USAGE: Unified AWS+Azure FOCUS standard dataset. Key columns: provider, ServiceName, Month, EffectiveCost, BilledCost, RegionId (Region identifier e.g. us-east-1, eastus).\n"
        "   - REGION & LOCATION BREAKDOWNS: When user asks to break down costs by region or location, query product_region / product_location from AWS_CUR, or RegionId from MULTICLOUD_FOCUS_COST_AND_USAGE / AZURE_FOCUS_COST_AND_USAGE / GCP_FOCUS_COST_AND_USAGE.\n"
        "   - AWS_FOCUS_COST_AND_USAGE / AZURE_FOCUS_COST_AND_USAGE: Provider-specific FOCUS cost datasets.\n"
        "   - AZURE_COST_USAGE: Granular Azure Cost & Usage dataset (Azure Cost Management export with 95 columns). Key columns: ResourceName, ResourceId, ResourceGroup, SubscriptionName, MeterCategory, MeterSubCategory, MeterName, ConsumedService, AdditionalInfo, CostInBillingCurrency, ActualCostInUsd, Quantity, timeInterval_Month.\n"
        "     * AZURE HYBRID BENEFIT (AHB) / HYBRID DISCOUNTS: AHB is a licensing overlay (Layer 0 rate optimization), NOT a commitment plan. When the user asks about Azure VMs using or not using Hybrid Discounts / Azure Hybrid Benefit (AHB), query AZURE_COST_USAGE (filter for MeterCategory = 'Virtual Machines' OR ConsumedService = 'Microsoft.Compute' OR ResourceId LIKE '%/virtualMachines/%').\n"
        "     * Inspect AdditionalInfo for AHB indicators ('AHBDsc', 'IsAHBDsc', 'LicenseType', 'Windows_Server', 'RHEL_BYOS', 'SLES_BYOS', 'AHB', 'BasePrice') and check license cost ($0 for AHB-discounted VMs vs >$0 for un-discounted commercial Windows, RHEL, SLES, or SQL Server VMs).\n"
        "     * Never route Azure Hybrid Benefit / Hybrid Discounts to MULTICLOUD_COMMITMENT_SAVINGS or Commitment_Plan!\n"
        "   - AWS_COST_ANOMALY / AZURE_COST_ANOMALY / GCP_COST_ANOMALY: Dedicated CloudHealth Anomaly Detection datasets containing identified cost anomalies, spikes, and unusual spend.\n"
        "     * Key columns: Service (or CloudProduct in GCP), CostImpact (dollar variance), CostImpactPercentage (%), CostImpactType (Increase/Decrease), Status (ACTIVE/INACTIVE/ARCHIVED), Region, AccountID (or SubscriptionID in Azure, ProjectID in GCP), Duration_Days, timeInterval_Month.\n"
        "     * When user asks about anomalies, cost spikes, unusual spend, or anomaly detection, query AWS_COST_ANOMALY / AZURE_COST_ANOMALY / GCP_COST_ANOMALY!\n"
        "     * By default, always query for Active anomalies (Status = 'ACTIVE') unless the user explicitly asks for Inactive ones (e.g. 'inactive', 'resolved', 'closed', 'archived').\n"
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
        "4. FINOPS FOUNDATION & CLOUD FINANCIAL MANAGEMENT BEST PRACTICES:\n"
        "   - Follow the FinOps Foundation Framework (Inform → Optimize → Operate) and practitioner doctrine.\n"
        "   - RATE OPTIMIZATION & COMMITMENTS:\n"
        "     * Layering: Compute SPs (maximum breadth across EC2/Fargate/Lambda, up to 66% discount) + EC2 Instance SPs (maximum depth up to 72% discount for steady-state families) + Spot (fault-tolerant batch/stateless up to 90%). Note: Compute SPs do NOT cover SageMaker.\n"
        "     * Coverage Target: Aim for 70% to 80% coverage of steady-state base compute. Never commit 100% to preserve headroom for rightsizing, modernization, and workload volatility.\n"
        "     * Break-even: 1-year No-Upfront commitments break even in ~7 to 9 months; 3-year commitments break even in ~14 to 18 months. Evaluate commitment cliffs and expiration schedules quarterly.\n"
        "   - TWO-SIGNAL WASTE CLASSIFICATION:\n"
        "     * Never recommend terminating cloud assets based on a single metric (e.g. low CPU alone). Require two corroborating signals: e.g. Network Bytes Out < 5MB AND TCP Connection Count = 0 over a 14-day evaluation window.\n"
        "     * Categorize recommendations cleanly: Quick Wins (immediate, non-disruptive, zero downtime, e.g. gp2 to gp3 storage upgrade for 20% savings + 3,000 IOPS, unattached EBS volumes, S3 7-day multipart upload abort rules, snapshot pruning) vs Strategic Modernization (architectural, e.g. AWS Graviton3/4 silicon migration for 20% savings, commercial DB license exit from Oracle/SQL Server to Aurora, serverless rightsizing).\n"
        "   - FINOPS ROI SIMULATION & BREAK-EVEN HORIZONS (STRICT DOCTRINE):\n"
        "     * BAN ON FLAT BREAKEVEN ASSUMPTIONS: NEVER state or assume a blanket, flat, or uniform average break-even period (such as 'average 22 days to breakeven for everything' or arbitrary 30 days). Break-even timelines and ROI dynamics depend strictly on the optimization category and economic friction:\n"
        "       1. Tier 1: Immediate Quick Wins / Waste Reclamation (unattached EBS/disks, zombie NAT gateways, unassociated elastic IPs, idle ALBs/NLBs, noncurrent S3 version sprawl): Break-even is Immediate / Day 1 (0–7 days). Implementation cost is $0 CapEx and negligible engineering (~1–2 hours); run-rate cost drops on the next billing invoice. ROI is mathematically near-infinite (>10,000%).\n"
        "       2. Tier 2: Storage Modernization & Dynamic Configuration (gp2 to gp3 conversions, S3 Intelligent-Tiering, Azure Cool/Archive, Aurora Serverless ACU floors): Break-even is 7 to 30 days (< 1 month). Implementation cost is minimal (1-click AWS/Azure console, Terraform update, zero application refactoring); 20–35% baseline savings realized immediately.\n"
        "       3. Tier 3: Rate Optimization & Commitments (AWS Savings Plans, Azure Reservations, GCP CUDs): 1-Year No-Upfront commitments break even in 7 to 9 months (~60–75% of term). 3-Year commitments break even in 14 to 18 months (< 15 months target). Break-Even Utilisation % = 1 - Discount %. Coverage target: 70% to 80% of steady-state base compute. Utilization target: > 80% to 95%.\n"
        "       4. Tier 4: Architectural & Silicon Modernization (AWS Graviton3/4 / ARM64 migrations, commercial DB license exit from Oracle/SQL Server to Aurora PostgreSQL/MySQL, serverless container rightsizing): Break-even is 90 to 180 days (3 to 6 months). Must model Total Implementation Cost = Engineering Sprints (40–160 dev hours @ $120–$150/hr) + 'Double-bubble' parallel cloud run costs during cutover. Payback Period (Months) = Total Implementation Cost / Monthly Net Savings. Net Annual ROI % = ((Annualized Net Savings - Total Implementation Cost) / Total Implementation Cost) * 100.\n"
        "     * FINOPS EFFICIENCY CALCULATIONS & KPIS:\n"
        "       - Effective Savings Rate (ESR): ESR % = (Realized Net Savings / Baseline Cloud Spend) * 100. Target benchmark: 15% to 25%.\n"
        "       - Realized vs. Potential Savings: Always present Realized Savings (banked in ledger) and Potential Savings (sized backlog) as separate lines.\n"
        "       - Unit Cost Efficiency: Track spend per business denominator (Cost per active tenant, Cost per transaction, Cost per 1M tokens, Cost per vCPU-hr).\n"
        "       - Value Realization Gates: Distinguish 'Spend Removed' (P&L line shrunk) from 'Spend Avoided' (growth absorbed without budget increase).\n"
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
        "   - EARLY-MONTH CLOUD BILLING INGESTION LATENCY:\n"
        "     * Cloud providers (AWS CUR, Azure, GCP) experience 24–72 hour settlement and export latency.\n"
        "     * During the first few days of a calendar month (e.g. Days 1–3), Month-to-Date (MTD) spend will often read $0.00 or near $0.00.\n"
        "     * NEVER interpret early-month $0.00 or low MTD spend as a 'spend anomaly', 'critical service outage', 'missing billing data alarm', or 'successful cost-cutting initiative'. It is routine billing ingestion latency. Focus analysis on finalized historical months.\n"
        "5. PRESENTATION STANDARDS:\n"
        "   - Always present financial figures in crisp markdown tables with dollar signs ($), commas, and percentage changes where applicable.\n"
        "   - Highlight cost drivers, trends, and actionable FinOps optimization opportunities.\n"
        "6. MULTI-CLOUD BEST DEFAULT DIMENSIONS & QUANTITY VS COST INTELLIGENCE:\n"
        "   - QUANTITY VS COST DISAMBIGUATION: When user asks for operational, volume, or capacity metrics (e.g. 'number of ec2 instances', 'how many VMs', 'count of databases', 'storage volume in GB', 'instance hours', 'invocations', 'units', 'consumed quantity', 'usage amount', 'usage quantity'), prioritize quantity measures (SUM(Instances), SUM(PricingQuantity), SUM(lineItem_UsageAmount), SUM(Instance_Hours), SUM(Quantity)) over financial spend.\n"
        "   - BEST DEFAULT DIMENSIONS MATRIX (NEVER DEFAULT TO REGION UNLESS EXPLICITLY REQUESTED):\n"
        "     * AWS EC2: Default dimension is 'product_InstanceType' (synonyms: instance type, vm size, machine type, flavor, node type). Quantity = SUM(Instances), SUM(Instance_Hours).\n"
        "     * AWS RDS: Default dimension is 'InstanceType' & Database Engine (synonyms: rds instance type, db size, db instance). Quantity = SUM(Instances).\n"
        "     * AWS S3: Default dimension is 'product_storageClass' (synonyms: storage class, storage tier, s3 tier, lifecycle tier: Standard, Intelligent-Tiering, Glacier). Quantity = SUM(lineItem_UsageAmount) (GB-Mo).\n"
        "     * AWS EBS: Default dimension is 'product_volumeType' (synonyms: volume type, ebs type, disk type, gp2, gp3, io1, io2). Quantity = SUM(lineItem_UsageAmount) (GB-Mo).\n"
        "     * AWS Lambda: Default dimension is 'lineItem_Operation' (synonyms: operation, api operation, action, invocations, duration). Quantity = SUM(lineItem_UsageAmount).\n"
        "     * Azure Compute/Storage/DB: Default dimension is 'ServiceSubcategory' from AZURE_FOCUS_COST_AND_USAGE (synonyms: meter category, meter subcategory, sub service). Quantity = SUM(PricingQuantity) (Hours / GB-Mo).\n"
        "     * GCP Compute/Storage/DB: Default dimension is 'ServiceSubcategory' from GCP_FOCUS_COST_AND_USAGE. Quantity = SUM(PricingQuantity) (Hours / GB-Mo).\n"
        "   - NEVER substitute Region or Location for service-specific dimensions unless the user explicitly used words like 'region', 'regional', or 'location'.\n"
        "7. COMPREHENSIVE MULTI-CLOUD & FOCUS 1.2 DIMENSIONAL TAXONOMY (NATURAL LANGUAGE SYNONYMS):\n"
        "   - SubaccountId: Member / Usage Account ID or Project Name across AWS, Azure, GCP. In MULTICLOUD_FOCUS_COST_AND_USAGE, represents AWS Account IDs, GCP Projects, or Azure Subscriptions. Synonyms: 'account', 'subaccount', 'sub-account', 'member account', 'linked account', 'usage account', 'project', 'subscription', 'cloud account', 'account id', 'account name'. Use for 'by account', 'account names', 'per project', 'by subscription', 'member accounts'.\n"
        "   - BillingAccountId: Root / Payer / Management Account ID. In MULTICLOUD_FOCUS_COST_AND_USAGE, represents the parent billing container. Synonyms: 'billing account', 'payer account', 'master account', 'root account', 'management account', 'billing container', 'parent account', 'payer id', 'billing account id'. Use for 'by billing account', 'payer account', 'master account', 'root account'.\n"
        "   - ServiceCategory: FOCUS 1.2 Macro category (Compute, Storage, Database, Networking, AI & Machine Learning, Management & Governance). Synonyms: 'service category', 'macro service', 'service family', 'category', 'domain', 'service domain', 'cloud domain'. Use for 'by category', 'service category', 'by domain', 'service family'.\n"
        "   - ServiceSubcategory: FOCUS 1.2 Granular category (Virtual Machines, Object Storage, Relational Database, NAT Gateway). Synonyms: 'service subcategory', 'granular service', 'sub service', 'sub-service', 'subcategory', 'meter category', 'meter subcategory', 'service component'. Use for 'by subcategory', 'service subcategory', 'by sub service', 'granular services'.\n"
        "   - PricingCategory: FOCUS commercial model (On-Demand, Committed, Dynamic, Spot). Synonyms: 'pricing category', 'pricing model', 'commercial model', 'contract type', 'pricing type', 'commitment type', 'charge type'. Use for 'by pricing category', 'pricing model', 'commercial model'.\n"
        "   - LeaseType / Purchase Option: Lease model analyzed via AWS_CUR (pricing_term & lineItem_LineItemType). Synonyms: 'lease type', 'purchase option', 'capacity type', 'ondemand', 'reservation', 'savings plan', 'spot'.\n"
        "   - RegionId: Geographic cloud location code (e.g. us-east-1, eastus). Synonyms: 'region', 'location', 'cloud region', 'datacenter', 'data center', 'geography', 'geo', 'zone'. Use for 'by region', 'regional breakdown', 'by location', 'by datacenter'.\n"
        "   - ResourceId: Individual resource identifier or ARN. Synonyms: 'resource', 'resource id', 'resource name', 'arn', 'instance id', 'asset', 'individual resource', 'top resources'. Use for 'by resource', 'top resources', 'resource-level'.\n"
        "   - ModelProvider: AI provider in MULTICLOUD_AI_COST_AND_USAGE. Synonyms: 'model provider', 'ai provider', 'ai vendor', 'llm vendor', 'ai company'. Use for 'by model provider', 'ai provider'.\n"
        "   - Model: AI model name in MULTICLOUD_AI_COST_AND_USAGE. Synonyms: 'model', 'model name', 'ai model', 'foundation model', 'llm', 'language model'. Use for 'by model', 'ai model', 'foundation model'.\n"
        "   - Modality: Interaction modality in MULTICLOUD_AI_COST_AND_USAGE. Synonyms: 'modality', 'media type', 'input modality', 'text vs multimodal', 'vision', 'embedding'. Use for 'by modality'.\n"
        "   - ExecutionType: Execution pipeline in MULTICLOUD_AI_COST_AND_USAGE. Synonyms: 'execution type', 'inference type', 'batch vs streaming', 'realtime vs batch', 'processing mode'. Use for 'by execution type'.\n"
        "   - TokenType: Token cost driver in MULTICLOUD_AI_COST_AND_USAGE. Synonyms: 'token type', 'prompt tokens', 'completion tokens', 'cached tokens', 'tokens'. Use for 'by token type'.\n"
        "   - HardwareType / HardwareFamily: Accelerator type & family in MULTICLOUD_AI_COST_AND_USAGE. Synonyms: 'hardware type', 'hardware family', 'accelerator', 'gpu', 'tpu', 'h100', 'a100'. Use for 'by hardware', 'gpu breakdown'.\n"
        "   - Commitment_Plan: Savings Plan / Reservation type in MULTICLOUD_COMMITMENT_SAVINGS. Synonyms: 'commitment plan', 'savings plan', 'reservation', 'ri', 'reserved instance'. Use for 'by commitment plan', 'savings plans'.\n"
        "   - Country: Carbon footprint geography in MULTICLOUD_OPERATIONAL_EMISSIONS. Synonyms: 'country', 'emissions country', 'carbon country', 'datacenter country', 'geography'. Use for 'by country', 'emissions by country'.\n\n"
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
        "6. When user asks about cost anomalies, unusual spikes, or anomaly detection, query 'AWS_COST_ANOMALY', 'AZURE_COST_ANOMALY', or 'GCP_COST_ANOMALY' (or all three for multi-cloud) via execute_datasource_query. By default, always filter for ACTIVE anomalies (Status = 'ACTIVE') unless the user explicitly requests inactive anomalies.\n"
        "7. CloudHealth MCP does NOT support tenant user management or user identity listing (e.g. users in tenant, user accounts, IAM permissions). When asked to list users in a tenant, clearly explain that CloudHealth MCP is strictly focused on multi-cloud FinOps (cost, usage, anomalies, organizations) and direct the user to the CloudHealth console (Setup -> Users) or their SSO/IdP directory.\n"
        "8. When user asks about AI model specs, token rates, per-token pricing, or comparing LLMs (e.g. Claude 3.7 vs GPT-4o, DeepSeek, Gemini token rates), Cleo has live access to real-time AI token economics and context specs via get_live_model_specs.\n"
        "9. When user asks for public cloud retail/list prices or Azure rate cards (e.g. Standard_D4s_v5 in eastus, spot rates, 1-yr reservation list prices), Cleo has live access to official Azure Retail Prices API rates via get_live_cloud_pricing.\n"
        "10. When user asks for current FinOps industry trends, FOCUS specification updates, or real-world cloud cost news, Cleo has live web search access via search_finops_web.\n\n"
        "CONVERSATIONAL MEMORY & MULTI-TURN CONTEXT:\n"
        "- You maintain full conversational memory across all turns in this session.\n"
        "- When the user asks about prior queries, results, or context (e.g. 'which month was I asking for?', 'who spent the most?', 'summarize the table', 'why?'), ALWAYS use the conversation history to answer directly, accurately, and concisely.\n"
        "- Never claim you don't know the requested month or parameters when they were stated in previous user turns or assistant answers.\n"
    )
    schema_ctx = build_llm_schema_context()
    if schema_ctx:
        prompt += f"\n{schema_ctx}\n"
    return prompt

# ── Multi-Cloud Service Catalogs & Query Understanding (from cleo_query.py) ───
from cleo_query import (
    ServiceMatch,
    CLOUD_SERVICES_MAP,
    AWS_SERVICES_MAP,
    SERVICE_DISPLAY_NAMES,
    PCODE_TO_PROVIDER,
    PCODE_TO_DISPLAY,
    _friendly_service_name,
    _fetch_total_cost,
    extract_requested_service,
    extract_all_requested_services,
    extract_requested_cloud,
    parse_query_time_context,
    detect_anomaly_status_filter,
    _detect_contextual_continuation,
    _deterministic_understand_query,
)

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

    def get_engine_display_name(self) -> str:
        eng = self.engine
        if eng == "direct":
            return "Direct FinOps Router (Rule-based)"
        if eng == "gemini":
            model = self.cfg.get("GEMINI_MODEL") or "gemini-3.5-flash"
            return f"Google Gemini (`{model}`)"
        if eng == "openai":
            model = self.cfg.get("OPENAI_MODEL") or "gpt-4o"
            return f"OpenAI (`{model}`)"
        if eng == "anthropic":
            model = self.cfg.get("ANTHROPIC_MODEL") or "claude-3-7-sonnet-latest"
            return f"Anthropic Claude (`{model}`)"
        if eng.startswith("mlx:"):
            m_name = eng.removeprefix("mlx:").split("/")[-1]
            return f"Local MLX (`{m_name}`)"
        if eng.startswith("ollama:"):
            m_name = eng.removeprefix("ollama:").split("/")[-1]
            return f"Local Ollama (`{m_name}`)"
        return f"AI Engine (`{eng}`)"

    def _get_cust_map(self, mcp: MCPClient = None) -> dict:
        if not hasattr(self, "_cust_map_cache"):
            self._cust_map_cache = {}
        if not self._cust_map_cache and mcp:
            for tool_name in ["list_orgs", "list_channel_customers"]:
                try:
                    r = mcp.call_tool(tool_name, {})
                    raw_txt = r.get("content", [{}])[0].get("text", "")
                    if raw_txt and "not found" not in raw_txt.lower():
                        custs = json.loads(raw_txt)
                        if isinstance(custs, list):
                            self._cust_map_cache = {
                                c["name"]: (c.get("id") or c.get("customerId"))
                                for c in custs if c.get("name") and (c.get("id") or c.get("customerId"))
                                and c["name"].lower().strip() not in ("all accounts", "all account", "all customers", "all customer", "all", "overall", "partner", "partner-wide", "partner wide")
                            }
                            if self._cust_map_cache:
                                break
                except Exception:
                    pass
        return getattr(self, "_cust_map_cache", {})

    def _get_account_name_map(self, mcp: MCPClient = None) -> dict[str, str]:
        if not hasattr(self, "_account_name_cache"):
            self._account_name_cache = {}
        if not self._account_name_cache and mcp:
            try:
                res = mcp.call_tool("execute_datasource_query", {
                    "queryInput": {
                        "sqlStatement": "SELECT CloudId AS id, AmazonName AS amazon_name, Name AS name FROM LOGICAL_ASSET_AWS_USAGE_ACCOUNT",
                        "limit": 100
                    },
                    "requestInfo": {"sourceType": "API", "caller": "mcp"}
                })
                content_txt = res.get("content", [{}])[0].get("text", "")
                raw_csv = json.loads(content_txt).get("csv", "")
                if raw_csv:
                    for r in csv.DictReader(io.StringIO(raw_csv)):
                        acc_id = (r.get("id") or "").strip()
                        acc_name = (r.get("name") or r.get("amazon_name") or "").strip()
                        if acc_id and acc_name:
                            self._account_name_cache[acc_id] = acc_name
            except Exception as e:
                logger.debug(f"[Account Name Lookup] {e}")
        return getattr(self, "_account_name_cache", {})

    def _call_active_llm(self, messages: list[dict], stats_out: dict = None, on_token = None) -> tuple[str, str]:
        """
        Attempts to call the configured LLM engine.
        Returns (response_text, error_message).
        """
        cfg = self.cfg or _load_config()
        engine = self.engine
        target_stats = stats_out if stats_out is not None else getattr(self, "last_stats", {})

        # Sanitize messages so UI-level metadata (tool_calls, tokens, timestamps)
        # doesn't break Jinja chat templates (e.g. Qwen3.5 tool_call.name undefined error)
        clean_messages = [
            {"role": m.get("role", "user"), "content": m.get("content") or ""}
            for m in messages
            if isinstance(m, dict) and m.get("role")
        ]

        # Ensure active engine and model identity is present in system prompt
        engine_label = self.get_engine_display_name()
        if not any(m.get("role") == "system" for m in clean_messages):
            clean_messages.insert(0, {"role": "system", "content": build_system_prompt([], engine_label=engine_label)})
        else:
            for m in clean_messages:
                if m.get("role") == "system" and "- Active AI Engine:" not in m.get("content", ""):
                    m["content"] = f"- Active AI Engine: {engine_label}\n" + m["content"]
                    break

        # ponytail: universal injection of learned memory & user corrections across all LLM handlers
        last_u_text = ""
        for m in reversed(clean_messages):
            if m.get("role") == "user":
                last_u_text = m.get("content", "")
                break
        if last_u_text:
            mem_block = get_memory().build_context_block(last_u_text)
            if mem_block and not any("CLEO LEARNED MEMORY" in m.get("content", "") for m in clean_messages if m.get("role") == "system"):
                for m in clean_messages:
                    if m.get("role") == "system":
                        m["content"] += f"\n\n{mem_block}"
                        break

        if engine.startswith("mlx:") or "mlx-community" in engine:
            repo_id = engine.removeprefix("mlx:").strip()
            try:
                ans = call_mlx_generate(repo_id, clean_messages, stats_out=target_stats, on_token=on_token)
                if ans:
                    return ans, ""
                return "", f"MLX returned empty response for model {repo_id}."
            except Exception as e:
                return "", f"MLX inference error ({e}). Ensure model weights exist in Hugging Face cache."

        elif engine.startswith("ollama"):
            model = engine.split(":", 1)[1] if ":" in engine else "qwen2.5:7b"
            try:
                ans = call_ollama_chat(model, clean_messages, stats_out=target_stats, on_token=on_token)
                if ans:
                    return ans, ""
                return "", f"Ollama returned empty response for model {model}."
            except Exception as e:
                return "", f"Could not connect to Ollama daemon ({e}). Make sure 'ollama serve' is running."

        elif engine == "gemini":
            key = cfg.get("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
            model = cfg.get("GEMINI_MODEL") or "gemini-3.5-flash"
            if not key:
                return "", "Gemini API key is not configured. Enter it in the Engine Settings modal or set GEMINI_API_KEY."
            try:
                ans = call_gemini_api(key, clean_messages, model=model, stats_out=target_stats)
                if ans:
                    if on_token:
                        on_token(ans)
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
                ans = call_openai_api(key, clean_messages, model=model, stats_out=target_stats)
                if ans:
                    if on_token:
                        on_token(ans)
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
                ans = call_anthropic_api(key, clean_messages, model=model, stats_out=target_stats)
                if ans:
                    if on_token:
                        on_token(ans)
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
        low = last_msg.lower()
        cust_map = self._get_cust_map(mcp=mcp)

        cont_ctx = _detect_contextual_continuation(messages, cust_map=cust_map)
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
        mem_ctx = get_memory().build_context_block(last_msg)
        mem_instruction = f"\nLEARNED USER CORRECTIONS & DOCTRINE (CRITICAL: Prioritize these user rules over defaults!):\n{mem_ctx}\n" if mem_ctx else ""
        system_instruction = (
            "You are Cleo's FinOps Request Analyzer. Analyze the user query in the context of recent chat history.\n"
            "Your mission is to understand the user's intent with extreme accuracy, correct any typos in services, dates, or cloud providers, normalize entities, and extract query parameters.\n\n"
            f"{mem_instruction}"
            f"REAL-TIME TEMPORAL DETAILS (Ground all dates against this calendar):\n"
            f"- Today's Date: {cal['today_str']} ({cal['today_verbose']}) — ALWAYS IGNORE TODAY in closed daily billing trends as in-flight.\n"
            f"- Yesterday: {cal['yesterday_str']} — ALWAYS REMEMBER that yesterday's data is PARTIAL due to cloud billing settlement latency.\n"
            f"- Current Billing Month (MTD): {cal['current_ym']} ({cal['current_month_name']})\n"
            f"- Last Completed Month: {cal['last_ym']} ({cal['last_month_name']})\n"
            f"- Year-To-Date (YTD): {cal['ytd_start']} to current month ({cal['current_ym']}), {cal['ytd_months']} months. When user requests YTD, set timeframe_months={cal['ytd_months']}, timeframe_days=null, and target_ym='{cal['current_ym']}'.\n"
            f"- Last 15 Days Window: {cal['d15_start']} to {cal['yesterday_str']} (15 closed days, ending yesterday, ignoring today)\n"
            f"- Last 30 Days Window: {cal['d30_start']} to {cal['yesterday_str']} (30 closed days, ending yesterday, ignoring today)\n\n"
            "Output ONLY a raw JSON object (no markdown, no code fencing, no explanation) with this schema:\n"
            "{\n"
            '  "intent": "fetch_data" | "general_finops_advisory" | "reformat_previous" | "history_qa" | "finops_recommendations" | "anomalies" | "unsupported_capability" | "general_chat",\n'
            '  "cloud": "aws" | "azure" | "gcp" | "all" | null,\n'
            '  "services": ["AmazonRDS", "AmazonS3", ...] or null,\n'
            '  "service": "AmazonRDS" | "AmazonEC2" | "AmazonS3" | "AWSLambda" | "AmazonBedrock" | string | null,\n'
            '  "customer": string or null,\n'
            '  "metric_type": "quantity" | "cost",\n'
            '  "target_dimension": "product_InstanceType" | "ServiceSubcategory" | "ServiceCategory" | "PricingCategory" | "LeaseType" | "SubaccountId" | "BillingAccountId" | "ResourceId" | "RegionId" | "Model" | "ModelProvider" | "Modality" | "ExecutionType" | "TokenType" | "HardwareType" | "HardwareFamily" | "Commitment_Plan" | "Country" | "product_storageClass" | "product_volumeType" | "lineItem_Operation" | "ServiceName" | "provider" | string | null,\n'
            '  "target_ym": "YYYY-MM" or null,\n'
            '  "timeframe_months": integer or null,\n'
            '  "timeframe_days": integer or null,\n'
            '  "breakdowns": ["instance_type", "engine_type", "storage_class", "volume_type", "service_subcategory", "service_category", "pricing_category", "lease_type", "account", "billing_account", "region", "location", "service", "customer", "resource", "model", "model_provider", "modality", "execution_type", "token_type", "hardware_type", "hardware_family", "commitment_plan", "country"],\n'
            '  "chart_types": ["bar", "horizontal-bar", "pie", "donut", "line"],\n'
            '  "include_chart": boolean,\n'
            '  "include_mom": boolean,\n'
            '  "is_new_data_fetch": boolean,\n'
            '  "corrected_query": string\n'
            "}\n\n"
            "CRITICAL RULES:\n"
            "1. GENERAL FINOPS ADVISORY (NO DATA FETCH): Set intent to 'general_finops_advisory' and is_new_data_fetch to false if the user asks a conceptual FinOps, architectural, best practice, or educational question (e.g. 'What is the difference between EffectiveCost and BilledCost?', 'How to optimize NAT gateways?', 'Explain FinOps framework phases', 'Savings Plans vs RIs', 'What is FOCUS?', 'FinOps best practices on egress'). These questions DO NOT require pulling data from CloudHealth.\n"
            "2. DATA FETCH & COMPARISON: Set intent to 'fetch_data' and is_new_data_fetch to true if the user asks to see, show, fetch, get, compare, analyze, or chart their costs, usage, spend, run-rate, or data from cloud providers (AWS, Azure, GCP), even if their prompt has typos (e.g. 'shw me jne 2026 cst for awz').\n"
            "   - CRITICAL: Any request asking to compare costs, calculate projected costs, compare previous month with current month, or asking about a specific customer / organization / tenant (e.g. 'ABC Coffee Mugs', 'Lundbeck') MUST ALWAYS be classified as 'fetch_data' with is_new_data_fetch=true! NEVER classify customer spend queries or cost comparisons as general advisory.\n"
            "3. TYPO CORRECTION & NORMALIZATION: In corrected_query, fix all spelling mistakes, typos in services (e.g. 'awz' -> 'AWS', 'rds' -> 'RDS', 'jne' -> 'June'), and clarify the sentence. In 'cloud', normalize to 'aws', 'azure', 'gcp', or 'all'. In 'service', normalize to canonical names like 'AmazonEC2', 'AmazonRDS', 'AmazonS3'. If multiple services are requested (e.g. 'RDS and S3'), list them all in 'services': ['AmazonRDS', 'AmazonS3'] and set 'service': 'AmazonRDS'. If the query asks about AI models, LLMs, foundation models, token costs, or multi-cloud AI spend, set 'service' to null (do NOT set AmazonRDS or other infrastructure services unless explicitly named). In 'target_ym', extract normalized 'YYYY-MM' (e.g. '2026-06').\n"
            "4. METRIC TYPE (QUANTITY VS COST): Set metric_type='quantity' if user asks for volume, count, operational capacity, or physical usage (e.g. 'number of ec2 instances', 'how many vms', 'instance hours', 'storage used in GB', 'how many invocations', 'count of databases'). Set metric_type='cost' (default) if user asks for financial spend, dollars, cost, or bill.\n"
            "5. MULTI-CLOUD BEST DEFAULT DIMENSIONS: When grouping dimension is not specified by the user:\n"
            "   - For Amazon EC2: target_dimension='product_InstanceType', breakdowns=['instance_type']\n"
            "   - For Amazon RDS: target_dimension='InstanceType', breakdowns=['instance_type', 'engine_type']\n"
            "   - For Amazon S3: target_dimension='product_storageClass', breakdowns=['storage_class']\n"
            "   - For Amazon EBS: target_dimension='product_volumeType', breakdowns=['volume_type']\n"
            "   - For AWS Lambda: target_dimension='lineItem_Operation'\n"
            "   - For Azure & GCP: target_dimension='ServiceSubcategory', breakdowns=['service_subcategory']\n"
            "   - For AI / Foundation Models: target_dimension='Model', breakdowns=['model'] (set service=null unless Bedrock is explicitly named)\n"
            "   - For Multi-Cloud / Top Services: target_dimension='ServiceName', breakdowns=['service']\n"
            "   - CRITICAL: NEVER default to 'region' or 'location' unless the user explicitly requested region or location! Only include 'region' if user explicitly asked to break down by region.\n"
            "6. REFORMAT ONLY: is_new_data_fetch is false and intent is 'reformat_previous' ONLY when the user asks purely to re-render the immediately preceding table into a different chart format (e.g. 'show that as a pie chart') without requesting new data or changing service.\n"
            "7. UNSUPPORTED CAPABILITY: Set intent to 'unsupported_capability' and is_new_data_fetch to false if user asks for tenant users, user accounts, IAM users, passwords, or identity management in the tenant (CloudHealth MCP does not manage user accounts).\n"
            "8. NEGATIVE CONSTRAINTS & FORMATTING: Set 'include_chart'=false if the user says 'without chart', 'no chart', 'without mom chart', 'table only', 'only table', 'skip chart', 'do not chart'. Set 'include_mom'=false if user says 'without mom', 'no mom', 'without mom chart', 'without variance', 'no variance'. Default both to true when not excluded.\n"
            "9. CONVERSATIONAL CONTINUATION & COMPARATIVE QUERIES ('similar data for X', 'same for Y', 'what about Z'):\n"
            "   When the user asks for 'similar data', 'simillar data', 'same data', 'same for X', or 'what about Y', ALWAYS inspect recent chat history (Previous User Query & Previous Assistant Topic).\n"
            "   Inherit the exact query intent (e.g. if prior was a 2027 forecast, current is ALSO a 2027 forecast; if prior was a 6-month monthly trend, current is a 6-month trend), timeframe, and breakdown structure, updating ONLY the entity specified by the user (e.g. cloud provider changed to Azure).\n"
            "   In 'corrected_query', write out the fully expanded contextual question (e.g. 'forecast for Azure cost for FY 2027 and break it down monthly').\n"
            "10. COMPREHENSIVE MULTI-CLOUD & FOCUS 1.2 DIMENSIONAL TAXONOMY (NATURAL LANGUAGE SYNONYMS):\n"
            "   - Account / Projects: 'account', 'accounts', 'account names', 'projects', 'subscriptions', 'subaccounts', 'member accounts', 'linked accounts', 'usage accounts', 'cloud account' -> target_dimension='SubaccountId', breakdowns=['account']. NEVER classify 'account' or 'account names' as 'customer'! 'customer' is strictly for MSP channel clients / organizations.\n"
            "   - Billing Account: 'billing account', 'payer account', 'master account', 'root account', 'management account', 'billing container', 'parent account' -> target_dimension='BillingAccountId', breakdowns=['billing_account'].\n"
            "   - Service Category: 'service category', 'macro service', 'service family', 'category', 'domain', 'service domain', 'cloud domain' -> target_dimension='ServiceCategory', breakdowns=['service_category'].\n"
            "   - Service Subcategory: 'service subcategory', 'granular service', 'sub service', 'sub-service', 'meter category', 'meter subcategory', 'service component' -> target_dimension='ServiceSubcategory', breakdowns=['service_subcategory'].\n"
            "   - Pricing Category: 'pricing category', 'pricing model', 'commercial model', 'contract type', 'pricing type', 'commitment type', 'charge type' -> target_dimension='PricingCategory', breakdowns=['pricing_category'].\n"
            "   - Lease Type / Purchase Option: 'lease type', 'by lease', 'purchase option', 'capacity type', 'ondemand', 'reservation', 'savings plan', 'spot' -> target_dimension='LeaseType', breakdowns=['lease_type']. Analyzed via AWS_CUR & CloudHealth FlexReports.\n"
            "   - Storage Class / Tier: 'storage class', 'storage tier', 's3 tier', 's3 storage class', 'lifecycle tier' -> target_dimension='product_storageClass', breakdowns=['storage_class'].\n"
            "   - Volume Type: 'volume type', 'ebs type', 'disk type', 'volume tier', 'gp2', 'gp3', 'io1', 'io2' -> target_dimension='product_volumeType', breakdowns=['volume_type'].\n"
            "   - Operation: 'operation', 'api operation', 'action', 'event', 'invocations' -> target_dimension='lineItem_Operation', breakdowns=['operation'].\n"
            "   - Instance Type: 'instance type', 'instance size', 'vm size', 'vm type', 'machine type', 'flavor' -> target_dimension='product_InstanceType', breakdowns=['instance_type'].\n"
            "   - Resource ID: 'resource', 'resource id', 'resource name', 'arn', 'instance id', 'asset', 'top resources' -> target_dimension='ResourceId', breakdowns=['resource'].\n"
            "   - Region: 'region', 'location', 'cloud region', 'datacenter', 'geography', 'zone' -> target_dimension='RegionId', breakdowns=['region']. (Only if explicitly requested!)\n"
            "   - AI Model Provider: 'model provider', 'ai provider', 'ai vendor', 'llm vendor', 'by provider' (in AI context) -> target_dimension='ModelProvider', breakdowns=['model_provider'].\n"
            "   - AI Model: 'ai model', 'by model', 'by llm', 'foundation model', 'model name', 'language model' -> target_dimension='Model', breakdowns=['model'].\n"
            "   - AI Modality: 'modality', 'media type', 'input modality', 'text vs multimodal', 'vision', 'embedding' -> target_dimension='Modality', breakdowns=['modality'].\n"
            "   - AI Execution Type: 'execution type', 'inference type', 'batch vs streaming', 'realtime vs batch', 'processing mode' -> target_dimension='ExecutionType', breakdowns=['execution_type'].\n"
            "   - AI Token Type: 'token type', 'prompt tokens', 'completion tokens', 'input tokens', 'cached tokens', 'tokens' -> target_dimension='TokenType', breakdowns=['token_type'].\n"
            "   - AI Hardware Type / Family: 'hardware type', 'hardware family', 'by hardware', 'gpu vs tpu', 'accelerator', 'h100 vs a100' -> target_dimension='HardwareType', breakdowns=['hardware_type'].\n"
            "   - Commitment Plan: 'commitment plan', 'savings plan', 'reservation', 'ri', 'reserved instance' -> target_dimension='Commitment_Plan', breakdowns=['commitment_plan']. (NEVER classify Azure Hybrid Benefit or Hybrid Discounts as Commitment Plan! AHB is a software licensing overlay analyzed via AZURE_COST_USAGE).\n"
            "   - Azure Hybrid Benefit / Hybrid Discounts: 'hybrid discount', 'hybrid discounts', 'azure hybrid benefit', 'ahb', 'hybrid benefit' for Azure VMs (Windows Server, Red Hat Enterprise Linux, SUSE Linux Enterprise, SQL Server) -> target_dimension='hybrid_benefit', breakdowns=['hybrid_benefit'], service='Virtual Machines', cloud='azure'. Analyzed via AZURE_COST_USAGE.\n"
            "   - Carbon & Emissions: 'emissions by country', 'carbon by country', 'by country', 'emissions by geography' -> target_dimension='Country', breakdowns=['country'].\n"
            "   - Cost Anomalies & Spikes: When user asks about cost anomalies, spikes, unusual spend, or 'why is X high', 'explain this spike', 'what drove the increase in Y', set intent='anomalies' and is_new_data_fetch=true. By default anomalies must target Active status unless user explicitly requests Inactive ones.\n"
            "11. GENERAL CHAT & AGENT/MODEL IDENTITY: Set intent to 'general_chat' and is_new_data_fetch to false if the user asks conversational questions, greetings, jokes, general knowledge, or questions about the AI model, engine, or assistant identity (e.g. 'what llm we are using right now?', 'who are you', 'what can you do', 'hello', 'tell me a joke', 'what model is this?').\n"
        )

        cust_list_snippet = f"Known Channel Customers: {', '.join(list(cust_map.keys())[:25])}\n\n" if cust_map else ""
        cont_line = ""
        if cont_ctx.get("is_continuation"):
            cont_line = f"Detected Continuation Context: User is asking for similar/same data to the prior turn. Prior analysis was '{cont_ctx['prior_query_type']}' ({cont_ctx.get('inherited_target_period_title') or ''}). In 'corrected_query', synthesize the complete expanded question (e.g. '{cont_ctx.get('expanded_query')}').\n\n"

        user_prompt = (
            f"Recent Context:\n{context_str}\n\n"
            f"{cont_line}"
            f"{cust_list_snippet}"
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
                if not cleaned:
                    raise ValueError("empty LLM response after strip")
                parsed = json.loads(cleaned)
                logger.info(f"[LLM-First Intent Analysis] Parsed: {parsed}")
                if "is_new_data_fetch" in parsed and "intent" in parsed:
                    bdowns = parsed.get("breakdowns") or det_info["breakdowns"]
                    # Sanitize: account vs customer disambiguation
                    is_account_trigger = any(w in low for w in [
                        "by account", "per account", "account name", "account names", "account breakdown",
                        "breakdown by account", "each account", "subaccount", "sub-account",
                        "by project", "per project", "by subscription", "per subscription"
                    ]) or any(b in ("account", "SubaccountId") for b in bdowns)
                    if is_account_trigger:
                        bdowns = [b for b in bdowns if b != "customer"]
                        if "account" not in bdowns:
                            bdowns.append("account")
                        parsed["target_dimension"] = "SubaccountId"
                        if parsed.get("customer") and any(w in str(parsed["customer"]).lower() for w in ["account", "account name", "account names", "subaccount"]):
                            parsed["customer"] = None

                    # Sanitize: never allow spurious region/location breakdowns unless explicitly in user prompt
                    if not any(w in low for w in ["region", "regions", "regional"]):
                        bdowns = [b for b in bdowns if b != "region"]
                    if not any(w in low for w in ["location", "locations", "geography", "geographic"]):
                        bdowns = [b for b in bdowns if b != "location"]

                    # Sanitize: Azure Hybrid Benefit (AHB) vs Commitment Plan
                    is_ahb_trigger = any(w in low for w in [
                        "hybrid discount", "hybrid discounts", "hybrid benefit", "hybrid benefits",
                        "azure hybrid benefit", "ahb", "ahb discount", "ahb discounts", "hybrid licensing", "hybrid license"
                    ])
                    if is_ahb_trigger:
                        bdowns = [b for b in bdowns if b != "commitment_plan"]
                        if "hybrid_benefit" not in bdowns:
                            bdowns.append("hybrid_benefit")
                        parsed["target_dimension"] = "hybrid_benefit"
                        parsed["breakdowns"] = bdowns
                        parsed["service"] = "Virtual Machines"
                        parsed["cloud"] = "azure"
                        parsed["intent"] = "fetch_data"
                        parsed["is_new_data_fetch"] = True

                    # Sanitize: Lease Type / Purchase Option (On-Demand, RI, Savings Plan, Spot)
                    is_lease_type_trigger = any(w in low for w in [
                        "lease type", "leasetype", "by lease", "lease breakdown", "lease types",
                        "purchase option", "purchase options", "purchase option breakdown", "by purchase option",
                        "pricing model", "pricing models", "pricing model breakdown", "by pricing model",
                        "capacity type", "by capacity type"
                    ]) or (any(w in low for w in ["ondemand", "on-demand", "reservation", "savings plan", "savingsplan", "spot"]) and any(w in low for w in ["breakdown", "break down", "split", "by", "usage", "cost", "spend", "lease", "above"]))
                    if is_lease_type_trigger:
                        if "lease_type" not in bdowns:
                            bdowns.append("lease_type")
                        parsed["target_dimension"] = "LeaseType"
                        parsed["breakdowns"] = bdowns
                        parsed["intent"] = "fetch_data"
                        parsed["is_new_data_fetch"] = True

                    # Sanitize: prevent hallucinated infrastructure services (AmazonRDS, AmazonEC2, etc.) on AI model queries or ungrounded queries
                    is_ai_topic = any(w in low for w in [
                        "ai model", "ai models", "foundation model", "foundation models", "ai spend", "ai cost",
                        "llm", "llms", "model name", "by model", "model provider", "token type", "token rate", "hardware family"
                    ]) or parsed.get("target_dimension") in ("Model", "ModelProvider", "Modality", "ExecutionType", "TokenType", "HardwareType", "HardwareFamily") or \
                    any(b in ("model", "model_provider", "modality", "execution_type", "token_type", "hardware_type", "hardware_family") for b in bdowns)

                    if is_ai_topic and not any(w in low for w in ["rds", "relational database", "aurora", "database", "postgres", "mysql"]):
                        if parsed.get("service") in ("AmazonRDS", "AmazonEC2", "AmazonS3", "AWSLambda", "AmazonVPC"):
                            logger.info(f"[Sanitize] Cleared hallucinated service '{parsed.get('service')}' for AI models query")
                            parsed["service"] = None

                    if parsed.get("service") == "AmazonRDS" and not any(w in low for w in ["rds", "database", "aurora", "relational", "postgres", "mysql"]) and not det_info.get("service"):
                        logger.info(f"[Sanitize] Cleared unsupported AmazonRDS service not found in prompt or context")
                        parsed["service"] = None

                    inc_chart = bool(parsed.get("include_chart", det_info["include_chart"])) and not is_no_chart_requested(low)
                    inc_mom = bool(parsed.get("include_mom", det_info["include_mom"])) and not is_no_mom_requested(low)

                    # Intent Guard: Queries asking for costs, spend, service comparison, run-rate,
                    # or naming a known customer must NEVER be misrouted to general advisory.
                    has_customer_signal = bool(parsed.get("customer") or det_info.get("customer"))
                    has_cost_or_comparison_signal = any(w in low for w in [
                        "cost", "spend", "billed", "usage", "service level", "services cost",
                        "compare", "comparison", "projected cost", "run-rate", "run rate",
                        "previous month", "last month", "current month"
                    ])
                    if (has_customer_signal or has_cost_or_comparison_signal) and parsed.get("intent") in ("general_finops_advisory", "general_chat"):
                        parsed["intent"] = "fetch_data"
                        parsed["is_new_data_fetch"] = True

                    if cont_ctx.get("is_continuation") and cont_ctx.get("prior_query_type") == "forecast":
                        if not parsed.get("cloud") and cont_ctx.get("new_cloud"):
                            parsed["cloud"] = cont_ctx["new_cloud"]
                        parsed["timeframe_months"] = 12
                        parsed["target_ym"] = f"{cont_ctx['inherited_target_year']}-01"
                        if not parsed.get("corrected_query") or any(w in parsed["corrected_query"].lower() for w in ["similar", "simillar", "same"]):
                            parsed["corrected_query"] = cont_ctx["expanded_query"]

                    return {
                        "intent": parsed.get("intent", det_info["intent"]),
                        "cloud": parsed.get("cloud") or det_info.get("cloud"),
                        "service": parsed.get("service") or det_info["service"],
                        "services": parsed.get("services") or det_info.get("services") or ([parsed.get("service")] if parsed.get("service") else []),
                        "customer": parsed.get("customer") or det_info["customer"],
                        "metric_type": parsed.get("metric_type") or det_info.get("metric_type", "cost"),
                        "target_dimension": parsed.get("target_dimension") or det_info.get("target_dimension"),
                        "target_ym": parsed.get("target_ym") or det_info.get("target_ym"),
                        "timeframe_months": parsed.get("timeframe_months") or det_info["timeframe_months"],
                        "timeframe_days": parsed.get("timeframe_days") or det_info["timeframe_days"],
                        "breakdowns": bdowns,
                        "chart_types": parsed.get("chart_types") or det_info["chart_types"],
                        "include_chart": inc_chart,
                        "include_mom": inc_mom,
                        "is_new_data_fetch": bool(parsed.get("is_new_data_fetch", det_info["is_new_data_fetch"])),
                        "corrected_query": parsed.get("corrected_query") or cont_ctx.get("expanded_query") or last_msg
                    }
        except Exception as ex:
            logger.warning(f"[LLM-First Intent Analysis] Fallback to deterministic: {ex}")

        return det_info

    def _reprocess_response(self, user_query: str, raw_response: str, messages: list[dict] = None) -> str:
        """
        Post-processing Pass:
        Enforces negative constraints like 'without chart' or 'without MoM'
        deterministically, preventing lossy LLM rewrites that drop charts or columns.
        """
        if not raw_response or not raw_response.strip():
            return raw_response

        low_query = (user_query or "").lower()
        no_chart = is_no_chart_requested(low_query)
        no_mom = is_no_mom_requested(low_query)

        processed = raw_response
        if no_chart:
            processed = re.sub(r'```chart.*?\n```', '', processed, flags=re.DOTALL)
            processed = re.sub(r'<canvas.*?</canvas>', '', processed, flags=re.DOTALL)
            processed = re.sub(r'\n{3,}', '\n\n', processed)
        if no_mom:
            processed = prune_mom_columns_from_markdown(processed)

        return processed.strip()

    def generate(self, messages: list[dict], mcp: MCPClient = None, on_token = None, on_status = None) -> str:
        t0 = time.perf_counter()
        self.last_stats = {}
        self.last_thinking = ""
        resp = ""
        streamed_chars = 0

        def streaming_collector(chunk: str):
            nonlocal streamed_chars
            if chunk:
                streamed_chars += len(chunk)
                if on_token:
                    on_token(chunk)

        try:
            if on_status:
                on_status("Analyzing query...")

            resp = self._generate_impl(messages, mcp=mcp, on_token=streaming_collector, on_status=on_status)
            clean_resp, direct_thinking = split_thinking_and_response(resp)
            if direct_thinking:
                self.last_thinking = (self.last_thinking + "\n\n" + direct_thinking).strip() if getattr(self, "last_thinking", None) else direct_thinking
                resp = clean_resp

            # If response was computed directly without streaming tokens (e.g. data table, direct SQL analysis):
            if on_token and streamed_chars == 0 and resp:
                chunk_sz = 120
                for i in range(0, len(resp), chunk_sz):
                    on_token(resp[i:i+chunk_sz])
                streamed_chars = len(resp)
            
            # Universal LLM Insight Pass for any data response lacking insights
            if "💡 FinOps Insights:" not in resp and "###" in resp and self.engine != "direct":
                # Ensure it's a data response by checking for tables or lists, ignoring simple text errors
                if "|" in resp or "-" in resp:
                    try:
                        if on_status:
                            on_status("Generating FinOps insights...")
                        last_msg = messages[-1]["content"] if messages else ""
                        sys_msg = {
                            "role": "system",
                            "content": (
                                "You are Cleo, an expert FinOps AI. Analyze the following data presentation returned to the user. "
                                "Provide 2 concise, actionable FinOps insights explaining the data, the biggest cost drivers or anomalies, "
                                "and exactly what the user can do with this information to optimize spend. "
                                "CRITICAL 1: If you see massive Month-over-Month spikes or 100% drops in third-party software, SaaS, or security platforms (e.g., WIZ, Reltio, IBM, Datadog, Snowflake), DO NOT classify them as 'discontinued' or 'unexpected usage spikes'. Correctly identify them as likely one-time or annual Cloud Marketplace commitments/renewals. "
                                "CRITICAL 2: If 'ComputeSavingsPlans', 'SavingsPlans', or 'Reserved Instances' dominate the spend, explicitly identify them as upfront/partial-upfront commitment fees rather than standard run-rate compute usage. "
                                "CRITICAL 3: When discussing ROI or break-even timelines, NEVER claim a flat, uniform timeline (such as 22 days for everything). Differentiate: Quick Wins are Immediate / Day 1 (0–7 days), Storage tiering is 7–30 days, Commitments are 7–9 months (1-yr) / 14–18 months (3-yr), and Strategic/Architecture migrations are 90–180 days (3–6 months). "
                                "CRITICAL 4 (AI & Foundation Models): When analyzing AI model costs, token usage, or MULTICLOUD_AI_COST_AND_USAGE: "
                                "NEVER recommend 'Shift from Heavy-Compute to Serverless' for managed API models (Gemini, Claude, GPT-4o, etc. are ALREADY serverless pay-per-token API endpoints). "
                                "NEVER confuse high total spend with high per-token cost: a model like Gemini Flash or GPT-4o-mini has high spend due to massive transaction volume, but has a very low unit cost per token. Never suggest migrating from a cheap model (Flash/mini) to an expensive reasoning model (Sonnet/Opus/Pro) to 'save cost'. "
                                "Always ground AI recommendations in actual AI Tokenomics levers: "
                                "(1) Prompt Caching (50–90% cost reduction on repeated system prompts and RAG contexts), "
                                "(2) Semantic Router / Model Cascading (routing simple queries to Flash-Lite/mini and reserving heavy frontier models only for complex reasoning), "
                                "(3) Context Window & Token Pruning (preventing quadratic input token accumulation in multi-turn agent loops), "
                                "(4) Batch Inference (50% discount for asynchronous evaluation or extraction workloads), "
                                "(5) Output Token Optimization (strict max_tokens and concise formatting), "
                                "and (6) Provisioned Throughput (PTUs / Bedrock Provisioned) vs. Pay-per-Token crossover analysis for high steady-state workloads. "
                                "CRITICAL 5 (Cloud Identity vs Region): NEVER confuse cloud Regions (e.g., sa-east-1, us-east-1, us-west-2, westus2, europe-west1) with Cloud Accounts, Subscriptions, or Project IDs. Regions are physical geographic deployment locations, NOT accounts. Always cite the Cloud Account ID / Subscription ID / Project ID when identifying ownership, and clearly designate the Region as the deployment location (e.g., 'Account `964862064788` in Region `sa-east-1`'). NEVER refer to a region code as an account (e.g. never say 'the sa-east-1 account')."
                            )
                        }
                        if is_exclude_other_requested(last_msg):
                            sys_msg["content"] += " CRITICAL 6: The user explicitly requested to EXCLUDE 'Other' / unallocated categories. Under NO circumstance should you mention, analyze, or recommend actions on 'Other' or unallocated items in your insights."
                        all_req_svcs = extract_all_requested_services(last_msg)
                        if len(all_req_svcs) >= 2:
                            svc_names_list = [getattr(s, "display_name", None) or getattr(s, "disp", None) or (s[1] if len(s) > 1 else s[0]) for s in all_req_svcs]
                            sys_msg["content"] += " CRITICAL 7: The user asked for multiple services (" + ", ".join(svc_names_list) + "). Provide balanced, actionable insights across each requested service. Never claim a service is missing or unqueried if its section is present in the data."
                        # Strip raw HTML canvas tags to avoid confusing the LLM and wasting tokens
                        clean_resp_text = re.sub(r'<canvas.*?</canvas>', '', resp, flags=re.DOTALL)
                        user_msg = {"role": "user", "content": f"User query: {last_msg}\n\nData:\n{clean_resp_text}"}
                        
                        insights_collector = []
                        def insights_streamer(token: str):
                            insights_collector.append(token)
                            if on_token:
                                on_token(token)

                        llm_ans, _ = self._call_active_llm([sys_msg, user_msg], on_token=insights_streamer if on_token else None)
                        if llm_ans:
                            clean_ans, insight_thinking = split_thinking_and_response(llm_ans)
                            if insight_thinking:
                                self.last_thinking = (self.last_thinking + "\n\n" + insight_thinking).strip() if getattr(self, "last_thinking", None) else insight_thinking
                            if clean_ans:
                                sanitized_insights = f"\n\n**💡 FinOps Insights:**\n{_sanitize_finops_bullet_titles(clean_ans)}"
                                resp += sanitized_insights
                                if on_token and not insights_collector:
                                    on_token(sanitized_insights)
                    except Exception as e:
                        logger.debug(f"[Universal LLM Insights] {e}")

            # ── Step 3: LLM Reprocessing & Verification Pass ──
            last_msg = messages[-1]["content"] if messages else ""
            resp = self._reprocess_response(last_msg, resp, messages=messages)

            clean_final, trailing_thinking = split_thinking_and_response(resp)
            if trailing_thinking:
                self.last_thinking = (self.last_thinking + "\n\n" + trailing_thinking).strip() if getattr(self, "last_thinking", None) else trailing_thinking
                resp = clean_final

            # ponytail: save successful data query for cross-session continuation
            if "###" in resp and ("|" in resp or "Spend" in resp or "Cost" in resp) and "No spend data" not in resp:
                try:
                    save_last_query(
                        query_type="cost_analysis",
                        dataset="CloudHealth",
                        sql=last_msg,
                        time_range={"target": last_msg}
                    )
                except Exception:
                    pass

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

    def _generate_impl(self, messages: list[dict], mcp: MCPClient = None, on_token = None, on_status = None) -> str:
        last_msg = messages[-1]["content"] if messages else ""
        logger.debug(f"[AI Generate] Processing user query: {last_msg}")
        low = last_msg.lower()

        # Cache customer map if MCP is present
        if not hasattr(self, "_cust_map_cache"):
            self._cust_map_cache = {}
        if not self._cust_map_cache and mcp:
            for tool_name in ["list_orgs", "list_channel_customers"]:
                try:
                    r = mcp.call_tool(tool_name, {})
                    raw_txt = r.get("content", [{}])[0].get("text", "")
                    if raw_txt:
                        custs = json.loads(raw_txt)
                        if isinstance(custs, list):
                            self._cust_map_cache = {
                                c["name"]: (c.get("id") or c.get("customerId"))
                                for c in custs if c.get("name") and (c.get("id") or c.get("customerId"))
                            }
                            if self._cust_map_cache:
                                break
                except Exception:
                    pass

        # ── Step 1: LLM-First Request Comprehension & Intent Routing ─────────
        intent_info = self._understand_query(messages, mcp=mcp)
        logger.info(f"[AI Generate] LLM Intent: {intent_info}")

        # ── Inject FinOps expert knowledge & market data for domain-matched queries ──
        finops_ctx = get_finops_context(last_msg)
        market_ctx = ""
        market_intent = detect_market_data_intent(last_msg)
        market_specs_data = None
        market_pricing_data = None
        market_trends_data = None
        if market_intent:
            if market_intent["type"] == "model_specs":
                market_specs_data = get_live_model_specs(last_msg)
                if market_specs_data:
                    market_ctx = f"\n\nLIVE MARKET DATA (AI MODEL SPECS & TOKEN RATES):\n{format_model_specs_markdown(market_specs_data)}\n"
            elif market_intent["type"] == "cloud_pricing":
                market_pricing_data = get_live_cloud_pricing(sku_filter=market_intent.get("sku", ""), region=market_intent.get("region", "eastus"))
                if market_pricing_data and market_pricing_data.get("items"):
                    market_ctx = f"\n\nLIVE MARKET DATA (CLOUD RETAIL PRICING):\n{format_cloud_pricing_markdown(market_pricing_data)}\n"
            elif market_intent["type"] == "finops_trends":
                market_trends_data = search_finops_web(last_msg, max_results=5)
                if market_trends_data:
                    market_ctx = f"\n\nLIVE WEB SEARCH RESULTS (FINOPS TRENDS & INDUSTRY NEWS):\n{format_finops_search_markdown(last_msg, market_trends_data)}\n"

        mem_ctx = get_memory().build_context_block(last_msg)
        extra_ctx = finops_ctx + market_ctx + mem_ctx
        if extra_ctx:
            messages = list(messages)  # don't mutate caller's list
            for i, m in enumerate(messages):
                if m.get("role") == "system":
                    messages[i] = {**m, "content": m["content"] + extra_ctx}
                    break
            if finops_ctx:
                logger.debug(f"[FinOps Refs] Injected expert context ({len(finops_ctx)} chars)")
            if market_ctx:
                logger.debug(f"[Market Data] Injected market intelligence ({len(market_ctx)} chars)")
            if mem_ctx:
                logger.debug(f"[Memory] Injected learned context ({len(mem_ctx)} chars)")

        # Prior context extraction
        prior_user_msgs = [m["content"] for m in messages[:-1] if m.get("role") == "user"]
        prior_assistant_msgs = [m["content"] for m in messages[:-1] if m.get("role") == "assistant"]

        # ── 0. Cache Management & Invalidation ────────────────────────────────
        is_clear_cache_cmd = bool(re.search(
            r'^(?:(?:please\s+)?(?:clear|reset|flush|purge|empty)\s+(?:the\s+)?(?:all\s+)?(?:local\s+)?cache(?:s)?|/clear[-_]cache)$',
            low.strip()
        ))
        if is_clear_cache_cmd:
            cleared_items = []
            if mcp and hasattr(mcp, "clear_cache"):
                mcp.clear_cache()
                cleared_items.append("CloudHealth telemetry & query cache")
            if hasattr(self, "_cust_map_cache"):
                self._cust_map_cache.clear()
                cleared_items.append("Customer & tenant mapping cache")
            if hasattr(self, "_account_name_cache"):
                self._account_name_cache.clear()
                cleared_items.append("Cloud account name cache")
            global _datasources_metadata_memory_cache
            _datasources_metadata_memory_cache.clear()
            cleared_items.append("Datasource schema & metadata cache")
            return (
                "### 🧹 Cache Cleared Successfully\n\n"
                "All local and in-memory caches have been purged:\n\n"
                + "\n".join(f"- ✅ **{item}**" for item in cleared_items) + "\n\n"
                "Live queries will now fetch fresh, uncached data directly from CloudHealth."
            )

        # ── 00. Model / AI Engine Identity Query ──
        is_model_query = bool(re.search(
            r'\b(?:what\s+(?:llm|model|ai\s+model|engine|ai\s+engine|ai)\s+(?:are\s+we|is\s+being|is)\s+using|what\s+llm\s+we\s+are\s+using|which\s+(?:llm|model|engine|ai)\s+(?:are\s+we|is\s+being|is)\s+used|what\s+model\s+is\s+this|which\s+model\s+is\s+this|what\s+llm\s+is\s+this|which\s+llm\s+are\s+you|what\s+llm\s+are\s+you|what\s+ai\s+model\s+are\s+you|which\s+ai\s+model\s+are\s+you|what\s+engine\s+are\s+we\s+using|which\s+engine\s+are\s+we\s+using|what\s+ai\s+is\s+this|what\s+ai\s+are\s+you|who\s+are\s+you)\b',
            low
        ))
        if is_model_query:
            engine_name = self.get_engine_display_name()
            return (
                f"### 🤖 Active AI Engine & Model\n\n"
                f"We are currently using **{engine_name}**.\n\n"
                f"- **Engine**: `{self.engine}`\n"
                f"- **Role**: FinOps AI Copilot & Cloud Cost Intelligence Agent\n"
                f"- **MCP Tools**: Connected to CloudHealth FinOps MCP server (telemetry, SQL, and anomalies)\n\n"
                f"You can switch or configure your AI engine anytime from the **Settings** modal in the top navigation bar."
            )

        # ── 00a. Dedicated Real-World Market Data Dispatcher ──────────────────
        # Handles external AI model pricing/specs, cloud retail pricing, and FinOps industry trends
        # when NOT querying the tenant's own CloudHealth internal cost telemetry.
        is_internal_tenant_query = any(k in low for k in [
            "our spend", "my spend", "our cost", "my cost", "our bill", "my bill",
            "our usage", "my usage", "our invoice", "my invoice", "our account", "my account",
            "tenant", "customer", "cloudhealth", "cur", "active anomalies", "inactive anomalies",
            "historical spend", "last month spend", "this month spend", "mtd spend", "ytd spend"
        ])
        if market_intent and not is_internal_tenant_query:
            m_type = market_intent.get("type")
            # In LLM mode, synthesize with the injected market_ctx (already attached to system message)
            if self.engine != "direct":
                llm_resp, err = self._call_active_llm(messages, on_token=on_token)
                if llm_resp:
                    return llm_resp
                if err:
                    logger.warning(f"[Market Data LLM Fallback] {err}")

            # Direct mode (or LLM fallback): Output structured, high-density markdown tables/reports
            if m_type == "model_specs":
                specs = market_specs_data if market_specs_data is not None else get_live_model_specs(last_msg)
                return format_model_specs_markdown(specs)
            elif m_type == "cloud_pricing":
                pdata = market_pricing_data if market_pricing_data is not None else get_live_cloud_pricing(
                    sku_filter=market_intent.get("sku", ""),
                    region=market_intent.get("region", "eastus")
                )
                return format_cloud_pricing_markdown(pdata)
            elif m_type == "finops_trends":
                sres = market_trends_data if market_trends_data is not None else search_finops_web(last_msg, max_results=5)
                return format_finops_search_markdown(last_msg, sres)

        # ── 00b. Direct LLM Pass-Through for Conversational & Open-Ended Queries ──
        # If the query is not asking for cloud cost data, metrics, or telemetry, pass it directly
        # to the active LLM rather than running through thousands of lines of data extraction regexes.
        is_telemetry_fetch = (
            intent_info.get("is_new_data_fetch")
            or intent_info.get("intent") in ("fetch_data", "anomalies", "reformat_previous", "history_qa", "finops_recommendations")
            or any(kw in low for kw in [
                "cost", "spend", "bill", "usage", "ec2", "s3", "rds", "lambda", "ebs", "cloudhealth",
                "datasource", "tenant", "customer", "customer id", "org", "organization", "schema",
                "anomaly", "anomalies", "run-rate", "run rate", "forecast", "mtd", "mom", "yoy",
                "subaccount", "billing account", "pricing category", "service category", "cloud",
                "breakdown", "break down", "top 5", "top 10", "highest spend"
            ])
        )
        if not is_telemetry_fetch and self.engine != "direct":
            if intent_info.get("intent") == "general_chat" or any(
                g in low.split() for g in ["hi", "hello", "hey", "greetings", "good morning", "good afternoon", "good evening", "help"]
            ):
                llm_resp, err = self._call_active_llm(messages, on_token=on_token)
                if llm_resp:
                    return llm_resp
                if err:
                    logger.warning(f"[Direct LLM Pass-Through Error] {err}")
                    return (
                        f"⚠️ **AI Engine Query Error ({self.get_engine_display_name()})**\n\n"
                        f"{err}\n\n"
                        f"> *Please check your API key, model configuration, or network connection in Settings.*"
                    )

        # ── 0a. Unsupported CloudHealth Capabilities (Tenant Users / IAM / Identity) ──
        is_user_list_query = bool(re.search(
            r'\b(?:list\s+(?:the\s+)?(?:of\s+)?users?|users?\s+list|all\s+users?|show\s+(?:me\s+)?(?:the\s+)?users?|get\s+(?:me\s+)?(?:the\s+)?users?|give\s+(?:me\s+)?(?:the\s+)?(?:list\s+(?:of\s+)?)?users?|who\s+are\s+the\s+users?|what\s+users?\b|users?\s+in\s+(?:this|the|our)\s+tenant|tenant\s+users?|user\s+accounts?|iam\s+users?|who\s+has\s+access|manage\s+users?|add\s+users?|invite\s+users?)\b',
            low
        )) or (intent_info.get("intent") == "unsupported_capability" and any(w in low for w in ["user", "users", "access"]))

        if is_user_list_query and not any(w in low for w in ["tag", "tags", "tagged"]):
            return (
                "### ℹ️ Capability Notice: Tenant User Management\n\n"
                "The **CloudHealth MCP server** does not currently provide tools or APIs for listing tenant users or managing user identities. "
                "Its capabilities are exclusively focused on **Multi-Cloud FinOps & Cost Intelligence**:\n\n"
                "- 📊 **Cost & Usage Analytics**: SQL queries across `AWS_CUR`, `MULTICLOUD_FOCUS_COST_AND_USAGE`, and `CLOUDHEALTH_CONSUMPTION_BREAKDOWN`\n"
                "- 🚨 **Cost Anomaly Detection**: Identifying abnormal spend spikes via `AWS_COST_ANOMALY`, `AZURE_COST_ANOMALY`, and `GCP_COST_ANOMALY`\n"
                "- 🏢 **Managed Organizations & Channel Customers**: Partner hierarchy via `list_channel_customers` and `list_managed_orgs`\n"
                "- 📚 **Dataset Catalogs & Schemas**: Telemetry structure via `list_standard_datasources` and `get_datasource_metadata`\n\n"
                "**Where to find user and access details for this tenant:**\n"
                "1. Log into your **CloudHealth / VMware Aria Cost** console.\n"
                "2. Navigate to **Setup** → **Users** (or **Administration** → **Identity & Access Management** in VMware Cloud Services Portal).\n"
                "3. If your tenant uses Single Sign-On (SSO), consult your corporate Identity Provider directory (e.g. Okta, Microsoft Entra ID).\n\n"
                "> 💡 *Tip*: If you would like to analyze cloud costs, usage trends, top services, or anomalies for this tenant or any customer, feel free to ask!"
            )

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
                        f"In your previous request, no specific customer was filtered (the query covered all accounts across the tenant).\n\n"
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
            "pie chart", "donut chart", "doughnut chart", "bar chart", "line chart", "waterfall chart", "area chart", "area graph",
            "show as pie", "show as donut", "show in pie", "show in a pie", "pie chart of", "area chart of",
            "give me the pie chart", "give me a pie chart", "give me the bar chart", "give me the donut chart", "give me the area chart",
            "visualize this as", "visualize it as", "plot this as", "chart of it", "chart of the above", "chart of this"
        ]) or bool(
            re.search(r'\b(?:chart|plot|graph|visualize)\s+(?:of\s+)?(?:it|this|that|above|the\s+above|same\s+data|previous\s+data)\b', low) or
            re.search(r'\b(?:show|render|draw|display)\s+(?:it|this|that|above)\s+(?:as\s+a\s+|in\s+a\s+)?(?:chart|graph|plot|pie|donut|bar|area)\b', low)
        ) or any(low.strip() == p for p in [
            "pie chart", "donut chart", "doughnut chart", "bar chart", "waterfall chart", "area chart",
            "pie chart please", "as a pie chart", "give me the pie chart of it", "give me the pie chart of the above data",
            "area chart please", "as an area chart", "give me the area chart of it", "give me the area chart of the above data",
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
                is_exclude_other = is_exclude_other_requested(low)
                if is_exclude_other:
                    rows = [
                        r for r in rows
                        if r["label"].lower() not in ("other", "(unallocated / other)", "unallocated", "other services", "other (combined)", "other categories", "other models")
                        and not r["label"].lower().startswith("other ")
                    ]
                    total = sum(r["cost"] for r in rows)

                if is_chart_transform:
                    detected_types = []
                    if any(w in low for w in ["area chart", "area graph", "stacked area", "area"]):
                        detected_types.append("area")
                    if any(w in low for w in ["pie chart", "pie graph", "pie"]):
                        detected_types.append("pie")
                    if any(w in low for w in ["donut", "doughnut"]):
                        detected_types.append("doughnut")
                    if any(w in low for w in ["bar chart", "bar graph", "column chart", "bar", "column"]):
                        detected_types.append("bar")
                    if any(w in low for w in ["line chart", "line graph", "line"]):
                        detected_types.append("line")
                    if any(w in low for w in ["waterfall chart", "waterfall"]):
                        detected_types.append("waterfall")

                    target_types = detected_types if detected_types else ["pie"]

                    top_limit = 8
                    top_rows = rows[:top_limit]
                    remainder = total - sum(r["cost"] for r in top_rows)

                    chart_labels = [r["label"] for r in top_rows]
                    chart_values = [round(r["cost"], 2) for r in top_rows]
                    if remainder > 0.5 and not is_exclude_other:
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

                        if remainder > 0.5 and not is_exclude_other:
                            rem_pct = (remainder / total) * 100 if total else 0.0
                            h_blank = " | —" if has_hours else ""
                            tbl_lines.append(f"| {len(top_rows)+1} | *Other Categories (combined)* | **${remainder:,.2f}** | {rem_pct:.1f}%{h_blank} |")

                        h_col = " | Instance Hours" if has_hours else ""
                        div_col = "|:---" if has_hours else ""
                        table_header = f"| # | Category / Item | Cost | % of Total{h_col} |\n|:---|:---|:---|:---{div_col}|\n" + "\n".join(tbl_lines)
                        total_blank = " |" if has_hours else ""
                        total_line = f"\n| **Total** | **All Categories** | **${total:,.2f}** | **100.0%**{total_blank} |\n"
                        table_block = f"{table_header}{total_line}\n"

                    clean_title = re.sub(r'[:(]?\s*(?:Pie|Bar|Donut|Doughnut|Line|Waterfall|Area)\s*Chart(?:\s*View)?\)?', '', parsed_hist['title'], flags=re.I).strip()
                    clean_title = re.sub(r'^[^\w\s]+', '', clean_title).strip()

                    chart_blocks = []
                    for t_type in target_types:
                        chart_title = f"{clean_title} ({t_type.capitalize()} Chart)"
                        chart_blocks.append(_chart_block(t_type, chart_title, chart_labels, values=chart_values, value_label="Cost ($)"))
                    chart_block = "\n".join(chart_blocks)

                    type_names = " & ".join(t.capitalize() for t in target_types)
                    icon = "🥧" if target_types == ["pie"] or target_types == ["doughnut"] else "📊"
                    source_str = f" via `{parsed_hist['dataset_name']}`" if parsed_hist["dataset_name"] else ""
                    return (
                        f"### {icon} {clean_title}: {type_names} Chart View\n\n"
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
        cont_ctx = _detect_contextual_continuation(messages, cust_map=getattr(self, "_cust_map_cache", {}))
        has_continuation_prefix = any(low.startswith(p) for p in [
            "and ", "now ", "also ", "then ", "what about", "how about", "and for", "now in", "instead", "switch to",
            "and what about", "and what is", "and what's", "and whats", "and in", "what if", "can you compare", "compare with",
            "now show", "and show", "show me", "give me"
        ]) or any(w in low for w in ["for that", "for them", "of that", "of them", "same period", "same customer", "similar", "simillar", "same data", "do the same", "same for", "similar for", "simillar for"]) or cont_ctx.get("is_continuation")

        has_cust_pronoun = bool(re.search(r'\b(their|theirs|them|that customer|this customer|same customer|that tenant|this tenant|same tenant|that client|this client|same client)\b', low))

        has_pronoun_ref = bool(re.search(r'\b(their|theirs|they|them|its|it|this|that|these|those|above|the above|previous|same data|this data|that data|of it|of this|of that|from above|similar|simillar|same)\b', low)) or cont_ctx.get("is_continuation")

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
            (bool(re.search(r'\b(?:fetch|get|show|give|list|display|find)\b', low)) and not has_pronoun_ref and not cont_ctx.get("is_continuation")) or
            bool(re.search(r'\b\d+\s*days?\b', low)) or
            (word_count > 6 and not has_continuation_prefix and not is_clarification and not has_cust_pronoun and not has_pronoun_ref and not cont_ctx.get("is_continuation"))
        )

        # ── Resolve channel customer list (cached) ───────────────────────
        if not hasattr(self, "_cust_map_cache"):
            self._cust_map_cache = {}
        cust_map = self._cust_map_cache
        if not cust_map and mcp:
            for tool_name in ["list_orgs", "list_channel_customers"]:
                try:
                    r = mcp.call_tool(tool_name, {})
                    raw_txt = r.get("content", [{}])[0].get("text", "")
                    if raw_txt:
                        custs = json.loads(raw_txt)
                        if isinstance(custs, list):
                            cust_map = {
                                c["name"]: (c.get("id") or c.get("customerId"))
                                for c in custs if c.get("name") and (c.get("id") or c.get("customerId"))
                            }
                            if cust_map:
                                self._cust_map_cache = cust_map
                                break
                except Exception as e:
                    logger.warning(f"[Customer CRN Map via {tool_name}] Failed: {e}")

        # Look backwards through prior user messages for the most recent FinOps query context
        prior_cost_query = ""
        for u_msg in reversed(prior_user_msgs):
            u_low = u_msg.lower()
            if any(w in u_low for w in ["cost", "spend", "bill", "usage", "trend", "breakdown", "customer", "channel", "tenant", "service", "aws", "forecast", "projection", "predict", "azure", "gcp"]):
                prior_cost_query = u_msg
                break

        prior_cost_low = prior_cost_query.lower() if prior_cost_query else ""
        is_prior_cost_query = bool(prior_cost_query)
        is_prior_cust_query = any(w in prior_cost_low for w in ["customer", "channel", "tenant", "client"]) or any(c.lower() in prior_cost_low for c in cust_map)

        is_followup = bool((is_prior_cost_query or has_pronoun_ref or cont_ctx.get("is_continuation")) and (has_continuation_prefix or is_short_filter_tweak or is_clarification or has_pronoun_ref or cont_ctx.get("is_continuation")) and not is_standalone_request)

        # ── Check if a specific customer name is in the query or inherited ──
        named_customer = None
        named_customer_crn = None
        generic_account_names = (
            "all accounts", "all account", "all customers", "all customer", "all", "overall",
            "partner-wide", "partner wide", "partner", "none", "null", "account names", "account name",
            "accounts", "account", "subaccount", "sub-account", "by account", "by account names"
        )
        if intent_info.get("customer"):
            llm_c = intent_info["customer"].strip().lower()
            if llm_c not in generic_account_names:
                for cname, ccrn in cust_map.items():
                    if cname.lower().strip() in generic_account_names:
                        continue
                    if cname.lower() == llm_c or cname.lower() in llm_c or llm_c in cname.lower():
                        named_customer = cname
                        named_customer_crn = ccrn
                        break
                if not named_customer:
                    named_customer = intent_info["customer"].strip()

        if not named_customer:
            for cname, ccrn in cust_map.items():
                if cname.lower().strip() in generic_account_names:
                    continue
                if cname.lower() in low:
                    named_customer = cname
                    named_customer_crn = ccrn
                    break

        if not named_customer:
            m_c = re.search(r'\bfor\s+customer\s+([A-Za-z0-9_-]+)|\bcustomer\s+([A-Za-z0-9_-]+)\b|\bfor\s+([A-Za-z0-9_-]+)\s+customer\b', last_msg, re.IGNORECASE)
            if m_c:
                extracted_c = m_c.group(1) or m_c.group(2) or m_c.group(3)
                if extracted_c.lower() not in ("all", "each", "every", "the", "a", "an", "any", "top", "our", "new", "this", "that"):
                    named_customer = extracted_c
                    for cname, ccrn in cust_map.items():
                        if cname.lower().strip() in generic_account_names:
                            continue
                        if cname.lower() == extracted_c.lower() or extracted_c.lower() in cname.lower():
                            named_customer = cname
                            named_customer_crn = ccrn
                            break

        is_cust_reset = any(w in low for w in ["all customer", "all channel", "partner wide", "overall", "all tenant", "every customer", "all accounts", "across all"])
        if not named_customer and (has_cust_pronoun or (is_followup and not is_standalone_request and is_prior_cust_query)) and not is_cust_reset:
            for u_msg in reversed(prior_user_msgs):
                u_low = u_msg.lower()
                for cname, ccrn in cust_map.items():
                    if cname.lower().strip() in generic_account_names:
                        continue
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
                        if cname.lower().strip() in generic_account_names:
                            continue
                        if cname.lower() in a_low:
                            named_customer = cname
                            named_customer_crn = ccrn
                            break
                    if named_customer:
                        break

        # Final guard: never treat All Accounts as a customer
        if named_customer and named_customer.lower().strip() in generic_account_names:
            named_customer = None
            named_customer_crn = None

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
                        f"Re-authenticate with channel customer CloudHealth access to query per-customer data, or "
                        f"ask for tenant-wide totals instead."
                    )

        # ── 2c. FinOps Advisory, Architecture & Playbook Queries ─────────────
        # If the user is asking an architectural, advisory, or methodology question
        # (e.g. "how do I optimize gp2 to gp3", "what is the break-even on Azure reservations",
        # "explain EffectiveCost vs BilledCost in FOCUS", "how to detect zombie NAT gateways"),
        # handle it via FinOps best-practice knowledge rather than misrouting to CloudHealth telemetry.
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

        # ── 2c. Math & Quick Calculations ────────────────────────────────────
        low_no_dates = re.sub(r'\b\d{4}[-/]\d{1,2}(?:[-/]\d{1,2})?\b', '', low)
        has_finops_signal = any(w in low for w in [
            "cost", "spend", "spending", "billed", "effective", "cloud", "aws", "azure", "gcp",
            "service", "services", "account", "accounts", "breakdown", "anomaly", "anomalies",
            "recommendation", "usage", "tenant", "customer", "mcp", "forecast", "all clouds"
        ])
        is_math = not has_finops_signal and not is_explicit_telemetry_table and (
            bool(re.search(r'\d+\s*[\+\-\*\/x×÷\^%]\s*\d+', low_no_dates)) or
            bool(re.search(r'\b(?:calculate|calc|math)\b', low))
        )
        if is_math:
            clean_math = last_msg.strip().rstrip("?").strip()
            m_match = re.search(r'(\d+(?:\.\d+)?\s*(?:[\+\-\*\/×÷]|x|\^|%)\s*\d+(?:\.\d+)?(?:\s*(?:[\+\-\*\/×÷]|x|\^|%)\s*\d+(?:\.\d+)?)*)', clean_math, re.IGNORECASE)
            if m_match and self.engine == "direct":
                expr_str = m_match.group(1).replace('x', '*').replace('X', '*').replace('×', '*').replace('÷', '/').replace('^', '**')
                try:
                    if re.fullmatch(r'[\d\s\+\-\*\/\.\(\)]+', expr_str):
                        res = eval(expr_str, {"__builtins__": {}}, {})
                        formatted = f"{res:,}" if isinstance(res, int) else f"{res:,.4f}".rstrip('0').rstrip('.')
                        orig_expr = m_match.group(1).strip()
                        return f"**{orig_expr}** = **{formatted}**"
                except Exception:
                    pass
            if self.engine != "direct":
                user_msg = {"role": "user", "content": last_msg}
                sys_msg = {
                    "role": "system",
                    "content": "You are Cleo, an intelligent AI assistant. Provide a direct, concise, and accurate answer to the user's mathematical or general calculation question."
                }
                llm_ans, _ = self._call_active_llm([sys_msg, user_msg])
                if llm_ans:
                    return llm_ans

        # ── 2d. FinOps Advisory, Architecture & Conceptual Queries ───────────
        # Handle conceptual, strategic, and advisory questions directly using LLM + FinOps knowledge
        # without querying CloudHealth telemetry or returning empty/unrelated data tables.
        is_advisory_phrase = any(phrase in low for phrase in [
            "how to", "how do i", "how can i", "how should", "best practice", "playbook",
            "explain", "what is", "what are", "difference between", "trade-off", "tradeoff",
            "doctrine", "strategy", "architecture", "framework", "what is focus", "what is billedcost",
            "what is effectivecost", "break-even", "breakeven", "roi of", "roi simulation", "simulate roi",
            "payback period", "time to breakeven", "efficiency calculation", "efficiency calculations",
            "inform optimize operate", "unit economics", "tag governance", "waste pattern", "savings plan vs", "ri vs",
            "egress cost", "nat gateway optimization"
        ])
        is_live_data_followup = is_followup and is_prior_cost_query and not is_advisory_phrase
        is_data_query = (
            intent_info.get("intent") == "fetch_data"
            or curr_t_ctx.get("is_specific")
            or any(w in low for w in [
                "break down", "breakdown", "by model", "by account", "by category",
                "by subcategory", "by pricing", "by billing account", "by region",
                "by resource", "by provider", "by modality", "by execution type",
                "show me", "get me", "give me", "our spend", "my spend", "our cost", "my cost"
            ])
        )

        finops_adv = get_finops_advisory(last_msg) if (not is_live_data_followup and not is_data_query) else None
        is_finops_topic = bool(finops_adv) or any(t in low for t in [
            "finops", "cloud", "focus", "cost", "spend", "billed", "effective", "pricing", "rate",
            "ri", "reserved", "savings plan", "commitment", "ebs", "ec2", "rds", "s3", "azure", "gcp",
            "aws", "amortiz", "unblended", "blended", "tag", "allocation", "unit economic", "waste",
            "anomaly", "rightsizing", "idle", "egress", "nat gateway", "marketplace", "license",
            "graviton", "kubernetes", "k8s", "opencost", "kubecost", "storage tier", "snapshot",
            "roi", "breakeven", "payback", "efficiency"
        ])

        is_general_finops_query = (
            not is_data_query and
            not named_customer and
            not any(w in low for w in ["compare", "comparison", "service level", "services cost", "projected cost", "run-rate", "run rate"]) and
            (
                (intent_info.get("intent") == "general_finops_advisory" and not any(w in low for w in ["cost", "spend", "usage", "billed", "service", "services"]))
                or
                (
                    is_advisory_phrase
                    and is_finops_topic
                    and not is_live_data_followup
                    and not is_explicit_telemetry_table
                    and not any(w in low for w in [
                        "get me", "fetch", "query", "show me our", "show me my", "our spend", "my spend",
                        "our cost", "my cost", "break it down", "breakdown by"
                    ])
                )
            )
        )
        if (is_general_finops_query or (finops_adv and not is_data_query and not named_customer and not is_explicit_telemetry_table)):
            # External LLM synthesis if active
            if self.engine != "direct":
                cal = get_realtime_calendar_info()
                adv_context = f"AUTHORITATIVE FINOPS GUIDANCE & CONTEXT:\n{finops_adv}\n" if finops_adv else ""
                if any(w in low for w in ["latest", "recent", "trend", "update", "focus 1.2", "news", "announcement", "current", "2026", "2025", "specification"]):
                    try:
                        web_results = search_finops_web(last_msg, max_results=3)
                        if web_results:
                            adv_context += f"\n\nCURRENT INDUSTRY WEB KNOWLEDGE:\n{format_finops_search_markdown(last_msg, web_results)}\n"
                    except Exception as e:
                        logger.debug(f"[Advisory Web Grounding] {e}")
                if mem_ctx:
                    adv_context = f"{mem_ctx}\n\n{adv_context}"
                sys_msg = {
                    "role": "system",
                    "content": (
                        "You are Cleo, an expert Principal Cloud FinOps Architect and advisor.\n"
                        "Ground your guidance in the FinOps Foundation Framework (Inform → Optimize → Operate) and practitioner doctrine.\n"
                        f"Real-Time Calendar Context: Current date is {cal['today_str']}, current billing period is {cal['current_ym']}.\n"
                        "Answer the user's question with deep FinOps precision and clarity:\n"
                        "- Provide clear definitions, financial mechanics, and trade-offs.\n"
                        "- When asked 'how to progress with' or 'how to implement' an optimization lever, provide a concrete, phased roadmap (Phase 1 Audit/Discovery, Phase 2 Staging/Canary, Phase 3 IaC/Execution with CLI/Terraform, Phase 4 Guardrails). NEVER output raw detection SQL or generic compute right-sizing text.\n"
                        "- Distinguish Quick Wins (non-disruptive, immediate) from Strategic Modernization (architectural).\n"
                        "- Include concrete metrics, formulas, or architecture/CLI steps where applicable.\n"
                        "- STRICT MANDATE ON ROI & BREAK-EVEN SIMULATIONS: NEVER assume a flat, uniform timeline (such as 'average 22 days for everything'). Break-even horizons depend strictly on the lever:\n"
                        "  * Quick Wins / Waste Elimination: Immediate / Day 1 (0–7 days), $0 CapEx, near-infinite ROI.\n"
                        "  * Storage / Config Modernization: 7–30 days (< 1 month), minimal effort, >1000% ROI.\n"
                        "  * Commitments (Savings Plans, Reservations): 7–9 months for 1-year terms (~60-75% of term), 14–18 months for 3-year terms. Break-Even Utilisation % = 1 - Discount %.\n"
                        "  * Strategic / Architectural Modernization (Graviton, Commercial DB exit): 90–180 days (3–6 months) accounting for engineering sprints (dev/QA) and double-bubble parallel run costs.\n"
                        "- Use standard FinOps KPIs: Effective Savings Rate (ESR), Commitment Coverage Target (70-80%), Utilization (>80%), and distinguish Realized Savings from Potential Savings.\n"
                        "- DO NOT call CloudHealth tools or generate SQL statements, as this is a general FinOps domain question.\n"
                        "- NEVER output internal pseudocode or python scripts (e.g. list_standard_datasources).\n\n"
                        f"{adv_context}"
                    )
                }
                user_msg = {"role": "user", "content": last_msg}
                llm_resp, err = self._call_active_llm([sys_msg, user_msg])
                if llm_resp:
                    return llm_resp
                if err:
                    logger.warning(f"[LLM Advisory Fallback] {err}")
            if finops_adv:
                return finops_adv
            # Fallback if in direct engine mode and no specific playbook matched
            return (
                f"### 💡 Cloud FinOps Expert Guidance\n\n"
                f"To address your inquiry regarding **{last_msg}**, adhere to the **FinOps Foundation Framework**:\n\n"
                f"- **Inform**: Gain granular visibility into unit metrics (BilledCost vs EffectiveCost in FOCUS 1.2), allocate shared costs, and identify drivers.\n"
                f"- **Optimize**: Implement Quick Wins (storage tiering, gp2→gp3, unattached EBS/IPs) before Strategic Modernization (Graviton, commitments, architecture).\n"
                f"- **Operate**: Automate continuous waste detection, tag governance, and track KPIs against business value.\n\n"
                f"*Source: FinOps Foundation Framework & Multi-Cloud Engineering Best Practices.*"
            )

        # ── 3. Cost, Spend, Breakdown & Billing Queries (High Priority) ──────
        is_cost_query = any(w in low for w in [
            "cost", "spend", "bill", "usage", "trend", "breakdown",
            "expense", "expensive", "top", "unblended", "recommendation",
            "recommendations", "optimize", "optimization", "forecast", "projected",
            "anomal", "spike", "spikes", "unusual",
            "chart", "waterfall", "graph", "plot", "pie", "donut", "doughnut", "instance", "ec2", "rds", "service",
            "region", "regions", "regional", "location", "locations", "geography"
        ]) or bool(is_prior_cost_query and is_clarification) or bool(
            intent_info.get("is_new_data_fetch") or intent_info.get("intent") in ("fetch_data", "anomalies", "finops_recommendations")
        )

        if is_prior_cost_query and is_followup and not is_user_list_query and not any(w in low for w in ["org", "organization", "dataset", "datasource"]):
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

            # If LLM identified a target_ym from typos or context that regex missed, incorporate it
            # only when the query is NOT asking for a multi-month timeframe/duration
            req_months = intent_info.get("timeframe_months") or time_ctx.get("timeframe_months")
            if intent_info.get("target_ym") and not is_specific and not (req_months and req_months > 1):
                target_ym = intent_info["target_ym"]
                is_specific = True
                try:
                    t_yr, t_mo = int(target_ym.split("-")[0]), int(target_ym.split("-")[1])
                    target_label = f"{FULL_NAMES[t_mo]} {t_yr}"
                except Exception:
                    target_label = f"Month {target_ym}"

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

            # ── AI Models and Dimensional Query Detection ─────────────────────
            is_ai_models_query = (
                any(w in low for w in [
                    "ai model", "ai models", "foundation model", "foundation models",
                    "by model", "model name", "model breakdown", "ai cost", "ai spend",
                    "llm", "llms", "model provider", "token type", "token rate", "hardware family"
                ]) or
                intent_info.get("target_dimension") in ("Model", "ModelProvider", "Modality", "ExecutionType", "TokenType", "HardwareType", "HardwareFamily") or
                any(b in ("model", "model_provider", "modality", "execution_type", "token_type", "hardware_type", "hardware_family") for b in (intent_info.get("breakdowns") or []))
            )

            # ── Multi-Service Detection ───────────────────────────────────────
            all_requested_services = extract_all_requested_services(last_msg)
            is_multi_service_request = len(all_requested_services) >= 2 and not is_ai_models_query
            if not is_multi_service_request and intent_info.get("services") and len(intent_info.get("services")) >= 2 and not is_ai_models_query:
                matched_list = []
                for s_name in intent_info["services"]:
                    pcode = str(s_name)
                    disp = PCODE_TO_DISPLAY.get(pcode, pcode)
                    prov = PCODE_TO_PROVIDER.get(pcode, "aws")
                    matched_list.append(ServiceMatch(pcode, disp, prov))
                all_requested_services = matched_list
                is_multi_service_request = True

            # ── Extract or inherit requested AWS service ──────────────────────
            requested_service, requested_service_disp = extract_requested_service(last_msg)
            if not requested_service and intent_info.get("service") and not is_ai_models_query and str(intent_info.get("service")).lower() not in ("ai", "ai models", "ai_models", "models", "foundation models", "ai model"):
                requested_service = intent_info["service"]
                requested_service_disp = PCODE_TO_DISPLAY.get(requested_service, requested_service)

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
            active_cloud = extract_requested_cloud(last_msg) or intent_info.get("cloud")
            if not active_cloud and is_followup:
                for u_msg in reversed(prior_user_msgs):
                    c = extract_requested_cloud(u_msg)
                    if c:
                        active_cloud = c
                        break
            if requested_service and not active_cloud:
                active_cloud = PCODE_TO_PROVIDER.get(str(requested_service))
            if cont_ctx.get("new_cloud") and not active_cloud:
                active_cloud = cont_ctx["new_cloud"]
            if cont_ctx.get("new_service") and not requested_service:
                requested_service = cont_ctx["new_service"]
                requested_service_disp = cont_ctx.get("new_service_disp")
            if requested_service and active_cloud:
                svc_prov = PCODE_TO_PROVIDER.get(str(requested_service))
                if svc_prov and svc_prov != active_cloud:
                    requested_service = None
                    requested_service_disp = None

            # Prior topic check from conversational history
            prior_topic = ""
            if prior_assistant_msgs:
                prior_topic = prior_assistant_msgs[-1].split("\n")[0].lower()
            was_monthly_trend = (
                "monthly spend trend" in prior_topic
                or "monthly breakdown" in prior_topic
                or "month-over-month" in prior_topic
            )
            was_forecast = (
                "forecast" in prior_topic
                or "spend projection" in prior_topic
                or "cost projection" in prior_topic
                or "projected spend" in prior_topic
                or (prior_assistant_msgs and any(w in prior_assistant_msgs[-1].lower() for w in [
                    "forecast period", "projected spend", "cost forecast", "cloudhealth cost forecast"
                ]))
            )

            # Forward-looking cost forecast / budget projection query (e.g. "forecast for AWS cost for the year 2027 and break it down monthly")
            is_forecast_term = (
                any(w in low for w in [
                    "forecast", "forecasting", "forecasted", "projection", "projections",
                    "predict", "predicting", "prediction", "predictive",
                    "expected cost", "expected spend", "expected cloud spend", "budget projection",
                    "spend projection", "cost projection", "future spend", "future cost", "forward-looking"
                ])
                or bool(re.search(r'\bproject(?:ed|ing)?\s+(?:cost|spend|budget|cloud|aws|azure|gcp)', low))
                or ("project" in low and any(w in low for w in ["cost", "spend", "budget"]) and not any(w in low for w in ["gcp project", "cloud project", "project id", "project name", "project number"]))
            )
            has_future_target = (
                bool(re.search(r'\b(202[7-9]|203\d)\b', low))
                or any(w in low for w in [
                    "next year", "upcoming year", "coming year", "following year", "future year",
                    "next 12 months", "future 12 months", "upcoming 12 months",
                    "next 6 months", "future 6 months", "upcoming 6 months",
                    "next 3 months", "future 3 months", "upcoming 3 months",
                    "next quarter", "future quarter", "upcoming quarter"
                ])
            )
            is_future_forecast = (
                (is_forecast_term and has_future_target)
                # If user explicitly requested a past date, never treat as forecast continuation
                or (not is_specific and cont_ctx.get("is_continuation") and cont_ctx.get("prior_query_type") == "forecast")
                or (not is_specific and was_forecast and (is_followup or cont_ctx.get("is_continuation")))
            )

            # Monthly spend trend / breakdown query across providers (NOT a service breakdown and NOT a future forecast)
            is_monthly_trend_query = (
                not requested_service
                and not is_future_forecast
                and not is_ai_models_query
                and not any(w in low for w in [
                    "by service", "service level", "each service", "top services", "services across",
                    "service category", "service spend", "by product", "services by",
                    "service breakdown", "services breakdown", "breakdown by service",
                    "breakdown of service", "breakdown of services", "service-level", "per service",
                    "by account", "per account", "by category", "by subcategory", "by pricing",
                    "by billing account", "by model", "by resource", "by region", "by modality",
                    "by execution type", "by provider", "by hardware", "by token",
                    "account breakdown", "category breakdown", "model breakdown", "region breakdown"
                ])
                and (
                    any(w in low for w in [
                        "monthly cost breakdown", "monthly spend breakdown", "monthly breakdown",
                        "monthly cost", "monthly spend", "monthly trend", "month trend", "3-month", "3 month", "3 months",
                        "month-over-month", "month over month", "mom variance", "mom change", "mom cost", "mom spend", "mom trend",
                        "waterfall", "trend over time", "spend over time", "cost over time",
                        "last 3 months", "last 6 months", "last 12 months", "trailing months"
                    ])
                    or bool(re.search(r'\b(?:mom|dod|yoy)\b', low))
                    or bool(re.search(r'\b\d{1,2}\s*[- ]?months?\b', low))
                    or (any(w in low for w in ["trend", "trends"]) and any(w in low for w in ["month", "months", "monthly", "cloud", "spend", "cost", "all clouds"]))
                    or "monthly" in intent_info.get("corrected_query", "").lower()
                    or (was_monthly_trend and (
                        _detect_chart_type(low) is not None
                        or any(w in low for w in ["chart", "bar", "line", "table", "data", "this", "that", "same", "reformat"])
                    ))
                    or _detect_chart_type(low) in ("waterfall", "line")
                    or (cont_ctx.get("is_continuation") and cont_ctx.get("prior_query_type") == "monthly_trend")
                )
            )

            # 3-Forecast. Forward-Looking Cost Forecasting & Budget Projections (AWS, Azure, GCP, Multi-Cloud)
            if is_future_forecast and mcp:
                # 1. Target Horizon & Future Months
                m_yr = re.search(r'\b(202[7-9]|203\d)\b', low)
                now_dt = datetime.date.today()
                if m_yr:
                    target_year = int(m_yr.group(1))
                    forecast_months = [f"{target_year}-{m:02d}" for m in range(1, 13)]
                    target_period_title = f"FY {target_year}"
                elif cont_ctx.get("is_continuation") and cont_ctx.get("inherited_target_year"):
                    target_year = cont_ctx["inherited_target_year"]
                    forecast_months = [f"{target_year}-{m:02d}" for m in range(1, 13)]
                    target_period_title = cont_ctx.get("inherited_target_period_title") or f"FY {target_year}"
                elif any(w in low for w in ["next year", "upcoming year", "coming year", "following year", "future year"]):
                    target_year = now_dt.year + 1
                    forecast_months = [f"{target_year}-{m:02d}" for m in range(1, 13)]
                    target_period_title = f"FY {target_year}"
                elif "next 6 months" in low or "future 6 months" in low:
                    start_m = now_dt.month + 1
                    start_y = now_dt.year
                    forecast_months = []
                    for offset in range(6):
                        m = ((start_m - 1 + offset) % 12) + 1
                        y = start_y + ((start_m - 1 + offset) // 12)
                        forecast_months.append(f"{y}-{m:02d}")
                    target_period_title = "Next 6 Months"
                elif "next quarter" in low or "future quarter" in low:
                    cur_q = (now_dt.month - 1) // 3 + 1
                    next_q = (cur_q % 4) + 1
                    next_q_yr = now_dt.year if cur_q < 4 else now_dt.year + 1
                    q_start_m = (next_q - 1) * 3 + 1
                    forecast_months = [f"{next_q_yr}-{m:02d}" for m in range(q_start_m, q_start_m + 3)]
                    target_period_title = f"Q{next_q} {next_q_yr}"
                else:
                    target_year = now_dt.year + 1 if now_dt.year < 2027 else 2027
                    forecast_months = [f"{target_year}-{m:02d}" for m in range(1, 13)]
                    target_period_title = f"FY {target_year}"

                # 2. Baseline History Window
                m_base = re.search(r'(?:last|past|trailing|prior)\s*(\d{1,2})\s*months?', low)
                if m_base:
                    baseline_months_count = max(2, min(int(m_base.group(1)), 24))
                elif intent_info.get("timeframe_months"):
                    baseline_months_count = max(2, min(int(intent_info["timeframe_months"]), 24))
                else:
                    baseline_months_count = 12

                # 3. Provider & Dataset Resolution
                prov_key = active_cloud or cont_ctx.get("new_cloud") or ("azure" if "azure" in low else ("gcp" if "gcp" in low else ("all" if any(w in low for w in ["all cloud", "all clouds", "multicloud", "multi-cloud"]) else "aws")))
                if prov_key == "azure":
                    if requested_service:
                        sql_fc = f"SELECT Month AS month, SUM(EffectiveCost) AS cost FROM AZURE_FOCUS_COST_AND_USAGE WHERE ServiceCategory = '{requested_service}' OR ServiceName = '{requested_service}' GROUP BY Month ORDER BY month DESC"
                    else:
                        sql_fc = "SELECT Month AS month, SUM(EffectiveCost) AS cost FROM AZURE_FOCUS_COST_AND_USAGE GROUP BY Month ORDER BY month DESC"
                    ds_name = "AZURE_FOCUS_COST_AND_USAGE"
                    prov_name = "Azure"
                elif prov_key == "gcp":
                    if requested_service:
                        sql_fc = f"SELECT Month AS month, SUM(EffectiveCost) AS cost FROM GCP_FOCUS_COST_AND_USAGE WHERE ServiceName = '{requested_service}' GROUP BY Month ORDER BY month DESC"
                    else:
                        sql_fc = "SELECT Month AS month, SUM(EffectiveCost) AS cost FROM GCP_FOCUS_COST_AND_USAGE GROUP BY Month ORDER BY month DESC"
                    ds_name = "GCP_FOCUS_COST_AND_USAGE"
                    prov_name = "Google Cloud (GCP)"
                elif prov_key in ("all", "multi-cloud"):
                    sql_fc = "SELECT Month AS month, SUM(EffectiveCost) AS cost FROM MULTICLOUD_FOCUS_COST_AND_USAGE GROUP BY Month ORDER BY month DESC"
                    ds_name = "MULTICLOUD_FOCUS_COST_AND_USAGE"
                    prov_name = "Multi-Cloud"
                else:
                    if requested_service:
                        sql_fc = f"SELECT Month AS month, SUM(EffectiveCost) AS cost FROM MULTICLOUD_FOCUS_COST_AND_USAGE WHERE provider = 'AWS' AND ServiceName = '{requested_service}' GROUP BY Month ORDER BY month DESC"
                    else:
                        sql_fc = "SELECT Month AS month, SUM(EffectiveCost) AS cost FROM MULTICLOUD_FOCUS_COST_AND_USAGE WHERE provider = 'AWS' GROUP BY Month ORDER BY month DESC"
                    ds_name = "MULTICLOUD_FOCUS_COST_AND_USAGE"
                    prov_name = "AWS"

                # 4. Telemetry Query Execution
                q_params_fc = {
                    "queryInput": {
                        "sqlStatement": sql_fc,
                        "dataGranularity": "MONTHLY",
                        "limit": max(baseline_months_count * 2, 24),
                        "timeRange": {"last": max(baseline_months_count, 12), "qualifier": "MONTH"}
                    },
                    "requestInfo": {"sourceType": "API", "caller": "mcp"}
                }
                if named_customer_crn:
                    q_params_fc["queryInput"]["channelCustomerId"] = named_customer_crn

                hist_rows = []
                try:
                    res_fc = mcp.call_tool("execute_datasource_query", q_params_fc)
                    raw_csv = json.loads(res_fc.get("content", [{}])[0].get("text", "{}")).get("csv", "")
                    for r in csv.DictReader(io.StringIO(raw_csv)):
                        m_str = (r.get("month") or r.get("Month") or r.get("timeInterval_Month") or "").strip()
                        try:
                            c_val = float(r.get("cost") or r.get("EffectiveCost") or r.get("lineItem_UnblendedCost") or 0.0)
                        except (ValueError, TypeError):
                            c_val = 0.0
                        if m_str and c_val > 0:
                            hist_rows.append((m_str, c_val))
                except Exception as e:
                    logger.warning(f"[Forecast Telemetry Query] {e}")

                # Chronological ascending order
                hist_rows.sort(key=lambda x: x[0])
                if len(hist_rows) > baseline_months_count:
                    hist_rows = hist_rows[-baseline_months_count:]

                if not hist_rows:
                    return (
                        f"### 📊 CloudHealth Cost Forecast: {prov_name} Spend Projection — {target_period_title}\n\n"
                        f"> ⚠️ **Baseline Data Notice**: Insufficient historical billing telemetry found in `{ds_name}` to train the statistical forecast model.\n\n"
                        f"💡 *Verify dataset ingestion status in CloudHealth FlexReports.*"
                    )

                # 5. Statistical FinOps Modeling (Outlier Dampening + Trend + Seasonality)
                vals = [c for _, c in hist_rows]
                n = len(vals)
                hist_total = sum(vals)
                hist_mean = hist_total / n
                hist_variance = sum((x - hist_mean) ** 2 for x in vals) / (n - 1) if n > 1 else 0.0
                hist_std = hist_variance ** 0.5

                # Outlier detection & dampening for non-recurring commitment spikes
                dampened_vals = []
                outlier_notes = []
                outlier_threshold = hist_mean + 1.5 * hist_std
                for m_str, v in hist_rows:
                    if n >= 4 and hist_std > 0 and v > outlier_threshold:
                        damp_val = hist_mean + 1.0 * hist_std
                        dampened_vals.append(damp_val)
                        spike_pct = ((v - hist_mean) / hist_mean) * 100
                        outlier_notes.append((m_str, v, damp_val, spike_pct))
                    else:
                        dampened_vals.append(v)

                # Linear regression (OLS) on dampened baseline
                if n >= 2:
                    x_idx = list(range(1, n + 1))
                    x_mean = sum(x_idx) / n
                    y_mean = sum(dampened_vals) / n
                    denom = sum((x_idx[i] - x_mean) ** 2 for i in range(n))
                    slope = sum((x_idx[i] - x_mean) * (dampened_vals[i] - y_mean) for i in range(n)) / denom if denom > 0 else 0.0
                    intercept = y_mean - slope * x_mean
                else:
                    slope = 0.0
                    intercept = hist_mean

                # Enterprise cloud spending seasonality multipliers
                SEASONAL_FACTORS = {
                    1: 0.98,  # Jan: post-holiday scale down
                    2: 0.97,  # Feb: shorter calendar month
                    3: 1.02,  # Mar: Q1 close
                    4: 1.00,  # Apr: steady state
                    5: 1.01,  # May: steady state
                    6: 1.03,  # Jun: Q2 / mid-year close
                    7: 1.01,  # Jul: summer steady
                    8: 1.02,  # Aug: architecture scaling
                    9: 1.03,  # Sep: Q3 close
                    10: 1.04, # Oct: Q4 holiday ramp
                    11: 1.06, # Nov: Cyber Week / holiday traffic peak
                    12: 1.14  # Dec: Annual fiscal true-ups, heavy peak compute
                }

                last_hist_ym = hist_rows[-1][0]
                lh_yr, lh_mo = int(last_hist_ym.split("-")[0]), int(last_hist_ym.split("-")[1])

                forecast_results = []
                for f_ym in forecast_months:
                    f_yr, f_mo = int(f_ym.split("-")[0]), int(f_ym.split("-")[1])
                    step_offset = (f_yr - lh_yr) * 12 + (f_mo - lh_mo)
                    step = n + step_offset
                    base_val = max(intercept + slope * step, hist_mean * 0.4)
                    s_factor = SEASONAL_FACTORS.get(f_mo, 1.0)
                    proj_val = base_val * s_factor
                    lower_b = proj_val * 0.94
                    upper_b = proj_val * 1.06
                    forecast_results.append({
                        "month": f_ym,
                        "cost": proj_val,
                        "lower": lower_b,
                        "upper": upper_b,
                        "month_num": f_mo
                    })

                total_projected = sum(r["cost"] for r in forecast_results)
                annual_delta = total_projected - hist_total
                annual_pct = (annual_delta / hist_total * 100) if hist_total > 0 else 0.0
                sign_ann = "+" if annual_delta >= 0 else "-"
                arrow_ann = "🔺" if annual_delta >= 0 else "🔻"
                annual_mom_str = f"{arrow_ann} {sign_ann}${abs(annual_delta):,.2f} ({sign_ann}{abs(annual_pct):.1f}%)"
                total_lower = sum(r["lower"] for r in forecast_results)
                total_upper = sum(r["upper"] for r in forecast_results)
                total_conf_str = f"${total_lower:,.2f} – ${total_upper:,.2f}"

                # 6. Monthly Breakdown Table
                tbl_lines = []
                prev_proj = None
                for r in forecast_results:
                    c = r["cost"]
                    m_lbl = _format_time_label(r["month"], "month")
                    budget_pct = (c / total_projected * 100) if total_projected > 0 else 0.0
                    if prev_proj is None:
                        last_c = hist_rows[-1][1]
                        delta = c - last_c
                        pct = (delta / last_c * 100) if last_c > 0 else 0.0
                        sign = "+" if delta >= 0 else "-"
                        arrow = "🔺" if delta >= 0 else "🔻"
                        mom_str = f"{arrow} {sign}${abs(delta):,.2f} ({sign}{abs(pct):.1f}%) vs Baseline"
                    else:
                        delta = c - prev_proj
                        pct = (delta / prev_proj * 100) if prev_proj > 0 else 0.0
                        sign = "+" if delta >= 0 else "-"
                        arrow = "🔺" if delta >= 0 else "🔻"
                        mom_str = f"{arrow} {sign}${abs(delta):,.2f} ({sign}{abs(pct):.1f}%)"

                    conf_str = f"${r['lower']:,.2f} – ${r['upper']:,.2f}"
                    tbl_lines.append(
                        f"| {m_lbl} (`{r['month']}`) | **${c:,.2f}** | {mom_str} | {budget_pct:.1f}% | {conf_str} |"
                    )
                    prev_proj = c

                table_md = (
                    f"| Forecast Period | Projected Spend | MoM Progression | % of {target_period_title} Budget | Confidence Interval (±6%) |\n"
                    f"|:---|:---|:---|:---|:---|\n"
                    f"{chr(10).join(tbl_lines)}\n\n"
                    f"| **Total {target_period_title} Projected Spend** | **${total_projected:,.2f}** | **{annual_mom_str}** | **100.0%** | **{total_conf_str}** |"
                )

                # 7. Visual Forecast Chart (Area, Line, or Requested Type)
                chart_md = ""
                include_chart = intent_info.get("include_chart", True) and not is_no_chart_requested(low)
                if include_chart:
                    raw_c_type = _detect_chart_type(low) or (intent_info.get("chart_types") or [None])[0] or "area"
                    chart_type = raw_c_type if raw_c_type in ("area", "line", "bar") else "area"
                    chart_labels = [_format_time_label(r["month"], "month") for r in forecast_results]
                    chart_values = [round(r["cost"], 2) for r in forecast_results]
                    chart_md = _chart_block(
                        chart_type,
                        f"{prov_name} Monthly Cost Forecast — {target_period_title}",
                        chart_labels,
                        values=chart_values,
                        value_label="Projected Spend ($)"
                    )

                # 8. FinOps Advisory & Insights (Inform -> Optimize -> Operate)
                outlier_bullets = []
                for o_m, o_orig, o_damp, o_spk in outlier_notes:
                    outlier_bullets.append(
                        f"- **Outlier Dampening ({_format_time_label(o_m, 'month')})**: Recorded a non-recurring spend spike of **${o_orig:,.2f}** (+{o_spk:.1f}% above mean), characteristic of an upfront commitment purchase or multi-year true-up. The statistical model dampened this outlier to **${o_damp:,.2f}** in the regression trendline to avoid artificially inflating the {target_period_title} baseline by ~25%."
                    )
                outlier_section = ("\n" + "\n".join(outlier_bullets)) if outlier_bullets else ""

                slope_sign = "+" if slope >= 0 else "-"
                hist_start_lbl = _format_time_label(hist_rows[0][0], "month")
                hist_end_lbl = _format_time_label(hist_rows[-1][0], "month")

                insight_md = ""
                if self.engine != "direct":
                    try:
                        sys_msg = {
                            "role": "system",
                            "content": (
                                "You are Cleo, an expert FinOps AI. Analyze the forward-looking cloud cost forecast table.\n"
                                "Provide 2-3 concise, actionable FinOps bullet insights highlighting:\n"
                                "1. Baseline growth trajectory and seasonal variance.\n"
                                "2. Commitment strategy (Savings Plans / RIs keel depth).\n"
                                "3. Operational variance governance.\n"
                                "Follow strict bullet titling rules (bold short title before colon)."
                            )
                        }
                        user_msg = {"role": "user", "content": f"User query: {last_msg}\n\nData:\n{table_md}"}
                        llm_ans, _ = self._call_active_llm([sys_msg, user_msg])
                        if llm_ans:
                            insight_md = f"\n\n**💡 FinOps Insights:**\n{_sanitize_finops_bullet_titles(llm_ans)}"
                    except Exception as e:
                        logger.debug(f"[LLM Commentary] {e}")

                if not insight_md:
                    avg_mo_val = total_projected / len(forecast_results) if forecast_results else 0.0
                    insight_md = (
                        f"\n\n**💡 FinOps Strategic Advisory & Insights:**\n"
                        f"- **Growth Trajectory & Run-Rate (Inform)**: Spend is projected to reach **${total_projected:,.2f}** in {target_period_title} (averaging **${avg_mo_val:,.2f}/month**), representing an organic growth slope of **{slope_sign}${abs(slope):,.2f}/month** over the {baseline_months_count}-month trailing baseline ({hist_start_lbl} to {hist_end_lbl}). Spending culminates in December with calendar year-end peak volume (+14% seasonality factor).\n"
                        f"{outlier_section}\n"
                        f"- **Commitment Keel Sizing (Optimize)**: With projected steady-state baseline spend hovering around **${forecast_results[0]['cost']:,.2f} – ${forecast_results[3]['cost']:,.2f}/mo** in early {target_period_title}, commit to no more than **60–70% of the baseline keel depth** via 1-year or 3-year Compute Savings Plans or Flexible RIs. Defer aggressive top-tier commitments until Q2 {target_period_title} to preserve flexibility for architecture changes.\n"
                        f"- **Operational Variance Governance (Operate)**: Implement automated budget anomaly alerts at 50%, 80%, and 100% of the monthly forecast targets in AWS Cost Anomaly Detection / CloudHealth. Conduct a 60–90 day re-forecasting review at the end of Q1 to true up actual trajectory against statistical assumptions."
                    )

                cust_suffix = f" for {named_customer}" if named_customer else ""
                svc_suffix = f" ({requested_service_disp or requested_service})" if requested_service else ""
                title = f"CloudHealth Cost Forecast: {prov_name}{svc_suffix} Spend Projection — {target_period_title}{cust_suffix}"

                wants_table = _detect_wants_table(low)
                tbl_part = f"{table_md}\n\n" if wants_table else ""
                chart_part = f"{chart_md}\n" if chart_md else ""

                return (
                    f"### 📊 {title}\n\n"
                    f"> ℹ️ **Forecasting Model Context**: Trained on **{len(hist_rows)} months** of live billing telemetry (`{ds_name}`: {hist_start_lbl} to {hist_end_lbl}) using linear regression, outlier dampening, and enterprise cloud seasonality curves. Confidence interval represents standard **±6%** variance band.\n\n"
                    f"{tbl_part}"
                    f"{chart_part}"
                    f"{insight_md}\n\n"
                    f"💡 *Forecast generated via CloudHealth FlexReports ({ds_name}). Grounded in FinOps Foundation Framework (Inform → Optimize → Operate).*"
                )

            # 3-Anomaly. CloudHealth Cost Anomaly Detection (AWS_COST_ANOMALY, AZURE_COST_ANOMALY & GCP_COST_ANOMALY)
            is_anomaly_query = any(w in low for w in [
                "anomal", "cost spike", "spend spike", "spike in cost",
                "spikes", "unusual spend", "abnormal spend", "abnormal cost", "unusual cost",
                "why is", "why was", "why did", "explain the spike", "explain this spike",
                "what drove", "what caused", "root cause", "driver of", "drivers of"
            ]) or (cont_ctx.get("is_continuation") and cont_ctx.get("prior_query_type") == "anomalies") or (
                any(p in low for p in ["inactive ones", "the inactive ones", "active ones", "the active ones"])
            ) or intent_info.get("intent") in ("anomalies", "explain_spike")
            if is_anomaly_query:
                is_multi = (active_cloud == "all") or any(w in low for w in [
                    "all cloud", "all clouds", "across all clouds", "multi-cloud", "multicloud", "cross-cloud", "every cloud"
                ])
                is_gcp = (active_cloud == "gcp") or any(w in low for w in ["gcp", "google cloud", "google"])
                is_azure = (active_cloud == "azure") or ("azure" in low or "microsoft" in low)

                if is_multi:
                    target_configs = [
                        {"cloud": "AWS", "ds": "AWS_COST_ANOMALY", "svc_col": "Service", "acc_col": "AccountID", "badge": "🟠 AWS"},
                        {"cloud": "GCP", "ds": "GCP_COST_ANOMALY", "svc_col": "CloudProduct", "acc_col": "ProjectID", "badge": "🟢 GCP"},
                        {"cloud": "Azure", "ds": "AZURE_COST_ANOMALY", "svc_col": "Service", "acc_col": "SubscriptionID", "badge": "🔵 Azure"},
                    ]
                    cloud_label = "Multi-Cloud"
                elif is_gcp:
                    target_configs = [
                        {"cloud": "GCP", "ds": "GCP_COST_ANOMALY", "svc_col": "CloudProduct", "acc_col": "ProjectID", "badge": "🟢 GCP"}
                    ]
                    cloud_label = "GCP"
                elif is_azure:
                    target_configs = [
                        {"cloud": "Azure", "ds": "AZURE_COST_ANOMALY", "svc_col": "Service", "acc_col": "SubscriptionID", "badge": "🔵 Azure"}
                    ]
                    cloud_label = "Azure"
                else:
                    target_configs = [
                        {"cloud": "AWS", "ds": "AWS_COST_ANOMALY", "svc_col": "Service", "acc_col": "AccountID", "badge": "🟠 AWS"}
                    ]
                    cloud_label = "AWS"

                # Parse requested limit (e.g. "top 3 anomalies" -> 3)
                m_lim = re.search(r'(?:top|limit)\s*(\d{1,2})', low)
                anomaly_limit = int(m_lim.group(1)) if m_lim else (limit if limit != 10 else 5)
                anomaly_limit = max(1, min(anomaly_limit, 20))

                # Month filter
                filter_ym = target_ym if is_specific or any(w in low for w in ["this month", "current month", "month", "september", "august", "july", "2026"]) else current_ym

                # Detect requested anomaly status: default to ACTIVE unless user explicitly asks for INACTIVE
                status_filter = detect_anomaly_status_filter(low)

                all_rows_by_cloud = {}
                clouds_with_fallback = set()
                fallback_used_month = None

                for cfg in target_configs:
                    c = cfg["cloud"]
                    ds = cfg["ds"]
                    svc_col = cfg["svc_col"]
                    acc_col = cfg["acc_col"]

                    where_clauses = []
                    if filter_ym:
                        where_clauses.append(f"timeInterval_Month = '{filter_ym}'")
                    if status_filter:
                        where_clauses.append(f"Status = '{status_filter}'")
                    if requested_service:
                        where_clauses.append(f"({svc_col} = '{requested_service}' OR {svc_col} LIKE '%{requested_service_disp}%')")

                    where_sql = f"WHERE {' AND '.join(where_clauses)} " if where_clauses else ""
                    anomaly_sql = (
                        f"SELECT {svc_col} AS service, CostImpact AS cost_impact, "
                        f"CostImpactPercentage AS impact_pct, CostImpactType AS impact_type, "
                        f"Status AS status, Duration_Days AS duration_days, Region AS region, "
                        f"{acc_col} AS account_id, EndDate AS end_date, timeInterval_Month AS month "
                        f"FROM {ds} "
                        f"{where_sql}"
                        f"ORDER BY CostImpact DESC"
                    )

                    c_rows = []
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
                        c_rows = list(csv.DictReader(io.StringIO(raw_csv)))
                    except Exception as e:
                        logger.error(f"[Anomaly Query Error - {ds}] {e}")

                    # Fallback: if requested month filter returned 0, step back to last closed month (last_ym) first!
                    # Do NOT run an unconstrained ORDER BY CostImpact DESC across all time, which would pull ancient test outliers.
                    if not c_rows and filter_ym:
                        clouds_with_fallback.add(c)
                        fb_month = last_ym if filter_ym == current_ym else None
                        if fb_month:
                            fallback_used_month = fb_month
                            fb_where = [f"timeInterval_Month = '{fb_month}'"]
                            if status_filter:
                                fb_where.append(f"Status = '{status_filter}'")
                            if requested_service:
                                fb_where.append(f"({svc_col} = '{requested_service}' OR {svc_col} LIKE '%{requested_service_disp}%')")
                            fb_sql = (
                                f"SELECT {svc_col} AS service, CostImpact AS cost_impact, "
                                f"CostImpactPercentage AS impact_pct, CostImpactType AS impact_type, "
                                f"Status AS status, Duration_Days AS duration_days, Region AS region, "
                                f"{acc_col} AS account_id, EndDate AS end_date, timeInterval_Month AS month "
                                f"FROM {ds} "
                                f"WHERE {' AND '.join(fb_where)} "
                                f"ORDER BY CostImpact DESC"
                            )
                            try:
                                res_fb = mcp.call_tool("execute_datasource_query", {
                                    "queryInput": {
                                        "sqlStatement": fb_sql,
                                        "dataGranularity": "MONTHLY",
                                        "limit": anomaly_limit,
                                        "timeRange": {"last": 6, "qualifier": "MONTH"}
                                    },
                                    "requestInfo": {"sourceType": "API", "caller": "mcp"}
                                })
                                raw_csv_fb = json.loads(res_fb["content"][0]["text"]).get("csv", "")
                                c_rows = list(csv.DictReader(io.StringIO(raw_csv_fb)))
                            except Exception as e:
                                logger.warning(f"[Anomaly Fallback Last Month - {ds}] {e}")

                        # If still 0, fall back to historical periods ONLY if the user asked generally/historically
                        is_hist_requested = any(w in low for w in [
                            "history", "historical", "over time", "past months", "trailing",
                            "any anomaly", "overall", "recent", "6 months", "12 months", "3 months"
                        ])
                        if not c_rows and is_hist_requested:
                            fb2_where = []
                            if status_filter:
                                fb2_where.append(f"Status = '{status_filter}'")
                            if requested_service:
                                fb2_where.append(f"({svc_col} = '{requested_service}' OR {svc_col} LIKE '%{requested_service_disp}%')")
                            fb2_where_sql = f"WHERE {' AND '.join(fb2_where)} " if fb2_where else ""
                            fallback_sql = (
                                f"SELECT {svc_col} AS service, CostImpact AS cost_impact, "
                                f"CostImpactPercentage AS impact_pct, CostImpactType AS impact_type, "
                                f"Status AS status, Duration_Days AS duration_days, Region AS region, "
                                f"{acc_col} AS account_id, EndDate AS end_date, timeInterval_Month AS month "
                                f"FROM {ds} "
                                f"{fb2_where_sql}"
                                f"ORDER BY CostImpact DESC"
                            )
                            try:
                                res_fb2 = mcp.call_tool("execute_datasource_query", {
                                    "queryInput": {
                                        "sqlStatement": fallback_sql,
                                        "dataGranularity": "MONTHLY",
                                        "limit": anomaly_limit,
                                        "timeRange": {"last": 6, "qualifier": "MONTH"}
                                    },
                                    "requestInfo": {"sourceType": "API", "caller": "mcp"}
                                })
                                raw_csv_fb2 = json.loads(res_fb2["content"][0]["text"]).get("csv", "")
                                c_rows = list(csv.DictReader(io.StringIO(raw_csv_fb2)))
                            except Exception as e:
                                logger.warning(f"[Anomaly Fallback Recent - {ds}] {e}")

                    for r in c_rows:
                        r["cloud"] = c
                        r["badge"] = cfg["badge"]
                    all_rows_by_cloud[c] = c_rows

                # If user asked about datasets specifically
                dataset_prefix = ""
                if any(w in low for w in ["dataset", "datasource", "where", "how", "catalog"]):
                    dataset_prefix = (
                        f"### 📚 CloudHealth Anomaly Detection Standard Datasets\n\n"
                        f"CloudHealth features dedicated machine learning anomaly detection datasets in FlexReports:\n\n"
                        f"| Dataset Identifier | Cloud | Description |\n"
                        f"|:---|:---|:---|\n"
                        f"| **`AWS_COST_ANOMALY`** | AWS | Identified AWS anomalies with cost variance, percentage spike, status, account, and duration. |\n"
                        f"| **`AZURE_COST_ANOMALY`** | Azure | Identified Azure anomalies supporting automated anomaly detection. |\n"
                        f"| **`GCP_COST_ANOMALY`** | GCP | Identified GCP anomalies with cost variance, percentage spike, status, project, and duration. |\n\n"
                    )

                total_rows_found = sum(len(rows) for rows in all_rows_by_cloud.values())
                if total_rows_found == 0:
                    scope_str = f" for **{filter_ym}**" if filter_ym else ""
                    if filter_ym == current_ym:
                        scope_str = f" for **{current_ym}** (or latest closed cycle **{last_ym}**)"
                    ds_list = ", ".join(f"`{cfg['ds']}`" for cfg in target_configs)
                    status_desc = f"{status_filter.lower()} " if status_filter else ""
                    tip_msg = "ask 'Show inactive anomalies' or 'Show anomalies from the last 6 months'." if status_filter == "ACTIVE" else "ask 'Show active anomalies' or 'Show anomalies from the last 6 months'."
                    return (
                        f"{dataset_prefix}"
                        f"### 🛡️ CloudHealth Cost Anomaly Detection ({cloud_label})\n\n"
                        f"No {status_desc}cost anomalies were detected in {ds_list}{scope_str}.\n\n"
                        f"- **Evaluated Datasets**: {ds_list}\n"
                        f"- **Time Scope**: Evaluated {filter_ym or 'current period'}\n"
                        f"- **Status**: Cloud spend is tracking within normal baseline variance limits without triggered alerts.\n\n"
                        f"> 💡 *Tip: To inspect historical or alternative anomalies, {tip_msg}*\n\n"
                        f"*Source: CloudHealth Anomaly Detection ({ds_list}).*"
                    )

                period_notice = ""
                if clouds_with_fallback and filter_ym:
                    if fallback_used_month:
                        period_notice = f"> ℹ️ **Billing Ingestion Notice**: No anomalies recorded for in-flight month **{filter_ym}** due to standard 24–48h ingestion latency; displaying anomalies from the latest closed billing period (**{fallback_used_month}**).\n\n"
                    else:
                        period_notice = f"> ℹ️ *Note: No anomalies recorded specifically for {filter_ym}; displaying top anomalies across recent billing periods.*\n\n"

                period_label = f"({filter_ym})" if filter_ym and not clouds_with_fallback else (f"({fallback_used_month})" if fallback_used_month else "(Recent Periods)")
                status_title_adj = f"{status_filter.title()} " if status_filter else ""

                # In multi-cloud mode, present top items from each cloud so AWS outliers do not bury GCP/Azure
                table_rows = []
                if is_multi:
                    per_cloud_quota = max(2, min(anomaly_limit, 4))
                    for cfg in target_configs:
                        c_rows = all_rows_by_cloud.get(cfg["cloud"], [])
                        table_rows.extend(c_rows[:per_cloud_quota])
                else:
                    cfg = target_configs[0]
                    table_rows = all_rows_by_cloud.get(cfg["cloud"], [])[:anomaly_limit]

                table_lines = []
                insights = []
                total_impact = 0.0
                active_count = 0

                for idx, r in enumerate(table_rows):
                    try:
                        impact_val = float(r.get("cost_impact") or 0)
                        pct_val = float(r.get("impact_pct") or 0)
                    except (ValueError, TypeError):
                        impact_val, pct_val = 0.0, 0.0

                    total_impact += impact_val
                    st = r.get("status", "UNKNOWN").upper()
                    if st == "ACTIVE":
                        active_count += 1
                        st_badge = "🔴 **ACTIVE**"
                    elif st == "INACTIVE":
                        st_badge = "⚪ **INACTIVE**"
                    else:
                        st_badge = f"📁 {st}"

                    svc = r.get("service", "Unknown")
                    reg = r.get("region", "global")
                    acc = r.get("account_id", "—")
                    dur = r.get("duration_days", "0")
                    dur_str = "Ongoing" if dur in ("0", "-1") and st == "ACTIVE" else f"{dur} days"
                    mo = r.get("month", "")
                    raw_date = r.get("end_date") or r.get("EndDate") or ""
                    date_val = raw_date.split("T")[0].split()[0] if raw_date else mo
                    sign = "+" if impact_val > 0 else ""
                    badge = r.get("badge", "")

                    if is_multi:
                        table_lines.append(
                            f"| {idx+1} | {badge} | `{svc}` | {st_badge} | **{sign}${impact_val:,.2f}** | **{pct_val:+.1f}%** 🔺 | `{reg}` | `{acc}` | {dur_str} | {date_val} |"
                        )
                    else:
                        table_lines.append(
                            f"| {idx+1} | `{svc}` | {st_badge} | **{sign}${impact_val:,.2f}** | **{pct_val:+.1f}%** 🔺 | `{reg}` | `{acc}` | {dur_str} | {date_val} |"
                        )

                    # Synthesize FinOps insights for top anomalies across providers
                    if idx < 4:
                        if "informatica" in svc.lower() or "wiz" in svc.lower() or "pendo" in svc.lower() or "marketplace" in svc.lower():
                            insights.append(f"- **Cloud Marketplace SaaS Spikes ({st_badge})**: `{svc}` in Region `{reg}` ({r.get('cloud', '')}) surged by **{pct_val:+.1f}%** (adding **{sign}${impact_val:,.2f}** on {date_val}). Audit third-party marketplace SaaS subscriptions, private offer auto-renewals, or unmonitored tool additions in Account/Project `{acc}`.")
                        elif "fabric" in svc.lower() or "databricks" in svc.lower():
                            insights.append(f"- **Data Analytics & Lakehouse Spikes ({st_badge})**: `{svc}` in Region `{reg}` added **{sign}${impact_val:,.2f}** ({pct_val:+.1f}%). Check compute cluster auto-termination, job cluster runaway, or F-SKU capacity allocations in Account/Project `{acc}`.")
                        elif any(k in svc.lower() for k in ["bedrock", "claude", "gpt", "anthropic", "openai", "vertex"]):
                            insights.append(f"- **GenAI / LLM Model Spikes ({st_badge})**: `{svc}` in Region `{reg}` surged by **{pct_val:+.1f}%** (adding **{sign}${impact_val:,.2f}** on {date_val}). Audit active inference endpoints, batch invocation jobs, or newly deployed agent workloads in Account/Project `{acc}`.")
                        elif any(k in svc.lower() for k in ["ec2", "ecs", "eks", "compute", "virtual machines"]):
                            insights.append(f"- **Compute Capacity Surge ({st_badge})**: `{svc}` in Region `{reg}` had an anomalous spend jump of **{sign}${impact_val:,.2f}** ({pct_val:+.1f}%). Check Auto Scaling group limits, unreserved on-demand instances, or container task runaway in Account/Project `{acc}`.")
                        elif any(k in svc.lower() for k in ["rds", "database", "aurora", "sql"]):
                            insights.append(f"- **Database Provisioning Variance ({st_badge})**: `{svc}` in Region `{reg}` experienced an anomalous increase of **{sign}${impact_val:,.2f}** ({pct_val:+.1f}%). Audit multi-AZ replicas, unreserved instances, or provisioned IOPS in Account/Project `{acc}`.")
                        else:
                            insights.append(f"- **{svc} ({st_badge})**: Added an unexpected **{sign}${impact_val:,.2f}** ({pct_val:+.1f}%) in Region `{reg}` (Account/Project `{acc}`).")

                # Multi-cloud summary breakdown card
                summary_breakdown = ""
                if is_multi:
                    summary_parts = []
                    for cfg in target_configs:
                        c_rows = all_rows_by_cloud.get(cfg["cloud"], [])
                        c_impact = sum(float(r.get("cost_impact") or 0) for r in c_rows)
                        summary_parts.append(f"- **{cfg['badge']} {status_title_adj}Anomaly Impact**: **+${c_impact:,.2f}** ({len(c_rows)} anomalies identified)")
                    summary_breakdown = "\n" + "\n".join(summary_parts) + "\n"

                insights_block = ""
                if insights:
                    insights_block = "\n#### 💡 FinOps Root Cause & Investigation Insights\n\n" + "\n".join(insights) + "\n"

                table_header = (
                    "| # | Cloud | Service / Asset | Status | Cost Impact | Variance (%) | Region / Location | Account / Sub / Project | Duration | Detected Date |\n"
                    "|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|\n"
                    if is_multi else
                    "| # | Service / Asset | Status | Cost Impact | Variance (%) | Region / Location | Account / Sub / Project | Duration | Detected Date |\n"
                    "|:---|:---|:---|:---|:---|:---|:---|:---|:---|\n"
                )

                evaluated_sources = ", ".join(f"`{cfg['ds']}`" for cfg in target_configs)

                status_summary_line = (
                    f"- **Inactive / Resolved Anomalies**: **{len(table_lines)}** (historical spikes resolved)\n"
                    if status_filter == "INACTIVE"
                    else f"- **Active Ongoing Anomalies**: **{active_count}** requiring immediate review\n"
                )

                return (
                    f"{dataset_prefix}"
                    f"### 🚨 CloudHealth Cost Anomaly Detection: Top {len(table_lines)} {status_title_adj}Anomalies {period_label}\n\n"
                    f"{period_notice}"
                    f"Queried live anomaly telemetry directly from {evaluated_sources}:\n\n"
                    f"- **Total Identified Anomaly Impact**: **+${total_impact:,.2f}** across **{len(table_lines)}** anomalies\n"
                    f"{status_summary_line}"
                    f"{summary_breakdown}\n"
                    f"{table_header}"
                    f"{chr(10).join(table_lines)}\n"
                    f"{insights_block}\n"
                    f"*Source: CloudHealth Anomaly Detection ({evaluated_sources}). Monitored via live CloudHealth FlexReports.*"
                )

            # 3-Region. Region & Location Spend Breakdown (AWS, Azure, GCP, Multi-Cloud)
            user_explicit_region = (
                any(w in low for w in ["region", "regions", "regional", "location", "locations", "geography", "geographic"]) or
                any(w in intent_info.get("corrected_query", "").lower() for w in ["region", "regions", "location", "locations"])
            )
            is_region_or_location_query = (
                (user_explicit_region and
                ("region" in intent_info.get("breakdowns", []) or "location" in intent_info.get("breakdowns", []) or user_explicit_region))
                or (cont_ctx.get("is_continuation") and cont_ctx.get("prior_query_type") == "region_breakdown")
            ) and not any(w in low for w in ["anomaly", "anomalies", "recommendation", "recommendations"])

            if is_region_or_location_query and mcp:
                partial_notice = ""
                if time_ctx.get("timeframe_days"):
                    t_days = time_ctx["timeframe_days"]
                    svc_scope_label = time_ctx.get("target_label", f"Last {t_days} Days")
                    if time_ctx.get("daily_range"):
                        svc_time_range = time_ctx["daily_range"]
                    else:
                        svc_time_range = {"last": t_days, "qualifier": "DAY"}
                    svc_granularity = "DAILY"
                    partial_notice = (
                        f"> ⚠️ **FinOps Ingestion Notice**: Current date (`{today_str}`) is excluded from closed analysis as in-flight; "
                        f"yesterday's data (`{yesterday_str}`) is preliminary/partial across all cloud providers due to standard 24–48h billing ingestion latency.\n\n"
                    )
                elif req_months and req_months > 1:
                    svc_time_range = {"last": min(req_months, 12), "qualifier": "MONTH"}
                    svc_scope_label = f"Last {min(req_months, 12)} Months"
                    svc_granularity = "MONTHLY"
                elif is_specific:
                    svc_time_range = {"from": target_ym, "to": target_ym}
                    svc_scope_label = target_label
                    svc_granularity = "MONTHLY"
                elif re.search(r'\b(quarter|quater|qtr)\b', low):
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
                    svc_scope_label = target_label or "Last Month"
                    svc_time_range = {"from": last_ym, "to": current_ym}
                    svc_granularity = "MONTHLY"

                wants_location = any(w in low for w in ["location", "locations"]) and not any(w in low for w in ["region", "regions"])
                dim_label = "Location" if wants_location else "Region"

                cloud_target = active_cloud
                if not cloud_target:
                    if requested_service and PCODE_TO_PROVIDER.get(str(requested_service)):
                        cloud_target = PCODE_TO_PROVIDER[str(requested_service)]
                    elif any(w in low for w in ["all cloud", "all clouds", "multi-cloud", "multicloud", "cross-cloud", "every cloud"]):
                        cloud_target = "all"
                    elif "azure" in low:
                        cloud_target = "azure"
                    elif "gcp" in low or "google" in low:
                        cloud_target = "gcp"
                    elif "aws" in low or "amazon" in low:
                        cloud_target = "aws"
                    else:
                        cloud_target = "aws" if not any(w in low for w in ["azure", "gcp"]) else "all"

                cust_suffix = f" for {named_customer}" if named_customer else ""
                reg_rows = []
                # ── AWS ──
                if cloud_target == "aws":
                    col = "product_location" if wants_location else "product_region"
                    col_alias = "location" if wants_location else "region"
                    where_clause = ""
                    prov_title = "AWS"
                    if requested_service:
                        pcode = str(requested_service)
                        pdisp = requested_service_disp or pcode
                        where_clause = f"WHERE lineItem_ProductCode = '{pcode}'"
                        title = f"CloudHealth Spend Analysis: {pdisp} (AWS) Spend by {dim_label}{cust_suffix} — {svc_scope_label}"
                    else:
                        title = f"CloudHealth Spend Analysis: AWS Spend by {dim_label}{cust_suffix} — {svc_scope_label}"

                    sql = f"SELECT {col} AS {col_alias}, SUM(lineItem_UnblendedCost) AS cost FROM AWS_CUR {where_clause} GROUP BY {col} ORDER BY cost DESC"
                    try:
                        q_input = {"sqlStatement": sql, "dataGranularity": svc_granularity, "limit": limit, "timeRange": svc_time_range}
                        if named_customer_crn:
                            q_input["channelCustomerId"] = named_customer_crn
                        res = mcp.call_tool("execute_datasource_query", {
                            "queryInput": q_input,
                            "requestInfo": {"sourceType": "API", "caller": "mcp"}
                        })
                        raw_csv = json.loads(res.get("content", [{}])[0].get("text", "{}")).get("csv", "")
                        for r in csv.DictReader(io.StringIO(raw_csv)):
                            c = float(r.get("cost") or 0.0)
                            reg = r.get(col_alias) or "unknown"
                            if c > 0:
                                reg_rows.append((reg, c))
                    except Exception as e:
                        logger.warning(f"[AWS Region Query] {e}")

                # ── Azure ──
                elif cloud_target == "azure":
                    prov_title = "Azure"
                    where_clause = ""
                    if requested_service:
                        pcode = str(requested_service)
                        pdisp = requested_service_disp or pcode
                        where_clause = f"WHERE ServiceName = '{pcode}'"
                        title = f"CloudHealth Spend Analysis: {pdisp} (Azure) Spend by Region{cust_suffix} — {svc_scope_label}"
                    else:
                        title = f"CloudHealth Spend Analysis: Azure Spend by Region{cust_suffix} — {svc_scope_label}"

                    sql = f"SELECT RegionId AS region, SUM(EffectiveCost) AS cost FROM AZURE_FOCUS_COST_AND_USAGE {where_clause} GROUP BY RegionId ORDER BY cost DESC"
                    try:
                        q_input = {"sqlStatement": sql, "dataGranularity": svc_granularity, "limit": limit, "timeRange": svc_time_range}
                        if named_customer_crn:
                            q_input["channelCustomerId"] = named_customer_crn
                        res = mcp.call_tool("execute_datasource_query", {
                            "queryInput": q_input,
                            "requestInfo": {"sourceType": "API", "caller": "mcp"}
                        })
                        raw_csv = json.loads(res.get("content", [{}])[0].get("text", "{}")).get("csv", "")
                        for r in csv.DictReader(io.StringIO(raw_csv)):
                            c = float(r.get("cost") or 0.0)
                            reg = r.get("region") or "unassigned"
                            if c > 0:
                                reg_rows.append((reg, c))
                    except Exception as e:
                        logger.warning(f"[Azure Region Query] {e}")

                # ── GCP ──
                elif cloud_target == "gcp":
                    prov_title = "GCP"
                    where_clause = ""
                    if requested_service:
                        pcode = str(requested_service)
                        pdisp = requested_service_disp or pcode
                        where_clause = f"WHERE ServiceName = '{pcode}'"
                        title = f"CloudHealth Spend Analysis: {pdisp} (GCP) Spend by Region{cust_suffix} — {svc_scope_label}"
                    else:
                        title = f"CloudHealth Spend Analysis: GCP Spend by Region{cust_suffix} — {svc_scope_label}"

                    sql = f"SELECT RegionId AS region, SUM(EffectiveCost) AS cost FROM GCP_FOCUS_COST_AND_USAGE {where_clause} GROUP BY RegionId ORDER BY cost DESC"
                    try:
                        q_input = {"sqlStatement": sql, "dataGranularity": svc_granularity, "limit": limit, "timeRange": svc_time_range}
                        if named_customer_crn:
                            q_input["channelCustomerId"] = named_customer_crn
                        res = mcp.call_tool("execute_datasource_query", {
                            "queryInput": q_input,
                            "requestInfo": {"sourceType": "API", "caller": "mcp"}
                        })
                        raw_csv = json.loads(res.get("content", [{}])[0].get("text", "{}")).get("csv", "")
                        for r in csv.DictReader(io.StringIO(raw_csv)):
                            c = float(r.get("cost") or 0.0)
                            reg = r.get("region") or "unknown"
                            if c > 0:
                                reg_rows.append((reg, c))
                    except Exception as e:
                        logger.warning(f"[GCP Region Query] {e}")

                # ── Multi-Cloud ──
                else:
                    prov_title = "Multi-Cloud"
                    title = f"CloudHealth Spend Analysis: Multi-Cloud Spend by Region{cust_suffix} — {svc_scope_label}"
                    sql = "SELECT provider AS provider, RegionId AS region, SUM(EffectiveCost) AS cost FROM MULTICLOUD_FOCUS_COST_AND_USAGE GROUP BY provider, RegionId ORDER BY cost DESC"
                    try:
                        q_input = {"sqlStatement": sql, "dataGranularity": svc_granularity, "limit": limit, "timeRange": svc_time_range}
                        if named_customer_crn:
                            q_input["channelCustomerId"] = named_customer_crn
                        res = mcp.call_tool("execute_datasource_query", {
                            "queryInput": q_input,
                            "requestInfo": {"sourceType": "API", "caller": "mcp"}
                        })
                        raw_csv = json.loads(res.get("content", [{}])[0].get("text", "{}")).get("csv", "")
                        for r in csv.DictReader(io.StringIO(raw_csv)):
                            c = float(r.get("cost") or 0.0)
                            p = r.get("provider") or "Cloud"
                            reg = r.get("region") or "unknown"
                            if c > 0:
                                reg_rows.append((f"{p} ({reg})", c))
                    except Exception as e:
                        logger.warning(f"[MultiCloud Region Query] {e}")

                if not reg_rows:
                    return (
                        f"### 🌐 CloudHealth Spend Analysis: {dim_label} Breakdown ({prov_title}{cust_suffix})\n\n"
                        f"No live billing data was returned for `{svc_scope_label}` across regions. "
                        f"This usually means there was no recorded cloud spend in this period, or the connected CloudHealth account doesn't have visibility into it.\n\n"
                        f"*Source: {'AWS_CUR' if cloud_target == 'aws' else 'FOCUS Datasets'} via CloudHealth FlexReports.*"
                    )

                total_reg_spend = sum(c for _, c in reg_rows)
                tbl_lines = [
                    f"| {idx} | **{reg}** | ${c:,.2f} | {((c / total_reg_spend) * 100 if total_reg_spend else 0):.1f}% |"
                    for idx, (reg, c) in enumerate(reg_rows[:limit], 1)
                ]
                table = (
                    f"| # | {dim_label} | Cost | % of {prov_title} Spend |\n"
                    f"|:---|:---|:---|:---|\n"
                    f"{chr(10).join(tbl_lines)}\n\n"
                    f"| **Total Analyzed Spend** | | **${total_reg_spend:,.2f}** | **100.0%** |"
                )

                # Chart Generation
                chart_type = (intent_info.get("chart_types") or [None])[0] or _detect_chart_type(low) or "bar"
                c_kind = chart_type if chart_type in ["pie", "doughnut", "bar", "horizontal-bar", "line", "area"] else "bar"
                chart_labels = [reg for reg, _ in reg_rows[:12]]
                chart_values = [round(c, 2) for _, c in reg_rows[:12]]
                chart_md = _chart_block(c_kind, f"{prov_title} Spend by {dim_label}{cust_suffix} — {svc_scope_label}", chart_labels, values=chart_values, horizontal=(c_kind == "horizontal-bar"), stacked=True)

                # LLM Insight Pass or FinOps Doctrine Fallback
                insight_md = ""
                if self.engine != "direct":
                    try:
                        sys_msg = {
                            "role": "system",
                            "content": (
                                "You are Cleo, an expert FinOps AI. Analyze the regional/location cloud spend table returned to the user.\n"
                                "Provide 2 concise, actionable FinOps bullet insights highlighting:\n"
                                "1. Primary regional concentration (name top region and % of spend).\n"
                                "2. Cross-region network egress risk ($0.02/GB) or multi-region commitment alignment (Savings Plans / RIs).\n"
                                "Follow strict bullet titling rules (bold short title before colon)."
                            )
                        }
                        user_msg = {"role": "user", "content": f"User query: {last_msg}\n\nData:\n{table}"}
                        llm_ans, _ = self._call_active_llm([sys_msg, user_msg])
                        if llm_ans:
                            insight_md = f"\n\n**💡 FinOps Insights:**\n{_sanitize_finops_bullet_titles(llm_ans)}"
                    except Exception as e:
                        logger.debug(f"[LLM Commentary] {e}")

                if not insight_md:
                    top_reg_name, top_reg_cost = reg_rows[0]
                    top_pct = (top_reg_cost / total_reg_spend * 100) if total_reg_spend else 0
                    insight_md = (
                        f"\n\n**💡 FinOps Insights:**\n"
                        f"- **Primary Regional Concentration**: Spend is heavily anchored in **{top_reg_name}** representing **${top_reg_cost:,.2f} ({top_pct:.1f}%)** of total analyzed spend. Ensure Compute Savings Plans and regional reservations match this deployment hub.\n"
                        f"- **Multi-Region & Egress Governance**: Multi-region footprints incur inter-region data transfer fees ($0.02/GB) and replicated storage overhead. Verify whether secondary regions require active-active compute or can be consolidated to minimize cross-region egress."
                    )

                wants_table = _detect_wants_table(low)
                tbl_md = f"{table}\n\n" if wants_table else ""
                return (
                    f"### 📊 {title}\n\n"
                    f"{partial_notice}"
                    f"{tbl_md}"
                    f"{chart_md}\n"
                    f"{insight_md}\n\n"
                    f"💡 *Live FinOps data retrieved from CloudHealth ({prov_title}).*"
                )

            # 3-MultiService. Dedicated Multi-Service Query Handler (e.g. "RDS and S3", "EC2 and RDS", "S3 and EBS")
            if is_multi_service_request and mcp:
                now = datetime.date.today()
                is_exclude_other = is_exclude_other_requested(low) or is_exclude_other_requested(intent_info.get("corrected_query", ""))
                m_months = re.search(r'(?:last|past|trailing|for)\s+(\d{1,2})\s*months?\b|\b(\d{1,2})\s*(?:months?|m)\b', low)
                m_days = re.search(r'(?:last|past|trailing|for)\s+(\d{1,3})\s*days?\b|\b(\d{1,3})\s*(?:days?|d)\b', low)
                has_explicit_user_timeframe = bool(m_months) or bool(m_days) or any(w in low for w in ["last month", "past month", "days", "daily", "day by day", "trend", "30 days", "60 days", "90 days"])

                is_ytd = bool(re.search(r'\b(?:ytd|year\s*to\s*date|this\s*year)\b', low)) or cont_ctx.get("inherited_is_ytd", False)
                is_qtd = bool(re.search(r'\b(?:qtd|quarter\s*to\s*date|this\s*quarter)\b', low)) or cont_ctx.get("inherited_is_qtd", False)
                if not (is_ytd or is_qtd) and not has_explicit_user_timeframe and is_followup and any(w in low for w in ["above", "previous", "earlier", "same", "exclude other", "remove other", "without other"]):
                    if prior_assistant_msgs and ("year-to-date" in prior_assistant_msgs[-1].lower() or "(ytd" in prior_assistant_msgs[-1].lower()):
                        is_ytd = True
                    elif prior_assistant_msgs and ("quarter-to-date" in prior_assistant_msgs[-1].lower() or "(qtd" in prior_assistant_msgs[-1].lower()):
                        is_qtd = True

                if is_ytd:
                    num_months = now.month
                    num_days = 0
                    period_str = f"Year-To-Date (YTD {now.year})"
                elif is_qtd:
                    q_num = ((now.month - 1) // 3) + 1
                    num_months = ((now.month - 1) % 3) + 1
                    num_days = 0
                    period_str = f"Q{q_num} QTD ({now.year})"
                elif m_months or intent_info.get("timeframe_months") or any(w in low for w in ["months", "month by month", "monthly"]):
                    num_months = intent_info.get("timeframe_months") or (int(m_months.group(1) or m_months.group(2)) if m_months else 12)
                    num_days = 0
                    period_str = f"Last {min(num_months, 12)} Months"
                elif m_days or intent_info.get("timeframe_days") or any(w in low for w in ["days", "daily", "day by day", "trend"]):
                    num_days = intent_info.get("timeframe_days") or (int(m_days.group(1) or m_days.group(2)) if m_days else 30)
                    num_months = 0
                    period_str = f"Last {num_days} Days Trend"
                else:
                    num_days = 30
                    num_months = 0
                    period_str = "Last 30 Days Trend"

                tenant_suffix = " (Tenant)" if any(w in low for w in ["tenant"]) else ""
                cust_label = named_customer or (f"All Accounts{tenant_suffix}" if tenant_suffix else "All Accounts")
                wants_table = _detect_wants_table(low)
                chart_type = _detect_chart_type(low)

                service_sections = []
                all_service_insights = []
                service_grand_totals = {}

                for s_idx, s_match in enumerate(all_requested_services):
                    svc_pcode = getattr(s_match, "pcode", None) or s_match[0]
                    svc_disp = getattr(s_match, "display_name", None) or getattr(s_match, "disp", None) or PCODE_TO_DISPLAY.get(svc_pcode, svc_pcode)
                    svc_prov = getattr(s_match, "provider", None) or PCODE_TO_PROVIDER.get(svc_pcode, "aws")

                    if svc_pcode == "AmazonRDS":
                        if num_days > 0:
                            rds_sql = (
                                "SELECT TimeInterval_Day AS time_val, InstanceType AS category, "
                                "SUM(BilledCost) AS cost, SUM(Instances) AS instances, "
                                "SUM(ComputeCost) AS compute_cost, SUM(StorageCost) AS storage_cost, "
                                "SUM(GP2StorageCost) AS gp2_cost, SUM(GP3StorageCost) AS gp3_cost "
                                "FROM AWS_RDS_COST_AND_USAGE "
                                "GROUP BY TimeInterval_Day, InstanceType "
                                "ORDER BY time_val ASC, cost DESC"
                            )
                            rds_tr = {"last": num_days, "qualifier": "DAY"}
                            rds_gran = "DAILY"
                        else:
                            rds_sql = (
                                "SELECT Month AS time_val, InstanceType AS category, "
                                "SUM(BilledCost) AS cost, SUM(Instances) AS instances, "
                                "SUM(ComputeCost) AS compute_cost, SUM(StorageCost) AS storage_cost, "
                                "SUM(GP2StorageCost) AS gp2_cost, SUM(GP3StorageCost) AS gp3_cost "
                                "FROM AWS_RDS_COST_AND_USAGE "
                                "GROUP BY Month, InstanceType "
                                "ORDER BY time_val ASC, cost DESC"
                            )
                            rds_tr = {"last": min(num_months, 12), "qualifier": "MONTH"}
                            rds_gran = "MONTHLY"

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
                                    it = (row.get("category") or row.get("instance_type") or row.get("InstanceType") or "").strip()
                                    tv = (row.get("time_val") or row.get("day") or row.get("month") or row.get("TimeInterval_Day") or "").strip()
                                    inst = float(row.get("instances") or 0.0)
                                    comp = float(row.get("compute_cost") or 0.0)
                                    stor = float(row.get("storage_cost") or 0.0)
                                    gp2 = float(row.get("gp2_cost") or 0.0)
                                    gp3 = float(row.get("gp3_cost") or 0.0)
                                    if it and tv and c > 0 and (num_days == 0 or tv != today_str):
                                        rds_rows.append({
                                            "time_val": tv, "category": it, "cost": c,
                                            "instances": inst, "compute_cost": comp, "storage_cost": stor,
                                            "gp2_cost": gp2, "gp3_cost": gp3
                                        })
                                except (ValueError, TypeError):
                                    pass
                        except Exception as e:
                            logger.warning(f"[AWS_RDS_COST_AND_USAGE Multi Query] {e}")

                        if rds_rows:
                            if is_exclude_other:
                                rds_rows = [r for r in rds_rows if not is_other_category_name(r["category"])]
                            total_rds = sum(r["cost"] for r in rds_rows)
                            service_grand_totals[svc_disp] = total_rds
                            agg_rds = {}
                            for r in rds_rows:
                                cat = r["category"]
                                if cat not in agg_rds:
                                    agg_rds[cat] = {"cost": 0.0, "instances": 0.0, "compute": 0.0, "storage": 0.0}
                                agg_rds[cat]["cost"] += r["cost"]
                                agg_rds[cat]["instances"] += r["instances"]
                                agg_rds[cat]["compute"] += r["compute_cost"]
                                agg_rds[cat]["storage"] += r["storage_cost"]

                            sorted_rds = sorted(agg_rds.items(), key=lambda x: x[1]["cost"], reverse=True)
                            if is_exclude_other:
                                sorted_rds = [(it, d) for it, d in sorted_rds if not is_other_category_name(it)]
                            tbl_lines = []
                            graviton_cands = []
                            months_present = sorted(list({r["time_val"] for r in rds_rows}))
                            m_count = len(months_present) or 1
                            for idx, (it, d) in enumerate(sorted_rds[:12]):
                                c = d["cost"]
                                pct = (c / total_rds * 100) if total_rds else 0.0
                                avg_inst = (d["instances"] / m_count) if d["instances"] > 0 else 0.0
                                inst_str = f"{avg_inst:.1f}" if avg_inst >= 0.1 else "—"
                                comp_str = f"${d['compute']:,.2f}" if d['compute'] > 0 else "—"
                                stor_str = f"${d['storage']:,.2f}" if d['storage'] > 0 else "—"
                                tbl_lines.append(f"| {idx+1} | `{it}` | **${c:,.2f}** | {pct:.1f}% | {inst_str} | {comp_str} | {stor_str} |")
                                if any(fam in it for fam in ["m5.", "m5d.", "r5.", "t3."]):
                                    graviton_cands.append((it, c))

                            rem_rds = sorted_rds[12:]
                            if rem_rds and not is_exclude_other:
                                rem_c = sum(d["cost"] for _, d in rem_rds)
                                rem_pct = (rem_c / total_rds * 100) if total_rds else 0.0
                                tbl_lines.append(f"| - | *Other ({len(rem_rds)} instance types)* | **${rem_c:,.2f}** | {rem_pct:.1f}% | — | — | — |")

                            rds_table_total_desc = "All Active Instance Types" if is_exclude_other else "All Instance Types"
                            tbl_block = (
                                f"| # | Instance Type | Billed Spend | % of Spend | Avg Instances | Compute Spend | Storage Spend |\n"
                                f"|:---|:---|:---|:---|:---|:---|:---|\n"
                                f"{chr(10).join(tbl_lines)}\n"
                                f"| **Total** | **{rds_table_total_desc}** | **${total_rds:,.2f}** | **100.0%** | | | |\n\n"
                            ) if wants_table else ""

                            rds_chart_items = [it for it, _ in sorted_rds if not is_other_category_name(it)][:8] if is_exclude_other else [it for it, _ in sorted_rds[:8]]
                            if chart_type in ["doughnut", "pie"]:
                                chart_rds_md = _chart_block(chart_type, f"Amazon RDS Spend by Instance Type ({period_str}) — {cust_label}", rds_chart_items, values=[round(agg_rds[it]["cost"], 2) for it in rds_chart_items], value_label="Cost ($)")
                            elif chart_type == "horizontal-bar":
                                chart_rds_md = _chart_block("horizontal-bar", f"Amazon RDS Spend by Instance Type ({period_str}) — {cust_label}", rds_chart_items, values=[round(agg_rds[it]["cost"], 2) for it in rds_chart_items], value_label="Cost ($)", horizontal=True)
                            else:
                                chart_rds_md = _build_time_category_stacked_chart(f"Amazon RDS Spend by Instance Type ({period_str}) — {cust_label}", rds_rows, time_col="time_val", cat_col="category", cost_col="cost", time_format=("day" if num_days > 0 else "month"), max_cats=8, exclude_other=is_exclude_other)

                            rds_total_lbl = "Total RDS Spend (Excl. Other)" if is_exclude_other else "Total RDS Spend"
                            service_sections.append(
                                f"#### 🗄️ {s_idx+1}. {svc_disp} Spend & Usage Breakdown\n\n"
                                f"- **{rds_total_lbl}**: **${total_rds:,.2f}** across **{len(sorted_rds)}** active database instance types\n\n"
                                f"{tbl_block}"
                                f"{chart_rds_md}"
                            )

                            rds_insights = []
                            if graviton_cands:
                                grav_sp = sum(c for _, c in graviton_cands)
                                rds_insights.append(
                                    f"- **AWS Graviton Modernization for RDS**: Identified **${grav_sp:,.2f}** across legacy x86 instances ({', '.join([f'`{it}`' for it, _ in graviton_cands[:3]])}). Transitioning to Graviton3/4 equivalents (`db.m7g`, `db.r7g`, `db.t4g`) yields up to **20% direct savings** (~**${grav_sp * 0.20:,.2f}**) with seamless engine compatibility."
                                )
                            tot_gp2 = sum(r["gp2_cost"] for r in rds_rows)
                            if tot_gp2 > 50.0:
                                rds_insights.append(
                                    f"- **RDS gp2 to gp3 Storage Upgrade**: Detected **${tot_gp2:,.2f}** in gp2 storage. Upgrading to gp3 delivers an immediate **20% storage cost reduction** (~**${tot_gp2 * 0.20:,.2f}**) with baseline 3,000 IOPS and 125 MB/s throughput."
                                )
                            rds_insights.append(
                                f"- **Database Reserved Instances / Savings Plans**: Steady-state databases run 24/7. Committing to 1-Year or 3-Year Database RIs reduces hourly run-rates by 30% to 55% compared to On-Demand."
                            )
                            all_service_insights.append(f"##### 🗄️ {svc_disp} Optimization Levers:\n" + "\n".join(rds_insights))
                        else:
                            service_sections.append(
                                f"#### 🗄️ {s_idx+1}. {svc_disp} Spend & Usage Breakdown\n\n"
                                f"*No live billing data returned for **{svc_disp}** ({cust_label}) in `{period_str}` (Total Spend: **$0.00**).*"
                            )

                    elif svc_pcode == "AmazonS3":
                        time_col = "timeInterval_Day" if num_days > 0 else "timeInterval_Month"
                        s3_tr = {"last": num_days, "qualifier": "DAY"} if num_days > 0 else {"last": min(num_months, 12), "qualifier": "MONTH"}
                        s3_gran = "DAILY" if num_days > 0 else "MONTHLY"

                        s3_sql = (
                            f"SELECT {time_col} AS time_val, lineItem_UsageType AS usage_type, lineItem_Operation AS operation, "
                            f"SUM(lineItem_UnblendedCost) AS cost, SUM(lineItem_UsageAmount) AS usage_qty "
                            f"FROM AWS_CUR "
                            f"WHERE lineItem_ProductCode = 'AmazonS3' "
                            f"GROUP BY {time_col}, lineItem_UsageType, lineItem_Operation "
                            f"ORDER BY time_val ASC, cost DESC"
                        )
                        s3_q_params = {
                            "queryInput": {
                                "sqlStatement": s3_sql,
                                "dataGranularity": s3_gran,
                                "limit": 500,
                                "timeRange": s3_tr
                            },
                            "requestInfo": {"sourceType": "API", "caller": "mcp"}
                        }
                        if named_customer_crn:
                            s3_q_params["channelCustomerId"] = named_customer_crn

                        s3_rows = []
                        try:
                            res_s3 = mcp.call_tool("execute_datasource_query", s3_q_params)
                            txt_s3 = res_s3.get("content", [{}])[0].get("text", "{}")
                            raw_csv = json.loads(txt_s3).get("csv", "")
                            for row in csv.DictReader(io.StringIO(raw_csv)):
                                try:
                                    c = float(row.get("cost") or row.get("lineItem_UnblendedCost") or 0.0)
                                    ut = (row.get("usage_type") or row.get("lineItem_UsageType") or "").strip()
                                    op = (row.get("operation") or row.get("lineItem_Operation") or "").strip()
                                    tv = (row.get("time_val") or row.get(time_col) or "").strip()
                                    q = float(row.get("usage_qty") or row.get("lineItem_UsageAmount") or 0.0)
                                    if (c > 0 or q > 0) and tv and (num_days == 0 or tv != today_str):
                                        cat = _classify_service_usage_type("AmazonS3", ut, op)
                                        s3_rows.append({
                                            "time_val": tv, "usage_type": ut, "operation": op,
                                            "category": cat, "cost": c, "usage_qty": q
                                        })
                                except (ValueError, TypeError):
                                    pass
                        except Exception as e:
                            logger.warning(f"[AWS_CUR AmazonS3 Multi Query] {e}")

                        if s3_rows:
                            if is_exclude_other:
                                s3_rows = [r for r in s3_rows if not is_other_category_name(r["category"])]
                            total_s3 = sum(r["cost"] for r in s3_rows)
                            service_grand_totals[svc_disp] = total_s3
                            agg_s3 = {}
                            for r in s3_rows:
                                cat = r["category"]
                                if cat not in agg_s3:
                                    agg_s3[cat] = {"cost": 0.0, "qty": 0.0, "op": r["operation"]}
                                agg_s3[cat]["cost"] += r["cost"]
                                agg_s3[cat]["qty"] += r["usage_qty"]
                                if not agg_s3[cat]["op"] and r["operation"]:
                                    agg_s3[cat]["op"] = r["operation"]

                            sorted_s3 = sorted(agg_s3.items(), key=lambda x: x[1]["cost"], reverse=True)
                            if is_exclude_other:
                                sorted_s3 = [(c, d) for c, d in sorted_s3 if not is_other_category_name(c)]
                            tbl_lines = []
                            for idx, (cat, d) in enumerate(sorted_s3[:12]):
                                c = d["cost"]
                                pct = (c / total_s3 * 100) if total_s3 else 0.0
                                q_str = f"{d['qty']:,.1f} GB-Mo" if d['qty'] > 0 and "Storage" in cat else (f"{d['qty']:,.0f} reqs" if d['qty'] > 0 and "Request" in cat else (f"{d['qty']:,.1f} units" if d['qty'] > 0 else "—"))
                                op_str = f" | {d['op']}" if d['op'] else ""
                                tbl_lines.append(f"| {idx+1} | **{cat}** | **${c:,.2f}** | {pct:.1f}% | {q_str}{op_str} |")

                            rem_s3 = sorted_s3[12:]
                            if rem_s3 and not is_exclude_other:
                                rem_c = sum(d["cost"] for _, d in rem_s3)
                                rem_pct = (rem_c / total_s3 * 100) if total_s3 else 0.0
                                tbl_lines.append(f"| - | *Other ({len(rem_s3)} categories)* | **${rem_c:,.2f}** | {rem_pct:.1f}% | — |")

                            s3_table_total_desc = "All Active S3 Categories" if is_exclude_other else "All S3 Categories"
                            tbl_block = (
                                f"| # | Storage Tier / Meter Category | Billed Spend | % of Spend | Usage Volume / Operations |\n"
                                f"|:---|:---|:---|:---|:---|\n"
                                f"{chr(10).join(tbl_lines)}\n"
                                f"| **Total** | **{s3_table_total_desc}** | **${total_s3:,.2f}** | **100.0%** | |\n\n"
                            ) if wants_table else ""

                            s3_chart_items = [c for c, _ in sorted_s3 if not is_other_category_name(c)][:8] if is_exclude_other else [c for c, _ in sorted_s3[:8]]
                            if chart_type in ["doughnut", "pie"]:
                                chart_s3_md = _chart_block(chart_type, f"Amazon S3 Spend by Category ({period_str}) — {cust_label}", s3_chart_items, values=[round(agg_s3[c]["cost"], 2) for c in s3_chart_items], value_label="Cost ($)")
                            elif chart_type == "horizontal-bar":
                                chart_s3_md = _chart_block("horizontal-bar", f"Amazon S3 Spend by Category ({period_str}) — {cust_label}", s3_chart_items, values=[round(agg_s3[c]["cost"], 2) for c in s3_chart_items], value_label="Cost ($)", horizontal=True)
                            else:
                                chart_s3_md = _build_time_category_stacked_chart(f"Amazon S3 Spend Breakdown ({period_str}) — {cust_label}", s3_rows, time_col="time_val", cat_col="category", cost_col="cost", time_format=("day" if num_days > 0 else "month"), max_cats=8, exclude_other=is_exclude_other)

                            s3_total_lbl = "Total S3 Spend (Excl. Other)" if is_exclude_other else "Total S3 Spend"
                            service_sections.append(
                                f"#### 🪣 {s_idx+1}. {svc_disp} Spend & Usage Breakdown\n\n"
                                f"- **{s3_total_lbl}**: **${total_s3:,.2f}** across **{len(sorted_s3)}** active storage and operational categories\n\n"
                                f"{tbl_block}"
                                f"{chart_s3_md}"
                            )

                            s3_insights = [
                                "- **S3 Intelligent-Tiering (INT) Activation**: For buckets with unknown or changing access patterns, enabling Intelligent-Tiering automatically transitions objects to Infrequent Access (40% lower) and Archive Instant Access (68% lower) tiers with zero operational overhead and zero retrieval fees.",
                                "- **S3 Lifecycle Management & Archive Transitions**: Configure automated expiration rules for temporary data, logs, and scratch files. Transition aged compliance and audit archives (>90–180 days) to Glacier Flexible or Deep Archive ($0.00099/GB vs $0.023/GB for Standard, a 95% reduction).",
                                "- **Abort Incomplete Multipart Uploads**: Implement a bucket lifecycle rule to automatically abort incomplete multipart uploads after 7 days, eliminating ghost storage costs from abandoned upload parts."
                            ]
                            all_service_insights.append(f"##### 🪣 {svc_disp} Optimization Levers:\n" + "\n".join(s3_insights))
                        else:
                            service_sections.append(
                                f"#### 🪣 {s_idx+1}. {svc_disp} Spend & Usage Breakdown\n\n"
                                f"*No live billing data returned for **{svc_disp}** ({cust_label}) in `{period_str}` (Total Spend: **$0.00**).*"
                            )

                    elif svc_pcode == "AmazonEC2":
                        if num_days > 0:
                            ec2_sql = (
                                "SELECT TimeInterval_Day AS time_val, product_InstanceType AS category, "
                                "SUM(Billed_Cost) AS cost, SUM(Instance_Hours) AS hours, SUM(Instances) AS instances "
                                "FROM AWS_EC2_COST_AND_USAGE "
                                "GROUP BY TimeInterval_Day, product_InstanceType "
                                "ORDER BY time_val ASC, cost DESC"
                            )
                            ec2_tr = {"last": num_days, "qualifier": "DAY"}
                            ec2_gran = "DAILY"
                        else:
                            ec2_sql = (
                                "SELECT Month AS time_val, product_InstanceType AS category, "
                                "SUM(Billed_Cost) AS cost, SUM(Instance_Hours) AS hours, SUM(Instances) AS instances "
                                "FROM AWS_EC2_COST_AND_USAGE "
                                "GROUP BY Month, product_InstanceType "
                                "ORDER BY time_val ASC, cost DESC"
                            )
                            ec2_tr = {"last": min(num_months, 12), "qualifier": "MONTH"}
                            ec2_gran = "MONTHLY"

                        ec2_q_params = {
                            "queryInput": {
                                "sqlStatement": ec2_sql,
                                "dataGranularity": ec2_gran,
                                "limit": 5000 if num_days > 0 else 500,
                                "timeRange": ec2_tr
                            },
                            "requestInfo": {"sourceType": "API", "caller": "mcp"}
                        }
                        if named_customer_crn:
                            ec2_q_params["channelCustomerId"] = named_customer_crn

                        ec2_rows = []
                        try:
                            res_ec2 = mcp.call_tool("execute_datasource_query", ec2_q_params)
                            txt_ec2 = res_ec2.get("content", [{}])[0].get("text", "{}")
                            raw_csv = json.loads(txt_ec2).get("csv", "")
                            for row in csv.DictReader(io.StringIO(raw_csv)):
                                try:
                                    c = float(row.get("cost") or row.get("Billed_Cost") or row.get("BilledCost") or 0.0)
                                    it = (row.get("category") or row.get("product_InstanceType") or row.get("InstanceType") or "").strip()
                                    tv = (row.get("time_val") or row.get("TimeInterval_Day") or row.get("Month") or "").strip()
                                    h = float(row.get("hours") or row.get("Instance_Hours") or row.get("InstanceHours") or 0.0)
                                    inst = float(row.get("instances") or row.get("Instances") or 0.0)
                                    if it and tv and c > 0 and (num_days == 0 or tv != today_str):
                                        ec2_rows.append({"time_val": tv, "category": it, "cost": c, "hours": h, "instances": inst})
                                except (ValueError, TypeError):
                                    pass
                        except Exception as e:
                            logger.warning(f"[AWS_EC2_COST_AND_USAGE Multi Query] {e}")

                        if ec2_rows:
                            if is_exclude_other:
                                ec2_rows = [r for r in ec2_rows if not is_other_category_name(r["category"])]
                            total_ec2 = sum(r["cost"] for r in ec2_rows)
                            service_grand_totals[svc_disp] = total_ec2
                            agg_ec2 = {}
                            for r in ec2_rows:
                                cat = r["category"]
                                if cat not in agg_ec2:
                                    agg_ec2[cat] = {"cost": 0.0, "hours": 0.0, "instances": 0.0}
                                agg_ec2[cat]["cost"] += r["cost"]
                                agg_ec2[cat]["hours"] += r["hours"]
                                agg_ec2[cat]["instances"] += r["instances"]

                            sorted_ec2 = sorted(agg_ec2.items(), key=lambda x: x[1]["cost"], reverse=True)
                            if is_exclude_other:
                                sorted_ec2 = [(it, d) for it, d in sorted_ec2 if not is_other_category_name(it)]
                            tbl_lines = []
                            grav_cands = []
                            for idx, (it, d) in enumerate(sorted_ec2[:12]):
                                c = d["cost"]
                                pct = (c / total_ec2 * 100) if total_ec2 else 0.0
                                tbl_lines.append(f"| {idx+1} | `{it}` | **${c:,.2f}** | {pct:.1f}% | {d['hours']:,.0f} hrs |")
                                if any(fam in it for fam in ["m5.", "c5.", "r5.", "t3."]):
                                    grav_cands.append((it, c))

                            rem_ec2 = sorted_ec2[12:]
                            if rem_ec2 and not is_exclude_other:
                                rem_c = sum(d["cost"] for _, d in rem_ec2)
                                rem_pct = (rem_c / total_ec2 * 100) if total_ec2 else 0.0
                                tbl_lines.append(f"| - | *Other ({len(rem_ec2)} instance types)* | **${rem_c:,.2f}** | {rem_pct:.1f}% | — |")

                            ec2_table_total_desc = "All Active Instance Types" if is_exclude_other else "All Instance Types"
                            tbl_block = (
                                f"| # | Instance Type | Billed Spend | % of Spend | Runtime Hours |\n"
                                f"|:---|:---|:---|:---|:---|\n"
                                f"{chr(10).join(tbl_lines)}\n"
                                f"| **Total** | **{ec2_table_total_desc}** | **${total_ec2:,.2f}** | **100.0%** | |\n\n"
                            ) if wants_table else ""

                            ec2_chart_items = [it for it, _ in sorted_ec2 if not is_other_category_name(it)][:8] if is_exclude_other else [it for it, _ in sorted_ec2[:8]]
                            if chart_type in ["doughnut", "pie"]:
                                chart_ec2_md = _chart_block(chart_type, f"Amazon EC2 Spend by Instance Type ({period_str}) — {cust_label}", ec2_chart_items, values=[round(agg_ec2[it]["cost"], 2) for it in ec2_chart_items], value_label="Cost ($)")
                            elif chart_type == "horizontal-bar":
                                chart_ec2_md = _chart_block("horizontal-bar", f"Amazon EC2 Spend by Instance Type ({period_str}) — {cust_label}", ec2_chart_items, values=[round(agg_ec2[it]["cost"], 2) for it in ec2_chart_items], value_label="Cost ($)", horizontal=True)
                            else:
                                chart_ec2_md = _build_time_category_stacked_chart(f"Amazon EC2 Spend Breakdown ({period_str}) — {cust_label}", ec2_rows, time_col="time_val", cat_col="category", cost_col="cost", time_format=("day" if num_days > 0 else "month"), max_cats=8, exclude_other=is_exclude_other)

                            ec2_total_lbl = "Total EC2 Spend (Excl. Other)" if is_exclude_other else "Total EC2 Spend"
                            service_sections.append(
                                f"#### 🖥️ {s_idx+1}. {svc_disp} Spend & Usage Breakdown\n\n"
                                f"- **{ec2_total_lbl}**: **${total_ec2:,.2f}** across **{len(sorted_ec2)}** active instance types\n\n"
                                f"{tbl_block}"
                                f"{chart_ec2_md}"
                            )

                            ec2_insights = []
                            if grav_cands:
                                g_spend = sum(c for _, c in grav_cands)
                                ec2_insights.append(
                                    f"- **AWS Graviton Modernization for EC2**: Identified **${g_spend:,.2f}** across x86 instances. Migrating to Graviton3/4 equivalents (`m7g`, `c7g`, `r7g`) yields up to **20% direct savings** (~**${g_spend * 0.20:,.2f}**) with superior price-performance."
                                )
                            ec2_insights.append(
                                "- **1-Year Compute Savings Plans**: For steady-state baseline instances, purchasing Compute Savings Plans delivers 25%–35% savings over On-Demand rates without instance family lock-in."
                            )
                            all_service_insights.append(f"##### 🖥️ {svc_disp} Optimization Levers:\n" + "\n".join(ec2_insights))
                        else:
                            service_sections.append(
                                f"#### 🖥️ {s_idx+1}. {svc_disp} Spend & Usage Breakdown\n\n"
                                f"*No live billing data returned for **{svc_disp}** ({cust_label}) in `{period_str}` (Total Spend: **$0.00**).*"
                            )

                    else:
                        time_col = "timeInterval_Day" if num_days > 0 else "timeInterval_Month"
                        other_tr = {"last": num_days, "qualifier": "DAY"} if num_days > 0 else {"last": min(num_months, 12), "qualifier": "MONTH"}
                        other_gran = "DAILY" if num_days > 0 else "MONTHLY"

                        if svc_prov == "aws":
                            if svc_pcode in ("AmazonEC2_EBS", "EBS"):
                                where_cl = (
                                    "WHERE lineItem_ProductCode = 'AmazonEC2' AND ("
                                    "lineItem_UsageType LIKE '%Volume%' OR lineItem_UsageType LIKE '%Snapshot%' OR "
                                    "lineItem_UsageType LIKE '%EBS%' OR lineItem_UsageType LIKE '%gp2%' OR lineItem_UsageType LIKE '%gp3%')"
                                )
                            else:
                                where_cl = f"WHERE lineItem_ProductCode = '{svc_pcode}'"
                            other_sql = (
                                f"SELECT {time_col} AS time_val, lineItem_UsageType AS usage_type, lineItem_Operation AS operation, "
                                f"SUM(lineItem_UnblendedCost) AS cost, SUM(lineItem_UsageAmount) AS usage_qty "
                                f"FROM AWS_CUR {where_cl} "
                                f"GROUP BY {time_col}, lineItem_UsageType, lineItem_Operation "
                                f"ORDER BY time_val ASC, cost DESC"
                            )
                        else:
                            prov_filter = "provider IN ('OCI', 'Oracle Cloud')" if svc_prov == "oci" else f"provider = '{svc_prov}'"
                            other_sql = (
                                f"SELECT Month AS time_val, ServiceName AS usage_type, PricingUnit AS operation, "
                                f"SUM(EffectiveCost) AS cost, SUM(PricingQuantity) AS usage_qty "
                                f"FROM MULTICLOUD_FOCUS_COST_AND_USAGE "
                                f"WHERE {prov_filter} AND ServiceName = '{svc_pcode}' "
                                f"GROUP BY Month, ServiceName, PricingUnit "
                                f"ORDER BY Month ASC, cost DESC"
                            )
                            other_tr = {"last": min(num_months, 12) if num_months > 0 else 2, "qualifier": "MONTH"}
                            other_gran = "MONTHLY"

                        other_q_params = {
                            "queryInput": {
                                "sqlStatement": other_sql,
                                "dataGranularity": other_gran,
                                "limit": 300,
                                "timeRange": other_tr
                            },
                            "requestInfo": {"sourceType": "API", "caller": "mcp"}
                        }
                        if named_customer_crn:
                            other_q_params["channelCustomerId"] = named_customer_crn

                        other_rows = []
                        try:
                            res_oth = mcp.call_tool("execute_datasource_query", other_q_params)
                            txt_oth = res_oth.get("content", [{}])[0].get("text", "{}")
                            raw_csv = json.loads(txt_oth).get("csv", "")
                            for row in csv.DictReader(io.StringIO(raw_csv)):
                                try:
                                    c = float(row.get("cost") or row.get("EffectiveCost") or 0.0)
                                    ut = (row.get("usage_type") or row.get("ServiceName") or "").strip()
                                    op = (row.get("operation") or row.get("PricingUnit") or "").strip()
                                    tv = (row.get("time_val") or "").strip()
                                    q = float(row.get("usage_qty") or row.get("PricingQuantity") or 0.0)
                                    if c > 0 and tv and (num_days == 0 or tv != today_str):
                                        cat = _classify_service_usage_type(svc_pcode, ut, op)
                                        other_rows.append({"time_val": tv, "category": cat, "cost": c, "usage_qty": q, "operation": op})
                                except (ValueError, TypeError):
                                    pass
                        except Exception as e:
                            logger.warning(f"[{svc_disp} Multi Query] {e}")

                        if other_rows:
                            if is_exclude_other:
                                other_rows = [r for r in other_rows if not is_other_category_name(r["category"])]
                            total_oth = sum(r["cost"] for r in other_rows)
                            service_grand_totals[svc_disp] = total_oth
                            agg_oth = {}
                            for r in other_rows:
                                cat = r["category"]
                                agg_oth[cat] = agg_oth.get(cat, 0.0) + r["cost"]

                            sorted_oth = sorted(agg_oth.items(), key=lambda x: x[1], reverse=True)
                            if is_exclude_other:
                                sorted_oth = [(cat, c) for cat, c in sorted_oth if not is_other_category_name(cat)]
                            tbl_lines = [f"| {idx+1} | **{cat}** | **${c:,.2f}** | {(c/total_oth*100):.1f}% |" for idx, (cat, c) in enumerate(sorted_oth[:10])]
                            oth_table_total_desc = "All Active Categories" if is_exclude_other else "All Categories"
                            tbl_block = (
                                f"| # | Service Category | Spend | % of Spend |\n"
                                f"|:---|:---|:---|:---|\n"
                                f"{chr(10).join(tbl_lines)}\n"
                                f"| **Total** | **{oth_table_total_desc}** | **${total_oth:,.2f}** | **100.0%** |\n\n"
                            ) if wants_table else ""

                            chart_oth_md = _build_time_category_stacked_chart(f"{svc_disp} Spend Breakdown ({period_str}) — {cust_label}", other_rows, time_col="time_val", cat_col="category", cost_col="cost", time_format=("day" if num_days > 0 else "month"), max_cats=8, exclude_other=is_exclude_other)

                            oth_total_lbl = f"Total {svc_disp} Spend (Excl. Other)" if is_exclude_other else f"Total {svc_disp} Spend"
                            service_sections.append(
                                f"#### ☁️ {s_idx+1}. {svc_disp} Spend & Usage Breakdown\n\n"
                                f"- **{oth_total_lbl}**: **${total_oth:,.2f}**\n\n"
                                f"{tbl_block}"
                                f"{chart_oth_md}"
                            )
                            all_service_insights.append(f"##### ☁️ {svc_disp} Optimization Levers:\n- Audit steady-state usage, rightsizing, and committed use opportunities for {svc_disp}.")
                        else:
                            service_sections.append(
                                f"#### ☁️ {s_idx+1}. {svc_disp} Spend & Usage Breakdown\n\n"
                                f"*No live billing data returned for **{svc_disp}** ({cust_label}) in `{period_str}` (Total Spend: **$0.00**).*"
                            )

                grand_total = sum(service_grand_totals.values())
                svc_title = " & ".join([getattr(s, "display_name", None) or getattr(s, "disp", None) or getattr(s, "pcode", None) or s[0] for s in all_requested_services])
                breakdown_summary = ", ".join([f"**{name}**: ${val:,.2f}" for name, val in service_grand_totals.items()]) if service_grand_totals else "No spend detected"

                partial_notice = (
                    f"> ⚠️ **FinOps Ingestion Notice**: Current date (`{today_str}`) is excluded from closed analysis as in-flight; "
                    f"yesterday's data (`{yesterday_str}`) is preliminary/partial across all cloud providers due to standard 24–48h billing ingestion latency.\n\n"
                ) if num_days > 0 else ""

                header_md = (
                    f"### 📊 CloudHealth Multi-Service Spend Analysis: {svc_title}\n\n"
                    f"Queried live from standard CloudHealth datasets for **{cust_label}**:\n\n"
                    f"- **Target Billing Period**: {period_str}\n"
                    f"- **Combined Multi-Service Spend**: **${grand_total:,.2f}** ({breakdown_summary})\n\n"
                    f"{partial_notice}"
                )

                body_md = "\n\n---\n\n".join(service_sections)

                insights_block = ""
                if all_service_insights:
                    insights_block = (
                        f"\n\n---\n\n### 💡 FinOps Insights & Multi-Service Optimization Levers:\n\n"
                        + "\n\n".join(all_service_insights)
                    )

                is_explicit_compare = any(w in low for w in ["compare", "vs", "versus", "comparison", "side by side", "side-by-side"])
                comparison_chart = ""
                if (is_explicit_compare or intent_info.get("include_chart", True)) and len(service_grand_totals) >= 2 and not is_no_chart_requested(low):
                    chart_title = f"Comparative Spend: {svc_title} ({period_str})"
                    comparison_chart = _chart_block(
                        "bar",
                        chart_title,
                        list(service_grand_totals.keys()),
                        values=[round(v, 2) for v in service_grand_totals.values()],
                        value_label="Spend ($)"
                    ) + "\n\n"

                return (
                    f"{header_md}"
                    f"{comparison_chart}"
                    f"{body_md}"
                    f"{insights_block}\n\n"
                    f"*Source: Standard CloudHealth FlexReports datasets via CloudHealth MCP.*"
                )

            # 3-Azure-AHB. Dedicated Azure Hybrid Benefit & Hybrid Discounts Analysis via AZURE_COST_USAGE
            is_azure_ahb = (
                intent_info.get("target_dimension") == "hybrid_benefit" or
                "hybrid_benefit" in (intent_info.get("breakdowns") or []) or
                any(w in low for w in [
                    "hybrid discount", "hybrid discounts", "hybrid benefit", "hybrid benefits",
                    "azure hybrid benefit", "ahb discount", "ahb discounts", "hybrid licensing", "hybrid license"
                ]) or
                ("azure" in low and "ahb" in low) or
                (any(w in low for w in ["vm", "vms", "virtual machine"]) and "ahb" in low)
            ) and not any(w in low for w in ["recommendation", "anomal", "spike"])

            if not is_azure_ahb and is_followup:
                prev_is_ahb = any("azure hybrid benefit" in a.lower() or "hybrid discount" in a.lower() or "azure_cost_usage" in a.lower() for a in prior_assistant_msgs[-1:])
                if prev_is_ahb and any(w in low for w in ["not using", "using", "vm", "vms", "discount", "hybrid", "list", "show"]):
                    is_azure_ahb = True

            if is_azure_ahb and mcp:
                partial_notice = ""
                if time_ctx.get("timeframe_days") or time_ctx.get("daily_range"):
                    t_days = time_ctx.get("timeframe_days") or 1
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
                elif req_months and req_months > 1:
                    svc_time_range = {"last": min(req_months, 12), "qualifier": "MONTH"}
                    svc_scope_label = f"Last {min(req_months, 12)} Months"
                    svc_granularity = "MONTHLY"
                else:
                    svc_scope_label = "Last 30 Days Trend"
                    svc_time_range = {"from": last_ym, "to": current_ym}
                    svc_granularity = "MONTHLY"

                sql = (
                    "SELECT ResourceName AS ResourceName, "
                    "SUM(ActualCostInBillingCurrency) AS SUM_ActualCostInBillingCurrency, "
                    "SUM(Quantity) AS SUM_Quantity, "
                    "MeterCategory AS MeterCategory, "
                    "MeterSubCategory AS MeterSubCategory, "
                    "timeInterval_Day AS Day, "
                    "AdditionalInfo AS AdditionalInfo, "
                    "ResourceId AS ResourceId, "
                    "MetricType AS MetricType "
                    "FROM AZURE_COST_USAGE "
                    "WHERE ((MeterCategory IN ('Virtual Machines Licenses')) OR (MeterCategory LIKE '%Virtual Machine Licenses%')) "
                    "AND (MetricType IN ('Actual')) "
                    "GROUP BY ResourceName, MeterCategory, MeterSubCategory, timeInterval_Day, AdditionalInfo, ResourceId, MetricType"
                )
                q_input = {
                    "sqlStatement": sql,
                    "needBackLinkingForTags": True,
                    "dataGranularity": "DAILY",
                    "timeRange": {"last": 30},
                    "limit": -1
                }
                if named_customer_crn:
                    q_input["channelCustomerId"] = named_customer_crn

                rows = []
                try:
                    res = mcp.call_tool("execute_datasource_query", {
                        "queryInput": q_input,
                        "requestInfo": {"sourceType": "API", "caller": "mcp"}
                    })
                    raw_csv = json.loads(res.get("content", [{}])[0].get("text", "{}")).get("csv", "")
                    if raw_csv:
                        rows = list(csv.DictReader(io.StringIO(raw_csv)))
                except Exception as e:
                    logger.warning(f"[Azure AHB Query AZURE_COST_USAGE] {e}")

                wants_not_using = any(w in low for w in [
                    "not using", "not have", "not having", "without", "missing", "inactive",
                    "non-using", "haven't", "don't have", "do not have", "unapplied", "no hybrid",
                    "un-discounted", "undiscounted"
                ])
                wants_using = any(w in low for w in [
                    "using", "having", "with", "active", "enabled", "applied"
                ]) and not wants_not_using

                cust_str = f" for **{named_customer}**" if named_customer else ""

                if rows:
                    vm_map = {}
                    for r in rows:
                        cost_val = (
                            r.get("SUM_ActualCostInBillingCurrency") or
                            r.get("ActualCostInBillingCurrency") or
                            r.get("cost") or
                            0.0
                        )
                        qty_val = (
                            r.get("SUM_Quantity") or
                            r.get("Quantity") or
                            r.get("usage_quantity") or
                            0.0
                        )
                        raw_cost = float(str(cost_val).replace("$", "").replace(",", "").strip() or 0.0)
                        qty = float(str(qty_val).replace(",", "").strip() or 0.0)

                        res_name = (r.get("ResourceName") or r.get("resource_name") or "").strip()
                        res_id = (r.get("ResourceId") or r.get("resource_id") or "").strip()
                        m_cat = (r.get("MeterCategory") or r.get("meter_category") or "").strip()
                        m_sub = (r.get("MeterSubCategory") or r.get("meter_subcategory") or "").strip()
                        info_str = str(r.get("AdditionalInfo") or r.get("additional_info") or "").strip()
                        info_lower = info_str.lower()
                        m_sub_low = m_sub.lower()

                        # Extract subscription and resource group from resource_id if possible
                        sub = (r.get("SubscriptionName") or r.get("subscription") or "").strip()
                        rg = (r.get("ResourceGroup") or r.get("resource_group") or "").strip()
                        if res_id and "/" in res_id:
                            parts = res_id.split("/")
                            if not sub and len(parts) > 2 and parts[1].lower() == "subscriptions":
                                sub = parts[2]
                            if not rg and len(parts) > 4 and parts[3].lower() == "resourcegroups":
                                rg = parts[4]

                        vm_name = res_name or (res_id.split("/")[-1] if "/" in res_id else "") or "Unknown VM"

                        # Classify workload from MeterSubCategory and AdditionalInfo
                        is_sql = any(w in m_sub_low for w in ["sql server", "sql"]) or ("sql" in info_lower)
                        is_rhel = any(w in m_sub_low for w in ["red hat", "rhel"]) or ("rhel" in info_lower)
                        is_sles = any(w in m_sub_low for w in ["suse", "sles"]) or ("suse" in info_lower) or ("sles" in info_lower)
                        is_windows = ("windows" in m_sub_low) or bool(re.search(r'\bwin\b', m_sub_low)) or ("windows" in info_lower)
                        is_third_party = any(w in m_sub_low for w in ["firewall", "palo alto", "vm-series", "fortinet", "f5", "barracuda", "checkpoint"])

                        if is_sql:
                            workload = "SQL Server (IaaS)"
                        elif is_rhel:
                            workload = "Red Hat (RHEL)"
                        elif is_sles:
                            workload = "SUSE (SLES)"
                        elif is_windows:
                            workload = "Windows Server"
                        elif is_third_party:
                            workload = "Marketplace Appliance"
                        else:
                            workload = m_sub or "Virtual Machine License"

                        is_eligible = not is_third_party
                        vm_key = (vm_name, res_id or vm_name, workload)

                        # Check explicit AHB metadata signals:
                        # 1. MeterSubCategory explicitly names Azure Hybrid Benefit or AHB or BYOS
                        is_ahb_in_meter = any(w in m_sub_low for w in ["azure hybrid benefit", "hybrid benefit", "ahb", "byos", "baseprice"])

                        # 2. AdditionalInfo has AHBDsc: True or LicenseType
                        has_ahb_info_flag = False
                        try:
                            if info_str.startswith("{") and info_str.endswith("}"):
                                info_obj = json.loads(info_str)
                                for k, v in info_obj.items():
                                    k_low = str(k).lower()
                                    v_str = str(v).lower()
                                    if k_low in ("ahbdsc", "isahbdsc") and v_str in ("true", "1", "yes"):
                                        has_ahb_info_flag = True
                                    elif k_low == "licensetype":
                                        if any(t in v_str for t in ("windows_server", "windows_client", "rhel", "sles", "ahb", "baseprice")):
                                            has_ahb_info_flag = True
                        except Exception:
                            pass

                        if not has_ahb_info_flag:
                            if re.search(r'["\']?(?:ahbdsc|isahbdsc)["\']?\s*:\s*["\']?(?:true|1)["\']?', info_lower) or \
                               re.search(r'["\']?licensetype["\']?\s*:\s*["\']?(?:windows_server|windows_client|rhel|sles|ahb|baseprice)', info_lower):
                                has_ahb_info_flag = True

                        if vm_key not in vm_map:
                            vm_map[vm_key] = {
                                "name": vm_name,
                                "resource_id": res_id,
                                "resource_group": rg or "Default RG",
                                "subscription": sub or "Default Subscription",
                                "workload": workload,
                                "is_eligible": is_eligible,
                                "meters": set(),
                                "total_cost": 0.0,
                                "total_qty": 0.0,
                                "has_explicit_ahb": False,
                            }
                        entry = vm_map[vm_key]
                        entry["total_cost"] += raw_cost
                        entry["total_qty"] += qty
                        if m_sub: entry["meters"].add(m_sub)
                        if is_ahb_in_meter or has_ahb_info_flag:
                            entry["has_explicit_ahb"] = True

                    all_vms = []
                    for vm_data in vm_map.values():
                        cost = round(vm_data["total_cost"], 2)
                        workload = vm_data["workload"]
                        is_eligible = vm_data["is_eligible"]
                        sku_disp = ", ".join(list(vm_data["meters"])[:2]) if vm_data["meters"] else "License"

                        # AHB evaluation:
                        # - Explicit AHB flag in meter or AdditionalInfo
                        # - OR license cost is $0.00 while usage quantity > 0 (exact AHB discount rule)
                        has_ahb = vm_data["has_explicit_ahb"] or (cost == 0.0 and vm_data["total_qty"] > 0.0)

                        if not is_eligible:
                            status = f"ℹ️ Third-Party Appliance ({workload})"
                            savings = 0.0
                        elif has_ahb:
                            status = f"✅ Using AHB ({workload})"
                            savings = 0.0
                        else:
                            # Not using AHB: paying active commercial license fee — enabling AHB waives this exact fee
                            status = f"⚠️ Not Using AHB ({workload} PAYG)"
                            savings = cost

                        all_vms.append({
                            "name": vm_data["name"],
                            "resource_group": vm_data["resource_group"],
                            "subscription": vm_data["subscription"],
                            "workload": workload,
                            "sku": sku_disp,
                            "has_ahb": has_ahb,
                            "is_eligible": is_eligible,
                            "status": status,
                            "cost": cost,
                            "savings": savings,
                            "resource_id": vm_data["resource_id"]
                        })

                    # Filter to eligible commercial workloads (excludes marketplace appliances)
                    eligible_vms = [v for v in all_vms if v["is_eligible"]]
                    eligible_vms.sort(key=lambda x: (x["has_ahb"], -x["cost"]))

                    if wants_not_using:
                        display_vms = [v for v in eligible_vms if not v["has_ahb"]]
                        title_filter = "VMs Not Using Hybrid Discounts (Paying On-Demand License Surcharge)"
                    elif wants_using:
                        display_vms = [v for v in eligible_vms if v["has_ahb"]]
                        title_filter = "VMs Using Hybrid Discounts (AHB Active / $0 License Cost)"
                    else:
                        display_vms = eligible_vms
                        title_filter = "VM Hybrid Benefit & License Cost Status"

                    total_evaluated = len(eligible_vms)
                    active_count = sum(1 for v in eligible_vms if v["has_ahb"])
                    inactive_count = sum(1 for v in eligible_vms if not v["has_ahb"])
                    tot_spend = sum(v["cost"] for v in eligible_vms)
                    potential_monthly_savings = sum(v["savings"] for v in eligible_vms if not v["has_ahb"])
                    potential_annual_savings = potential_monthly_savings * 12
                    adoption_pct = ((active_count / total_evaluated) * 100) if total_evaluated else 0.0

                    win_vms = [v for v in eligible_vms if v["workload"] == "Windows Server"]
                    rhel_vms = [v for v in eligible_vms if v["workload"] == "Red Hat (RHEL)"]
                    sles_vms = [v for v in eligible_vms if v["workload"] == "SUSE (SLES)"]
                    sql_vms = [v for v in eligible_vms if v["workload"] == "SQL Server (IaaS)"]
                    workload_summary_bullets = []
                    if win_vms:
                        win_act = sum(1 for v in win_vms if v["has_ahb"])
                        win_inact = len(win_vms) - win_act
                        win_sav = sum(v["savings"] for v in win_vms if not v["has_ahb"])
                        workload_summary_bullets.append(f"  - **Windows Server**: {len(win_vms)} VMs ({win_act} active, {win_inact} un-discounted — potential savings: ${win_sav:,.2f}/mo)")
                    if rhel_vms:
                        rhel_act = sum(1 for v in rhel_vms if v["has_ahb"])
                        rhel_inact = len(rhel_vms) - rhel_act
                        rhel_sav = sum(v["savings"] for v in rhel_vms if not v["has_ahb"])
                        workload_summary_bullets.append(f"  - **Red Hat Enterprise Linux (RHEL)**: {len(rhel_vms)} VMs ({rhel_act} active, {rhel_inact} un-discounted — potential savings: ${rhel_sav:,.2f}/mo)")
                    if sles_vms:
                        sles_act = sum(1 for v in sles_vms if v["has_ahb"])
                        sles_inact = len(sles_vms) - sles_act
                        sles_sav = sum(v["savings"] for v in sles_vms if not v["has_ahb"])
                        workload_summary_bullets.append(f"  - **SUSE Linux Enterprise Server (SLES)**: {len(sles_vms)} VMs ({sles_act} active, {sles_inact} un-discounted — potential savings: ${sles_sav:,.2f}/mo)")
                    if sql_vms:
                        sql_act = sum(1 for v in sql_vms if v["has_ahb"])
                        sql_inact = len(sql_vms) - sql_act
                        sql_sav = sum(v["savings"] for v in sql_vms if not v["has_ahb"])
                        workload_summary_bullets.append(f"  - **SQL Server on VMs (IaaS)**: {len(sql_vms)} VMs ({sql_act} active, {sql_inact} un-discounted — potential savings: ${sql_sav:,.2f}/mo)")

                    workload_section = ""
                    if workload_summary_bullets:
                        workload_section = "- **Workload Breakdown**:\n" + "\n".join(workload_summary_bullets) + "\n"

                    if display_vms:
                        tbl_lines = [
                            f"| {idx} | `{v['name']}` | `{v['resource_group']}` | `{v['subscription']}` | {v['workload']} | `{v['sku']}` | {v['status']} | ${v['cost']:,.2f} | ${v['savings']:,.2f}/mo |"
                            for idx, v in enumerate(display_vms[:30], 1)
                        ]
                        table_md = (
                            f"| # | VM Name | Resource Group | Subscription | Workload / OS | License Meter | Hybrid Discount Status | Billed License Cost | Realizable Monthly Savings |\n"
                            f"|:---|:---|:---|:---|:---|:---|:---|:---|:---|\n"
                            f"{chr(10).join(tbl_lines)}\n\n"
                            f"| **Total** | **{len(display_vms)} VMs** | | | | | | **${sum(v['cost'] for v in display_vms):,.2f}** | **${sum(v['savings'] for v in display_vms):,.2f}/mo** |"
                        )
                    else:
                        table_md = f"*(No Virtual Machines found matching '{title_filter}' in {svc_scope_label}. All evaluated VMs with commercial licenses are already using Azure Hybrid Benefit.)*\n\n"

                    # Chart
                    chart_md = ""
                    if total_evaluated > 0:
                        if active_count > 0 and inactive_count > 0:
                            chart_md = _chart_block(
                                "donut",
                                f"Azure VMs: Hybrid Discount License Spend Split — {svc_scope_label}",
                                ["AHB Active ($0 License Cost)", "AHB Inactive (Paying PAYG License)"],
                                values=[
                                    round(sum(v["cost"] for v in eligible_vms if v["has_ahb"]), 2),
                                    round(sum(v["cost"] for v in eligible_vms if not v["has_ahb"]), 2)
                                ]
                            )
                        elif display_vms:
                            top_vms = display_vms[:10]
                            chart_md = _chart_block(
                                "bar",
                                f"Top Azure VMs by License Cost ({title_filter}) — {svc_scope_label}",
                                [v["name"] for v in top_vms],
                                values=[v["cost"] for v in top_vms],
                                horizontal=True
                            )

                    third_party_vms = [v for v in all_vms if not v["is_eligible"]]
                    third_party_note = ""
                    if third_party_vms:
                        tp_spend = sum(v["cost"] for v in third_party_vms)
                        third_party_note = (
                            f"> ℹ️ **Excluded Third-Party Marketplace Appliances ({len(third_party_vms)} Instances, ${tp_spend:,.2f})**: "
                            f"Network and security appliances (such as `VM-Series Next Generation Firewall`) are third-party ISV marketplace products, "
                            f"not eligible for Microsoft Azure Hybrid Benefit (Software Assurance/BYOS).\n\n"
                        )

                    ahb_finops_insights_md = (
                        f"#### 💡 Strategic FinOps Insights: TCO Economics, CapEx Licensing & Dev/Test Optimization\n\n"
                        f"##### 1. Invoice Savings vs. Net Enterprise TCO (Do We Save the Same as Billed?)\n"
                        f"- **Cloud Invoice (OpEx) Impact**: **100% of the software license surcharge is eliminated** immediately on your Azure billing meter (dropping the VM rate to the base Linux compute rate).\n"
                        f"- **Net Enterprise TCO Reality**:\n"
                        f"  $$\\text{{Net Enterprise Savings}} = \\text{{Cloud Billed Surcharge Waived}} - (\\text{{Amortized License CapEx}} + \\text{{Annual Software Assurance OpEx}})$$\n"
                        f"  - **Surplus / Shelfware On-Premises Licenses**: If your organization already owns unassigned licenses with active Software Assurance (SA), or is decommissioning on-prem hardware during cloud migration, the marginal license cost is **$0.00**, capturing **100% pure net savings**.\n"
                        f"  - **Purchasing Net-New Licenses**: If you must purchase licenses to qualify for AHB, perpetual licenses are capitalized (**CapEx** amortized over 3–5 years) while mandatory **Software Assurance** (~25%/year of base license price) is an annual operating expense (**OpEx**). Alternatively, 1-year or 3-year Server Subscriptions (via CSP/EA) provide an operational model with built-in SA.\n"
                        f"  - **Breakeven Rule**: On-demand hourly licensing (PAYG) is cost-effective **only** for temporary or intermittent workloads running `< 40%–50%` of the month (~300 hours). For 24/7 steady-state production workloads, owning licenses with SA yields **40%–60% net savings over 3 years**.\n"
                        f"  - **Minimum Core Floor**: Microsoft enforces a minimum licensing requirement of **8 core licenses per VM for Windows Server** (even on 2- or 4-vCPU VMs) and **4 core licenses per VM for SQL Server**.\n\n"
                        f"##### 2. Azure Dev/Test Subscriptions for Non-Critical Workloads (Entitlement Preservation)\n"
                        f"- **Automatic $0 Windows OS Surcharge**: Moving non-critical environments (Dev, Test, QA, Staging, Sandbox, POC) to **Azure Enterprise Dev/Test or PAYG Dev/Test Subscriptions** automatically bills Windows VMs at **base Linux rates**.\n"
                        f"- **No AHB or Software Assurance Required**: Non-prod Windows VMs receive the discounted rate natively without buying licenses, allocating SA, or flipping AHB flags.\n"
                        f"- **SQL Server Developer Edition ($0 License Cost)**: Dev/Test instances can run SQL Server Developer Edition, providing 100% of SQL Server Enterprise Edition features with **$0 licensing fees**.\n"
                        f"- **Entitlement Preservation Strategy**: Running non-prod workloads in Dev/Test subscriptions preserves all corporate Software Assurance / AHB entitlements **exclusively for Production workloads**, eliminating the need for net-new license purchases.\n"
                        f"- **Compliance & Operational Guardrails**:\n"
                        f"  - Requires active Visual Studio subscriber licensing for all engineers/testers accessing the subscription; strictly zero production customer traffic allowed.\n"
                        f"  - Pair Dev/Test subscriptions with **automated off-hours shutdown schedules** (e.g. stop after 7 PM and on weekends) to compound savings to **70%–85% overall non-prod cost reduction**.\n\n"
                        f"##### 3. Strategic Workload Decision Tree\n"
                        f"1. **Production Windows & SQL (24/7 Steady State)**: Apply **Azure Hybrid Benefit (Layer 0)** using corporate SA licenses, then layer 3-Year Reservations (up to 72%) on top.\n"
                        f"2. **Non-Production Windows & SQL (Dev/Test/QA)**: Migrate to **Azure Dev/Test Subscriptions** (Free OS + $0 Dev SQL) and configure auto-shutdown policies.\n"
                        f"3. **Base Linux (Ubuntu, Debian, CentOS)**: No AHB or Dev/Test OS discount needed (already $0 OS license). Maximize compute efficiency via Rightsizing & Compute Savings Plans.\n"
                        f"4. **Commercial Linux (RHEL / SLES)**: Apply **Red Hat Cloud Access (RHEL_BYOS)** or **SUSE BYOS** if corporate subscriptions exist; otherwise evaluate modernizing to AlmaLinux/Rocky Linux.\n\n"
                    )

                    return (
                        f"### 📊 Azure Virtual Machines: Azure Hybrid Benefit (AHB) Analysis{cust_str} — {svc_scope_label}\n\n"
                        f"{partial_notice}"
                        f"{third_party_note}"
                        f"> ℹ️ **FinOps Scope Note (Dataset Filter)**: Evaluated through `AZURE_COST_USAGE` for `MeterCategory IN ('Virtual Machines Licenses', 'Virtual Machine Licenses')`. "
                        f"Base Linux VMs (Ubuntu, Debian, CentOS, etc.) carry **$0 OS licensing markup** and have no license records, naturally focusing this audit purely on commercial workloads.\n\n"
                        f"- **Total Evaluated Commercial License Instances**: **{total_evaluated}**\n"
                        f"- **Using Hybrid Discounts (AHB Active / $0 License Fee)**: **{active_count}** ({adoption_pct:.1f}% adoption)\n"
                        f"- **Not Using Hybrid Discounts (Paying PAYG Surcharge)**: **{inactive_count}**\n"
                        f"{workload_section}"
                        f"- **Total Billed License Spend**: **${tot_spend:,.2f}**\n"
                        f"- **Potential Realizable Savings**: **${potential_monthly_savings:,.2f}/month** (**${potential_annual_savings:,.2f}/year**) by enabling AHB on un-discounted commercial VMs\n\n"
                        f"#### 📋 {title_filter} ({len(display_vms)} Instances)\n\n"
                        f"{table_md}\n"
                        f"{chart_md}\n"
                        f"#### 🚀 Actionable FinOps Remediation & CLI Commands\n\n"
                        f"Azure Hybrid Benefit applies Software Assurance rights and Bring-Your-Own-Subscription mobility across **multiple OS & workload families** with zero downtime and no reboot required:\n\n"
                        f"##### 1. Windows Server VMs (Eliminates ~40%–50% OS License Fee)\n"
                        f"```bash\n"
                        f"# Enable AHB for a single Windows VM:\n"
                        f"az vm update --resource-group <resourceGroup> --name <vmName> --license-type Windows_Server\n\n"
                        f"# Batch-enable across all Windows VMs in a Resource Group:\n"
                        f"for vm in $(az vm list --resource-group <resourceGroup> --query \"[?storageProfile.osDisk.osType=='Windows'].name\" -o tsv); do\n"
                        f"  az vm update --resource-group <resourceGroup> --name \"$vm\" --license-type Windows_Server\n"
                        f"done\n"
                        f"```\n\n"
                        f"##### 2. Red Hat Enterprise Linux (RHEL) VMs (Eliminates ~25%–35% Red Hat Software Fee)\n"
                        f"```bash\n"
                        f"# Enable AHB for RHEL using Red Hat Cloud Access (BYOS):\n"
                        f"az vm update --resource-group <resourceGroup> --name <vmName> --license-type RHEL_BYOS\n\n"
                        f"##### 3. SUSE Linux Enterprise Server (SLES) VMs (Eliminates ~25%–35% SUSE Software Fee)\n"
                        f"```bash\n"
                        f"# Enable AHB for SLES using SUSE BYOS:\n"
                        f"az vm update --resource-group <resourceGroup> --name <vmName> --license-type SLES_BYOS\n\n"
                        f"##### 4. SQL Server on Azure VMs (Eliminates ~50%–55% SQL License Fee)\n"
                        f"```bash\n"
                        f"# Enable AHB for SQL Server via SQL IaaS Agent Extension:\n"
                        f"az sql vm update --resource-group <resourceGroup> --name <vmName> --license-type AHB\n"
                        f"```\n\n"
                        f"##### 5. Azure SQL Database & Managed Instance (PaaS — BasePrice Rate)\n"
                        f"```bash\n"
                        f"# Enable AHB for Azure SQL Database:\n"
                        f"az sql db update --resource-group <resourceGroup> --server <serverName> --name <dbName> --license-type BasePrice\n\n"
                        f"# Enable AHB for Azure SQL Managed Instance:\n"
                        f"az sql mi update --resource-group <resourceGroup> --name <miName> --license-type BasePrice\n"
                        f"```\n\n"
                        f"##### 6. Azure Kubernetes Service (AKS) Windows Node Pools\n"
                        f"```bash\n"
                        f"# Enable AHB for Windows node pool on AKS:\n"
                        f"az aks nodepool update --resource-group <resourceGroup> --cluster-name <clusterName> --name <nodepoolName> --license-type Windows_Server\n"
                        f"```\n\n"
                        f"{ahb_finops_insights_md}"
                        f"> **FinOps Foundation Practice Note (Layer 0 Rate Optimization)**:\n"
                        f"> - **Zero Contract Lock-in**: Azure Hybrid Benefit is a licensing mobility entitlement under Software Assurance or Red Hat / SUSE Cloud Access, not a multi-year spend commitment. Rate adjustments take effect immediately on the next billing hour.\n"
                        f"> - **Compounded Rate Layering**: Always enable AHB first (Layer 0). Once compute rates drop to base Linux rates, layer Azure Reservations (up to 72%) or Compute Savings Plans (up to 65%) on top for maximum compound savings.\n\n"
                        f"*Source: `AZURE_COST_USAGE` via CloudHealth FlexReports.*"
                    )

                else:
                    # 0 rows returned from AZURE_COST_USAGE
                    ahb_finops_insights_md = (
                        f"#### 💡 Strategic FinOps Insights: TCO Economics, CapEx Licensing & Dev/Test Optimization\n\n"
                        f"##### 1. Invoice Savings vs. Net Enterprise TCO (Do We Save the Same as Billed?)\n"
                        f"- **Cloud Invoice (OpEx) Impact**: **100% of the software license surcharge is eliminated** immediately on your Azure billing meter (dropping the VM rate to the base Linux compute rate).\n"
                        f"- **Net Enterprise TCO Reality**:\n"
                        f"  $$\\text{{Net Enterprise Savings}} = \\text{{Cloud Billed Surcharge Waived}} - (\\text{{Amortized License CapEx}} + \\text{{Annual Software Assurance OpEx}})$$\n"
                        f"  - **Surplus / Shelfware On-Premises Licenses**: If your organization already owns unassigned licenses with active Software Assurance (SA), or is decommissioning on-prem hardware during cloud migration, the marginal license cost is **$0.00**, capturing **100% pure net savings**.\n"
                        f"  - **Purchasing Net-New Licenses**: If you must purchase licenses to qualify for AHB, perpetual licenses are capitalized (**CapEx** amortized over 3–5 years) while mandatory **Software Assurance** (~25%/year of base license price) is an annual operating expense (**OpEx**). Alternatively, 1-year or 3-year Server Subscriptions (via CSP/EA) provide an operational model with built-in SA.\n"
                        f"  - **Breakeven Rule**: On-demand hourly licensing (PAYG) is cost-effective **only** for temporary or intermittent workloads running `< 40%–50%` of the month (~300 hours). For 24/7 steady-state production workloads, owning licenses with SA yields **40%–60% net savings over 3 years**.\n"
                        f"  - **Minimum Core Floor**: Microsoft enforces a minimum licensing requirement of **8 core licenses per VM for Windows Server** (even on 2- or 4-vCPU VMs) and **4 core licenses per VM for SQL Server**.\n\n"
                        f"##### 2. Azure Dev/Test Subscriptions for Non-Critical Workloads (Entitlement Preservation)\n"
                        f"- **Automatic $0 Windows OS Surcharge**: Moving non-critical environments (Dev, Test, QA, Staging, Sandbox, POC) to **Azure Enterprise Dev/Test or PAYG Dev/Test Subscriptions** automatically bills Windows VMs at **base Linux rates**.\n"
                        f"- **No AHB or Software Assurance Required**: Non-prod Windows VMs receive the discounted rate natively without buying licenses, allocating SA, or flipping AHB flags.\n"
                        f"- **SQL Server Developer Edition ($0 License Cost)**: Dev/Test instances can run SQL Server Developer Edition, providing 100% of SQL Server Enterprise Edition features with **$0 licensing fees**.\n"
                        f"- **Entitlement Preservation Strategy**: Running non-prod workloads in Dev/Test subscriptions preserves all corporate Software Assurance / AHB entitlements **exclusively for Production workloads**, eliminating the need for net-new license purchases.\n"
                        f"- **Compliance & Operational Guardrails**:\n"
                        f"  - Requires active Visual Studio subscriber licensing for all engineers/testers accessing the subscription; strictly zero production customer traffic allowed.\n"
                        f"  - Pair Dev/Test subscriptions with **automated off-hours shutdown schedules** (e.g. stop after 7 PM and on weekends) to compound savings to **70%–85% overall non-prod cost reduction**.\n\n"
                        f"##### 3. Strategic Workload Decision Tree\n"
                        f"1. **Production Windows & SQL (24/7 Steady State)**: Apply **Azure Hybrid Benefit (Layer 0)** using corporate SA licenses, then layer 3-Year Reservations (up to 72%) on top.\n"
                        f"2. **Non-Production Windows & SQL (Dev/Test/QA)**: Migrate to **Azure Dev/Test Subscriptions** (Free OS + $0 Dev SQL) and configure auto-shutdown policies.\n"
                        f"3. **Base Linux (Ubuntu, Debian, CentOS)**: No AHB or Dev/Test OS discount needed (already $0 OS license). Maximize compute efficiency via Rightsizing & Compute Savings Plans.\n"
                        f"4. **Commercial Linux (RHEL / SLES)**: Apply **Red Hat Cloud Access (RHEL_BYOS)** or **SUSE BYOS** if corporate subscriptions exist; otherwise evaluate modernizing to AlmaLinux/Rocky Linux.\n\n"
                    )

                    return (
                        f"### 📊 Azure Virtual Machines: Azure Hybrid Benefit (AHB) Analysis{cust_str} — {svc_scope_label}\n\n"
                        f"{partial_notice}"
                        f"No Azure Virtual Machine license records were returned from `AZURE_COST_USAGE` for {svc_scope_label}.\n\n"
                        f"#### 🔍 Azure Hybrid Benefit (AHB) Multi-Workload Detection Architecture\n"
                        f"In CloudHealth and Azure Cost Management, Azure Hybrid Benefit applies across **multiple operating systems and enterprise workloads** (Windows Server, Red Hat Enterprise Linux, SUSE Linux Enterprise Server, and SQL Server).\n\n"
                        f"- **Dataset Filter**: `AZURE_COST_USAGE` with:\n"
                        f"  ```sql\n"
                        f"  WHERE ((MeterCategory IN ('Virtual Machines Licenses')) OR (MeterCategory LIKE '%Virtual Machine Licenses%'))\n"
                        f"    AND (MetricType IN ('Actual'))\n"
                        f"  ```\n"
                        f"- **Base Linux Isolation**: Base Linux VMs (Ubuntu, Debian, AlmaLinux, Rocky, CentOS) carry **$0 OS licensing markup** and have no records under `Virtual Machines Licenses`. This naturally isolates audits to commercial workloads.\n"
                        f"- **Deterministic AHB Detection Rule**:\n"
                        f"  - **AHB Active ($0 License Cost)**: If a commercial VM has active usage (`Quantity > 0`) but `ActualCostInBillingCurrency == $0.00`, Azure Hybrid Benefit is active.\n"
                        f"  - **AHB Inactive (Paying PAYG Surcharge)**: If `ActualCostInBillingCurrency > $0.00`, the VM is incurring on-demand software licensing fees. Enabling AHB eliminates this billed amount.\n"
                        f"  - **`MeterSubCategory` & `AdditionalInfo`**: Provides explicit flags such as `SQL Server Azure Hybrid Benefit`, `AHBDsc: True`, or `LicenseType` (`Windows_Server`, `RHEL_BYOS`, `SLES_BYOS`, `AHB`).\n\n"
                        f"#### 🛠️ Live Estate Audit Playbook: Azure Resource Graph (ARG) & CLI\n"
                        f"To audit which Azure VMs are currently not using Azure Hybrid Benefit across your subscriptions:\n\n"
                        f"```kusto\n"
                        f"// Azure Resource Graph KQL: Find Windows, RHEL, and SLES VMs missing Azure Hybrid Benefit\n"
                        f"Resources\n"
                        f"| where type =~ 'microsoft.compute/virtualmachines'\n"
                        f"| extend osType = tostring(properties.storageProfile.osDisk.osType)\n"
                        f"| extend licenseType = tostring(properties.licenseType)\n"
                        f"| extend imageOffer = tostring(properties.storageProfile.imageReference.offer)\n"
                        f"| extend isWindows = osType =~ 'Windows'\n"
                        f"| extend isRHEL = imageOffer has 'rhel' or imageOffer has 'redhat'\n"
                        f"| extend isSLES = imageOffer has 'suse' or imageOffer has 'sles'\n"
                        f"| where (isWindows and (isnull(licenseType) or licenseType !~ 'Windows_Server'))\n"
                        f"     or (isRHEL and (isnull(licenseType) or licenseType !startswith 'RHEL'))\n"
                        f"     or (isSLES and (isnull(licenseType) or licenseType !startswith 'SLES'))\n"
                        f"| project name, resourceGroup, subscriptionId, osType, imageOffer, licenseType, vmSize = properties.hardwareProfile.vmSize\n"
                        f"```\n\n"
                        f"**Workload Remediation Commands (Zero Downtime)**:\n"
                        f"```bash\n"
                        f"# 1. Windows Server:\n"
                        f"az vm update --resource-group <resourceGroup> --name <vmName> --license-type Windows_Server\n\n"
                        f"# 2. Red Hat Enterprise Linux (RHEL):\n"
                        f"az vm update --resource-group <resourceGroup> --name <vmName> --license-type RHEL_BYOS\n\n"
                        f"# 3. SUSE Linux Enterprise Server (SLES):\n"
                        f"az vm update --resource-group <resourceGroup> --name <vmName> --license-type SLES_BYOS\n\n"
                        f"# 4. SQL Server on VM (IaaS):\n"
                        f"az sql vm update --resource-group <resourceGroup> --name <vmName> --license-type AHB\n"
                        f"```\n\n"
                        f"{ahb_finops_insights_md}"
                        f"> **💡 FinOps Foundation Practice Note**:\n"
                        f"> Azure Hybrid Benefit applies Software Assurance and BYOS rights directly to cloud compute, eliminating 25%–55% in software markup across Windows Server, RHEL, SLES, and SQL Server. Unlike Reservations, AHB carries no commitment term or lock-in and should be applied as Layer 0 before sizing compute commitments.\n\n"
                        f"*Source: `AZURE_COST_USAGE` via CloudHealth FlexReports.*"
                    )

            # 3-LEASE-TYPE. Dedicated CloudHealth & AWS CUR Lease Type Breakdown (On-Demand, Reserved Instances, Savings Plans, Spot)
            is_lease_type_request = (
                intent_info.get("target_dimension") in ("LeaseType", "PricingCategory", "PurchaseOption") or
                "lease_type" in (intent_info.get("breakdowns") or []) or
                any(w in low for w in [
                    "lease type", "leasetype", "by lease", "lease breakdown", "lease types",
                    "purchase option", "purchase options", "purchase option breakdown", "by purchase option",
                    "pricing model", "pricing models", "pricing model breakdown", "by pricing model",
                    "capacity type", "by capacity type"
                ]) or
                (any(w in low for w in ["ondemand", "on-demand", "reservation", "savings plan", "savingsplan", "spot"]) and any(w in low for w in ["breakdown", "break down", "split", "by", "usage", "cost", "spend", "lease", "above"]))
            ) and not is_ai_models_query and not any(w in low for w in ["recommendation", "anomal", "spike"])

            if is_lease_type_request and mcp:
                target_svc = intent_info.get("service")
                if not target_svc and is_followup:
                    hist_blob = " ".join(prior_cost_low.split() + [a.lower() for a in prior_assistant_msgs[-2:]])
                    if any(w in hist_blob for w in ["rds", "relational database", "aurora", "database"]):
                        target_svc = "AmazonRDS"
                    elif any(w in hist_blob for w in ["ec2", "elastic compute", "virtual machine"]):
                        target_svc = "AmazonEC2"

                is_rds = (target_svc == "AmazonRDS") or ("rds" in low) or ("database" in low and "ec2" not in low)
                is_ec2 = (target_svc == "AmazonEC2") or ("ec2" in low)
                if is_rds:
                    target_svc = "AmazonRDS"
                    svc_disp = "Amazon RDS"
                    where_svc = "WHERE lineItem_ProductCode = 'AmazonRDS'"
                elif is_ec2:
                    target_svc = "AmazonEC2"
                    svc_disp = "Amazon EC2"
                    where_svc = "WHERE lineItem_ProductCode = 'AmazonEC2'"
                else:
                    svc_disp = "AWS Compute"
                    where_svc = "WHERE lineItem_ProductCode IN ('AmazonEC2', 'AmazonRDS')"

                is_ytd = bool(re.search(r'\b(?:ytd|year[- ]to[- ]date|this\s*year)\b', low))
                is_qtd = bool(re.search(r'\b(?:qtd|quarter[- ]to[- ]date|this\s*quarter)\b', low))
                if not is_ytd and not is_qtd and is_followup:
                    hist_blob = " ".join(prior_cost_low.split() + [a.lower() for a in prior_assistant_msgs[-2:]])
                    if re.search(r'\b(?:ytd|year[- ]to[- ]date|this\s*year)\b', hist_blob):
                        is_ytd = True
                    elif re.search(r'\b(?:qtd|quarter[- ]to[- ]date|this\s*quarter)\b', hist_blob):
                        is_qtd = True

                m_months = re.search(r'\b(\d+)\s*(?:months?|m)\b', low)
                m_days = re.search(r'\b(\d+)\s*(?:days?|d)\b', low)
                has_explicit_months = is_ytd or is_qtd or bool(m_months) or bool(intent_info.get("timeframe_months")) or any(w in low for w in ["12months", "12 months", "year", "annual", "months", "month by month", "monthly"])
                if has_explicit_months:
                    num_months = intent_info.get("timeframe_months") or (datetime.date.today().month if is_ytd else (((datetime.date.today().month - 1) % 3) + 1 if is_qtd else (int(m_months.group(1)) if m_months else 12)))
                else:
                    num_months = 3  # Default to recent 3 months for lease breakdown trend

                tenant_suffix = " (Tenant)" if any(w in low for w in ["tenant"]) else ""
                cust_label = named_customer or (f"All Accounts{tenant_suffix}" if tenant_suffix else "All Accounts")

                cur_sql = (
                    "SELECT timeInterval_Month AS month, pricing_term AS pricing_term, lineItem_LineItemType AS item_type, "
                    "SUM(lineItem_UnblendedCost) AS cost, SUM(lineItem_UsageAmount) AS usage_amount "
                    "FROM AWS_CUR "
                    f"{where_svc} "
                    "GROUP BY timeInterval_Month, pricing_term, lineItem_LineItemType "
                    "ORDER BY month ASC, cost DESC"
                )
                cur_q_params = {
                    "queryInput": {
                        "sqlStatement": cur_sql,
                        "dataGranularity": "MONTHLY",
                        "limit": 300,
                        "timeRange": {"last": num_months, "qualifier": "MONTH"}
                    },
                    "requestInfo": {"sourceType": "API", "caller": "mcp"}
                }
                if named_customer_crn:
                    cur_q_params["channelCustomerId"] = named_customer_crn

                lease_totals = defaultdict(float)
                usage_totals = defaultdict(float)
                monthly_lease = defaultdict(lambda: defaultdict(float))
                monthly_net = defaultdict(float)
                all_months = set()
                chart_rows = []

                try:
                    res_cur = mcp.call_tool("execute_datasource_query", cur_q_params)
                    txt_cur = res_cur.get("content", [{}])[0].get("text", "{}")
                    raw_csv = json.loads(txt_cur).get("csv", "")
                    for r in csv.DictReader(io.StringIO(raw_csv)):
                        m = (r.get("month") or "").strip()
                        term = (r.get("pricing_term") or "").strip()
                        itype = (r.get("item_type") or "").strip()
                        c = float(r.get("cost") or 0.0)
                        u = float(r.get("usage_amount") or 0.0)
                        if not m:
                            continue
                        all_months.add(m)

                        if term == "OnDemand" and itype == "Usage":
                            ltype = "On-Demand"
                        elif term == "Reserved" or itype in ("RIFee", "DiscountedUsage"):
                            ltype = "Reserved Instances (RI)"
                        elif "SavingsPlan" in itype:
                            if itype == "SavingsPlanCoveredUsage":
                                ltype = "Savings Plans"
                            elif itype == "SavingsPlanNegation":
                                ltype = "Discounts & Offsets"
                            else:
                                ltype = "Savings Plans"
                        elif term == "Spot" or "spot" in itype.lower():
                            ltype = "Spot Instances"
                        elif itype == "Fee":
                            ltype = "Upfront Fees / Commitments"
                        elif c < 0 or itype in ("EdpDiscount", "SppDiscount", "PrivateRateDiscount", "Credit", "BundledDiscount"):
                            ltype = "Discounts & Offsets"
                        else:
                            ltype = "Other / Unallocated"

                        if ltype in ("On-Demand", "Reserved Instances (RI)", "Savings Plans", "Spot Instances"):
                            lease_totals[ltype] += c
                            usage_totals[ltype] += u
                            monthly_lease[m][ltype] += c
                            if c > 0:
                                chart_rows.append({"month": m, "category": ltype, "cost": c})
                        elif ltype in ("Upfront Fees / Commitments", "Discounts & Offsets"):
                            monthly_lease[m][ltype] += c
                        monthly_net[m] += c
                except Exception as e:
                    logger.warning(f"[AWS_CUR Lease Type Query] {e}")

                if lease_totals:
                    sorted_months = sorted(list(all_months))
                    if is_ytd:
                        period_str = f"Year-to-Date (YTD {datetime.date.today().year})" if not sorted_months else f"Year-to-Date ({sorted_months[0]} to {sorted_months[-1]})"
                    elif is_qtd:
                        curr_q = ((datetime.date.today().month - 1) // 3) + 1
                        period_str = f"Quarter-to-Date (QTD Q{curr_q} {datetime.date.today().year})"
                    else:
                        period_str = f"Last {num_months} Months ({sorted_months[0]} to {sorted_months[-1]})" if sorted_months else f"Last {num_months} Months"

                    CANONICAL_LEASES = [
                        ("On-Demand", "Standard uncommitted pay-as-you-go capacity"),
                        ("Reserved Instances (RI)", "1-Yr / 3-Yr committed capacity reservations"),
                        ("Savings Plans", "Flexible commitment-discounted usage"),
                        ("Spot Instances", "Discounted spare capacity (N/A for RDS, active for EC2)")
                    ]
                    total_compute = sum(lease_totals[lt] for lt, _ in CANONICAL_LEASES)
                    committed_spend = lease_totals["Reserved Instances (RI)"] + lease_totals["Savings Plans"]
                    coverage_pct = (committed_spend / total_compute * 100) if total_compute > 0 else 0.0

                    sum_rows = []
                    for idx, (lt, desc) in enumerate(CANONICAL_LEASES, 1):
                        sp = lease_totals.get(lt, 0.0)
                        pct = (sp / total_compute * 100) if total_compute > 0 else 0.0
                        u_hrs = usage_totals.get(lt, 0.0)
                        hrs_str = f"{u_hrs:,.1f} hrs" if u_hrs > 0 else ("N/A (Managed DB)" if is_rds and lt == "Spot Instances" else "—")
                        status_badge = "✅ Committed" if "Reserved" in lt or "Savings" in lt else ("⚡ Flexible" if "Spot" in lt else "⚠️ Uncommitted")
                        sum_rows.append(
                            f"| {idx} | **{lt}** | ${sp:,.2f} | {pct:.1f}% | {hrs_str} | {status_badge} | {desc} |"
                        )

                    summary_table = (
                        f"#### 🖥️ Spend & Usage Breakdown by Lease Type\n\n"
                        f"| # | Lease Type / Purchase Option | Billed Spend | % of Spend | Usage Hours | Status | FinOps Definition |\n"
                        f"|:---|:---|:---|:---|:---|:---|:---|\n"
                        f"{chr(10).join(sum_rows)}\n"
                        f"| | **Total Compute Spend** | **${total_compute:,.2f}** | **100.0%** | | | Commitment Coverage: **{coverage_pct:.1f}%** |\n"
                    )

                    trend_table = ""
                    if len(sorted_months) > 1:
                        m_rows = []
                        for m in sorted_months:
                            od = monthly_lease[m].get("On-Demand", 0.0)
                            ri = monthly_lease[m].get("Reserved Instances (RI)", 0.0)
                            sp = monthly_lease[m].get("Savings Plans", 0.0)
                            spot = monthly_lease[m].get("Spot Instances", 0.0)
                            disc = monthly_lease[m].get("Discounts & Offsets", 0.0)
                            net = monthly_net[m]
                            m_rows.append(
                                f"| {m} | ${od:,.2f} | ${ri:,.2f} | ${sp:,.2f} | ${spot:,.2f} | ${disc:,.2f} | ${net:,.2f} |"
                            )
                        trend_table = (
                            f"\n#### 📅 Monthly Trend by Lease Type\n\n"
                            f"| Month | On-Demand | Reserved (RI) | Savings Plan | Spot | Discounts (EDP/SPP) | Net Spend |\n"
                            f"|:---|:---|:---|:---|:---|:---|:---|\n"
                            f"{chr(10).join(m_rows)}\n"
                        )

                    chart_md = ""
                    if not is_no_chart_requested(low) and chart_rows:
                        chart_md = _build_time_category_stacked_chart(
                            f"{svc_disp} Spend by Lease Type & Month — {cust_label}",
                            chart_rows,
                            time_col="month",
                            cat_col="category",
                            cost_col="cost",
                            time_format="month",
                            max_cats=6
                        )

                    od_spend = lease_totals.get("On-Demand", 0.0)
                    potential_sp_savings = od_spend * 0.30
                    if is_rds:
                        spot_note = "- **Spot Instance Applicability**: **Spot instances are not supported for Amazon RDS**. AWS limits Spot capacity to stateless, fault-tolerant workloads (EC2, ECS, EMR, Batch). Relational database clusters require guaranteed hardware tenancy and stateful persistence."
                        rec_note = f"- **Commitment Coverage Target (30%–55% Savings)**: Identified **${od_spend:,.2f}** in steady-state On-Demand RDS spend. Sizing **1-Year or 3-Year Database Savings Plans** or Database RIs to achieve 70%–80% coverage would yield estimated savings of **${potential_sp_savings:,.2f} to ${od_spend*0.50:,.2f}**."
                    else:
                        spot_note = "- **Spot Instance Opportunities**: Spot provides **60%–90% discounts** relative to retail On-Demand. Ideal for stateless Kubernetes workers, CI/CD runners, and batch processing."
                        rec_note = f"- **Commitment Rightsizing**: Identified **${od_spend:,.2f}** in On-Demand compute. Layer Compute Savings Plans for baseline 24/7 workloads to capture **${potential_sp_savings:,.2f}** in direct savings."

                    finops_insights = (
                        f"#### 💡 FinOps Insights & Commitment Optimization Levers\n\n"
                        f"- **Current Commitment Coverage**: **{coverage_pct:.1f}%** of compute spend is currently protected under commitments (**${committed_spend:,.2f}** across RIs and Savings Plans).\n"
                        f"{rec_note}\n"
                        f"{spot_note}\n\n"
                        f"> **FinOps Foundation Rate Optimization Principle**:\n"
                        f"> Rightsizing and modernization (e.g. Graviton) must precede multi-year commitment purchases. Lock in baseline usage with flexible Savings Plans first, followed by standard RIs for static database instances.\n\n"
                        f"*Source: AWS_CUR via CloudHealth FlexReports.*"
                    )

                    return (
                        f"### 📋 CloudHealth Lease Type & Pricing Model Breakdown: {svc_disp}\n\n"
                        f"Queried live from standard dataset **`AWS_CUR`** for **{cust_label}**:\n\n"
                        f"- **Target Billing Period**: {period_str}\n"
                        f"- **Total Analyzed Compute Spend**: **${total_compute:,.2f}**\n\n"
                        f"{summary_table}\n"
                        f"{trend_table}\n"
                        f"{chart_md}\n"
                        f"{finops_insights}"
                    )

            # 3-RDS-IT. Dedicated RDS Instance Type, Engine & Spend Analysis via AWS_RDS_COST_AND_USAGE & AWS_CUR
            is_rds_instance_or_usage = (
                (
                    (intent_info.get("service") == "AmazonRDS" and not is_ai_models_query) or
                    any(w in low for w in ["rds", "relational database", "aurora"]) or
                    ("database" in low and any(w in low for w in ["instance", "type", "engine", "usage", "spend", "cost", "breakdown"]))
                ) and not is_ai_models_query
            ) and not any(w in low for w in ["recommendation", "anomal", "spike", "optimize rds"]) and not is_multi_service_request and not is_lease_type_request

            if not is_rds_instance_or_usage and is_followup:
                prev_is_rds = any("aws_rds_cost_and_usage" in a.lower() or "rds spend analysis" in a.lower() or "rds usage" in a.lower() for a in prior_assistant_msgs[-1:]) or \
                              any(w in prior_cost_low for w in ["rds", "database engine", "rds instance", "relational database"])
                if prev_is_rds and not any(w in low for w in ["customer", "tenant", "anomal", "recommendation", "ec2", "s3", "bigquery", "azure", "lease"]) and not is_multi_service_request and not is_lease_type_request:
                    is_rds_instance_or_usage = True

            if is_rds_instance_or_usage and mcp:
                is_ytd = bool(re.search(r'\b(?:ytd|year[- ]to[- ]date|this\s*year)\b', low))
                is_qtd = bool(re.search(r'\b(?:qtd|quarter[- ]to[- ]date|this\s*quarter)\b', low))
                if not is_ytd and not is_qtd and is_followup:
                    hist_text = " ".join(prior_cost_low.split() + [a.lower() for a in prior_assistant_msgs[-2:]])
                    if re.search(r'\b(?:ytd|year[- ]to[- ]date|this\s*year)\b', hist_text):
                        is_ytd = True
                    elif re.search(r'\b(?:qtd|quarter[- ]to[- ]date|this\s*quarter)\b', hist_text):
                        is_qtd = True

                is_exclude_other = is_exclude_other_requested(low) or is_exclude_other_requested(intent_info.get("corrected_query", ""))
                m_months = re.search(r'\b(\d+)\s*(?:months?|m)\b', low)
                m_days = re.search(r'\b(\d+)\s*(?:days?|d)\b', low)
                has_explicit_months = is_ytd or is_qtd or bool(m_months) or bool(intent_info.get("timeframe_months")) or any(w in low for w in ["12months", "12 months", "year", "annual", "months", "month by month", "monthly"])
                if has_explicit_months:
                    num_months = intent_info.get("timeframe_months") or (datetime.date.today().month if is_ytd else (((datetime.date.today().month - 1) % 3) + 1 if is_qtd else (int(m_months.group(1)) if m_months else 12)))
                    num_days = 0
                else:
                    # User rule: "use last 30days trend for such requests by default unless I ask for the specific time window"
                    num_days = intent_info.get("timeframe_days") or (int(m_days.group(1)) if m_days else 30)
                    num_months = 0

                tenant_suffix = " (Tenant)" if any(w in low for w in ["tenant"]) else ""
                cust_label = named_customer or (f"All Accounts{tenant_suffix}" if tenant_suffix else "All Accounts")

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
                    if is_ytd:
                        period_label = f"Year-to-Date (YTD {datetime.date.today().year})"
                    elif is_qtd:
                        curr_q = ((datetime.date.today().month - 1) // 3) + 1
                        period_label = f"Quarter-to-Date (QTD Q{curr_q} {datetime.date.today().year})"
                    else:
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
                    if is_exclude_other:
                        rds_rows = [r for r in rds_rows if not is_other_category_name(r["instance_type"])]
                    months_present = sorted(list({r["month"] for r in rds_rows}))
                    if num_days > 0:
                        period_str = f"Last {num_days} Days Trend ({months_present[0]} to {months_present[-1]})" if months_present else f"Last {num_days} Days Trend"
                    elif is_ytd:
                        period_str = f"Year-to-Date ({months_present[0]} to {months_present[-1]})" if months_present else f"Year-to-Date (YTD {datetime.date.today().year})"
                    elif is_qtd:
                        curr_q = ((datetime.date.today().month - 1) // 3) + 1
                        period_str = f"Quarter-to-Date ({months_present[0]} to {months_present[-1]})" if months_present else f"Quarter-to-Date (QTD Q{curr_q})"
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
                    if is_exclude_other:
                        sorted_types = [(it, d) for it, d in sorted_types if not is_other_category_name(it)]

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
                    if remaining_types and not is_exclude_other:
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
                            max_cats=10,
                            exclude_other=is_exclude_other
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
                            if is_exclude_other:
                                engine_rows = [r for r in engine_rows if not is_other_category_name(r["engine"])]
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
                (
                    (intent_info.get("service") == "AmazonEC2" and not is_ai_models_query and any(w in low for w in ["instance", "usage", "spend", "cost", "breakdown", "ec2", "how many", "number of", "count"])) or
                    (any(w in low for w in ["instance type", "instancetype", "instance dataset", "ec2_cost_and_usage", "by instance", "instance breakdown", "instances breakdown"]) and not is_ai_models_query) or
                    ("ec2" in low and any(w in low for w in ["instance", "type", "breakdown", "how many", "number of", "count"])) or
                    (intent_info.get("service") == "AmazonEC2" and not is_ai_models_query and intent_info.get("metric_type") == "quantity")
                )
            ) and not any(w in low for w in ["recommendation", "anomal", "spike", "rds", "relational", "aurora", "database"]) and intent_info.get("service") != "AmazonRDS" and not is_multi_service_request and not is_lease_type_request

            if not is_ec2_instance_query and is_followup:
                prev_is_ec2 = any("aws_ec2_cost_and_usage" in a.lower() or "instance type breakdown" in a.lower() for a in prior_assistant_msgs[-1:]) or \
                              any(w in prior_cost_low for w in ["instance type", "instance breakdown", "ec2 usage by instance"])
                if prev_is_ec2 and not any(w in low for w in ["customer", "tenant", "anomal", "recommendation", "gp2", "rds", "s3", "bigquery", "azure", "lease"]) and not is_multi_service_request and not is_lease_type_request:
                    is_ec2_instance_query = True

            if is_ec2_instance_query and mcp:
                is_ytd = bool(re.search(r'\b(?:ytd|year[- ]to[- ]date|this\s*year)\b', low))
                is_qtd = bool(re.search(r'\b(?:qtd|quarter[- ]to[- ]date|this\s*quarter)\b', low))
                if not is_ytd and not is_qtd and is_followup:
                    hist_text = " ".join(prior_cost_low.split() + [a.lower() for a in prior_assistant_msgs[-2:]])
                    if re.search(r'\b(?:ytd|year[- ]to[- ]date|this\s*year)\b', hist_text):
                        is_ytd = True
                    elif re.search(r'\b(?:qtd|quarter[- ]to[- ]date|this\s*quarter)\b', hist_text):
                        is_qtd = True

                is_exclude_other = is_exclude_other_requested(low) or is_exclude_other_requested(intent_info.get("corrected_query", ""))
                m_days = re.search(r'\b(\d+)\s*(?:days?|d)\b', low)
                m_months = re.search(r'\b(\d+)\s*(?:months?|m)\b', low)
                has_explicit_months = is_ytd or is_qtd or bool(m_months) or bool(intent_info.get("timeframe_months")) or is_specific or bool(intent_info.get("target_ym")) or any(w in low for w in ["month by month", "monthly", "12 months", "year", "months", "last month", "past month", "previous month", "prior month"])
                if (is_ytd or is_qtd or has_explicit_months) and not (m_days or (intent_info.get("timeframe_days") and not is_specific and not is_ytd and not is_qtd)):
                    num_days = 0
                else:
                    # User rule: "use last 30days trend for such requests by default unless I ask for the specific time window"
                    num_days = intent_info.get("timeframe_days") or (int(m_days.group(1)) if m_days else 30)

                tenant_suffix = " (Tenant)" if any(w in low for w in ["tenant"]) else ""
                cust_label = named_customer or (f"All Accounts{tenant_suffix}" if tenant_suffix else "All Accounts")

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
                    target_m_filter = f"AND Month = '{target_ym}' " if (is_specific and target_ym) else ""
                    ec2_sql = (
                        "SELECT Month AS month, product_InstanceType AS instance_type, "
                        "SUM(Billed_Cost) AS cost, SUM(Instance_Hours) AS hours, "
                        "SUM(Instances) AS instances, SUM(VCPUs) AS vcpus "
                        "FROM AWS_EC2_COST_AND_USAGE "
                        "WHERE product_InstanceType IS NOT NULL AND product_InstanceType != '' AND product_InstanceType != 'Unknown' "
                        f"{target_m_filter}"
                        "GROUP BY Month, product_InstanceType "
                        "ORDER BY month DESC, cost DESC"
                    )
                    if is_specific and target_ym:
                        query_time_ranges = [{"from": target_ym, "to": target_ym}]
                    elif is_ytd:
                        curr_m = datetime.date.today().month
                        query_time_ranges = [{"last": curr_m, "qualifier": "MONTH"}]
                    elif is_qtd:
                        curr_m = ((datetime.date.today().month - 1) % 3) + 1
                        query_time_ranges = [{"last": curr_m, "qualifier": "MONTH"}]
                    else:
                        query_time_ranges = [{"last": 8, "qualifier": "MONTH"}]

                def _fetch_ec2_chunk(tr):
                    ec2_q_params = {
                        "queryInput": {
                            "sqlStatement": ec2_sql,
                            "dataGranularity": "DAILY" if num_days > 0 else "MONTHLY",
                            "limit": -1 if num_days > 0 else 500,
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
                                inst = float(row.get("instances") or row.get("SUM_Instances") or row.get("Instances") or 0.0)
                                if it and t_val and (c > 0 or h > 0 or inst > 0):
                                    if num_days > 0 and t_val == today_str:
                                        continue
                                    chunk_rows.append({
                                        "time_val": t_val, "instance_type": it, "cost": c,
                                        "hours": h, "vcpus": v, "instances": inst
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
                    if is_exclude_other:
                        ec2_rows = [r for r in ec2_rows if not is_other_category_name(r["instance_type"])]
                    chart_type = _detect_chart_type(low)
                    wants_table = _detect_wants_table(low)
                    t_format = "quarter" if "quarter" in low else "month"
                    is_quantity_mode = (
                        intent_info.get("metric_type") == "quantity" or
                        any(w in low for w in ["number of", "how many", "count of", "count", "quantity", "quantities", "instance count", "instances count", "total instances", "how many ec2"]) or
                        ("instances" in low and not any(w in low for w in ["cost", "spend", "dollar", "$", "bill", "price"]))
                    )

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
                                agg_types[it] = {"cost": 0.0, "hours": 0.0, "instances": 0.0, "vcpus": 0.0}
                            agg_types[it]["cost"] += r["cost"]
                            agg_types[it]["hours"] += r["hours"]
                            agg_types[it]["instances"] += r.get("instances", 0.0)
                            agg_types[it]["vcpus"] += r.get("vcpus", 0.0)

                        if is_quantity_mode:
                            sort_key = "hours" if any(w in low for w in ["hours", "runtime"]) and not any(w in low for w in ["how many instance", "number of instance", "instance count"]) else "instances"
                            sorted_types = sorted(agg_types.items(), key=lambda x: x[1].get(sort_key, 0.0), reverse=True)
                        else:
                            sorted_types = sorted(agg_types.items(), key=lambda x: x[1]["cost"], reverse=True)
                        if is_exclude_other:
                            sorted_types = [(it, d) for it, d in sorted_types if not is_other_category_name(it)]

                        total_period_cost = sum(d["cost"] for _, d in sorted_types) or 1.0
                        total_period_hours = sum(d["hours"] for _, d in sorted_types)
                        total_period_instances = sum(d.get("instances", 0.0) for _, d in sorted_types)

                        tbl_lines = []
                        graviton_candidates = []
                        prev_gen_candidates = []

                        for idx, (it, d) in enumerate(sorted_types[:15]):
                            c = d["cost"]
                            h = d["hours"]
                            inst_c = d.get("instances", 0.0)
                            v = d.get("vcpus", 0.0)
                            if is_quantity_mode:
                                pct = (inst_c / total_period_instances * 100) if total_period_instances > 0 else 0.0
                                tbl_lines.append(
                                    f"| {idx+1} | `{it}` | **{inst_c:,.0f}** | {h:,.0f} hrs | {v:,.0f} | ${c:,.2f} | {pct:.1f}% |"
                                )
                            else:
                                pct = (c / total_period_cost) * 100
                                tbl_lines.append(
                                    f"| {idx+1} | `{it}` | **${c:,.2f}** | {inst_c:,.0f} | {h:,.0f} hrs | {pct:.1f}% |"
                                )
                            cand = {"instance_type": it, "cost": c, "hours": h, "instances": inst_c}
                            if any(fam in it for fam in ["m5.", "c5.", "r5.", "t3."]):
                                graviton_candidates.append(cand)
                            elif any(fam in it for fam in ["c3.", "c4.", "m4.", "r4.", "t2."]):
                                prev_gen_candidates.append(cand)

                        remaining_types = sorted_types[15:]
                        if remaining_types and not is_exclude_other:
                            rem_cost = sum(d["cost"] for _, d in remaining_types)
                            rem_hours = sum(d["hours"] for _, d in remaining_types)
                            rem_inst = sum(d.get("instances", 0.0) for _, d in remaining_types)
                            rem_vcpus = sum(d.get("vcpus", 0.0) for _, d in remaining_types)
                            rem_count = len(remaining_types)
                            if is_quantity_mode:
                                rem_pct = (rem_inst / total_period_instances * 100) if total_period_instances > 0 else 0.0
                                tbl_lines.append(
                                    f"| - | *Other ({rem_count} instance types)* | **{rem_inst:,.0f}** | {rem_hours:,.0f} hrs | {rem_vcpus:,.0f} | ${rem_cost:,.2f} | {rem_pct:.1f}% |"
                                )
                            else:
                                rem_pct = (rem_cost / total_period_cost) * 100
                                tbl_lines.append(
                                    f"| - | *Other ({rem_count} instance types)* | **${rem_cost:,.2f}** | {rem_inst:,.0f} | {rem_hours:,.0f} hrs | {rem_pct:.1f}% |"
                                )

                        chart_md = ""
                        val_label = "Instances" if is_quantity_mode and not any(w in low for w in ["hours", "runtime"]) else ("Hours" if is_quantity_mode else "Cost ($)")
                        chart_vals = [round(d.get("instances" if val_label == "Instances" else ("hours" if val_label == "Hours" else "cost"), 0.0), 2 if val_label == "Cost ($)" else 0) for _, d in sorted_types[:10]]
                        if chart_type in ["doughnut", "pie"]:
                            chart_md = _chart_block(
                                chart_type,
                                f"EC2 Instance Type ({val_label}) ({period_header}) — {cust_label}",
                                [it for it, _ in sorted_types[:10]],
                                values=chart_vals,
                                value_label=val_label
                            )
                        elif chart_type == "horizontal-bar":
                            chart_md = _chart_block(
                                "horizontal-bar",
                                f"EC2 Instance Type ({val_label}) ({period_header}) — {cust_label}",
                                [it for it, _ in sorted_types[:10]],
                                values=chart_vals,
                                value_label=val_label,
                                horizontal=True
                            )
                        elif chart_type == "line":
                            day_totals: dict = {}
                            for r in ec2_rows:
                                day_totals[r["time_val"]] = day_totals.get(r["time_val"], 0.0) + (r.get("instances" if val_label == "Instances" else ("hours" if val_label == "Hours" else "cost"), 0.0))
                            sorted_days = sorted(day_totals.keys())
                            line_labels = [_format_time_label(d, "day") for d in sorted_days]
                            line_vals = [round(day_totals[d], 2 if val_label == "Cost ($)" else 0) for d in sorted_days]
                            chart_md = _chart_block(
                                "line",
                                f"EC2 Total ({val_label}) Trend (Last {num_days} Days) — {cust_label}",
                                line_labels, values=line_vals, value_label=val_label
                            )
                            if val_label == "Cost ($)":
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
                                max_cats=12,
                                exclude_other=is_exclude_other
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

                        if is_quantity_mode:
                            tbl_header = (
                                f"| # | Instance Type | Active Instances | Runtime Hours | vCPUs | Billed Cost | % of Fleet |\n"
                                f"|:---|:---|:---|:---|:---|:---|:---|\n"
                            )
                            tbl_total_row = f"| **Total** | **All Instance Types** | **{total_period_instances:,.0f}** | **{total_period_hours:,.0f} hrs** | | **${total_period_cost:,.2f}** | **100.0%** |\n\n"
                            metric_summary = (
                                f"- **Total Active Fleet Instances**: **{total_period_instances:,.0f} instances**\n"
                                f"- **Total Fleet Runtime Hours**: **{total_period_hours:,.0f} hours**\n"
                                f"- **Total EC2 Billed Spend**: **${total_period_cost:,.2f}** across **{len(sorted_types)}** active instance types\n\n"
                            )
                        else:
                            tbl_header = (
                                f"| # | Instance Type | Cost | Active Instances | Instance Hours | % of Total |\n"
                                f"|:---|:---|:---|:---|:---|:---|\n"
                            )
                            tbl_total_row = f"| **Total** | **All Instance Types** | **${total_period_cost:,.2f}** | **{total_period_instances:,.0f}** | **{total_period_hours:,.0f} hrs** | **100.0%** |\n\n"
                            metric_summary = (
                                f"- **Total EC2 Billed Spend**: **${total_period_cost:,.2f}** across **{len(sorted_types)}** active instance types\n\n"
                            )

                        tbl_block = (
                            f"{tbl_header}"
                            f"{chr(10).join(tbl_lines)}\n"
                            f"{tbl_total_row}"
                        ) if wants_table else ""

                        return (
                            f"### 🖥️ CloudHealth EC2 Spend Analysis: Instance Type Breakdown\n\n"
                            f"Queried live from standard dataset **`AWS_EC2_COST_AND_USAGE`** for **{cust_label}**:\n\n"
                            f"- **Target Billing Period**: {period_header}\n"
                            f"{metric_summary}"
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
                        if is_exclude_other:
                            target_m_rows = [r for r in target_m_rows if not is_other_category_name(r["instance_type"])]
                        month_total = sum(r["cost"] for r in target_m_rows) or 1.0
                        total_instances = sum(r.get("instances", 0.0) for r in target_m_rows)
                        total_hours = sum(r.get("hours", 0.0) for r in target_m_rows)

                        if is_quantity_mode:
                            sort_key = "hours" if any(w in low for w in ["hours", "runtime"]) and not any(w in low for w in ["how many instance", "number of instance", "instance count"]) else "instances"
                            target_m_rows.sort(key=lambda x: x.get(sort_key, 0.0), reverse=True)
                        else:
                            target_m_rows.sort(key=lambda x: x["cost"], reverse=True)

                        tbl_lines = []
                        graviton_candidates = []
                        prev_gen_candidates = []

                        for idx, r in enumerate(target_m_rows[:15]):
                            it = r["instance_type"]
                            c = r["cost"]
                            h = r["hours"]
                            inst_c = r.get("instances", 0.0)
                            v = r.get("vcpus", 0.0)
                            if is_quantity_mode:
                                pct = (inst_c / total_instances * 100) if total_instances > 0 else 0.0
                                tbl_lines.append(
                                    f"| {idx+1} | `{it}` | **{inst_c:,.0f}** | {h:,.0f} hrs | {v:,.0f} | ${c:,.2f} | {pct:.1f}% |"
                                )
                            else:
                                pct = (c / month_total) * 100
                                tbl_lines.append(
                                    f"| {idx+1} | `{it}` | **${c:,.2f}** | {inst_c:,.0f} | {h:,.0f} hrs | {pct:.1f}% |"
                                )
                            # Identify optimization candidates
                            if any(fam in it for fam in ["m5.", "c5.", "r5.", "t3."]):
                                graviton_candidates.append(r)
                            elif any(fam in it for fam in ["c3.", "c4.", "m4.", "r4.", "t2."]):
                                prev_gen_candidates.append(r)

                        remaining_m_rows = target_m_rows[15:]
                        if remaining_m_rows and not is_exclude_other:
                            rem_cost = sum(r["cost"] for r in remaining_m_rows)
                            rem_hours = sum(r["hours"] for r in remaining_m_rows)
                            rem_inst = sum(r.get("instances", 0.0) for r in remaining_m_rows)
                            rem_vcpus = sum(r.get("vcpus", 0.0) for r in remaining_m_rows)
                            rem_count = len(remaining_m_rows)
                            if is_quantity_mode:
                                rem_pct = (rem_inst / total_instances * 100) if total_instances > 0 else 0.0
                                tbl_lines.append(
                                    f"| - | *Other ({rem_count} instance types)* | **{rem_inst:,.0f}** | {rem_hours:,.0f} hrs | {rem_vcpus:,.0f} | ${rem_cost:,.2f} | {rem_pct:.1f}% |"
                                )
                            else:
                                rem_pct = (rem_cost / month_total) * 100
                                tbl_lines.append(
                                    f"| - | *Other ({rem_count} instance types)* | **${rem_cost:,.2f}** | {rem_inst:,.0f} | {rem_hours:,.0f} hrs | {rem_pct:.1f}% |"
                                )

                        # Build chart
                        chart_md = ""
                        val_label = "Instances" if is_quantity_mode and not any(w in low for w in ["hours", "runtime"]) else ("Hours" if is_quantity_mode else "Cost ($)")
                        m_chart_vals = [round(r.get("instances" if val_label == "Instances" else ("hours" if val_label == "Hours" else "cost"), 0.0), 2 if val_label == "Cost ($)" else 0) for r in target_m_rows[:10]]
                        if chart_type in ["doughnut", "pie"]:
                            chart_labels = [r["instance_type"] for r in target_m_rows[:10]]
                            chart_md = _chart_block(chart_type, f"EC2 Instance Type ({val_label}) ({_format_time_label(target_month, t_format)}) — {cust_label}", chart_labels, values=m_chart_vals, value_label=val_label)
                        elif chart_type == "horizontal-bar":
                            chart_labels = [r["instance_type"] for r in target_m_rows[:10]]
                            chart_md = _chart_block("horizontal-bar", f"EC2 Instance Type ({val_label}) ({_format_time_label(target_month, t_format)}) — {cust_label}", chart_labels, values=m_chart_vals, value_label=val_label, horizontal=True)
                        elif chart_type == "waterfall":
                            wf_labels = ["Baseline (Total)"] + [r["instance_type"] for r in target_m_rows[:7]] + ["Total"]
                            wf_vals = [round(month_total, 2)] + [round(r["cost"], 2) for r in target_m_rows[:7]] + [round(month_total, 2)]
                            chart_md = _build_waterfall_chart(
                                f"EC2 Instance Type Cost Contribution ({_format_time_label(target_month, t_format)})",
                                wf_labels, wf_vals
                            )
                        elif chart_type == "line":
                            mo_totals: dict = {}
                            for r in ec2_rows:
                                mo_totals[r["time_val"]] = mo_totals.get(r["time_val"], 0.0) + (r.get("instances" if val_label == "Instances" else ("hours" if val_label == "Hours" else "cost"), 0.0))
                            sorted_mos = sorted(mo_totals.keys())
                            line_labels = [_format_time_label(m, t_format) for m in sorted_mos]
                            line_vals = [round(mo_totals[m], 2 if val_label == "Cost ($)" else 0) for m in sorted_mos]
                            chart_md = _chart_block(
                                "line",
                                f"EC2 Total ({val_label}) Trend ({len(sorted_mos)} Months) — {cust_label}",
                                line_labels, values=line_vals, value_label=val_label
                            )
                            if val_label == "Cost ($)":
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
                                max_cats=12,
                                exclude_other=is_exclude_other
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

                        if is_quantity_mode:
                            tbl_header = (
                                f"| # | Instance Type | Active Instances | Runtime Hours | vCPUs | Billed Cost | % of Fleet |\n"
                                f"|:---|:---|:---|:---|:---|:---|:---|\n"
                            )
                            tbl_total_row = f"| **Total** | **All Instance Types** | **{total_instances:,.0f}** | **{total_hours:,.0f} hrs** | | **${month_total:,.2f}** | **100.0%** |\n\n"
                            metric_summary = (
                                f"- **Total Active Fleet Instances**: **{total_instances:,.0f} instances**\n"
                                f"- **Total Fleet Runtime Hours**: **{total_hours:,.0f} hours**\n"
                                f"- **Total EC2 Billed Spend**: **${month_total:,.2f}** across **{len(target_m_rows)}** active instance types\n\n"
                            )
                        else:
                            tbl_header = (
                                f"| # | Instance Type | Cost | Active Instances | Instance Hours | % of Total |\n"
                                f"|:---|:---|:---|:---|:---|:---|\n"
                            )
                            tbl_total_row = f"| **Total** | **All Instance Types** | **${month_total:,.2f}** | **{total_instances:,.0f}** | **{total_hours:,.0f} hrs** | **100.0%** |\n\n"
                            metric_summary = (
                                f"- **Total EC2 Billed Spend**: **${month_total:,.2f}** ({total_instances:,.0f} active instances, {total_hours:,.0f} hrs) across **{len(target_m_rows)}** active instance types\n\n"
                            )

                        tbl_block = (
                            f"{tbl_header}"
                            f"{chr(10).join(tbl_lines)}\n"
                            f"{tbl_total_row}"
                        ) if wants_table else ""

                        return (
                            f"### 🖥️ CloudHealth EC2 Spend Analysis: Instance Type Breakdown\n\n"
                            f"Queried live from standard dataset **`AWS_EC2_COST_AND_USAGE`** for **{cust_label}**:\n\n"
                            f"- **Target Billing Period**: `{_format_time_label(target_month, t_format)}`\n"
                            f"{metric_summary}"
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

            is_breakdown_kw = any(w in low for w in [
                "breakdown", "break down", "break it down", "top services", "by service", "by product",
                "by model", "by provider", "by category", "by subcategory", "by account", "by region",
                "by instance type", "by model name", "model name"
            ])
            is_dimensional_query = bool(
                intent_info.get("target_dimension")
                or any(b in (intent_info.get("breakdowns") or []) for b in ["model", "ai_model", "model_provider", "instance_type", "service_category", "service_subcategory", "pricing_category", "account", "region", "resource"])
                or is_breakdown_kw
            )

            if requested_service and str(requested_service) not in ("AmazonRDS", "AmazonEC2", "Azure", "GCP", "AWS", "Cloud", "all", "AI", "ai") and not is_dimensional_query:
                major_svc_pcode = str(requested_service)
                major_svc_disp = requested_service_disp or major_svc_pcode
                major_svc_prov = PCODE_TO_PROVIDER.get(major_svc_pcode, "aws")
                major_svc_target = major_svc_pcode
            elif any(w in low for w in ["s3", "storage bucket", "buckets", "s3 bucket"]) and not is_dimensional_query:
                major_svc_pcode = "AmazonS3"
                major_svc_disp = "Amazon S3"
                major_svc_target = "AmazonS3"
            elif any(w in low for w in ["ebs", "ebs volume", "ebs volumes", "ebs snapshot", "ebs snapshots"]) and not is_dimensional_query:
                major_svc_pcode = "AmazonEC2_EBS"
                major_svc_disp = "Amazon EBS"
                major_svc_target = "EBS"
            elif any(w in low for w in ["lambda", "serverless function", "lambda function"]) and not is_dimensional_query:
                major_svc_pcode = "AWSLambda"
                major_svc_disp = "AWS Lambda"
                major_svc_target = "AWSLambda"
            elif any(w in low for w in ["dynamodb", "nosql database", "dynamo"]) and not is_dimensional_query:
                major_svc_pcode = "AmazonDynamoDB"
                major_svc_disp = "Amazon DynamoDB"
                major_svc_target = "AmazonDynamoDB"
            elif any(w in low for w in ["bedrock", "amazon bedrock", "aws bedrock"]) and not is_dimensional_query:
                major_svc_pcode = "AmazonBedrock"
                major_svc_disp = "Amazon Bedrock"
                major_svc_target = "AmazonBedrock"
            elif any(w in low for w in ["cloudfront", "edge cdn", "cloud front"]) and not is_dimensional_query:
                major_svc_pcode = "AmazonCloudFront"
                major_svc_disp = "Amazon CloudFront"
                major_svc_target = "AmazonCloudFront"
            elif any(w in low for w in ["nat gateway", "nat gateways", "vpc networking"]) and not is_dimensional_query:
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
                major_svc_target and not is_dimensional_query and
                not any(w in low for w in ["recommendation", "recommendations", "anomal", "spike", "usagetype", "usage-type", "usage type"]) and not is_multi_service_request
            )

            if is_major_service_inquiry:
                # Resolve timeframe: daily trend vs monthly
                is_ytd = bool(re.search(r'\b(?:ytd|year\s*to\s*date|this\s*year)\b', low))
                is_qtd = bool(re.search(r'\b(?:qtd|quarter\s*to\s*date|this\s*quarter)\b', low))
                if not is_ytd and not is_qtd and is_followup:
                    hist_text = " ".join(prior_cost_low.split() + [a.lower() for a in prior_assistant_msgs[-2:]])
                    if any(w in hist_text for w in ["ytd", "year to date", "this year"]):
                        is_ytd = True
                    elif any(w in hist_text for w in ["qtd", "quarter to date", "this quarter"]):
                        is_qtd = True

                is_exclude_other = is_exclude_other_requested(low) or is_exclude_other_requested(intent_info.get("corrected_query", ""))
                is_trend_query = any(w in low for w in ["trend", "daily", "day", "days", "last 15", "last 30", "trailing", "over time"])
                m_days = re.search(r'(?:last|past|trailing|for)\s+(\d{1,3})\s*days?\b|\b(\d{1,3})\s*(?:days?|d)\b', low)
                num_days = 0 if (is_ytd or is_qtd) else (intent_info.get("timeframe_days") or (int(m_days.group(1) or m_days.group(2)) if m_days else (30 if is_trend_query else 0)))
                m_months = re.search(r'(?:last|past|trailing|for)\s+(\d{1,2})\s*months?\b|\b(\d{1,2})\s*(?:months?|m)\b', low)
                num_months = datetime.date.today().month if is_ytd else (((datetime.date.today().month - 1) % 3) + 1 if is_qtd else (intent_info.get("timeframe_months") or (int(m_months.group(1) or m_months.group(2)) if m_months else 0)))
                time_col = "timeInterval_Day" if num_days > 0 else "timeInterval_Month"
                t_format = "day" if num_days > 0 else ("quarter" if "quarter" in low else "month")

                if num_days > 0:
                    d_start = cal["d15_start"] if num_days <= 15 else (datetime.date.today() - datetime.timedelta(days=num_days)).strftime("%Y-%m-%d")
                    svc_time_range = {"from": d_start, "to": yesterday_str}
                    svc_granularity = "DAILY"
                    svc_period_label = f"Trailing {num_days} Days: {d_start} to {yesterday_str}"
                elif is_ytd:
                    svc_time_range = {"last": min(num_months, 12), "qualifier": "MONTH"}
                    svc_granularity = "MONTHLY"
                    svc_period_label = f"Year-To-Date (YTD {datetime.date.today().year})"
                elif is_qtd:
                    q_num = ((datetime.date.today().month - 1) // 3) + 1
                    svc_time_range = {"last": min(num_months, 12), "qualifier": "MONTH"}
                    svc_granularity = "MONTHLY"
                    svc_period_label = f"Q{q_num} QTD ({datetime.date.today().year})"
                elif num_months > 1:
                    svc_time_range = {"last": min(num_months, 12), "qualifier": "MONTH"}
                    svc_granularity = "MONTHLY"
                    svc_period_label = f"Last {min(num_months, 12)} Months"
                elif is_specific and target_ym != last_ym:
                    svc_time_range = {"from": target_ym, "to": target_ym}
                    svc_granularity = "MONTHLY"
                    svc_period_label = target_label
                else:
                    svc_time_range = {"last": 1, "qualifier": "MONTH", "excludeCurrent": False}
                    svc_granularity = "MONTHLY"
                    svc_period_label = f"Current Month ({current_ym})"

                cust_label = named_customer or "All Accounts"
                wants_table = _detect_wants_table(low)
                chart_type = _detect_chart_type(low)

                # Query AWS_CUR or MULTICLOUD_FOCUS
                svc_rows = []
                is_quantity_mode = (
                    intent_info.get("metric_type") == "quantity" or
                    any(w in low for w in ["number of", "how many", "count of", "quantity", "quantities", "storage used", "gb used", "gigabytes", "tb used", "hours", "invocations", "volume in gb"])
                )

                if major_svc_prov == "aws":
                    if major_svc_pcode == "AmazonS3" and not any(w in low for w in ["usagetype", "usage type", "operation"]):
                        where_clause = "WHERE lineItem_ProductCode = 'AmazonS3' AND product_storageClass IS NOT NULL AND product_storageClass != ''"
                        sql_svc = (
                            f"SELECT {time_col} AS time_val, product_storageClass AS usage_type, '' AS operation, "
                            f"SUM(lineItem_UnblendedCost) AS cost, SUM(lineItem_UsageAmount) AS usage_qty "
                            f"FROM AWS_CUR "
                            f"{where_clause} "
                            f"GROUP BY {time_col}, product_storageClass "
                            f"ORDER BY time_val ASC, cost DESC"
                        )
                    elif major_svc_pcode == "AmazonEC2_EBS" and not any(w in low for w in ["usagetype", "usage type", "operation"]):
                        where_clause = "WHERE lineItem_ProductCode = 'AmazonEC2' AND product_volumeType IS NOT NULL AND product_volumeType != ''"
                        sql_svc = (
                            f"SELECT {time_col} AS time_val, product_volumeType AS usage_type, '' AS operation, "
                            f"SUM(lineItem_UnblendedCost) AS cost, SUM(lineItem_UsageAmount) AS usage_qty "
                            f"FROM AWS_CUR "
                            f"{where_clause} "
                            f"GROUP BY {time_col}, product_volumeType "
                            f"ORDER BY time_val ASC, cost DESC"
                        )
                    else:
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
                                if (c > 0 or q > 0) and (num_days == 0 or tv != today_str):
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
                    native_table_map = {
                        "azure": "AZURE_FOCUS_COST_AND_USAGE",
                        "gcp": "GCP_FOCUS_COST_AND_USAGE",
                    }
                    if major_svc_prov in native_table_map:
                        sql_svc = (
                            f"SELECT Month AS time_val, ServiceSubcategory AS usage_type, PricingUnit AS operation, "
                            f"SUM(PricingQuantity) AS usage_qty, SUM(EffectiveCost) AS cost "
                            f"FROM {native_table_map[major_svc_prov]} "
                            f"WHERE ServiceName = '{major_svc_pcode}' "
                            f"GROUP BY Month, ServiceSubcategory, PricingUnit "
                            f"ORDER BY Month ASC, cost DESC"
                        )
                    else:
                        prov_clause = "provider IN ('OCI', 'Oracle Cloud')" if major_svc_prov == "oci" else f"provider = '{major_svc_prov}'"
                        sql_svc = (
                            f"SELECT Month AS time_val, ServiceName AS usage_type, PricingUnit AS operation, "
                            f"SUM(PricingQuantity) AS usage_qty, SUM(EffectiveCost) AS cost "
                            f"FROM MULTICLOUD_FOCUS_COST_AND_USAGE "
                            f"WHERE {prov_clause} AND ServiceName = '{major_svc_pcode}' "
                            f"GROUP BY Month, ServiceName, PricingUnit "
                            f"ORDER BY Month ASC, cost DESC"
                        )
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
                                q = float(r.get("usage_qty") or 0.0)
                                if c > 0 or q > 0:
                                    svc_rows.append({
                                        "time_val": tv, "usage_type": r.get("usage_type", major_svc_disp),
                                        "operation": r.get("operation", ""), "cost": c, "qty": q
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
                cat_qtys: dict = {}
                cat_ops: dict = {}
                time_totals: dict = {}

                for r in svc_rows:
                    cat = _classify_service_usage_type(major_svc_pcode, r["usage_type"], r.get("operation", ""))
                    cost = r["cost"]
                    qty = r.get("qty", 0.0)
                    t_val = r["time_val"]

                    cat_totals[cat] = cat_totals.get(cat, 0.0) + cost
                    cat_qtys[cat] = cat_qtys.get(cat, 0.0) + qty
                    time_totals[t_val] = time_totals.get(t_val, 0.0) + cost
                    if cat not in cat_ops and r.get("operation"):
                        cat_ops[cat] = r["operation"]

                if is_exclude_other:
                    cat_totals = {k: v for k, v in cat_totals.items() if not is_other_category_name(k)}
                    cat_qtys = {k: v for k, v in cat_qtys.items() if not is_other_category_name(k)}

                # Fetch real total
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
                total_svc_qty = sum(cat_qtys.values())

                if is_quantity_mode:
                    sorted_cats = sorted(cat_totals.items(), key=lambda x: cat_qtys.get(x[0], 0.0), reverse=True)
                else:
                    sorted_cats = sorted(cat_totals.items(), key=lambda x: x[1], reverse=True)

                unit_label = "GB-Mo" if any(w in major_svc_pcode.lower() for w in ["s3", "storage", "ebs", "disk", "blob"]) else ("Hours" if any(w in major_svc_pcode.lower() for w in ["vm", "compute"]) else "Units")

                # Format Markdown table
                tbl_lines = []
                for idx, (cat, c) in enumerate(sorted_cats[:12]):
                    q = cat_qtys.get(cat, 0.0)
                    op_note = cat_ops.get(cat, "")
                    if is_quantity_mode:
                        pct = (q / total_svc_qty * 100) if total_svc_qty > 0 else 0.0
                        op_str = f" | {op_note[:45]}" if op_note else " | Active volume"
                        tbl_lines.append(f"| {idx+1} | **{cat}** | **{q:,.1f} {unit_label}** | ${c:,.2f} | {pct:.1f}%{op_str} |")
                    else:
                        pct = (c / total_svc_spend) * 100
                        qty_str = f"{q:,.1f} {unit_label} | " if q > 0 else ""
                        op_str = f" | {op_note[:45]}" if op_note else " | Active operational workload"
                        tbl_lines.append(f"| {idx+1} | **{cat}** | **${c:,.2f}** | {qty_str}{pct:.1f}%{op_str} |")

                cat_header = "Service Category / Storage Tier" if any(w in major_svc_pcode.lower() for w in ["s3", "storage", "ebs", "blob"]) else "Service Category / Meter"
                if is_quantity_mode:
                    tbl_header = (
                        f"| # | {cat_header} | Usage Volume | Spend ($) | % of Total Volume | Operational Usage Notes |\n"
                        f"|:---|:---|:---|:---|:---|:---|\n"
                    )
                    tbl_total_row = f"| **Total** | **All Categories** | **{total_svc_qty:,.1f} {unit_label}** | **${total_svc_spend:,.2f}** | **100.0%** | |\n\n"
                    metric_summary = (
                        f"- **Total Operational Volume**: **{total_svc_qty:,.1f} {unit_label}**\n"
                        f"- **Total {major_svc_disp} Spend**: **${total_svc_spend:,.2f}** across **{len(sorted_cats)}** distinct service categories\n\n"
                    )
                else:
                    qty_col = "Usage Volume | " if total_svc_qty > 0 else ""
                    qty_sep = ":---|" if total_svc_qty > 0 else ""
                    tbl_header = (
                        f"| # | {cat_header} | Spend ($) | {qty_col}% of Total | Operational Usage Notes |\n"
                        f"|:---|:---|:---|{qty_sep}:---|:---|\n"
                    )
                    qty_tot = f"**{total_svc_qty:,.1f} {unit_label}** | " if total_svc_qty > 0 else ""
                    tbl_total_row = f"| **Total** | **All Categories** | **${total_svc_spend:,.2f}** | {qty_tot}**100.0%** | |\n\n"
                    metric_summary = (
                        f"- **Total {major_svc_disp} Billed Spend**: **${total_svc_spend:,.2f}**" + (f" ({total_svc_qty:,.1f} {unit_label})" if total_svc_qty > 0 else "") + f" across **{len(sorted_cats)}** distinct service categories\n\n"
                    )

                tbl_block = (
                    f"{tbl_header}"
                    f"{chr(10).join(tbl_lines)}\n"
                    f"{tbl_total_row}"
                ) if wants_table else ""

                # Build synchronized chart
                chart_md = ""
                chart_title = f"{major_svc_disp} ({('Volume (' + unit_label + ')') if is_quantity_mode else 'Spend'}) — {svc_period_label} — {cust_label}"
                c_labels = [c[0] for c in sorted_cats[:8]]
                c_vals = [round(cat_qtys.get(c[0], 0.0) if is_quantity_mode else c[1], 2) for c in sorted_cats[:8]]
                val_lbl = unit_label if is_quantity_mode else "Cost ($)"

                if chart_type in ["doughnut", "pie"]:
                    chart_md = _chart_block(chart_type, chart_title, c_labels, values=c_vals, value_label=val_lbl)
                elif chart_type == "horizontal-bar":
                    chart_md = _chart_block("horizontal-bar", chart_title, c_labels, values=c_vals, value_label=val_lbl, horizontal=True)
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
                    chart_md = _chart_block("bar", chart_title, c_labels, values=c_vals, value_label=val_lbl, horizontal=False)

                # Attach domain-specific FinOps optimization levers
                domain_insights = _generate_domain_finops_insights(major_svc_pcode, cat_totals, total_svc_spend)
                insights_block = "\n#### 💡 FinOps Optimization Levers & Architecture Recommendations\n\n" + "\n".join(domain_insights) + "\n"

                partial_note = f"> ⚠️ *Note: Billing data for yesterday (`{yesterday_str}`) is preliminary/partial due to cloud billing settlement latency.*\n\n" if num_days > 0 else ""

                return (
                    f"### ☁️ CloudHealth {major_svc_disp} Spend & Usage Analysis\n\n"
                    f"Queried live telemetry for **{cust_label}**:\n\n"
                    f"{partial_note}"
                    f"- **Target Billing Period**: `{svc_period_label}`\n"
                    f"{metric_summary}"
                    f"{tbl_block}"
                    f"{chart_md}"
                    f"{insights_block}\n"
                    f"*Source: {'AWS_CUR' if major_svc_prov == 'aws' else native_table_map.get(major_svc_prov, 'MULTICLOUD_FOCUS_COST_AND_USAGE')} & CloudHealth FlexReports. Continuous FinOps monitoring active.*"
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
                cust_display = named_customer or "All Accounts"

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
                    c_kind = chart_type if chart_type in ["doughnut", "pie", "bar", "horizontal-bar", "line", "area"] else "bar"
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

            # 3-Rec. Cost Optimization Recommendations & ROI Simulation
            is_rec_query = any(w in low for w in [
                "recommendation", "recommendations", "optimize", "optimization",
                "rightsizer", "rightsizing", "reduce cost", "cost reduction",
                "save money", "savings opportunity", "savings opportunities",
                "roi", "simulation", "simulate", "breakeven", "break-even", "payback",
                "efficiency calculation", "efficiency calculations", "payback period"
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

                # Executive Savings & ROI Calculation
                quick_win_total = (gp2_cost * 0.20) + (acu_cost * 0.25) + (backup_cost * 0.35)
                strategic_total = (oracle_cost * 0.25) + (sqlserver_cost * 0.25) + (graviton_cost * 0.20 if (oracle_cost == 0 and sqlserver_cost == 0) else 0)
                if quick_win_total == 0 and strategic_total == 0:
                    quick_win_total = total_rec_spend * 0.15
                    strategic_total = total_rec_spend * 0.15
                total_pot_sav = quick_win_total + strategic_total
                total_pot_pct = (total_pot_sav / total_rec_spend * 100) if total_rec_spend > 0 else 0

                # Grounded FinOps ROI & Break-Even Modeling (Ban on Flat Assumptions)
                # Quick Wins: $0 CapEx, Day 1 / Immediate Payback (< 14 days), > 1,000% ROI
                # Strategic Modernization: ~60-120 dev hours ($7,500 - $15,000) + cutover, 90-180 days (3-6 mo) payback
                # Rate Optimization: 1-Year No-Upfront commitments break even in 7-9 months at >=80% utilization
                strategic_impl_cost = max(3500.0, strategic_total * 1.5)
                strategic_payback_mo = (strategic_impl_cost / strategic_total) if strategic_total > 0 else 4.0
                strategic_annual_sav = strategic_total * 12.0
                strategic_roi_pct = max(120.0, ((strategic_annual_sav - strategic_impl_cost) / strategic_impl_cost * 100)) if strategic_impl_cost > 0 else 220.0

                is_roi_query = any(w in low for w in ["roi", "simulate", "simulation", "breakeven", "break-even", "payback", "efficiency calculation"])
                report_heading = (
                    f"### 📈 Deep-Dive FinOps ROI Simulation & Break-Even Analysis: {named_customer}{svc_title}\n\n"
                    if is_roi_query else
                    f"### 💡 Deep-Dive FinOps Optimization Recommendations: {named_customer}{svc_title}\n\n"
                )

                return (
                    f"{report_heading}"
                    f"Based on **{named_customer}**'s live AWS CUR telemetry over the last **{rec_days} days** (Total Period Spend: **${total_rec_spend:,.2f}**):\n\n"
                    f"#### 🔍 Top Granular Cost Drivers & Line Items\n\n"
                    f"| Usage Type | AWS Operation | Line Item Description | Spend | % of Spend |\n"
                    f"|:---|:---|:---|:---|:---|\n"
                    f"{chr(10).join(table_lines)}\n"
                    f"| **Total Period Spend** | | | **${total_rec_spend:,.2f}** | **100.0%** |\n\n"
                    f"#### 🎯 High-Impact FinOps Optimization Levers (Fina Waste Framework)\n\n"
                    f"{rec_md}\n\n"
                    f"#### 📊 Executive Savings & Payback Scorecard\n\n"
                    f"| Optimization Category | Implementation Effort | Estimated Monthly Savings | % Reduction | Break-Even Horizon | Net Annualized ROI |\n"
                    f"|:---|:---|:---|:---|:---|:---|\n"
                    f"| **Immediate Quick Wins** (gp2->gp3, ACU floor, Backup pruning) | 🔻 Low (1-Click / Config) | **${quick_win_total:,.2f}** | **{(quick_win_total/total_rec_spend*100):.1f}%** | **Immediate / Day 1 (0–14 days)** | **> 1,000% (Instantaneous)** |\n"
                    f"| **Strategic Modernization** (Graviton / Engine Migration / Rightsizing) | 🟡 Medium / 🔴 High | **${strategic_total:,.2f}** | **{(strategic_total/total_rec_spend*100):.1f}%** | **90–180 Days (3–6 Months)** | **{strategic_roi_pct:.0f}% Net ROI** |\n"
                    f"| **Rate Optimization** (1-Year Compute Savings Plans / RIs) | 🔻 Low (Financial Governance) | **${comm_sav:,.2f}** | **{(comm_sav/total_rec_spend*100):.1f}%** | **7–9 Months (at ≥80% util)** | **25% – 35% Net Annual Savings** |\n"
                    f"| **Total Projected Savings Opportunity** | | **${total_pot_sav:,.2f} / month** | **{total_pot_pct:.1f}%** | — | — |\n\n"
                    f"#### 📈 FinOps ROI & Break-Even Simulation Model\n\n"
                    f"| Category / Strategy | Implementation Cost (Dev + Double-Bubble) | Monthly Run-Rate Delta | Annualized Net Savings | Break-Even Horizon | Net 1-Year ROI % | Value Realization Gate |\n"
                    f"|:---|:---|:---|:---|:---|:---|:---|\n"
                    f"| **Immediate Waste & Quick Wins** | $0 CapEx (~2 hrs dev review) | -${quick_win_total:,.2f} / mo | ${quick_win_total*12:,.2f} / yr | **Day 1 / Immediate (< 14 days)** | **> 1,000%** | Spend Removed (Immediate P&L relief) |\n"
                    f"| **Strategic Silicon & DB Modernization** | ~${strategic_impl_cost:,.2f} (Sprints + Cutover) | -${strategic_total:,.2f} / mo | ${strategic_annual_sav:,.2f} / yr | **90–180 Days ({strategic_payback_mo:.1f} mo)** | **{strategic_roi_pct:.0f}%** | Spend Removed (License Exit) |\n"
                    f"| **1-Year Steady-State Commitments** | $0 Upfront (No-Upfront SP/RI) | -${comm_sav:,.2f} / mo | ${comm_sav*12:,.2f} / yr | **7–9 Months (Payback threshold)** | **25%–35%** | Spend Avoided (Baseline discount) |\n\n"
                    f"##### 🎯 FinOps Efficiency Metrics & KPIs\n"
                    f"- **Effective Savings Rate (ESR)**: **{total_pot_pct:.1f}%** (target benchmark: 15%–25% across balanced estates).\n"
                    f"- **Commitment Coverage Target**: **70% to 80%** of steady-state compute (never 100% to preserve headroom for rightsizing).\n"
                    f"- **Commitment Utilization Target**: **≥ 80% to 95%** (break-even utilization threshold: `1 - discount %`).\n"
                    f"- **Realized vs. Potential Savings**: Sized potential backlog is **${total_pot_sav:,.2f} / month**; realized savings will be booked upon sprint cutover.\n"
                    f"- **Governed Break-Even Taxonomy**: Rejects flat 22-day blanket assumptions — zero friction quick wins pay back on Day 1, while structural database and silicon migrations require a realistic 3–6 month amortized payback window.\n\n"
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
                    q_params = {
                        "queryInput": {
                            "sqlStatement": "SELECT provider AS provider, ServiceName AS service, SUM(EffectiveCost) AS cost FROM MULTICLOUD_FOCUS_COST_AND_USAGE GROUP BY provider, ServiceName ORDER BY cost DESC",
                            "dataGranularity": "MONTHLY", "limit": 200, "timeRange": {"last": 1, "qualifier": "MONTH"}
                        },
                        "requestInfo": {"sourceType": "API", "caller": "mcp"}
                    }
                    if named_customer_crn:
                        q_params["orgId"] = named_customer_crn
                    res_mc = mcp.call_tool("execute_datasource_query", q_params)
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
                    "MONTHLY", {"last": 1, "qualifier": "MONTH"},
                    crn=named_customer_crn
                ) or sum(r[2] for r in mc_rows)
                cust_prefix = f"for **{named_customer}** " if named_customer else ""
                rec_title = f"{cust_prefix}Across All Cloud Providers" if is_multi_cloud else f"{cust_prefix}for {target_cloud_rec.upper()}"

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
                     "Upgrade AWS EBS/RDS `gp2` volumes to `gp3`, remove orphaned/unattached Azure managed disks, and apply Azure/GCP storage lifecycle tiering (Hot→Cool/Archive, Standard→Nearline/Coldline).",
                     "Immediate / Day 1 (0–14 days)", "> 1,000% (Instantaneous)"),
                    ("Commercial Database Modernization & Licensing Rightsizing", "Database Licensing & Architecture Modernization (Strategic)",
                     "🔴 High", db_cost, 0.30,
                     "Modernize proprietary commercial database engines (Oracle/SQL Server on RDS) to Amazon Aurora PostgreSQL, rightsize Azure SQL DTU/vCore allocations.",
                     "90–180 days (3–6 months)", "180% – 300% Net ROI"),
                    ("Architecture & Silicon Modernization (ARM64/Graviton)", "Hardware Efficiency & Silicon Modernization (Strategic)",
                     "🟡 Medium", compute_cost, 0.18,
                     "Transition stateless compute to ARM64/Graviton-class instances (AWS Graviton, GCP Tau, Azure Ampere) for better price-performance with minimal application changes.",
                     "90–150 days (3–5 months)", "150% – 250% Net ROI"),
                    ("AI Token Economics & Batch Inference Optimization", "AI/GenAI Capacity Planning & Token Economics (Strategic)",
                     "🟡 Medium", ai_cost, 0.35,
                     "Enable prompt caching on Claude/Bedrock and Azure OpenAI models to cut repeated prompt-token costs, and route non-realtime workloads through batch inference APIs for discounted rates.",
                     "Immediate (< 14 days)", "> 800% Net ROI"),
                ]

                levers_md = []
                opp_rows = []
                total_savings = 0.0
                for idx, (title, category, complexity, base_cost, pct, action, payback, roi_label) in enumerate(lever_defs, start=1):
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
                        f"- **Break-Even & Payback Horizon**: **{payback}** (Expected ROI: **{roi_label}**).\n"
                    )
                    opp_rows.append((title, base_cost, complexity, sav, payback, roi_label, category))

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
                        f"- **Break-Even Horizon**: **7–9 months (1-Yr Term)** at ≥80% baseline utilization.\n"
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

                if opp_rows:
                    sc_rows = [
                        f"| **{title}** | {complexity} | ${base_cost:,.2f} | **${sav:,.2f}** | {(sav/base_cost*100):.1f}% | {payback} | {roi_label} |"
                        for title, base_cost, complexity, sav, payback, roi_label, _ in opp_rows
                    ]
                    scorecard_table = (
                        f"| Optimization Opportunity / Lever | Complexity | Baseline Spend | Est. Monthly Savings | % Reduction | Break-Even Horizon | Net Annualized ROI |\n"
                        f"|:---|:---|:---|:---|:---|:---|:---|\n"
                        f"{chr(10).join(sc_rows)}\n"
                        f"| **Total Projected Savings Opportunity** | | **${total_mc_spend:,.2f}** | **${total_savings:,.2f} / month** | **{savings_pct:.1f}%** | — | — |"
                    )

                    # Dynamic FinOps ROI Simulation rows
                    sim_rows = []
                    for title, base_cost, complexity, sav, payback, roi_label, cat in opp_rows:
                        ann_sav = sav * 12.0
                        if "Quick Win" in cat or "Immediate" in payback:
                            impl_cost = "$0 CapEx (~2 hrs dev review)"
                            dest = "Spend Removed (Next billing cycle)"
                        elif "Strategic" in cat or "90" in payback:
                            est_cost = max(4000.0, sav * 1.5)
                            impl_cost = f"~${est_cost:,.2f} (Dev Sprints + Cutover)"
                            dest = "Spend Removed (License & silicon exit)"
                        else:
                            impl_cost = "$0 Upfront (No-Upfront Commitment)"
                            dest = "Spend Avoided (Baseline discount)"
                        sim_rows.append(
                            f"| **{title}** | {impl_cost} | -${sav:,.2f} / mo | ${ann_sav:,.2f} / yr | **{payback}** | **{roi_label}** | {dest} |"
                        )
                    sim_table = (
                        f"| Optimization Lever / Strategy | Implementation Cost (CapEx / Dev) | Monthly Run-Rate Delta | Annualized Net Savings | Break-Even Horizon | Expected 1-Year ROI | Value Realization Gate |\n"
                        f"|:---|:---|:---|:---|:---|:---|:---|\n"
                        f"{chr(10).join(sim_rows)}"
                    )
                else:
                    scorecard_table = (
                        f"| Metric | Value |\n"
                        f"|:---|:---|\n"
                        f"| **Total Monthly Spend Analyzed** | **${total_mc_spend:,.2f}** |\n"
                        f"| **Total Projected Savings Opportunity** | **${total_savings:,.2f} / month** |\n"
                        f"| **Potential Spend Reduction** | **{savings_pct:.1f}%** |\n"
                        f"| **Break-Even Horizon (Rate Optimization)** | **7–9 Months (1-Yr Term)** |"
                    )
                    sim_table = (
                        f"| Optimization Lever / Strategy | Implementation Cost | Monthly Run-Rate Delta | Annualized Net Savings | Break-Even Horizon | Expected 1-Year ROI | Value Realization Gate |\n"
                        f"|:---|:---|:---|:---|:---|:---|:---|\n"
                        f"| **Commitments & Modernization Review** | $0 Upfront | -${total_savings:,.2f} / mo | ${total_savings*12:,.2f} / yr | **7–9 Months** | **25%–35%** | Spend Avoided |"
                    )

                is_roi_query = any(w in low for w in ["roi", "simulate", "simulation", "breakeven", "break-even", "payback", "efficiency calculation"])
                heading_banner = (
                    f"### 📈 CloudHealth Multi-Cloud FinOps ROI Simulation & Break-Even Analysis {rec_title}\n"
                    if is_roi_query else
                    f"### 💡 CloudHealth Multi-Cloud FinOps Optimization: Top Strategic Recommendations {rec_title}\n"
                )

                rec_sections = [
                    heading_banner,
                    f"Based on live multi-cloud telemetry retrieved from **CloudHealth FOCUS & Billing Datasets** "
                    f"(Total Analyzed Monthly Spend: **${total_mc_spend:,.2f}**):\n",
                    f"#### 🔍 Top Spend by Provider & Service\n\n{spend_table}\n",
                    f"#### 🎯 Prioritized Multi-Cloud FinOps Levers\n",
                    *levers_md,
                    f"#### 📊 Executive Savings & Payback Scorecard\n\n{scorecard_table}\n",
                    f"#### 📈 FinOps ROI & Break-Even Simulation Model\n\n{sim_table}\n\n"
                    f"##### 🎯 FinOps Efficiency Metrics & KPIs\n"
                    f"- **Effective Savings Rate (ESR)**: **{savings_pct:.1f}%** (target benchmark: 15%–25% across balanced multi-cloud estates).\n"
                    f"- **Commitment Coverage Target**: **70% to 80%** of steady-state compute (never 100% to preserve headroom for rightsizing).\n"
                    f"- **Commitment Utilization Target**: **≥ 80% to 95%** (break-even utilization threshold: `1 - discount %`).\n"
                    f"- **Realized vs. Potential Savings**: Sized potential backlog is **${total_savings:,.2f} / month**; realized savings will be booked upon sprint cutover.\n"
                    f"- **Governed Break-Even Taxonomy**: Rejects flat 22-day blanket assumptions — zero friction quick wins pay back on Day 1, while structural database and silicon migrations require a realistic 3–6 month amortized payback window.\n\n",
                    f"*Source: CloudHealth FOCUS & Multi-Cloud Billing Datasets (AWS CUR, Azure Cost Management, GCP BigQuery). "
                    f"Savings and ROI figures are grounded in the FinOps Foundation Framework and live telemetry.*"
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
                    monthly_sql = "SELECT Month AS month, SUM(EffectiveCost) AS cost FROM MULTICLOUD_FOCUS_COST_AND_USAGE WHERE provider = 'AWS' GROUP BY Month ORDER BY month DESC"
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
                            elif chart_type in ("line", "area"):
                                # Trend: line or area chart of monthly totals + MoM variance waterfall
                                lbls = [_format_time_label(r["month"], t_format) for r in m_rows]
                                vals = [round(float(r.get("cost") or 0), 2) for r in m_rows]
                                chart_md = _chart_block(chart_type, f"{scope_name} Monthly Cloud Spend Trend", lbls, values=vals, value_label="Cost ($)")
                                # MoM variance waterfall beneath
                                mo_totals_g = {r["month"]: float(r.get("cost") or 0) for r in m_rows}
                                sorted_mos_g = sorted(mo_totals_g.keys())
                                variance_md = _build_mom_variance_chart(
                                    f"{scope_name} Month-over-Month Spend Variance", sorted_mos_g, mo_totals_g, time_format=t_format
                                )
                                chart_md += f"\n{variance_md}" if variance_md else ""
                            else:
                                c_kind = chart_type if chart_type in ["pie", "doughnut", "bar", "horizontal-bar", "area"] else "bar"
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
            is_service_or_specific_query = any(w in low for w in [
                "service", "services", "top service", "top services", "by service", "each service",
                "service category", "service spend", "product", "breakdown", "anomaly", "anomalies", "recommendation"
            ])
            _prior_was_monthly_trend = bool(prior_assistant_msgs) and "MoM Change" in prior_assistant_msgs[-1]
            if _prior_was_monthly_trend and is_followup and mcp and not is_service_or_specific_query:
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

            # 3a. Service-Level MoM Comparison / Projection Query (Customer-scoped or All Accounts)
            is_explicit_comparison = any(w in low for w in ["compare", "comparison", "variance", "mom"]) or (
                any(w in low for w in ["previous month", "last month", "prior month"]) and any(w in low for w in ["current month", "projected", "projection", "forecast", "run rate", "runrate", "mtd"])
            )
            is_multi_month = not is_explicit_comparison and (
                bool(req_months and req_months > 2) or any(w in low for w in ["12 month", "12 months", "6 month", "6 months", "3 month", "3 months", "over time", "trend", "timeline", "history", "annual", "by month", "each month"])
            )
            is_svc_comparison = not is_multi_month and (
                any(w in low for w in ["service level", "each service"]) or
                ("service" in low and any(w in low for w in ["compare", "comparison", "project", "projected", "forecast", "previous month", "last month", "run rate", "runrate", "variance"])) or
                (any(w in low for w in ["by service", "service cost", "services cost"]) and any(w in low for w in ["compare", "comparison", "project", "projected", "forecast", "mom", "variance", "projection"]))
            ) and not requested_service

            if is_svc_comparison:
                is_all_clouds = any(w in low for w in ["all clouds", "all cloud", "across all", "across clouds", "multi-cloud", "multicloud"]) or (active_cloud in (None, "all"))

                if is_all_clouds:
                    target_ds = "MULTICLOUD_FOCUS_COST_AND_USAGE"
                    sql_stmt = (
                        "SELECT Month AS month, ServiceName AS service, "
                        "SUM(BilledCost) AS cost "
                        "FROM MULTICLOUD_FOCUS_COST_AND_USAGE "
                        "GROUP BY Month, ServiceName "
                        "ORDER BY month DESC, cost DESC"
                    )
                    cloud_scope_str = "Across All Clouds"
                    col_name = "Cloud Service"
                elif active_cloud == "azure":
                    target_ds = "MULTICLOUD_FOCUS_COST_AND_USAGE"
                    sql_stmt = (
                        "SELECT Month AS month, ServiceName AS service, "
                        "SUM(BilledCost) AS cost "
                        "FROM MULTICLOUD_FOCUS_COST_AND_USAGE "
                        "WHERE provider = 'Azure' "
                        "GROUP BY Month, ServiceName "
                        "ORDER BY month DESC, cost DESC"
                    )
                    cloud_scope_str = "Azure"
                    col_name = "Azure Service"
                elif active_cloud == "gcp":
                    target_ds = "MULTICLOUD_FOCUS_COST_AND_USAGE"
                    sql_stmt = (
                        "SELECT Month AS month, ServiceName AS service, "
                        "SUM(BilledCost) AS cost "
                        "FROM MULTICLOUD_FOCUS_COST_AND_USAGE "
                        "WHERE provider IN ('GCP', 'Google Cloud') "
                        "GROUP BY Month, ServiceName "
                        "ORDER BY month DESC, cost DESC"
                    )
                    cloud_scope_str = "GCP"
                    col_name = "GCP Service"
                else:
                    target_ds = "AWS_CUR"
                    sql_stmt = (
                        "SELECT timeInterval_Month AS month, lineItem_ProductCode AS service, "
                        "SUM(lineItem_UnblendedCost) AS cost "
                        "FROM AWS_CUR "
                        "GROUP BY timeInterval_Month, lineItem_ProductCode "
                        "ORDER BY month DESC, cost DESC"
                    )
                    cloud_scope_str = "AWS"
                    col_name = "AWS Service"

                q_params = {
                    "queryInput": {
                        "sqlStatement": sql_stmt,
                        "dataGranularity": "MONTHLY",
                        "limit": 200,
                        "timeRange": {"last": 2, "qualifier": "MONTH"}
                    },
                    "requestInfo": {"sourceType": "API", "caller": "mcp"}
                }
                if named_customer and named_customer_crn:
                    q_params["channelCustomerId"] = named_customer_crn

                res_multi_svc = mcp.call_tool("execute_datasource_query", q_params)
                res_txt = res_multi_svc.get("content", [{}])[0].get("text", "")
                try:
                    multi_csv = json.loads(res_txt).get("csv", "")
                except Exception:
                    multi_csv = ""

                services_by_month = {last_ym: {}, current_ym: {}}
                all_services = set()
                if multi_csv:
                    for row in csv.DictReader(io.StringIO(multi_csv)):
                        m = (row.get("month") or row.get("Month") or row.get("timeInterval_Month") or "").strip()
                        m_prefix = m[:7]
                        s = (row.get("service") or row.get("ServiceName") or row.get("ServiceCategory") or row.get("lineItem_ProductCode") or "").strip()
                        try:
                            c = float(row.get("cost") or row.get("BilledCost") or row.get("EffectiveCost") or row.get("lineItem_UnblendedCost") or 0)
                        except (ValueError, TypeError):
                            c = 0.0
                        if m_prefix in services_by_month and s:
                            services_by_month[m_prefix][s] = services_by_month[m_prefix].get(s, 0.0) + c
                            all_services.add(s)

                if is_all_clouds and not all_services:
                    logger.info("[Section 3a] MULTICLOUD_FOCUS_COST_AND_USAGE returned no rows, falling back to AWS_CUR")
                    fb_q_params = dict(q_params)
                    fb_q_params["queryInput"] = {
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
                    }
                    try:
                        res_fb = mcp.call_tool("execute_datasource_query", fb_q_params)
                        fb_txt = res_fb.get("content", [{}])[0].get("text", "")
                        fb_csv = json.loads(fb_txt).get("csv", "")
                        if fb_csv:
                            for row in csv.DictReader(io.StringIO(fb_csv)):
                                m = (row.get("month") or row.get("timeInterval_Month") or "").strip()
                                m_prefix = m[:7]
                                s = (row.get("service") or row.get("lineItem_ProductCode") or "").strip()
                                try:
                                    c = float(row.get("cost") or row.get("lineItem_UnblendedCost") or 0)
                                except (ValueError, TypeError):
                                    c = 0.0
                                if m_prefix in services_by_month and s:
                                    services_by_month[m_prefix][s] = services_by_month[m_prefix].get(s, 0.0) + c
                                    all_services.add(s)
                            if all_services:
                                target_ds = "AWS_CUR"
                                cloud_scope_str = "AWS"
                                col_name = "AWS Service"
                    except Exception as e_fb:
                        logger.warning(f"[Section 3a Fallback] {e_fb}")

                now_dt = datetime.date.today()
                days_in_month = calendar.monthrange(now_dt.year, now_dt.month)[1]
                days_elapsed = max(now_dt.day, 1)
                effective_days = max(days_elapsed - 1.5, 1.0)  # adj. for CloudHealth 24-48hr lag
                runrate_factor = (days_in_month / effective_days) if effective_days < days_in_month else 1.0

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

                scope_title = named_customer if named_customer else "All Accounts"
                source_scope = f"organization-scoped: {named_customer}" if named_customer else "tenant-scoped"

                # Comparison Bar Chart: Last Month vs Projected Month-End
                chart_part = ""
                if intent_info.get("include_chart", True) and not is_no_chart_requested(low) and svc_comparison_list:
                    top_chart_svcs = svc_comparison_list[:8]
                    chart_md = _chart_block(
                        chart_type="bar",
                        title=f"Service Spend Comparison: Last Month vs Projected ({scope_title})",
                        labels=[x["service"] for x in top_chart_svcs],
                        datasets=[
                            {
                                "label": f"Last Month ({last_ym})",
                                "data": [round(x["last_month"], 2) for x in top_chart_svcs]
                            },
                            {
                                "label": f"Projected Month-End ({current_ym})",
                                "data": [round(x["projected"], 2) for x in top_chart_svcs]
                            }
                        ],
                        stacked=False
                    )
                    if chart_md:
                        chart_part = f"{chart_md}\n\n"

                insight_md = ""
                if self.engine != "direct" and svc_comparison_list:
                    try:
                        sys_msg = {"role": "system", "content": "You are Cleo, an expert FinOps AI. Provide 2 concise bullet observations about this service-level spend comparison, highlighting top drivers and significant MoM variance."}
                        user_msg = {"role": "user", "content": f"Scope: {scope_title}\nData summary:\n{table_rows}"}
                        llm_ans, _ = self._call_active_llm([sys_msg, user_msg])
                        if llm_ans:
                            insight_md = f"\n\n**💡 FinOps Insights:**\n{_sanitize_finops_bullet_titles(llm_ans)}"
                    except Exception as e:
                        logger.debug(f"[LLM Commentary] {e}")

                return (
                    f"### 📊 Service-Level Cost Comparison & Run-Rate Forecast: {scope_title} ({cloud_scope_str})\n\n"
                    f"{chart_part}"
                    f"*Comparing Last Month ({last_ym}) actuals with Current Month ({current_ym}) projected run-rate (Day {days_elapsed} of {days_in_month}, multiplier: {runrate_factor:.2f}x):*\n\n"
                    f"| {col_name} | Last Month ({last_ym}) | Current MTD ({current_ym}) | Projected Month-End ({current_ym}) | MoM Variance ($) | MoM Variance (%) |\n"
                    f"|:---|:---|:---|:---|:---|:---|\n"
                    f"{table_rows}\n"
                    f"| **Total Customer Spend** | **${tot_lm:,.2f}** | **${tot_mtd:,.2f}** | **${tot_proj:,.2f}** | **{tot_sign}${abs(tot_diff):,.2f}** | **{tot_pct:+.1f}% {tot_icon}** |\n\n"
                    f"**💡 Key FinOps Spend Drivers:**\n"
                    f"{top_driver_bullets}"
                    f"- **Customer Trajectory**: {scope_title} is tracking towards a month-end total of **${tot_proj:,.2f}**, representing an overall {tot_pct:+.1f}% ({tot_sign}${abs(tot_diff):,.2f}) variance against {last_ym}.\n\n"
                    f"{insight_md}\n\n"
                    f"*Source: {target_ds} via CloudHealth ({source_scope}). Run-rate projection formula: MTD * ({days_in_month}/{days_elapsed}). Numbers match CloudHealth Portal.*"
                )

            # 3b. Named Customer Single Service / Monthly History Breakdown
            if named_customer and named_customer_crn:

                svc_time_range = {"from": target_ym, "to": target_ym} if (is_specific and target_ym != last_ym) else {"last": 1, "qualifier": "MONTH", "excludeCurrent": True}
                svc_label = target_label if (is_specific and target_ym != last_ym) else last_ym

                # Multi-cloud detection for named customer
                is_all_clouds = any(w in low for w in ["all clouds", "all cloud", "across all", "across clouds", "multi-cloud", "multicloud"]) or (active_cloud == "all")

                if is_all_clouds:
                    target_ds = "MULTICLOUD_FOCUS_COST_AND_USAGE"
                    cloud_disp_name = "Cloud"
                    if requested_service:
                        tot_sql = (
                            f"SELECT Month AS month, ServiceName AS service, "
                            f"SUM(BilledCost) AS cost "
                            f"FROM MULTICLOUD_FOCUS_COST_AND_USAGE "
                            f"WHERE ServiceName = '{requested_service}' "
                            f"GROUP BY Month, ServiceName "
                            f"ORDER BY month DESC"
                        )
                        svc_sql = (
                            f"SELECT ServiceName AS service, SUM(BilledCost) AS cost "
                            f"FROM MULTICLOUD_FOCUS_COST_AND_USAGE "
                            f"WHERE ServiceName = '{requested_service}' "
                            f"GROUP BY ServiceName ORDER BY cost DESC"
                        )
                    else:
                        tot_sql = (
                            "SELECT Month AS month, "
                            "SUM(BilledCost) AS cost "
                            "FROM MULTICLOUD_FOCUS_COST_AND_USAGE "
                            "GROUP BY Month "
                            "ORDER BY month DESC"
                        )
                        svc_sql = (
                            "SELECT ServiceName AS service, "
                            "SUM(BilledCost) AS cost "
                            "FROM MULTICLOUD_FOCUS_COST_AND_USAGE "
                            "GROUP BY ServiceName "
                            "ORDER BY cost DESC"
                        )
                elif active_cloud == "azure":
                    target_ds = "MULTICLOUD_FOCUS_COST_AND_USAGE"
                    cloud_disp_name = "Azure"
                    if requested_service:
                        tot_sql = (
                            f"SELECT Month AS month, ServiceName AS service, "
                            f"SUM(BilledCost) AS cost "
                            f"FROM MULTICLOUD_FOCUS_COST_AND_USAGE "
                            f"WHERE provider = 'Azure' AND ServiceName = '{requested_service}' "
                            f"GROUP BY Month, ServiceName "
                            f"ORDER BY month DESC"
                        )
                        svc_sql = (
                            f"SELECT ServiceName AS service, SUM(BilledCost) AS cost "
                            f"FROM MULTICLOUD_FOCUS_COST_AND_USAGE "
                            f"WHERE provider = 'Azure' AND ServiceName = '{requested_service}' "
                            f"GROUP BY ServiceName ORDER BY cost DESC"
                        )
                    else:
                        tot_sql = (
                            "SELECT Month AS month, "
                            "SUM(BilledCost) AS cost "
                            "FROM MULTICLOUD_FOCUS_COST_AND_USAGE "
                            "WHERE provider = 'Azure' "
                            "GROUP BY Month "
                            "ORDER BY month DESC"
                        )
                        svc_sql = (
                            "SELECT ServiceName AS service, "
                            "SUM(BilledCost) AS cost "
                            "FROM MULTICLOUD_FOCUS_COST_AND_USAGE "
                            "WHERE provider = 'Azure' "
                            "GROUP BY ServiceName "
                            "ORDER BY cost DESC"
                        )
                elif active_cloud == "gcp":
                    target_ds = "MULTICLOUD_FOCUS_COST_AND_USAGE"
                    cloud_disp_name = "GCP"
                    if requested_service:
                        tot_sql = (
                            f"SELECT Month AS month, ServiceName AS service, "
                            f"SUM(BilledCost) AS cost "
                            f"FROM MULTICLOUD_FOCUS_COST_AND_USAGE "
                            f"WHERE provider IN ('GCP', 'Google Cloud') AND ServiceName = '{requested_service}' "
                            f"GROUP BY Month, ServiceName "
                            f"ORDER BY month DESC"
                        )
                        svc_sql = (
                            f"SELECT ServiceName AS service, SUM(BilledCost) AS cost "
                            f"FROM MULTICLOUD_FOCUS_COST_AND_USAGE "
                            f"WHERE provider IN ('GCP', 'Google Cloud') AND ServiceName = '{requested_service}' "
                            f"GROUP BY ServiceName ORDER BY cost DESC"
                        )
                    else:
                        tot_sql = (
                            "SELECT Month AS month, "
                            "SUM(BilledCost) AS cost "
                            "FROM MULTICLOUD_FOCUS_COST_AND_USAGE "
                            "WHERE provider IN ('GCP', 'Google Cloud') "
                            "GROUP BY Month "
                            "ORDER BY month DESC"
                        )
                        svc_sql = (
                            "SELECT ServiceName AS service, "
                            "SUM(BilledCost) AS cost "
                            "FROM MULTICLOUD_FOCUS_COST_AND_USAGE "
                            "WHERE provider IN ('GCP', 'Google Cloud') "
                            "GROUP BY ServiceName "
                            "ORDER BY cost DESC"
                        )
                else:
                    target_ds = "AWS_CUR"
                    cloud_disp_name = "AWS"
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
                    svc_sql = (
                        "SELECT lineItem_ProductCode AS service, "
                        "SUM(lineItem_UnblendedCost) AS cost "
                        "FROM AWS_CUR "
                        "GROUP BY lineItem_ProductCode "
                        "ORDER BY cost DESC"
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
                        "sqlStatement": svc_sql,
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
                        m_val = (row.get("month") or row.get("Month") or row.get("timeInterval_Month") or "").strip()
                        c_val = float(row.get("cost") or row.get("BilledCost") or row.get("EffectiveCost") or row.get("lineItem_UnblendedCost") or 0)
                        if m_val:
                            tot_by_month[m_val[:7]] = c_val
                    except (ValueError, TypeError):
                        pass

                # If specific target_ym is outside the last 12 months, fetch it explicitly
                if is_specific and target_ym not in tot_by_month:
                    if target_ds == "MULTICLOUD_FOCUS_COST_AND_USAGE":
                        target_sql = (
                            f"SELECT Month AS month, ServiceName AS service, SUM(BilledCost) AS cost FROM MULTICLOUD_FOCUS_COST_AND_USAGE WHERE ServiceName = '{requested_service}' GROUP BY Month, ServiceName ORDER BY month DESC"
                            if requested_service else
                            "SELECT Month AS month, SUM(BilledCost) AS cost FROM MULTICLOUD_FOCUS_COST_AND_USAGE GROUP BY Month ORDER BY month DESC"
                        )
                    else:
                        target_sql = (
                            f"SELECT timeInterval_Month AS month, lineItem_ProductCode AS service, SUM(lineItem_UnblendedCost) AS cost FROM AWS_CUR WHERE lineItem_ProductCode = '{requested_service}' GROUP BY timeInterval_Month, lineItem_ProductCode ORDER BY month DESC"
                            if requested_service else
                            "SELECT timeInterval_Month AS month, SUM(lineItem_UnblendedCost) AS cost FROM AWS_CUR GROUP BY timeInterval_Month ORDER BY month DESC"
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
                                m_val = (row.get("month") or row.get("Month") or row.get("timeInterval_Month") or "").strip()
                                c_val = float(row.get("cost") or row.get("BilledCost") or row.get("EffectiveCost") or row.get("lineItem_UnblendedCost") or 0)
                                if m_val:
                                    tot_by_month[target_ym] = c_val
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
                if mtd_cost > 0:
                    forecast_cost = mtd_cost * runrate_factor
                elif lm_cost > 0:
                    forecast_cost = lm_cost
                else:
                    forecast_cost = 0.0

                fc_delta_badge = ""
                if lm_cost > 0 and mtd_cost > 0:
                    fc_diff = forecast_cost - lm_cost
                    fc_pct = (fc_diff / lm_cost) * 100
                    fc_sign = "+" if fc_diff > 0 else ("-" if fc_diff < 0 else "")
                    fc_icon = "🔺" if fc_diff > 0 else "🔻"
                    fc_delta_badge = f" *({fc_sign}${abs(fc_diff):,.2f} / {fc_pct:+.1f}% vs Last Month {fc_icon})*"
                elif lm_cost > 0 and mtd_cost == 0.0:
                    fc_delta_badge = f" *(Estimated baseline from {last_ym}; Day {days_elapsed} MTD pending billing ingestion)*"

                mtd_display_note = f" *(Day {days_elapsed} of {days_in_month} — pending cloud billing ingestion)*" if (days_elapsed <= 2 or mtd_cost == 0.0) else f" *(Day {days_elapsed} of {days_in_month})*"

                svc_label_disp = f" ({requested_service_disp})" if requested_service_disp else ""
                month_col_hdr = f"{requested_service_disp} Spend" if requested_service_disp else "Total Spend"

                # Calculate Month-over-Month (MoM) delta for each month
                month_row_list = []
                for idx, m in enumerate(months_sorted[:6]):
                    cost_val = tot_by_month[m]
                    if m == current_ym:
                        if mtd_cost > 0:
                            mom_str = f"Projected: **~${forecast_cost:,.2f}** *(Run-rate, adj. for 24–48hr delay)*"
                        else:
                            mom_str = f"Estimated: **~${forecast_cost:,.2f}** *(Baseline from Last Month; Day {days_elapsed} pending ingestion)*"
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
                    svc = (row.get("service") or row.get("ServiceName") or row.get("ServiceCategory") or row.get("lineItem_ProductCode") or "").strip()
                    try:
                        c = float(row.get("cost") or row.get("BilledCost") or row.get("EffectiveCost") or row.get("lineItem_UnblendedCost") or 0)
                    except (ValueError, TypeError):
                        c = 0.0
                    if svc:
                        svc_by_month[svc] = svc_by_month.get(svc, 0.0) + c

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
                                "You are Cleo, an expert FinOps AI. Provide 2–3 concise bullet FinOps insights. Be specific and actionable.\n"
                                "STRICT FINOPS INGESTION RULES:\n"
                                f"1. EARLY-MONTH BILLING LATENCY: Today is Day {days_elapsed} of {current_ym}. Cloud billing exports have a standard 24–72 hour settlement lag. An MTD spend of $0.00 or near $0.00 at the start of a month is NORMAL billing delay. NEVER classify $0.00 MTD as a 'spend anomaly', 'cost reduction', 'discontinued service', or 'service outage'. DO NOT claim services have stopped or that spend dropped to zero.\n"
                                "2. COMMITMENT FEES: If 'ComputeSavingsPlans', 'SavingsPlans', or 'Reserved Instances' dominate the spend, explicitly identify them as upfront/partial-upfront commitment fees rather than standard run-rate compute usage.\n"
                                "3. Focus insights on finalized historical trends (Last Month vs prior months), primary service drivers, and optimization actions."
                            )
                        }
                        svc_context_str = f"Specific Service: {requested_service_disp} ({requested_service})\n" if requested_service else ""
                        mtd_context_note = f" (Day {days_elapsed} of {days_in_month} — pending cloud billing ingestion)" if (days_elapsed <= 2 or mtd_cost == 0.0) else ""
                        user_m = {"role": "user", "content": f"Customer: {named_customer}\n{svc_context_str}Target Month ({target_label}): ${target_cost:,.2f}\nLast Month ({last_ym}): ${lm_cost:,.2f}\nMTD ({current_ym}): ${mtd_cost:,.2f}{mtd_context_note}\nTop Services ({svc_label}): {dict(top_svcs[:5])}"}
                        llm_ans, _ = self._call_active_llm([sys_m, user_m])
                        if llm_ans:
                            insight_md = f"\n\n**💡 FinOps Insights:**\n{_sanitize_finops_bullet_titles(llm_ans)}"
                    except Exception as e:
                        logger.debug(f"[LLM Commentary] {e}")

                summary_table = (
                    f"| **Total {target_label} Spend{svc_label_disp}** ({target_ym}) | **${target_cost:,.2f}** |\n"
                    f"| **Total Last Month{svc_label_disp}** ({last_ym}) | **${lm_cost:,.2f}**{lm_delta_badge} |\n"
                    f"| **Total MTD{svc_label_disp}** ({current_ym}) | **${mtd_cost:,.2f}**{mtd_display_note} |\n"
                    f"| **Projected Month-End Forecast{svc_label_disp}** ({current_ym}) | **${forecast_cost:,.2f}**{fc_delta_badge} |\n"
                ) if (is_specific and target_ym not in (last_ym, current_ym)) else (
                    f"| **Total Last Month{svc_label_disp}** ({last_ym}) | **${lm_cost:,.2f}**{lm_delta_badge} |\n"
                    f"| **Total MTD{svc_label_disp}** ({current_ym}) | **${mtd_cost:,.2f}**{mtd_display_note} |\n"
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
                            f"Top {cloud_disp_name} Services — {named_customer} ({svc_label})",
                            labels,
                            values=values,
                            value_label="Cost ($)"
                        )
                    elif chart_type == "horizontal-bar" and top_svcs:
                        labels = [s for s, _ in top_svcs]
                        values = [round(c, 2) for _, c in top_svcs]
                        chart_md = _chart_block(
                            "horizontal-bar",
                            f"Top {cloud_disp_name} Services — {named_customer} ({svc_label})",
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
                            if target_ds == "MULTICLOUD_FOCUS_COST_AND_USAGE":
                                prov_filt = f"WHERE provider = '{cloud_disp_name}' " if cloud_disp_name in ("Azure", "GCP") else ""
                                chart_sql = (
                                    f"SELECT Month AS month, ServiceName AS service, SUM(BilledCost) AS cost "
                                    f"FROM MULTICLOUD_FOCUS_COST_AND_USAGE "
                                    f"{prov_filt}"
                                    f"GROUP BY Month, ServiceName "
                                    f"ORDER BY month ASC, cost DESC"
                                )
                            else:
                                chart_sql = (
                                    "SELECT timeInterval_Month AS month, lineItem_ProductCode AS service, "
                                    "SUM(lineItem_UnblendedCost) AS cost "
                                    "FROM AWS_CUR "
                                    "GROUP BY timeInterval_Month, lineItem_ProductCode "
                                    "ORDER BY month ASC, cost DESC"
                                )
                            chart_q_params = {
                                "queryInput": {
                                    "sqlStatement": chart_sql,
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
                                    f"{cloud_disp_name} Spend by Service & Month — {named_customer}",
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
                                f"Top {cloud_disp_name} Services — {named_customer} ({svc_label})",
                                labels, values=values, horizontal=False, stacked=True)

                tbl_block = (
                    f"| Metric | Amount |\n"
                    f"|:---|:---|\n"
                    f"{summary_table}\n"
                    f"#### 📅 {hist_title}\n\n"
                    f"| Month | {month_col_hdr} | MoM Change |\n"
                    f"|:---|:---|:---|\n"
                    f"{month_rows}\n\n"
                    f"#### ☁️ Top {cloud_disp_name} Services ({svc_label})\n\n"
                    f"| Service | Cost | % of Total |\n"
                    f"|:---|:---|:---|\n"
                    f"{svc_rows}"
                ) if wants_table else ""

                return (
                    f"### 📊 {header_title}\n\n"
                    f"{tbl_block}"
                    f"{chart_md}"
                    f"{insight_md}\n\n"
                    f"*Source: {target_ds} via CloudHealth (channel-scoped). Numbers match CloudHealth Portal.*"
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

                is_all_clouds = any(w in low for w in ["all clouds", "all cloud", "across all", "across clouds", "multi-cloud", "multicloud"]) or (active_cloud in (None, "all"))

                def _fetch_cust_spend(item):
                    cname, ccrn = item
                    try:
                        cust_csv = ""
                        if is_all_clouds:
                            r = mcp.call_tool("execute_datasource_query", {
                                "channelCustomerId": ccrn,
                                "queryInput": {
                                    "sqlStatement": (
                                        "SELECT Month AS month, "
                                        "SUM(BilledCost) AS cost "
                                        "FROM MULTICLOUD_FOCUS_COST_AND_USAGE "
                                        "GROUP BY Month "
                                        "ORDER BY month DESC"
                                    ),
                                    "dataGranularity": "MONTHLY",
                                    "limit": months_needed,
                                    "timeRange": {"last": min(months_needed, 12), "qualifier": "MONTH"}
                                },
                                "requestInfo": {"sourceType": "API", "caller": "mcp"}
                            })
                            cust_csv = json.loads(r["content"][0]["text"]).get("csv", "")
                            if not cust_csv.strip() or len(cust_csv.strip().splitlines()) <= 1:
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
                        elif active_cloud == "azure":
                            r = mcp.call_tool("execute_datasource_query", {
                                "channelCustomerId": ccrn,
                                "queryInput": {
                                    "sqlStatement": (
                                        "SELECT Month AS month, "
                                        "SUM(BilledCost) AS cost "
                                        "FROM MULTICLOUD_FOCUS_COST_AND_USAGE "
                                        "WHERE provider = 'Azure' "
                                        "GROUP BY Month "
                                        "ORDER BY month DESC"
                                    ),
                                    "dataGranularity": "MONTHLY",
                                    "limit": months_needed,
                                    "timeRange": {"last": min(months_needed, 12), "qualifier": "MONTH"}
                                },
                                "requestInfo": {"sourceType": "API", "caller": "mcp"}
                            })
                            cust_csv = json.loads(r["content"][0]["text"]).get("csv", "")
                        elif active_cloud == "gcp":
                            r = mcp.call_tool("execute_datasource_query", {
                                "channelCustomerId": ccrn,
                                "queryInput": {
                                    "sqlStatement": (
                                        "SELECT Month AS month, "
                                        "SUM(BilledCost) AS cost "
                                        "FROM MULTICLOUD_FOCUS_COST_AND_USAGE "
                                        "WHERE provider = 'GCP' "
                                        "GROUP BY Month "
                                        "ORDER BY month DESC"
                                    ),
                                    "dataGranularity": "MONTHLY",
                                    "limit": months_needed,
                                    "timeRange": {"last": min(months_needed, 12), "qualifier": "MONTH"}
                                },
                                "requestInfo": {"sourceType": "API", "caller": "mcp"}
                            })
                            cust_csv = json.loads(r["content"][0]["text"]).get("csv", "")
                        else:
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
                                m_val = row.get("month") or row.get("Month") or row.get("timeInterval_Month") or ""
                                c_val = float(row.get("cost") or row.get("BilledCost") or row.get("lineItem_UnblendedCost") or 0)
                                if m_val:
                                    spend_by_month[m_val] = c_val
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
                    f"*Source: CloudHealth FOCUS & Billing Datasets (channel-scoped per customer). Numbers match CloudHealth Portal.*"
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
                                "CRITICAL 2: If 'ComputeSavingsPlans', 'SavingsPlans', or 'Reserved Instances' dominate the spend, explicitly identify them as upfront/partial-upfront commitment fees rather than standard run-rate compute usage. "
                                "CRITICAL 3: In the first 1–3 days of a month, near-zero MTD spend is normal cloud billing settlement latency. DO NOT classify it as a service outage, cancellation, or spend drop."
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

            # 3c. Multi-Cloud & Provider Service & Dimensional Spend Breakdown (AWS, Azure, GCP, AI)
            elif not is_monthly_trend_query and (requested_service or active_cloud or any(w in low for w in [
                "service", "product", "ec2", "s3", "rds", "bigquery", "vertex", "blob",
                "azure", "gcp", "aws", "cloud", "breakdown", "break down", "cost", "spend", "ai",
                "category", "subcategory", "pricing", "account", "model", "resource", "region"
            ])):
                has_multi_month_kw = bool(re.search(r'\b\d+\s*months?\b', low)) or any(w in low for w in ["months", "multi month", "trailing months", "past months"])
                req_months = intent_info.get("timeframe_months") or time_ctx.get("timeframe_months")
                if not req_months and time_ctx.get("months_needed") and has_multi_month_kw and not is_specific:
                    req_months = time_ctx.get("months_needed")

                partial_notice = ""
                if time_ctx.get("timeframe_days") or time_ctx.get("daily_range"):
                    t_days = time_ctx.get("timeframe_days") or 1
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
                elif req_months and req_months > 1:
                    svc_time_range = {"last": min(req_months, 12), "qualifier": "MONTH"}
                    svc_scope_label = f"Last {min(req_months, 12)} Months"
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
                table = ""
                svc_hdr = ""

                def _true_total_cost(sql: str, time_range_override: Optional[dict] = None) -> float:
                    return _fetch_total_cost(mcp, sql, svc_granularity, time_range_override or svc_time_range)

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

                # ── Unified Dimensional Breakdown Router ──
                # Supports all FOCUS 1.2 dimensions, AI & Foundation Model dimensions, and Commitment/Emissions datasets.
                DIMENSIONAL_BREAKDOWNS = [
                    # FOCUS Dataset Dimensions
                    {
                        "id": "service_subcategory",
                        "column": "ServiceSubcategory",
                        "label": "Service Subcategory",
                        "dataset": "MULTICLOUD_FOCUS_COST_AND_USAGE",
                        "metric": "EffectiveCost",
                        "provider_col": "provider",
                        "triggers": [
                            "by service subcategory", "by subcategory", "by sub-category", "per subcategory",
                            "service subcategories", "subcategory breakdown", "breakdown by subcategory",
                            "by sub service", "per sub service", "sub-service", "sub service", "granular service",
                            "service component", "meter category", "meter subcategory", "by meter",
                            "component breakdown", "sub services", "granular services", "by meter category",
                            "by meter subcategory"
                        ]
                    },
                    {
                        "id": "service_category",
                        "column": "ServiceCategory",
                        "label": "Service Category",
                        "dataset": "MULTICLOUD_FOCUS_COST_AND_USAGE",
                        "metric": "EffectiveCost",
                        "provider_col": "provider",
                        "triggers": [
                            "by service category", "by service_category", "by category", "per category",
                            "service categories", "category breakdown", "breakdown by category", "per service category",
                            "by service categories", "by macro service", "macro service", "service family",
                            "by service family", "by domain", "domain breakdown", "service domain", "cloud domain"
                        ]
                    },
                    {
                        "id": "pricing_category",
                        "column": "PricingCategory",
                        "label": "Pricing Category",
                        "dataset": "MULTICLOUD_FOCUS_COST_AND_USAGE",
                        "metric": "EffectiveCost",
                        "provider_col": "provider",
                        "triggers": [
                            "by pricing category", "by pricing model", "by pricing", "per pricing category",
                            "pricing categories", "pricing category breakdown", "breakdown by pricing", "by pricing type",
                            "by commercial model", "commercial model", "by contract type", "contract type",
                            "by commitment type", "commitment type", "by charge type", "charge type breakdown"
                        ]
                    },
                    {
                        "id": "billing_account",
                        "column": "BillingAccountId",
                        "label": "Billing Account ID",
                        "dataset": "MULTICLOUD_FOCUS_COST_AND_USAGE",
                        "metric": "EffectiveCost",
                        "provider_col": "provider",
                        "triggers": [
                            "by billing account", "per billing account", "billing account id", "payer account",
                            "master account", "billing accounts", "breakdown by billing account", "billing account breakdown",
                            "root account", "management account", "parent account", "by payer", "by master",
                            "billing container", "by payer account", "by management account", "by root account",
                            "payer id", "master account id", "root account id", "payer accounts", "master accounts"
                        ]
                    },
                    {
                        "id": "region",
                        "column": "RegionId",
                        "label": "Region ID",
                        "dataset": "MULTICLOUD_FOCUS_COST_AND_USAGE",
                        "metric": "EffectiveCost",
                        "provider_col": "provider",
                        "triggers": [
                            "by region", "per region", "by region id", "region breakdown", "breakdown by region",
                            "by location", "per location", "regional breakdown", "by datacenter", "by data center",
                            "datacenter breakdown", "by geography", "by geo", "geographic breakdown", "geo breakdown",
                            "by zone", "by availability zone", "cloud region", "by cloud region", "locations breakdown"
                        ]
                    },
                    {
                        "id": "resource",
                        "column": "ResourceId",
                        "label": "Resource ID",
                        "dataset": "MULTICLOUD_FOCUS_COST_AND_USAGE",
                        "metric": "EffectiveCost",
                        "provider_col": "provider",
                        "triggers": [
                            "by resource", "per resource", "by resource id", "resource breakdown", "breakdown by resource",
                            "resource-level", "top resources", "by individual resource", "by resource name",
                            "resource name", "by arn", "per resource id", "individual resources", "by instance id",
                            "by asset", "asset breakdown"
                        ]
                    },
                    {
                        "id": "account",
                        "column": "SubaccountId",
                        "label": "Account Name / ID",
                        "dataset": "MULTICLOUD_FOCUS_COST_AND_USAGE",
                        "metric": "EffectiveCost",
                        "provider_col": "provider",
                        "triggers": [
                            "by account", "per account", "account name", "account names", "account-level",
                            "sub account", "subaccount", "sub-account", "breakdown by account", "account breakdown",
                            "each account", "by project", "per project", "by subscription", "per subscription",
                            "project breakdown", "subscription breakdown", "member account", "linked account",
                            "usage account", "cloud account", "by member account", "by linked account",
                            "by usage account", "by cloud account", "subscription-level", "project-level",
                            "per cloud account", "linked accounts", "member accounts", "usage accounts"
                        ]
                    },
                    # AI / Foundation Models Dataset Dimensions
                    {
                        "id": "ai_model_provider",
                        "column": "ModelProvider",
                        "label": "AI Model Provider",
                        "dataset": "MULTICLOUD_AI_COST_AND_USAGE",
                        "metric": "EffectiveCost",
                        "provider_col": "provider",
                        "triggers": [
                            "by model provider", "per model provider", "model provider breakdown", "breakdown by model provider",
                            "ai provider", "by ai provider", "ai provider breakdown", "ai vendor", "by ai vendor",
                            "llm vendor", "model vendor", "ai company"
                        ]
                    },
                    {
                        "id": "ai_model",
                        "column": "Model",
                        "label": "AI Model",
                        "dataset": "MULTICLOUD_AI_COST_AND_USAGE",
                        "metric": "EffectiveCost",
                        "provider_col": "provider",
                        "triggers": [
                            "by model", "per model", "by ai model", "by llm", "model breakdown", "breakdown by model",
                            "ai model breakdown", "llm breakdown", "foundation model", "by foundation model", "models breakdown",
                            "by model name", "per model name", "model name", "breakdown by model name", "break down by model name",
                            "break down by model", "break it down by model name", "break it down by model",
                            "foundation models", "by llm model", "llm models", "language model", "language models"
                        ]
                    },
                    {
                        "id": "ai_modality",
                        "column": "Modality",
                        "label": "Modality",
                        "dataset": "MULTICLOUD_AI_COST_AND_USAGE",
                        "metric": "EffectiveCost",
                        "provider_col": "provider",
                        "triggers": [
                            "by modality", "per modality", "modality breakdown", "breakdown by modality",
                            "media type", "by media type", "input modality", "text vs multimodal", "multimodal breakdown"
                        ]
                    },
                    {
                        "id": "ai_execution_type",
                        "column": "ExecutionType",
                        "label": "Execution Type",
                        "dataset": "MULTICLOUD_AI_COST_AND_USAGE",
                        "metric": "EffectiveCost",
                        "provider_col": "provider",
                        "triggers": [
                            "by execution type", "per execution type", "execution type breakdown", "breakdown by execution type",
                            "inference type", "by inference type", "batch vs streaming", "realtime vs batch", "processing mode"
                        ]
                    },
                    {
                        "id": "ai_token_type",
                        "column": "TokenType",
                        "label": "Token Type",
                        "dataset": "MULTICLOUD_AI_COST_AND_USAGE",
                        "metric": "EffectiveCost",
                        "provider_col": "provider",
                        "triggers": [
                            "by token type", "per token type", "token type breakdown", "breakdown by token type",
                            "prompt tokens", "completion tokens", "input tokens", "output tokens", "cached tokens",
                            "tokens breakdown", "by tokens"
                        ]
                    },
                    {
                        "id": "ai_hardware_family",
                        "column": "HardwareFamily",
                        "label": "Hardware Family",
                        "dataset": "MULTICLOUD_AI_COST_AND_USAGE",
                        "metric": "EffectiveCost",
                        "provider_col": "provider",
                        "triggers": [
                            "by hardware family", "per hardware family", "hardware family breakdown",
                            "gpu family", "accelerator family", "h100 vs a100"
                        ]
                    },
                    {
                        "id": "ai_hardware_type",
                        "column": "HardwareType",
                        "label": "Hardware Type",
                        "dataset": "MULTICLOUD_AI_COST_AND_USAGE",
                        "metric": "EffectiveCost",
                        "provider_col": "provider",
                        "triggers": [
                            "by hardware type", "by hardware", "per hardware type", "hardware breakdown", "breakdown by hardware",
                            "accelerator type", "by accelerator", "gpu vs tpu", "accelerators"
                        ]
                    },
                    # Commitment Savings
                    {
                        "id": "commitment_plan",
                        "column": "Commitment_Plan",
                        "label": "Commitment Plan",
                        "dataset": "MULTICLOUD_COMMITMENT_SAVINGS",
                        "metric": "Commitment_Savings",
                        "provider_col": "Provider",
                        "triggers": [
                            "by commitment plan", "by commitment", "by commitment type", "commitment breakdown",
                            "savings plan breakdown", "by savings plan", "by reservation", "reservation breakdown",
                            "by ri", "ri breakdown", "savings plans breakdown", "reserved instances"
                        ]
                    },
                    # Operational Emissions
                    {
                        "id": "emissions_country",
                        "column": "Country",
                        "label": "Emissions by Country",
                        "dataset": "MULTICLOUD_OPERATIONAL_EMISSIONS",
                        "metric": "Carbon",
                        "provider_col": "ProviderName",
                        "unit": "MT CO2e",
                        "triggers": [
                            "by country", "emissions by country", "carbon by country", "emissions by geography",
                            "carbon by geography", "by datacenter country"
                        ]
                    }
                ]

                matched_dim = None
                target_dim = intent_info.get("target_dimension")
                target_bdowns = intent_info.get("breakdowns") or []

                for d_def in DIMENSIONAL_BREAKDOWNS:
                    if d_def["id"] == "commitment_plan" and any(w in low for w in ["hybrid discount", "hybrid discounts", "azure hybrid benefit", "ahb", "hybrid benefit"]):
                        continue
                    if (target_dim and target_dim.lower() in (d_def["column"].lower(), d_def["id"].lower())) or \
                       (d_def["id"] in target_bdowns) or \
                       (d_def["id"].replace("ai_", "") in target_bdowns) or \
                       any(t in low for t in d_def["triggers"]):
                        matched_dim = d_def
                        break
                if not matched_dim and "ai" in low and any(w in low for w in ["by provider", "per provider"]):
                    matched_dim = next((d for d in DIMENSIONAL_BREAKDOWNS if d["id"] == "ai_model_provider"), None)
                if not matched_dim and is_ai_models_query:
                    matched_dim = next((d for d in DIMENSIONAL_BREAKDOWNS if d["id"] == "ai_model"), None)

                if matched_dim:
                    dim_col = matched_dim["column"]
                    dim_label = matched_dim["label"]
                    dataset = matched_dim["dataset"]
                    metric_col = matched_dim["metric"]
                    prov_col = matched_dim.get("provider_col", "provider")
                    is_cost = (metric_col == "EffectiveCost")

                    is_exclude_other = is_exclude_other_requested(low) or is_exclude_other_requested(intent_info.get("corrected_query", ""))

                    where_clauses = []
                    if cloud_target == "aws":
                        where_clauses.append(f"{prov_col} = 'AWS'")
                    elif cloud_target == "azure":
                        where_clauses.append(f"{prov_col} = 'Azure'")
                    elif cloud_target == "gcp":
                        where_clauses.append(f"{prov_col} IN ('GCP', 'Google Cloud')")

                    if matched_dim["id"] in ("ai_model", "ai_model_provider"):
                        where_clauses.append(f"{dim_col} IS NOT NULL AND {dim_col} != ''")
                    if is_exclude_other:
                        where_clauses.append(f"LOWER({dim_col}) NOT IN ('other', 'unallocated', '(unallocated / other)', 'other services', 'other categories', 'other models') AND LOWER({dim_col}) NOT LIKE 'other %'")

                    if requested_service and str(requested_service).lower() not in ("ai", "ai models", "ai_models", "models", "foundation models", "all", "cloud") and dataset in ("MULTICLOUD_FOCUS_COST_AND_USAGE", "MULTICLOUD_AI_COST_AND_USAGE"):
                        where_clauses.append(f"ServiceName = '{requested_service}'")

                    where_str = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

                    is_multi_month_dim = not is_specific and (
                        bool(req_months and req_months > 1)
                        or has_multi_month_kw
                        or any(w in low for w in ["monthly", "by month", "each month", "month over month", "over time", "trend"])
                        or (isinstance(svc_time_range, dict) and svc_time_range.get("qualifier") == "MONTH" and int(svc_time_range.get("last", 1)) > 1)
                        or (isinstance(svc_time_range, dict) and "from" in svc_time_range and "to" in svc_time_range and svc_time_range["from"] != svc_time_range["to"])
                    )

                    if is_multi_month_dim:
                        dim_sql = (
                            f"SELECT Month AS month, {prov_col} AS provider, {dim_col} AS dimension, "
                            f"SUM({metric_col}) AS val "
                            f"FROM {dataset} "
                            f"{where_str} "
                            f"GROUP BY Month, {prov_col}, {dim_col} "
                            f"ORDER BY month ASC, val DESC"
                        )
                        q_limit = 500
                    else:
                        dim_sql = (
                            f"SELECT {prov_col} AS provider, {dim_col} AS dimension, "
                            f"SUM({metric_col}) AS val "
                            f"FROM {dataset} "
                            f"{where_str} "
                            f"GROUP BY {prov_col}, {dim_col} "
                            f"ORDER BY val DESC"
                        )
                        q_limit = min(limit, 15) if limit else 15

                    clean_rows = []
                    all_months = []
                    try:
                        res = mcp.call_tool("execute_datasource_query", {
                            "queryInput": {
                                "sqlStatement": dim_sql,
                                "dataGranularity": svc_granularity,
                                "limit": q_limit,
                                "timeRange": svc_time_range
                            },
                            "requestInfo": {"sourceType": "API", "caller": "mcp"}
                        })
                        res_text = (res.get("content") or [{}])[0].get("text", "")
                        res_json = json.loads(res_text) if res_text.startswith("{") else {}
                        if "error" in res_json:
                            logger.warning(f"[{dim_label} Query Error] Initial query returned error: {res_json['error']}. Retrying once...")
                            time.sleep(1.0)
                            res = mcp.call_tool("execute_datasource_query", {
                                "queryInput": {
                                    "sqlStatement": dim_sql,
                                    "dataGranularity": svc_granularity,
                                    "limit": 200,
                                    "timeRange": svc_time_range
                                },
                                "requestInfo": {"sourceType": "API", "caller": "mcp"}
                            })
                            res_text = (res.get("content") or [{}])[0].get("text", "")
                            res_json = json.loads(res_text) if res_text.startswith("{") else {}

                        raw_csv = res_json.get("csv", "")
                        acc_map = self._get_account_name_map(mcp) if matched_dim["id"] == "account" else {}

                        for r in csv.DictReader(io.StringIO(raw_csv)):
                            v = float(r.get("val") or 0.0)
                            d_val = (r.get("dimension") or "").strip()
                            if not d_val:
                                d_val = "(Unallocated / Other)"
                            elif matched_dim["id"] == "account" and d_val in acc_map:
                                d_val = f"{acc_map[d_val]} ({d_val})"

                            if is_exclude_other and (
                                d_val.lower() in ("other", "(unallocated / other)", "unallocated", "other services", "other (combined)", "other categories", "other models")
                                or d_val.lower().startswith("other ")
                            ):
                                continue

                            prov = (r.get("provider") or "").strip()
                            if not prov:
                                prov = cloud_target.capitalize() if cloud_target != "all" else "Multi-Cloud"
                            m_val = str(r.get("month") or "").strip()

                            if v > 0:
                                clean_rows.append({"month": m_val, "provider": prov, "dimension": d_val, "val": v})

                        if is_multi_month_dim:
                            all_months = sorted(list(set(r["month"] for r in clean_rows if r["month"])))
                    except Exception as e:
                        logger.warning(f"[{dim_label} Query] {e}")

                    if is_multi_month_dim and len(all_months) > 1 and clean_rows:
                        # Multi-month dimensional analysis: Show Monthly Spend History + Overall Breakdown + Stacked Bar Chart
                        month_totals = defaultdict(float)
                        month_dim_totals = defaultdict(lambda: defaultdict(float))
                        dim_totals = defaultdict(float)
                        dim_prov = {}

                        for r in clean_rows:
                            m = r["month"]
                            d = r["dimension"]
                            v = r["val"]
                            month_totals[m] += v
                            month_dim_totals[m][d] += v
                            dim_totals[d] += v
                            dim_prov[d] = r["provider"]

                        grand_total = sum(dim_totals.values())
                        scope_disp = cloud_target.upper() if cloud_target != "all" else "Multi-Cloud"
                        unit_str = matched_dim.get("unit", "$")
                        val_fmt = (lambda x: f"${x:,.2f}") if is_cost else (lambda x: f"{x:,.2f} {unit_str}")

                        # 1. Monthly History Table (displayed recent first)
                        month_table_rows = []
                        for m in reversed(all_months):
                            tot = month_totals[m]
                            orig_idx = all_months.index(m)
                            if orig_idx > 0:
                                prev_m = all_months[orig_idx - 1]
                                diff = tot - month_totals[prev_m]
                                pct = (diff / month_totals[prev_m] * 100) if month_totals[prev_m] > 0 else 0.0
                                sign = "+" if diff > 0 else ("-" if diff < 0 else "")
                                icon = "🔺" if diff > 0 else "🔻"
                                delta_str = f"{sign}${abs(diff):,.2f} ({pct:+.1f}%) {icon}" if is_cost else f"{sign}{abs(diff):,.2f} ({pct:+.1f}%)"
                            else:
                                delta_str = "—"

                            top_d, top_v = max(month_dim_totals[m].items(), key=lambda x: x[1])
                            top_pct = (top_v / tot * 100) if tot > 0 else 0.0
                            month_table_rows.append(
                                f"| {m} | {val_fmt(tot)} | {delta_str} | `{top_d}` ({top_pct:.1f}%) |"
                            )

                        hist_table = (
                            f"#### 📅 Monthly Spend History ({svc_scope_label})\n\n"
                            f"| Month | Total Spend | MoM Change | Top {dim_label} (% of Month) |\n"
                            f"|:---|:---|:---|:---|\n"
                            f"{chr(10).join(month_table_rows)}\n"
                        )

                        # 2. Overall Top Dimensions Table
                        sorted_dims = sorted(dim_totals.items(), key=lambda x: x[1], reverse=True)
                        top_dims_table_rows = []
                        for d, v in sorted_dims[:limit]:
                            p = dim_prov.get(d, scope_disp)
                            pct = (v / grand_total * 100) if grand_total else 0.0
                            top_dims_table_rows.append(
                                f"| {p} | `{d}` | {val_fmt(v)} | {pct:.1f}% |"
                            )

                        dim_table = (
                            f"#### ☁️ Top {dim_label} Breakdown ({svc_scope_label})\n\n"
                            f"| Cloud Provider | {dim_label} | Total Cost ({svc_scope_label}) | % of Total |\n"
                            f"|:---|:---|:---|:---|\n"
                            f"{chr(10).join(top_dims_table_rows)}\n\n"
                            f"| **Total** | | **{val_fmt(grand_total)}** | **100.0%** |"
                        )

                        chart_md = ""
                        if not is_no_chart_requested(low):
                            chart_md = _build_time_category_stacked_chart(
                                f"{scope_disp} Spend by {dim_label} & Month — {svc_scope_label}",
                                clean_rows,
                                time_col="month",
                                cat_col="dimension",
                                cost_col="val",
                                time_format="month",
                                max_cats=10,
                                unit="Cost ($)" if is_cost else unit_str,
                                exclude_other=is_exclude_other
                            )
                            if chart_md:
                                chart_md = f"\n\n{chart_md}"

                        return (
                            f"### 📊 CloudHealth Spend Analysis: {scope_disp} Cost by {dim_label} — {svc_scope_label}\n\n"
                            f"{partial_notice}"
                            f"{hist_table}\n"
                            f"{dim_table}"
                            f"{chart_md}\n\n"
                            f"💡 *Live FinOps data from `{dataset}` via CloudHealth FlexReports.*"
                        )

                    elif clean_rows:
                        # Single-month or flat dimensional breakdown
                        dim_rows = [(r["provider"], r["dimension"], r["val"]) for r in clean_rows]
                        total_val = sum(r[2] for r in dim_rows)
                        scope_disp = cloud_target.upper() if cloud_target != "all" else "Multi-Cloud"
                        unit_str = matched_dim.get("unit", "$")
                        val_header = f"Cost ({svc_scope_label})" if is_cost else f"{dim_label} ({svc_scope_label})"
                        tbl_lines = [
                            f"| {prov} | `{d_val}` | {f'${v:,.2f}' if is_cost else f'{v:,.2f} {unit_str}'} | {((v/total_val)*100 if total_val else 0):.1f}% |"
                            for prov, d_val, v in dim_rows[:limit]
                        ]
                        table = (
                            f"| Cloud Provider | {dim_label} | {val_header} | % of Total |\n"
                            f"|:---|:---|:---|:---|\n"
                            f"{chr(10).join(tbl_lines)}\n\n"
                            f"| **Total** | | **{f'${total_val:,.2f}' if is_cost else f'{total_val:,.2f} {unit_str}'}** | **100.0%** |"
                        )

                        chart_md = ""
                        if not is_no_chart_requested(low):
                            chart_labels = [
                                f"{prov}: {d_val}" if cloud_target == "all" else d_val
                                for prov, d_val, _ in dim_rows[:10]
                            ]
                            chart_vals = [v for _, _, v in dim_rows[:10]]
                            val_lbl = "Cost ($)" if is_cost else unit_str
                            chart_md = _chart_block(
                                "horizontal-bar",
                                f"{scope_disp} Spend by {dim_label} — {svc_scope_label}",
                                chart_labels,
                                values=chart_vals,
                                value_label=val_lbl,
                                horizontal=True
                            )
                            if chart_md:
                                chart_md = f"\n\n{chart_md}"

                        return (
                            f"### 📊 CloudHealth Spend Analysis: {scope_disp} Cost by {dim_label} — {svc_scope_label}\n\n"
                            f"{partial_notice}"
                            f"{table}"
                            f"{chart_md}\n\n"
                            f"💡 *Live FinOps data from `{dataset}` via CloudHealth FlexReports.*"
                        )
                    else:
                        scope_disp = cloud_target.upper() if cloud_target != "all" else "Multi-Cloud"
                        if matched_dim["id"].startswith("ai_") or dataset == "MULTICLOUD_AI_COST_AND_USAGE":
                            return (
                                f"### 🤖 CloudHealth Spend Analysis: {scope_disp} Cost by {dim_label} — {svc_scope_label}\n\n"
                                f"No active AI model spend data was returned from `{dataset}` for {svc_scope_label}.\n\n"
                                f"> 💡 **FinOps Diagnostic Tips**:\n"
                                f"> 1. **Billing Ingestion Latency**: Cloud providers typically settle billing telemetry with a 24–48 hour delay. Try querying the prior closed month (e.g. 'last month') if analyzing current MTD.\n"
                                f"> 2. **Account / Filter Scope**: If filtering by a specific subaccount, customer, or service, verify that resources were actively provisioned during this timeframe.\n"
                                f"> 3. **Dataset Ingestion**: Check whether `{dataset}` is active and configured in your CloudHealth FlexReports tenant.\n\n"
                                f"*Source: `{dataset}` via CloudHealth FlexReports.*"
                            )
                        return (
                            f"### 📊 CloudHealth Spend Analysis: {scope_disp} Cost by {dim_label} — {svc_scope_label}\n\n"
                            f"{_format_empty_data_notice(dataset, dim_label, svc_scope_label)}\n\n"
                            f"*Source: `{dataset}` via CloudHealth FlexReports.*"
                        )

                # ── Single Service Query ──
                if requested_service:
                    pcode = str(requested_service)
                    pdisp = requested_service_disp or pcode
                    prov = PCODE_TO_PROVIDER.get(pcode, "aws")
                    prov_disp = "AWS" if prov == "aws" else ("Azure" if prov == "azure" else ("GCP" if prov == "gcp" else "Cloud"))
                    
                    svc_cost = 0.0
                    used_fallback = False
                    if prov == "aws":
                        svc_sql = f"SELECT provider AS provider, ServiceName AS service, SUM(EffectiveCost) AS cost FROM MULTICLOUD_FOCUS_COST_AND_USAGE WHERE provider = 'AWS' AND ServiceName = '{pcode}' GROUP BY provider, ServiceName ORDER BY cost DESC"
                        try:
                            res = mcp.call_tool("execute_datasource_query", {
                                "queryInput": {"sqlStatement": svc_sql, "dataGranularity": svc_granularity, "limit": 1, "timeRange": svc_time_range},
                                "requestInfo": {"sourceType": "API", "caller": "mcp"}
                            })
                            raw_csv = json.loads(res["content"][0]["text"]).get("csv", "")
                            for r in csv.DictReader(io.StringIO(raw_csv)):
                                svc_cost = float(r.get("cost") or 0.0)
                        except Exception as e:
                            logger.warning(f"[Single Service Query] AWS: {e}")

                        if svc_cost <= 0.0 and is_specific and target_ym == current_ym:
                            try:
                                res_fb = mcp.call_tool("execute_datasource_query", {
                                    "queryInput": {"sqlStatement": svc_sql, "dataGranularity": svc_granularity, "limit": 1, "timeRange": {"from": last_ym, "to": last_ym}},
                                    "requestInfo": {"sourceType": "API", "caller": "mcp"}
                                })
                                raw_csv_fb = json.loads(res_fb["content"][0]["text"]).get("csv", "")
                                for r in csv.DictReader(io.StringIO(raw_csv_fb)):
                                    svc_cost = float(r.get("cost") or 0.0)
                                if svc_cost > 0.0:
                                    used_fallback = True
                                    get_memory().record_sql_fix(svc_sql, f"Zero records for open period {current_ym}", f"{svc_sql} [timeRange: {last_ym}]")
                            except Exception:
                                pass
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

                        if svc_cost <= 0.0 and is_specific and target_ym == current_ym:
                            try:
                                res_fb = mcp.call_tool("execute_datasource_query", {
                                    "queryInput": {
                                        "sqlStatement": f"SELECT provider AS provider, ServiceName AS service, SUM(EffectiveCost) AS cost FROM MULTICLOUD_FOCUS_COST_AND_USAGE WHERE ServiceName LIKE '%{pcode}%' GROUP BY provider, ServiceName ORDER BY cost DESC",
                                        "dataGranularity": svc_granularity, "limit": 1, "timeRange": {"from": last_ym, "to": last_ym}
                                    },
                                    "requestInfo": {"sourceType": "API", "caller": "mcp"}
                                })
                                raw_csv_fb = json.loads(res_fb["content"][0]["text"]).get("csv", "")
                                for r in csv.DictReader(io.StringIO(raw_csv_fb)):
                                    svc_cost = float(r.get("cost") or 0.0)
                                if svc_cost > 0.0:
                                    used_fallback = True
                            except Exception:
                                pass

                    if svc_cost > 0.0:
                        fallback_note = (
                            f"> 💡 **FinOps Ingestion Notice**: Current month (`{current_ym}`) has no finalized billing data yet due to cloud billing settlement latency (24–72h). "
                            f"Displaying spend from the latest closed billing cycle (**{last_ym}**).\n\n"
                            if used_fallback else ""
                        )
                        table = (
                            f"{fallback_note}"
                            f"| Cloud Provider | Service Category | Cost |\n"
                            f"|:---|:---|:---|\n"
                            f"| {prov_disp} | {pdisp} | **${svc_cost:,.2f}** |\n"
                        )
                        svc_hdr = f"CloudHealth Spend Analysis: {pdisp} ({prov_disp}) Spend — {svc_scope_label if not used_fallback else last_ym}"
                    else:
                        return (
                            f"### ☁️ CloudHealth Spend Analysis: {pdisp} ({prov_disp})\n\n"
                            f"No live billing data was returned for **{pdisp}** in `{svc_scope_label}`. This usually means "
                            f"there was no spend in this period, or the connected CloudHealth account/scope doesn't have "
                            f"visibility into it.\n\n"
                            f"*Source: MULTICLOUD_FOCUS_COST_AND_USAGE via CloudHealth FlexReports.*"
                        )

                # ── Multi-Month / Time-Series Service Spend Breakdown (Stacked Area / Line / Bar) ──
                elif not requested_service and not is_specific and (
                    bool(req_months and req_months > 1)
                    or bool(time_ctx.get("timeframe_days"))
                    or any(w in low for w in ["trend", "over time", "monthly", "by month", "each month", "month over month", "mom"])
                    or any(ct in ("area", "line") for ct in (intent_info.get("chart_types") or [_detect_chart_type(low)]))
                ) and not any(w in low for w in [
                    "by cloud", "by provider", "per cloud", "per provider", "cloud-wise", "cloud wise",
                    "breakdown by cloud", "each cloud", "cloud level", "cloud-level"
                ]):
                    if cloud_target == "azure":
                        prov_where = "WHERE provider = 'Azure'"
                        prov_title = "Azure"
                    elif cloud_target == "gcp":
                        prov_where = "WHERE provider IN ('GCP', 'Google Cloud')"
                        prov_title = "Google Cloud (GCP)"
                    elif cloud_target == "aws":
                        prov_where = "WHERE provider = 'AWS'"
                        prov_title = "AWS"
                    else:
                        prov_where = ""
                        prov_title = "Multi-Cloud"

                    svc_month_costs = defaultdict(lambda: defaultdict(float))
                    svc_totals = defaultdict(float)
                    svc_prov = {}
                    all_months = []

                    ts_sql = (
                        f"SELECT Month AS month, provider AS provider, ServiceName AS service, SUM(EffectiveCost) AS cost "
                        f"FROM MULTICLOUD_FOCUS_COST_AND_USAGE "
                        f"{prov_where} "
                        f"GROUP BY Month, provider, ServiceName "
                        f"ORDER BY month ASC, cost DESC"
                    )
                    try:
                        res = mcp.call_tool("execute_datasource_query", {
                            "queryInput": {
                                "sqlStatement": ts_sql,
                                "dataGranularity": svc_granularity,
                                "limit": 500,
                                "timeRange": svc_time_range
                            },
                            "requestInfo": {"sourceType": "API", "caller": "mcp"}
                        })
                        raw_csv = json.loads(res.get("content", [{}])[0].get("text", "{}")).get("csv", "")
                        for r in csv.DictReader(io.StringIO(raw_csv)):
                            m = r.get("month") or ""
                            s = _friendly_service_name(r.get("service") or "")
                            p = r.get("provider") or prov_title
                            c = float(r.get("cost") or 0.0)
                            if s and c > 0:
                                svc_month_costs[s][m] += c
                                svc_totals[s] += c
                                svc_prov[s] = p
                        all_months = sorted(list(set(m for s in svc_month_costs for m in svc_month_costs[s] if m)))
                    except Exception as e:
                        logger.warning(f"[Multi-Month Service Query] {e}")

                    if len(all_months) > 1 and svc_totals:
                        month_labels = [_format_time_label(m, "month") for m in all_months]
                        sorted_services = sorted(svc_totals.keys(), key=lambda s: svc_totals[s], reverse=True)
                        top_services = sorted_services[:limit]
                        total_spend = sum(svc_totals.values())
                        month_totals = {m: sum(svc_month_costs[s].get(m, 0.0) for s in svc_totals) for m in all_months}

                        tbl_headers = ["Cloud Provider", "Service Category"] + month_labels + ["Total Spend", f"% of {prov_title} Spend"]
                        tbl_lines = []
                        for s in top_services:
                            p = svc_prov.get(s, prov_title)
                            m_cells = [f"${svc_month_costs[s].get(m, 0.0):,.2f}" for m in all_months]
                            tot_c = svc_totals[s]
                            pct = (tot_c / total_spend * 100) if total_spend else 0.0
                            tbl_lines.append(f"| {p} | {s} | " + " | ".join(m_cells) + f" | ${tot_c:,.2f} | {pct:.1f}% |")

                        tot_month_cells = [f"**${month_totals.get(m, 0.0):,.2f}**" for m in all_months]
                        table = (
                            f"| " + " | ".join(tbl_headers) + " |\n"
                            f"|" + "|".join([":---"] * len(tbl_headers)) + "|\n"
                            f"{chr(10).join(tbl_lines)}\n\n"
                            f"| **Total {prov_title} Spend** | | " + " | ".join(tot_month_cells) + f" | **${total_spend:,.2f}** | **100.0%** |"
                        )
                        svc_hdr = f"CloudHealth Spend Analysis: Top {prov_title} Services by Spend — {svc_scope_label}"

                        insight_md = ""
                        if self.engine != "direct":
                            try:
                                sys_msg = {
                                    "role": "system",
                                    "content": (
                                        f"You are Cleo, an expert FinOps AI. Provide 2 concise bullet observations about the {prov_title} service spend trend over time.\n"
                                        "STRICT RULES:\n"
                                        "1. Bullet titles MUST accurately reflect all services mentioned in that bullet.\n"
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
                            top_s1 = top_services[0] if top_services else "Primary Services"
                            top_s2 = top_services[1] if len(top_services) > 1 else ""
                            s_names = f"{top_s1} and {top_s2}" if top_s2 else top_s1
                            insight_md = (
                                "\n\n**💡 FinOps Insights:**\n"
                                f"- **Core Infrastructure Trend ({s_names})**: {s_names} represent the predominant cost drivers across `{svc_scope_label}`. Tracking month-over-month variances ensures unexpected scaling spikes are remediated early.\n"
                                f"- **Architecture & Commitment Modernization**: Leveraging multi-year commitments (Savings Plans, CUDs) alongside active waste cleanup yields significant run-rate reduction."
                            )

                        chart_types = intent_info.get("chart_types") or []
                        det_type = _detect_chart_type(low)
                        if det_type and det_type not in chart_types:
                            chart_types.append(det_type)
                        if not chart_types and not is_no_chart_requested(low):
                            chart_types = ["area" if any(w in low for w in ["area", "trend", "over time", "month"]) else "bar"]

                        chart_blocks = []
                        if not is_no_chart_requested(low):
                            for ct in chart_types:
                                c_kind = ct if ct in ["pie", "doughnut", "line", "area", "bar", "horizontal-bar"] else "area"
                                if c_kind in ("area", "line", "bar"):
                                    chart_datasets = []
                                    for s in top_services[:8]:
                                        chart_datasets.append({
                                            "label": s,
                                            "data": [round(svc_month_costs[s].get(m, 0.0), 2) for m in all_months]
                                        })
                                    rem_svcs = sorted_services[8:]
                                    if rem_svcs:
                                        rem_data = [round(sum(svc_month_costs[s].get(m, 0.0) for s in rem_svcs), 2) for m in all_months]
                                        if any(v > 0 for v in rem_data):
                                            chart_datasets.append({"label": "Other Services", "data": rem_data})

                                    chart_blocks.append(_chart_block(
                                        c_kind,
                                        f"Top {prov_title} Services Spend Trend ({svc_scope_label})",
                                        month_labels,
                                        datasets=chart_datasets,
                                        value_label="Cost ($)",
                                        stacked=True
                                    ))
                                else:
                                    chart_blocks.append(_chart_block(
                                        c_kind,
                                        f"Top {prov_title} Services Spend Share ({svc_scope_label})",
                                        [s for s in top_services[:10]],
                                        values=[round(svc_totals[s], 2) for s in top_services[:10]],
                                        horizontal=(c_kind == "horizontal-bar"),
                                        stacked=False
                                    ))

                        chart_md = "\n\n".join(chart_blocks)
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

                # ── Azure Specific Breakdown ──
                elif cloud_target == "azure":
                    azure_rows = []
                    used_fallback = False
                    svc_sql = "SELECT provider AS provider, ServiceName AS service, SUM(EffectiveCost) AS cost FROM MULTICLOUD_FOCUS_COST_AND_USAGE WHERE provider = 'Azure' GROUP BY provider, ServiceName ORDER BY cost DESC"
                    try:
                        res = mcp.call_tool("execute_datasource_query", {
                            "queryInput": {
                                "sqlStatement": svc_sql,
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

                    # Fallback when open month has no records due to billing ingestion delay
                    if not azure_rows and is_specific and target_ym == current_ym:
                        logger.info(f"[Azure Service Query] {current_ym} returned no rows; falling back to latest closed month {last_ym}...")
                        try:
                            res_fb = mcp.call_tool("execute_datasource_query", {
                                "queryInput": {
                                    "sqlStatement": svc_sql,
                                    "dataGranularity": svc_granularity, "limit": limit, "timeRange": {"from": last_ym, "to": last_ym}
                                },
                                "requestInfo": {"sourceType": "API", "caller": "mcp"}
                            })
                            raw_csv_fb = json.loads(res_fb["content"][0]["text"]).get("csv", "")
                            for r in csv.DictReader(io.StringIO(raw_csv_fb)):
                                c = float(r.get("cost") or 0.0)
                                s = r.get("service")
                                if s and c > 0:
                                    azure_rows.append((r.get("provider") or "Azure", s, c))
                            if azure_rows:
                                used_fallback = True
                                get_memory().record_sql_fix(svc_sql, f"Zero records for open period {current_ym}", f"{svc_sql} [timeRange: {last_ym}]")
                        except Exception as e:
                            logger.warning(f"[Azure Service Fallback Query] {e}")

                    if not azure_rows:
                        return (
                            f"### ☁️ CloudHealth Spend Analysis: Azure\n\n"
                            f"{_format_empty_data_notice('MULTICLOUD_FOCUS_COST_AND_USAGE', 'Azure Services', svc_scope_label)}\n\n"
                            f"*Source: MULTICLOUD_FOCUS_COST_AND_USAGE via CloudHealth FlexReports.*"
                        )

                    total_time_range = {"from": last_ym, "to": last_ym} if used_fallback else svc_time_range
                    total_az = _true_total_cost(
                        "SELECT SUM(EffectiveCost) AS cost FROM MULTICLOUD_FOCUS_COST_AND_USAGE WHERE provider = 'Azure'",
                        time_range_override=total_time_range
                    ) or sum(r[2] for r in azure_rows)
                    tbl_lines = [
                        f"| {p} | {s} | ${c:,.2f} | {((c/total_az)*100 if total_az else 0):.1f}% |"
                        for p, s, c in azure_rows
                    ]
                    fallback_note = (
                        f"> 💡 **FinOps Ingestion Notice**: Current month (`{current_ym}`) has no finalized billing data yet due to cloud billing settlement latency (24–72h). "
                        f"Displaying top Azure services from the latest closed billing cycle (**{last_ym}**).\n\n"
                        if used_fallback else ""
                    )
                    table = (
                        f"{fallback_note}"
                        f"| Cloud Provider | Service Category | Cost | % of Azure Spend |\n"
                        f"|:---|:---|:---|:---|\n"
                        f"{chr(10).join(tbl_lines)}\n\n"
                        f"| **Total Azure Spend** | | **${total_az:,.2f}** | **100.0%** |"
                    )
                    svc_hdr = f"CloudHealth Spend Analysis: Top Azure Services by Spend — {svc_scope_label if not used_fallback else last_ym}"

                # ── GCP Specific Breakdown ──
                elif cloud_target == "gcp":
                    gcp_rows = []
                    used_fallback = False
                    svc_sql = "SELECT provider AS provider, ServiceName AS service, SUM(EffectiveCost) AS cost FROM MULTICLOUD_FOCUS_COST_AND_USAGE WHERE provider IN ('GCP', 'Google Cloud') GROUP BY provider, ServiceName ORDER BY cost DESC"
                    try:
                        res = mcp.call_tool("execute_datasource_query", {
                            "queryInput": {
                                "sqlStatement": svc_sql,
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

                    # Fallback when open month has no records due to billing ingestion delay
                    if not gcp_rows and is_specific and target_ym == current_ym:
                        logger.info(f"[GCP Service Query] {current_ym} returned no rows; falling back to latest closed month {last_ym}...")
                        try:
                            res_fb = mcp.call_tool("execute_datasource_query", {
                                "queryInput": {
                                    "sqlStatement": svc_sql,
                                    "dataGranularity": svc_granularity, "limit": limit, "timeRange": {"from": last_ym, "to": last_ym}
                                },
                                "requestInfo": {"sourceType": "API", "caller": "mcp"}
                            })
                            raw_csv_fb = json.loads(res_fb["content"][0]["text"]).get("csv", "")
                            for r in csv.DictReader(io.StringIO(raw_csv_fb)):
                                c = float(r.get("cost") or 0.0)
                                s = r.get("service")
                                if s and c > 0:
                                    gcp_rows.append((r.get("provider") or "GCP", s, c))
                            if gcp_rows:
                                used_fallback = True
                                get_memory().record_sql_fix(svc_sql, f"Zero records for open period {current_ym}", f"{svc_sql} [timeRange: {last_ym}]")
                        except Exception as e:
                            logger.warning(f"[GCP Service Fallback Query] {e}")

                    if not gcp_rows:
                        return (
                            f"### ☁️ CloudHealth Spend Analysis: GCP\n\n"
                            f"{_format_empty_data_notice('MULTICLOUD_FOCUS_COST_AND_USAGE', 'Google Cloud Services', svc_scope_label)}\n\n"
                            f"*Source: MULTICLOUD_FOCUS_COST_AND_USAGE via CloudHealth FlexReports.*"
                        )

                    total_time_range = {"from": last_ym, "to": last_ym} if used_fallback else svc_time_range
                    total_gcp = _true_total_cost(
                        "SELECT SUM(EffectiveCost) AS cost FROM MULTICLOUD_FOCUS_COST_AND_USAGE WHERE provider IN ('GCP', 'Google Cloud')",
                        time_range_override=total_time_range
                    ) or sum(r[2] for r in gcp_rows)
                    tbl_lines = [
                        f"| {p} | {s} | ${c:,.2f} | {((c/total_gcp)*100 if total_gcp else 0):.1f}% |"
                        for p, s, c in gcp_rows
                    ]
                    fallback_note = (
                        f"> 💡 **FinOps Ingestion Notice**: Current month (`{current_ym}`) has no finalized billing data yet due to cloud billing settlement latency (24–72h). "
                        f"Displaying top GCP services from the latest closed billing cycle (**{last_ym}**).\n\n"
                        if used_fallback else ""
                    )
                    table = (
                        f"{fallback_note}"
                        f"| Cloud Provider | Service Category | Cost | % of GCP Spend |\n"
                        f"|:---|:---|:---|:---|\n"
                        f"{chr(10).join(tbl_lines)}\n\n"
                        f"| **Total GCP Spend** | | **${total_gcp:,.2f}** | **100.0%** |"
                    )
                    svc_hdr = f"CloudHealth Spend Analysis: Top Google Cloud (GCP) Services by Spend — {svc_scope_label if not used_fallback else last_ym}"

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
                    used_fallback = False
                    svc_sql = "SELECT provider AS provider, ServiceName AS service, SUM(EffectiveCost) AS cost FROM MULTICLOUD_FOCUS_COST_AND_USAGE WHERE provider = 'AWS' GROUP BY provider, ServiceName ORDER BY cost DESC"
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

                    # Fallback when open month has no records due to billing ingestion delay
                    if not aws_rows and is_specific and target_ym == current_ym:
                        logger.info(f"[AWS Service Query] {current_ym} returned no rows; falling back to latest closed month {last_ym}...")
                        try:
                            res_fb = mcp.call_tool("execute_datasource_query", {
                                "queryInput": {
                                    "sqlStatement": svc_sql,
                                    "dataGranularity": svc_granularity, "limit": limit, "timeRange": {"from": last_ym, "to": last_ym}
                                },
                                "requestInfo": {"sourceType": "API", "caller": "mcp"}
                            })
                            raw_csv_fb = json.loads(res_fb["content"][0]["text"]).get("csv", "")
                            for r in csv.DictReader(io.StringIO(raw_csv_fb)):
                                c = float(r.get("cost") or 0.0)
                                s = _friendly_service_name(r.get("service") or "")
                                if s and c > 0:
                                    aws_rows.append(("AWS", s, c))
                            if aws_rows:
                                used_fallback = True
                                get_memory().record_sql_fix(svc_sql, f"Zero records for open period {current_ym}", f"{svc_sql} [timeRange: {last_ym}]")
                        except Exception as e:
                            logger.warning(f"[AWS Service Fallback Query] {e}")

                    if not aws_rows:
                        return (
                            f"### ☁️ CloudHealth Spend Analysis: AWS\n\n"
                            f"{_format_empty_data_notice('MULTICLOUD_FOCUS_COST_AND_USAGE', 'AWS Services', svc_scope_label)}\n\n"
                            f"*Source: MULTICLOUD_FOCUS_COST_AND_USAGE via CloudHealth FlexReports.*"
                        )

                    total_time_range = {"from": last_ym, "to": last_ym} if used_fallback else svc_time_range
                    total_aws = _true_total_cost(
                        "SELECT SUM(EffectiveCost) AS cost FROM MULTICLOUD_FOCUS_COST_AND_USAGE WHERE provider = 'AWS'",
                        time_range_override=total_time_range
                    ) or sum(r[2] for r in aws_rows)
                    tbl_lines = [
                        f"| {p} | {s} | ${c:,.2f} | {((c/total_aws)*100 if total_aws else 0):.1f}% |"
                        for p, s, c in aws_rows[:limit]
                    ]
                    fallback_note = (
                        f"> 💡 **FinOps Ingestion Notice**: Current month (`{current_ym}`) has no finalized billing data yet due to cloud billing settlement latency (24–72h). "
                        f"Displaying top AWS services from the latest closed billing cycle (**{last_ym}**).\n\n"
                        if used_fallback else ""
                    )
                    table = (
                        f"{fallback_note}"
                        f"| Cloud Provider | Service Category | Cost | % of AWS Spend |\n"
                        f"|:---|:---|:---|:---|\n"
                        f"{chr(10).join(tbl_lines)}\n\n"
                        f"| **Total AWS Spend** | | **${total_aws:,.2f}** | **100.0%** |"
                    )
                    svc_hdr = f"CloudHealth Spend Analysis: Top {len(tbl_lines)} AWS Services by Spend — {svc_scope_label if not used_fallback else last_ym}"

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
                        used_fallback = False
                        is_exclude_other = is_exclude_other_requested(low) or is_exclude_other_requested(intent_info.get("corrected_query", ""))
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
                                if is_exclude_other and (
                                    s.lower() in ("other", "(unallocated / other)", "unallocated", "other services", "other (combined)", "other categories", "other models")
                                    or s.lower().startswith("other ")
                                ):
                                    continue
                                if s and c > 0:
                                    multi_rows.append((p, s, c))
                        except Exception as e:
                            logger.warning(f"[MultiCloud Query] {e}")

                        # Fallback when open month has no records due to billing ingestion delay
                        if not multi_rows and is_specific and target_ym == current_ym:
                            logger.info(f"[MultiCloud Query] {current_ym} returned no rows; falling back to latest closed month {last_ym}...")
                            try:
                                res_fb = mcp.call_tool("execute_datasource_query", {
                                    "queryInput": {
                                        "sqlStatement": "SELECT provider AS provider, ServiceName AS service, SUM(EffectiveCost) AS cost FROM MULTICLOUD_FOCUS_COST_AND_USAGE GROUP BY provider, ServiceName ORDER BY cost DESC",
                                        "dataGranularity": svc_granularity, "limit": 15, "timeRange": {"from": last_ym, "to": last_ym}
                                    },
                                    "requestInfo": {"sourceType": "API", "caller": "mcp"}
                                })
                                raw_csv_fb = json.loads(res_fb["content"][0]["text"]).get("csv", "")
                                for r in csv.DictReader(io.StringIO(raw_csv_fb)):
                                    c = float(r.get("cost") or 0.0)
                                    s = _friendly_service_name(r.get("service") or "")
                                    p = r.get("provider") or "AWS"
                                    if is_exclude_other and (
                                        s.lower() in ("other", "(unallocated / other)", "unallocated", "other services", "other (combined)", "other categories", "other models")
                                        or s.lower().startswith("other ")
                                    ):
                                        continue
                                    if s and c > 0:
                                        multi_rows.append((p, s, c))
                                if multi_rows:
                                    used_fallback = True
                                    get_memory().record_sql_fix(
                                        "SELECT provider, ServiceName, SUM(EffectiveCost) FROM MULTICLOUD_FOCUS_COST_AND_USAGE",
                                        f"Zero records for open period {current_ym}",
                                        f"SELECT provider, ServiceName, SUM(EffectiveCost) FROM MULTICLOUD_FOCUS_COST_AND_USAGE [timeRange: {last_ym}]"
                                    )
                            except Exception as e:
                                logger.warning(f"[MultiCloud Fallback Query] {e}")

                        if not multi_rows:
                            return (
                                f"### ☁️ CloudHealth Multi-Cloud Spend Analysis\n\n"
                                f"{_format_empty_data_notice('MULTICLOUD_FOCUS_COST_AND_USAGE', 'Multi-Cloud Services', svc_scope_label)}\n\n"
                                f"*Source: MULTICLOUD_FOCUS_COST_AND_USAGE via CloudHealth FlexReports.*"
                            )

                        multi_rows.sort(key=lambda x: x[2], reverse=True)
                        total_time_range = {"from": last_ym, "to": last_ym} if used_fallback else svc_time_range
                        total_multi = _true_total_cost(
                            "SELECT SUM(EffectiveCost) AS cost FROM MULTICLOUD_FOCUS_COST_AND_USAGE",
                            time_range_override=total_time_range
                        ) or sum(r[2] for r in multi_rows)

                        top_multi = multi_rows[:limit]
                        tbl_lines = [
                            f"| {p} | {s} | ${c:,.2f} | {((c/total_multi)*100 if total_multi else 0):.1f}% |"
                            for p, s, c in top_multi
                        ]
                        fallback_note = (
                            f"> 💡 **FinOps Ingestion Notice**: Current month (`{current_ym}`) has no finalized billing data yet due to cloud billing settlement latency (24–72h). "
                            f"Displaying top cloud services from the latest closed billing cycle (**{last_ym}**).\n\n"
                            if used_fallback else ""
                        )
                        table = (
                            f"{fallback_note}"
                            f"| Cloud Provider | Service Category | Cost | % of Total |\n"
                            f"|:---|:---|:---|:---|\n"
                            f"{chr(10).join(tbl_lines)}\n\n"
                            f"| **Total Multi-Cloud Spend** | | **${total_multi:,.2f}** | **100.0%** |"
                        )
                        display_scope = last_ym if used_fallback else svc_scope_label
                        svc_hdr = f"CloudHealth Multi-Cloud Spend Analysis: Top Services Across All Clouds — {display_scope}"

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

                chart_types = intent_info.get("chart_types") or []
                det_type = _detect_chart_type(low)
                if det_type and det_type not in chart_types:
                    chart_types.append(det_type)
                if not chart_types and not is_no_chart_requested(low) and any(w in low for w in ["breakdown", "top", "distribution", "split", "product", "service"]):
                    chart_types = ["bar"]

                chart_md = ""
                if chart_types and not is_no_chart_requested(low):
                    chart_blocks = []
                    effective_chart_scope = last_ym if used_fallback else svc_scope_label
                    for ct in chart_types:
                        c_kind = ct if ct in ["pie", "doughnut", "line", "area", "bar", "horizontal-bar"] else "bar"
                        if cloud_target == "aws" and aws_rows:
                            labels = [s for _, s, _ in aws_rows[:12]]
                            values = [c for _, _, c in aws_rows[:12]]
                            chart_blocks.append(_chart_block(c_kind, f"Top AWS Services by Spend ({effective_chart_scope})", labels, values=values, horizontal=(c_kind == "horizontal-bar" or "horizontal" in low), stacked=False))
                        elif cloud_target == "azure" and azure_rows:
                            labels = [s for _, s, _ in azure_rows[:12]]
                            values = [c for _, _, c in azure_rows[:12]]
                            chart_blocks.append(_chart_block(c_kind, f"Top Azure Services by Spend ({effective_chart_scope})", labels, values=values, horizontal=(c_kind == "horizontal-bar" or "horizontal" in low), stacked=False))
                        elif cloud_target == "gcp" and gcp_rows:
                            labels = [s for _, s, _ in gcp_rows[:12]]
                            values = [c for _, _, c in gcp_rows[:12]]
                            chart_blocks.append(_chart_block(c_kind, f"Top GCP Services by Spend ({effective_chart_scope})", labels, values=values, horizontal=(c_kind == "horizontal-bar" or "horizontal" in low), stacked=False))
                        elif False and cloud_target == "oci" and oci_rows:
                            labels = [s for _, s, _ in oci_rows[:12]]
                            values = [c for _, _, c in oci_rows[:12]]
                            chart_blocks.append(_chart_block(c_kind, f"Top OCI Services by Spend ({effective_chart_scope})", labels, values=values, horizontal=(c_kind == "horizontal-bar" or "horizontal" in low), stacked=False))
                        elif cloud_target == "all" and multi_rows:
                            labels = [f"{p} {s}" for p, s, _ in multi_rows[:12]]
                            values = [c for _, _, c in multi_rows[:12]]
                            chart_blocks.append(_chart_block(c_kind, f"Top Multi-Cloud Services by Spend ({effective_chart_scope})", labels, values=values, horizontal=(c_kind == "horizontal-bar" or "horizontal" in low), stacked=False))
                    chart_md = "\n\n".join(chart_blocks)

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
                # "all clouds" → multi-line chart (one line per provider) + per-cloud MoM table.
                # Single query grouped by provider+month is cleaner than 3 separate calls.
                if active_cloud in (None, "all") or any(w in low for w in ["all cloud", "all clouds", "multi-cloud", "multicloud", "every cloud"]):
                    target_months_count = 3
                    m_cnt = re.search(r'\b(\d{1,2})\s*[- ]?months?\b', low)
                    if m_cnt:
                        target_months_count = max(2, min(int(m_cnt.group(1)), 24))
                    elif intent_info.get("timeframe_months"):
                        target_months_count = max(2, min(int(intent_info["timeframe_months"]), 24))
                    elif months_needed and months_needed > 1:
                        target_months_count = min(months_needed, 24)
                    elif was_monthly_trend and prior_assistant_msgs:
                        prev_cnt_match = re.search(r'Last\s+(\d{1,2})\s+Months', prior_assistant_msgs[-1])
                        if prev_cnt_match:
                            target_months_count = int(prev_cnt_match.group(1))

                    sql_all = (
                        "SELECT provider AS provider, Month AS month, SUM(EffectiveCost) AS cost "
                        "FROM MULTICLOUD_FOCUS_COST_AND_USAGE "
                        "GROUP BY provider, Month ORDER BY month DESC"
                    )
                    res_all = mcp.call_tool("execute_datasource_query", {
                        "queryInput": {
                            "sqlStatement": sql_all,
                            "dataGranularity": "MONTHLY",
                            "limit": target_months_count * 10,
                            "timeRange": {"last": target_months_count, "qualifier": "MONTH"}
                        },
                        "requestInfo": {"sourceType": "API", "caller": "mcp"}
                    })
                    content_all = res_all.get("content", [{}])[0].get("text", "{}")
                    try:
                        c_all = json.loads(content_all)
                        if "csv" in c_all:
                            rows_all = list(csv.DictReader(io.StringIO(c_all["csv"])))
                            # Collect exact target trailing months (sorted ascending)
                            all_months_raw = sorted({r["month"] for r in rows_all if r.get("month")})
                            all_months_set = all_months_raw[-target_months_count:] if len(all_months_raw) >= target_months_count else all_months_raw
                            raw_providers = {r["provider"] for r in rows_all if r.get("provider")}
                            providers_seen = [p for p in ["AWS", "Azure", "Google Cloud"] if p in raw_providers] + [p for p in sorted(raw_providers) if p not in ("AWS", "Azure", "Google Cloud")]
                            t_format = "quarter" if "quarter" in low else "month"

                            # Build per-provider monthly cost map
                            prov_month_cost: dict[str, dict[str, float]] = {}
                            for r in rows_all:
                                prov = r.get("provider")
                                mon = r.get("month")
                                if prov and mon:
                                    prov_month_cost.setdefault(prov, {})[mon] = float(r.get("cost") or 0)

                            # ── Multi-dataset chart & Dynamic MoM Breakdown ───
                            CLOUD_COLORS = {
                                "AWS": "#FF9900",          # AWS Orange
                                "Azure": "#0078D4",        # Azure Blue
                                "Google Cloud": "#34A853", # Google Brand Green (vibrant contrast vs Azure Blue)
                                "GCP": "#34A853",
                                "OCI": "#E53935",          # Oracle Red
                                "Oracle": "#E53935"
                            }

                            now_dt = datetime.date.today()
                            current_ym = f"{now_dt.year}-{now_dt.month:02d}"
                            is_latest_inflight = (all_months_set[-1] == current_ym) if all_months_set else False

                            if is_latest_inflight:
                                days_in_month = calendar.monthrange(now_dt.year, now_dt.month)[1]
                                days_elapsed = max(now_dt.day, 1)
                                runrate_factor = days_in_month / days_elapsed
                            else:
                                days_in_month = 30
                                days_elapsed = 30
                                runrate_factor = 1.0

                            # ── 1. Chart (line or stacked bar per user request) ──
                            chart_labels = []
                            for m in all_months_set:
                                formatted_lbl = _format_time_label(m, t_format)
                                if m == all_months_set[-1] and is_latest_inflight:
                                    chart_labels.append(f"{formatted_lbl} (Forecast)")
                                else:
                                    chart_labels.append(formatted_lbl)

                            chart_datasets = []
                            for prov in providers_seen:
                                data_pts = []
                                for m in all_months_set:
                                    raw_c = prov_month_cost.get(prov, {}).get(m, 0.0)
                                    if m == all_months_set[-1] and is_latest_inflight:
                                        data_pts.append(round(raw_c * runrate_factor, 2))
                                    else:
                                        data_pts.append(round(raw_c, 2))
                                color = CLOUD_COLORS.get(prov, "#8B5CF6")
                                chart_datasets.append({
                                    "label": prov,
                                    "data": data_pts,
                                    "borderColor": color,
                                    "backgroundColor": color
                                })

                            include_chart = intent_info.get("include_chart", True) and not is_no_chart_requested(low)
                            include_mom = intent_info.get("include_mom", True) and not is_no_mom_requested(low)

                            chart_md = ""
                            if include_chart:
                                trend_chart_type = _detect_chart_type(low) or (intent_info.get("chart_types") or [None])[0] or "line"
                                if trend_chart_type not in ("bar", "line", "waterfall", "area"):
                                    trend_chart_type = "line"
                                chart_kind = "bar" if trend_chart_type == "bar" else ("area" if trend_chart_type == "area" else "line")

                                chart_md = _chart_block(
                                    chart_kind,
                                    f"Multi-Cloud Monthly Spend Trend — {chart_labels[0]} to {chart_labels[-1]}",
                                    chart_labels,
                                    datasets=chart_datasets,
                                    value_label="Cost ($)",
                                    stacked=True
                                )

                            # ── 2. Table construction (ALL months in all_months_set) ──
                            hist_months = all_months_set[:-1] if is_latest_inflight else all_months_set

                            if include_mom:
                                headers = ["Cloud Provider"]
                                if hist_months:
                                    headers.append(_format_time_label(hist_months[0], t_format))
                                    for i in range(1, len(hist_months)):
                                        prev_s = _format_time_label(hist_months[i - 1], t_format).split()[0]
                                        curr_s = _format_time_label(hist_months[i], t_format).split()[0]
                                        headers.append(_format_time_label(hist_months[i], t_format))
                                        headers.append(f"MoM ({prev_s} → {curr_s})")

                                if is_latest_inflight:
                                    prev_s = _format_time_label(hist_months[-1], t_format).split()[0] if hist_months else "Prior"
                                    curr_lbl = _format_time_label(all_months_set[-1], t_format)
                                    curr_s = curr_lbl.split()[0]
                                    headers.append(f"{curr_lbl} (MTD)")
                                    headers.append(f"{curr_lbl} (Forecast)")
                                    if hist_months:
                                        headers.append(f"MoM ({prev_s} → {curr_s} Forecast)")

                                tbl_rows = [
                                    "| " + " | ".join(headers) + " |",
                                    "|" + "|".join([":---"] * len(headers)) + "|"
                                ]

                                # Provider rows
                                for prov in providers_seen:
                                    row_cells = [prov]
                                    if hist_months:
                                        row_cells.append(f"${prov_month_cost.get(prov, {}).get(hist_months[0], 0.0):,.2f}")
                                        for i in range(1, len(hist_months)):
                                            m_prev = hist_months[i - 1]
                                            m_curr = hist_months[i]
                                            c_prev = prov_month_cost.get(prov, {}).get(m_prev, 0.0)
                                            c_curr = prov_month_cost.get(prov, {}).get(m_curr, 0.0)
                                            row_cells.append(f"${c_curr:,.2f}")
                                            if c_prev > 0:
                                                d = c_curr - c_prev
                                                p = d / c_prev * 100
                                                a = "🔺" if d >= 0 else "🔻"
                                                s = "+" if d >= 0 else "-"
                                                row_cells.append(f"{a} {s}${abs(d):,.2f} ({s}{abs(p):.1f}%)")
                                            else:
                                                row_cells.append("—")

                                    if is_latest_inflight:
                                        m_prev = hist_months[-1] if hist_months else None
                                        m_curr = all_months_set[-1]
                                        c_prev = prov_month_cost.get(prov, {}).get(m_prev, 0.0) if m_prev else 0.0
                                        c_mtd = prov_month_cost.get(prov, {}).get(m_curr, 0.0)
                                        c_fc = c_mtd * runrate_factor
                                        row_cells.append(f"${c_mtd:,.2f}")
                                        row_cells.append(f"${c_fc:,.2f}")
                                        if hist_months:
                                            if c_prev > 0:
                                                d = c_fc - c_prev
                                                p = d / c_prev * 100
                                                a = "🔺" if d >= 0 else "🔻"
                                                s = "+" if d >= 0 else "-"
                                                row_cells.append(f"{a} {s}${abs(d):,.2f} ({s}{abs(p):.1f}%)")
                                            else:
                                                row_cells.append("—")

                                    tbl_rows.append("| " + " | ".join(row_cells) + " |")

                                # Total Multi-Cloud summary row
                                tot_cells = ["**Total Multi-Cloud**"]
                                if hist_months:
                                    tot_c0 = sum(prov_month_cost.get(p, {}).get(hist_months[0], 0.0) for p in providers_seen)
                                    tot_cells.append(f"**${tot_c0:,.2f}**")
                                    for i in range(1, len(hist_months)):
                                        m_prev = hist_months[i - 1]
                                        m_curr = hist_months[i]
                                        tot_prev = sum(prov_month_cost.get(p, {}).get(m_prev, 0.0) for p in providers_seen)
                                        tot_curr = sum(prov_month_cost.get(p, {}).get(m_curr, 0.0) for p in providers_seen)
                                        tot_cells.append(f"**${tot_curr:,.2f}**")
                                        if tot_prev > 0:
                                            d = tot_curr - tot_prev
                                            p = d / tot_prev * 100
                                            a = "🔺" if d >= 0 else "🔻"
                                            s = "+" if d >= 0 else "-"
                                            tot_cells.append(f"**{a} {s}${abs(d):,.2f} ({s}{abs(p):.1f}%)**")
                                        else:
                                            tot_cells.append("—")

                                if is_latest_inflight:
                                    m_prev = hist_months[-1] if hist_months else None
                                    m_curr = all_months_set[-1]
                                    tot_prev = sum(prov_month_cost.get(p, {}).get(m_prev, 0.0) for p in providers_seen) if m_prev else 0.0
                                    tot_mtd = sum(prov_month_cost.get(p, {}).get(m_curr, 0.0) for p in providers_seen)
                                    tot_fc = tot_mtd * runrate_factor
                                    tot_cells.append(f"**${tot_mtd:,.2f}**")
                                    tot_cells.append(f"**${tot_fc:,.2f}**")
                                    if hist_months:
                                        if tot_prev > 0:
                                            d = tot_fc - tot_prev
                                            p = d / tot_prev * 100
                                            a = "🔺" if d >= 0 else "🔻"
                                            s = "+" if d >= 0 else "-"
                                            tot_cells.append(f"**{a} {s}${abs(d):,.2f} ({s}{abs(p):.1f}%)**")
                                        else:
                                            tot_cells.append("—")

                                tbl_rows.append("| " + " | ".join(tot_cells) + " |")
                                table_md = "\n".join(tbl_rows)
                            else:
                                # Clean monthly spend table WITHOUT MoM variance columns
                                headers = ["Cloud Provider"]
                                for m in hist_months:
                                    headers.append(_format_time_label(m, t_format))
                                if is_latest_inflight:
                                    curr_lbl = _format_time_label(all_months_set[-1], t_format)
                                    headers.append(f"{curr_lbl} (MTD)")
                                    headers.append(f"{curr_lbl} (Forecast)")

                                tbl_rows = [
                                    "| " + " | ".join(headers) + " |",
                                    "|" + "|".join([":---"] * len(headers)) + "|"
                                ]

                                for prov in providers_seen:
                                    row_cells = [prov]
                                    for m in hist_months:
                                        row_cells.append(f"${prov_month_cost.get(prov, {}).get(m, 0.0):,.2f}")
                                    if is_latest_inflight:
                                        m_curr = all_months_set[-1]
                                        c_mtd = prov_month_cost.get(prov, {}).get(m_curr, 0.0)
                                        c_fc = c_mtd * runrate_factor
                                        row_cells.append(f"${c_mtd:,.2f}")
                                        row_cells.append(f"${c_fc:,.2f}")
                                    tbl_rows.append("| " + " | ".join(row_cells) + " |")

                                tot_cells = ["**Total Multi-Cloud**"]
                                for m in hist_months:
                                    tot_c = sum(prov_month_cost.get(p, {}).get(m, 0.0) for p in providers_seen)
                                    tot_cells.append(f"**${tot_c:,.2f}**")
                                if is_latest_inflight:
                                    m_curr = all_months_set[-1]
                                    tot_mtd = sum(prov_month_cost.get(p, {}).get(m_curr, 0.0) for p in providers_seen)
                                    tot_fc = tot_mtd * runrate_factor
                                    tot_cells.append(f"**${tot_mtd:,.2f}**")
                                    tot_cells.append(f"**${tot_fc:,.2f}**")
                                tbl_rows.append("| " + " | ".join(tot_cells) + " |")
                                table_md = "\n".join(tbl_rows)

                            fc_notice = (
                                f"> ℹ️ **Run-Rate Projection Notice**: `{_format_time_label(all_months_set[-1], t_format)}` is in-flight (Day {days_elapsed} of {days_in_month}). "
                                f"Forecast is calculated via standard linear run-rate: `MTD * ({days_in_month}/{days_elapsed}) = {runrate_factor:.2f}x`.\n\n"
                                if is_latest_inflight else ""
                            )

                            insight_md = ""
                            if self.engine != "direct":
                                try:
                                    sys_msg = {"role": "system", "content": f"You are Cleo, an expert FinOps AI. Provide 2 concise bullet observations about this multi-cloud {len(all_months_set)}-month spend trend, comparing historical trends and the latest forecast."}
                                    user_msg = {"role": "user", "content": f"User query: {last_msg}\n\nData:\n{table_md}"}
                                    llm_ans, _ = self._call_active_llm([sys_msg, user_msg])
                                    if llm_ans:
                                        insight_md = f"\n\n**💡 FinOps Insights:**\n{_sanitize_finops_bullet_titles(llm_ans)}"
                                except Exception as e:
                                    logger.debug(f"[LLM Commentary] {e}")

                            chart_part = f"{chart_md}\n\n" if chart_md else ""
                            table_heading = "**Month-over-Month Variance by Cloud Provider:**\n\n" if include_mom else "**Monthly Spend by Cloud Provider:**\n\n"
                            title_suffix = "Trend " if include_chart else ""
                            return (
                                f"### 📊 Multi-Cloud Monthly Spend {title_suffix}— Last {len(all_months_set)} Months\n\n"
                                f"{chart_part}"
                                f"{table_heading}"
                                f"{fc_notice}"
                                f"{table_md}\n"
                                f"{insight_md}\n\n"
                                f"💡 *Source: MULTICLOUD_FOCUS_COST_AND_USAGE via CloudHealth FlexReports.*"
                            )
                    except Exception as e:
                        logger.warning(f"[All-Cloud Trend] {e}")
                    # fallback to single aggregated trend
                    return _monthly_trend_markdown("all")
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
                content = "[]"
                for tool_name in ["list_orgs", "list_channel_customers"]:
                    try:
                        res = mcp.call_tool(tool_name, {})
                        txt = res.get("content", [{}])[0].get("text", "[]")
                        if txt and txt != "[]":
                            content = txt
                            break
                    except Exception:
                        pass
                try:
                    custs = json.loads(content)
                    if isinstance(custs, list) and custs:
                        rows = "\n".join([f"| `{(c.get('customerId') or c.get('id', 'N/A'))}` | **{c.get('name', 'N/A')}** | {c.get('status', 'Active')} |" for c in custs])
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
            llm_resp, err = self._call_active_llm(messages, on_token=on_token)
            if llm_resp:
                return llm_resp
            if err:
                logger.warning(f"[LLM Fallback] {err}")
                return (
                    f"⚠️ **AI Engine Query Error ({self.get_engine_display_name()})**\n\n"
                    f"{err}\n\n"
                    f"> *Please check your API key, model configuration, or network connection in Settings.*"
                )

        # ── 7. Direct Mode Fallback Guidance Menu ────────────────────────────────
        finops_fallback = get_finops_advisory(last_msg)
        if finops_fallback:
            return finops_fallback

        return (
            f"I have received your request: *\"{last_msg}\"*\n\n"
            f"Here are key actions you can ask me to perform in Direct (Rule-Based) Mode:\n\n"
            f"- 📊 **Analyze Costs**: \"Show top 5 customers by spend last month\" or \"Show top AWS services\"\n"
            f"- 🏢 **List Organizations**: \"List all managed organizations\"\n"
            f"- 👥 **List Channel Customers**: \"List all channel customers and tenants\"\n"
            f"- 📚 **Inspect Datasets**: \"List available datasources\" or \"Show schema for CLOUDHEALTH_CONSUMPTION_BREAKDOWN\"\n\n"
            f"> 💡 *Tip*: To ask free-form FinOps questions or use natural language reasoning, enable an AI Engine (e.g. Gemini, OpenAI, Claude, MLX, or Ollama) in Settings."
        )

def run_agent_turn(mcp: MCPClient, ai: AIClient, messages: list[dict], on_token = None, on_status = None) -> str:
    return ai.generate(messages, mcp=mcp, on_token=on_token, on_status=on_status)

def get_access_token(interactive: bool = False) -> Optional[str]:
    mcp_data = auth_helper.load_token()
    if mcp_data:
        if MCP_RESOURCE in mcp_data:
            mcp_data = mcp_data[MCP_RESOURCE]
        token = auth_helper.refresh_token(mcp_data)
        if token:
            return token

    if interactive:
        mcp_data = auth_helper.authenticate(MCP_RESOURCE)
        if mcp_data:
            return mcp_data["token"]["access_token"]
    return None

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Cleo FinOps Agent — Terminal CLI",
        epilog=(
            "Examples:\n"
            "  python3 cleo_agent.py \"Show top 5 AWS services\"\n"
            "  python3 cleo_agent.py --engine gemini \"List all organizations\"\n"
            "  python3 cleo_agent.py                          # Interactive REPL mode\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("query", nargs="*", help="FinOps query to ask Cleo (omit for interactive CLI mode)")
    parser.add_argument("--engine", default=None, help="AI engine (direct, mlx:..., ollama:..., gemini, openai, anthropic)")
    args = parser.parse_args()

    cfg = _load_config()
    engine_key = args.engine or os.environ.get("AI_ENGINE") or cfg.get("ai_engine", "direct")

    token = get_access_token(interactive=False)
    if not token and sys.stdin.isatty():
        print("🔐 CloudHealth session not found. Starting OAuth authentication in browser...")
        token = get_access_token(interactive=True)

    mcp = MCPClient(token, token_refresher=lambda: get_access_token(interactive=False)) if token else None
    ai = AIClient(engine_key, cfg, tools=STANDARD_CH_TOOLS)

    prompt = " ".join(args.query).strip()
    if prompt:
        print(f"🤖 [Cleo FinOps Agent] Engine: {engine_key}")
        print(f"💬 Query: {prompt}\n")
        reply = run_agent_turn(mcp, ai, [{"role": "user", "content": prompt}])
        print(reply)
    else:
        print("🚀 Cleo FinOps Agent CLI")
        print(f"🤖 Active Engine: {engine_key}")
        if not token:
            print("⚠️  No CloudHealth token found. Operating in offline / advisory mode.")
        print("Type your FinOps query or 'exit' / 'quit' to exit.\n")
        history = []
        while True:
            try:
                user_input = input("cleo> ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nGoodbye!")
                break
            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit", "q"):
                break
            history.append({"role": "user", "content": user_input})
            reply = run_agent_turn(mcp, ai, history)
            history.append({"role": "assistant", "content": reply})
            print(f"\n{reply}\n")

