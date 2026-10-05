"""
test_pure_functions.py — Comprehensive unit tests for Cleo pure functions.
Does not require an active LLM or live MCP connection.
"""
import pytest
from cleo_agent import (
    parse_query_time_context,
    extract_requested_service,
    extract_requested_cloud,
    detect_anomaly_status_filter,
    _csv_to_markdown,
    _clean_chart_title,
    _classify_service_usage_type,
)
from cleo_server import _is_valid_guid, _generate_chat_title


def test_parse_query_time_context():
    # Explicit month & year
    ctx = parse_query_time_context("Show spend for July 2026")
    assert ctx["target_ym"] == "2026-07"
    assert "July 2026" in ctx["target_label"]

    # Ascending sort
    ctx_asc = parse_query_time_context("Lowest services by cost")
    assert ctx_asc["sort_desc"] is False


def test_extract_requested_service():
    # Direct service match
    match = extract_requested_service("What did we spend on EC2 last month?")
    assert match is not None
    assert match[0] == "AmazonEC2"
    assert match.provider == "aws"

    # BigQuery match
    match_bq = extract_requested_service("Show BigQuery reservation costs")
    assert match_bq is not None
    assert match_bq.provider == "gcp"

    # Negation / reset across all services
    match_neg = extract_requested_service("Show total spend across all services, not just EC2")
    assert match_neg[0] is None


def test_extract_requested_cloud():
    assert extract_requested_cloud("Show AWS spend") == "aws"
    assert extract_requested_cloud("Show Azure cost") == "azure"
    assert extract_requested_cloud("Google Cloud BigQuery spend") == "gcp"
    assert extract_requested_cloud("Compare AWS and Azure spend") == "all"
    assert extract_requested_cloud("Show multi-cloud costs") == "all"


def test_detect_anomaly_status_filter():
    # 1. Default for anomaly queries is always ACTIVE
    assert detect_anomaly_status_filter("Show my top cost anomalies across all clouds for this month") == "ACTIVE"
    assert detect_anomaly_status_filter("Show AWS cost anomalies detected by CloudHealth") == "ACTIVE"
    assert detect_anomaly_status_filter("Show cost spikes") == "ACTIVE"
    assert detect_anomaly_status_filter("Show active anomalies") == "ACTIVE"
    assert detect_anomaly_status_filter("What are our top cost anomalies?") == "ACTIVE"

    # 2. When user explicitly asks for inactive anomalies
    assert detect_anomaly_status_filter("Show inactive anomalies") == "INACTIVE"
    assert detect_anomaly_status_filter("Show resolved cost anomalies") == "INACTIVE"
    assert detect_anomaly_status_filter("Show closed anomalies") == "INACTIVE"
    assert detect_anomaly_status_filter("Show the inactive ones") == "INACTIVE"
    assert detect_anomaly_status_filter("Show inactive ones") == "INACTIVE"
    assert detect_anomaly_status_filter("Show past anomalies") == "INACTIVE"

    # 3. When user explicitly asks for all statuses / both
    assert detect_anomaly_status_filter("Show all anomalies regardless of status") is None
    assert detect_anomaly_status_filter("Show both active and inactive anomalies") is None
    assert detect_anomaly_status_filter("Show anomalies for all statuses") is None


def test_csv_to_markdown():
    csv_raw = "service,cost,usage\nAmazonEC2,1250.50,100\nAmazonRDS,450.25,50"
    md = _csv_to_markdown(csv_raw)
    assert "| service | cost | usage |" in md
    assert "$1,250.50" in md
    assert "$450.25" in md


def test_clean_chart_title():
    raw_title = "((Last Month Service Spend))"
    cleaned = _clean_chart_title(raw_title)
    assert not cleaned.startswith("((")
    assert not cleaned.endswith("))")
    assert "Partner-Wide" not in _clean_chart_title("RDS Cost for All Accounts (Partner-Wide)")


def test_classify_service_usage_type():
    classified_ebs = _classify_service_usage_type("AmazonEC2", "EBS:VolumeUsage.gp2")
    assert "gp2" in classified_ebs.lower() or "storage" in classified_ebs.lower()

    classified_s3 = _classify_service_usage_type("AmazonS3", "TimedStorage-ByteHrs")
    assert "standard" in classified_s3.lower() or "storage" in classified_s3.lower()


def test_guid_validation():
    valid_uuid = "e7b0c36e-2144-4f27-a1df-7b56832db8c2"
    invalid_uuid = "invalid-uuid-123"
    assert _is_valid_guid(valid_uuid) is True
    assert _is_valid_guid(invalid_uuid) is False
    assert _is_valid_guid("") is False
    assert _is_valid_guid(None) is False


def test_generate_chat_title():
    t1 = _generate_chat_title("Compare previous month service level cost for ABC Coffee Mugs")
    assert "ABC Coffee Mugs" in t1 or "Compare" in t1
    assert len(t1) <= 60


def test_estimate_token_count():
    from cleo_llm import estimate_token_count
    assert estimate_token_count("") == 0
    assert estimate_token_count("Hello world") >= 2


def test_normalize_ollama_url():
    from cleo_llm import normalize_ollama_url
    assert normalize_ollama_url("11434") == "http://127.0.0.1:11434"
    assert normalize_ollama_url("http://localhost:11434").startswith("http://")
    assert normalize_ollama_url("http://0.0.0.0:11434") == "http://127.0.0.1:11434"


def test_detect_system_info():
    from cleo_llm import detect_system_info
    info = detect_system_info()
    assert "os" in info
    assert "total_ram_bytes" in info
    assert "recommended_engine_id" in info


def test_chart_predicates():
    from cleo_charts import is_no_chart_requested, is_no_mom_requested
    assert is_no_chart_requested("show spend without any chart") is True
    assert is_no_chart_requested("show monthly trend") is False
    assert is_no_mom_requested("without mom variance") is True
    assert is_no_mom_requested("show mom growth") is False


def test_prune_messages_to_context_budget():
    from cleo_server import prune_messages_to_context_budget
    messages = [
        {"role": "system", "content": "You are Cleo."},
        {"role": "user", "content": "Query 1 " * 500},
        {"role": "assistant", "content": "Response 1 " * 500},
        {"role": "user", "content": "Query 2 recent"},
    ]
    # Restrict budget so old messages get trimmed
    pruned = prune_messages_to_context_budget(messages, max_tokens=100)
    assert pruned[0]["role"] == "system"
    assert pruned[-1]["content"] == "Query 2 recent"
    assert len(pruned) < len(messages)


def test_multi_month_service_breakdown_parsing():
    q = "give me the last 12 month AWS cost by Service Category breakdown"
    t_ctx = parse_query_time_context(q)
    assert t_ctx["timeframe_months"] == 12
    assert t_ctx["months_needed"] == 12
    assert extract_requested_cloud(q) == "aws"


def test_deterministic_understand_date_vs_math():
    from cleo_query import _deterministic_understand_query
    
    def q(text):
        return _deterministic_understand_query([{"role": "user", "content": text}])

    # Dates like 2026-08 must not be classified as math
    info = q("Give me the 2026-08 cost for all the clouds and break it down by account names")
    assert info["intent"] == "fetch_data"
    assert info["metric_type"] == "cost"
    assert info["target_ym"] == "2026-08"

    # Real math query should be classified as general_chat
    info_math = q("what is 50000 / 12?")
    assert info_math["intent"] == "general_chat"

    # 'account' should not match 'count' in quantity_triggers
    info_acct = q("AWS cost breakdown by account")
    assert info_acct["metric_type"] == "cost"


def test_dimensional_breakdown_queries():
    from cleo_query import _deterministic_understand_query

    def q(text):
        return _deterministic_understand_query([{"role": "user", "content": text}])

    # 1. Service category
    res_cat = q("break down AWS costs by Service Category")
    assert res_cat["intent"] == "fetch_data"
    assert res_cat["cloud"] == "aws"
    assert res_cat["metric_type"] == "cost"

    # 2. Pricing category
    res_prc = q("show costs by pricing category for 2026-08")
    assert res_prc["intent"] == "fetch_data"
    assert res_prc["target_ym"] == "2026-08"

    # 3. AI Model
    res_ai = q("break down AI costs by model")
    assert res_ai["intent"] == "fetch_data"
    assert res_ai["metric_type"] == "cost"

    # 4. Region
    res_reg = q("Google Cloud spend by region")
    assert res_reg["intent"] == "fetch_data"
    assert res_reg["cloud"] == "gcp"

    # 5. Negative constraint (without chart)
    res_nc = q("AWS cost by account without chart")
    assert res_nc["include_chart"] is False

    # 6. Account names breakdown for all clouds
    res_acct = q("Give me the May 2026 cost for all the clouds and break it down by account names")
    assert res_acct["intent"] == "fetch_data"
    assert res_acct["cloud"] == "all"
    assert res_acct["target_ym"] == "2026-05"
    assert res_acct["target_dimension"] == "SubaccountId"
    assert "account" in res_acct["breakdowns"]


def test_multicloud_service_comparison_routing():
    from cleo_query import _deterministic_understand_query

    q_text = "Compare previous month service level cost for ABC Coffee Mugs with current month projected cost across all clouds"
    low = q_text.lower()
    info = _deterministic_understand_query([{"role": "user", "content": q_text}])
    assert info["intent"] == "fetch_data"
    assert info["cloud"] == "all"

    # Simulate routing logic in cleo_agent
    req_months = 2  # As returned when comparing 2 months (previous + current)
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
    )
    is_all_clouds = any(w in low for w in ["all clouds", "all cloud", "across all", "across clouds", "multi-cloud", "multicloud"]) or (info.get("cloud") in (None, "all"))

    assert is_explicit_comparison is True
    assert is_multi_month is False
    assert is_svc_comparison is True
    assert is_all_clouds is True


def test_multimonth_dimensional_breakdown_chart():
    import json
    from cleo_charts import _build_time_category_stacked_chart
    from cleo_query import _deterministic_understand_query

    q_text = "give me the AI Cost data for last 12months and break it down by Model name"
    low = q_text.lower()
    info = _deterministic_understand_query([{"role": "user", "content": q_text}])
    assert info["intent"] == "fetch_data"
    assert info["metric_type"] == "cost"

    # Multi-month dimensional breakdown detection
    has_multi_month_kw = "12months" in low or "12 months" in low
    is_multi_month_dim = bool(info.get("timeframe_months") or has_multi_month_kw)
    assert is_multi_month_dim is True

    # Test stacked chart generation
    sample_rows = [
        {"month": "2025-12", "dimension": "Gemini 2.5 Flash", "val": 42925.94},
        {"month": "2025-12", "dimension": "(Unallocated / Other)", "val": 202877.10},
        {"month": "2026-01", "dimension": "Gemini 2.5 Flash", "val": 9731.68},
        {"month": "2026-01", "dimension": "(Unallocated / Other)", "val": 255853.45},
    ]

    chart_md = _build_time_category_stacked_chart(
        "Multi-Cloud Spend by AI Model & Month — Last 12 Months",
        sample_rows,
        time_col="month",
        cat_col="dimension",
        cost_col="val",
        time_format="month",
        max_cats=10
    )

    assert "```chart" in chart_md
    spec = json.loads(chart_md.split("```chart")[1].split("```")[0].strip())
    assert spec["type"] == "bar"
    assert spec["stacked"] is True
    assert spec["horizontal"] is False
    assert "Dec 2025" in spec["labels"]
    assert "Jan 2026" in spec["labels"]
    assert any(ds["label"] == "Gemini 2.5 Flash" for ds in spec["datasets"])
    assert any(ds["label"] == "(Unallocated / Other)" for ds in spec["datasets"])


def test_market_data_intent_and_specs():
    from cleo_market_data import (
        detect_market_data_intent,
        get_live_model_specs,
        format_model_specs_markdown,
        format_cloud_pricing_markdown,
        format_finops_search_markdown,
    )

    # 1. Intent Detection
    intent_models = detect_market_data_intent("Compare token rates for claude-3-7-sonnet vs gpt-4o")
    assert intent_models is not None
    assert intent_models["type"] == "model_specs"

    intent_models_general = detect_market_data_intent("What are the latest models in AI?")
    assert intent_models_general is not None
    assert intent_models_general["type"] == "model_specs"

    intent_pricing = detect_market_data_intent("What is the retail price for Standard_D4s_v5 in Azure?")
    assert intent_pricing is not None
    assert intent_pricing["type"] == "cloud_pricing"
    assert "d4s_v5" in intent_pricing["sku"]

    intent_trends = detect_market_data_intent("What's the latest happening in finops and what's the latest trend and all?")
    assert intent_trends is not None
    assert intent_trends["type"] == "finops_trends"

    intent_internal = detect_market_data_intent("Show our AWS spend on EC2")
    assert intent_internal is None

    # 2. Model Specs Catalog & Formatting
    specs = get_live_model_specs("claude-3-7-sonnet vs gpt-4o")
    assert len(specs) >= 2
    md = format_model_specs_markdown(specs)
    assert "| Model | Provider | Input ($/1M) | Output ($/1M) |" in md
    assert "Claude" in md
    assert "GPT-4o" in md
    assert "Prompt Caching ROI" in md

    # 3. Cloud Pricing Formatting
    sample_pricing = {
        "service": "Virtual Machines",
        "region": "eastus",
        "sku": "standard_d4s_v5",
        "items": [{
            "armSkuName": "Standard_D4s_v5",
            "meterName": "D4s v5",
            "retailPrice": 0.192,
            "unitOfMeasure": "1 Hour",
            "type": "Consumption",
            "effectiveStartDate": "2024-01-01"
        }]
    }
    pricing_md = format_cloud_pricing_markdown(sample_pricing)
    assert "Standard_D4s_v5" in pricing_md
    assert "0.1920" in pricing_md

    # 4. FinOps Search Formatting & Query Sanitization (Zero Tenant Leakage)
    from cleo_market_data import sanitize_web_search_query
    dirty_query = "Our spend is $142,500 for customer acme-corp in account 123456789012. What are the latest trends in FinOps?"
    sanitized = sanitize_web_search_query(dirty_query)
    assert "$142,500" not in sanitized
    assert "123456789012" not in sanitized
    assert "acme-corp" not in sanitized
    assert "our spend" not in sanitized.lower()
    assert "trends in finops" in sanitized.lower()

    sample_search = [{
        "title": "FinOps Open Cost & Usage Specification (FOCUS) 1.2 Released",
        "snippet": "FOCUS 1.2 introduces normalized billing attributes across clouds.",
        "url": "https://focus.finops.org"
    }]
    search_md = format_finops_search_markdown("FOCUS 1.2", sample_search)
    assert "(FOCUS) 1.2 Released" in search_md
    assert "https://focus.finops.org" in search_md


def test_ai_models_12month_dimensional_breakdown_routing():
    import json
    from cleo_agent import AIClient

    class MockMCP:
        def __init__(self):
            self.last_query = None
        def call_tool(self, tool_name, args):
            self.last_query = args
            csv_data = "month,provider,dimension,val\n2025-11,AWS,Claude 3.5 Sonnet,1200\n2025-12,AWS,Claude 3.5 Sonnet,1500\n2026-01,OpenAI,GPT-4o,2000"
            return {"content": [{"type": "text", "text": json.dumps({"csv": csv_data})}]}

    mock_mcp = MockMCP()
    client = AIClient("direct", {}, [])
    resp = client.generate([
        {"role": "user", "content": "Give me the last 12months cost data for AI models and break it down by model name"}
    ], mcp=mock_mcp)

    assert mock_mcp.last_query is not None
    sql = mock_mcp.last_query["queryInput"]["sqlStatement"]
    assert "MULTICLOUD_AI_COST_AND_USAGE" in sql
    assert "Model AS dimension" in sql
    assert "AWS_CUR" not in sql

    tr = mock_mcp.last_query["queryInput"]["timeRange"]
    assert tr.get("last") == 12
    assert tr.get("qualifier") == "MONTH"

    assert "Multi-Cloud Cost by AI Model — Last 12 Months" in resp
    assert "```chart" in resp
    assert '"stacked": true' in resp
    assert '"horizontal": false' in resp
    assert "Claude 3.5 Sonnet" in resp
    assert "GPT-4o" in resp


if __name__ == "__main__":
    # Self-run check
    test_parse_query_time_context()
    test_extract_requested_service()
    test_extract_requested_cloud()
    test_detect_anomaly_status_filter()
    test_csv_to_markdown()
    test_clean_chart_title()
    test_classify_service_usage_type()
    test_guid_validation()
    test_generate_chat_title()
    test_estimate_token_count()
    test_normalize_ollama_url()
    test_detect_system_info()
    test_chart_predicates()
    test_prune_messages_to_context_budget()
    test_multi_month_service_breakdown_parsing()
    test_deterministic_understand_date_vs_math()
    test_dimensional_breakdown_queries()
    test_multicloud_service_comparison_routing()
    test_multimonth_dimensional_breakdown_chart()
    test_market_data_intent_and_specs()
    test_ai_models_12month_dimensional_breakdown_routing()
    print("All unit tests passed successfully!")



