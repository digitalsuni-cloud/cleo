"""
cleo_finops_refs.py — Dynamic FinOps knowledge injection and advisory engine for Cleo.

Reads from the OptimNow cloud-finops skill references and playbooks:
  - Injects relevant context for LLM generation (keeping prompts lean).
  - Provides direct, structured advisory responses for the Direct FinOps Router.
"""
import os
import re
from pathlib import Path
from functools import lru_cache
from typing import Optional

BASE_DIR = Path(__file__).parent
REFS_DIR = BASE_DIR / ".agents/skills/cloud-finops/references"
PLAYBOOKS_DIR = BASE_DIR / ".agents/skills/cloud-finops/playbooks"

# ── Routing table: (target_file_key, [trigger_keywords]) ───────────────────────
# Keys starting with "playbook:" resolve in PLAYBOOKS_DIR.
# Other keys resolve in REFS_DIR.
ROUTING: list[tuple[str, list[str]]] = [
    # ── High-frequency named playbooks (Exact Pattern Matching) ───────────────
    ("playbook:aws-gp2-to-gp3",                 ["gp2 to gp3", "gp2-to-gp3", "gp2", "gp3", "volume usage gp2", "ebs volume"]),
    ("playbook:aws-zombie-nat-gateway",         ["zombie nat", "zombie nat gateway", "idle nat", "nat gateway cost", "nat gateway idle"]),
    ("playbook:aws-nat-gateway-endpoint-substitution", ["gateway endpoint", "nat gateway data", "s3 nat gateway", "endpoint substitution", "nat to gateway"]),
    ("playbook:aws-graviton-candidate",         ["graviton", "arm64", "c7g", "m7g", "r7g", "t4g", "graviton candidate", "x86 to arm"]),
    ("playbook:aws-orphaned-ebs-volumes",       ["orphaned ebs", "unattached ebs", "available ebs", "unattached volume", "orphan volume", "orphan ebs"]),
    ("playbook:aws-snapshot-sprawl",            ["snapshot sprawl", "ebs snapshot", "old snapshot", "prune snapshot"]),
    ("playbook:aws-s3-incomplete-multipart-uploads", ["incomplete multipart", "multipart upload", "abort multipart", "abortincomplete"]),
    ("playbook:aws-s3-noncurrent-version-sprawl", ["noncurrent version", "version sprawl", "s3 versioning cost", "noncurrentversion"]),
    ("playbook:aws-s3-cold-data-in-standard",   ["cold data", "s3 cold", "s3 standard to", "intelligent-tiering", "glacier lifecycle"]),
    ("playbook:aws-idle-load-balancer",         ["idle load balancer", "load balancer", "alb", "nlb", "idle alb", "idle nlb", "unused load balancer", "underutilized load balancer", "application load balancer"]),
    ("playbook:aws-cross-az-egress",            ["cross-az", "cross az", "availability zone egress", "az chatterbox", "cross-availability"]),
    ("playbook:aws-oversized-rds",              ["oversized rds", "rds rightsiz", "database rightsiz", "rds cpu", "rds overprovisioned", "rds instance class", "rightsizing levers for rds", "rightsizing rds", "rightsize rds", "rds databases", "rds database"]),
    ("playbook:aws-sagemaker-idle-endpoint",    ["sagemaker idle", "sagemaker endpoint", "idle endpoint", "endpoint sprawl"]),
    ("playbook:aws-sagemaker-notebook-always-on", ["sagemaker notebook", "always-on notebook", "notebook instance", "auto-stop notebook"]),
    ("playbook:aws-gpu-for-cpu-bound-workload", ["gpu for cpu", "cpu-bound workload", "gpu instance for a cpu", "cpu bound"]),
    ("playbook:aws-gpu-instance-oversized",     ["oversized gpu", "gpu oversized", "tensor core underutilized", "gpu rightsizing"]),
    ("playbook:aws-multi-gpu-underutilized",    ["multi-gpu", "single-gpu workload", "underutilized gpu"]),
    ("playbook:aws-outdated-gpu-generation",    ["outdated gpu", "k80", "v100 to", "a100 to", "gpu generation"]),
    ("playbook:aws-expiring-commitment-no-decision", ["expiring commitment", "commitment no decision", "commitment cliff", "renewal decision"]),

    # Azure Playbooks
    ("playbook:azure-log-analytics-sprawl",     ["log analytics", "log ingestion", "workspace ingestion", "log analytics cost", "retention data"]),
    ("playbook:azure-orphan-disks",             ["orphan disk", "orphaned disk", "unattached disk", "managed disk", "managed disks", "orphan disks", "orphaned disks", "unattached managed"]),
    ("playbook:azure-idle-vm",                  ["stopped vs deallocated", "deallocated", "idle vm", "stopped vm", "azure idle"]),
    ("playbook:azure-orphaned-public-ips-and-nics", ["public ip", "public ips", "network interface", "network interfaces", "orphaned public", "orphaned nic", "unassociated ip", "unused public ip"]),
    ("playbook:azure-snapshot-sprawl",          ["azure snapshot", "managed disk snapshot"]),
    ("playbook:azure-unused-reservation",       ["unused reservation", "unused azure reservation", "reservation refund"]),
    ("playbook:azure-app-service-overprovisioned", ["app service plan", "app service overprovisioned", "app service scale"]),
    ("playbook:azure-idle-sql-database",        ["idle sql", "idle azure sql", "sql database pause"]),

    # GCP Playbooks
    ("playbook:gcp-orphan-persistent-disks",    ["persistent disk", "persistent disks", "orphan persistent disk", "unattached persistent disk", "orphan disk gcp", "orphan persistent"]),
    ("playbook:gcp-cud-mismatch",               ["cud mismatch", "resource-based cud", "cud machine family"]),
    ("playbook:gcp-idle-gke-autopilot",         ["idle gke", "gke autopilot idle", "autopilot cluster"]),
    ("playbook:gcp-cloud-functions-cold-starts", ["cloud functions cold", "functions min instances", "cold start"]),

    # Cross-Cloud Playbooks
    ("playbook:cross-cloud-agent-loop-burn",    ["agent loop", "agent-loop", "loop burn", "flat-line burn", "infinite loop agent"]),
    ("playbook:cross-cloud-coding-agent-token-waste", ["coding agent", "coding tool", "claude code cost", "cursor cost", "copilot cost", "windsurf cost"]),
    ("playbook:cross-cloud-schedule-blindness", ["schedule blindness", "non-production 24/7", "non-prod schedule", "weekend shutdown", "auto stop"]),
    ("playbook:cross-cloud-untagged-spend-drift", ["untagged spend", "untagged drift", "tag drift", "unallocated spend drift"]),

    # ── AI / LLM Inference & Economics References ─────────────────────────────
    ("finops-bedrock",              ["bedrock", "aws bedrock", "sagemaker", "foundation model", "model unit", "inference profile"]),
    ("finops-azure-openai",         ["azure openai", "aoai", "gpt-4o", "gpt-5", "ptu", "provisioned throughput units", "openai service"]),
    ("finops-vertexai",             ["vertex ai", "vertex", "vertexai", "google vertex", "gemini pricing", "gemini models", "gemini", "vertex batch", "google ai studio", "vertex provisioned throughput"]),
    ("finops-anthropic",            ["anthropic", "claude api", "opus", "haiku", "batch api", "50% discount", "prompt caching"]),
    ("finops-for-ai",               ["llm inference", "token economics", "gpu cost", "ai cost", "ai roi", "ai allocation", "gpu utilization", "dcgm", "tensor core", "memory bandwidth", "rag harness", "genai cost"]),
    ("finops-agentic",              ["agentic finops", "agent wallet", "cost per completed task", "cost per task", "agent payment", "x402", "mpp"]),
    ("finops-genai-capacity",       ["genai capacity", "provisioned vs shared", "throughput units", "traffic shape", "spillover", "capacity planning"]),
    ("finops-ai-self-hosted-vs-managed", ["self-hosted", "vllm", "sglang", "llama.cpp", "gpu rental", "runpod", "coreweave", "lambda labs", "build vs buy llm", "hybrid routing", "litellm", "portkey"]),
    ("finops-open-weight-vendors",  ["deepseek", "qwen", "qwen api", "kimi", "moonshot", "glm", "z.ai", "open-weight", "chinese model", "peak and off-peak"]),
    ("finops-ai-dev-tools",         ["ai coding tool", "dev tool finops", "byok coding", "github copilot", "cursor"]),
    ("finops-ai-value-management",  ["ai investment", "ai business case", "ai value", "stage gate", "incremental funding", "realisation rate", "total cost of ai", "tca", "labour claim"]),

    # ── AWS Core References ───────────────────────────────────────────────────
    ("finops-aws-commitments",      ["savings plan", "reserved instance", "compute savings plan", "ec2 instance savings plan", "convertible ri", "standard ri", "break-even", "edp negotiation", "commitment portfolio", "peak compute", "peak usage", "spot diversification", "spot instance"]),
    ("finops-aws-patterns",         ["aws pattern", "aws optimis", "aws rightsiz", "aurora acu", "aurora serverless", "commercial database licensing"]),
    ("finops-aws",                  ["aws billing", "aws cur", "cost explorer", "ec2 rightsiz", "s3 lifecycle", "cloudfront", "billing conductor", "cost categories", "aws governance"]),

    # ── Azure Core References ─────────────────────────────────────────────────
    ("finops-azure-commitments",    ["azure reservation", "azure savings plan", "azure hybrid benefit", "ahb", "azure spot", "macc", "ri exchange", "2027 exchange"]),
    ("finops-azure-patterns",       ["azure pattern", "azure optimis", "azure rightsiz", "aks cost", "azure storage tier", "hot to cool", "archive tier"]),
    ("finops-azure",                ["azure cost", "azure billing", "cost management export", "azure advisor", "azure policy", "ea-to-mca", "mca transition", "p95", "rehydration", "early deletion", "archive storage", "aks node pool", "azure kubernetes service", "aks system", "aks user"]),

    # ── GCP Core References ───────────────────────────────────────────────────
    ("finops-gcp",                  ["gcp", "google cloud", "bigquery", "bigquery billing", "bigquery slot", "slot reservation", "on-demand analysis", "partition table", "clustering", "cloud sql", "gcs", "sustained use discount", "sud", "committed use discount", "cud", "cloud carbon footprint", "gcs lifecycle", "coldline", "archive storage gcp", "google cloud storage"]),

    # ── OCI References ────────────────────────────────────────────────────────
    ("finops-oci",                  ["oci compute", "oci storage", "oracle cloud", "universal credits", "oci cost report", "oci budget"]),

    # ── Kubernetes References ─────────────────────────────────────────────────
    ("finops-kubernetes",           ["kubernetes", "k8s", "opencost", "kubecost", "gke cost", "eks split cost", "aks cost analysis", "karpenter", "cluster autoscaler", "vpa", "p95", "p99", "pod disruption", "pdb", "node efficiency", "shared daemonset"]),

    # ── Data Platforms References ─────────────────────────────────────────────
    ("finops-databricks",           ["databricks", "dbu", "spark optimis", "unity catalog", "dbcu", "photon", "photon multiplier", "serverless compute premium"]),
    ("finops-fabric",               ["microsoft fabric", "fabric", "f-sku", "capacity unit", "cu smoothing", "fabric throttling", "power bi premium"]),
    ("finops-snowflake",            ["snowflake", "auto-suspend", "warehouse credit", "snowflake optimis", "query_attribution_history", "cortex governance"]),

    # ── FinOps Practice & Operating Model References ──────────────────────────
    ("finops-framework",            ["finops framework", "finops foundation", "finops capability", "finops maturity", "crawl walk run", "crawl, walk, and run", "crawl-to-walk", "inform optimize operate", "inform, optimize, operate", "principles", "finops lifecycle", "core phases", "three phases", "three core phases", "lifecycle"]),
    ("finops-allocation-showback",  ["effectivecost", "billedcost", "effectivecost vs billedcost", "effectivecost and billedcost", "blended cost", "blended rate", "blended cost trap", "showback", "amortised", "unblended cost", "shared services allocation", "defensible allocation", "unallocated spend"]),
    ("finops-chargeback",           ["hard chargeback", "chargeback", "financial accountability", "erp readiness", "sap co", "transfer pricing", "intercompany recharge", "sox controls"]),
    ("finops-kpis-benchmarking",    ["finops kpi", "unit economics", "business denominator", "cost per customer", "forecast variance", "benchmarking", "executive reporting", "cfo narrative", "maturity scorecard", "realised savings", "potential savings"]),
    ("finops-anomaly-management",   ["anomaly detection", "cost anomaly", "cost spike", "masked anomaly", "threshold tuning", "alert fatigue", "budget anomaly", "unusual spend"]),
    ("finops-tagging",              ["tagging strategy", "mandatory tagging", "naming convention", "tag enforcement", "iac tag", "mcp governance", "tag compliance", "intake migration"]),
    ("finops-sam",                  ["saas management", "licence optimis", "shadow it", "saas sprawl", "renewal governance"]),
    ("finops-itam",                 ["itam", "byol", "bring your own license", "licence compliance", "vendor negotiation", "entitlement management", "marketplace channel", "software asset management"]),
    ("finops-onboarding-workloads", ["onboarding workload", "migration cost", "intake gate", "double-bubble", "ma integration", "post-migration finops"]),
    ("finops-waste-detection-playbooks", ["waste detection", "waste playbook", "two-signal classification", "classification confidence"]),
    ("greenops-cloud-carbon",       ["greenops", "cloud carbon", "scope 2", "scope 3", "carbon-aware", "sustainability", "csrd"]),
    ("optimnow-methodology",        ["optimnow", "diagnose before prescribing", "four pillars", "finops strategy design", "engagement design", "practice positioning"]),
]

_MAX_CHARS = int(os.environ.get("CLEO_REF_MAX_CHARS", "12000"))
_MAX_REFS = int(os.environ.get("CLEO_REF_MAX_REFS", "2"))


@lru_cache(maxsize=128)
def _read_target(key: str) -> str:
    """Read and cache a reference or playbook file, stripping YAML frontmatter if present."""
    if key.startswith("playbook:"):
        slug = key.split(":", 1)[1]
        path = PLAYBOOKS_DIR / f"{slug}.md"
    else:
        path = REFS_DIR / f"{key}.md"

    if not path.exists():
        return ""

    text = path.read_text(encoding="utf-8")
    # Clean YAML frontmatter
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            text = parts[2].strip()

    return text


def _find_matches(query: str, max_matches: int = _MAX_REFS) -> list[str]:
    """Finds matching reference/playbook keys scored by specificity and keyword length."""
    low = query.lower()
    scored = []

    for key, keywords in ROUTING:
        score = 0
        matched_any = False
        for kw in keywords:
            if kw in low:
                matched_any = True
                # Multi-word phrase matches get heavy weighting over generic single words
                weight = len(kw) * 3 if " " in kw else len(kw)
                # Extra boost for named playbooks
                if key.startswith("playbook:"):
                    weight += 5
                score += weight

        if matched_any:
            scored.append((score, key))

    # Sort by highest score first
    scored.sort(key=lambda x: x[0], reverse=True)
    return [k for _, k in scored[:max_matches]]


def get_finops_context(query: str) -> str:
    """
    Returns a formatted FinOps reference block to inject into the LLM system prompt.
    """
    matched = _find_matches(query, max_matches=_MAX_REFS)
    if not matched:
        return ""

    parts = []
    for key in matched:
        content = _read_target(key)
        if content:
            if key.startswith("playbook:"):
                slug = key.split(":", 1)[1]
                label = f"Playbook: {slug.replace('-', ' ').title()}"
            else:
                label = key.replace("finops-", "").replace("greenops-", "GreenOps: ").replace("-", " ").title()

            if len(content) > _MAX_CHARS:
                cut = content.rfind("\n\n", 0, _MAX_CHARS)
                content = content[:cut if cut > 0 else _MAX_CHARS] + "\n\n*[Reference truncated]*"

            parts.append(f"### FinOps Expert Reference: {label}\n\n{content}")

    if not parts:
        return ""

    return (
        "\n\n---\n"
        "## OPTIMNOW FINOPS EXPERT KNOWLEDGE\n"
        "The following authoritative reference(s) were loaded for this query. "
        "Apply their guidance precisely. Diagnose before prescribing. Connect cost to value. "
        "Recommend progressively (quick wins first, structural changes second).\n\n"
        + "\n\n---\n\n".join(parts)
        + "\n\n---"
    )


_ADVISORY_STOPWORDS = {
    "what", "when", "where", "how", "who", "why", "will", "can", "does", "did",
    "the", "and", "for", "that", "this", "with", "from", "have", "are",
    "was", "were", "show", "tell", "give", "get", "use", "used", "about"
}

def _extract_best_sections(text: str, query: str, max_chars: int = 3500) -> str:
    """
    Extracts the most relevant markdown sections (by heading or named pattern) for a query.
    """
    low_tokens = [w for w in re.findall(r"[a-z0-9]{3,}", query.lower()) if w not in _ADVISORY_STOPWORDS]
    bigrams = [f"{low_tokens[i]} {low_tokens[i+1]}" for i in range(len(low_tokens)-1)] if len(low_tokens) > 1 else []
    
    # Split text into coherent sections: if document has abundant H2 headers (>=4), keep H3 subsections together
    h2_count = len(re.findall(r"^## [^#]", text, re.MULTILINE))
    split_pattern = (
        r"\n(?=## [^#]|\*\*[A-Z][a-zA-Z0-9 /()-]+\*\*\nService:)"
        if h2_count >= 4
        else r"\n(?=###? [^#]|\*\*[A-Z][a-zA-Z0-9 /()-]+\*\*\nService:)"
    )
    sections = re.split(split_pattern, text)
    if not sections or len(sections) == 1:
        return text[:max_chars]

    scored = []
    for idx, sec in enumerate(sections):
        lines = sec.strip().split("\n")
        header = lines[0].lower() if lines else ""
        sec_low = sec.lower()
        score = 0

        # Exact bigram matches get major bonus
        for bg in bigrams:
            if bg in sec_low:
                score += 12
            if bg in header:
                score += 15

        # Distinct keyword match scoring
        matched_tokens = set()
        for w in low_tokens:
            stem = w.rstrip("s").rstrip("ing").rstrip("ed")
            if len(stem) >= 3 and (stem in sec_low or w in sec_low):
                matched_tokens.add(w)
                score += 1
            if len(stem) >= 3 and (stem in header or w in header):
                score += 8

        # Heavily reward sections that match multiple distinct query terms
        score += (len(matched_tokens) ** 2) * 5

        # Bonus for core diagnostic sections
        if any(h in header for h in ["detection", "fix", "problem", "decision", "comparison", "overview", "metric", "focus"]):
            score += 2

        # Hyphenated terms & exact phrases (e.g. break-even, auto-suspend, 1-year, anti-pattern)
        for hw in re.findall(r"[a-z0-9]+-[a-z0-9]+", query.lower()):
            if hw in sec_low or hw.replace("-", " ") in sec_low:
                score += 15

        scored.append((score, idx, sec))

    scored.sort(key=lambda x: x[0], reverse=True)
    # Pick top 2 highest scoring sections
    chosen_indices = [idx for _, idx, _ in scored[:2] if scored[0][0] > 0]
    if not chosen_indices:
        chosen_indices = [0]

    # Slice each chosen section around best match
    sliced_parts = []
    per_sec_limit = max_chars if len(chosen_indices) == 1 else max_chars // len(chosen_indices)
    for i in chosen_indices:
        sec = sections[i].strip()
        if len(sec) > per_sec_limit:
            # Find best match position favoring the most specific (lowest-frequency) query token
            best_pos = 0
            sec_low = sec.lower()
            candidates = []
            for bg in bigrams:
                m = re.search(r"\b" + re.escape(bg) + r"\b", sec_low)
                if m:
                    candidates.append((-10, m.start(), bg))
            for w in low_tokens:
                m = re.search(r"\b" + re.escape(w), sec_low)
                if m:
                    cnt = len(re.findall(r"\b" + re.escape(w), sec_low))
                    candidates.append((cnt, m.start(), w))
            for hw in re.findall(r"[a-z0-9]+-[a-z0-9]+", query.lower()):
                m = re.search(r"\b" + re.escape(hw), sec_low)
                if not m:
                    m = re.search(r"\b" + re.escape(hw.replace("-", " ")), sec_low)
                if m:
                    # Hyphenated / compound terms are exceptionally specific
                    candidates.append((-5, m.start(), hw))
            if candidates:
                candidates.sort(key=lambda x: x[0])
                best_pos = candidates[0][1]
            if best_pos > per_sec_limit // 2:
                start = sec.rfind("\n\n", 0, max(0, best_pos - 300))
                if start == -1:
                    start = sec.rfind("\n", 0, max(0, best_pos - 300))
                start = max(0, start)
                sec = sec[start:].strip()
            if len(sec) > per_sec_limit:
                cut = sec.rfind("\n\n", 0, per_sec_limit)
                if cut < per_sec_limit // 2:
                    cut = sec.rfind("\n", 0, per_sec_limit)
                sec = sec[:cut if cut > 0 else per_sec_limit].strip()
        sliced_parts.append(sec)

    extracted = "\n\n".join(sliced_parts)
    if len(extracted) > max_chars:
        cut = extracted.rfind("\n\n", 0, max_chars)
        if cut < max_chars // 2:
            cut = extracted.rfind("\n", 0, max_chars)
        extracted = extracted[:cut if cut > 0 else max_chars] + "\n\n*(Section truncated)*"
    return extracted


def get_finops_advisory(query: str) -> Optional[str]:
    """
    Direct advisory engine for Cleo.
    Synthesizes and returns an authoritative, structured response from the
    matched OptimNow playbook or reference when no external LLM is configured.
    """
    matched = _find_matches(query, max_matches=1)
    if not matched:
        return None

    target_key = matched[0]
    content = _read_target(target_key)
    if not content:
        return None

    # Check if this is a playbook
    if target_key.startswith("playbook:"):
        slug = target_key.split(":", 1)[1]
        title = slug.replace("-", " ").title()
        return (
            f"### 📋 OptimNow FinOps Playbook: {title}\n\n"
            f"{content}\n\n"
            f"---\n"
            f"*Source: Authoritative OptimNow Multi-Cloud FinOps Playbooks & Waste Detection Catalogue.*"
        )

    # Reference file: extract best matching sections
    label = target_key.replace("finops-", "").replace("greenops-", "GreenOps: ").replace("-", " ").title()
    body = _extract_best_sections(content, query)

    return (
        f"### 💡 OptimNow FinOps Expert Guidance: {label}\n\n"
        f"{body}\n\n"
        f"---\n"
        f"*Source: Authoritative OptimNow Multi-Cloud FinOps Framework & Engineering Doctrine.*"
    )
