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


if __name__ == "__main__":
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
    print("All unit tests passed successfully!")



