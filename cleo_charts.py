"""
cleo_charts.py — Visual chart generation, tabular formatting, and markdown sanitization.
Zero dependencies beyond the Python standard library.
"""
import re
import csv
import io
import json
import calendar
from typing import Optional


def is_no_chart_requested(low: str) -> bool:
    """Check if the user explicitly asked to omit or skip charts."""
    negative_chart_patterns = [
        "without chart", "without any chart", "no chart", "no charts", "skip chart",
        "without mom chart", "no mom chart", "without a chart", "table only", "only table",
        "just table", "just the table", "don't chart", "dont chart", "do not chart",
        "no graph", "without graph", "without a graph", "exclude chart"
    ]
    return any(p in low for p in negative_chart_patterns)


def is_no_mom_requested(low: str) -> bool:
    """Check if the user explicitly asked to omit Month-over-Month variance columns."""
    negative_mom_patterns = [
        "without mom", "no mom", "without month-over-month", "no month-over-month",
        "without mom chart", "no mom chart", "without variance", "no variance",
        "exclude mom", "skip mom", "without month over month", "no month over month"
    ]
    return any(p in low for p in negative_mom_patterns)


def is_exclude_other_requested(text: str) -> bool:
    """
    Check if the user explicitly asked to omit or exclude 'Other', 'Unallocated', etc.
    Handles quotes, punctuation, typos, and variations like 'exclude "other" model',
    'without other', 'remove other category', 'filter out other'.
    """
    if not text:
        return False
    low = text.lower().strip()
    pattern = r'\b(?:exclude|excluding|without|remove|filter\s+out|ignore|omit|drop|no|don\'t\s+include|dont\s+include|hide)\b.*?\b(?:[\'"]?other[\'"]?|unallocated|un-allocated)\b'
    if re.search(pattern, low):
        return True
    return any(p in low for p in [
        "exclude other", "exclude the other", "without other",
        "exclude unallocated", "without unallocated",
        "filter out other", "remove other", "ignore other",
        "no other", "omit other", "drop other", "hide other"
    ])


def is_other_category_name(name: str) -> bool:
    """
    Check if a category, instance type, or service dimension represents 'Other',
    'Unallocated', or an aggregated remainder bucket (e.g. 'RDS Other', 'Compute Other',
    '*Other (18 instance types)*', 'Other categories', etc.).
    """
    if not name:
        return False
    c = str(name).strip().lower().strip("*").strip()
    if c in ("other", "unallocated", "(unallocated / other)", "un-allocated", "others", "other (combined)", "other categories", "other services", "other models"):
        return True
    if c.startswith("other ") or c.endswith(" other") or "other (" in c or "(other" in c:
        return True
    return bool(re.search(r'\b(?:other|unallocated)\b', c))


def prune_mom_columns_from_markdown(text: str) -> str:
    """Prunes Month-over-Month (MoM) or Day-over-Day (DoD) variance columns from markdown tables."""
    lines = text.split("\n")
    new_lines = []
    in_table = False
    header_indices = None

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if not in_table:
                # Check if this is a header row containing MoM / DoD
                if any(c.lower().startswith("mom") or c.lower().startswith("dod") for c in cells):
                    in_table = True
                    header_indices = [idx for idx, c in enumerate(cells) if not c.lower().startswith("mom") and not c.lower().startswith("dod")]
                    kept = [cells[idx] for idx in header_indices]
                    new_lines.append("| " + " | ".join(kept) + " |")
                    continue
            elif in_table and header_indices is not None:
                if len(cells) >= len(header_indices):
                    kept = [cells[idx] for idx in header_indices if idx < len(cells)]
                    new_lines.append("| " + " | ".join(kept) + " |")
                    continue
        else:
            in_table = False
            header_indices = None
        new_lines.append(line)

    res = "\n".join(new_lines)
    res = res.replace("**Month-over-Month Variance by Cloud Provider:**", "**Monthly Spend by Cloud Provider:**")
    res = res.replace("Month-over-Month Variance", "Monthly Spend")
    return res


def _detect_chart_type(low: str) -> Optional[str]:
    """Return chart type string if user asked for a chart, else None."""
    if is_no_chart_requested(low):
        return None

    # Variance chart → waterfall (must check before generic "waterfall" keyword)
    if any(w in low for w in ["variance chart", "waterfall chart", "bridge chart"]):
        return "waterfall"
    if "waterfall" in low:
        return "waterfall"
    if any(w in low for w in ["doughnut chart", "donut chart", "doughnut graph", "donut graph",
        "doughnut only", "donut only", "doughnut", "donut"
    ]):
        return "doughnut"
    if any(w in low for w in ["area chart", "area graph", "stacked area", "area plot", "area only", "area"]):
        return "area"
    if any(w in low for w in ["pie chart", "pie graph", "pie breakdown", "pie only", "pie"]):
        return "pie"
    # Trend → line chart (bare "trend" keyword or multi-month progression triggers line, not bar)
    if any(w in low for w in [
        "trend", "line chart", "line graph", "trend line", "trend chart", "over time chart", "over time",
        "monthly cost breakdown", "monthly spend breakdown", "monthly breakdown", "monthly trend", "3-month", "3 month"
    ]):
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
    if is_no_mom_requested(low):
        return False
    return any(w in low for w in [
        "trend", "over time", "mom", "month over month", "month-over-month",
        "dod", "day over day", "day-over-day", "yoy", "year over year", "year-over-year",
        "variance", "change over", "how it changed", "how has it changed"
    ])


def _build_mom_variance_chart(
    title: str,
    time_labels: list,
    period_totals: dict,
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
    """
    if not title:
        return ""
    s = str(title).replace("`", "").replace("'", "").replace('"', "")
    
    def _unparenthesize_nested(m):
        prefix = m.group(1).strip()
        inner = m.group(2).strip()
        return f" — {prefix}: {inner}"
    s = re.sub(r'\s*\(\s*([^()]+?)\s*\(\s*([^()]+?)\s*\)\s*\)', _unparenthesize_nested, s)
    
    s = re.sub(r'\s*\(\s*Partner-Wide\s*\)', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\s*\(\s*Partner Tenant\s*\)', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\bPartner-Wide\b', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\bPartner Tenant\b', '', s, flags=re.IGNORECASE)
    
    s = re.sub(r'([—:])\s*\(([^)]+)\)', r'\1 \2', s)
    s = re.sub(r'\s*\(([^)]+)\)', r' — \1', s)
    
    s = s.replace("(", "").replace(")", "")
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
    if not chart_type or chart_type in ("none", "null", "false", False):
        return ""
    limit = max_labels if max_labels is not None else (366 if (datasets or len(labels) > 30) else 30)
    labels_clean = [str(l)[:80] for l in labels[:limit]]
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
            entry = {
                "label": ds.get("label", ""),
                "data": d
            }
            if "borderColor" in ds:
                entry["borderColor"] = ds["borderColor"]
            if "backgroundColor" in ds:
                entry["backgroundColor"] = ds["backgroundColor"]
            clean_ds.append(entry)
        spec["datasets"] = clean_ds
    elif values is not None:
        spec["values"] = [round(float(v), 2) for v in values[:len(labels_clean)]]

    return f"\n```chart\n{json.dumps(spec, indent=2)}\n```\n"


def _build_time_category_stacked_chart(
    title: str,
    raw_rows: list[dict],
    time_col: str = "month",
    cat_col: str = "category",
    cost_col: str = "cost",
    time_format: str = "month",
    max_cats: int = 12,
    unit: str = "Cost ($)",
    exclude_other: bool = False
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
    if exclude_other:
        sorted_cats = [
            (c, v) for c, v in sorted_cats
            if not is_other_category_name(c)
        ]
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

    if not exclude_other and other_cats:
        other_d = [round(sum(time_cat_matrix[t].get(oc, 0.0) for oc in other_cats), 2) for t in raw_times]
        if any(v > 0 for v in other_d):
            datasets.append({
                "label": "Other",
                "data": other_d
            })

    return _chart_block("bar", title, labels, datasets=datasets, value_label=unit, stacked=True)


def split_thinking_and_response(text: str) -> tuple[str, str]:
    """
    Separates thinking process / reasoning trace from the final user-facing response.
    Returns: (clean_response, thinking_process)
    """
    if not text:
        return "", ""
    text = text.strip()

    if "</think>" in text:
        parts = text.split("</think>", 1)
        thinking = parts[0].replace("<think>", "").strip()
        clean = parts[1].strip()
        clean = re.sub(r'^(?:---\s*\n+|\*{3,}\s*\n+)', '', clean).strip()
        return clean, thinking

    if text.startswith("<think>"):
        parts = text.split("<think>", 1)[1]
        salvage = re.search(
            r'\n(?:Final (?:Answer|Response|Draft):?|Here (?:are|is) the (?:final|revised|clean)|###|\*\*Summary)',
            parts, flags=re.IGNORECASE
        )
        if salvage:
            thinking = parts[:salvage.start()].strip()
            clean = parts[salvage.start():].strip()
            return clean, thinking
        return "", text

    return text, ""


def _sanitize_finops_bullet_titles(text: str) -> str:
    """
    Sanitizes LLM-generated FinOps insight bullet headers to ensure the title
    accurately reflects the content and services mentioned.
    """
    clean_text, _ = split_thinking_and_response(text)
    if clean_text:
        text = clean_text
    lines = text.split("\n")
    cleaned_lines = []
    for line in lines:
        m = re.match(r'^(\s*[-*•]\s*\*\*([^*]+)\*\*:?)(.*)$', line)
        if m:
            title, body = m.group(2).strip(), m.group(3)
            title_low = title.lower()
            body_low = body.lower()

            new_title = title
            if "ec2" in title_low and any(w in body_low for w in ["rds", "database", "aurora"]):
                new_title = "Compute & Database Concentration (EC2 & RDS)"
            elif "rds" in title_low and "ec2" in body_low:
                new_title = "Compute & Database Concentration (EC2 & RDS)"
            elif "aws" in title_low and any(w in body_low for w in ["azure", "gcp", "multi-cloud"]):
                new_title = "Multi-Cloud Infrastructure Distribution"
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
    """Converts CSV formatted text into formatted Markdown table with currency symbols."""
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
    """
    ut = (usage_type or "").lower()
    op = (operation or "").lower()
    pc = (pcode or "").lower()
    d = (desc or "").lower()

    if "s3" in pc or "amazons3" in pc or "bucket" in pc:
        if "general purpose" in ut or "timedstorage" in ut or "bytehrs" in ut:
            return "S3 Standard / General Purpose Storage"
        if "intelligent" in ut or "int" in ut:
            return "S3 Intelligent-Tiering"
        if "instant retrieval" in ut:
            return "S3 Archive Instant Retrieval"
        if "glacier" in ut or "deeparchive" in ut or "archive" in ut or "gir" in ut:
            return "S3 Glacier / Deep Archive"
        if "sia" in ut or "standard-ia" in ut or "standardia" in ut or "infrequent" in ut:
            return "S3 Standard-IA (Infrequent Access)"
        if "z-ia" in ut or "onezone" in ut:
            return "S3 One Zone-IA"
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
        if "tag" in ut:
            return "S3 Object Tagging & Metadata"
        if "analytics" in ut:
            return "S3 Storage Class Analysis"
        return f"S3 {usage_type}" if usage_type else "S3 Other Operations"

    if "ebs" in pc or ("ec2" in pc and any(k in ut for k in ["volume", "snapshot", "ebs", "iops", "general purpose", "provisioned"])):
        if "gp3" in ut:
            return "EBS gp3 General Purpose Volume"
        if "gp2" in ut or "general purpose" in ut:
            return "EBS gp2/gp3 General Purpose SSD"
        if "io1" in ut or "io2" in ut or "provisioned iops" in ut:
            return "EBS io1/io2 Provisioned IOPS Volume"
        if "st1" in ut or "throughput optimized" in ut:
            return "EBS Throughput Optimized HDD (st1)"
        if "sc1" in ut or "cold hdd" in ut:
            return "EBS Cold HDD (sc1)"
        if "magnetic" in ut:
            return "EBS Magnetic (Standard)"
        if "snapshot" in ut:
            return "EBS Snapshots"
        if "iops" in ut:
            return "EBS Provisioned IOPS"
        if "throughput" in ut:
            return "EBS Provisioned Throughput"
        return f"EBS {usage_type}" if usage_type else "EBS Storage & Volumes"

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
