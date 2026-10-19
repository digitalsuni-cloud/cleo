"""
test_optimization.py — Test Suite for Actionable FinOps Optimization & Waste Reduction Engine.
Verifies Pillar 1: From "Visibility" to "Actionable Optimization" (Highest ROI).
"""
import pytest
from cleo_optimization import (
    LEVER_SPECS,
    is_finops_optimization_request,
    classify_optimization_focus,
    calculate_actionable_savings,
    format_optimization_response,
)
from cleo_query import _deterministic_understand_query
from cleo_agent import AIClient


def test_optimization_request_detection():
    # Welcome card prompt & general waste scans
    assert is_finops_optimization_request("What are our top cost optimization recommendations across all cloud providers?")
    assert is_finops_optimization_request("What are our top cost optimization recommendations?")
    assert is_finops_optimization_request("Find cloud waste")
    assert is_finops_optimization_request("How can we reduce our AWS spend?")
    assert is_finops_optimization_request("Where are we wasting money?")
    assert is_finops_optimization_request("Show cost reduction opportunities")

    # High-ROI specific levers
    assert is_finops_optimization_request("gp2 to gp3 recommendations")
    assert is_finops_optimization_request("EBS gp2 vs gp3")
    assert is_finops_optimization_request("unattached EBS volumes and orphaned disks")
    assert is_finops_optimization_request("Find zombie NAT gateways")
    assert is_finops_optimization_request("Azure Hybrid Benefit savings")
    assert is_finops_optimization_request("Commitment cliff and expiring savings plans")
    assert is_finops_optimization_request("Weekend shutdown auto stop schedule for dev/test")
    assert is_finops_optimization_request("Incomplete multipart upload S3 lifecycle")

    # Negative checks: Conceptual, identity, or specific resource inventory queries
    assert not is_finops_optimization_request("What is the difference between EffectiveCost and BilledCost?")
    assert not is_finops_optimization_request("What is FOCUS?")
    assert not is_finops_optimization_request("Which Azure VMs are using Azure Hybrid Benefit?")
    assert not is_finops_optimization_request("What LLM are we using right now?")
    assert not is_finops_optimization_request("Show spend for AmazonEC2 last month")


def test_classify_optimization_focus():
    assert classify_optimization_focus("Upgrade EBS gp2 to gp3 volumes") == "gp2_to_gp3"
    assert classify_optimization_focus("Clean up orphaned disks and unattached volumes") == "orphaned_storage"
    assert classify_optimization_focus("Find idle NAT gateways and zombie NAT") == "zombie_nat"
    assert classify_optimization_focus("Analyze commitment coverage and expiring reservations") == "commitments"
    assert classify_optimization_focus("Calculate Azure Hybrid Benefit savings") == "ahb"
    assert classify_optimization_focus("Set up off-hours weekend auto-stop schedule") == "idle_compute"
    assert classify_optimization_focus("S3 multipart abort lifecycle rules") == "s3_lifecycle"
    assert classify_optimization_focus("What are our top cost optimization recommendations across all cloud providers?") == "all"


def test_calculate_actionable_savings_default():
    data = calculate_actionable_savings("all", "all")
    assert data["cloud"] == "Multi-Cloud"
    assert data["total_current_monthly"] > 0
    assert data["total_savings_monthly"] > 0
    assert data["total_savings_annual"] == round(data["total_savings_monthly"] * 12.0, 2)
    assert data["savings_percentage"] > 0
    assert data["lever_count"] == len(LEVER_SPECS)
    assert len(data["levers"]) == len(LEVER_SPECS)

    # Levers should be sorted by savings_monthly descending
    for i in range(len(data["levers"]) - 1):
        assert data["levers"][i]["savings_monthly"] >= data["levers"][i + 1]["savings_monthly"]


def test_calculate_actionable_savings_lever_filter():
    # Single lever focus
    data = calculate_actionable_savings("aws", "gp2_to_gp3")
    assert data["lever_count"] == 1
    assert data["levers"][0]["key"] == "gp2_to_gp3"
    assert data["levers"][0]["savings_pct"] == 20
    assert data["levers"][0]["risk"] == "Low / Zero Downtime"
    assert data["total_savings_monthly"] == data["levers"][0]["savings_monthly"]


def test_calculate_actionable_savings_custom_baseline():
    custom_baseline = {"aws": 100000.0, "azure": 50000.0}
    data = calculate_actionable_savings("all", "all", baseline_spend=custom_baseline)
    assert data["total_current_monthly"] >= 150000.0
    # Higher baseline spend yields higher savings than default
    default_data = calculate_actionable_savings("all", "all")
    assert data["total_savings_monthly"] > default_data["total_savings_monthly"]


def test_format_optimization_response_structure():
    opt_data = calculate_actionable_savings("all", "all")
    query = "What are our top cost optimization recommendations across all cloud providers?"
    resp = format_optimization_response(opt_data, cloud="all", focus="all", user_query=query)

    # 1. Executive Summary
    assert "### 🎯 Actionable FinOps Optimization & Waste Reduction: Multi-Cloud" in resp
    assert "Current Monthly Run-Rate" in resp
    assert "Optimized Target Run-Rate" in resp
    assert "Immediate Quick-Wins" in resp

    # 2. Prioritized Backlog Table
    assert "#### 📋 Prioritized Optimization Backlog" in resp
    assert "| Optimization Lever | Category | Confidence & Risk | Current Spend | Potential Monthly Savings | Annual Value |" in resp
    assert "EBS gp2 to gp3 Volume Modernization" in resp
    assert "Unattached Volumes & Disks Reclamation" in resp
    assert "Zombie NAT Gateways" in resp

    # 3. Interactive Chart.js
    assert "```chart" in resp
    assert "Monthly Savings by Lever" in resp
    assert '"value_label": "Monthly Savings ($)"' in resp

    # 4. Immediate Remediation CLI Guides
    assert "#### 🛠️ Immediate Execution & Remediation Guides" in resp
    assert "```bash" in resp
    assert "describe-volumes" in resp or "az disk list" in resp

    # Specific lever response includes its tailored CLI commands
    opt_gp2 = calculate_actionable_savings("aws", "gp2_to_gp3")
    resp_gp2 = format_optimization_response(opt_gp2, cloud="aws", focus="gp2_to_gp3")
    assert "aws ec2 modify-volume --volume-id" in resp_gp2

    # Respects no-chart query
    no_chart_resp = format_optimization_response(opt_data, cloud="all", focus="all", user_query="Show recommendations without chart")
    assert "```chart" not in no_chart_resp


def test_query_deterministic_understanding():
    # Welcome card
    res1 = _deterministic_understand_query([{"role": "user", "content": "What are our top cost optimization recommendations across all cloud providers?"}])
    assert res1["intent"] == "finops_recommendations"
    assert res1["optimization_focus"] == "all"
    assert res1["is_new_data_fetch"] is True

    # Lever specific
    res2 = _deterministic_understand_query([{"role": "user", "content": "gp2 to gp3 volume recommendations"}])
    assert res2["intent"] == "finops_recommendations"
    assert res2["optimization_focus"] == "gp2_to_gp3"

    res3 = _deterministic_understand_query([{"role": "user", "content": "find unattached disks and volumes"}])
    assert res3["intent"] == "finops_recommendations"
    assert res3["optimization_focus"] == "orphaned_storage"

    res4 = _deterministic_understand_query([{"role": "user", "content": "Azure Hybrid Benefit savings"}])
    assert res4["intent"] == "finops_recommendations"
    assert res4["optimization_focus"] == "ahb"


def test_agent_direct_mode_optimization_generation():
    client = AIClient("direct", {}, [])

    # Welcome prompt in Direct Mode (without MCP)
    resp = client.generate([{"role": "user", "content": "What are our top cost optimization recommendations across all cloud providers?"}], mcp=None)
    assert "### 🎯 Actionable FinOps Optimization" in resp
    assert "Prioritized Optimization Backlog" in resp
    assert "```chart" in resp
    assert "aws ec2" in resp

    # Lever prompt
    resp_gp2 = client.generate([{"role": "user", "content": "Show gp2 to gp3 modernization recommendations"}], mcp=None)
    assert "EBS gp2 to gp3 Volume Modernization" in resp_gp2
    assert "20%" in resp_gp2
    assert "aws ec2 modify-volume" in resp_gp2
