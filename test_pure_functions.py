"""
test_pure_functions.py — Comprehensive unit tests for Cleo pure functions.
Does not require an active LLM or live MCP connection.
"""
import pytest
from cleo_agent import (
    parse_query_time_context,
    extract_requested_service,
    extract_all_requested_services,
    extract_requested_cloud,
    detect_anomaly_status_filter,
    _csv_to_markdown,
    _clean_chart_title,
    _classify_service_usage_type,
)
from cleo_charts import (
    is_exclude_other_requested,
    is_other_category_name,
    _build_time_category_stacked_chart,
)
from cleo_query import (
    _detect_contextual_continuation,
    _deterministic_understand_query,
)
from cleo_server import _is_valid_guid, _generate_chat_title, _wait_for_server_ready, _find_free_port
import datetime


def test_parse_query_time_context():
    # Explicit month & year
    ctx = parse_query_time_context("Show spend for July 2026")
    assert ctx["target_ym"] == "2026-07"
    assert "July 2026" in ctx["target_label"]

    # Ascending sort
    ctx_asc = parse_query_time_context("Lowest services by cost")
    assert ctx_asc["sort_desc"] is False

    # YTD detection
    ctx_ytd = parse_query_time_context("give me the RDS and S3 cost and usage data for YTD")
    now = datetime.date.today()
    assert ctx_ytd["timeframe_months"] == now.month
    assert ctx_ytd["months_needed"] == now.month
    assert "YTD" in ctx_ytd["target_label"]


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

    # Multi-service extraction
    all_matches = extract_all_requested_services("give me the RDS and S3 cost and usage data for YTD")
    assert len(all_matches) == 2
    assert all_matches[0].pcode == "AmazonRDS"
    assert all_matches[0].display_name == "RDS"
    assert all_matches[0].disp == "RDS"
    assert all_matches[0].provider == "aws"
    pcode, disp = all_matches[0]
    assert pcode == "AmazonRDS"
    assert disp == "RDS"
    assert all_matches[1].pcode == "AmazonS3"
    assert all_matches[1].display_name == "S3"

    three_matches = extract_all_requested_services("Show EC2, RDS, and S3 spend")
    assert len(three_matches) == 3
    assert [m.pcode for m in three_matches] == ["AmazonEC2", "AmazonRDS", "AmazonS3"]
    assert [m.display_name for m in three_matches] == ["EC2", "RDS", "S3"]


def test_multi_service_and_ytd_understanding():
    now = datetime.date.today()
    q = "give me the RDS and S3 cost and usage data for YTD"
    understood = _deterministic_understand_query([{"role": "user", "content": q}])
    assert understood["intent"] == "fetch_data"
    assert understood["timeframe_months"] == now.month
    assert understood["timeframe_days"] is None
    assert understood["services"] == ["AmazonRDS", "AmazonS3"]
    assert understood["service"] == "AmazonRDS"

    # MTD test
    q_mtd = "What is our AWS MTD spend?"
    ctx_mtd = parse_query_time_context(q_mtd)
    assert ctx_mtd["target_ym"] == now.strftime("%Y-%m")
    assert "MTD" in ctx_mtd["target_label"]
    und_mtd = _deterministic_understand_query([{"role": "user", "content": q_mtd}])
    assert und_mtd["timeframe_months"] == 1
    assert und_mtd["timeframe_days"] is None

    # QTD test
    q_qtd = "Show EC2 cost for QTD"
    ctx_qtd = parse_query_time_context(q_qtd)
    expected_qtd_m = ((now.month - 1) % 3) + 1
    assert ctx_qtd["timeframe_months"] == expected_qtd_m
    assert "QTD" in ctx_qtd["target_label"]
    und_qtd = _deterministic_understand_query([{"role": "user", "content": q_qtd}])
    assert und_qtd["timeframe_months"] == expected_qtd_m
    assert und_qtd["timeframe_days"] is None



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

    # Realistic production scenario: system prompt is ~13k tokens, followed by multi-turn chat
    realistic_msgs = [
        {"role": "system", "content": "FinOps assistant doctrine and rules " * 1500},  # ~12-14k tokens
        {"role": "user", "content": "Show our top cloud services across AWS, Azure, and GCP this month"},
        {"role": "assistant", "content": "### 📊 CloudHealth Multi-Cloud Spend Analysis\n\n| AWS | EC2 | $500 |"},
        {"role": "user", "content": "give me the above data for last 3 months"},
    ]
    # With default 28k budget, ALL dialogue turns must be preserved
    preserved = prune_messages_to_context_budget(realistic_msgs)
    assert len(preserved) == 4
    assert preserved[1]["role"] == "user"
    assert preserved[2]["role"] == "assistant"
    assert preserved[3]["content"] == "give me the above data for last 3 months"

    # Excessively long conversation exceeding budget preserves min_recent_turns
    long_msgs = [{"role": "system", "content": "Cleo System Prompt " * 1500}]
    for i in range(20):
        long_msgs.append({"role": "user", "content": f"Turn {i} user " * 200})
        long_msgs.append({"role": "assistant", "content": f"Turn {i} assistant " * 200})
    pruned_long = prune_messages_to_context_budget(long_msgs, max_tokens=20000)
    assert pruned_long[0]["role"] == "system"
    assert len(pruned_long) >= 5  # system + at least 4 chat messages
    assert pruned_long[-1]["content"].startswith("Turn 19 assistant")


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
    assert "Model IS NOT NULL" in sql
    assert "AWS_CUR" not in sql
    assert mock_mcp.last_query["queryInput"]["limit"] == 500

    tr = mock_mcp.last_query["queryInput"]["timeRange"]
    assert tr.get("last") == 12
    assert tr.get("qualifier") == "MONTH"

    assert "Multi-Cloud Cost by AI Model — Last 12 Months" in resp
    assert "```chart" in resp
    assert '"stacked": true' in resp
    assert '"horizontal": false' in resp
    assert "Claude 3.5 Sonnet" in resp
    assert "GPT-4o" in resp

    # Test AI models query without explicit breakdown also routes to MULTICLOUD_AI_COST_AND_USAGE
    mock_mcp_implicit = MockMCP()
    resp_implicit = client.generate([
        {"role": "user", "content": "Give me the last 12months cost data for AI models"}
    ], mcp=mock_mcp_implicit)
    assert mock_mcp_implicit.last_query is not None
    assert "MULTICLOUD_AI_COST_AND_USAGE" in mock_mcp_implicit.last_query["queryInput"]["sqlStatement"]
    assert "Multi-Cloud Cost by AI Model — Last 12 Months" in resp_implicit

    # Test Empty State: If AI query returns no rows, must not fall through to Virtual Machines
    class MockEmptyMCP:
        def call_tool(self, tool_name, args):
            return {"content": [{"type": "text", "text": json.dumps({"csv": "month,provider,dimension,val\n"})}]}
    resp_empty = client.generate([
        {"role": "user", "content": "Give me the last 12months cost data for AI models and break it down by model name"}
    ], mcp=MockEmptyMCP())
    assert "No active AI model spend data was returned" in resp_empty
    assert "Virtual Machines" not in resp_empty

    # Test LLM-First Sanitization: If LLM hallucinates 'AmazonRDS' for an AI models query,
    # _understand_query must sanitize service to None and not route to RDS
    client_llm = AIClient("gemini", {}, [])
    fake_llm_json = json.dumps({
        "intent": "fetch_data",
        "cloud": "all",
        "service": "AmazonRDS",
        "customer": None,
        "metric_type": "cost",
        "target_dimension": "Model",
        "timeframe_months": 12,
        "breakdowns": ["model"],
        "chart_types": ["bar"],
        "include_chart": True,
        "include_mom": True,
        "is_new_data_fetch": True,
        "corrected_query": "Give me the last 12 months cost data for AI models and break it down by model name"
    })
    client_llm._call_active_llm = lambda msgs: (fake_llm_json, None)
    sanitized_intent = client_llm._understand_query([
        {"role": "user", "content": "Give me the last 12months cost data for AI models and break it down by model name"}
    ])
    assert sanitized_intent.get("service") is None, f"Expected None service, got {sanitized_intent.get('service')}"

    # Test RDS Guard: Even if mock_intent has service='AmazonRDS', it must route to MULTICLOUD_AI_COST_AND_USAGE
    mock_mcp_llm = MockMCP()
    client_guarded = AIClient("direct", {}, [])
    client_guarded._understand_query = lambda msgs, mcp=None: json.loads(fake_llm_json)
    resp_guarded = client_guarded.generate([
        {"role": "user", "content": "Give me the last 12months cost data for AI models and break it down by model name"}
    ], mcp=mock_mcp_llm)
    assert mock_mcp_llm.last_query is not None
    sql_guarded = mock_mcp_llm.last_query["queryInput"]["sqlStatement"]
    assert "MULTICLOUD_AI_COST_AND_USAGE" in sql_guarded
    assert "AWS_RDS" not in sql_guarded
    assert "Model AS dimension" in sql_guarded


def test_wait_for_server_ready():
    import http.server
    import threading

    # 1. Unused port should timeout and return False
    unused_port = _find_free_port(59123)
    assert _wait_for_server_ready(unused_port, timeout=0.15) is False

    # 2. Responding server on /health should return True
    class DummyHealthHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/health":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):
            pass  # suppress log output during test

    server = http.server.HTTPServer(("127.0.0.1", unused_port), DummyHealthHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        assert _wait_for_server_ready(unused_port, timeout=2.0) is True
    finally:
        server.shutdown()
        server.server_close()


def test_oauth_disconnected_handling(tmp_path, monkeypatch):
    from auth.oauth import OAuth2Helper
    from cleo_agent import get_access_token
    import cleo_agent

    # Test that permanent refresh failure purges tokens and returns None
    tokens_file = str(tmp_path / "mcp_tokens.json")
    helper = OAuth2Helper(client_id="test-client", client_secret="test-sec")
    helper.tokens_file = tokens_file

    sample_token_data = {
        "token": {
            "access_token": "expired_at",
            "refresh_token": "invalid_rt",
            "expires_at": 1000  # long past
        }
    }
    # Simulate failed refresh returning 400 invalid_grant
    monkeypatch.setattr(helper, "_post_request", lambda payload: None)
    helper.last_error = "HTTP 400: {\"error\":\"invalid_grant\"}"

    # Verify refresh_token purges invalid tokens
    refreshed = helper.refresh_token(sample_token_data)
    assert refreshed is None

    # Test get_access_token does not return dead token
    monkeypatch.setattr(cleo_agent.auth_helper, "load_token", lambda: None)
    monkeypatch.setattr(cleo_agent.auth_helper, "refresh_token", lambda d: None)
    token = get_access_token(interactive=False)
    assert token is None


def test_exclude_other_requested_and_chart_filtering():
    # 1. Test is_exclude_other_requested predicate
    assert is_exclude_other_requested('exclude "other" model from the above data') is True
    assert is_exclude_other_requested('exclude other from the abouve resoult') is True
    assert is_exclude_other_requested('without other') is True
    assert is_exclude_other_requested('filter out other models') is True
    assert is_exclude_other_requested('remove other') is True
    assert is_exclude_other_requested('omit other categories') is True
    assert is_exclude_other_requested('exclude unallocated') is True
    assert is_exclude_other_requested("Give me the last 6 months cost and usage reports for the AI Usage") is False
    assert is_exclude_other_requested("Show spend on other services") is False

    # 2. Test _build_time_category_stacked_chart with exclude_other=True
    raw_rows = [
        {"month": "2026-08", "dimension": f"Model_{i}", "val": 100 - i} for i in range(15)
    ]
    raw_rows.append({"month": "2026-08", "dimension": "Other", "val": 500})

    # When exclude_other=True: "Other" is excluded AND no 11th remainder "Other" dataset is added
    chart_json_str = _build_time_category_stacked_chart(
        "Spend by Model", raw_rows, time_col="month", cat_col="dimension", cost_col="val",
        max_cats=10, exclude_other=True
    )
    import json
    chart_data = json.loads(chart_json_str.replace("```chart\n", "").replace("\n```", ""))
    dataset_labels = [ds["label"] for ds in chart_data["datasets"]]
    assert "Other" not in dataset_labels
    assert len(chart_data["datasets"]) == 10

    # When exclude_other=False: remainder categories are aggregated into "Other"
    chart_json_str_with_other = _build_time_category_stacked_chart(
        "Spend by Model", raw_rows, time_col="month", cat_col="dimension", cost_col="val",
        max_cats=10, exclude_other=False
    )
    chart_data_with_other = json.loads(chart_json_str_with_other.replace("```chart\n", "").replace("\n```", ""))
    dataset_labels_with_other = [ds["label"] for ds in chart_data_with_other["datasets"]]
    assert "Other" in dataset_labels_with_other

    # 3. Test continuation detection and deterministic understanding for follow-up
    session_messages = [
        {"role": "user", "content": "Give me the last 6 months cost and usage reports for the AI Usage"},
        {"role": "assistant", "content": "### 📊 CloudHealth Spend Analysis: Multi-Cloud Cost by AI Model — Last 6 Months\n\n| Month | Total Spend |\n|:---|:---|\n| 2026-09 | $273,995.51 |"},
        {"role": "user", "content": 'exclude "other" model from the above data'}
    ]
    cont = _detect_contextual_continuation(session_messages)
    assert cont["is_continuation"] is True
    assert cont["prior_query_type"] == "ai_model_breakdown"
    assert cont["inherited_timeframe_months"] == 6

    intent = _deterministic_understand_query(session_messages)
    assert intent["intent"] == "fetch_data"
    assert intent["target_dimension"] == "Model"
    assert "model" in intent["breakdowns"]
    assert intent["timeframe_months"] == 6
    assert intent["is_new_data_fetch"] is True


def test_is_other_category_name_and_multiservice_continuation():
    # 1. is_other_category_name checks
    assert is_other_category_name("Other") is True
    assert is_other_category_name("other") is True
    assert is_other_category_name("(Unallocated / Other)") is True
    assert is_other_category_name("RDS Other") is True
    assert is_other_category_name("EC2 Other") is True
    assert is_other_category_name("*Other (14 instance types)*") is True
    assert is_other_category_name("Other / Unclassified") is True
    assert is_other_category_name("Other Models") is True
    assert is_other_category_name("Other (8 items)") is True

    # Real categories should NOT match
    assert is_other_category_name("db.m5.large") is False
    assert is_other_category_name("Standard Storage") is False
    assert is_other_category_name("Amazon RDS") is False
    assert is_other_category_name("Provisioned IOPS") is False

    # 2. Multi-Service YTD continuation
    now = datetime.date.today()
    ms_messages = [
        {"role": "user", "content": "give me the RDS and S3 cost and usage data for YTD"},
        {"role": "assistant", "content": (
            "### 📊 CloudHealth Multi-Service Spend Analysis: RDS & S3\n\n"
            "Queried live from standard CloudHealth datasets for **All Accounts**:\n\n"
            "- **Target Billing Period**: Year-to-Date (YTD 2026)\n"
            "- **Combined Multi-Service Spend**: **$48,120.00**\n\n"
        )},
        {"role": "user", "content": "remove Other from above data for RDS Usage"}
    ]
    cont = _detect_contextual_continuation(ms_messages)
    assert cont["is_continuation"] is True
    assert cont["prior_query_type"] == "multi_service"
    assert cont["inherited_is_ytd"] is True

    intent = _deterministic_understand_query(ms_messages)
    assert intent["timeframe_months"] == now.month
    assert intent["timeframe_days"] is None
    assert is_exclude_other_requested(ms_messages[-1]["content"]) is True

    # 3. Service breakdown YTD continuation (e.g. RDS instance type)
    sb_messages = [
        {"role": "user", "content": "give me RDS spend by instance type for YTD"},
        {"role": "assistant", "content": (
            "### 🗄️ CloudHealth RDS Spend Analysis: Instance Type Breakdown\n\n"
            "- **Target Billing Period**: Year-to-Date (YTD 2026)\n"
            "- **Total RDS Billed Spend**: **$30,000.00** across 12 active database instance types\n\n"
            "#### 🖥️ Spend by RDS Instance Type\n\n"
            "| # | Instance Type | Total Spend | % of Total |\n"
            "|:---|:---|:---|:---|\n"
            "| 1 | `db.r5.large` | **$12,000.00** | 40.0% |\n"
        )},
        {"role": "user", "content": "remove Other from above data"}
    ]
    cont_sb = _detect_contextual_continuation(sb_messages)
    assert cont_sb["is_continuation"] is True
    assert cont_sb["prior_query_type"] == "service_breakdown"
    assert cont_sb["inherited_is_ytd"] is True

    intent_sb = _deterministic_understand_query(sb_messages)
    assert intent_sb["timeframe_months"] == now.month
    assert intent_sb["timeframe_days"] is None


def test_azure_hybrid_benefit_understanding():
    # 1. User's exact prompt
    q1 = "Find the Azure VMs that are not using the Hybrid Discounts"
    res1 = _deterministic_understand_query([{"role": "user", "content": q1}])
    assert res1["intent"] == "fetch_data"
    assert res1["cloud"] == "azure"
    assert res1["service"] == "Virtual Machines"
    assert res1["target_dimension"] == "hybrid_benefit"
    assert "hybrid_benefit" in res1["breakdowns"]
    assert "commitment_plan" not in res1["breakdowns"]

    # 2. Variant: "Which Azure VMs are using Azure Hybrid Benefit?"
    q2 = "Which Azure VMs are using Azure Hybrid Benefit?"
    res2 = _deterministic_understand_query([{"role": "user", "content": q2}])
    assert res2["intent"] == "fetch_data"
    assert res2["cloud"] == "azure"
    assert res2["target_dimension"] == "hybrid_benefit"
    assert "commitment_plan" not in res2["breakdowns"]

    # 3. Variant: "Find VMs not using hybrid discounts" (no cloud mentioned explicitly)
    q3 = "Find VMs not using hybrid discounts"
    res3 = _deterministic_understand_query([{"role": "user", "content": q3}])
    assert res3["cloud"] == "azure"
    assert res3["service"] == "Virtual Machines"
    assert res3["target_dimension"] == "hybrid_benefit"
    assert "commitment_plan" not in res3["breakdowns"]


def test_azure_hybrid_benefit_handler_execution():
    from cleo_agent import AIClient
    import json

    # Mock MCP client
    class MockMCP:
        def __init__(self, csv_data=""):
            self.csv_data = csv_data
            self.calls = []

        def call_tool(self, name, args):
            self.calls.append((name, args))
            if name == "execute_datasource_query":
                return {"content": [{"type": "text", "text": json.dumps({"csv": self.csv_data})}]}
            return {}

    # Sample CSV data matching exact AZURE_COST_USAGE FlexReport query schema and user production records:
    # 1. SQL Server VMs with AHB active ($0.00 license cost with Quantity > 0)
    # 2. RHEL VMs without AHB (paying PAYG license cost > $0.00)
    # 3. Third-party marketplace firewall appliances (VM-Series)
    # 4. Windows Server VM paying PAYG license
    # 5. Windows Server VM with AHBDsc: True ($0.00 license cost)
    sample_csv = (
        '"ResourceName","SUM_ActualCostInBillingCurrency","SUM_Quantity","MeterCategory","MeterSubCategory","Day","AdditionalInfo","ResourceId","MetricType"\n'
        '"demo-resource-moor-b89db1824aae","$0.00","24.00","Virtual Machines Licenses","SQL Server Azure Hybrid Benefit","2026-10-05","{}","/subscriptions/3014f819-2065-33d3-84f0-91832b28f210/resourceGroups/b28fb2b64752/providers/Microsoft.Compute/virtualMachines/0979fb4b8fc076411571bab68a3965c3cbe67bf68f484d35ef192e11fdeb0fac","Actual"\n'
        '"demo-resource-peak-91344e6899c0","$0.00","23.98","Virtual Machines Licenses","SQL Server Azure Hybrid Benefit","2026-10-05","{}","/subscriptions/f6c0cd82-7ac5-3fef-941c-f4bee573b700/resourceGroups/fabed64eab7e/providers/Microsoft.Compute/virtualMachines/f33137783aed8f1e5a92ba289977b6a1414f2de6b0d9d031d00fab400a45d560","Actual"\n'
        '"demo-resource-canyon-29e167b8cf65","$58.32","24.00","Virtual Machine Licenses","VM-Series Next Generation Firewall","2026-10-05","{}","/subscriptions/c290d621-1cc8-344f-9fb7-aa83ffd62870/resourceGroups/7eb908bcdb7c/providers/Microsoft.Compute/virtualMachines/3dbf575f871199c5c46ff67db9313b62d7acd972731b5d3a4c822c893ed9ec1c","Actual"\n'
        '"demo-resource-lava-36288709f81b","$58.32","24.00","Virtual Machine Licenses","VM-Series Next Generation Firewall","2026-10-05","{}","/subscriptions/c290d621-1cc8-344f-9fb7-aa83ffd62870/resourceGroups/7eb908bcdb7c/providers/Microsoft.Compute/virtualMachines/dcfdd8e4876e9913e171d5c3c5b2fcbea6f0fa0ad1f66e28abb364bebf72dbaa","Actual"\n'
        '"demo-resource-fjord-6fb8019a8e8e","$0.00","12.00","Virtual Machines Licenses","SQL Server Developer Edition","2026-10-06","{}","/subscriptions/d0d47ef3-ab41-3ba8-9472-e8cc2eff0461/resourceGroups/3b14f369014e/providers/Microsoft.Compute/virtualMachines/dc6354f4c424004609ef5be41df5b0c09617b376749d302f91a9d013aa4345f7","Actual"\n'
        '"demo-resource-xenolith-3704766fd7d5","$0.00","23.98","Virtual Machines Licenses","SQL Server Azure Hybrid Benefit","2026-09-29","{}","/subscriptions/b46bd1c3-fbb7-3dce-9287-8a5c618981b9/resourceGroups/29710c9d1c74/providers/Microsoft.Compute/virtualMachines/beceaf711a074d8d50c1c2e9ed277406c9d09ea948f8a8aaee5c03cda9e0f039","Actual"\n'
        '"demo-resource-cedar-5108926f1192","$3.12","24.00","Virtual Machines Licenses","Red Hat Enterprise Linux","2026-10-02","{}","/subscriptions/edf40cf7-1016-3f7c-9c4c-94020202e771/resourceGroups/eae988a7851f/providers/Microsoft.Compute/virtualMachines/a39bfc3c03d946c1f84fd185babc0b27708403b1a0a7712b9b1a23359a48bfec","Actual"\n'
        '"demo-resource-vale-764a16dd377b","$2.21","24.00","Virtual Machines Licenses","Red Hat Enterprise Linux","2026-10-03","{}","/subscriptions/865812a6-83ba-3611-8714-a657cc87097c/resourceGroups/a84334184bb4/providers/Microsoft.Compute/virtualMachines/437947a302c3ff3c3dfeb11191d7885155355277063b67c5209a91a503304db1","Actual"\n'
        '"demo-resource-keystone-3f18b67080f0","$0.55","24.00","Virtual Machines Licenses","Red Hat Enterprise Linux","2026-09-13","{}","/subscriptions/865812a6-83ba-3611-8714-a657cc87097c/resourceGroups/a611fa274ef0/providers/Microsoft.Compute/virtualMachines/f8a55d302bb084690c85f678a7869b346cf11d78b3d7751a3ac90a8d5ed55846","Actual"\n'
        '"vm-prod-winpayg","$15.00","24.00","Virtual Machines Licenses","Windows Server","2026-10-05","{}","/subscriptions/sub1/resourceGroups/rg1/providers/Microsoft.Compute/virtualMachines/vm-prod-winpayg","Actual"\n'
        '"vm-prod-winahb","$0.00","24.00","Virtual Machines Licenses","Windows Server","2026-10-05","{""AHBDsc"":""True""}","/subscriptions/sub1/resourceGroups/rg1/providers/Microsoft.Compute/virtualMachines/vm-prod-winahb","Actual"\n'
    )

    mock_mcp = MockMCP(csv_data=sample_csv)
    client = AIClient("direct", {}, [])

    # 1. Ask for VMs NOT using hybrid discounts
    messages = [{"role": "user", "content": "Find the Azure VMs that are not using the Hybrid Discounts"}]
    res = client.generate(messages, mcp=mock_mcp)

    # Verify query routed to AZURE_COST_USAGE with Virtual Machines Licenses filter
    query_calls = [c for c in mock_mcp.calls if c[0] == "execute_datasource_query"]
    assert len(query_calls) > 0
    query_call = query_calls[0]
    sql_stmt = query_call[1]["queryInput"]["sqlStatement"]
    assert "AZURE_COST_USAGE" in sql_stmt
    assert "Virtual Machines Licenses" in sql_stmt
    assert "MULTICLOUD_COMMITMENT_SAVINGS" not in sql_stmt

    # Verify response identifies un-discounted RHEL and Windows VMs paying active software licensing fees
    assert "demo-resource-cedar-5108926f1192" in res
    assert "demo-resource-vale-764a16dd377b" in res
    assert "demo-resource-keystone-3f18b67080f0" in res
    assert "vm-prod-winpayg" in res
    assert "Red Hat (RHEL)" in res
    assert "Windows Server" in res
    assert "AZURE_COST_USAGE" in res
    assert "MULTICLOUD_COMMITMENT_SAVINGS" not in res
    assert "az vm update" in res
    assert "--license-type Windows_Server" in res
    assert "--license-type RHEL_BYOS" in res
    assert "--license-type SLES_BYOS" in res
    assert "az sql vm update" in res
    assert "Workload / OS" in res
    assert "Realizable Monthly Savings" in res or "Savings" in res

    # Verify strategic FinOps insights (TCO, CapEx vs OpEx, Dev/Test subscriptions)
    assert "Dev/Test" in res
    assert "CapEx" in res
    assert "Software Assurance" in res

    # 2. Ask for VMs USING hybrid discounts
    mock_mcp_using = MockMCP(csv_data=sample_csv)
    res_using = client.generate([{"role": "user", "content": "Which Azure VMs are using Azure Hybrid Benefit?"}], mcp=mock_mcp_using)
    assert "demo-resource-moor-b89db1824aae" in res_using
    assert "demo-resource-peak-91344e6899c0" in res_using
    assert "demo-resource-xenolith-3704766fd7d5" in res_using
    assert "vm-prod-winahb" in res_using
    assert "Dev/Test" in res_using

    # 3. Test empty CSV (0 rows returned)
    mock_mcp_empty = MockMCP(csv_data="")
    res_empty = client.generate(messages, mcp=mock_mcp_empty)
    assert "AZURE_COST_USAGE" in res_empty
    assert "Virtual Machines Licenses" in res_empty
    assert "MULTICLOUD_COMMITMENT_SAVINGS" not in res_empty
    assert "az vm update" in res_empty
    assert "RHEL_BYOS" in res_empty
    assert "SLES_BYOS" in res_empty
    assert "Dev/Test" in res_empty
    assert "CapEx" in res_empty
    assert "Software Assurance" in res_empty


def test_build_llm_schema_context_validity():
    import cleo_agent
    schema_ctx = cleo_agent.build_llm_schema_context()
    assert schema_ctx, "Schema context should not be empty when cache exists"
    assert "CLOUDHEALTH DATASOURCE SCHEMAS & KEY COLUMNS" in schema_ctx

    # Check key datasets are present
    assert "`AZURE_COST_USAGE`" in schema_ctx
    assert "`AWS_CUR`" in schema_ctx
    assert "`MULTICLOUD_FOCUS_COST_AND_USAGE`" in schema_ctx
    assert "`MULTICLOUD_OPERATIONAL_EMISSIONS`" in schema_ctx
    assert "`UNIFIED_AI_TOKENOMICS`" in schema_ctx
    assert "`AWS_K8S_COST`" in schema_ctx
    assert "`AWS_DATABRICKS_USAGE`" in schema_ctx
    assert "`MULTICLOUD_COMMITMENT_SAVINGS`" in schema_ctx

    # Check that critical columns exist and are not filtered out
    assert "MeterCategory" in schema_ctx
    assert "MeterSubCategory" in schema_ctx
    assert "ResourceName" in schema_ctx
    assert "ResourceId" in schema_ctx
    assert "ActualCostInBillingCurrency" in schema_ctx
    assert "lineItem_UnblendedCost" in schema_ctx
    assert "product_instanceType" in schema_ctx
    assert "Carbon" in schema_ctx
    assert "Country" in schema_ctx
    assert "Commitment_Plan" in schema_ctx
    assert "Status" in schema_ctx


def test_lease_type_understanding_and_handler_execution():
    from cleo_query import _deterministic_understand_query
    from cleo_agent import AIClient
    import json

    # 1. Test intent & dimension extraction
    res1 = _deterministic_understand_query([{"role": "user", "content": "breakdown the above usage by Lease type"}])
    assert res1["target_dimension"] == "LeaseType"
    assert "lease_type" in res1["breakdowns"]

    res2 = _deterministic_understand_query([{"role": "user", "content": "give me the RDS cost and usage by purchase option for YTD"}])
    assert res2["target_dimension"] == "LeaseType"
    assert "lease_type" in res2["breakdowns"]
    assert res2["service"] == "AmazonRDS"

    res3 = _deterministic_understand_query([{"role": "user", "content": "EC2 spend by ondemand, reservation, savings plan and spot"}])
    assert res3["target_dimension"] == "LeaseType"
    assert "lease_type" in res3["breakdowns"]

    # 2. Test execution with MockMCP simulating the user's exact chat session
    class MockMCP:
        def __init__(self, csv_data=""):
            self.csv_data = csv_data
            self.calls = []

        def call_tool(self, name, args):
            self.calls.append((name, args))
            if name == "execute_datasource_query":
                return {"content": [{"type": "text", "text": json.dumps({"csv": self.csv_data})}]}
            return {}

    sample_cur_csv = (
        '"month","pricing_term","item_type","cost","usage_amount"\n'
        '"2026-08","OnDemand","Usage","62059.75","2000.0"\n'
        '"2026-08","Reserved","RIFee","8494.47","500.0"\n'
        '"2026-08","OnDemand","SavingsPlanCoveredUsage","5833.75","200.0"\n'
        '"2026-08",,"EdpDiscount","-12597.99","0.0"\n'
        '"2026-09","OnDemand","Usage","60362.28","1900.0"\n'
        '"2026-09","Reserved","RIFee","8220.46","480.0"\n'
        '"2026-09","OnDemand","SavingsPlanCoveredUsage","6541.57","210.0"\n'
        '"2026-09",,"EdpDiscount","-13180.88","0.0"\n'
    )

    mock_mcp = MockMCP(csv_data=sample_cur_csv)
    client = AIClient("direct", {}, [])

    chat_history = [
        {"role": "user", "content": "give me the RDS and S3 cost and usage data for YTD"},
        {"role": "assistant", "content": "### 📊 CloudHealth Multi-Service Spend Analysis: RDS & S3\n- Target Billing Period: Year-to-Date (2025-12 to 2026-10)"},
        {"role": "user", "content": "remove 'Other' from above data from RDS Spend"},
        {"role": "assistant", "content": "### 🗄️ CloudHealth RDS Spend Analysis: Instance Type Breakdown\n- Target Billing Period: Year-to-Date (2025-12 to 2026-10)"},
        {"role": "user", "content": "breakdown the above usage by Lease type"}
    ]

    res = client.generate(chat_history, mcp=mock_mcp)

    # Verify query executed against AWS_CUR grouping by pricing_term and lineItem_LineItemType
    query_calls = [c for c in mock_mcp.calls if c[0] == "execute_datasource_query"]
    assert len(query_calls) > 0, "Expected execute_datasource_query call"
    sql_stmt = query_calls[0][1]["queryInput"]["sqlStatement"]
    assert "FROM AWS_CUR" in sql_stmt
    assert "pricing_term" in sql_stmt
    assert "lineItem_LineItemType" in sql_stmt

    # Verify response contains all canonical lease types
    assert "On-Demand" in res
    assert "Reserved Instances (RI)" in res
    assert "Savings Plans" in res
    assert "Spot Instances" in res
    assert "Amazon RDS" in res
    # Verify Spot note for RDS
    assert "Spot instances are not supported for Amazon RDS" in res
    # Verify commitment coverage %
    assert "Commitment Coverage" in res


def test_column_synonyms_and_expanded_context():
    import cleo_agent
    from cleo_query import COLUMN_SYNONYMS, _deterministic_understand_query

    # 1. Verify COLUMN_SYNONYMS catalog exists and contains key dimensions
    assert "SubaccountId" in COLUMN_SYNONYMS
    assert "member account" in COLUMN_SYNONYMS["SubaccountId"]["synonyms"]
    assert "BillingAccountId" in COLUMN_SYNONYMS
    assert "master account" in COLUMN_SYNONYMS["BillingAccountId"]["synonyms"]
    assert "ServiceCategory" in COLUMN_SYNONYMS
    assert "macro service" in COLUMN_SYNONYMS["ServiceCategory"]["synonyms"]
    assert "ServiceSubcategory" in COLUMN_SYNONYMS
    assert "granular service" in COLUMN_SYNONYMS["ServiceSubcategory"]["synonyms"]
    assert "product_storageClass" in COLUMN_SYNONYMS
    assert "storage tier" in COLUMN_SYNONYMS["product_storageClass"]["synonyms"]
    assert "product_volumeType" in COLUMN_SYNONYMS
    assert "disk type" in COLUMN_SYNONYMS["product_volumeType"]["synonyms"]
    assert "HardwareFamily" in COLUMN_SYNONYMS
    assert "h100 vs a100" in COLUMN_SYNONYMS["HardwareFamily"]["synonyms"]

    # 2. Verify build_llm_schema_context() includes annotations and the synonyms dictionary
    ctx = cleo_agent.build_llm_schema_context()
    assert "COLUMN & DIMENSION NATURAL LANGUAGE SYNONYMS DICTIONARY" in ctx
    assert "SubaccountId / lineItem_UsageAccountId / SubscriptionId" in ctx
    assert "ServiceCategory / MeterCategory" in ctx
    assert "LeaseType (pricing_term & lineItem_LineItemType)" in ctx

    # 3. Verify deterministic query understanding routes synonyms correctly
    test_cases = [
        ("azure cost by member account", "SubaccountId", "account"),
        ("aws spend by master account", "BillingAccountId", "billing_account"),
        ("cloud spend by macro service", "ServiceCategory", "service_category"),
        ("gcp cost by granular service", "ServiceSubcategory", "service_subcategory"),
        ("aws spend by datacenter", "RegionId", "region"),
        ("aws spend by asset", "ResourceId", "resource"),
        ("s3 cost by storage tier", "product_storageClass", "storage_class"),
        ("ebs cost by disk type", "product_volumeType", "volume_type"),
        ("ai spend by foundation model", "Model", "model"),
        ("ai spend by accelerator family", "HardwareFamily", "hardware_family"),
        ("cloud spend by commercial model", "LeaseType", "lease_type"),
    ]
    for q, expected_dim, expected_bdown in test_cases:
        res = _deterministic_understand_query([{"role": "user", "content": q}])
        assert res.get("target_dimension") == expected_dim, f"Query '{q}' failed: expected target_dimension '{expected_dim}', got '{res.get('target_dimension')}'"
        assert expected_bdown in (res.get("breakdowns") or []), f"Query '{q}' failed: expected breakdown '{expected_bdown}' in {res.get('breakdowns')}"

    # 4. Verify quantity triggers with units/quantities
    q_res = _deterministic_understand_query([{"role": "user", "content": "ec2 usage quantity in units"}])
    assert q_res.get("metric_type") == "quantity"


def test_cleo_enhancements_suite():
    """Validates the roadmap improvements: cross-session continuation, live specs, fuzzy typos, empty diagnostics."""
    import tempfile, pathlib
    from cleo_memory import save_last_query, load_last_query, _LAST_QUERY_PATH
    from cleo_query import _detect_contextual_continuation, _deterministic_understand_query
    from cleo_agent import merge_live_metadata_into_curated_specs, CURATED_DATASET_SPECS, _format_empty_data_notice

    # 1. Cross-Session Continuation Persistence
    orig_path = _LAST_QUERY_PATH
    with tempfile.NamedTemporaryFile(suffix=".json") as tf:
        test_file = pathlib.Path(tf.name)
        import cleo_memory
        cleo_memory._LAST_QUERY_PATH = test_file

        save_last_query("forecast", "MULTICLOUD_FOCUS_COST_AND_USAGE", "forecast for aws", {"from": "2026-01", "to": "2026-12"})
        loaded = load_last_query()
        assert loaded.get("dataset") == "MULTICLOUD_FOCUS_COST_AND_USAGE"
        assert loaded.get("sql") == "forecast for aws"

        # Verify single-turn continuation picks up saved session query
        cont_res = _detect_contextual_continuation([{"role": "user", "content": "same for azure"}])
        assert cont_res.get("is_continuation") is True
        assert cont_res.get("new_cloud") == "azure"

        cleo_memory._LAST_QUERY_PATH = orig_path

    # 2. Dynamic Live Metadata Enrichment into CURATED_DATASET_SPECS
    test_live = {
        "CUSTOM_FINOPS_FLEET": {
            "displayName": "Custom FinOps Fleet",
            "columns": [
                {"name": "FleetCost", "type": "MEASURE", "dataType": "DOUBLE"},
                {"name": "FleetId", "type": "DIMENSION", "dataType": "STRING"}
            ]
        }
    }
    merge_live_metadata_into_curated_specs(test_live)
    assert "CUSTOM_FINOPS_FLEET" in CURATED_DATASET_SPECS
    assert "FleetCost" in CURATED_DATASET_SPECS["CUSTOM_FINOPS_FLEET"]["priority_measures"]
    assert "FleetId" in CURATED_DATASET_SPECS["CUSTOM_FINOPS_FLEET"]["priority_dimensions"]

    # 3. Contextual Empty Data Diagnostics
    notice = _format_empty_data_notice("AWS_CUR", "instance_type", "EC2", "2026-06")
    assert "FinOps Diagnostic Tips" in notice
    assert "Billing Ingestion Latency" in notice
    assert "2026-06" in notice

    # 4. Fuzzy service matching in deterministic router
    fuzzy_q = _deterministic_understand_query([{"role": "user", "content": "amazonec2"}])
    assert fuzzy_q.get("service") == "AmazonEC2"

    # 5. Anomaly date extraction (date granularity vs month)
    sample_row = {"end_date": "2026-10-08T00:00:00Z", "month": "2026-10"}
    extracted_date = (sample_row.get("end_date") or "").split("T")[0].split()[0] or sample_row.get("month", "")
    assert extracted_date == "2026-10-08"

    # 6. Daily anomaly granularity & Marketplace tag
    daily_row = {"day": "2026-10-07", "marketplace": "Yes", "account_id": "352755461691", "account_name": "demo-aws-bedrock"}
    date_val = daily_row.get("day") or (daily_row.get("end_date") or "").split("T")[0]
    mp_tag = " 🛒 *(Marketplace)*" if daily_row.get("marketplace", "").lower() in ("yes", "true", "1") else ""
    acc_disp = f"`{daily_row['account_name']}` (`{daily_row['account_id']}`)"
    assert date_val == "2026-10-07"
    assert "Marketplace" in mp_tag
    assert "demo-aws-bedrock" in acc_disp


def test_extract_cost_threshold():
    from cleo_query import extract_cost_threshold

    # "more than 50$"
    res1 = extract_cost_threshold("show me the impact cost more than 50$")
    assert res1["min"] == 50.0
    assert res1["operator"] == ">"

    # "cost impact > $100"
    res2 = extract_cost_threshold("cost impact > $100")
    assert res2["min"] == 100.0
    assert res2["operator"] == ">"

    # "under $20"
    res3 = extract_cost_threshold("show anomalies under $20")
    assert res3["max"] == 20.0
    assert res3["operator"] == "<"

    # "between $50 and $200"
    res4 = extract_cost_threshold("filter between $50 and $200")
    assert res4["min"] == 50.0
    assert res4["max"] == 200.0
    assert res4["operator"] == "between"

    # "$50+"
    res5 = extract_cost_threshold("anomalies with impact $50+")
    assert res5["min"] == 50.0
    assert res5["operator"] == ">="

    # "at least $75.50"
    res6 = extract_cost_threshold("cost impact at least $75.50")
    assert res6["min"] == 75.50
    assert res6["operator"] == ">="


def test_continuation_with_cost_threshold_drilldown():
    from cleo_query import _detect_contextual_continuation, _deterministic_understand_query

    # Conversation history: user asked for multi-cloud anomalies, Cleo returned table
    history = [
        {"role": "user", "content": "Show my top cost anomalies across all clouds for this month"},
        {
            "role": "assistant",
            "content": (
                "### 🚨 CloudHealth Cost Anomaly Detection: Top 12 Active Anomalies (2026-10)\n\n"
                "- Total Identified Anomaly Impact: +$2,834.57 across 12 anomalies\n"
                "| # | Cloud | Service / Asset | Status | Cost Impact |"
            ),
        },
        {"role": "user", "content": "show me the impact cost more than 50$"},
    ]

    cont = _detect_contextual_continuation(history)
    assert cont["is_continuation"] is True
    assert cont["prior_query_type"] == "anomalies"
    assert cont["min_impact"] == 50.0
    assert cont["new_cloud"] == "all"
    assert "cost impact > $50.00" in cont["expanded_query"]

    und = _deterministic_understand_query(history)
    assert und["intent"] == "anomalies"
    assert und["is_new_data_fetch"] is True
    assert und["min_impact"] == 50.0
    assert und["cloud"] == "all"


def test_anomaly_threshold_execution_and_presentation():
    import json
    from cleo_agent import AIClient

    class MockMCP:
        def __init__(self):
            self.calls = []

        def call_tool(self, name, args):
            self.calls.append((name, args))
            sql = args.get("queryInput", {}).get("sqlStatement", "")
            if "AWS_COST_ANOMALY" in sql:
                if "CostImpact >= 50" in sql:
                    csv_data = '"service","cost_impact","impact_pct","impact_type","status","duration_days","region","account_id","day","month"\n'
                else:
                    csv_data = (
                        '"service","cost_impact","impact_pct","impact_type","status","duration_days","region","account_id","day","month"\n'
                        '"AmazonPinpoint","21.36","702.6","Spike","ACTIVE","0","sa-east-1","964862064788","2026-10-07","2026-10"\n'
                    )
            elif "GCP_COST_ANOMALY" in sql:
                csv_data = (
                    '"service","cost_impact","impact_pct","impact_type","status","duration_days","region","account_id","day","month"\n'
                    '"NetApp Volumes","746.03","125.4","Spike","ACTIVE","0","us-central1","gcp-project-1","2026-10-06","2026-10"\n'
                    '"Vertex AI","719.15","85.2","Spike","ACTIVE","0","us-central1","gcp-project-1","2026-10-05","2026-10"\n'
                )
            elif "AZURE_COST_ANOMALY" in sql:
                if "CostImpact >= 50" in sql:
                    csv_data = '"service","cost_impact","impact_pct","impact_type","status","duration_days","region","account_id","day","month"\n'
                else:
                    csv_data = (
                        '"service","cost_impact","impact_pct","impact_type","status","duration_days","region","account_id","day","month"\n'
                        '"Data Factory","22.13","45.0","Spike","ACTIVE","0","eastus","sub-1","2026-10-04","2026-10"\n'
                    )
            else:
                csv_data = ""
            return {"content": [{"type": "text", "text": json.dumps({"csv": csv_data})}]}

    mock_mcp = MockMCP()
    client = AIClient("direct", {}, [])

    chat_history = [
        {"role": "user", "content": "Show my top cost anomalies across all clouds for this month"},
        {
            "role": "assistant",
            "content": (
                "### 🚨 CloudHealth Cost Anomaly Detection: Top 12 Active Anomalies (2026-10)\n\n"
                "- Total Identified Anomaly Impact: +$2,834.57 across 12 anomalies\n"
                "| # | Cloud | Service / Asset | Status | Cost Impact |\n"
                "| 1 | 🟠 AWS | `AmazonPinpoint` | 🔴 **ACTIVE** | **+$21.36** |\n"
            ),
        },
        {"role": "user", "content": "show me the impact cost more than 50$"},
    ]

    res = client.generate(chat_history, mcp=mock_mcp)

    # 1. Verify SQL executed contained CostImpact >= 50.0
    anom_calls = [c for c in mock_mcp.calls if c[0] == "execute_datasource_query"]
    assert len(anom_calls) > 0, "Expected execute_datasource_query calls"
    for _, call_args in anom_calls:
        sql_stmt = call_args["queryInput"]["sqlStatement"]
        assert "CostImpact >= 50" in sql_stmt, f"Expected CostImpact >= 50 in SQL statement: {sql_stmt}"

    # 2. Verify table title reflects threshold
    assert "Cost Impact > $50.00" in res

    # 3. Verify presented rows contain only items > $50 (NetApp Volumes, Vertex AI) and no AWS items (< $50)
    assert "NetApp Volumes" in res
    assert "Vertex AI" in res
    assert "AmazonPinpoint" not in res
    assert "$746.03" in res
    assert "$719.15" in res


def test_percentage_and_variance_thresholds():
    from cleo_query import extract_cost_threshold

    # "variance > 100%"
    res1 = extract_cost_threshold("show anomalies with variance > 100%")
    assert res1["min_pct"] == 100.0

    # "spike over 50%"
    res2 = extract_cost_threshold("spikes over 50%")
    assert res2["min_pct"] == 50.0

    # "variance under 20%"
    res3 = extract_cost_threshold("variance under 20%")
    assert res3["max_pct"] == 20.0

    # "50%+"
    res4 = extract_cost_threshold("anomalies 50%+")
    assert res4["min_pct"] == 50.0


def test_negative_exclusions():
    from cleo_query import extract_negative_exclusions

    # "exclude EC2 and without Azure"
    ex1 = extract_negative_exclusions("show top spend exclude EC2 and without Azure")
    assert "AmazonEC2" in ex1["services"]
    assert "azure" in ex1["clouds"]

    # "remove netapp volumes and except Pinpoint"
    ex2 = extract_negative_exclusions("remove netapp volumes and except Pinpoint")
    assert any("netapp" in s.lower() for s in ex2["services"] + ex2["raw_terms"])
    assert any("pinpoint" in s.lower() for s in ex2["services"] + ex2["raw_terms"])

    # "without gcp"
    ex3 = extract_negative_exclusions("top services without gcp")
    assert "gcp" in ex3["clouds"]


def test_ordinal_resolution():
    from cleo_query import resolve_ordinal_reference_from_history

    sample_table = (
        "### 🚨 CloudHealth Cost Anomaly Detection: Top 12 Active Anomalies (2026-10)\n\n"
        "- Total Identified Anomaly Impact: +$2,834.57 across 12 anomalies\n"
        "| # | Cloud | Service / Asset | Status | Cost Impact |\n"
        "|:---|:---|:---|:---|:---|\n"
        "| 1 | 🟠 AWS | `AmazonPinpoint` | 🔴 **ACTIVE** | **+$21.36** (702.6% spike) |\n"
        "| 2 | 🔵 GCP | `NetApp Volumes` | 🔴 **ACTIVE** | **+$746.03** (125.4% spike) |\n"
        "| 3 | 🔷 Azure | `Data Factory` | 🔴 **ACTIVE** | **+$22.13** (45.0% spike) |\n"
    )

    history = [
        {"role": "user", "content": "show anomalies"},
        {"role": "assistant", "content": sample_table},
    ]

    # "#2"
    r2 = resolve_ordinal_reference_from_history("tell me more about #2", history)
    assert r2 is not None
    assert "netapp" in r2["service"].lower()
    assert r2["cloud"] == "gcp"

    # "the first one"
    r1 = resolve_ordinal_reference_from_history("what happened to the first one?", history)
    assert r1 is not None
    assert "pinpoint" in r1["service"].lower()
    assert r1["cloud"] == "aws"

    # "item 3"
    r3 = resolve_ordinal_reference_from_history("drill down into item 3", history)
    assert r3 is not None
    assert "data factory" in r3["service"].lower()
    assert r3["cloud"] == "azure"


def test_contextual_relative_dates():
    from cleo_query import parse_query_time_context

    # With context_ym="2026-06", "previous month" should resolve to 2026-05
    ctx_prev = parse_query_time_context("show spend for previous month", context_ym="2026-06")
    assert ctx_prev["target_ym"] == "2026-05"

    # "the month before that" with context_ym="2026-06" -> 2026-05
    ctx_before = parse_query_time_context("what about the month before that?", context_ym="2026-06")
    assert ctx_before["target_ym"] == "2026-05"

    # "that month" with context_ym="2026-06" -> 2026-06
    ctx_that = parse_query_time_context("breakdown for that month", context_ym="2026-06")
    assert ctx_that["target_ym"] == "2026-06"


def test_sort_and_limit_adjustments():
    from cleo_query import extract_sort_refinement, extract_limit_adjustment

    # Sorting
    s1 = extract_sort_refinement("sort by variance")
    assert s1["field"] == "variance"
    assert s1["direction"] == "desc"

    s2 = extract_sort_refinement("lowest first")
    assert s2["field"] == "cost"
    assert s2["direction"] == "asc"

    s3 = extract_sort_refinement("longest duration")
    assert s3["field"] == "duration"
    assert s3["direction"] == "desc"

    # Limit adjustments
    assert extract_limit_adjustment("show top 10 instead") == 10
    assert extract_limit_adjustment("expand to 20") == 20
    assert extract_limit_adjustment("make it 5") == 5


def test_multi_cloud_continuation_pivots():
    from cleo_query import _detect_contextual_continuation

    # Prior anomalies query, user follows up with a GCP service
    history_gcp = [
        {"role": "user", "content": "Show all anomalies"},
        {"role": "assistant", "content": "| 1 | GCP | NetApp Volumes | ACTIVE | $746.03 |"},
        {"role": "user", "content": "NetApp Volumes"},
    ]
    cont = _detect_contextual_continuation(history_gcp)
    assert cont["is_continuation"] is True
    assert cont["new_service"] is not None
    assert "netapp" in cont["new_service"].lower()

    # User follows up with a region code
    history_reg = [
        {"role": "user", "content": "Show my top spend"},
        {"role": "assistant", "content": "| AWS | EC2 | $1,200 |"},
        {"role": "user", "content": "sa-east-1"},
    ]
    cont_reg = _detect_contextual_continuation(history_reg)
    assert cont_reg["is_continuation"] is True
    assert cont_reg["new_region"] == "sa-east-1"


def test_general_spend_filtering_and_exclusions_end_to_end():
    import json
    from cleo_agent import AIClient

    class MockMCPSpend:
        def __init__(self):
            self.calls = []

        def call_tool(self, name, args):
            self.calls.append((name, args))
            csv_data = (
                '"provider","service","cost"\n'
                '"AWS","Amazon Elastic Compute Cloud - Compute","1500.00"\n'
                '"AWS","Amazon Relational Database Service","800.00"\n'
                '"AWS","Amazon Simple Storage Service","200.00"\n'
                '"Azure","Virtual Machines","600.00"\n'
                '"GCP","Google Cloud Storage","150.00"\n'
            )
            return {"content": [{"type": "text", "text": json.dumps({"csv": csv_data})}]}

    mock_mcp = MockMCPSpend()
    client = AIClient("direct", {}, [])

    # Conversation: user asks for top services, then asks to exclude EC2 and filter > $250
    history = [
        {"role": "user", "content": "Show top services across all clouds"},
        {"role": "assistant", "content": "| AWS | EC2 | $1,500.00 |\n| AWS | RDS | $800.00 |\n| Azure | Virtual Machines | $600.00 |\n| AWS | S3 | $200.00 |\n| GCP | Storage | $150.00 |"},
        {"role": "user", "content": "exclude EC2 and only show cost > 250"},
    ]

    res = client.generate(history, mcp=mock_mcp)
    assert "Amazon Elastic Compute Cloud" not in res
    assert "Relational Database Service" in res
    assert "Virtual Machines" in res
    # S3 ($200) and GCP Storage ($150) should be excluded because < $250
    assert "Simple Storage Service" not in res
    assert "Google Cloud Storage" not in res


def test_no_insights_requested_predicate_and_stripping():
    from cleo_charts import is_no_insights_requested, strip_finops_insights

    # 1. Predicate testing
    trues = [
        "no Insights needed",
        "show top anomalies, no Insights needed",
        "show top services without insights",
        "data only",
        "just data",
        "only the data",
        "raw data only",
        "show cost forecast, insights not needed",
        "no recommendations please",
        "skip insights",
        "skip the insights",
        "omit insights",
        "without any insights",
        "don't provide any insights",
        "no need for insights",
        "without commentary",
        "no commentary",
        "without observations",
        "no analysis",
        "without analysis",
    ]
    falses = [
        "what are the insights",
        "show me insights for EC2",
        "give me insights",
        "any insights on RDS?",
    ]
    for t in trues:
        assert is_no_insights_requested(t) is True, f"Failed for {t}"
    for f in falses:
        assert is_no_insights_requested(f) is False, f"Failed for {f}"

    # 2. Stripping test
    md_with_insights = (
        "### 📊 CloudHealth Cost Forecast\n\n"
        "| Month | Cost |\n"
        "|:---|:---|\n"
        "| Jan | $100 |\n\n"
        "**💡 FinOps Insights:**\n"
        "- **Growth**: Spend grew by 10%.\n"
        "- **Commitments**: Layer Savings Plans.\n\n"
        "*Source: AWS_CUR via CloudHealth FlexReports.*"
    )
    stripped = strip_finops_insights(md_with_insights)
    assert "💡 FinOps Insights" not in stripped
    assert "| Jan | $100 |" in stripped
    assert "*Source: AWS_CUR via CloudHealth FlexReports.*" in stripped

    # 3. Stripping multi-service heading and blockquotes
    md_heading = (
        "### 📊 Multi-Service Spend\n\n"
        "| Service | Cost |\n"
        "|:---|:---|\n"
        "| EC2 | $100 |\n\n"
        "---\n\n"
        "### 💡 FinOps Insights & Multi-Service Optimization Levers:\n\n"
        "- **EC2**: Graviton migration.\n\n"
        "> **💡 FinOps Foundation Practice Note**:\n"
        "> Azure Hybrid Benefit applies Software Assurance...\n\n"
        "*Source: CloudHealth FlexReports.*"
    )
    stripped_heading = strip_finops_insights(md_heading)
    assert "💡 FinOps" not in stripped_heading
    assert "| EC2 | $100 |" in stripped_heading
    assert "*Source: CloudHealth FlexReports.*" in stripped_heading


def test_no_insights_needed_agent_execution():
    from cleo_agent import AIClient
    import json

    class MockMCP:
        def __init__(self, csv_data=""):
            self.csv_data = csv_data
            self.calls = []

        def call_tool(self, name, args):
            self.calls.append((name, args))
            if name == "execute_datasource_query":
                return {"content": [{"type": "text", "text": json.dumps({"csv": self.csv_data})}]}
            return {}

    anom_csv = (
        '"service","cost_impact","impact_pct","impact_type","status","duration_days","region","account_id","day","month"\n'
        '"AmazonEC2","550.00","25.0","Spike","ACTIVE","3","us-east-1","123456789012","2026-10-01","2026-10"\n'
        '"AmazonRDS","320.00","15.0","Spike","ACTIVE","2","us-west-2","123456789012","2026-10-02","2026-10"\n'
    )
    client = AIClient("direct", {}, [])

    # Query WITH insights
    res_with = client.generate([{"role": "user", "content": "Show top anomalies"}], mcp=MockMCP(anom_csv))
    assert "💡" in res_with
    assert "FinOps" in res_with

    # Query with "no Insights needed"
    res_without = client.generate([{"role": "user", "content": "Show top anomalies, no Insights needed"}], mcp=MockMCP(anom_csv))
    assert "AmazonEC2" in res_without
    assert "550.00" in res_without
    assert "💡" not in res_without
    assert "FinOps" not in res_without


def test_multiturn_context_passing_and_disambiguation():
    import json
    from cleo_query import _detect_contextual_continuation
    from cleo_agent import AIClient

    cust_map = {"Acme Corp": "12345", "Globex Inc": "67890"}

    # 1. Deterministic continuation inheritance across multiple turns
    messages = [
        {"role": "user", "content": "Show EC2 spend for Acme Corp in June 2026"},
        {"role": "assistant", "content": "### 📊 EC2 Cost Analysis for Acme Corp (2026-06)\n| Service | Cost |\n| EC2 | $100 |"},
        {"role": "user", "content": "break down by instance type"},
        {"role": "assistant", "content": "### 📊 EC2 Cost Breakdown by Instance Type\n| Type | Cost |\n| t3.micro | $50 |"},
        {"role": "user", "content": "what about Azure?"}  # Ambiguous/terse turn 3
    ]

    cont = _detect_contextual_continuation(messages, cust_map=cust_map)
    assert cont["is_continuation"] is True
    # Customer should be inherited from Turn 1 even though Turn 2 didn't mention it
    assert cont["new_customer"] == "Acme Corp"
    assert cont["new_cloud"] == "azure"

    # 2. LLM-First Intent prompt receives multi-turn history & active entities
    captured_prompts = []
    class MockLLMClient(AIClient):
        def _call_active_llm(self, msgs, stats_out=None, on_token=None):
            captured_prompts.append(msgs)
            return json.dumps({
                "intent": "fetch_data",
                "cloud": "azure",
                "service": "Virtual Machines",
                "customer": "Acme Corp",
                "metric_type": "cost",
                "target_dimension": "product_InstanceType",
                "target_ym": "2026-06",
                "timeframe_months": 1,
                "timeframe_days": None,
                "min_cost": None,
                "max_cost": None,
                "min_impact": None,
                "max_impact": None,
                "min_pct": None,
                "max_pct": None,
                "limit": None,
                "breakdowns": ["instance_type"],
                "chart_types": ["bar"],
                "include_chart": True,
                "include_mom": True,
                "is_new_data_fetch": True,
                "corrected_query": "Show Azure VM spend for Acme Corp in June 2026 broken down by instance type"
            }), None

    client = MockLLMClient("gemini", {}, [])
    client._cust_map_cache = cust_map
    understood = client._understand_query(messages)

    assert len(captured_prompts) == 1
    llm_user_prompt = captured_prompts[0][1]["content"]
    assert "User: Show EC2 spend for Acme Corp in June 2026" in llm_user_prompt
    assert "Assistant: ### 📊 EC2 Cost Analysis for Acme Corp (2026-06)" in llm_user_prompt
    assert "User: break down by instance type" in llm_user_prompt
    assert "Current User Query: \"what about Azure?\"" in llm_user_prompt
    assert "Active Customer: Acme Corp" in llm_user_prompt
    assert understood["customer"] == "Acme Corp"
    assert understood["cloud"] == "azure"


def test_unconfigure_and_clear_token():
    import os
    from cleo_agent import _save_config, _load_config
    from cleo_server import save_token, delete_token, TokenUpdateRequest

    # 1. Test _save_config None removes key
    _save_config({"TEST_TEMP_KEY": "temp_secret"})
    assert _load_config().get("TEST_TEMP_KEY") == "temp_secret"
    _save_config({"TEST_TEMP_KEY": None})
    assert "TEST_TEMP_KEY" not in _load_config()

    # 2. Test save_token and delete_token for frontier provider
    res_save = save_token(TokenUpdateRequest(engine="gemini", token="mock-gemini-key-999"))
    assert res_save["status"] == "ok"
    assert _load_config().get("GEMINI_API_KEY") == "mock-gemini-key-999"
    assert os.environ.get("GEMINI_API_KEY") == "mock-gemini-key-999"

    # Now unconfigure/clear token
    res_del = delete_token("gemini")
    assert res_del["status"] == "ok"
    assert res_del["unconfigured"] == "gemini"
    assert "GEMINI_API_KEY" not in _load_config()
    assert os.environ.get("GEMINI_API_KEY") is None

    # 3. Test empty token via POST also triggers unconfigure
    save_token(TokenUpdateRequest(engine="openai", token="mock-openai-key"))
    assert _load_config().get("OPENAI_API_KEY") == "mock-openai-key"
    res_empty = save_token(TokenUpdateRequest(engine="openai", token="   "))
    assert res_empty["status"] == "ok"
    assert "OPENAI_API_KEY" not in _load_config()
    assert os.environ.get("OPENAI_API_KEY") is None


def test_top_services_continuation_and_no_insights():
    import json
    from cleo_agent import AIClient
    from cleo_query import extract_negative_exclusions, _detect_contextual_continuation
    from cleo_charts import is_no_insights_requested

    query = "give me the above data for last 3 months without any isights"

    # 1. Negative exclusions does not mistake 'any isights' for a cloud service
    excl = extract_negative_exclusions(query)
    assert "any isights" not in excl["services"]
    assert "isights" not in excl["services"]
    assert excl["services"] == []

    # 2. Typos like 'isights' are recognized as insight suppression
    assert is_no_insights_requested(query) is True

    # 3. Continuation context preserves top_services prior type
    messages = [
        {"role": "user", "content": "Show our top cloud services across AWS, Azure, and GCP this month"},
        {"role": "assistant", "content": "### 📊 CloudHealth Multi-Cloud Spend Analysis: Top Services Across All Clouds — MTD (October 2026)\n\n| Cloud Provider | Service Category | Cost | % of Total |\n|:---|:---|:---|:---|\n| Google Cloud | Compute Engine | $548,696.75 | 15.5% |\n| Azure | Virtual Machines | $386,932.45 | 10.9% |\n| AWS | ComputeSavingsPlans | $367,012.32 | 10.3% |\n\n| **Total Multi-Cloud Spend** | | **$1,302,641.52** | **100.0%** |"},
        {"role": "user", "content": query}
    ]
    cont = _detect_contextual_continuation(messages)
    assert cont["is_continuation"] is True
    assert cont["prior_query_type"] == "top_services"
    assert cont["new_cloud"] == "all"

    # 4. End-to-end execution with Mock LLM failure & Mock MCP returns multi-month table without insights
    class MockMCP:
        def call_tool(self, name, args):
            csv_data = (
                "month,provider,service,cost\n"
                "2026-08,Google Cloud,Compute Engine,500000.0\n"
                "2026-08,Azure,Virtual Machines,350000.0\n"
                "2026-09,Google Cloud,Compute Engine,520000.0\n"
                "2026-09,Azure,Virtual Machines,370000.0\n"
                "2026-10,Google Cloud,Compute Engine,548696.75\n"
                "2026-10,Azure,Virtual Machines,386932.45\n"
            )
            return {"content": [{"text": json.dumps({"csv": csv_data})}]}

    class FailingLLMClient(AIClient):
        def _call_active_llm(self, msgs, stats_out=None, on_token=None):
            # Simulate local LLM hallucinating unsupported_capability on continuation
            return json.dumps({
                "intent": "unsupported_capability",
                "is_new_data_fetch": False,
                "corrected_query": "Unable to fetch data as no previous data was provided in this conversation"
            }), None

    client = FailingLLMClient("ollama", {}, [])
    res = client._generate_impl(messages, mcp=MockMCP())

    assert "CloudHealth Spend Analysis: Top Multi-Cloud Services by Spend — Last 3 Months" in res
    assert "Compute Engine" in res
    assert "Virtual Machines" in res
    assert "💡 FinOps Insights" not in res


def test_mtd_forecast_generation():
    from cleo_agent import AIClient
    import calendar
    import json

    # 1. Verify parse_query_time_context returns is_mtd correctly
    ctx_mtd = parse_query_time_context("top services MTD")
    assert ctx_mtd["is_mtd"] is True
    ctx_this_mo = parse_query_time_context("show AWS cost this month")
    assert ctx_this_mo["is_mtd"] is True
    ctx_hist = parse_query_time_context("show spend for July 2026")
    assert ctx_hist["is_mtd"] is False
    ctx_multimo = parse_query_time_context("show top services for last 3 months")
    assert ctx_multimo["is_mtd"] is False

    now_dt = datetime.date.today()
    days_in_month = calendar.monthrange(now_dt.year, now_dt.month)[1]
    days_elapsed = max(now_dt.day, 1)
    expected_factor = days_in_month / days_elapsed

    class MockMCP:
        def __init__(self, empty_first=False):
            self.empty_first = empty_first
            self.exec_count = 0

        def call_tool(self, name, args):
            if name == "execute_datasource_query":
                self.exec_count += 1
                if self.empty_first and self.exec_count == 1:
                    return {"content": [{"text": json.dumps({"csv": ""})}]}
            sql = args.get("queryInput", {}).get("sqlStatement", "")
            if "SUM(EffectiveCost)" in sql and "GROUP BY" not in sql:
                return {"content": [{"text": json.dumps({"csv": "cost\n15000.0\n"})}]}
            csv_data = (
                "provider,service,cost\n"
                "AWS,Amazon EC2,10000.0\n"
                "Azure,Virtual Machines,5000.0\n"
            )
            return {"content": [{"text": json.dumps({"csv": csv_data})}]}

    client = AIClient("direct", {}, [])

    # 2. Test MTD query with sufficient days (days_elapsed >= 3)
    if days_elapsed >= 3:
        res = client._generate_impl([{"role": "user", "content": "show top services MTD"}], mcp=MockMCP())
        assert "Month-End Forecast" in res
        assert "MTD Cost" in res
        assert "Run-Rate Projection Notice" in res
        assert f"{expected_factor:.2f}x" in res
        ec2_proj = 10000.0 * expected_factor
        assert f"${ec2_proj:,.2f}" in res
        tot_proj = 15000.0 * expected_factor
        assert f"${tot_proj:,.2f}" in res
    else:
        res = client._generate_impl([{"role": "user", "content": "show top services MTD"}], mcp=MockMCP())
        assert "Month-End Forecast" not in res
        assert "Run-Rate Projection Notice" not in res

    # 3. Test historical closed month (July 2026) -> No Forecast column
    res_hist = client._generate_impl([{"role": "user", "content": "show top services in July 2026"}], mcp=MockMCP())
    assert "Month-End Forecast" not in res_hist
    assert "Run-Rate Projection Notice" not in res_hist

    # 4. Test multi-month query -> No Forecast column
    res_multi = client._generate_impl([{"role": "user", "content": "show top services for last 3 months"}], mcp=MockMCP())
    assert "Month-End Forecast" not in res_multi
    assert "Run-Rate Projection Notice" not in res_multi

    # 5. Test Fallback when open month returns 0 rows (falling back to last month) -> No Forecast column
    res_fb = client._generate_impl([{"role": "user", "content": "show top services MTD"}], mcp=MockMCP(empty_first=True))
    assert "FinOps Ingestion Notice" in res_fb
    assert "Month-End Forecast" not in res_fb
    assert "Run-Rate Projection Notice" not in res_fb

    # 6. Test AWS-specific MTD query
    if days_elapsed >= 3:
        res_aws = client._generate_impl([{"role": "user", "content": "show top AWS services MTD"}], mcp=MockMCP())
        assert "Month-End Forecast" in res_aws
        assert "MTD Cost" in res_aws
        assert "Run-Rate Projection Notice" in res_aws

    # 7. Test continuation query with non-data LLM intent doesn't crash on unbound cont_ctx
    class UnboundCheckLLM(AIClient):
        def _understand_query(self, msgs, mcp=None):
            return {"intent": "other", "is_new_data_fetch": False}

    check_client = UnboundCheckLLM("direct", {}, [])
    res_cont = check_client._generate_impl([
        {"role": "assistant", "content": "prior table"},
        {"role": "user", "content": "give me the above data for last 3 months"}
    ], mcp=MockMCP())
    assert res_cont is not None


def test_multimonth_service_mom_and_single_chart():
    from cleo_agent import AIClient
    import json
    class MultiMonthMockMCP:
        def call_tool(self, name, args):
            csv_data = (
                "month,provider,service,cost\n"
                "2026-07,Google Cloud,Compute Engine,2500000.0\n"
                "2026-07,Azure,Virtual Machines,1500000.0\n"
                "2026-08,Google Cloud,Compute Engine,3000000.0\n"
                "2026-08,Azure,Virtual Machines,1400000.0\n"
                "2026-09,Google Cloud,Compute Engine,2700000.0\n"
                "2026-09,Azure,Virtual Machines,1600000.0\n"
            )
            return {"content": [{"text": json.dumps({"csv": csv_data})}]}

    client = AIClient("direct", {}, [])
    res = client._generate_impl([
        {"role": "user", "content": "give me the top cloud services for last 3 months and provide MOM as well"}
    ], mcp=MultiMonthMockMCP())

    assert "MoM Change" in res
    assert "Jul 2026" in res
    assert "Aug 2026" in res
    assert "Sep 2026" in res
    assert "Compute Engine" in res
    assert "-$300,000.00 (-10.0%)" in res
    assert res.count("```chart") == 1
    assert '"type": "line"' in res


def test_cloud_support_charges_ytd_mom():
    from cleo_agent import AIClient
    import json
    class SupportMockMCP:
        def call_tool(self, name, args):
            csv_data = (
                "month,provider,service,billed_cost,cost\n"
                "2026-01,Google Cloud,Support,119148.92,119148.92\n"
                "2026-01,AWS,AWSSupportEnterprise,124.28,124.28\n"
                "2026-02,Google Cloud,Support,88833.10,88833.10\n"
                "2026-02,AWS,AWSSupportEnterprise,101.20,101.20\n"
                "2026-03,Google Cloud,Support,129697.10,129697.10\n"
                "2026-03,AWS,AWSSupportEnterprise,114.11,114.11\n"
            )
            return {"content": [{"text": json.dumps({"csv": csv_data})}]}

    client = AIClient("direct", {}, [])
    res = client._generate_impl([
        {"role": "user", "content": "give me the ytd data for support charges with MoM"}
    ], mcp=SupportMockMCP())

    assert "CloudHealth Support Charges Spend Analysis" in res
    assert "Google Cloud" in res
    assert "AWS" in res
    assert "MoM Change" in res
    assert "Combined Support Spend" in res
    assert res.count("```chart") == 1
    assert '"type": "line"' in res


def test_emr_and_networking_mappings_and_intent():
    from cleo_query import _deterministic_understand_query, extract_requested_service

    test_cases = [
        ("give me the ytd data for emr", "ElasticMapReduce", "aws"),
        ("give me the ytd data for networking", "Networking", "all"),
        ("give me the ytd data for savings plans", "ComputeSavingsPlans", "aws"),
        ("give me the ytd data for azure reservations", "Azure Reservations", "azure"),
        ("give me the ytd data for marketplace spend", "Marketplace", "all"),
        ("give me the ytd data for storage accounts", "Storage Accounts", "azure"),
        ("provide mtd numbers for kafka", "AmazonMSK", "aws"),
        ("gcp networking spend", "Networking", "gcp"),
        ("bare metal solution spend", "Bare Metal Solution", "gcp"),
        ("aws support", "AWSSupport", "aws"),
        ("aws business support", "AWSSupportBusiness", "aws"),
        ("aws developer support", "AWSSupportDeveloper", "aws"),
        ("aws enterprise support", "AWSSupportEnterprise", "aws"),
        ("enterprise on-ramp support", "AWSSupportEnterpriseOnRamp", "aws"),
    ]
    for q, expected_pcode, expected_prov in test_cases:
        u = _deterministic_understand_query([{"role": "user", "content": q}])
        assert u["intent"] in ("fetch_data", "finops_recommendations"), f"Failed intent for '{q}': {u['intent']}"
        svc = extract_requested_service(q)
        assert svc[0] == expected_pcode, f"Failed pcode for '{q}': expected {expected_pcode}, got {svc[0]}"
        assert getattr(svc, "provider", None) == expected_prov, f"Failed provider for '{q}': expected {expected_prov}, got {getattr(svc, 'provider', None)}"


def test_multicloud_service_major_inquiry_execution():
    from cleo_agent import AIClient
    import json

    class NetworkingMockMCP:
        def __init__(self):
            self.last_sql = ""

        def call_tool(self, name, args):
            self.last_sql = args.get("queryInput", {}).get("sqlStatement", "")
            csv_data = (
                "time_val,usage_type,operation,usage_qty,cost\n"
                "2026-08,Networking,Bytes,1000000,185400.50\n"
                "2026-09,Networking,Bytes,1100000,214130.26\n"
            )
            return {"content": [{"text": json.dumps({"csv": csv_data})}]}

    client = AIClient("direct", {}, [])
    mock_mcp = NetworkingMockMCP()
    res = client._generate_impl([
        {"role": "user", "content": "give me the data for networking"}
    ], mcp=mock_mcp)

    assert "MULTICLOUD_FOCUS_COST_AND_USAGE" in mock_mcp.last_sql
    assert "provider = 'all'" not in mock_mcp.last_sql
    assert "CloudHealth" in res
    assert "Networking" in res
    assert "185,400.50" in res or "214,130.26" in res


def test_llm_data_curation_pass_and_filtering():
    from cleo_agent import AIClient
    from cleo_charts import filter_charts_to_requested_type
    import json

    # ── 1. Test filter_charts_to_requested_type ──
    multi_chart_md = """### 📊 Top Services Breakdown

| Service | Spend |
|:---|:---|
| EC2 | $1,000.00 |

```chart
{
  "type": "bar",
  "title": "Bar Breakdown",
  "labels": ["EC2"],
  "values": [1000]
}
```

```chart
{
  "type": "line",
  "title": "Trend Line",
  "labels": ["Aug 2026", "Sep 2026"],
  "datasets": [{"label": "EC2", "data": [900, 1000]}]
}
```

```chart
{
  "type": "pie",
  "title": "Pie Share",
  "labels": ["EC2"],
  "values": [1000]
}
```
"""
    # Filter to line chart only
    filtered_line = filter_charts_to_requested_type(multi_chart_md, "only line chart is enough I think")
    assert "Trend Line" in filtered_line
    assert '"type": "line"' in filtered_line
    assert "Bar Breakdown" not in filtered_line
    assert "Pie Share" not in filtered_line
    assert "| EC2 | $1,000.00 |" in filtered_line

    # Filter to bar chart only
    filtered_bar = filter_charts_to_requested_type(multi_chart_md, "bar chart only please")
    assert "Bar Breakdown" in filtered_bar
    assert "Trend Line" not in filtered_bar
    assert "Pie Share" not in filtered_bar

    # Deduplicate identical charts
    duplicate_chart_md = """
```chart
{"type": "bar", "title": "A"}
```
```chart
{"type": "bar", "title": "A"}
```
"""
    deduped = filter_charts_to_requested_type(duplicate_chart_md, "show me the chart")
    assert deduped.count("```chart") == 1

    # ── 2. Test _is_curatable_data_response ──
    client = AIClient("direct", {}, [])
    assert client._is_curatable_data_response(multi_chart_md) is True
    assert client._is_curatable_data_response("### 🧹 Cache Cleared Successfully\nAll caches purged.") is False
    assert client._is_curatable_data_response("### 🤖 Active AI Engine & Model\nWe are using Qwen.") is False
    assert client._is_curatable_data_response("Hello! How can I help you today?") is False

    # ── 3. Test _verify_curated_response ──
    raw_table = "### Spend\n\n| Svc | Cost |\n|:---|:---|\n| EC2 | $500 |\n\nTotal: $500"
    good_curated = "### Curated Spend\n\n> ⚠️ Notice\n\n| Svc | Cost |\n|:---|:---|\n| EC2 | $500 |\n\nTotal: $500\n\n**💡 FinOps Insights:**\n- Optimize EC2"
    bad_dropped_table = "### Curated Spend\n\nHere is your spend: EC2 was $500."
    bad_refusal = "I cannot provide the requested spend data."
    bad_chart_json = '### Spend\n\n| Svc | Cost |\n|:---|:---|\n| EC2 | $500 |\n\n```chart\n{malformed json\n```'

    assert client._verify_curated_response(raw_table, good_curated, "show spend") is True
    assert client._verify_curated_response(raw_table, bad_dropped_table, "show spend") is False
    assert client._verify_curated_response(raw_table, bad_refusal, "show spend") is False
    assert client._verify_curated_response(raw_table, bad_chart_json, "show spend") is False

    # ── 4. Test _llm_curate_and_verify_data_response End-to-End with Mock LLM ──
    class MockCuratingLLMClient(AIClient):
        def __init__(self, curate_reply, should_fail=False):
            super().__init__("ollama", {}, [])
            self.curate_reply = curate_reply
            self.should_fail = should_fail
            self.call_count = 0

        def _call_active_llm(self, msgs, stats_out=None, on_token=None):
            self.call_count += 1
            if self.should_fail:
                return None, "Connection timeout"
            if on_token:
                on_token("curated_token")
            return self.curate_reply, None

    raw_2month_data = """### 📊 Top Services — Last 3 Months

| Service | Jul 2026 | Aug 2026 | Total |
|:---|:---|:---|:---|
| EC2 | $1,000 | $1,200 | $2,200 |
"""
    expected_curated = """> ⚠️ **Data Coverage Notice**: You requested data for 3 months, but the CloudHealth billing dataset currently contains finalized records for 2 months (July 2026 & August 2026). September 2026 is pending ingestion.

### 📊 Top Services — Last 3 Months

| Service | Jul 2026 | Aug 2026 | Total |
|:---|:---|:---|:---|
| EC2 | $1,000 | $1,200 | $2,200 |

**💡 FinOps Insights:**
- **EC2 Growth**: EC2 increased by $200 (20.0%) month-over-month.
"""
    # Success case: LLM curation strips any stray coverage notice while keeping table and insights
    curating_client = MockCuratingLLMClient(expected_curated)
    curated_result = curating_client._llm_curate_and_verify_data_response(
        user_query="give me the above data for last 3 months and provide MOM as well",
        raw_response=raw_2month_data
    )
    assert curating_client.call_count == 1
    assert "Data Coverage Notice" not in curated_result
    assert "| EC2 | $1,000 | $1,200 | $2,200 |" in curated_result
    assert "💡 FinOps Insights:" in curated_result

    # Robustness case: Even if LLM returns narrative text or concise insights, the table is preserved
    lossy_curated = "EC2 spend was $1,000 in July and $1,200 in August."
    lossy_client = MockCuratingLLMClient(lossy_curated)
    fallback_result = lossy_client._llm_curate_and_verify_data_response(
        user_query="give me last 3 months",
        raw_response=raw_2month_data
    )
    assert "| EC2 | $1,000 | $1,200 | $2,200 |" in fallback_result
    assert "Data Coverage Notice" not in fallback_result

    # Error case: LLM connection timeout -> table preserved, no coverage notice
    error_client = MockCuratingLLMClient("", should_fail=True)
    error_result = error_client._llm_curate_and_verify_data_response(
        user_query="give me last 3 months",
        raw_response=raw_2month_data
    )
    assert "| EC2 | $1,000 | $1,200 | $2,200 |" in error_result
    assert "Data Coverage Notice" not in error_result

    # Current month case: "this month" must NEVER produce a Data Coverage Notice
    this_month_data = """### 📊 Top Cost Anomalies — This Month (Oct 2026)

| Cloud | Account | Service | Impact |
|:---|:---|:---|:---|
| 🟠 AWS | Production | EC2 | +$1,200 |
"""
    month_client = MockCuratingLLMClient("- **EC2 Spike**: Investigating.")
    month_result = month_client._llm_curate_and_verify_data_response(
        user_query="Show top anomalies for this month",
        raw_response=this_month_data
    )
    assert "Data Coverage Notice" not in month_result
    assert "| 🟠 AWS | Production | EC2 | +$1,200 |" in month_result

    # ── 5. Test AIClient.generate integration with curation pass ──
    class MockMCP:
        def call_tool(self, name, args):
            csv_data = "month,provider,service,cost\n2026-07,AWS,EC2,1000\n2026-08,AWS,EC2,1200\n"
            return {"content": [{"text": json.dumps({"csv": csv_data})}]}

    # When engine == "ollama", generate() invokes curation pass
    client_gen = MockCuratingLLMClient(expected_curated)
    gen_result = client_gen.generate([
        {"role": "user", "content": "give me the above data for last 3 months and provide MOM as well"}
    ], mcp=MockMCP())

    assert "Data Coverage Notice" not in gen_result
    assert "EC2" in gen_result
    assert "💡 FinOps Insights:" in gen_result


def test_top_20_categories_before_other_services_in_chart():
    from cleo_agent import AIClient
    import json, re

    # 25 distinct services across 3 months
    csv_lines = ["month,provider,service,cost"]
    for idx in range(1, 26):
        svc_name = f"Service_{idx:02d}"
        for m in ["2026-07", "2026-08", "2026-09"]:
            cost = 1000.0 * (26 - idx)  # Service_01 is highest cost, Service_25 is lowest
            csv_lines.append(f"{m},Multi-Cloud,{svc_name},{cost}")
    csv_data = "\n".join(csv_lines) + "\n"

    class LargeServiceMockMCP:
        def call_tool(self, name, args):
            return {"content": [{"text": json.dumps({"csv": csv_data})}]}

    client = AIClient("direct", {}, [])
    res = client._generate_impl([
        {"role": "user", "content": "Top Multi-Cloud Services Spend Trend — Last 3 Months"}
    ], mcp=LargeServiceMockMCP())

    # Extract chart JSON
    chart_match = re.search(r'```chart\s*\n(.*?)\n```', res, flags=re.DOTALL)
    assert chart_match is not None, f"Expected chart in response:\n{res}"
    chart_spec = json.loads(chart_match.group(1))

    ds = chart_spec.get("datasets", [])
    labels = [d["label"] for d in ds]

    # At least 20 distinct services must be shown before "Other Services"
    assert len(labels) == 21, f"Expected 20 top services + 1 Other Services dataset, got {len(labels)}: {labels}"
    for idx in range(1, 21):
        assert f"Service_{idx:02d}" in labels, f"Missing Service_{idx:02d} in chart datasets: {labels}"

    assert "Other Services" in labels
    assert labels[-1] == "Other Services"
    # Services 21 through 25 should NOT be individual datasets
    for idx in range(21, 26):
        assert f"Service_{idx:02d}" not in labels


def test_top_cloud_services_mom_variance_tables():
    from cleo_agent import AIClient
    import json

    class MultiCloudMockMCP:
        def call_tool(self, name, args):
            sql = args.get("queryInput", {}).get("sqlStatement", "")
            time_range = args.get("queryInput", {}).get("timeRange", {})
            from_m = time_range.get("from", "")

            # Single SUM(EffectiveCost) query for overall totals
            if "SUM(EffectiveCost)" in sql and "GROUP BY" not in sql:
                if from_m == "2026-09":
                    return {"content": [{"text": json.dumps({"csv": "cost\n15000.0\n"})}]}
                return {"content": [{"text": json.dumps({"csv": "cost\n18500.0\n"})}]}

            if "last" in time_range:
                csv_multi = (
                    "month,provider,service,cost\n"
                    "2026-09,AWS,Amazon EC2,8000.0\n"
                    "2026-09,Azure,Virtual Machines,4000.0\n"
                    "2026-09,Google Cloud,Compute Engine,3000.0\n"
                    "2026-10,AWS,Amazon EC2,10000.0\n"
                    "2026-10,Azure,Virtual Machines,5000.0\n"
                    "2026-10,Google Cloud,Compute Engine,3500.0\n"
                )
                return {"content": [{"text": json.dumps({"csv": csv_multi})}]}

            # Previous month query
            if from_m == "2026-09":
                csv_prev = (
                    "provider,service,cost\n"
                    "AWS,Amazon EC2,8000.0\n"
                    "Azure,Virtual Machines,4000.0\n"
                    "Google Cloud,Compute Engine,3000.0\n"
                )
                return {"content": [{"text": json.dumps({"csv": csv_prev})}]}

            # Current month query
            csv_cur = (
                "month,provider,service,cost\n"
                "2026-10,AWS,Amazon EC2,10000.0\n"
                "2026-10,Azure,Virtual Machines,5000.0\n"
                "2026-10,Google Cloud,Compute Engine,3500.0\n"
            )
            return {"content": [{"text": json.dumps({"csv": csv_cur})}]}

    client = AIClient("direct", {}, [])

    # 1. Top cloud services across AWS, Azure, and GCP this month
    res1 = client._generate_impl([
        {"role": "user", "content": "Show our top cloud services across AWS, Azure, and GCP this month"}
    ], mcp=MultiCloudMockMCP())
    assert "Previous Month" in res1
    assert "MTD Cost" in res1
    assert "Month-End Forecast" in res1
    assert "MoM Variance ($)" in res1
    assert "MoM Variance (%)" in res1
    assert "Total Multi-Cloud Spend" in res1

    # 2. Welcome card prompt with explicit "with MoM variance"
    res2 = client._generate_impl([
        {"role": "user", "content": "Show our top cloud services across AWS, Azure, and GCP this month with MoM variance"}
    ], mcp=MultiCloudMockMCP())
    assert "Previous Month" in res2
    assert "MTD Cost" in res2
    assert "Month-End Forecast" in res2
    assert "MoM Variance ($)" in res2
    assert "MoM Variance (%)" in res2
    assert "Total Multi-Cloud Spend" in res2

    # 3. Section 3a: Service spend comparison across all clouds
    res3 = client._generate_impl([
        {"role": "user", "content": "Compare previous month service level cost with the current month projected cost across all clouds"}
    ], mcp=MultiCloudMockMCP())
    assert "Previous Month" in res3
    assert "MTD Cost" in res3
    assert "Month-End Forecast" in res3
    assert "MoM Variance ($)" in res3
    assert "MoM Variance (%)" in res3
    assert "$8,000.00" in res3

    # 4. Section 3a with limit starvation: first query returns only current month, triggering dedicated query
    class StarvedMockMCP:
        def __init__(self):
            self.dedicated_called = False

        def call_tool(self, name, args):
            time_range = args.get("queryInput", {}).get("timeRange", {})
            if time_range.get("from") == "2026-09":
                self.dedicated_called = True
                csv_prev = "provider,service,cost\nAWS,Amazon EC2,8500.0\nAzure,Virtual Machines,4200.0\n"
                return {"content": [{"text": json.dumps({"csv": csv_prev})}]}
            # When timeRange is {"last": 2, ...}, simulate all 200 rows being 2026-10
            csv_cur_only = "month,provider,service,cost\n2026-10,AWS,Amazon EC2,10000.0\n2026-10,Azure,Virtual Machines,5000.0\n"
            return {"content": [{"text": json.dumps({"csv": csv_cur_only})}]}

    starved_mcp = StarvedMockMCP()
    res4 = client._generate_impl([
        {"role": "user", "content": "Compare previous month service level cost with the current month projected cost across all clouds"}
    ], mcp=starved_mcp)
    assert starved_mcp.dedicated_called is True
    assert "$8,500.00" in res4
    assert "$4,200.00" in res4

    # 5. Channel Customers current-month query with MoM variance
    class CustMockMCP:
        def call_tool(self, name, args):
            if name in ("list_orgs", "list_channel_customers"):
                return {"content": [{"text": json.dumps([{"name": "Acme Corp", "customerId": "crn:123"}])}]}
            csv_data = "month,cost\n2026-09,10000.0\n2026-10,12000.0\n"
            return {"content": [{"text": json.dumps({"csv": csv_data})}]}

    res5 = client._generate_impl([
        {"role": "user", "content": "Find channel customers and show multi-cloud spend this month"}
    ], mcp=CustMockMCP())
    assert "Previous Month" in res5
    assert "MTD Spend" in res5
    assert "Month-End Forecast" in res5
    assert "MoM Variance ($)" in res5
    assert "MoM Variance (%)" in res5
    assert "Total Channel Customer Spend" in res5
    assert "$10,000.00" in res5

    # 6. Azure Spend chip prompt with MoM variance
    res6 = client._generate_impl([
        {"role": "user", "content": "Show our top Azure services this month with MoM variance"}
    ], mcp=MultiCloudMockMCP())
    assert "Previous Month" in res6
    assert "MTD Cost" in res6
    assert "Month-End Forecast" in res6
    assert "MoM Variance ($)" in res6
    assert "MoM Variance (%)" in res6

    # 7. GCP Spend chip prompt with MoM variance
    res7 = client._generate_impl([
        {"role": "user", "content": "Show our top GCP services this month with MoM variance"}
    ], mcp=MultiCloudMockMCP())
    assert "Previous Month" in res7
    assert "MTD Cost" in res7
    assert "Month-End Forecast" in res7
    assert "MoM Variance ($)" in res7
    assert "MoM Variance (%)" in res7


if __name__ == "__main__":
    test_mtd_forecast_generation()
    test_unconfigure_and_clear_token()
    test_multiturn_context_passing_and_disambiguation()
    test_parse_query_time_context()
    test_extract_requested_service()
    test_extract_requested_cloud()
    test_detect_anomaly_status_filter()
    test_extract_cost_threshold()
    test_continuation_with_cost_threshold_drilldown()
    test_csv_to_markdown()
    test_clean_chart_title()
    test_classify_service_usage_type()
    test_guid_validation()
    test_generate_chat_title()
    test_estimate_token_count()
    test_normalize_ollama_url()
    test_detect_system_info()
def test_waterfall_chart_reformat_and_mom_parsing():
    from cleo_charts import _detect_chart_type, _parse_table_data_from_assistant_markdown
    from cleo_agent import AIClient

    # 1. Detection of typos and variations
    assert _detect_chart_type("give me the waterfal chart for the about mom cost") == "waterfall"
    assert _detect_chart_type("give me the waterfal chart for the above mom cost") == "waterfall"
    assert _detect_chart_type("show as a water fall") == "waterfall"
    assert _detect_chart_type("variance chart please") == "waterfall"
    assert _detect_chart_type("bridge chart of spend") == "waterfall"

    # 2. Parsing MoM variance table
    table_text = (
        "### 📊 CloudHealth Multi-Cloud Spend Analysis: Top Services Across All Clouds — Month 2026-10\n\n"
        "| Cloud Provider | Service Category | Previous Month (2026-09) | MTD Cost (2026-10) | Month-End Forecast (2026-10) | MoM Variance ($) | MoM Variance (%) |\n"
        "|:---|:---|:---|:---|:---|:---|:---|\n"
        "| Google Cloud | Compute Engine | $2,737,347.94 | $724,243.36 | **$2,041,049.46** | -$696,298.48 | -25.4% 🔻 |\n"
        "| Azure | Virtual Machines | $1,461,714.38 | $424,561.85 | **$1,196,492.50** | -$265,221.88 | -18.1% 🔻 |\n"
        "| AWS | Compute Savings Plans | $22,140.25 | $348,785.66 | **$982,941.41** | +$960,801.16 | +4339.6% 🔺 |\n\n"
        "| **Total Multi-Cloud Spend** | | **$13,346,943.41** | **$3,627,790.17** | **$10,223,772.30** | **-$3,123,171.11** | **-23.4% 🔻** |\n"
    )
    parsed = _parse_table_data_from_assistant_markdown(table_text)
    assert parsed is not None
    assert parsed["is_mom_table"] is True
    assert parsed["prev_total"] == 13346943.41
    assert parsed["forecast_total"] == 10223772.30
    assert parsed["variance_total"] == -3123171.11
    assert len(parsed["mom_rows"]) == 3
    assert parsed["mom_rows"][0]["label"] == "Google Cloud Compute Engine"
    assert parsed["mom_rows"][0]["variance"] == -696298.48
    assert parsed["mom_rows"][2]["label"] == "AWS Compute Savings Plans"
    assert parsed["mom_rows"][2]["variance"] == 960801.16

    # 3. Agent Section 0d reformat execution from chat history
    class NoOpMCP:
        def call_tool(self, name, args):
            raise AssertionError(f"Should not call any MCP tool for pure reformat: {name}")

    client = AIClient("direct", {}, [])
    messages = [
        {"role": "user", "content": "Show our top cloud services across AWS, Azure, and GCP this month with MoM variance"},
        {"role": "assistant", "content": table_text},
        {"role": "user", "content": "give me the waterfal chart for the above MoM cost"}
    ]
    res = client._generate_impl(messages, mcp=NoOpMCP())
    assert "```chart" in res
    assert '"type": "waterfall"' in res
    assert "Previous Month (2026-09)" in res
    assert "Month-End Forecast (2026-10)" in res
    assert "AWS Compute Savings Plans" in res
    assert "Google Cloud Compute Engine" in res
    assert "Other Services Net Variance" in res


if __name__ == "__main__":
    test_chart_predicates()
    test_prune_messages_to_context_budget()
    test_multi_month_service_breakdown_parsing()
    test_deterministic_understand_date_vs_math()
    test_dimensional_breakdown_queries()
    test_multicloud_service_comparison_routing()
    test_multimonth_dimensional_breakdown_chart()
    test_market_data_intent_and_specs()
    test_ai_models_12month_dimensional_breakdown_routing()
    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as td:
        class DummyMonkey:
            def setattr(self, obj, attr, val): setattr(obj, attr, val)
        test_oauth_disconnected_handling(pathlib.Path(td), DummyMonkey())
    test_exclude_other_requested_and_chart_filtering()
    test_multi_service_and_ytd_understanding()
    test_azure_hybrid_benefit_understanding()
    test_azure_hybrid_benefit_handler_execution()
    test_build_llm_schema_context_validity()
    test_no_insights_requested_predicate_and_stripping()
    test_no_insights_needed_agent_execution()
    test_multimonth_service_mom_and_single_chart()
    test_cloud_support_charges_ytd_mom()
    test_emr_and_networking_mappings_and_intent()
    test_multicloud_service_major_inquiry_execution()
    test_llm_data_curation_pass_and_filtering()
    test_top_20_categories_before_other_services_in_chart()
    test_top_cloud_services_mom_variance_tables()
    test_waterfall_chart_reformat_and_mom_parsing()
    test_unique_instance_detection_and_consolidation()
    test_unique_instance_agent_handlers()
    print("All unit tests passed successfully!")


def test_unique_instance_detection_and_consolidation():
    from cleo_query import is_unique_instance_request, reconcile_and_consolidate_breakdown

    # 1. Detection of unique instance / resource count prompts
    q1 = "We should be able to calculate the number of unique instances per month or day here"
    assert is_unique_instance_request(q1) is True

    q2 = "give me the resource count of ec2 instances by region for last 6months"
    assert is_unique_instance_request(q2) is True

    q3 = "how many unique instances per day"
    assert is_unique_instance_request(q3) is True

    q4 = "calculate unique instances per month or day"
    assert is_unique_instance_request(q4) is True

    q5 = "show distinct instances of ec2"
    assert is_unique_instance_request(q5) is True

    # Spend queries should not trigger unique instance mode
    assert is_unique_instance_request("What did we spend on EC2 last month?") is False
    assert is_unique_instance_request("Show top AWS services by cost") is False

    # 2. Limit starvation consolidation logic
    # Scenario A: No starvation, rows sum to total
    rows_exact = [("AWS", "EC2", 80.0), ("AWS", "S3", 20.0)]
    rec_exact, other_c, notice = reconcile_and_consolidate_breakdown(rows_exact, 100.0, limit=10)
    assert len(rec_exact) == 2
    assert other_c == 0.0
    assert notice == ""

    # Scenario B: Display limit truncation (tail spend omitted)
    rows_trunc = [("AWS", f"Service_{i}", 10.0) for i in range(15)]
    true_total = 250.0  # Top 10 sum to 100.0, remaining 150.0 is tail spend
    rec_trunc, other_c, notice = reconcile_and_consolidate_breakdown(rows_trunc, true_total, limit=10, other_label="Other Services (Consolidated)")
    assert len(rec_trunc) == 11
    assert rec_trunc[-1][1] == "Other Services (Consolidated)"
    assert rec_trunc[-1][2] == 150.0
    assert other_c == 150.0
    assert "Consolidation & Query Limit Notice" in notice
    assert "$150.00" in notice
    # Verify 100% financial reconciliation
    assert sum(r[2] for r in rec_trunc) == true_total

    # Scenario C: Dict rows consolidation
    dict_rows = [{"service": f"Svc_{i}", "cost": 10.0} for i in range(12)]
    rec_dict, oth_d, notice_d = reconcile_and_consolidate_breakdown(dict_rows, 200.0, limit=10)
    assert len(rec_dict) == 11
    assert rec_dict[-1]["service"] == "Other (Consolidated)"
    assert rec_dict[-1]["cost"] == 100.0
    assert sum(r["cost"] for r in rec_dict) == 200.0


def test_unique_instance_agent_handlers():
    import json
    class MockUniqueMCP:
        def call_tool(self, name, args):
            if name != "execute_datasource_query":
                raise ValueError(f"Unexpected tool: {name}")
            sql = args["queryInput"]["sqlStatement"]
            if "GROUP BY timeInterval_Month" in sql:
                csv_data = "month,unique_instances,cost\n2026-08,3148460,308016.92\n2026-09,3284818,302298.53\n2026-10,1113382,259577.89\n"
            elif "GROUP BY TimeInterval_Day" in sql:
                csv_data = "day,unique_instances,cost\n2026-10-10,123379,9410.20\n2026-10-09,121239,9350.10\n"
            elif "GROUP BY product_region" in sql:
                csv_data = "region,unique_instances,cost\nus-east-1,16950210,1428460.10\nus-east-2,329812,17458.07\nus-west-2,49611,1452.12\n"
            else:
                csv_data = "col,cost\nval,100.0\n"
            return {"content": [{"text": json.dumps({"csv": csv_data})}]}

    from cleo_agent import AIClient
    client = AIClient("direct", {}, [])
    
    # 1. Test "We should be able to calculate the number of unique instances per month or day here"
    res1 = client._generate_impl(
        [{"role": "user", "content": "We should be able to calculate the number of unique instances per month or day here"}],
        mcp=MockUniqueMCP()
    )
    assert "CloudHealth Compute Fleet Analysis: EC2 Unique Instances per Month & Daily Concurrency" in res1
    assert "Peak Daily Active Fleet" in res1
    assert "123,379" in res1
    assert "Monthly Unique Active Instances Trend" in res1
    assert "3,284,818" in res1
    assert "Trailing Daily Active Fleet Concurrency" in res1
    assert "Regional Fleet Distribution" in res1
    assert "Ephemeral Churn vs Concurrent Capacity" in res1

    # 2. Test "give me the resource count of ec2 instances by region for last 6months"
    class MockRegionCountMCP:
        def call_tool(self, name, args):
            sql = args["queryInput"]["sqlStatement"]
            assert "COUNT(DISTINCT lineItem_ResourceId)" in sql
            csv_data = "region,instance_count,cost\nus-east-1,16950210,1428460.10\nus-east-2,329812,17458.07\nus-west-2,49611,1452.12\n"
            return {"content": [{"text": json.dumps({"csv": csv_data})}]}

    res2 = client._generate_impl(
        [{"role": "user", "content": "give me the resource count of ec2 instances by region for last 6months"}],
        mcp=MockRegionCountMCP()
    )
    assert "CloudHealth Resource Count Analysis" in res2
    assert "Unique Instances" in res2
    assert "% of Fleet" in res2
    assert "16,950,210" in res2
    assert "97.8%" in res2
    assert "$1,428,460.10" in res2
    assert "Total Analyzed Fleet" in res2
    assert "17,329,633" in res2


def test_multi_service_unique_resource_profiles_and_requests():
    from cleo_query import is_unique_instance_request, resolve_service_resource_profile

    # 1. Resource request detection across all cloud services
    assert is_unique_instance_request("calculate the number of unique instances per month or day here") is True
    assert is_unique_instance_request("how many unique databases per month") is True
    assert is_unique_instance_request("calculate unique s3 buckets per day") is True
    assert is_unique_instance_request("how many unique ebs volumes") is True
    assert is_unique_instance_request("how many unique lambda functions per month") is True
    assert is_unique_instance_request("dynamodb tables count by month") is True
    assert is_unique_instance_request("unique Azure VMs per month") is True
    assert is_unique_instance_request("how many unique GCP VM instances") is True

    # Negative checks (financial spend/cost queries shouldn't trigger unique resource fleet mode)
    assert is_unique_instance_request("what is the ec2 cost for last month") is False
    assert is_unique_instance_request("how much spend on s3") is False

    # 2. AWS Profile Resolution
    prof_rds = resolve_service_resource_profile("rds databases per month")
    assert prof_rds["pcode"] == "AmazonRDS"
    assert prof_rds["service_disp"] == "Amazon RDS"
    assert prof_rds["plural_noun"] == "Database Instances"
    assert "lineItem_ProductCode = 'AmazonRDS'" in prof_rds["cur_where"]

    prof_s3 = resolve_service_resource_profile("s3 buckets count")
    assert prof_s3["pcode"] == "AmazonS3"
    assert prof_s3["service_disp"] == "Amazon S3"
    assert prof_s3["plural_noun"] == "Buckets"

    prof_ebs = resolve_service_resource_profile("ebs volumes count")
    assert prof_ebs["pcode"] == "AmazonEC2_EBS"
    assert prof_ebs["plural_noun"] == "EBS Volumes"

    prof_lambda = resolve_service_resource_profile("lambda functions count")
    assert prof_lambda["pcode"] == "AWSLambda"
    assert prof_lambda["plural_noun"] == "Lambda Functions"

    prof_dynamo = resolve_service_resource_profile("dynamodb tables count")
    assert prof_dynamo["pcode"] == "AmazonDynamoDB"
    assert prof_dynamo["plural_noun"] == "DynamoDB Tables"

    # 3. Azure & GCP Profile Resolution
    prof_az_vm = resolve_service_resource_profile("unique azure vms per month")
    assert prof_az_vm["service_disp"] == "Azure Virtual Machines"
    assert prof_az_vm["plural_noun"] == "Virtual Machines"

    prof_gcp_vm = resolve_service_resource_profile("unique gcp vm instances per month")
    assert prof_gcp_vm["service_disp"] == "Google Compute Engine"
    assert prof_gcp_vm["plural_noun"] == "VM Instances"

    prof_az_blob = resolve_service_resource_profile("azure blob storage buckets")
    assert prof_az_blob["service_disp"] == "Azure Blob Storage"
    assert prof_az_blob["plural_noun"] == "Storage Containers"

    prof_gcp_storage = resolve_service_resource_profile("google cloud storage buckets")
    assert prof_gcp_storage["service_disp"] == "Google Cloud Storage"
    assert prof_gcp_storage["plural_noun"] == "Buckets"


def test_usagetype_starvation_reconciliation():
    from cleo_query import reconcile_and_consolidate_breakdown

    # Test dict rows with usage_type, operation, description, qty, cost
    ut_rows = [
        {"usage_type": f"USW2-BoxUsage:{i}", "operation": "RunInstances", "description": f"Instance {i}", "qty": 100.0, "cost": 50.0}
        for i in range(25)
    ]
    true_spend = 2000.0  # Top 15 sum to 750.0, true total is 2000.0, so remainder is 1250.0
    rec_ut, oth_cost, notice = reconcile_and_consolidate_breakdown(
        ut_rows, true_spend, limit=15, query_limit=25, other_label="Other Usage Types (Consolidated)"
    )
    assert len(rec_ut) == 16
    assert rec_ut[-1]["usage_type"] == "Other Usage Types (Consolidated)"
    assert rec_ut[-1]["cost"] == 1250.0
    assert rec_ut[-1]["operation"] == "-"
    assert rec_ut[-1]["qty"] == 0.0
    assert oth_cost == 1250.0
    assert "Consolidation & Query Limit Notice" in notice
    assert "$1,250.00" in notice
    assert "reached the query limit (25)" in notice
    assert sum(r["cost"] for r in rec_ut) == true_spend


def test_agent_multi_service_unique_fleet_and_usagetype():
    import json
    from cleo_agent import AIClient

    # 1. RDS Unique Database Instances
    class MockRDSUniqueMCP:
        def __init__(self):
            self.queries = []
        def call_tool(self, name, args):
            sql = args["queryInput"]["sqlStatement"]
            self.queries.append(sql)
            if "GROUP BY timeInterval_Month" in sql:
                csv_data = "month,unique_instances,cost\n2026-08,42,12000.00\n2026-09,45,12500.00\n2026-10,48,13000.00\n"
            elif "GROUP BY TimeInterval_Day" in sql:
                csv_data = "day,unique_instances,cost\n2026-10-10,48,430.00\n2026-10-09,47,425.00\n"
            elif "GROUP BY product_region" in sql:
                csv_data = "region,unique_instances,cost\nus-east-1,35,9500.00\nus-west-2,13,3500.00\n"
            else:
                csv_data = "col,cost\nval,100.0\n"
            return {"content": [{"text": json.dumps({"csv": csv_data})}]}

    client = AIClient("direct", {}, [])
    mcp_rds = MockRDSUniqueMCP()
    res_rds = client._generate_impl(
        [{"role": "user", "content": "how many unique RDS databases per month or day here"}],
        mcp=mcp_rds
    )
    assert any("lineItem_ProductCode = 'AmazonRDS'" in q for q in mcp_rds.queries)
    assert "CloudHealth Amazon RDS Fleet Analysis: Unique Database Instances per Month & Daily Concurrency" in res_rds
    assert "Database Instances" in res_rds
    assert "Peak Daily Active Fleet" in res_rds
    assert "48" in res_rds

    # 2. S3 Unique Buckets
    class MockS3UniqueMCP:
        def __init__(self):
            self.queries = []
        def call_tool(self, name, args):
            sql = args["queryInput"]["sqlStatement"]
            self.queries.append(sql)
            if "GROUP BY timeInterval_Month" in sql:
                csv_data = "month,unique_instances,cost\n2026-08,120,5000.00\n2026-09,122,5100.00\n2026-10,125,5200.00\n"
            elif "GROUP BY TimeInterval_Day" in sql:
                csv_data = "day,unique_instances,cost\n2026-10-10,125,170.00\n2026-10-09,124,168.00\n"
            elif "GROUP BY product_region" in sql:
                csv_data = "region,unique_instances,cost\nus-east-1,90,3800.00\nus-west-2,35,1400.00\n"
            else:
                csv_data = "col,cost\nval,100.0\n"
            return {"content": [{"text": json.dumps({"csv": csv_data})}]}

    mcp_s3 = MockS3UniqueMCP()
    res_s3 = client._generate_impl(
        [{"role": "user", "content": "calculate unique S3 buckets per month or day here"}],
        mcp=mcp_s3
    )
    assert any("lineItem_ProductCode = 'AmazonS3'" in q for q in mcp_s3.queries)
    assert "CloudHealth Amazon S3 Fleet Analysis: Unique Buckets per Month & Daily Concurrency" in res_s3
    assert "Buckets" in res_s3

    # 3. Usage Type breakdown with starvation and consolidation
    class MockUTMCP:
        def call_tool(self, name, args):
            sql = args["queryInput"]["sqlStatement"]
            if "SUM(lineItem_UnblendedCost) AS cost FROM AWS_CUR" in sql and "GROUP BY" not in sql:
                # True total cost query
                return {"content": [{"text": json.dumps({"csv": "cost\n15000.00\n"})}]}
            elif "lineItem_UsageType AS usage_type" in sql:
                # Granular usage items - return 100 items (query limit)
                lines = ["usage_type,operation,description,usage_qty,cost"]
                for i in range(100):
                    lines.append(f"USW2-BoxUsage:t3.large-{i},RunInstances,T3 Large Instance {i},720.0,100.0")
                return {"content": [{"text": json.dumps({"csv": "\n".join(lines)})}]}
            return {"content": [{"text": json.dumps({"csv": "cost\n0.0\n"})}]}

    mcp_ut = MockUTMCP()
    res_ut = client._generate_impl(
        [{"role": "user", "content": "what is RDS usage breakdown by usagetype"}],
        mcp=mcp_ut
    )
    assert "Granular UsageType Breakdown" in res_ut
    assert "Other Usage Types (Consolidated)" in res_ut
    assert "Consolidation & Query Limit Notice" in res_ut
    assert "$15,000.00" in res_ut
    assert "100.0%" in res_ut


def test_azure_vm_count_by_lease_type_and_instance_type():
    import json
    from cleo_agent import AIClient
    from cleo_query import is_unique_instance_request

    # 1. Verify is_unique_instance_request matches "vm count"
    prompt = "Give me the last month Azure VM count and break it down by Lease type and instance type"
    assert is_unique_instance_request(prompt) is True

    # 2. Mock MCP for Azure AZURE_COST_USAGE lease type and instance type query
    class MockAzureLeaseMCP:
        def __init__(self):
            self.queries = []
        def call_tool(self, name, args):
            sql = args["queryInput"]["sqlStatement"]
            self.queries.append(sql)
            assert "FROM AZURE_COST_USAGE" in sql
            assert "FROM AWS_CUR" not in sql
            assert "MeterSubCategory" in sql
            assert "MeterName" in sql
            assert "PricingModel" in sql
            assert "COUNT(DISTINCT ResourceId)" in sql

            csv_data = (
                "month,pricing_category,meter_subcategory,meter_name,vm_count,cost,usage_amount\n"
                "2026-09,OnDemand,Dsv5 Series,D16s v5,83,47833.82,60220.6\n"
                "2026-09,Reservation,Standard_M128s VM,Compute Hours,13,455691.44,52722.0\n"
                "2026-09,OnDemand,Standard_M128s VM,Compute Hours,13,455691.44,52722.0\n"
                "2026-09,Spot,DSv2 Series VM,DS4 v2 Spot,48324,4283.68,42836.7\n"
            )
            return {"content": [{"text": json.dumps({"csv": csv_data})}]}

    mcp = MockAzureLeaseMCP()
    client = AIClient("direct", {}, [])
    res = client._generate_impl([{"role": "user", "content": prompt}], mcp=mcp)

    assert "AZURE_COST_USAGE" in res
    assert "AWS_CUR" not in res
    assert "Azure Virtual Machines" in res
    assert "On-Demand (Pay-As-You-Go)" in res
    assert "Azure Reservations / Savings Plans" in res
    assert "Spot VMs" in res
    assert "D16s v5 (Dsv5 Series)" in res
    assert "Standard_M128s VM" in res
    assert "DS4 v2 Spot (DSv2 Series VM)" in res
    assert "Active VMs" in res
    assert "Azure Hybrid Benefit" in res
    assert "*Source: AZURE_COST_USAGE via CloudHealth FlexReports.*" in res


def test_azure_and_gcp_instance_type_not_hijacked_by_aws_ec2_or_rds():
    import json
    from cleo_agent import AIClient

    class MockMultiCloudMCP:
        def __init__(self):
            self.queries = []
        def call_tool(self, name, args):
            if name == "execute_datasource_query":
                sql = args["queryInput"]["sqlStatement"]
                self.queries.append(sql)
                csv_data = "month,provider,dimension,val\n2026-09,Cloud,Type_A,500.00\n"
                return {"content": [{"text": json.dumps({"csv": csv_data})}]}
            return {"content": [{"text": json.dumps({"csv": ""})}]}

    client = AIClient("direct", {}, [])

    # 1. Azure VM by instance type should query MULTICLOUD_FOCUS_COST_AND_USAGE or AZURE_FOCUS_COST_AND_USAGE, NEVER AWS_EC2_COST_AND_USAGE
    mcp_az = MockMultiCloudMCP()
    res_az = client._generate_impl([{"role": "user", "content": "Give me Azure VM spend by instance type"}], mcp=mcp_az)
    assert any("provider = 'Azure'" in q for q in mcp_az.queries)
    assert not any("AWS_EC2_COST_AND_USAGE" in q for q in mcp_az.queries)
    assert not any("FROM AWS_CUR" in q for q in mcp_az.queries)
    assert "EC2" not in res_az or "EC2" in "AWS_EC2_COST_AND_USAGE"

    # 2. GCP Compute Engine by instance type should NEVER query AWS_EC2_COST_AND_USAGE
    mcp_gcp = MockMultiCloudMCP()
    res_gcp = client._generate_impl([{"role": "user", "content": "Give me GCP Compute Engine spend by instance type"}], mcp=mcp_gcp)
    assert any("provider IN ('GCP', 'Google Cloud')" in q for q in mcp_gcp.queries)
    assert not any("AWS_EC2_COST_AND_USAGE" in q for q in mcp_gcp.queries)
    assert not any("FROM AWS_CUR" in q for q in mcp_gcp.queries)


def test_newchat_routes_and_ui():
    """Verify that cleo_server redirects / to /newChat and serves cleo_ui for /newChat, /newchat, /chat/newChat, and /chat/newchat."""
    import os
    from starlette.testclient import TestClient
    from cleo_server import app

    client = TestClient(app)

    # 1. Verify / and /chat redirect to /newChat
    for root_path in ["/", "/chat"]:
        resp_redirect = client.get(root_path, follow_redirects=False)
        assert resp_redirect.status_code == 307
        assert resp_redirect.headers.get("location") == "/newChat"

        resp_followed = client.get(root_path, follow_redirects=True)
        assert resp_followed.status_code == 200
        assert "<!DOCTYPE html>" in resp_followed.text or "<html" in resp_followed.text

    # 2. Verify direct /newChat endpoints
    for path in ["/newChat", "/newchat", "/chat/newChat", "/chat/newchat"]:
        resp = client.get(path)
        assert resp.status_code == 200, f"Expected 200 for {path}, got {resp.status_code}"
        assert "<!DOCTYPE html>" in resp.text or "<html" in resp.text

    # Verify cleo_ui.html contains /newChat path handling in updateURLForSession
    ui_path = os.path.join(os.path.dirname(__file__), "cleo_ui.html")
    with open(ui_path, "r", encoding="utf-8") as f:
        ui_content = f.read()

    assert "targetPath = '/newChat'" in ui_content
    assert "newchat" in ui_content


def test_home_page_landing_hero_and_navigation():
    """Verify that Cleo UI defines and renders the full Cleo FinOps Command Center landing page on home page navigation."""
    import os

    ui_path = os.path.join(os.path.dirname(__file__), "cleo_ui.html")
    with open(ui_path, "r", encoding="utf-8") as f:
        ui = f.read()

    # 1. Verify brand-link has goToLandingPage navigation
    assert 'onclick="goToLandingPage()"' in ui
    assert 'function goToLandingPage()' in ui

    # 2. Verify getWelcomeHeroHTML is defined with Command Center title, desc, and cards
    assert 'function getWelcomeHeroHTML()' in ui
    assert 'Cleo FinOps Command Center' in ui
    assert 'CloudHealth Autonomous Intelligence & Waste Reclamation' in ui
    assert 'Directly connected to live CloudHealth FlexReports' in ui

    # 3. Verify all 4 action cards exist
    assert 'Top Cloud Services (All Clouds)' in ui
    assert 'id="card-channel-customers"' in ui
    assert 'Channel Customers' in ui
    assert 'id="card-service-comparison"' in ui
    assert 'Lundbeck Service Comparison' in ui
    assert 'Waste Reclamation' in ui

    # 4. Verify newSession sets stream.innerHTML using getWelcomeHeroHTML()
    assert 'stream.innerHTML = getWelcomeHeroHTML();' in ui


def test_coverage_type_instance_count_routing():
    """Verify that 'give me the RDS instance count by coverage type' routes to coverage / lease breakdown with On-Demand, RI, and Savings Plans."""
    from cleo_query import _deterministic_understand_query
    from cleo_agent import AIClient

    # 1. Deterministic intent classification
    intent = _deterministic_understand_query([{"role": "user", "content": "give me the RDS instance count by coverage type"}])
    assert intent.get("service") == "AmazonRDS"
    assert "lease_type" in (intent.get("breakdowns") or [])
    assert intent.get("target_dimension") == "LeaseType"
    assert intent.get("metric_type") == "quantity"

    # 2. Mock MCP returning realistic AWS_CUR coverage rows
    mock_csv = (
        '"timeInterval_Month","pricing_term","lineItem_LineItemType","product_instanceType","vm_count","cost","usage_amount"\n'
        '"2026-10","OnDemand","Usage","db.m5.large","104","5245.48","1000.0"\n'
        '"2026-10","Reserved","DiscountedUsage","db.r6g.2xlarge","171","0.0","2000.0"\n'
        '"2026-10","Reserved","RIFee","","1","7558.81","0.0"\n'
        '"2026-10","OnDemand","SavingsPlanCoveredUsage","db.r7g.2xlarge","40","3011.61","500.0"\n'
    )

    class MockCoverageMCP:
        def __init__(self):
            self.queries = []

        def call_tool(self, name, args):
            sql = args.get("queryInput", {}).get("sqlStatement", "")
            self.queries.append(sql)
            import json
            return {"content": [{"text": json.dumps({"csv": mock_csv})}]}

    mcp = MockCoverageMCP()
    client = AIClient("direct", {}, [])
    resp = client._generate_impl([{"role": "user", "content": "give me the RDS instance count by coverage type"}], mcp=mcp)

    # 3. Verify query targeted AWS_CUR with pricing_term and lineItem_LineItemType
    assert any("FROM AWS_CUR" in q for q in mcp.queries)
    assert any("pricing_term" in q for q in mcp.queries)
    assert any("lineItem_LineItemType" in q for q in mcp.queries)
    assert any("AmazonRDS" in q for q in mcp.queries)

    # 4. Verify markdown response output contains coverage breakdown and counts
    assert "Coverage Type" in resp or "Lease Type" in resp
    assert "On-Demand" in resp
    assert "Reserved Instances (RI)" in resp
    assert "Savings Plans" in resp
    assert "Commitment Coverage" in resp
    assert "171" in resp
    assert "104" in resp
    assert "40" in resp


def test_channel_customers_card_visibility_direct_vs_partner():
    """Verify that Channel Customers card and chip are hidden from the first paint for direct customers, and visible for partner accounts."""
    import cleo_server
    from starlette.testclient import TestClient

    # 1. Direct customer scenario (_tools without list_channel_customers)
    original_tools = list(cleo_server._tools)
    try:
        cleo_server._tools = [
            {"name": "list_standard_datasources"},
            {"name": "list_orgs"},
            {"name": "execute_datasource_query"}
        ]
        client = TestClient(cleo_server.app)
        resp = client.get("/newChat")
        assert resp.status_code == 200
        html = resp.text
        # Must inject window.hasChannelCustomersTool = false
        assert "window.hasChannelCustomersTool = false;" in html
        # card-channel-customers and chip-channel-customers must be hidden from initial HTML
        assert 'id="card-channel-customers" style="display: none;"' in html
        assert 'id="chip-channel-customers" style="display: none;"' in html

        # 2. Partner account scenario (_tools with list_channel_customers)
        cleo_server._tools = [
            {"name": "list_standard_datasources"},
            {"name": "list_channel_customers"},
            {"name": "execute_datasource_query"}
        ]
        resp_partner = client.get("/newChat")
        assert resp_partner.status_code == 200
        html_partner = resp_partner.text
        # Must inject window.hasChannelCustomersTool = true
        assert "window.hasChannelCustomersTool = true;" in html_partner
        # card-channel-customers and chip-channel-customers must NOT have display: none
        assert 'id="card-channel-customers" style="display: none;"' not in html_partner
        assert 'id="chip-channel-customers" style="display: none;"' not in html_partner
    finally:
        cleo_server._tools = original_tools


