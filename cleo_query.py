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

from cleo_charts import is_no_chart_requested, is_no_mom_requested, is_exclude_other_requested, is_other_category_name, is_no_insights_requested

# ── Canonical Multi-Cloud & FOCUS 1.2 Column Synonyms & Metadata ────────
COLUMN_SYNONYMS: Dict[str, Dict[str, Any]] = {
    # Accounts & Hierarchy
    "SubaccountId": {
        "label": "Member / Usage Account ID",
        "synonyms": [
            "account", "subaccount", "sub-account", "member account", "linked account",
            "usage account", "project", "project id", "subscription", "subscription id",
            "cloud account", "account id", "account name", "linked accounts", "member accounts", "usage accounts"
        ],
        "description": "FOCUS member account identifier (AWS Linked Account ID, GCP Project ID, Azure Subscription ID)"
    },
    "BillingAccountId": {
        "label": "Payer / Billing Account ID",
        "synonyms": [
            "billing account", "payer account", "master account", "root account",
            "management account", "billing container", "parent account", "payer id",
            "billing account id", "master account id", "root account id", "payer accounts", "master accounts"
        ],
        "description": "FOCUS root/payer container identifier"
    },
    "bill_PayerAccountId": {
        "label": "Payer Account ID",
        "synonyms": ["payer account", "master account", "root account", "management account", "payer id"],
        "description": "AWS CUR root/payer account ID"
    },
    "lineItem_UsageAccountId": {
        "label": "Usage / Linked Account ID",
        "synonyms": ["usage account", "member account", "linked account", "subaccount", "account id"],
        "description": "AWS CUR linked/usage member account ID"
    },
    "SubscriptionId": {
        "label": "Subscription ID",
        "synonyms": ["subscription", "subscription id", "sub id", "azure subscription", "account id"],
        "description": "Azure subscription identifier"
    },
    "SubscriptionName": {
        "label": "Subscription Name",
        "synonyms": ["subscription name", "subscription", "account name"],
        "description": "Azure subscription display name"
    },

    # Service & Taxonomy
    "ServiceCategory": {
        "label": "Service Category (Macro)",
        "synonyms": [
            "service category", "macro service", "service family", "category",
            "domain", "service domain", "cloud domain", "category breakdown", "service categories"
        ],
        "description": "FOCUS 1.2 high-level service category (Compute, Storage, Database, Networking, AI & Machine Learning, Management & Governance)"
    },
    "ServiceSubcategory": {
        "label": "Service Subcategory (Granular)",
        "synonyms": [
            "service subcategory", "granular service", "sub service", "sub-service",
            "subcategory", "meter category", "meter subcategory", "service component",
            "component", "sub services", "granular services"
        ],
        "description": "FOCUS 1.2 granular service subcategory (Virtual Machines, Object Storage, Relational Database, NAT Gateway)"
    },
    "ServiceName": {
        "label": "Service Name",
        "synonyms": ["service", "service name", "cloud service", "product", "offering", "service title", "services"],
        "description": "Normalized cloud service name across providers"
    },
    "lineItem_ProductCode": {
        "label": "AWS Service / Product Code",
        "synonyms": ["service", "product code", "product", "aws service", "service name"],
        "description": "AWS service identifier (e.g. AmazonEC2, AmazonRDS, AmazonS3)"
    },
    "MeterCategory": {
        "label": "Azure Meter Category",
        "synonyms": ["meter category", "service category", "azure service", "service family"],
        "description": "Azure service classification"
    },
    "MeterSubCategory": {
        "label": "Azure Meter Subcategory",
        "synonyms": ["meter subcategory", "sub service", "azure subcategory", "service subcategory"],
        "description": "Azure granular service classification"
    },

    # Pricing, Lease & Commercial Models
    "PricingCategory": {
        "label": "Pricing Category / Model",
        "synonyms": [
            "pricing category", "pricing model", "commercial model", "contract type",
            "pricing type", "charge model", "pricing categories", "pricing breakdown"
        ],
        "description": "FOCUS commercial model (On-Demand, Committed, Dynamic, Spot)"
    },
    "pricing_term": {
        "label": "AWS Pricing Term",
        "synonyms": ["pricing term", "lease type", "purchase option", "lease model", "ondemand", "reservation"],
        "description": "AWS pricing commitment term (OnDemand, Reserved)"
    },
    "lineItem_LineItemType": {
        "label": "AWS Line Item Charge Type",
        "synonyms": [
            "line item type", "charge type", "fee type", "lease type",
            "savings plan", "spot", "discount", "tax", "credit", "refund"
        ],
        "description": "AWS CUR line item charge type (Usage, Fee, Credit, DiscountedUsage, SavingsPlanCoveredUsage, SpotUsage)"
    },
    "pricing_PurchaseOption": {
        "label": "AWS Purchase Option",
        "synonyms": ["purchase option", "capacity type", "commitment option", "all upfront", "no upfront", "partial upfront"],
        "description": "AWS commitment payment option"
    },

    # Service-Specific Technical Dimensions
    "product_instanceType": {
        "label": "EC2 Instance Type",
        "synonyms": [
            "instance type", "instance size", "vm size", "vm type",
            "machine type", "compute size", "flavor", "node type"
        ],
        "description": "AWS compute instance size (e.g. r6a.large, m6gd.4xlarge)"
    },
    "InstanceType": {
        "label": "RDS Instance Type",
        "synonyms": ["instance type", "rds instance type", "database instance type", "db size", "db instance"],
        "description": "AWS RDS database instance size (e.g. db.r6g.xlarge)"
    },
    "product_storageClass": {
        "label": "S3 Storage Class / Tier",
        "synonyms": [
            "storage class", "storage tier", "s3 tier", "s3 storage class",
            "lifecycle tier", "hot/cool/archive", "glacier", "storage tiering"
        ],
        "description": "AWS S3 storage tier (General Purpose/Standard, Intelligent-Tiering, Glacier Flexible/Deep Archive)"
    },
    "product_volumeType": {
        "label": "EBS Volume Type",
        "synonyms": [
            "volume type", "ebs type", "disk type", "volume tier",
            "gp2", "gp3", "io1", "io2", "provisioned iops"
        ],
        "description": "AWS EBS volume type (General Purpose gp2/gp3, Provisioned IOPS io1/io2, Cold HDD sc1)"
    },
    "lineItem_Operation": {
        "label": "API Operation / Action",
        "synonyms": ["operation", "api operation", "action", "event", "api call", "invocations", "runinstances"],
        "description": "Cloud service API action or operation"
    },

    # Geography & Assets
    "RegionId": {
        "label": "Region ID / Location",
        "synonyms": [
            "region", "location", "cloud region", "datacenter", "data center",
            "geography", "geo", "zone", "availability zone", "regional breakdown", "locations breakdown"
        ],
        "description": "Cloud geographic region code (e.g. us-east-1, eastus, europe-west1)"
    },
    "product_region": {
        "label": "AWS Region",
        "synonyms": ["region", "aws region", "datacenter", "location"],
        "description": "AWS geographic region code"
    },
    "ResourceId": {
        "label": "Resource Identifier",
        "synonyms": [
            "resource", "resource id", "resource name", "arn", "instance id",
            "asset", "individual resource", "top resources", "resource-level", "asset breakdown"
        ],
        "description": "Unique cloud resource identifier or ARN"
    },
    "ResourceName": {
        "label": "Resource Name",
        "synonyms": ["resource name", "resource", "vm name", "bucket name", "database name"],
        "description": "Display name of the cloud resource"
    },

    # Measures: Spend & Financials
    "EffectiveCost": {
        "label": "Effective Cost (Net / True Spend)",
        "synonyms": [
            "effective cost", "net cost", "true cost", "actual cost",
            "real cost", "amortized cost", "cost", "spend", "spending"
        ],
        "description": "FOCUS true net cost after amortizing upfront commitments and deducting negotiated discounts"
    },
    "BilledCost": {
        "label": "Billed Cost (Invoice Cost)",
        "synonyms": ["billed cost", "invoice cost", "unblended cost", "gross spend", "invoice amount", "list spend"],
        "description": "FOCUS undiscounted or invoiced cost charged on the periodic cloud bill"
    },
    "lineItem_UnblendedCost": {
        "label": "Unblended Cost",
        "synonyms": ["unblended cost", "cost", "spend", "spending", "dollar amount", "bill amount"],
        "description": "AWS CUR direct cash charge for usage"
    },
    "ActualCostInUsd": {
        "label": "Actual Cost in USD",
        "synonyms": ["actual cost", "cost in usd", "spend", "spending", "azure cost"],
        "description": "Azure direct accrued cost in USD"
    },
    "AmortizedCostInUsd": {
        "label": "Amortized Cost in USD",
        "synonyms": ["amortized cost", "effective cost", "true cost", "amortized spend"],
        "description": "Azure amortized spend accounting for reservations and savings plans"
    },

    # Measures: Usage & Quantity
    "PricingQuantity": {
        "label": "Pricing Quantity (Usage Units)",
        "synonyms": ["quantity", "pricing quantity", "usage quantity", "units", "hours", "gb-months", "invocations", "volume"],
        "description": "FOCUS consumable unit count (Compute Hours, GB-Mo, Requests, Invocations)"
    },
    "lineItem_UsageAmount": {
        "label": "AWS Usage Amount",
        "synonyms": ["usage amount", "usage quantity", "quantity", "gb", "hours", "units", "invocations"],
        "description": "AWS CUR consumable quantity measure"
    },
    "Quantity": {
        "label": "Azure Quantity",
        "synonyms": ["quantity", "units", "usage quantity", "hours", "gb"],
        "description": "Azure consumed resource units"
    },
    "Instances": {
        "label": "Instance Count",
        "synonyms": ["instances", "instance count", "number of instances", "vms", "database count", "node count"],
        "description": "Count of provisioned virtual compute or database instances"
    },
    "Instance_Hours": {
        "label": "Instance Hours",
        "synonyms": ["instance hours", "compute hours", "vm hours", "hours run"],
        "description": "Total compute execution hours"
    },

    # AI & Foundation Models
    "ModelProvider": {
        "label": "AI Model Provider",
        "synonyms": ["model provider", "ai provider", "ai vendor", "llm vendor", "ai company", "vendor"],
        "description": "AI model publisher (OpenAI, Anthropic, Google, AWS Bedrock, Meta, Mistral)"
    },
    "Model": {
        "label": "AI Model Name",
        "synonyms": [
            "model", "model name", "ai model", "foundation model",
            "llm", "language model", "foundation models", "llm models"
        ],
        "description": "AI model name (e.g. gpt-4o, claude-3-5-sonnet, gemini-1.5-pro, llama-3)"
    },
    "Modality": {
        "label": "AI Interaction Modality",
        "synonyms": ["modality", "media type", "input modality", "text vs multimodal", "vision", "audio", "embedding"],
        "description": "Model capability modality (text, multimodal, vision, speech, embedding)"
    },
    "ExecutionType": {
        "label": "AI Execution Type",
        "synonyms": ["execution type", "inference type", "batch vs streaming", "realtime vs batch", "processing mode"],
        "description": "Inference delivery method (realtime synchronous, batch, streaming, fine-tuning)"
    },
    "TokenType": {
        "label": "AI Token Type",
        "synonyms": [
            "token type", "prompt tokens", "completion tokens",
            "input tokens", "output tokens", "cached tokens", "tokens"
        ],
        "description": "Token metering category (Input/Prompt, Output/Completion, Cache Read/Write)"
    },
    "HardwareType": {
        "label": "AI Accelerator Type",
        "synonyms": ["hardware type", "accelerator type", "accelerator", "gpu vs tpu", "hardware"],
        "description": "Silicon compute accelerator (GPU, TPU, Trainium, Inferentia)"
    },
    "HardwareFamily": {
        "label": "AI Accelerator Family",
        "synonyms": ["hardware family", "gpu family", "accelerator family", "h100 vs a100", "b200"],
        "description": "Specific chip generation family (NVIDIA H100, A100, B200, Google TPU v5e)"
    },

    # Commitments, Carbon & Customers
    "Commitment_Plan": {
        "label": "Commitment Plan",
        "synonyms": [
            "commitment plan", "savings plan", "reservation", "ri",
            "reserved instance", "commitment type", "savings plans", "reserved instances"
        ],
        "description": "Multi-cloud rate commitment type (Compute Savings Plans, EC2 Instance Savings Plans, Standard RIs, Azure Reservations)"
    },
    "Country": {
        "label": "Emissions Geography / Country",
        "synonyms": ["country", "emissions country", "carbon country", "datacenter country", "geography"],
        "description": "Datacenter geographic country for carbon accounting"
    },
    "Carbon": {
        "label": "Carbon Footprint",
        "synonyms": ["carbon", "emissions", "mt co2e", "carbon footprint", "greenhouse gas", "ghg"],
        "description": "Metric tons of CO2 equivalent emissions"
    },
    "CustomerName": {
        "label": "Channel Customer / Tenant",
        "synonyms": ["customer", "customer name", "tenant", "client", "organization", "msp customer"],
        "description": "MSP channel customer or end-client organization"
    }
}

# ── Multi-Cloud Service Mapping & Extraction (AWS, Azure, GCP) ────────
class ServiceMatch(tuple):
    """Subclass of tuple supporting both 2-item legacy unpacking (pcode, disp) and .provider property."""
    def __new__(cls, pcode, disp, provider):
        return super(ServiceMatch, cls).__new__(cls, (pcode, disp))
    def __init__(self, pcode, disp, provider):
        self.pcode = pcode
        self.disp = disp
        self.provider = provider

    @property
    def display_name(self):
        return self.disp

CLOUD_SERVICES_MAP = {
    # ── AWS ──
    "ebs": ("AmazonEC2_EBS", "Amazon EBS", "aws"),
    "amazonec2_ebs": ("AmazonEC2_EBS", "Amazon EBS", "aws"),
    "amazonebs": ("AmazonEC2_EBS", "Amazon EBS", "aws"),
    "elastic block store": ("AmazonEC2_EBS", "Amazon EBS", "aws"),
    "rds": ("AmazonRDS", "RDS", "aws"),
    "amazonrds": ("AmazonRDS", "RDS", "aws"),
    "relational database service": ("AmazonRDS", "RDS", "aws"),
    "amazon relational database service": ("AmazonRDS", "RDS", "aws"),
    "ec2": ("AmazonEC2", "EC2", "aws"),
    "amazonec2": ("AmazonEC2", "EC2", "aws"),
    "elastic compute cloud": ("AmazonEC2", "EC2", "aws"),
    "amazon elastic compute cloud": ("AmazonEC2", "EC2", "aws"),
    "s3": ("AmazonS3", "S3", "aws"),
    "amazons3": ("AmazonS3", "S3", "aws"),
    "simple storage service": ("AmazonS3", "S3", "aws"),
    "amazon simple storage service": ("AmazonS3", "S3", "aws"),
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

def extract_all_requested_services(query_text: str) -> list[ServiceMatch]:
    """Extracts all cloud service identifiers, display labels, and providers mentioned in query text, respecting negations and resets."""
    q_low = (query_text or "").lower()

    # 1. Explicit resets to all services, overall spend, or complaints about sticking to a service
    all_svcs_patterns = [
        "all service", "all the service", "all the services", "all services",
        "all product", "all products", "every service", "every product",
        "across all services", "across services", "total spend across all", "overall spend across all",
        "overall breakdown", "general spend", "entire spend", "all of them",
        "all aws services", "all cloud services", "multi cloud", "multicloud",
        "stop showing"
    ]
    if any(p in q_low for p in all_svcs_patterns):
        return []

    found_matches = []
    seen_pcodes = set()

    # Check individual services, skipping negated references. Longest alias first so a more
    # specific match (e.g. "bigquery reservation api") wins over a shorter one it contains.
    for alias, (pcode, disp, prov) in sorted(CLOUD_SERVICES_MAP.items(), key=lambda kv: -len(kv[0])):
        for m in re.finditer(r'\b' + re.escape(alias) + r'\b', q_low):
            prefix = q_low[:m.start()].strip()
            is_negated = bool(re.search(
                r'\b(?:not\s+(?:just\s+)?(?:for\s+)?(?:the\s+)?|no\s+|except\s+|exclude\s+|excluding\s+|without\s+|remove\s+|drop\s+|skip\s+|omit\s+|filter\s+out\s+|other\s+than\s+|stuck\s+at\s+|stuck\s+on\s+|why\s+(?:you\'?re\s+)?(?:still\s+)?(?:stuck\s+at\s+)?)$',
                prefix
            ))
            if not is_negated and pcode not in seen_pcodes:
                seen_pcodes.add(pcode)
                found_matches.append((m.start(), ServiceMatch(pcode, disp, prov)))

    # Sort matches in the order they appear in user's prompt
    found_matches.sort(key=lambda x: x[0])
    return [match for _, match in found_matches]


def extract_requested_service(query_text: str):
    """Extracts cloud service identifier, display label, and provider from query text, respecting negations and resets."""
    svcs = extract_all_requested_services(query_text)
    return svcs[0] if svcs else (None, None)

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

def extract_cost_threshold(query_text: str) -> dict:
    """
    Extracts numeric thresholds / comparisons (e.g. 'more than $50', 'impact cost > 50',
    'over $100', 'between 50 and 200', 'under $20', 'cost impact at least $50') from text.
    Returns dict with keys: 'min': float|None, 'max': float|None, 'operator': str|None.
    """
    if not query_text:
        return {}
    low = query_text.lower()
    res = {}

    # 1. Between X and Y
    m_between = re.search(
        r'\bbetween\s+\$?([0-9]+(?:\.[0-9]+)?)\$?\s+(?:and|to|-)\s+\$?([0-9]+(?:\.[0-9]+)?)\$?\b',
        low
    )
    if m_between:
        res["min"] = float(m_between.group(1))
        res["max"] = float(m_between.group(2))
        res["operator"] = "between"
        return res

    # 2. Min / Greater than / More than / Over / Above / Exceeding / At least / Higher than
    m_min = re.search(
        r'(?:\b(?:more\s+than|greater\s+than|above|over|exceeding|higher\s+than|at\s+least|min(?:imum)?)\b|>=|>)\s*\$?([0-9]+(?:\.[0-9]+)?)\$?',
        low
    )
    if m_min:
        res["min"] = float(m_min.group(1))
        res["operator"] = ">=" if (">=" in m_min.group(0) or "at least" in m_min.group(0) or "min" in m_min.group(0)) else ">"

    # Also check suffix forms like "$50 or more", "50$ or higher", "$50+", "more than 50$"
    if "min" not in res:
        m_min_suff = re.search(r'\$?([0-9]+(?:\.[0-9]+)?)\$?\s*(?:\+|(?:or\s+more|or\s+higher|and\s+above)\b)', low)
        if m_min_suff:
            res["min"] = float(m_min_suff.group(1))
            res["operator"] = ">="

    # 3. Max / Less than / Under / Below / At most / Smaller than / Lower than
    m_max = re.search(
        r'(?:\b(?:less\s+than|under|below|smaller\s+than|at\s+most|max(?:imum)?|lower\s+than)\b|<=|<)\s*\$?([0-9]+(?:\.[0-9]+)?)\$?',
        low
    )
    if m_max:
        res["max"] = float(m_max.group(1))
        res["operator"] = "<=" if ("<=" in m_max.group(0) or "at most" in m_max.group(0) or "max" in m_max.group(0)) else "<"
    # 4. Percentage Thresholds: e.g. 'variance > 100%', 'spike over 50%', 'more than 200%', '50%+'
    m_pct_min = re.search(
        r'(?:\b(?:more\s+than|greater\s+than|above|over|exceeding|higher\s+than|at\s+least|min(?:imum)?)\b|>=|>)\s*([0-9]+(?:\.[0-9]+)?)\s*%',
        low
    )
    if m_pct_min:
        res["min_pct"] = float(m_pct_min.group(1))
    else:
        m_pct_suff = re.search(r'([0-9]+(?:\.[0-9]+)?)\s*%\s*(?:\+|(?:or\s+more|or\s+higher|and\s+above)\b)', low)
        if m_pct_suff:
            res["min_pct"] = float(m_pct_suff.group(1))

    m_pct_max = re.search(
        r'(?:\b(?:less\s+than|under|below|smaller\s+than|at\s+most|max(?:imum)?|lower\s+than)\b|<=|<)\s*([0-9]+(?:\.[0-9]+)?)\s*%',
        low
    )
    if m_pct_max:
        res["max_pct"] = float(m_pct_max.group(1))

    return res

def extract_negative_exclusions(query_text: str) -> dict:
    """
    Extracts named entities explicitly requested to be excluded, removed, or filtered out, e.g.:
    'exclude ec2', 'without pinpoint', 'remove NetApp', 'except aws', 'skip rds', 'filter out other'.
    Returns dict: {'services': list[str], 'clouds': list[str], 'raw_terms': list[str], 'has_exclusion': bool}
    """
    if not query_text:
        return {"services": [], "clouds": [], "raw_terms": [], "has_exclusion": False}
    low = query_text.lower()
    res = {"services": [], "clouds": [], "raw_terms": [], "has_exclusion": False}

    matches = re.findall(
        r'\b(?:exclude|excluding|without|except|remove|filter\s+out|drop|omit|skip)\s+([a-zA-Z0-9_\-\s]+?)(?:\s+from|\s+in|\s+and|\s*$|[,\.;])',
        low
    )
    for m in matches:
        candidate = m.strip().strip('"').strip("'")
        if not candidate or candidate in ("chart", "mom", "variance", "it", "this", "that"):
            continue
        res["has_exclusion"] = True
        res["raw_terms"].append(candidate)
        if candidate in ("aws", "amazon"):
            res["clouds"].append("aws")
        elif candidate in ("azure", "microsoft"):
            res["clouds"].append("azure")
        elif candidate in ("gcp", "google", "google cloud"):
            res["clouds"].append("gcp")
        else:
            matched_svc, _ = extract_requested_service(candidate)
            if matched_svc:
                res["services"].append(matched_svc)
            else:
                res["services"].append(candidate)

    return res

def resolve_ordinal_reference_from_history(messages: list[dict], query_text: str) -> Optional[dict]:
    """
    Resolves ordinal table references ('#1', 'item 1', 'row 1', 'the first one', 'top one',
    '#2', 'second one', 'item 3', etc.) against the most recent assistant markdown table.
    Returns parsed dict of that row's fields (e.g. {'service': '...', 'cloud': '...', 'region': '...', ...})
    or None if no match.
    """
    if isinstance(messages, str) and isinstance(query_text, list):
        messages, query_text = query_text, messages
    if not messages or not query_text:
        return None
    low = query_text.lower()

    target_idx = None
    m_num = re.search(r'(?:item|row|entry|anomaly|service|number|#)\s*#?\s*(\d{1,2})\b', low)
    if m_num:
        target_idx = int(m_num.group(1))
    elif any(w in low for w in ["the first one", "first one", "top one", "first anomaly", "number one", "#1"]):
        target_idx = 1
    elif any(w in low for w in ["the second one", "second one", "second anomaly", "number two", "#2"]):
        target_idx = 2
    elif any(w in low for w in ["the third one", "third one", "third anomaly", "number three", "#3"]):
        target_idx = 3
    elif any(w in low for w in ["the fourth one", "fourth one", "fourth anomaly", "#4"]):
        target_idx = 4
    elif any(w in low for w in ["the fifth one", "fifth one", "fifth anomaly", "#5"]):
        target_idx = 5

    if not target_idx or target_idx < 1:
        return None

    for m in reversed(messages):
        if m.get("role") != "assistant":
            continue
        content = m.get("content", "")
        if "|" not in content:
            continue

        lines = [line.strip() for line in content.split("\n") if line.strip().startswith("|") and line.strip().endswith("|")]
        if len(lines) < 3:
            continue

        header_line = lines[0]
        data_lines = [l for l in lines[1:] if not re.match(r'^\|[\s:\-]+(?:\|[\s:\-]+)+\|$', l)]
        if not data_lines:
            continue

        headers = [h.strip().lower() for h in header_line.split("|")[1:-1]]

        chosen_line = None
        for dl in data_lines:
            cells = [c.strip() for c in dl.split("|")[1:-1]]
            if not cells:
                continue
            first_cell = re.sub(r'[^0-9]', '', cells[0])
            if first_cell and int(first_cell) == target_idx:
                chosen_line = cells
                break

        if not chosen_line and target_idx <= len(data_lines):
            chosen_line = [c.strip() for c in data_lines[target_idx - 1].split("|")[1:-1]]

        if chosen_line:
            row_dict = {"ordinal_index": target_idx}
            for idx, h in enumerate(headers):
                if idx < len(chosen_line):
                    clean_val = chosen_line[idx].replace("**", "").replace("`", "").strip()
                    row_dict[h] = clean_val
                    if any(k in h for k in ["service", "asset"]):
                        row_dict["service"] = clean_val.split("(")[0].strip()
                    elif "cloud" in h:
                        row_dict["cloud"] = "aws" if "aws" in clean_val.lower() else ("azure" if "azure" in clean_val.lower() else ("gcp" if "gcp" in clean_val.lower() else clean_val))
                    elif any(k in h for k in ["region", "location"]):
                        row_dict["region"] = clean_val
                    elif any(k in h for k in ["account", "sub", "project"]):
                        row_dict["account"] = clean_val
                    elif any(k in h for k in ["cost impact", "impact"]):
                        m_val = re.search(r'[\$]?([0-9]+(?:\.[0-9]+)?)', clean_val)
                        if m_val: row_dict["cost_impact"] = float(m_val.group(1))
                    elif "variance" in h or "%" in h:
                        m_pct = re.search(r'([0-9]+(?:\.[0-9]+)?)\s*%', clean_val)
                        if m_pct: row_dict["variance_pct"] = float(m_pct.group(1))
            return row_dict

    return None

def extract_sort_refinement(query_text: str) -> Optional[dict]:
    """
    Extracts sorting preferences from conversational query, e.g.:
    'sort by variance', 'lowest first', 'sort ascending', 'highest cost first', 'longest duration'.
    Returns dict: {'field': 'variance'|'cost'|'duration'|'service'|'cloud'|'date', 'direction': 'asc'|'desc'}
    or None.
    """
    if not query_text:
        return None
    low = query_text.lower()

    is_asc = any(w in low for w in ["lowest first", "least first", "cheapest first", "smallest first", "shortest first", "ascending", "asc", "bottom first", "shortest"])
    is_desc = any(w in low for w in ["highest first", "most first", "largest first", "biggest first", "longest first", "descending", "desc", "top first", "longest"])

    m_sort = re.search(r'\b(?:sort|order)(?:\s+by)?\s+([a-z\s_%]+)', low)
    has_sort_keyword = bool(m_sort) or is_asc or is_desc or any(w in low for w in ["sort", "re-sort", "order by", "ranked by", "rank by", "longest", "shortest", "highest", "lowest"])

    if not has_sort_keyword:
        return None

    sort_text = m_sort.group(1).strip() if m_sort else low

    field = "cost"
    if any(w in sort_text for w in ["variance", "pct", "percent", "percentage", "%", "spike"]):
        field = "variance"
    elif any(w in sort_text for w in ["cost", "spend", "dollar", "impact", "amount", "value"]):
        field = "cost"
    elif any(w in sort_text for w in ["duration", "days", "length", "time"]):
        field = "duration"
    elif any(w in sort_text for w in ["service", "name", "asset"]):
        field = "service"
    elif any(w in sort_text for w in ["cloud", "provider"]):
        field = "cloud"
    elif any(w in sort_text for w in ["date", "day"]):
        field = "date"

    direction = "asc" if is_asc else ("desc" if is_desc else ("asc" if any(w in sort_text for w in ["asc", "ascending"]) else "desc"))
    return {"field": field, "direction": direction}

def extract_limit_adjustment(query_text: str) -> Optional[int]:
    """
    Extracts explicit limit adjustments from conversational turns, e.g.:
    'show top 10 instead', 'expand to 20', 'make it 15', 'top 20', 'show 5 instead',
    'increase limit to 25', 'give me 20'.
    Returns int limit, or None.
    """
    if not query_text:
        return None
    low = query_text.lower()

    m = re.search(
        r'\b(?:(?:show|display|expand|increase|make\s+it|give\s+me)\s+(?:to\s+)?(?:top\s+)?(\d{1,3})|top\s+(\d{1,3})\s+(?:instead|results?|rows?|entries|items)?|(\d{1,3})\s+instead|limit\s+(?:to\s+)?(\d{1,3}))\b',
        low
    )
    if m:
        for g in m.groups():
            if g and g.isdigit():
                val = int(g)
                if 1 <= val <= 100:
                    return val
    return None

def parse_query_time_context(query: str, context_ym: str = None) -> dict:
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

    is_ytd = bool(re.search(r'\b(?:ytd|year\s*to\s*date|this\s*year)\b', low))
    is_qtd = bool(re.search(r'\b(?:qtd|quarter\s*to\s*date|this\s*quarter)\b', low))
    is_mtd = bool(re.search(r'\b(?:mtd|month\s*to\s*date|this\s*month|current\s*month)\b', low))

    if not target_ym:
        if is_ytd:
            target_ym = current_ym
            target_label = f"Year-To-Date (YTD {now.year})"
            is_specific = True
        elif is_qtd:
            target_ym = current_ym
            q_num = ((now.month - 1) // 3) + 1
            target_label = f"Q{q_num} QTD ({now.year})"
            is_specific = True
        elif is_mtd:
            target_ym = current_ym
            target_label = f"MTD ({FULL_NAMES[now.month]} {now.year})"
            is_specific = True
        elif any(w in low for w in [
            "last month", "previous month", "prior month", "the month before",
            "the month before that", "month prior"
        ]):
            if context_ym and re.match(r'^\d{4}-(?:0[1-9]|1[0-2])$', context_ym):
                c_yr, c_mo = int(context_ym.split("-")[0]), int(context_ym.split("-")[1])
                prev_m = (c_mo - 1) if c_mo > 1 else 12
                prev_yr = c_yr if c_mo > 1 else (c_yr - 1)
                target_ym = f"{prev_yr}-{prev_m:02d}"
                target_label = f"Previous Month ({FULL_NAMES[prev_m]} {prev_yr})"
            else:
                target_ym = last_ym
                prev_m = (now.month - 1) if now.month > 1 else 12
                prev_yr = now.year if now.month > 1 else (now.year - 1)
                target_label = f"Last Month ({FULL_NAMES[prev_m]} {prev_yr})"
            is_specific = True
        elif any(w in low for w in ["that month", "same month"]) and context_ym and re.match(r'^\d{4}-(?:0[1-9]|1[0-2])$', context_ym):
            target_ym = context_ym
            c_yr, c_mo = int(context_ym.split("-")[0]), int(context_ym.split("-")[1])
            target_label = f"{FULL_NAMES[c_mo]} {c_yr}"
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
    months_needed = now.month if is_ytd else (((now.month - 1) % 3) + 1 if is_qtd else min(12, max(2, diff + 2)))

    # Check for requested duration count e.g. "last 6 months", "past 3 months"
    m_count = re.search(r'(?:last|past|previous|for)\s+(\d{1,2})\s*months?\b|\b([1-9]|[1-4]\d)\s+months\b', low)
    timeframe_months = now.month if is_ytd else (((now.month - 1) % 3) + 1 if is_qtd else None)
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
    if not messages:
        return {"is_continuation": False}

    if len(messages) < 2:
        # Cross-session fallback: check if user asks to repeat or continue prior session query
        last_m = messages[-1].get("content", "").lower() if messages else ""
        has_cross_continuation_intent = any(w in last_m for w in [
            "previous query", "last query", "prior query", "last time", "from last session", "previous session",
            "same for", "similar for", "simillar for", "repeat that", "repeat this", "repeat the query", "run it again"
        ])
        if not has_cross_continuation_intent:
            return {"is_continuation": False}

        try:
            from cleo_memory import load_last_query
            saved = load_last_query()
            if saved and saved.get("sql"):
                q_type = str(saved.get("query_type", "")).lower()
                if "forecast" in q_type or "forecast" in saved["sql"].lower():
                    asst_title = "### 📊 CloudHealth Cost Forecast: Spend Projection"
                elif "monthly" in q_type or "trend" in q_type:
                    asst_title = "### 📊 Monthly Spend Trend & Breakdown"
                elif "anomal" in q_type:
                    asst_title = "### 🛡️ CloudHealth Cost Anomaly Detection"
                else:
                    asst_title = f"### 📊 CloudHealth Spend Analysis\nData for {saved.get('dataset', 'CloudHealth')}"

                synthetic_msgs = [
                    {"role": "user", "content": saved["sql"]},
                    {"role": "assistant", "content": asst_title},
                    messages[-1]
                ]
                return _detect_contextual_continuation(synthetic_msgs, cust_map=cust_map)
        except Exception:
            pass
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
    new_svc_cand, new_svc_disp_cand = extract_requested_service(last_msg)
    has_region_code = bool(re.search(r'\b(?:us|eu|ap|sa|ca|me|af)-[a-z]+-\d+\b|\b(?:eastus|westus|centralus|northeurope|westeurope|eastasia|southeastasia)\b', low))
    if len(words) <= 7:
        has_cloud_or_svc = (
            any(c in low for c in ["azure", "aws", "gcp", "google cloud", "google", "amazon", "microsoft"]) or
            bool(new_svc_cand) or
            has_region_code
        )
        has_status_pivot = any(p in low for p in ["inactive", "the inactive ones", "inactive ones", "active ones", "active"])
        if (has_cloud_or_svc or has_status_pivot) and (
            any(p in low for p in ["what about", "how about", "and for", "now for", "for ", "instead", "too", "also", "as well", "simillar", "similar", "same", "show "]) or
            len(words) <= 5
        ):
            is_short_pivot = True

    thresh = extract_cost_threshold(low)
    has_threshold_refinement = bool(thresh.get("min") is not None or thresh.get("max") is not None or thresh.get("min_pct") is not None or thresh.get("max_pct") is not None) and (
        any(w in low for w in ["impact", "cost", "spend", "variance", "more", "less", "over", "under", "above", "below", "show", "filter", "only", "where", "with", "than", "$", "dollar", "%", "percent", "spike"])
    )

    lim_adj = extract_limit_adjustment(low)
    has_limit_refinement = lim_adj is not None

    sort_ref = extract_sort_refinement(low)
    has_sort_refinement = sort_ref is not None

    exclusions = extract_negative_exclusions(low)
    has_exclusion_refinement = exclusions.get("has_exclusion", False)

    ordinal_entity = resolve_ordinal_reference_from_history(messages, low)
    has_ordinal_refinement = ordinal_entity is not None

    has_relative_date_refinement = any(w in low for w in [
        "previous month", "last month", "the month before", "the month before that",
        "prior month", "that month", "same month"
    ])

    if not (has_similar_term or is_short_pivot or has_threshold_refinement or has_limit_refinement or has_sort_refinement or has_exclusion_refinement or has_ordinal_refinement or has_relative_date_refinement):
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
    if ordinal_entity:
        if ordinal_entity.get("service") and not new_svc:
            new_svc = ordinal_entity.get("service")
            new_svc_disp = ordinal_entity.get("service")
        if ordinal_entity.get("cloud") and not new_cloud:
            new_cloud = ordinal_entity.get("cloud")

    m_reg = re.search(r'\b(?:us|eu|ap|sa|ca|me|af)-[a-z]+-\d+\b|\b(?:eastus|westus|centralus|northeurope|westeurope|eastasia|southeastasia)\b', low)
    new_region = m_reg.group(0) if m_reg else None

    new_customer = None
    if cust_map:
        for cname in cust_map.keys():
            if cname.lower() in low:
                new_customer = cname
                break

    if not new_cloud:
        for prev_u in reversed(prior_user_msgs):
            c = extract_requested_cloud(prev_u)
            if c:
                new_cloud = c
                break
        if not new_cloud:
            if "across all clouds" in (last_asst_head + " " + last_asst_body) or "multi-cloud" in (last_asst_head + " " + last_asst_body):
                new_cloud = "all"
            elif "azure" in (last_asst_head + " " + last_asst_body):
                new_cloud = "azure"
            elif "gcp" in (last_asst_head + " " + last_asst_body):
                new_cloud = "gcp"
            elif "aws" in (last_asst_head + " " + last_asst_body):
                new_cloud = "aws"

    # Prior Query Type Detection
    prior_type = None
    inherited_target_year = None
    inherited_period_title = None
    inherited_timeframe_months = None
    inherited_target_ym = None
    for prev_u in reversed(prior_user_msgs):
        u_tctx = parse_query_time_context(prev_u)
        if u_tctx.get("target_ym") and u_tctx.get("is_specific"):
            inherited_target_ym = u_tctx.get("target_ym")
            break
    if not inherited_target_ym and last_asst:
        m_asst_ym = re.search(r'\b(202[0-9])-(0[1-9]|1[0-2])\b', last_asst)
        if m_asst_ym:
            inherited_target_ym = m_asst_ym.group(0)

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
        "identified anomaly impact" in last_asst_body or
        any(w in last_user_low for w in ["anomal", "cost spike", "spend spike", "unusual spend"]) or
        (has_threshold_refinement and any(w in (last_asst_head + " " + last_asst_body) for w in ["anomaly", "anomalies", "cost impact"]))
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
        "spend by rds instance type" in last_asst_body or
        "spend by ec2 instance type" in last_asst_body or
        any(w in last_user_low for w in ["instance type", "engine type", "storage class", "volume type"])
    ):
        prior_type = "service_breakdown"
        if "year-to-date" in last_asst_body or "(ytd" in last_asst_body or "ytd" in last_user_low:
            inherited_is_ytd = True
            inherited_timeframe_months = datetime.date.today().month
        elif "quarter-to-date" in last_asst_body or "(qtd" in last_asst_body or "qtd" in last_user_low:
            inherited_is_qtd = True
            inherited_timeframe_months = ((datetime.date.today().month - 1) % 3) + 1
        else:
            m_m = re.search(r'last\s*(\d{1,2})\s*months?', last_asst_body + " " + last_user_low)
            if m_m:
                inherited_timeframe_months = int(m_m.group(1))
            m_d = re.search(r'last\s*(\d{1,3})\s*days?', last_asst_body + " " + last_user_low)
            if m_d:
                inherited_timeframe_days = int(m_d.group(1))

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
        "top services" in last_user_low or
        "top spend" in last_user_low or
        ("top spend" in last_asst_head and "multi-service" not in last_asst_head) or
        ("spend analysis" in last_asst_head and "multi-service" not in last_asst_head) or
        ("top spend" in last_asst_body and "multi-service" not in last_asst_head)
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

    # Check for Multi-Service Spend:
    elif (
        "multi-service spend analysis" in last_asst_head or
        "multi-service spend" in last_asst_head or
        "multi-service" in last_asst_head or
        ("multi-service" in last_asst_body and "combined multi-service spend" in last_asst_body)
    ):
        prior_type = "multi_service"
        if "year-to-date" in last_asst_body or "(ytd" in last_asst_body or "ytd" in last_user_low:
            inherited_is_ytd = True
            inherited_timeframe_months = datetime.date.today().month
        elif "quarter-to-date" in last_asst_body or "(qtd" in last_asst_body or "qtd" in last_user_low:
            inherited_is_qtd = True
            inherited_timeframe_months = ((datetime.date.today().month - 1) % 3) + 1
        else:
            m_m = re.search(r'last\s*(\d{1,2})\s*months?', last_asst_body + " " + last_user_low)
            if m_m:
                inherited_timeframe_months = int(m_m.group(1))
            m_d = re.search(r'last\s*(\d{1,3})\s*days?', last_asst_body + " " + last_user_low)
            if m_d:
                inherited_timeframe_days = int(m_d.group(1))

    if not prior_type and ordinal_entity:
        if any(w in (last_asst_head + " " + last_asst_body) for w in ["anomal", "cost impact", "variance"]):
            prior_type = "anomalies"
        else:
            prior_type = "top_services"

    if not prior_type:
        return {"is_continuation": False}

    # Synthesize expanded query representation
    prov_disp = "all clouds" if new_cloud == "all" else ("Azure" if new_cloud == "azure" else ("GCP" if new_cloud == "gcp" else ("AWS" if new_cloud == "aws" else (new_svc_disp or "Cloud"))))
    if prior_type == "forecast":
        yr_label = inherited_period_title or f"FY {inherited_target_year or 2027}"
        expanded_query = f"give me the forecast for {prov_disp} cost for {yr_label} and break it down monthly"
    elif prior_type == "monthly_trend":
        expanded_query = f"give me the monthly spend trend and breakdown for {prov_disp} for the last {inherited_timeframe_months or 6} months"
    elif prior_type == "anomalies":
        status_word = "inactive " if ("inactive" in low or "inactive" in last_user_low) else ""
        thresh_phrase = ""
        if thresh.get("min") is not None:
            thresh_phrase = f" with cost impact > ${thresh['min']:,.2f}"
        elif thresh.get("max") is not None:
            thresh_phrase = f" with cost impact < ${thresh['max']:,.2f}"
        elif thresh.get("min_pct") is not None:
            thresh_phrase = f" with variance > {thresh['min_pct']:.0f}%"
        if ordinal_entity and any(w in low for w in ["why", "spike", "drill", "explain", "detail"]):
            reg_phrase = f" in {ordinal_entity.get('region')}" if ordinal_entity.get('region') else ""
            expanded_query = f"explain why {new_svc_disp or 'item'} had a cost anomaly{reg_phrase}"
        else:
            expanded_query = f"show top {status_word}cost anomalies detected for {prov_disp}{thresh_phrase}".strip()
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
    elif prior_type == "multi_service":
        svc_part = f"for {new_svc_disp}" if new_svc_disp else ""
        expanded_query = f"give me the multi-service spend analysis {svc_part}".strip()
    else:
        expanded_query = last_msg

    return {
        "is_continuation": True,
        "prior_query_type": prior_type,
        "inherited_target_year": inherited_target_year or 2027,
        "inherited_target_period_title": inherited_period_title or f"FY {inherited_target_year or 2027}",
        "inherited_timeframe_months": inherited_timeframe_months or 12,
        "inherited_timeframe_days": inherited_timeframe_days if 'inherited_timeframe_days' in locals() else None,
        "inherited_target_ym": inherited_target_ym,
        "inherited_is_ytd": inherited_is_ytd if 'inherited_is_ytd' in locals() else False,
        "inherited_is_qtd": inherited_is_qtd if 'inherited_is_qtd' in locals() else False,
        "inherited_limit": lim_adj,
        "sort_refinement": sort_ref,
        "exclusions": exclusions,
        "ordinal_entity": ordinal_entity,
        "new_cloud": new_cloud,
        "new_service": new_svc,
        "new_service_disp": new_svc_disp,
        "new_region": new_region,
        "new_customer": new_customer,
        "min_cost": thresh.get("min"),
        "max_cost": thresh.get("max"),
        "min_impact": thresh.get("min") if (prior_type == "anomalies" or "impact" in low) else None,
        "max_impact": thresh.get("max") if (prior_type == "anomalies" or "impact" in low) else None,
        "min_pct": thresh.get("min_pct"),
        "max_pct": thresh.get("max_pct"),
        "operator": thresh.get("operator"),
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

    # Azure Hybrid Benefit (AHB) signal detection
    is_ahb = any(w in low for w in [
        "hybrid discount", "hybrid discounts", "azure hybrid benefit", "hybrid benefit", "hybrid benefits",
        "ahb", "ahb discount", "ahb discounts", "hybrid licensing", "hybrid license"
    ])
    if is_ahb and not cloud:
        cloud = "azure"

    exclusions = cont_ctx.get("exclusions") or extract_negative_exclusions(last_msg)
    ex_svcs = set(exclusions.get("services", []))
    ex_terms = [t.lower() for t in exclusions.get("raw_terms", [])]

    # Service detection
    all_svcs = [
        s for s in extract_all_requested_services(last_msg)
        if getattr(s, "pcode", s[0]) not in ex_svcs and not any(t in getattr(s, "pcode", s[0]).lower() or t in getattr(s, "display_name", s[1]).lower() for t in ex_terms)
    ]
    service = cont_ctx.get("new_service")
    if service and (service in ex_svcs or any(t in service.lower() for t in ex_terms)):
        service = None
    if not service and is_ahb:
        service = "Virtual Machines"
    if not service:
        if all_svcs:
            service = all_svcs[0][0]
            if not cloud:
                cloud = getattr(all_svcs[0], "provider", None) or PCODE_TO_PROVIDER.get(service)

    if not service:
        if cloud == "azure":
            if any(w in low for w in ["vm", "vms", "virtual machine"]) and "Virtual Machines" not in ex_svcs: service = "Virtual Machines"
            elif any(w in low for w in ["blob", "storage account", "storage"]) and "Blob Storage" not in ex_svcs: service = "Blob Storage"
            elif any(w in low for w in ["disk", "disks", "managed disk"]) and "Managed Disks" not in ex_svcs: service = "Managed Disks"
            elif any(w in low for w in ["sql", "database"]) and "Azure SQL Database" not in ex_svcs: service = "Azure SQL Database"
            else: service = None
        elif cloud == "gcp":
            if any(w in low for w in ["compute", "gce", "instance"]) and "Compute Engine" not in ex_svcs: service = "Compute Engine"
            elif any(w in low for w in ["storage", "gcs", "bucket"]) and "Cloud Storage" not in ex_svcs: service = "Cloud Storage"
            elif any(w in low for w in ["disk", "persistent disk"]) and "Persistent Disk" not in ex_svcs: service = "Persistent Disk"
            elif any(w in low for w in ["bigquery", "query"]) and "BigQuery" not in ex_svcs: service = "BigQuery"
            elif any(w in low for w in ["sql", "database"]) and "Cloud SQL" not in ex_svcs: service = "Cloud SQL"
            else: service = None
        else:
            if (any(w in low for w in ["rds", "aurora", "relational database"]) or ("database" in low and not any(w in low for w in ["ec2", "s3", "dynamo"]))) and "AmazonRDS" not in ex_svcs and not any(t in "amazonrds" for t in ex_terms):
                service = "AmazonRDS"
            elif (any(w in low for w in ["ec2", "compute instance", "virtual machine"]) or ("instance type" in low and "rds" not in low and "database" not in low)) and "AmazonEC2" not in ex_svcs and not any(t in "amazonec2" for t in ex_terms):
                service = "AmazonEC2"
            elif any(w in low for w in ["s3", "bucket", "object storage"]) and "AmazonS3" not in ex_svcs:
                service = "AmazonS3"
            elif any(w in low for w in ["ebs", "ebs volume", "block storage", "gp2", "gp3"]) and "AmazonEC2_EBS" not in ex_svcs:
                service = "AmazonEC2_EBS"
            elif any(w in low for w in ["lambda", "serverless"]) and "AWSLambda" not in ex_svcs:
                service = "AWSLambda"
            elif any(w in low for w in ["dynamo", "dynamodb", "nosql"]) and "AmazonDynamoDB" not in ex_svcs:
                service = "AmazonDynamoDB"
            elif any(w in low for w in ["bedrock", "claude 3", "titan"]) and "AmazonBedrock" not in ex_svcs:
                service = "AmazonBedrock"
            elif any(w in low for w in ["cloudfront", "cdn"]) and "AmazonCloudFront" not in ex_svcs:
                service = "AmazonCloudFront"
            elif any(w in low for w in ["vpc", "nat gateway"]) and "AmazonVPC" not in ex_svcs:
                service = "AmazonVPC"
            elif not cloud and any(w in low for w in ["azure", "aks"]):
                service = None
                cloud = "azure"
            elif not cloud and any(w in low for w in ["gcp", "google cloud"]):
                service = None
                cloud = "gcp"

    if service and (service in ex_svcs or any(t in service.lower() for t in ex_terms)):
        service = None

    # Timeframe detection
    is_mtd = bool(re.search(r'\b(?:mtd|month\s*to\s*date|this\s*month|current\s*month)\b', low))
    m_months = re.search(r'\b(\d+)\s*(?:months?|m)\b', low)
    m_days = re.search(r'\b(\d+)\s*(?:days?|d)\b', low)
    inherited_ytd = cont_ctx.get("inherited_is_ytd", False) and not (bool(m_months) or bool(m_days) or is_mtd)
    inherited_qtd = cont_ctx.get("inherited_is_qtd", False) and not (bool(m_months) or bool(m_days) or is_mtd)
    is_ytd = bool(re.search(r'\b(?:ytd|year\s*to\s*date|this\s*year)\b', low)) or inherited_ytd
    is_qtd = bool(re.search(r'\b(?:qtd|quarter\s*to\s*date|this\s*quarter)\b', low)) or inherited_qtd
    has_explicit_months = is_ytd or is_qtd or is_mtd or bool(m_months) or any(w in low for w in ["12months", "12 months", "year", "annual", "months", "month by month", "monthly"])
    has_explicit_days = bool(m_days) or any(w in low for w in ["60days", "60 days", "30days", "30 days", "daily", "by day", "day by day", "per day"])

    timeframe_months = None
    timeframe_days = None
    if is_ytd:
        timeframe_months = datetime.date.today().month
        timeframe_days = None
    elif is_qtd:
        timeframe_months = ((datetime.date.today().month - 1) % 3) + 1
        timeframe_days = None
    elif is_mtd and not has_explicit_days:
        timeframe_months = 1
        timeframe_days = None
    elif cont_ctx.get("is_continuation"):
        if cont_ctx.get("prior_query_type") == "forecast":
            timeframe_months = 12
            timeframe_days = None
        elif cont_ctx.get("prior_query_type") in ("monthly_trend", "ai_model_breakdown"):
            timeframe_months = cont_ctx.get("inherited_timeframe_months", 6)
            timeframe_days = None
        elif cont_ctx.get("prior_query_type") in ("multi_service", "service_breakdown"):
            if is_ytd:
                timeframe_months = datetime.date.today().month
                timeframe_days = None
            elif is_qtd:
                timeframe_months = ((datetime.date.today().month - 1) % 3) + 1
                timeframe_days = None
            elif cont_ctx.get("inherited_timeframe_months"):
                timeframe_months = cont_ctx.get("inherited_timeframe_months")
                timeframe_days = None
            elif cont_ctx.get("inherited_timeframe_days"):
                timeframe_days = cont_ctx.get("inherited_timeframe_days")
                timeframe_months = None
            elif has_explicit_months:
                timeframe_months = int(m_months.group(1)) if m_months else 12
            elif has_explicit_days:
                timeframe_days = int(m_days.group(1)) if m_days else 30
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
    if any(w in low for w in [
        "lease type", "leasetype", "by lease", "lease breakdown", "lease types",
        "purchase option", "purchaseoption", "pricing model", "ondemand", "reservation",
        "savingsplan", "savings plan", "spot"
    ]):
        breakdowns.append("lease_type")
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
    ]) or bool(re.search(r'\b(?:find|list|query|fetch)\b', low))
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
        "inactive ones", "the inactive ones", "active ones", "the active ones",
        "impact cost", "cost impact"
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
    t_ctx = parse_query_time_context(last_msg, context_ym=cont_ctx.get("inherited_target_ym"))
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
        "vcpus", "vcpu", "cores", "units", "consumed quantity", "usage amount", "usage quantity",
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
        "by subscription", "per subscription", "sub account", "subaccount", "sub-account",
        "member account", "linked account", "usage account", "cloud account",
        "by member account", "by linked account", "by usage account", "by cloud account",
        "subscription-level", "project-level", "per cloud account", "linked accounts",
        "member accounts", "usage accounts"
    ]):
        breakdowns.append("account")
        target_dimension = "SubaccountId"
    elif any(w in low for w in [
        "by billing account", "per billing account", "billing account id", "billing accounts",
        "billing account breakdown", "breakdown by billing account", "payer account", "master account",
        "root account", "management account", "parent account", "by payer", "by master",
        "billing container", "by payer account", "by management account", "by root account",
        "payer id", "master account id", "root account id", "payer accounts", "master accounts"
    ]):
        breakdowns.append("billing_account")
        target_dimension = "BillingAccountId"
    elif any(w in low for w in [
        "by service category", "by service_category", "by category", "per category",
        "service categories", "category breakdown", "breakdown by category", "per service category",
        "by service categories", "by macro service", "macro service", "service family",
        "by service family", "by domain", "domain breakdown", "service domain", "cloud domain"
    ]):
        breakdowns.append("service_category")
        target_dimension = "ServiceCategory"
    elif any(w in low for w in [
        "by pricing category", "by pricing model", "by pricing", "per pricing category",
        "pricing categories", "pricing category breakdown", "breakdown by pricing", "by pricing type",
        "by lease type", "by lease", "lease type", "leasetype", "lease breakdown", "lease types",
        "by purchase option", "purchase option", "purchase options", "purchase option breakdown",
        "capacity type", "by capacity type", "by commercial model", "commercial model",
        "by contract type", "contract type", "by commitment type", "commitment type",
        "by charge type", "charge type breakdown"
    ]) or (any(w in low for w in ["ondemand", "on-demand", "reservation", "savings plan", "savingsplan", "spot"]) and any(w in low for w in ["breakdown", "break down", "split", "by", "usage", "cost", "spend", "lease", "above", "rds", "ec2"])):
        breakdowns.append("lease_type")
        target_dimension = "LeaseType"
    elif any(w in low for w in [
        "by resource", "per resource", "resource breakdown", "breakdown by resource",
        "by resource id", "resource-level", "top resources", "by individual resource",
        "by resource name", "resource name", "by arn", "per resource id", "individual resources",
        "by instance id", "by asset", "asset breakdown"
    ]):
        breakdowns.append("resource")
        target_dimension = "ResourceId"
    elif any(w in low for w in [
        "by region", "per region", "by region id", "region breakdown", "breakdown by region",
        "by location", "per location", "regional breakdown", "by datacenter", "by data center",
        "datacenter breakdown", "by geography", "by geo", "geographic breakdown", "geo breakdown",
        "by zone", "by availability zone", "cloud region", "by cloud region", "locations breakdown"
    ]):
        breakdowns.append("region")
        target_dimension = "RegionId"
    elif any(w in low for w in [
        "by model provider", "per model provider", "model provider breakdown", "breakdown by model provider",
        "ai provider", "by ai provider", "ai provider breakdown", "ai vendor", "by ai vendor",
        "llm vendor", "model vendor", "ai company"
    ]):
        breakdowns.append("model_provider")
        target_dimension = "ModelProvider"
    elif any(w in low for w in [
        "by model", "per model", "ai model", "model breakdown", "by llm", "llm breakdown",
        "foundation model", "by foundation model", "models breakdown", "by model name",
        "per model name", "model name", "breakdown by model name", "break down by model name",
        "break down by model", "break it down by model name", "break it down by model",
        "foundation models", "by llm model", "llm models", "language model", "language models"
    ]) or cont_ctx.get("prior_query_type") == "ai_model_breakdown" or (is_exclude_other_requested(low) and any(w in low for w in ["model", "models", "ai"])):
        breakdowns.append("model")
        target_dimension = "Model"
    elif any(w in low for w in ["by modality", "per modality", "modality breakdown", "breakdown by modality", "media type", "by media type", "input modality", "text vs multimodal", "multimodal breakdown"]):
        breakdowns.append("modality")
        target_dimension = "Modality"
    elif any(w in low for w in ["by execution type", "per execution type", "execution type breakdown", "breakdown by execution type", "inference type", "by inference type", "batch vs streaming", "realtime vs batch", "processing mode"]):
        breakdowns.append("execution_type")
        target_dimension = "ExecutionType"
    elif any(w in low for w in ["by token type", "per token type", "token type breakdown", "breakdown by token type", "prompt tokens", "completion tokens", "input tokens", "output tokens", "cached tokens", "tokens breakdown", "by tokens"]):
        breakdowns.append("token_type")
        target_dimension = "TokenType"
    elif any(w in low for w in ["by hardware family", "per hardware family", "hardware family breakdown", "gpu family", "accelerator family", "h100 vs a100"]):
        breakdowns.append("hardware_family")
        target_dimension = "HardwareFamily"
    elif any(w in low for w in ["by hardware type", "by hardware", "per hardware type", "hardware breakdown", "breakdown by hardware", "accelerator type", "by accelerator", "gpu vs tpu", "accelerators"]):
        breakdowns.append("hardware_type")
        target_dimension = "HardwareType"
    elif is_ahb:
        breakdowns.append("hybrid_benefit")
        target_dimension = "hybrid_benefit"
    elif any(w in low for w in ["by commitment plan", "by commitment", "by commitment type", "commitment breakdown", "savings plan breakdown", "by savings plan", "by reservation", "reservation breakdown", "by ri", "ri breakdown", "savings plans breakdown", "reserved instances"]):
        breakdowns.append("commitment_plan")
        target_dimension = "Commitment_Plan"
    elif any(w in low for w in ["by country", "emissions by country", "carbon by country", "emissions by geography", "carbon by geography", "by datacenter country"]):
        breakdowns.append("country")
        target_dimension = "Country"
    elif any(w in low for w in ["storageclass", "storage class", "tier", "storage tier", "by storage tier", "s3 tier", "lifecycle tier", "tier breakdown"]):
        breakdowns.append("storage_class")
        target_dimension = "product_storageClass"
    if any(w in low for w in ["volumetype", "volume type", "gp2", "gp3", "ebs type", "by volume type", "by disk type", "disk type", "by ebs type", "ebs volume type", "gp2 vs gp3"]):
        breakdowns.append("volume_type")
        target_dimension = "product_volumeType"
    if any(w in low for w in [
        "subcategory", "sub-category", "service subcategory", "by sub service", "per sub service",
        "sub-service", "sub service", "granular service", "service component", "meter category",
        "meter subcategory", "by meter", "component breakdown", "sub services", "granular services",
        "by meter category", "by meter subcategory"
    ]):
        breakdowns.append("service_subcategory")
        target_dimension = "ServiceSubcategory"
    if any(w in low for w in ["by operation", "operation breakdown", "api operation", "by api operation", "by action", "action breakdown", "api calls"]):
        breakdowns.append("operation")
        target_dimension = "lineItem_Operation"
    if any(w in low for w in ["by instance type", "instancetype", "by vm size", "vm size", "by machine type", "machine type", "instance size", "by instance size", "node type"]):
        breakdowns.append("instance_type")
        if not target_dimension:
            target_dimension = "product_InstanceType"

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
    include_insights = not is_no_insights_requested(low)

    if cont_ctx.get("is_continuation") and cont_ctx.get("prior_query_type") == "forecast":
        target_ym = f"{cont_ctx['inherited_target_year']}-01"

    thresh_det = extract_cost_threshold(last_msg)
    det_min_cost = thresh_det.get("min") if thresh_det.get("min") is not None else cont_ctx.get("min_cost")
    det_max_cost = thresh_det.get("max") if thresh_det.get("max") is not None else cont_ctx.get("max_cost")
    det_min_impact = thresh_det.get("min") if (is_anomaly or "impact" in low) else cont_ctx.get("min_impact")
    det_max_impact = thresh_det.get("max") if (is_anomaly or "impact" in low) else cont_ctx.get("max_impact")

    result = {
        "intent": intent,
        "cloud": cloud,
        "service": service,
        "services": [s[0] for s in all_svcs] if all_svcs else ([service] if service else []),
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
        "include_insights": include_insights,
        "is_new_data_fetch": is_new_data_fetch,
        "min_cost": det_min_cost,
        "max_cost": det_max_cost,
        "min_impact": det_min_impact,
        "max_impact": det_max_impact,
        "min_pct": thresh_det.get("min_pct") or cont_ctx.get("min_pct"),
        "max_pct": thresh_det.get("max_pct") or cont_ctx.get("max_pct"),
        "exclusions": cont_ctx.get("exclusions") or extract_negative_exclusions(last_msg),
        "ordinal_entity": cont_ctx.get("ordinal_entity"),
        "sort_refinement": cont_ctx.get("sort_refinement"),
        "limit": cont_ctx.get("inherited_limit"),
        "anomaly_status": detect_anomaly_status_filter(last_msg) if is_anomaly else None,
        "corrected_query": cont_ctx.get("expanded_query") or last_msg
    }

    # ponytail: fuzzy service correction — fires only when deterministic match failed
    # ceiling: difflib is O(n*m) but n is tiny (50 service names) so cost is negligible
    if not result.get("service"):
        from difflib import get_close_matches
        service_keys = list(AWS_SERVICES_MAP.keys()) + list(CLOUD_SERVICES_MAP.keys())
        matches = get_close_matches(low, service_keys, n=1, cutoff=0.82)
        if matches:
            matched_key = matches[0]
            canonical = AWS_SERVICES_MAP.get(matched_key) or CLOUD_SERVICES_MAP.get(matched_key)
            if canonical:
                result["service"] = canonical
                result["fuzzy_corrected"] = True

    return result

