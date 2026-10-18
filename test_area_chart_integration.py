import json
from cleo_agent import AIClient

class MockMCP:
    def call_tool(self, name, args):
        if name == "execute_datasource_query":
            sql = args.get("queryInput", {}).get("sqlStatement", "")
            if "Month" in sql and ("ServiceName" in sql or "ServiceSubcategory" in sql) and "MULTICLOUD_FOCUS_COST_AND_USAGE" in sql:
                csv_data = (
                    "month,provider,service,cost\n"
                    "2026-06,Google Cloud,Compute Engine,2700.10\n"
                    "2026-06,Google Cloud,BigQuery,1600.25\n"
                    "2026-06,Google Cloud,Cloud Storage,550.40\n"
                    "2026-07,Google Cloud,Compute Engine,2800.50\n"
                    "2026-07,Google Cloud,BigQuery,1650.30\n"
                    "2026-07,Google Cloud,Cloud Storage,560.10\n"
                    "2026-08,Google Cloud,Compute Engine,2900.20\n"
                    "2026-08,Google Cloud,BigQuery,1700.10\n"
                    "2026-08,Google Cloud,Cloud Storage,570.80\n"
                )
                return {"content": [{"text": json.dumps({"csv": csv_data})}]}
            if "MULTICLOUD_FOCUS_COST_AND_USAGE" in sql and "GCP" in sql:
                csv_data = "provider,service,cost\nGCP,Compute Engine,8400.80\nGCP,BigQuery,4950.65\nGCP,Cloud Storage,1681.30\n"
                return {"content": [{"text": json.dumps({"csv": csv_data})}]}
            if "AWS_CUR" in sql or ("MULTICLOUD_FOCUS_COST_AND_USAGE" in sql and "AWS" in sql):
                csv_data = (
                    "month,cost\n"
                    "2026-09,847945.71\n"
                    "2026-08,2222223.27\n"
                    "2026-07,955159.19\n"
                    "2026-06,898578.79\n"
                    "2026-05,848455.34\n"
                    "2026-04,818335.24\n"
                    "2026-03,792935.24\n"
                    "2026-02,775200.00\n"
                    "2026-01,760300.00\n"
                    "2025-12,820100.00\n"
                    "2025-11,735400.00\n"
                    "2025-10,710250.00\n"
                )
                return {"content": [{"text": json.dumps({"csv": csv_data})}]}
            if "AZURE_FOCUS_COST_AND_USAGE" in sql:
                csv_data = (
                    "month,cost\n"
                    "2026-09,420100.50\n"
                    "2026-08,415200.20\n"
                    "2026-07,410300.10\n"
                    "2026-06,405400.00\n"
                    "2026-05,400500.00\n"
                    "2026-04,395600.00\n"
                    "2026-03,390700.00\n"
                    "2026-02,385800.00\n"
                    "2026-01,380900.00\n"
                    "2025-12,375000.00\n"
                    "2025-11,370000.00\n"
                    "2025-10,365000.00\n"
                )
                return {"content": [{"text": json.dumps({"csv": csv_data})}]}
            if "SUM(EffectiveCost)" in sql:
                return {"content": [{"text": json.dumps({"csv": "cost\n15032.75\n"})}]}
        return {"content": [{"text": "{}"}]}

def test_case_1_chart_transform_from_previous_message():
    agent = AIClient("direct", {}, [])
    prior_tbl = """### 📊 CloudHealth Spend Analysis: Top Google Cloud (GCP) Services by Spend — Last 3 Months

| Cloud Provider | Service Category | Cost | % of GCP Spend |
|:---|:---|:---|:---|
| GCP | Compute Engine | $5,400.20 | 55.7% |
| GCP | BigQuery | $3,200.50 | 33.0% |
| GCP | Cloud Storage | $1,100.80 | 11.3% |

| **Total GCP Spend** | | **$9,701.50** | **100.0%** |
"""

    history = [
        {"role": "user", "content": "give me gcp spend by service"},
        {"role": "assistant", "content": prior_tbl}
    ]

    msgs1 = history + [{"role": "user", "content": "give me the area chart and pie chart for the above data"}]
    res1 = agent.generate(msgs1, mcp=MockMCP())
    assert "```chart" in res1, "No chart block in res1"
    assert '"type": "area"' in res1, f"Missing area chart in res1:\n{res1}"
    assert '"type": "pie"' in res1, f"Missing pie chart in res1:\n{res1}"
    print("✓ Test Case 1 Passed: Both Area Chart and Pie Chart emitted from table in chat history!")


def test_case_2_new_prompt_area_chart_gcp_service_breakdown():
    agent = AIClient("direct", {}, [])
    msgs2 = [{"role": "user", "content": "give me the area chart for last 3 months data with service breakdown for GCP"}]
    res2 = agent.generate(msgs2, mcp=MockMCP())

    # Header check
    assert ("Top Google Cloud (GCP) Services by Spend" in res2 or "GCP Cost by Service" in res2), f"Wrong header in res2:\n{res2[:300]}"

    # Table check: multi-month columns
    assert "Jun 2026" in res2 and "Jul 2026" in res2 and "Aug 2026" in res2, f"Missing month columns in table:\n{res2}"
    assert "Compute Engine" in res2, f"Missing Compute Engine in table:\n{res2}"

    # Chart check:
    # 1. Type is area
    assert '"type": "area"' in res2, f"Chart is not area chart in res2:\n{res2}"
    # 2. X-axis has months (date range in x-axis)
    assert '"Jun 2026"' in res2 and '"Jul 2026"' in res2 and '"Aug 2026"' in res2, f"Chart labels do not have months:\n{res2}"
    # 3. Datasets has services as breakdown categories
    assert '"label": "Compute Engine"' in res2, f"Chart datasets missing Compute Engine label:\n{res2}"
    assert '"label": "BigQuery"' in res2, f"Chart datasets missing BigQuery label:\n{res2}"
    # 4. Y-axis is Cost ($)
    assert '"value_label": "Cost ($)"' in res2, f"Chart value_label is not Cost ($):\n{res2}"
    # 5. Stacked is True
    assert '"stacked": true' in res2, f"Chart stacked is not true:\n{res2}"

    print("✓ Test Case 2 Passed: GCP service breakdown has months in X-axis, services as breakdown categories, cost as Y-axis metric, and stacked area chart!")


def test_case_3_forward_looking_cost_forecast_aws():
    agent = AIClient("direct", {}, [])
    msgs3 = [{"role": "user", "content": "give me the forecast for AWS cost for the year 2027 and break it down monthly and use the last 12 months data for forecasting."}]
    res3 = agent.generate(msgs3, mcp=MockMCP())

    # 1. Header check
    assert "CloudHealth Cost Forecast: AWS Spend Projection — FY 2027" in res3, f"Wrong header in res3:\n{res3[:300]}"

    # 2. Context banner check
    assert "Trained on **12 months** of live billing telemetry" in res3, f"Missing context banner in res3:\n{res3[:500]}"

    # 3. Monthly Breakdown Table check: covers all 12 months of 2027
    for m_idx in range(1, 13):
        ym_str = f"2027-{m_idx:02d}"
        assert ym_str in res3, f"Missing forecast month {ym_str} in table:\n{res3}"
    assert "Total FY 2027 Projected Spend" in res3, f"Missing total FY 2027 in table:\n{res3}"
    assert "Confidence Interval" in res3, f"Missing Confidence Interval in table:\n{res3}"

    # 4. Chart check:
    assert "```chart" in res3, f"No chart in res3:\n{res3}"
    assert '"type": "area"' in res3, f"Chart is not area in res3:\n{res3}"
    assert "AWS Monthly Cost Forecast" in res3, f"Wrong chart title in res3:\n{res3}"
    assert '"Jan 2027"' in res3 and '"Dec 2027"' in res3, f"Missing forecast months in chart labels:\n{res3}"

    # 5. FinOps Insights & Outlier Dampening check:
    assert "Outlier Dampening (Aug 2026)" in res3, f"Missing outlier dampening note in res3:\n{res3}"
    assert "Commitment Keel Sizing (Optimize)" in res3, f"Missing keel sizing insight in res3:\n{res3}"
    assert "Operational Variance Governance (Operate)" in res3, f"Missing operate insight in res3:\n{res3}"

    print("✓ Test Case 3 Passed: Forward-looking FY 2027 AWS monthly forecast emitted table, area chart, and FinOps advisory!")


def test_case_4_multiturn_continuation_azure_forecast():
    agent = AIClient("direct", {}, [])
    msgs3 = [{"role": "user", "content": "give me the forecast for AWS cost for the year 2027 and break it down monthly and use the last 12 months data for forecasting."}]
    res3 = agent.generate(msgs3, mcp=MockMCP())

    msgs4 = msgs3 + [
        {"role": "assistant", "content": res3},
        {"role": "user", "content": "give me the simillar data for azure"}
    ]
    res4 = agent.generate(msgs4, mcp=MockMCP())

    # 1. Header check: correctly resolved to Azure FY 2027 Forecast
    assert "CloudHealth Cost Forecast: Azure Spend Projection — FY 2027" in res4, f"Wrong header in res4:\n{res4[:300]}"

    # 2. Dataset check: trained on AZURE_FOCUS_COST_AND_USAGE
    assert "AZURE_FOCUS_COST_AND_USAGE" in res4, f"Wrong dataset in res4:\n{res4[:500]}"

    # 3. Monthly breakdown: covers all 12 months of 2027
    for m_idx in range(1, 13):
        ym_str = f"2027-{m_idx:02d}"
        assert ym_str in res4, f"Missing forecast month {ym_str} in res4:\n{res4}"
    assert "Total FY 2027 Projected Spend" in res4, f"Missing total FY 2027 in res4:\n{res4}"

    # 4. Chart check:
    assert "```chart" in res4, f"No chart in res4:\n{res4}"
    assert '"type": "area"' in res4, f"Chart is not area in res4:\n{res4}"
    assert "Azure Monthly Cost Forecast" in res4, f"Wrong chart title in res4:\n{res4}"
    assert "FY 2027" in res4, f"Missing FY 2027 in res4:\n{res4}"
    assert '"Jan 2027"' in res4 and '"Dec 2027"' in res4, f"Missing forecast months in chart labels:\n{res4}"

    print("✓ Test Case 4 Passed: Contextual continuation correctly inherited FY 2027 monthly forecast for Azure from chat history!")


if __name__ == "__main__":
    test_case_1_chart_transform_from_previous_message()
    test_case_2_new_prompt_area_chart_gcp_service_breakdown()
    test_case_3_forward_looking_cost_forecast_aws()
    test_case_4_multiturn_continuation_azure_forecast()
    print("All area chart integration tests passed!")

