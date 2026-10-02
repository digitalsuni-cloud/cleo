---
name: finops-lever-execution-guides
fcp_domain: "Optimize Usage & Cost"
fcp_capability: "Usage Optimization & Strategic Modernization"
fcp_phases: ["Optimize", "Operate"]
fcp_personas_primary: ["FinOps Practitioner", "Cloud Engineering", "DevOps"]
---

# FinOps Optimization Levers: Implementation & Execution Blueprints

This reference provides end-to-end implementation playbooks for the four primary multi-cloud cost optimization levers recommended by Cleo. Grounded in the **FinOps Lifecycle (Inform → Optimize → Operate)**, each blueprint defines the phased rollout, CLI commands, IaC specifications, risk mitigation, and validation criteria.

---

## 1. Multi-Cloud Storage Modernization & Waste Elimination

### Overview & Financial Impact
- **FinOps Classification**: Quick Win / Tactical Modernization
- **Expected Savings**: 20% to 35% on baseline block and object storage spend.
- **Payback & Break-Even Horizon**: **Immediate / Day 1 (0–14 days)** (< 1 month), > 1,000% instantaneous ROI.
- **Primary Targets**:
  1. AWS EBS gp2 volumes $\rightarrow$ gp3
  2. Azure orphaned/unattached Managed Disks & stale snapshots
  3. Multi-Cloud Object Storage lifecycle tiering (AWS S3 Intelligent-Tiering, Azure Blob Cool/Archive, GCP Nearline/Coldline)

### Phased Execution Roadmap

#### Phase 1: AWS EBS gp2 to gp3 Conversion
- **Zero-Downtime Guarantee**: EBS Elastic Volumes allows live modification while attached and serving I/O.
- **Performance Baseline**: gp3 delivers 3,000 IOPS and 125 MB/s throughput baseline at 20% lower cost per GB.
- **Execution Steps**:
  1. **Identify gp2 Volumes**:
     ```bash
     aws ec2 describe-volumes \
       --filters "Name=volume-type,Values=gp2" \
       --query "Volumes[].{ID:VolumeId,Size:Size,AZ:AvailabilityZone,State:State}" \
       --output table
     ```
  2. **Convert Volumes $\le$ 1,000 GiB**:
     ```bash
     aws ec2 modify-volume --volume-id vol-0123456789abcdef0 --volume-type gp3
     ```
  3. **Convert Volumes $>$ 1,000 GiB**:
     If current gp2 baseline exceeds 3,000 IOPS (3 IOPS per GiB), provision matching IOPS and throughput:
     ```bash
     aws ec2 modify-volume --volume-id vol-0123456789abcdef0 --volume-type gp3 --iops 4500 --throughput 250
     ```
  4. **IaC Source Fix**:
     Update Terraform modules or CloudFormation templates to prevent drift:
     ```hcl
     # terraform/modules/storage/main.tf
     resource "aws_ebs_volume" "app_data" {
       availability_zone = "us-east-1a"
       size              = 100
       type              = "gp3" # Upgraded from gp2
       # baseline 3000 IOPS and 125 MB/s included at zero extra cost
     }
     ```

#### Phase 2: Azure Orphaned Disks & Snapshot Pruning
- **Identify Unattached Managed Disks**:
  ```bash
  az disk list --query "[?managedBy==null].{Name:name,ResourceGroup:resourceGroup,DiskSizeGb:diskSizeGb,Tier:sku.tier}" --output table
  ```
- **Remediation**:
  Create final snapshot if retention required, then delete unattached disks:
  ```bash
  az snapshot create --resource-group rg-prod --name snap-cleanup-vol1 --source /subscriptions/.../disks/vol1
  az disk delete --resource-group rg-prod --name vol1 --yes
  ```

#### Phase 3: Object Storage Lifecycle Tiering & Abort Incomplete Uploads
- **AWS S3 Lifecycle Configuration**:
  Add rules for Intelligent-Tiering and aborting incomplete multipart uploads:
  ```json
  {
    "Rules": [
      {
        "ID": "Auto-Intelligent-Tiering-And-Abort",
        "Status": "Enabled",
        "Filter": {},
        "Transitions": [
          { "Days": 0, "StorageClass": "INTELLIGENT_TIERING" }
        ],
        "AbortIncompleteMultipartUpload": {
          "DaysAfterInitiation": 7
        }
      }
    ]
  }
  ```
  Apply with:
  ```bash
  aws s3api put-bucket-lifecycle-configuration --bucket my-app-bucket --lifecycle-configuration file://lifecycle.json
  ```
- **Azure Blob Storage**: Enable Azure Blob Lifecycle Management rule to move blobs from Hot to Cool after 30 days and Archive after 90 days.
- **GCP Cloud Storage**: Set bucket lifecycle condition `age: 30` to transition to `NEARLINE` and `age: 90` to `COLDLINE`.

#### Phase 4: Operational Guardrails & Policy Enforcement
- Deploy AWS Config Rule `ebs-volume-gp3-check` with auto-remediation.
- Apply Azure Policy denying the creation of unapproved disk types or unattached disks without TTL tags.

---

## 2. Commercial Database Modernization & Licensing Rightsizing

### Overview & Financial Impact
- **FinOps Classification**: Strategic Modernization (Architectural)
- **Expected Savings**: 30% to 50% on database compute and software license fees.
- **Payback & Break-Even Horizon**: **90–180 days (3–6 months)**, 180% – 300% Net ROI.
- **Primary Targets**: Proprietary commercial engines (Oracle EE, Microsoft SQL Server) on AWS RDS/EC2/Azure VMs $\rightarrow$ Open-source compatible engines (Amazon Aurora PostgreSQL / MySQL, Azure Database for PostgreSQL Flexible Server).

### Phased Execution Roadmap

#### Phase 1: Database Assessment & Workload Profiling
1. Run AWS Schema Conversion Tool (SCT) to evaluate database schemas, stored procedures, and triggers for PostgreSQL/MySQL compatibility.
2. Review Azure Hybrid Benefit (AHB) coverage for existing Microsoft SQL Server workloads to confirm license mobility.

#### Phase 2: Schema & Code Migration
1. Automate DDL conversion using AWS SCT.
2. Refactor proprietary SQL extensions (PL/SQL or T-SQL specific procedural code) into standard ANSI SQL / PL/pgSQL.
3. Establish data replication using AWS Database Migration Service (DMS) or Azure Data Factory with Change Data Capture (CDC).

#### Phase 3: Staging, Load Testing & Rightsizing
1. Deploy target database on Amazon Aurora PostgreSQL Serverless v2 with auto-scaling ACU ranges:
   ```hcl
   resource "aws_rds_cluster" "aurora_cluster" {
     cluster_identifier = "aurora-pg-cluster"
     engine             = "aurora-postgresql"
     engine_mode        = "provisioned"
     serverlessv2_scaling_configuration {
       min_capacity = 0.5
       max_capacity = 16.0
     }
   }
   ```
2. Validate transaction throughput, P95/P99 latency, and failover behavior under production-equivalent simulated load.

#### Phase 4: Production Cutover & License Decommissioning
1. Quiesce write traffic, complete final DMS replication catchup, update DNS/application connection strings.
2. Verify application health and decommission legacy commercial database instances.
3. Notify Finance to terminate third-party commercial license contracts or release Software Assurance cores.

---

## 3. Architecture & Silicon Modernization (ARM64/Graviton)

### Overview & Financial Impact
- **FinOps Classification**: Strategic Modernization (Platform)
- **Expected Savings**: 18% to 25% price-performance gain over equivalent x86 instances.
- **Payback & Break-Even Horizon**: **90–150 days (3–5 months)**, 150% – 250% Net ROI.
- **Primary Targets**: Stateless microservices, caching tiers (Redis/ElastiCache), containerized workloads (EKS, ECS, AKS, GKE), and build agents.

### Phased Execution Roadmap

#### Phase 1: Runtime & Dependency Audit
1. Audit language and runtime compatibility: Python, Go, Node.js, Java, .NET Core, and Rust have native multi-arch support.
2. Compile and publish multi-arch container images in CI/CD pipeline using Docker Buildx:
   ```bash
   docker buildx build --platform linux/amd64,linux/arm64 -t myrepo/app:latest --push .
   ```

#### Phase 2: Non-Production Canary Testing
1. Provision dev/staging node groups using Graviton instances (`m7g.large`, `c7g.large`, `r7g.large` on AWS; `Standard_Dpsv5` on Azure; `t2a` on GCP).
2. Execute regression test suite, integration tests, and performance benchmarks.

#### Phase 3: Staged Production Rollout
1. Roll out using weighted canary deployments or blue/green node pools in Kubernetes:
   ```yaml
   apiVersion: apps/v1
   kind: Deployment
   metadata:
     name: api-service
   spec:
     template:
       spec:
         affinity:
           nodeAffinity:
             preferredDuringSchedulingIgnoredDuringExecution:
             - weight: 100
               preference:
                 matchExpressions:
                 - key: kubernetes.io/arch
                   operator: In
                   values:
                   - arm64
   ```
2. Monitor latency, error rates, and CPU utilization for 14 days before cutting over 100% of production traffic.

#### Phase 4: IaC Updates & Residual Tracking
1. Update Terraform definitions across all environments.
2. Track percentage of compute spend on ARM64 as a FinOps efficiency metric.

---

## 4. AI Token Economics & Batch Inference Optimization

### Overview & Financial Impact
- **FinOps Classification**: Strategic / AI Efficiency Modernization
- **Expected Savings**: 35% to 50% on GenAI / LLM API spend.
- **Payback & Break-Even Horizon**: **Immediate (< 14 days)**, > 800% Net ROI.
- **Primary Targets**: Foundation model calls (Anthropic Claude, Azure OpenAI, Amazon Bedrock, Google Vertex AI).

### Phased Execution Roadmap

#### Phase 1: Enable Prompt Caching
- **Mechanism**: Leverage prefix prompt caching on Anthropic Claude (cache read input tokens at 10% of base price) and Azure OpenAI / Gemini context caching.
- **Implementation**: Place static system instructions, documentation, and RAG schemas before dynamic user messages.
- **Financial Return**: Immediate 90% reduction on cached prompt tokens for conversational agents and repeated workflows.

#### Phase 2: Route Asynchronous Workloads to Batch Inference APIs
- **Mechanism**: Separate real-time user-facing latency-critical calls from asynchronous tasks (summarization, document extraction, embeddings, backfills).
- **Implementation**:
  - Route non-interactive traffic to Anthropic Message Batches API, OpenAI Batch API, or Bedrock Batch Inference.
  - Automatically receive a flat **50% discount** with a 24-hour SLA.

#### Phase 3: Model Rightsizing & Hierarchical Fallbacks
- **Architecture**:
  1. Route classification, routing, and simple extraction tasks to lightweight models (Claude 3.5 Haiku, Gemini 1.5 Flash, GPT-4o-mini).
  2. Reserve high-parameter models (Claude 3.7 Sonnet, GPT-4o) exclusively for complex multi-step reasoning, architectural synthesis, and code generation.

#### Phase 4: Agentic Loop & Telemetry Guardrails
- Establish token rate limiters, maximum turn caps (e.g. max 15 tool iterations), and spend circuit-breakers to eliminate runaway agent loops.
- Track **Cost per Completed Task** as the primary FinOps unit metric for GenAI services.
