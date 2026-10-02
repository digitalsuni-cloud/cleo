"""
test_pure_functions.py — Comprehensive unit tests for Cleo pure functions.
Does not require an active LLM or live MCP connection.
"""
import pytest
from cleo_agent import (
    parse_query_time_context,
    extract_requested_service,
    extract_requested_cloud,
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


if __name__ == "__main__":
    # Self-run check
    test_parse_query_time_context()
    test_extract_requested_service()
    test_extract_requested_cloud()
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
    print("All unit tests passed successfully!")



