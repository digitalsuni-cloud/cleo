"""
test_cleo_finops_suite.py — Automated 110-test-case probe suite for Cleo FinOps Agent.

Covers:
  1. AWS Inefficiencies & Architecture (15 tests)
  2. Commitments & Sizing Strategies (15 tests)
  3. Azure FinOps Inefficiencies (15 tests)
  4. GCP FinOps Inefficiencies (10 tests)
  5. Kubernetes FinOps & Allocation (10 tests)
  6. Data Platforms & AI Economics (15 tests)
  7. FinOps Operating Model & FOCUS 1.2 (15 tests)
  8. CloudHealth Live Telemetry & Platform (15 tests)

Evaluates intent classification, substance, accuracy, and absence of misrouted telemetry / fallbacks.
"""
import sys
import os
import json
import time

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cleo_agent import (
    get_access_token,
    MCPClient,
    AIClient,
    _load_config,
    build_system_prompt,
    run_agent_turn,
)

# ── 110 Rigorous Test Cases ──────────────────────────────────────────────────
TEST_CASES = [
    # ── Category 1: AWS Inefficiencies & Architecture (15 tests) ───────────────
    {
        "id": "AWS-01",
        "category": "AWS Architecture",
        "type": "advisory",
        "query": "How do I optimize EBS volumes by migrating from gp2 to gp3?",
        "expected_keywords": ["gp3", "20%", "iops", "throughput"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "AWS-02",
        "category": "AWS Architecture",
        "type": "advisory",
        "query": "What workloads are ideal candidates for migrating to AWS Graviton?",
        "expected_keywords": ["graviton", "arm", "price-performance"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "AWS-03",
        "category": "AWS Architecture",
        "type": "advisory",
        "query": "How do we identify and eliminate zombie NAT Gateways in AWS?",
        "expected_keywords": ["nat gateway", "idle", "traffic"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "AWS-04",
        "category": "AWS Architecture",
        "type": "advisory",
        "query": "How do VPC Gateway Endpoints reduce NAT Gateway data processing charges for S3?",
        "expected_keywords": ["gateway endpoint", "s3", "nat gateway", "free"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "AWS-05",
        "category": "AWS Architecture",
        "type": "advisory",
        "query": "What are best practices for cleaning up orphaned unattached EBS volumes?",
        "expected_keywords": ["unattached", "available", "snapshot", "delete"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "AWS-06",
        "category": "AWS Architecture",
        "type": "advisory",
        "query": "How to detect and prune EBS snapshot sprawl in AWS?",
        "expected_keywords": ["snapshot", "retention", "ami", "lifecycle"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "AWS-07",
        "category": "AWS Architecture",
        "type": "advisory",
        "query": "What lifecycle rule prevents S3 incomplete multipart uploads from accumulating cost?",
        "expected_keywords": ["multipart", "abort", "lifecycle", "days"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "AWS-08",
        "category": "AWS Architecture",
        "type": "advisory",
        "query": "When should we transition objects from S3 Standard to Intelligent-Tiering or Glacier?",
        "expected_keywords": ["intelligent-tiering", "glacier", "lifecycle", "retrieval"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "AWS-09",
        "category": "AWS Architecture",
        "type": "advisory",
        "query": "How do we detect an idle or underutilized AWS Application Load Balancer?",
        "expected_keywords": ["load balancer", "target", "request", "idle"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "AWS-10",
        "category": "AWS Architecture",
        "type": "advisory",
        "query": "How can cross-AZ data transfer egress costs be minimized in AWS?",
        "expected_keywords": ["cross-az", "availability zone", "egress", "traffic"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "AWS-11",
        "category": "AWS Architecture",
        "type": "advisory",
        "query": "What are the key rightsizing levers for oversized Amazon RDS database instances?",
        "expected_keywords": ["rds", "rightsizing", "cpu", "memory"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "AWS-12",
        "category": "AWS Architecture",
        "type": "advisory",
        "query": "How do we tune Aurora Serverless v2 ACU allocation to avoid idle baseline burn?",
        "expected_keywords": ["aurora", "acu", "serverless", "capacity"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "AWS-13",
        "category": "AWS Architecture",
        "type": "advisory",
        "query": "How to detect idle or oversized SageMaker endpoints?",
        "expected_keywords": ["sagemaker", "endpoint", "invocation", "idle"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "AWS-14",
        "category": "AWS Architecture",
        "type": "advisory",
        "query": "What waste occurs with always-on SageMaker notebook instances and how to fix it?",
        "expected_keywords": ["notebook", "stop", "idle", "auto-stop"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "AWS-15",
        "category": "AWS Architecture",
        "type": "advisory",
        "query": "How do we identify GPU instances running purely CPU-bound workloads in AWS?",
        "expected_keywords": ["gpu", "utilization", "cpu", "workload"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },

    # ── Category 2: Commitments & Sizing Strategies (15 tests) ────────────────
    {
        "id": "CMT-01",
        "category": "Commitments",
        "type": "advisory",
        "query": "What is the difference between AWS Compute Savings Plans and EC2 Instance Savings Plans?",
        "expected_keywords": ["compute savings plan", "ec2 instance", "flexibility", "discount"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "CMT-02",
        "category": "Commitments",
        "type": "advisory",
        "query": "How do AWS Convertible Reserved Instances compare to Standard RIs?",
        "expected_keywords": ["convertible", "standard", "exchange", "discount"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "CMT-03",
        "category": "Commitments",
        "type": "advisory",
        "query": "What is the break-even utilization rate on a 1-year No Upfront AWS Savings Plan?",
        "expected_keywords": ["break-even", "savings plan", "utilization", "commitment"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "CMT-04",
        "category": "Commitments",
        "type": "advisory",
        "query": "How does Azure Reservation break-even compare to Azure Savings Plans?",
        "expected_keywords": ["reservation", "savings plan", "break-even", "azure"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "CMT-05",
        "category": "Commitments",
        "type": "advisory",
        "query": "How does Azure Hybrid Benefit AHB work for Windows Server and SQL Server?",
        "expected_keywords": ["azure hybrid benefit", "ahb", "license", "core"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "CMT-06",
        "category": "Commitments",
        "type": "advisory",
        "query": "What is the 1 February 2027 exchange retirement deadline for Azure compute reservations?",
        "expected_keywords": ["2027", "exchange", "reservation", "azure"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "CMT-07",
        "category": "Commitments",
        "type": "advisory",
        "query": "How do GCP Committed Use Discounts CUDs differ between resource-based and spend-based?",
        "expected_keywords": ["committed use discount", "cud", "resource-based", "spend-based"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "CMT-08",
        "category": "Commitments",
        "type": "advisory",
        "query": "How do GCP Sustained Use Discounts SUDs apply automatically to Compute Engine?",
        "expected_keywords": ["sustained use discount", "sud", "automatic", "compute engine"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "CMT-09",
        "category": "Commitments",
        "type": "advisory",
        "query": "What is the recommended phased commitment purchasing strategy for a Crawl-to-Walk maturity org?",
        "expected_keywords": ["phased", "crawl", "walk", "commitment"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "CMT-10",
        "category": "Commitments",
        "type": "advisory",
        "query": "How do we manage expiring commitments without risking commitment gap cliffs?",
        "expected_keywords": ["expiring", "renewal", "commitment", "runway"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "CMT-11",
        "category": "Commitments",
        "type": "advisory",
        "query": "Why is committing to 100% of peak compute usage considered an anti-pattern?",
        "expected_keywords": ["peak", "waste", "baseline", "anti-pattern"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "CMT-12",
        "category": "Commitments",
        "type": "advisory",
        "query": "How does Spot instance diversification protect against price spikes and evictions?",
        "expected_keywords": ["spot", "diversification", "eviction", "instance"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "CMT-13",
        "category": "Commitments",
        "type": "advisory",
        "query": "How does AWS EDP Enterprise Discount Plan interact with Savings Plans and RIs?",
        "expected_keywords": ["edp", "savings plan", "discount", "enterprise"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "CMT-14",
        "category": "Commitments",
        "type": "advisory",
        "query": "What is Microsoft MACC commitment burn-down and how does Azure marketplace spend count?",
        "expected_keywords": ["macc", "azure", "marketplace", "burn"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "CMT-15",
        "category": "Commitments",
        "type": "advisory",
        "query": "What is commitment portfolio liquidity in FinOps?",
        "expected_keywords": ["liquidity", "portfolio", "commitment", "risk"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },

    # ── Category 3: Azure FinOps Inefficiencies (15 tests) ─────────────────────
    {
        "id": "AZR-01",
        "category": "Azure FinOps",
        "type": "advisory",
        "query": "How do we control and optimize Azure Log Analytics workspace ingestion costs?",
        "expected_keywords": ["log analytics", "ingestion", "retention", "commitment"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "AZR-02",
        "category": "Azure FinOps",
        "type": "advisory",
        "query": "How to detect orphaned unattached Azure Managed Disks?",
        "expected_keywords": ["unattached", "managed disk", "orphan", "delete"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "AZR-03",
        "category": "Azure FinOps",
        "type": "advisory",
        "query": "What is the difference between Stopped and Deallocated states in Azure VMs?",
        "expected_keywords": ["stopped", "deallocated", "compute", "billing"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "AZR-04",
        "category": "Azure FinOps",
        "type": "advisory",
        "query": "How to optimize AKS Azure Kubernetes Service node pools and system vs user pools?",
        "expected_keywords": ["aks", "node pool", "system", "user"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "AZR-05",
        "category": "Azure FinOps",
        "type": "advisory",
        "query": "How to detect unused Azure Reservations and what are the refund/exchange limits?",
        "expected_keywords": ["reservation", "unused", "refund", "exchange"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "AZR-06",
        "category": "Azure FinOps",
        "type": "advisory",
        "query": "When should data in Azure Blob Storage be tiered from Hot to Cool or Archive?",
        "expected_keywords": ["blob", "hot", "cool", "archive"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "AZR-07",
        "category": "Azure FinOps",
        "type": "advisory",
        "query": "What are the hidden rehydration and early deletion costs in Azure Archive storage?",
        "expected_keywords": ["archive", "rehydration", "early deletion", "days"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "AZR-08",
        "category": "Azure FinOps",
        "type": "advisory",
        "query": "How to detect and clean up orphaned Azure Public IPs and Network Interfaces?",
        "expected_keywords": ["public ip", "nic", "orphaned", "unassociated"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "AZR-09",
        "category": "Azure FinOps",
        "type": "advisory",
        "query": "How to identify overprovisioned Azure App Service Plans?",
        "expected_keywords": ["app service", "cpu", "scale", "plan"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "AZR-10",
        "category": "Azure FinOps",
        "type": "advisory",
        "query": "How to identify idle Azure SQL Databases and evaluate Serverless tier?",
        "expected_keywords": ["azure sql", "serverless", "pause", "idle"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "AZR-11",
        "category": "Azure FinOps",
        "type": "advisory",
        "query": "What is the break-even between Azure OpenAI Provisioned Throughput Units PTUs and PAYG?",
        "expected_keywords": ["ptu", "payg", "azure openai", "throughput"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "AZR-12",
        "category": "Azure FinOps",
        "type": "advisory",
        "query": "What is the EA to MCA transition impact on Azure billing and cost management?",
        "expected_keywords": ["ea", "mca", "billing profile", "invoice"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "AZR-13",
        "category": "Azure FinOps",
        "type": "advisory",
        "query": "How does Azure Policy enforce tagging and prevent deployment of non-standard VM sizes?",
        "expected_keywords": ["azure policy", "tag", "vm", "enforce"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "AZR-14",
        "category": "Azure FinOps",
        "type": "advisory",
        "query": "How does Azure Advisor rightsizing recommendations compare to actual CPU p95 metrics?",
        "expected_keywords": ["azure advisor", "p95", "rightsizing", "metric"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "AZR-15",
        "category": "Azure FinOps",
        "type": "advisory",
        "query": "How to eliminate snapshot sprawl in Azure Managed Disks?",
        "expected_keywords": ["snapshot", "managed disk", "retention", "azure"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },

    # ── Category 4: GCP FinOps Inefficiencies (10 tests) ──────────────────────
    {
        "id": "GCP-01",
        "category": "GCP FinOps",
        "type": "advisory",
        "query": "When should BigQuery workloads use slot reservations versus on-demand analysis pricing?",
        "expected_keywords": ["bigquery", "slot", "on-demand", "edition"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "GCP-02",
        "category": "GCP FinOps",
        "type": "advisory",
        "query": "How to optimize BigQuery costs using partition tables and clustering?",
        "expected_keywords": ["bigquery", "partition", "cluster", "scan"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "GCP-03",
        "category": "GCP FinOps",
        "type": "advisory",
        "query": "How to detect and clean up orphan Google Cloud persistent disks?",
        "expected_keywords": ["persistent disk", "orphan", "unattached", "gcp"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "GCP-04",
        "category": "GCP FinOps",
        "type": "advisory",
        "query": "How do GKE Autopilot clusters compare to Standard GKE in terms of pod billing?",
        "expected_keywords": ["gke", "autopilot", "standard", "pod"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "GCP-05",
        "category": "GCP FinOps",
        "type": "advisory",
        "query": "How to handle GCP resource-based CUD mismatch when VM machine families change?",
        "expected_keywords": ["cud", "mismatch", "machine family", "gcp"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "GCP-06",
        "category": "GCP FinOps",
        "type": "advisory",
        "query": "How to configure Google Cloud Storage lifecycle management across Standard, Nearline, Coldline, and Archive?",
        "expected_keywords": ["gcs", "nearline", "coldline", "archive"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "GCP-07",
        "category": "GCP FinOps",
        "type": "advisory",
        "query": "What causes Cloud Functions cold starts and cost spikes, and how to tune min instances?",
        "expected_keywords": ["cloud functions", "min instances", "cold start", "concurrency"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "GCP-08",
        "category": "GCP FinOps",
        "type": "advisory",
        "query": "What are the common storage auto-increase pitfalls in Google Cloud SQL?",
        "expected_keywords": ["cloud sql", "storage", "auto-increase", "downsize"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "GCP-09",
        "category": "GCP FinOps",
        "type": "advisory",
        "query": "How does GCP Cloud Billing export to BigQuery enable detailed cost visibility?",
        "expected_keywords": ["billing export", "bigquery", "gcp", "labels"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "GCP-10",
        "category": "GCP FinOps",
        "type": "advisory",
        "query": "How does GCP Cloud Carbon Footprint measure emissions across Google Cloud regions?",
        "expected_keywords": ["carbon", "footprint", "emissions", "gcp"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },

    # ── Category 5: Kubernetes FinOps & Allocation (10 tests) ──────────────────
    {
        "id": "K8S-01",
        "category": "Kubernetes FinOps",
        "type": "advisory",
        "query": "How does OpenCost allocate Kubernetes spend based on CPU and RAM requests versus usage?",
        "expected_keywords": ["opencost", "request", "usage", "cpu"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "K8S-02",
        "category": "Kubernetes FinOps",
        "type": "advisory",
        "query": "How do Kubecost and OpenCost calculate idle node capacity cost?",
        "expected_keywords": ["idle", "node", "kubecost", "allocation"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "K8S-03",
        "category": "Kubernetes FinOps",
        "type": "advisory",
        "query": "What are the best practices for container rightsizing using VPA with p95 or p99 targets?",
        "expected_keywords": ["vpa", "rightsizing", "p95", "headroom"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "K8S-04",
        "category": "Kubernetes FinOps",
        "type": "advisory",
        "query": "How does Karpenter improve Kubernetes node efficiency compared to standard Cluster Autoscaler?",
        "expected_keywords": ["karpenter", "cluster autoscaler", "consolidation", "node"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "K8S-05",
        "category": "Kubernetes FinOps",
        "type": "advisory",
        "query": "How does AWS EKS Split Cost Allocation work with AWS CUR?",
        "expected_keywords": ["eks", "split cost", "cur", "pod"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "K8S-06",
        "category": "Kubernetes FinOps",
        "type": "advisory",
        "query": "How does GKE Cost Allocation tag and export namespace metrics to BigQuery?",
        "expected_keywords": ["gke", "namespace", "bigquery", "cost allocation"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "K8S-07",
        "category": "Kubernetes FinOps",
        "type": "advisory",
        "query": "How should shared Kubernetes daemonsets and ingress controllers be allocated across namespaces?",
        "expected_keywords": ["daemonset", "ingress", "shared", "namespace"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "K8S-08",
        "category": "Kubernetes FinOps",
        "type": "advisory",
        "query": "How do Pod Disruption Budgets PDBs interact with aggressive Karpenter consolidation?",
        "expected_keywords": ["pdb", "pod disruption", "karpenter", "consolidation"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "K8S-09",
        "category": "Kubernetes FinOps",
        "type": "advisory",
        "query": "What is the node efficiency KPI formula in Kubernetes FinOps?",
        "expected_keywords": ["efficiency", "node", "utilization", "request"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "K8S-10",
        "category": "Kubernetes FinOps",
        "type": "advisory",
        "query": "How can Kubernetes spend be emitted in FOCUS 1.2 format?",
        "expected_keywords": ["focus", "opencost", "effectivecost", "format"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },

    # ── Category 6: Data Platforms & AI Economics (15 tests) ──────────────────
    {
        "id": "DATA-01",
        "category": "Data Platforms",
        "type": "advisory",
        "query": "What is the recommended auto-suspend timeout for Snowflake virtual warehouses?",
        "expected_keywords": ["snowflake", "auto-suspend", "timeout", "seconds"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "DATA-02",
        "category": "Data Platforms",
        "type": "advisory",
        "query": "How does Snowflake QUERY_ATTRIBUTION_HISTORY attribute warehouse credits to specific queries?",
        "expected_keywords": ["query_attribution_history", "snowflake", "credit", "attribution"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "DATA-03",
        "category": "Data Platforms",
        "type": "advisory",
        "query": "What is the Databricks Photon engine multiplier and when is it cost-effective?",
        "expected_keywords": ["databricks", "photon", "multiplier", "dbu"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "DATA-04",
        "category": "Data Platforms",
        "type": "advisory",
        "query": "How do Databricks serverless compute premiums compare to customer-hosted EC2/Azure VM clusters?",
        "expected_keywords": ["databricks", "serverless", "dbu", "cluster"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "DATA-05",
        "category": "Data Platforms",
        "type": "advisory",
        "query": "How does Microsoft Fabric Capacity Unit CU smoothing window work across 24 hours?",
        "expected_keywords": ["fabric", "cu", "smoothing", "capacity"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "DATA-06",
        "category": "Data Platforms",
        "type": "advisory",
        "query": "How does prompt caching reduce input token costs in Anthropic Claude and OpenAI?",
        "expected_keywords": ["prompt caching", "token", "input", "cache"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "DATA-07",
        "category": "Data Platforms",
        "type": "advisory",
        "query": "What discount does Anthropic Batch API provide and what is the turnaround SLA?",
        "expected_keywords": ["batch api", "50%", "anthropic", "discount"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "DATA-08",
        "category": "Data Platforms",
        "type": "advisory",
        "query": "What is the break-even threshold between self-hosted vLLM/SGLang GPUs and managed LLM APIs?",
        "expected_keywords": ["vllm", "managed", "gpu", "break-even"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "DATA-09",
        "category": "Data Platforms",
        "type": "advisory",
        "query": "Why is standard GPU utilization percentage misleading for AI inference without DCGM metrics?",
        "expected_keywords": ["gpu", "dcgm", "tensor core", "memory bandwidth"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "DATA-10",
        "category": "Data Platforms",
        "type": "advisory",
        "query": "How to detect and prevent agent-loop flat-line burn in autonomous AI systems?",
        "expected_keywords": ["agent", "loop", "burn", "token"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "DATA-11",
        "category": "Data Platforms",
        "type": "advisory",
        "query": "What is cost per completed task in agentic FinOps?",
        "expected_keywords": ["cost per task", "agentic", "finops", "task"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "DATA-12",
        "category": "Data Platforms",
        "type": "advisory",
        "query": "How to optimize AI coding tool spend across Cursor, Claude Code, and GitHub Copilot?",
        "expected_keywords": ["coding tool", "cursor", "copilot", "seat"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "DATA-13",
        "category": "Data Platforms",
        "type": "advisory",
        "query": "How does Bedrock Application Inference Profiles enable cross-region routing and cost tracking?",
        "expected_keywords": ["bedrock", "inference profile", "routing", "cost"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "DATA-14",
        "category": "Data Platforms",
        "type": "advisory",
        "query": "How does Google Vertex AI provisioned throughput compare to PAYG for Gemini models?",
        "expected_keywords": ["vertex", "provisioned throughput", "gemini", "payg"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "DATA-15",
        "category": "Data Platforms",
        "type": "advisory",
        "query": "What are the token pricing dynamics and peak versus off-peak discounts of open-weight model APIs like DeepSeek and Qwen?",
        "expected_keywords": ["deepseek", "qwen", "token", "open-weight"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },

    # ── Category 7: FinOps Operating Model & FOCUS 1.2 (15 tests) ──────────────
    {
        "id": "OPS-01",
        "category": "FinOps Framework",
        "type": "advisory",
        "query": "Explain the difference between EffectiveCost and BilledCost in FOCUS 1.2.",
        "expected_keywords": ["effectivecost", "billedcost", "amortiz", "focus"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "OPS-02",
        "category": "FinOps Framework",
        "type": "advisory",
        "query": "What are the three core phases of the FinOps lifecycle?",
        "expected_keywords": ["inform", "optimize", "operate"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "OPS-03",
        "category": "FinOps Framework",
        "type": "advisory",
        "query": "What are the key differences across Crawl, Walk, and Run maturity stages in FinOps?",
        "expected_keywords": ["crawl", "walk", "run", "maturity"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "OPS-04",
        "category": "FinOps Framework",
        "type": "advisory",
        "query": "What is the blended cost trap and why should FinOps practitioners avoid blended rates?",
        "expected_keywords": ["blended", "unblended", "trap", "amortized"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "OPS-05",
        "category": "FinOps Framework",
        "type": "advisory",
        "query": "What is the difference between Showback and Hard Chargeback, and what are the accounting prerequisites?",
        "expected_keywords": ["showback", "chargeback", "erp", "accountability"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "OPS-06",
        "category": "FinOps Framework",
        "type": "advisory",
        "query": "What are the defensible allocation methods for shared cloud services like network and security?",
        "expected_keywords": ["shared", "allocation", "proportional", "fixed"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "OPS-07",
        "category": "FinOps Framework",
        "type": "advisory",
        "query": "What are unit economics metrics in FinOps and why are business denominators important?",
        "expected_keywords": ["unit economics", "denominator", "business", "metric"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "OPS-08",
        "category": "FinOps Framework",
        "type": "advisory",
        "query": "How should cost anomaly detection thresholds be tuned to minimize alert fatigue?",
        "expected_keywords": ["anomaly", "threshold", "alert fatigue", "detection"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "OPS-09",
        "category": "FinOps Framework",
        "type": "advisory",
        "query": "What is schedule blindness in non-production environments and how is it eliminated?",
        "expected_keywords": ["schedule blindness", "non-production", "schedule", "stop"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "OPS-10",
        "category": "FinOps Framework",
        "type": "advisory",
        "query": "What are mandatory tagging hygiene standards at the workload intake migration gate?",
        "expected_keywords": ["tag", "intake", "mandatory", "migration"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "OPS-11",
        "category": "FinOps Framework",
        "type": "advisory",
        "query": "What is the double-bubble cost pattern during cloud migrations?",
        "expected_keywords": ["double-bubble", "migration", "parallel", "cost"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "OPS-12",
        "category": "FinOps Framework",
        "type": "advisory",
        "query": "What is GreenOps and how do Scope 2 and Scope 3 cloud carbon emissions map to FinOps?",
        "expected_keywords": ["greenops", "scope 2", "scope 3", "carbon"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "OPS-13",
        "category": "FinOps Framework",
        "type": "advisory",
        "query": "How should ITAM and FinOps collaborate on Bring Your Own License BYOL and software asset management?",
        "expected_keywords": ["itam", "byol", "license", "compliance"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "OPS-14",
        "category": "FinOps Framework",
        "type": "advisory",
        "query": "What is the OptimNow doctrine of 'diagnose before prescribing'?",
        "expected_keywords": ["diagnose", "prescribing", "optimnow", "telemetry"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },
    {
        "id": "OPS-15",
        "category": "FinOps Framework",
        "type": "advisory",
        "query": "What is the difference between realized savings and potential savings reporting for executive stakeholders?",
        "expected_keywords": ["realized", "potential", "savings", "executive"],
        "banned_phrases": ["CloudHealth Spend Analysis: Monthly Breakdown", "I have received your request:"],
    },

    # ── Category 8: CloudHealth Live Telemetry & Platform (15 tests) ───────────
    {
        "id": "CH-01",
        "category": "CloudHealth Telemetry",
        "type": "telemetry",
        "query": "List available datasets",
        "expected_keywords": ["AWS_CUR", "Available", "dataset"],
        "banned_phrases": ["I have received your request:"],
    },
    {
        "id": "CH-02",
        "category": "CloudHealth Telemetry",
        "type": "telemetry",
        "query": "Show schema for AWS_CUR",
        "expected_keywords": ["AWS_CUR", "lineItem", "Column Name"],
        "banned_phrases": ["I have received your request:"],
    },
    {
        "id": "CH-03",
        "category": "CloudHealth Telemetry",
        "type": "telemetry",
        "query": "Show metadata for CLOUDHEALTH_CONSUMPTION_BREAKDOWN",
        "expected_keywords": ["CLOUDHEALTH_CONSUMPTION_BREAKDOWN", "Column Name"],
        "banned_phrases": ["I have received your request:"],
    },
    {
        "id": "CH-04",
        "category": "CloudHealth Telemetry",
        "type": "telemetry",
        "query": "List all managed organizations",
        "expected_keywords": ["Organization", "CRN"],
        "banned_phrases": ["I have received your request:"],
    },
    {
        "id": "CH-05",
        "category": "CloudHealth Telemetry",
        "type": "telemetry",
        "query": "List all channel customers and tenants",
        "expected_keywords": ["Customer", "CRN"],
        "banned_phrases": ["I have received your request:"],
    },
    {
        "id": "CH-06",
        "category": "CloudHealth Telemetry",
        "type": "telemetry",
        "query": "Show monthly spend breakdown",
        "expected_keywords": ["Spend Analysis", "month", "$"],
        "banned_phrases": ["I have received your request:"],
    },
    {
        "id": "CH-07",
        "category": "CloudHealth Telemetry",
        "type": "telemetry",
        "query": "Show top 5 customers by spend last month",
        "expected_keywords": ["Customer", "$"],
        "banned_phrases": ["I have received your request:"],
    },
    {
        "id": "CH-08",
        "category": "CloudHealth Telemetry",
        "type": "telemetry",
        "query": "Show top AWS services by spend",
        "expected_keywords": ["Service", "$"],
        "banned_phrases": ["I have received your request:"],
    },
    {
        "id": "CH-09",
        "category": "CloudHealth Telemetry",
        "type": "telemetry",
        "query": "Show multi-cloud spend by service across all clouds",
        "expected_keywords": ["Spend", "$"],
        "banned_phrases": ["I have received your request:"],
    },
    {
        "id": "CH-10",
        "category": "CloudHealth Telemetry",
        "type": "telemetry",
        "query": "What is the spend for customer OpenNet?",
        "expected_keywords": ["OpenNet", "$"],
        "banned_phrases": ["I have received your request:"],
    },
    {
        "id": "CH-11",
        "category": "CloudHealth Telemetry",
        "type": "telemetry",
        "query": "Show AWS cost anomalies detected by CloudHealth",
        "expected_keywords": ["Anomal", "AWS"],
        "banned_phrases": ["I have received your request:"],
    },
    {
        "id": "CH-12",
        "category": "CloudHealth Telemetry",
        "type": "telemetry",
        "query": "Show Azure cost anomalies detected by CloudHealth",
        "expected_keywords": ["Anomal", "Azure"],
        "banned_phrases": ["I have received your request:"],
    },
    {
        "id": "CH-13",
        "category": "CloudHealth Telemetry",
        "type": "telemetry",
        "query": "Show EC2 cost for customer Lundbeck",
        "expected_keywords": ["Lundbeck", "$"],
        "banned_phrases": ["I have received your request:"],
    },
    {
        "id": "CH-14",
        "category": "CloudHealth Telemetry",
        "type": "telemetry",
        "query": "Top 3 cost optimization recommendations for Lundbeck based on their last 30 days usage",
        "expected_keywords": ["Lundbeck", "Optimization", "Savings"],
        "banned_phrases": ["I have received your request:"],
    },
    {
        "id": "CH-15",
        "category": "CloudHealth Telemetry",
        "type": "telemetry",
        "query": "Show monthly cloud spend trend with waterfall chart",
        "expected_keywords": ["Spend", "$"],
        "banned_phrases": ["I have received your request:"],
    },
]


def run_suite(engine: str = "direct", verbose: bool = False, max_tests: int = 110) -> dict:
    """Runs the test suite and returns detailed results."""
    token = get_access_token()
    mcp = MCPClient(token=token or "")
    tools = mcp.initialize().get("tools", [])
    cfg = _load_config()
    ai = AIClient(engine_key=engine, cfg=cfg, tools=tools)

    passed = 0
    failed = 0
    results = []

    test_slice = TEST_CASES[:max_tests]
    print(f"\n🚀 Running Cleo FinOps Probe Suite ({len(test_slice)} test cases)")
    print(f"   Engine: {engine} | CloudHealth Authenticated: {bool(token)}")
    print("=" * 75)

    start_time = time.time()

    for idx, tc in enumerate(test_slice, start=1):
        t_id = tc["id"]
        cat = tc["category"]
        q = tc["query"]
        expected_kw = tc["expected_keywords"]
        banned = tc.get("banned_phrases", [])

        messages = [
            {"role": "system", "content": build_system_prompt(tools)},
            {"role": "user", "content": q}
        ]

        try:
            resp = run_agent_turn(mcp, ai, messages)
            resp_low = resp.lower()

            # Verify no banned phrases
            banned_hit = [b for b in banned if b.lower() in resp_low]
            if banned_hit:
                status = "FAIL"
                reason = f"Hit banned phrase: {banned_hit[0]}"
            else:
                # Check for expected keywords (require at least 1 or 2 matching depending on length)
                matched_kw = [k for k in expected_kw if k.lower() in resp_low]
                # Pass threshold: at least 1 keyword for short lists, or 50% of keywords
                min_match = 1 if len(expected_kw) <= 2 else max(2, len(expected_kw) // 2)
                if len(matched_kw) >= min_match:
                    status = "PASS"
                    reason = f"Matched keywords: {matched_kw}"
                else:
                    status = "FAIL"
                    reason = f"Missing keywords (needed >={min_match} of {expected_kw}, got {matched_kw})"

        except Exception as e:
            status = "ERROR"
            reason = f"Exception raised: {str(e)}"
            resp = ""

        if status == "PASS":
            passed += 1
            icon = "✅"
        else:
            failed += 1
            icon = "❌"

        results.append({
            "id": t_id,
            "category": cat,
            "query": q,
            "status": status,
            "reason": reason,
            "response_preview": resp[:180].replace("\n", " ") if resp else ""
        })

        if verbose or status != "PASS":
            print(f"{icon} [{t_id}] ({cat}) '{q[:40]}...' -> {status} ({reason})")

    duration = round(time.time() - start_time, 2)
    pass_pct = round((passed / len(test_slice)) * 100, 1)

    print("=" * 75)
    print(f"📊 SUMMARY: {passed}/{len(test_slice)} Passed ({pass_pct}%) | Failed: {failed} | Time: {duration}s")
    print("=" * 75)

    # ── Multi-Turn LLM-First Intent & Timeframe Regression Suite ──
    print("\n🔄 Running Multi-Turn LLM-First Intent & 30-Day Trend Regression Checks...")
    mt_start = time.time()
    try:
        # Turn 1: EC2 spend query
        t1_resp = ai.generate([{"role": "user", "content": "Show me ec2 usage"}], mcp=mcp)
        assert "EC2 Spend Analysis" in t1_resp, "Turn 1 failed: EC2 not found"
        assert "AWS_EC2_COST_AND_USAGE" in t1_resp, "Turn 1 failed: AWS_EC2_COST_AND_USAGE not in response"

        # Turn 2: RDS Lundbeck 12 months (verifying no EC2 table cache hijack occurs!)
        t2_msgs = [
            {"role": "user", "content": "Show me ec2 usage"},
            {"role": "assistant", "content": t1_resp},
            {"role": "user", "content": "okay. get me the last 12months RDS Usage for Lundbeck customer and break it down by InstanceType and another chart with Enginetype"}
        ]
        t2_resp = ai.generate(t2_msgs, mcp=mcp)
        assert "RDS Spend Analysis" in t2_resp, "Turn 2 failed: RDS Spend Analysis not in response"
        assert "Lundbeck" in t2_resp, "Turn 2 failed: Lundbeck not in response"
        assert "AWS_RDS_COST_AND_USAGE" in t2_resp, "Turn 2 failed: AWS_RDS_COST_AND_USAGE not in response"
        assert "AWS_CUR" in t2_resp, "Turn 2 failed: AWS_CUR not in response"
        assert "EC2 Spend Analysis" not in t2_resp, "Turn 2 failed: EC2 cached table falsely returned!"

        # Turn 3: Pie chart reformat (verifying legitimate reformat operates on immediate prior turn)
        t3_msgs = t2_msgs + [
            {"role": "assistant", "content": t2_resp},
            {"role": "user", "content": "now show that as a pie chart"}
        ]
        t3_resp = ai.generate(t3_msgs, mcp=mcp)
        assert "Pie Chart View" in t3_resp, "Turn 3 failed: Pie Chart View not in response"
        assert "RDS Spend Analysis" in t3_resp, "Turn 3 failed: RDS not preserved in reformat"

        # Default 30-day trend check
        t4_resp = ai.generate([{"role": "user", "content": "show rds usage for Lundbeck"}], mcp=mcp)
        assert "Last 30 Days Trend" in t4_resp, f"Default 30 days failed: {t4_resp[:200]}"

        # Multi-cloud 30-day trend check without double parentheses
        t5_resp = ai.generate([{"role": "user", "content": "show top services across all clouds"}], mcp=mcp)
        assert "Last 30 Days Trend" in t5_resp, f"Multi-cloud 30 days failed: {t5_resp[:200]}"
        assert "((Last Month" not in t5_resp and "(Last Month" not in t5_resp, "Double parentheses in title!"

        # Turn 6: Multi-Cloud Spend across AWS, Azure, GCP, and OCI this month
        t6_resp = ai.generate([{"role": "user", "content": "Show our top cloud services across AWS, Azure, GCP, and OCI this month"}], mcp=mcp)
        assert "Multi-Cloud Spend Analysis" in t6_resp or "Top Services Across All Clouds" in t6_resp, f"Turn 6 failed: {t6_resp[:200]}"
        assert "list_standard_datasources" not in t6_resp, "Turn 6 failed: pseudocode leaked!"
        assert "AWS" in t6_resp and "Azure" in t6_resp, "Turn 6 failed: providers missing"

        # Turn 7: Real-time 15-day RDS Usage breakdown by engine type
        t7_resp = ai.generate([{"role": "user", "content": "Give me the last 15days for RDS Usage and break it down by engine type"}], mcp=mcp)
        assert "Last 15 Days Trend" in t7_resp, f"Turn 7 failed: timeframe missing: {t7_resp[:200]}"
        assert "RDS Database Engine Distribution" in t7_resp, f"Turn 7 failed: engine table missing: {t7_resp[:200]}"
        assert "2026-09-09)" not in t7_resp, f"Turn 7 failed: truncated at day 5: {t7_resp[:300]}"

        mt_dur = round(time.time() - mt_start, 2)
        print(f"✅ Multi-Turn LLM-First Intent & Timeframe Regression: 7/7 PASSED ({mt_dur}s)")
    except Exception as ex:
        print(f"❌ Multi-Turn LLM-First Intent Regression FAILED: {ex}")
        failed += 1

    return {
        "engine": engine,
        "total": len(test_slice),
        "passed": passed,
        "failed": failed,
        "pass_pct": pass_pct,
        "duration_sec": duration,
        "details": results
    }


if __name__ == "__main__":
    engine_arg = sys.argv[1] if len(sys.argv) > 1 else "direct"
    run_suite(engine=engine_arg, verbose=False)
