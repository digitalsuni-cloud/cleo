"""
cleo_query.py — Multi-Cloud Service Catalogs, Query Understanding, and Intent Routing.
Zero external runtime dependencies: stdlib (re, datetime, json, csv, io) + cleo_charts.
"""
import re
import json
import csv
import io
import datetime
from typing import Optional, Dict, Any, List, Tuple

try:
    from cleo_logger import get_logger
    logger = get_logger("query")
except ImportError:
    import logging
    logger = logging.getLogger("cleo.query")
    logger.setLevel(logging.INFO)

from cleo_charts import is_no_chart_requested, is_no_mom_requested, is_exclude_other_requested

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
    "ebs": ("AmazonEC2_EBS", "Amazon EBS", "aws"),
    "amazonec2_ebs": ("AmazonEC2_EBS", "Amazon EBS", "aws"),
    "amazonebs": ("AmazonEC2_EBS", "Amazon EBS", "aws"),
    "elastic block store": ("AmazonEC2_EBS", "Amazon EBS", "aws"),
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
PCODE_TO_DISPLAY = SERVICE_DISPLAY_NAMES

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

def detect_anomaly_status_filter(query_text: str) -> Optional[str]:
    """
    Detects desired anomaly status filter from user query.
    By default for anomaly queries, returns 'ACTIVE' unless user asks for inactive ones
    (e.g. 'inactive', 'resolved', 'closed', 'archived'), or None if user asks for both / all statuses.
    """
    low = (query_text or "").lower()
    wants_all_status = any(p in low for p in [
        "all status", "all statuses", "both active and inactive", "active and inactive",
        "active or inactive", "regardless of status", "any status"
    ])
    if wants_all_status:
        return None
    wants_inactive = any(w in low for w in [
        "inactive", "resolved", "closed", "archived", "the inactive ones", "inactive ones", "past anomalies"
    ])
    if wants_inactive:
        return "INACTIVE"
    return "ACTIVE"

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
    single_date = None
    daily_range = None
    timeframe_days = None

    # 0. Single Day / Date: e.g. 'September 29th', 'Sep 29', '29th of September', '2026-09-29'
    m_iso_day = re.search(r'\b((?:19|20)\d{2})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b', low)
    if m_iso_day:
        yr, mo, dy = int(m_iso_day.group(1)), int(m_iso_day.group(2)), int(m_iso_day.group(3))
        single_date = f"{yr}-{mo:02d}-{dy:02d}"
        target_ym = f"{yr}-{mo:02d}"
        target_label = f"{FULL_NAMES[mo]} {dy}, {yr}"
        is_specific = True
        daily_range = {"from": single_date, "to": single_date}
        timeframe_days = 1

    if not single_date:
        m_day_1 = re.search(r'\b(' + '|'.join(MONTHS.keys()) + r')\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s*,?\s*((?:19|20)\d{2}))?\b', low)
        m_day_2 = re.search(r'\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(' + '|'.join(MONTHS.keys()) + r')(?:\s*,?\s*((?:19|20)\d{2}))?\b', low)
        m_day = m_day_1 or m_day_2
        if m_day:
            if m_day_1:
                m_str, d_str, y_str = m_day.group(1), m_day.group(2), m_day.group(3)
            else:
                d_str, m_str, y_str = m_day.group(1), m_day.group(2), m_day.group(3)
            m_num = MONTHS[m_str]
            day_num = int(d_str)
            yr = int(y_str) if y_str else (now.year if m_num <= now.month else now.year - 1)
            single_date = f"{yr}-{m_num:02d}-{day_num:02d}"
            target_ym = f"{yr}-{m_num:02d}"
            target_label = f"{FULL_NAMES[m_num]} {day_num}, {yr}"
            is_specific = True
            daily_range = {"from": single_date, "to": single_date}
            timeframe_days = 1

    # 1. Month Name + Year (e.g. 'July 2026', 'Jul 26', 'July 2025')
    if not target_ym:
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
    if daily_range is None:
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
    timeframe_months = None
    if m_count:
        try:
            req_cnt = int(m_count.group(1) or m_count.group(2))
            timeframe_months = req_cnt
            months_needed = min(12, max(months_needed, req_cnt))
            target_label = f"Last {req_cnt} Months"
            is_specific = False
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
        "timeframe_months": timeframe_months,
        "sort_desc": sort_desc,
        "is_specific": is_specific,
        "current_ym": current_ym,
        "last_ym": last_ym,
        "limit": limit,
        "timeframe_days": timeframe_days,
        "daily_range": daily_range,
        "single_date": single_date
    }

def _detect_contextual_continuation(messages: list[dict], cust_map: dict = None) -> dict:
    """
    Reads full conversational history to detect if the current request is a comparative,
    substitution, or continuation turn (e.g. 'give me the similar data for azure',
    'same for azure', 'what about gcp', 'do the same for ec2', 'now for customer Acme').
    Pulls analysis type (forecast, monthly trend, anomalies, etc.), timeframes,
    and dimensions from prior turns and substitutes the newly requested target entity.
    """
    if not messages or len(messages) < 2:
        return {"is_continuation": False}

    last_msg = messages[-1].get("content", "") if messages else ""
    low = last_msg.lower().strip()

    # 1. Check for continuation / comparison triggers
    has_similar_term = bool(re.search(
        r'\b(?:simillar|similar|same|equivalent|comparable|identical)\s+(?:data|numbers?|spend|cost|trend|forecast|projection|breakdown|results?|metrics?|analysis|view)\b'
        r'|\b(?:give\s+me|show\s+me|get\s+me|display|fetch|provide|what\s+is)?\s*(?:the\s+)?(?:simillar|similar|same)\s+(?:data|spend|cost|numbers?|forecast|projection|trend)?\s*(?:for|in|on|with|across|of)?\s*(?:aws|azure|gcp|google|ec2|rds|s3|lambda|dynamo|bedrock|customer|[a-z0-9_-]+)'
        r'|\b(?:same|simillar|similar)\s+(?:for|in|on|with|to)\b'
        r'|\b(?:do\s+the\s+same|show\s+the\s+same|can\s+you\s+do\s+the\s+same|repeat\s+(?:the\s+same|this|that))\b'
        r'|\b(?:what\s+about|how\s+about|and\s+for|now\s+for|what\s+is\s+the\s+same)\b',
        low
    )) or is_exclude_other_requested(low) or any(t in low for t in [
        "from the above", "above data", "above result", "the above data", "the above result", "previous data", "previous result"
    ])

    words = low.split()
    is_short_pivot = False
    if len(words) <= 6:
        has_cloud_or_svc = (
            any(c in low for c in ["azure", "aws", "gcp", "google cloud"]) or
            any(s in low for s in ["ec2", "rds", "s3", "lambda", "dynamo", "bedrock"])
        )
        has_status_pivot = any(p in low for p in ["inactive", "the inactive ones", "inactive ones", "active ones", "active"])
        if (has_cloud_or_svc or has_status_pivot) and (
            any(p in low for p in ["what about", "how about", "and for", "now for", "for ", "instead", "too", "also", "as well", "simillar", "similar", "same", "show "]) or
            len(words) <= 4
        ):
            is_short_pivot = True

    if not (has_similar_term or is_short_pivot):
        return {"is_continuation": False}

    prior_user_msgs = [m.get("content", "") for m in messages[:-1] if m.get("role") == "user"]
    prior_assistant_msgs = [m.get("content", "") for m in messages[:-1] if m.get("role") == "assistant"]

    if not prior_user_msgs and not prior_assistant_msgs:
        return {"is_continuation": False}

    last_user = prior_user_msgs[-1] if prior_user_msgs else ""
    last_user_low = last_user.lower()
    last_asst = prior_assistant_msgs[-1] if prior_assistant_msgs else ""
    last_asst_head = last_asst.split("\n")[0].lower() if last_asst else ""
    last_asst_body = last_asst[:2500].lower() if last_asst else ""

    # Detect newly requested entity in current prompt
    new_cloud = extract_requested_cloud(last_msg)
    new_svc, new_svc_disp = extract_requested_service(last_msg)
    new_customer = None
    if cust_map:
        for cname in cust_map.keys():
            if cname.lower() in low:
                new_customer = cname
                break

    # Prior Query Type Detection
    prior_type = None
    inherited_target_year = None
    inherited_period_title = None
    inherited_timeframe_months = None
    inherited_target_ym = None

    # Check for Forecast:
    is_prior_forecast = (
        "cost forecast:" in last_asst_head or
        "spend projection" in last_asst_head or
        "projected spend" in last_asst_body or
        "forecast period" in last_asst_body or
        "forecast generated via" in last_asst_body or
        ("forecast" in last_user_low and any(w in last_user_low for w in ["2027", "2028", "2029", "2030", "next year", "upcoming year", "future", "project"]))
    )

    if is_prior_forecast:
        prior_type = "forecast"
        m_yr = re.search(r'\b(202[7-9]|203\d)\b', last_asst_head + " " + last_asst_body + " " + last_user_low)
        if m_yr:
            inherited_target_year = int(m_yr.group(1))
            inherited_period_title = f"FY {inherited_target_year}"
        elif "next 6 months" in (last_asst_head + " " + last_user_low):
            inherited_period_title = "Next 6 Months"
        elif "next quarter" in (last_asst_head + " " + last_user_low):
            inherited_period_title = "Next Quarter"
        else:
            inherited_target_year = 2027
            inherited_period_title = f"FY {inherited_target_year}"

    # Check for Monthly Trend:
    elif (
        "monthly spend trend" in last_asst_head or
        "monthly breakdown" in last_asst_head or
        "month-over-month" in last_asst_head or
        "waterfall" in last_asst_head or
        ("mom progression" in last_asst_body and not is_prior_forecast) or
        any(w in last_user_low for w in ["monthly trend", "month-over-month", "monthly cost", "monthly spend", "waterfall"])
    ):
        prior_type = "monthly_trend"
        m_m = re.search(r'last\s*(\d{1,2})\s*months?', last_asst_head + " " + last_user_low)
        inherited_timeframe_months = int(m_m.group(1)) if m_m else 6

    # Check for Cost Anomalies:
    elif (
        "anomaly detection" in last_asst_head or
        "anomalies" in last_asst_head or
        "cost anomalies" in last_asst_body or
        any(w in last_user_low for w in ["anomal", "cost spike", "spend spike", "unusual spend"])
    ):
        prior_type = "anomalies"

    # Check for Region / Location Breakdown:
    elif (
        "spend by region" in last_asst_head or
        "spend by location" in last_asst_head or
        any(w in last_user_low for w in ["by region", "by location", "regions", "regional"])
    ):
        prior_type = "region_breakdown"

    # Check for Service Instance / Subcategory Breakdown:
    elif (
        "spend by instance type" in last_asst_head or
        "spend by engine" in last_asst_head or
        "spend by storage class" in last_asst_head or
        "spend by volume type" in last_asst_head or
        any(w in last_user_low for w in ["instance type", "engine type", "storage class", "volume type"])
    ):
        prior_type = "service_breakdown"

    # Check for AI Model / Foundation Model Breakdown:
    elif (
        "cost by ai model" in last_asst_head or
        "spend by ai model" in last_asst_head or
        "cost by model" in last_asst_head or
        "spend by model" in last_asst_head or
        "top ai model" in last_asst_body or
        any(w in last_user_low for w in ["ai usage", "ai model", "ai models", "foundation model", "foundation models", "llm", "llms", "model breakdown"])
    ):
        prior_type = "ai_model_breakdown"
        m_m = re.search(r'last\s*(\d{1,2})\s*months?', last_asst_head + " " + last_user_low)
        inherited_timeframe_months = int(m_m.group(1)) if m_m else 6

    # Check for Top Services:
    elif (
        "top aws services" in last_asst_head or
        "top azure services" in last_asst_head or
        "top gcp services" in last_asst_head or
        "top services by spend" in last_asst_head or
        "top services" in last_user_low
    ):
        prior_type = "top_services"

    # Check for Customer Spend:
    elif (
        "channel customers" in last_asst_head or
        "customer spend" in last_asst_head or
        "top channel customer" in last_asst_head or
        ("customer" in last_user_low and any(w in last_user_low for w in ["top", "spend", "cost", "breakdown"]))
    ):
        prior_type = "customer_spend"

    if not prior_type:
        return {"is_continuation": False}

    # Synthesize expanded query representation
    prov_disp = "Azure" if new_cloud == "azure" else ("GCP" if new_cloud == "gcp" else ("AWS" if new_cloud == "aws" else (new_svc_disp or "Cloud")))
    if prior_type == "forecast":
        yr_label = inherited_period_title or f"FY {inherited_target_year or 2027}"
        expanded_query = f"give me the forecast for {prov_disp} cost for {yr_label} and break it down monthly"
    elif prior_type == "monthly_trend":
        expanded_query = f"give me the monthly spend trend and breakdown for {prov_disp} for the last {inherited_timeframe_months or 6} months"
    elif prior_type == "anomalies":
        status_word = "inactive " if ("inactive" in low or "inactive" in last_user_low) else ""
        expanded_query = f"show top {status_word}cost anomalies detected for {prov_disp}"
    elif prior_type == "region_breakdown":
        expanded_query = f"show {prov_disp} spend breakdown by region"
    elif prior_type == "service_breakdown":
        expanded_query = f"show {prov_disp} spend breakdown"
    elif prior_type == "ai_model_breakdown":
        expanded_query = f"give me the multi-cloud spend by ai model for the last {inherited_timeframe_months or 6} months"
    elif prior_type == "top_services":
        expanded_query = f"show top services by spend for {prov_disp}"
    elif prior_type == "customer_spend":
        expanded_query = f"show spend breakdown for customer {new_customer or 'target'}"
    else:
        expanded_query = last_msg

    return {
        "is_continuation": True,
        "prior_query_type": prior_type,
        "inherited_target_year": inherited_target_year or 2027,
        "inherited_target_period_title": inherited_period_title or f"FY {inherited_target_year or 2027}",
        "inherited_timeframe_months": inherited_timeframe_months or 12,
        "inherited_target_ym": inherited_target_ym,
        "new_cloud": new_cloud,
        "new_service": new_svc,
        "new_service_disp": new_svc_disp,
        "new_customer": new_customer,
        "expanded_query": expanded_query
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

    # Detect contextual continuations (e.g. 'give me the similar data for azure')
    cont_ctx = _detect_contextual_continuation(messages, cust_map=cust_map)

    # Customer detection
    customer = cont_ctx.get("new_customer")
    if not customer and cust_map:
        for cname in cust_map.keys():
            if cname.lower() in low:
                customer = cname
                break

    # Cloud detection
    cloud = cont_ctx.get("new_cloud") or extract_requested_cloud(last_msg)

    # Service detection
    service = cont_ctx.get("new_service")
    if not service:
        svc_match = extract_requested_service(last_msg)
        if svc_match and svc_match[0]:
            service = svc_match[0]
            if not cloud:
                cloud = getattr(svc_match, "provider", None) or PCODE_TO_PROVIDER.get(service)

    if not service:
        if cloud == "azure":
            if any(w in low for w in ["vm", "vms", "virtual machine"]): service = "Virtual Machines"
            elif any(w in low for w in ["blob", "storage account", "storage"]): service = "Blob Storage"
            elif any(w in low for w in ["disk", "disks", "managed disk"]): service = "Managed Disks"
            elif any(w in low for w in ["sql", "database"]): service = "Azure SQL Database"
            else: service = None
        elif cloud == "gcp":
            if any(w in low for w in ["compute", "gce", "instance"]): service = "Compute Engine"
            elif any(w in low for w in ["storage", "gcs", "bucket"]): service = "Cloud Storage"
            elif any(w in low for w in ["disk", "persistent disk"]): service = "Persistent Disk"
            elif any(w in low for w in ["bigquery", "query"]): service = "BigQuery"
            elif any(w in low for w in ["sql", "database"]): service = "Cloud SQL"
            else: service = None
        else:
            if any(w in low for w in ["rds", "aurora", "relational database"]) or ("database" in low and not any(w in low for w in ["ec2", "s3", "dynamo"])):
                service = "AmazonRDS"
            elif any(w in low for w in ["ec2", "compute instance", "virtual machine"]) or ("instance type" in low and "rds" not in low and "database" not in low):
                service = "AmazonEC2"
            elif any(w in low for w in ["s3", "bucket", "object storage"]):
                service = "AmazonS3"
            elif any(w in low for w in ["ebs", "ebs volume", "block storage", "gp2", "gp3"]):
                service = "AmazonEC2_EBS"
            elif any(w in low for w in ["lambda", "serverless"]):
                service = "AWSLambda"
            elif any(w in low for w in ["dynamo", "dynamodb", "nosql"]):
                service = "AmazonDynamoDB"
            elif any(w in low for w in ["bedrock", "claude 3", "titan"]):
                service = "AmazonBedrock"
            elif any(w in low for w in ["cloudfront", "cdn"]):
                service = "AmazonCloudFront"
            elif any(w in low for w in ["vpc", "nat gateway"]):
                service = "AmazonVPC"
            elif not cloud and any(w in low for w in ["azure", "aks"]):
                service = None
                cloud = "azure"
            elif not cloud and any(w in low for w in ["gcp", "google cloud"]):
                service = None
                cloud = "gcp"

    # Timeframe detection
    m_months = re.search(r'\b(\d+)\s*(?:months?|m)\b', low)
    m_days = re.search(r'\b(\d+)\s*(?:days?|d)\b', low)
    has_explicit_months = bool(m_months) or any(w in low for w in ["12months", "12 months", "year", "annual", "months", "month by month", "monthly"])
    has_explicit_days = bool(m_days) or any(w in low for w in ["60days", "60 days", "30days", "30 days", "daily", "by day", "day by day", "per day"])

    timeframe_months = None
    timeframe_days = None
    if cont_ctx.get("is_continuation"):
        if cont_ctx.get("prior_query_type") == "forecast":
            timeframe_months = 12
            timeframe_days = None
        elif cont_ctx.get("prior_query_type") in ("monthly_trend", "ai_model_breakdown"):
            timeframe_months = cont_ctx.get("inherited_timeframe_months", 6)
            timeframe_days = None
        elif has_explicit_months:
            timeframe_months = int(m_months.group(1)) if m_months else 12
        elif has_explicit_days:
            timeframe_days = int(m_days.group(1)) if m_days else 30
    elif has_explicit_months:
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
    if any(w in low for w in ["region", "regions", "regional"]):
        breakdowns.append("region")
    if any(w in low for w in ["location", "locations", "geography", "geographic"]):
        breakdowns.append("location")
    if any(w in low for w in ["service", "product"]):
        breakdowns.append("service")
    if any(w in low for w in ["customer", "tenant", "client"]):
        breakdowns.append("customer")

    # Chart types
    chart_types = []
    if any(w in low for w in ["area chart", "area graph", "stacked area", "area"]):
        chart_types.append("area")
    if "pie" in low:
        chart_types.append("pie")
    if any(w in low for w in ["donut", "doughnut"]):
        chart_types.append("donut")
    if any(w in low for w in ["bar", "column", "stacked"]):
        chart_types.append("bar")
    if "line" in low:
        chart_types.append("line")
    if "waterfall" in low:
        chart_types.append("waterfall")

    # Strict check for pure reformat without new service/data
    has_fetch_verb = any(w in low for w in [
        "get me", "fetch", "query", "find me", "show me", "show our", "show us", "show", "list", "display",
        "usage for", "spend for", "break it down", "break down", "top services", "top service", "cloud services", "cloud service"
    ])
    has_explicit_data_subject = bool(service) or bool(customer) or bool(m_months) or bool(m_days)

    is_pure_reformat = (
        bool(re.search(r'\b(?:chart|plot|graph|visualize)\s+(?:of\s+)?(?:it|this|that|above|the\s+above|same\s+data|previous\s+data)\b', low) or
        re.search(r'\b(?:show|render|draw|display)\s+(?:it|this|that|above)\s+(?:as\s+a\s+|in\s+a\s+)?(?:chart|graph|plot|pie|donut|bar|area)\b', low) or
        any(low.strip() == p for p in [
            "pie chart", "donut chart", "doughnut chart", "bar chart", "waterfall chart", "area chart",
            "pie chart please", "as a pie chart", "give me the pie chart of it", "give me the pie chart of the above data",
            "area chart please", "as an area chart", "give me the area chart of it", "give me the area chart of the above data",
            "chart it", "plot it", "graph it", "show as pie", "show as donut", "show in pie", "show as a pie chart", "show as area"
        ])) and not (has_explicit_data_subject or has_fetch_verb)
    )

    is_history_qa = any(w in low for w in [
        "which customer", "what customer", "which service", "what service", "which month", "what month",
        "who was #1", "what was #1", "who spent the most", "total spend", "what was the total"
    ]) and not has_explicit_data_subject

    is_rec = any(w in low for w in [
        "recommendation", "recommendations", "optimize", "optimization", "saving", "savings",
        "reduce cost", "cost reduction", "rightsizing", "waste", "underutilized",
        "roi", "simulation", "simulate", "breakeven", "break-even", "payback",
        "efficiency calculation", "efficiency calculations", "payback period"
    ])
    is_anomaly = any(w in low for w in [
        "anomal", "spike", "unusual spend", "unexpected cost",
        "inactive ones", "the inactive ones", "active ones", "the active ones"
    ]) or (cont_ctx.get("is_continuation") and cont_ctx.get("prior_query_type") == "anomalies")

    is_user_query = bool(re.search(
        r'\b(?:list\s+(?:the\s+)?(?:of\s+)?users?|users?\s+list|all\s+users?|show\s+(?:me\s+)?(?:the\s+)?users?|get\s+(?:me\s+)?(?:the\s+)?users?|give\s+(?:me\s+)?(?:the\s+)?(?:list\s+(?:of\s+)?)?users?|who\s+are\s+the\s+users?|what\s+users?\b|users?\s+in\s+(?:this|the|our)\s+tenant|tenant\s+users?|user\s+accounts?|iam\s+users?|who\s+has\s+access|manage\s+users?|add\s+users?|invite\s+users?)\b',
        low
    )) and not any(w in low for w in ["tag", "tags", "tagged"])

    # General Cloud FinOps Advisory / Conceptual Question (no live data needed)
    is_general_finops = any(phrase in low for phrase in [
        "what is the difference", "difference between", "what is billedcost", "what is effectivecost",
        "what is focus", "what is finops", "explain finops", "finops framework", "finops phases",
        "inform optimize operate", "savings plan vs", "savings plans vs", "ri vs", "reserved instance vs",
        "how to optimize", "how do i optimize", "best practice", "playbook", "doctrine", "strategy",
        "unit economics", "tag governance", "waste pattern", "gp2 to gp3", "zombie nat", "egress cost",
        "break-even on", "breakeven on", "break-even utilization", "breakeven utilization",
        "how does azure reservation break-even", "what is the break-even between"
    ]) and not (customer or has_fetch_verb or any(w in low for w in ["our spend", "my spend", "our cost", "my cost", "show me our", "show me my", "simulate roi for our", "simulate roi for my"]))

    # Date parsing
    t_ctx = parse_query_time_context(last_msg)
    target_ym = t_ctx.get("target_ym") if t_ctx.get("is_specific") else None

    # Math calculation or general non-finops question
    low_no_dates = re.sub(r'\b\d{4}[-/]\d{1,2}(?:[-/]\d{1,2})?\b', '', low)
    has_finops_signal = any(w in low for w in [
        "cost", "spend", "spending", "billed", "effective", "cloud", "aws", "azure", "gcp",
        "service", "services", "account", "accounts", "breakdown", "anomaly", "anomalies",
        "recommendation", "usage", "tenant", "customer", "mcp", "forecast", "all clouds",
        "category", "subcategory", "pricing", "model", "resource", "region", "modality"
    ])
    is_math = not has_finops_signal and (
        bool(re.search(r'\d+\s*[\+\-\*\/x×÷\^%]\s*\d+', low_no_dates)) or
        bool(re.search(r'\b(?:calculate|calc|math)\b', low))
    )

    if is_user_query:
        intent = "unsupported_capability"
        is_new_data_fetch = False
    elif is_math:
        intent = "general_chat"
        is_new_data_fetch = False
    elif is_general_finops:
        intent = "general_finops_advisory"
        is_new_data_fetch = False
    elif is_pure_reformat and prior_assistant_msgs:
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
    elif service or customer or has_fetch_verb or any(w in low for w in ["spend", "cost", "usage", "billed", "hours", "breakdown", "service", "services"]) or cont_ctx.get("is_continuation"):
        intent = "fetch_data"
        is_new_data_fetch = True
    else:
        intent = "general_chat"
        is_new_data_fetch = False

    # Metric type detection: quantity vs cost
    quantity_triggers = [
        "number of", "how many", "count of", "quantity", "quantities",
        "instance count", "instances count", "vm count", "server count",
        "hours", "runtime", "instance hours", "vm hours", "compute hours",
        "vcpus", "vcpu", "cores",
        "storage used", "storage volume", "volume in gb", "volume in tb",
        "gb used", "gigabytes", "tb used", "terabytes",
        "invocations", "executions", "requests"
    ]
    is_quantity = any(t in low for t in quantity_triggers) or bool(re.search(r'\bcounts?\b', low))
    if "instances" in low and not any(w in low for w in ["cost", "spend", "spending", "billed", "dollar", "$", "price", "bill"]):
        is_quantity = True
    metric_type = "quantity" if is_quantity else "cost"

    # Best default dimension based on Multi-Cloud Matrix
    target_dimension = None
    if cont_ctx.get("is_continuation") and cont_ctx.get("prior_query_type") in ("forecast", "monthly_trend"):
        # Forecast and monthly trends do not default to subcategory breakdowns
        target_dimension = None
        breakdowns = []
    elif any(w in low for w in [
        "by account", "per account", "account name", "account names", "account breakdown",
        "breakdown by account", "each account", "by project", "per project",
        "by subscription", "per subscription", "sub account", "subaccount", "sub-account"
    ]):
        breakdowns.append("account")
        target_dimension = "SubaccountId"
    elif any(w in low for w in [
        "by billing account", "per billing account", "billing account id", "billing accounts",
        "billing account breakdown", "breakdown by billing account", "payer account", "master account"
    ]):
        breakdowns.append("billing_account")
        target_dimension = "BillingAccountId"
    elif any(w in low for w in [
        "by service category", "by service_category", "by category", "per category",
        "service categories", "category breakdown", "breakdown by category", "per service category"
    ]):
        breakdowns.append("service_category")
        target_dimension = "ServiceCategory"
    elif any(w in low for w in [
        "by pricing category", "by pricing model", "by pricing", "per pricing category",
        "pricing categories", "pricing category breakdown", "breakdown by pricing", "by pricing type"
    ]):
        breakdowns.append("pricing_category")
        target_dimension = "PricingCategory"
    elif any(w in low for w in ["by resource", "per resource", "resource breakdown", "breakdown by resource"]):
        breakdowns.append("resource")
        target_dimension = "ResourceId"
    elif any(w in low for w in ["by model provider", "per model provider", "model provider breakdown", "ai provider"]):
        breakdowns.append("model_provider")
        target_dimension = "ModelProvider"
    elif any(w in low for w in ["by model", "per model", "ai model", "model breakdown", "by llm", "llm breakdown"]) or cont_ctx.get("prior_query_type") == "ai_model_breakdown" or (is_exclude_other_requested(low) and any(w in low for w in ["model", "models", "ai"])):
        breakdowns.append("model")
        target_dimension = "Model"
    elif any(w in low for w in ["by modality", "per modality", "modality breakdown"]):
        breakdowns.append("modality")
        target_dimension = "Modality"
    elif any(w in low for w in ["by execution type", "execution type breakdown"]):
        breakdowns.append("execution_type")
        target_dimension = "ExecutionType"
    elif any(w in low for w in ["by token type", "token type breakdown"]):
        breakdowns.append("token_type")
        target_dimension = "TokenType"
    elif any(w in low for w in ["by hardware type", "by hardware", "hardware breakdown"]):
        breakdowns.append("hardware_type")
        target_dimension = "HardwareType"
    elif any(w in low for w in ["by hardware family", "hardware family breakdown"]):
        breakdowns.append("hardware_family")
        target_dimension = "HardwareFamily"
    elif any(w in low for w in ["by commitment plan", "by commitment", "savings plan breakdown", "by savings plan"]):
        breakdowns.append("commitment_plan")
        target_dimension = "Commitment_Plan"
    elif any(w in low for w in ["by country", "emissions by country", "carbon by country"]):
        breakdowns.append("country")
        target_dimension = "Country"
    elif any(w in low for w in ["storageclass", "storage class", "tier"]):
        breakdowns.append("storage_class")
        target_dimension = "product_storageClass"
    if any(w in low for w in ["volumetype", "volume type", "gp2", "gp3", "ebs type"]):
        breakdowns.append("volume_type")
        target_dimension = "product_volumeType"
    if any(w in low for w in ["subcategory", "sub-category", "service subcategory"]):
        breakdowns.append("service_subcategory")
        target_dimension = "ServiceSubcategory"

    if not target_dimension and not (cont_ctx.get("is_continuation") and cont_ctx.get("prior_query_type") in ("forecast", "monthly_trend")):
        if service == "AmazonEC2":
            target_dimension = "product_InstanceType"
            if "instance_type" not in breakdowns:
                breakdowns.append("instance_type")
        elif service == "AmazonRDS":
            target_dimension = "InstanceType"
            if "instance_type" not in breakdowns:
                breakdowns.append("instance_type")
        elif service == "AmazonS3":
            target_dimension = "product_storageClass"
            if "storage_class" not in breakdowns:
                breakdowns.append("storage_class")
        elif service == "AmazonEC2_EBS":
            target_dimension = "product_volumeType"
            if "volume_type" not in breakdowns:
                breakdowns.append("volume_type")
        elif service == "AWSLambda":
            target_dimension = "lineItem_Operation"
        elif cloud in ("azure", "gcp") or (service and service in ("Azure", "GCP")):
            target_dimension = "ServiceSubcategory"
            if "service_subcategory" not in breakdowns:
                breakdowns.append("service_subcategory")
        elif cloud == "all":
            target_dimension = "ServiceName"
            if "service" not in breakdowns:
                breakdowns.append("service")

    include_chart = not is_no_chart_requested(low)
    include_mom = not is_no_mom_requested(low)

    if cont_ctx.get("is_continuation") and cont_ctx.get("prior_query_type") == "forecast":
        target_ym = f"{cont_ctx['inherited_target_year']}-01"

    return {
        "intent": intent,
        "cloud": cloud,
        "service": service,
        "customer": customer,
        "metric_type": metric_type,
        "target_dimension": target_dimension,
        "target_ym": target_ym,
        "timeframe_months": timeframe_months,
        "timeframe_days": timeframe_days,
        "breakdowns": breakdowns,
        "chart_types": chart_types,
        "include_chart": include_chart,
        "include_mom": include_mom,
        "is_new_data_fetch": is_new_data_fetch,
        "anomaly_status": detect_anomaly_status_filter(last_msg) if is_anomaly else None,
        "corrected_query": cont_ctx.get("expanded_query") or last_msg
    }

