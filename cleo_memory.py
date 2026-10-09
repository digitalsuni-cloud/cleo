"""
cleo_memory.py — Self-learning memory for Cleo.

Backed by a single ~/.cleo/memory.json file. No external dependencies.
Three record types:
  - sql_fix     : bad SQL + error + working fix (auto-logged on query failure)
  - correction  : user-supplied "what you should have done" (from 👎 feedback)
  - good_pattern: summary of a query that got a 👍 (positive reinforcement)

Retrieval: simple token-overlap scoring — good enough for FinOps domain vocab.
"""
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Optional

_MEMORY_PATH = Path(os.path.expanduser("~/.cleo/memory.json"))
_MAX_ENTRIES = 500        # cap to keep file lean
_TOP_K       = 4          # max entries injected per query
_MIN_SCORE   = 0.08       # minimum overlap score to bother injecting


def _tokenize(text: str) -> set[str]:
    """Extract lowercase alphanumeric tokens (2+ chars), keeping numbers, cloud acronyms, and hyphenated terms."""
    if not text:
        return set()
    tokens = set()
    for w in re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", text.lower()):
        if w not in _STOPWORDS and len(w) >= 2:
            tokens.add(w)
    return tokens

_STOPWORDS = {
    "the","and","for","that","this","with","from","have","are","was","were",
    "what","when","where","how","who","will","can","does","did","per","not",
    "its","you","your","our","their","show","tell","give","get","use","used",
    "about","into","onto","than","then","over","under"
}


def _score(query_tokens: set[str], entry_tokens: set[str]) -> float:
    if not entry_tokens or not query_tokens:
        return 0.0
    return len(query_tokens & entry_tokens) / len(entry_tokens | query_tokens)


class CleoMemory:
    def __init__(self, memory_path: Path = _MEMORY_PATH):
        self._path = Path(memory_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    def _sanitize_entries(self, entries: list[dict]) -> list[dict]:
        """Filters out malformed, empty, or transient error memory entries."""
        clean = []
        for e in entries:
            t = e.get("type")
            # Discard empty sql fixes or transient 403 / network errors
            if t == "sql_fix":
                if not e.get("good_sql") or "403" in e.get("error", "") or "Forbidden" in e.get("error", ""):
                    continue
            # Discard corrections that have neither query nor instruction
            if t == "correction":
                if not e.get("instruction") or (not e.get("query") and len(e.get("instruction", "")) < 5):
                    continue
            # Discard empty negative feedback
            if t == "negative_feedback":
                if not e.get("query") and not e.get("rejected"):
                    continue
            clean.append(e)
        return clean

    def _load(self):
        if self._path.exists():
            try:
                raw_data = json.loads(self._path.read_text("utf-8"))
                if isinstance(raw_data, list):
                    self._data = self._sanitize_entries(raw_data)
                else:
                    self._data = []
            except Exception:
                self._data = []
        else:
            self._data = []

    def _save(self):
        self._data = self._sanitize_entries(self._data)
        if len(self._data) > _MAX_ENTRIES:
            self._data = self._data[-_MAX_ENTRIES:]
        self._path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")

    def _add(self, entry: dict):
        entry["id"]  = str(uuid.uuid4())[:8]
        entry["ts"]  = int(time.time())
        self._data.append(entry)
        self._save()

    # ── Public write methods ──────────────────────────────────────────────────

    def record_sql_fix(self, bad_sql: str, error: str, good_sql: str):
        """Auto-called when a SQL query fails and a proven fix is verified."""
        if not good_sql or not bad_sql or "403" in error or "Forbidden" in error:
            return
        self._add({
            "type":     "sql_fix",
            "bad_sql":  bad_sql[:400],
            "error":    error[:200],
            "good_sql": good_sql[:400],
            "search_text": f"{bad_sql} {error} {good_sql}",
        })

    def record_correction(self, user_query: str, correction: str):
        """Called when user submits 👎 feedback with explicit correction text ('Teach Cleo')."""
        if not correction:
            return
        self._add({
            "type":        "correction",
            "query":       user_query[:300],
            "instruction": correction[:500],
            "search_text": f"{user_query} {correction}",
        })

    def record_negative_feedback(self, user_query: str, bad_response_summary: str):
        """Called when user submits 👎 feedback (capturing the rejected answer to avoid repetition)."""
        self._add({
            "type":        "negative_feedback",
            "query":       user_query[:300],
            "rejected":    bad_response_summary[:400],
            "search_text": f"{user_query} {bad_response_summary} avoid bad rejected do not repeat wrong",
        })

    def record_good_pattern(self, user_query: str, response_summary: str):
        """Called when user gives 👍 — stores the query→answer pattern."""
        self._add({
            "type":     "good_pattern",
            "query":    user_query[:300],
            "summary":  response_summary[:400],
            "search_text": f"{user_query} {response_summary}",
        })

    def record_rule(self, instruction: str, search_keywords: str = ""):
        """Stores a persistent operational or FinOps rule in memory."""
        self._add({
            "type":        "rule",
            "instruction": instruction[:600],
            "search_text": f"{instruction} {search_keywords} date today yesterday current date partial days daily trend spend cost usage billing rds ec2",
        })

    def delete(self, entry_id: str) -> bool:
        before = len(self._data)
        self._data = [e for e in self._data if e.get("id") != entry_id]
        if len(self._data) < before:
            self._save()
            return True
        return False

    def all_entries(self) -> list[dict]:
        return list(self._data)

    # ── Retrieval ─────────────────────────────────────────────────────────────

    def find_relevant(self, query: str, top_k: int = _TOP_K) -> list[dict]:
        """Return up to top_k entries most relevant to query by token overlap and semantic boost."""
        qtok = _tokenize(query)
        if not qtok:
            return []
        q_low = query.lower()
        scored = []
        for e in self._data:
            etok = _tokenize(e.get("search_text", ""))
            s = _score(qtok, etok)
            
            # Massive priority for exact query matching or query containment
            entry_q = (e.get("query") or "").lower().strip()
            if entry_q and (entry_q in q_low or q_low in entry_q):
                s += 2.0
            elif entry_q:
                q_words = set(entry_q.split())
                if len(q_words) >= 2 and len(q_words & set(q_low.split())) >= len(q_words) * 0.7:
                    s += 1.0

            # Exact SQL match priority
            entry_bad_sql = (e.get("bad_sql") or "").lower().strip()
            if entry_bad_sql and (entry_bad_sql in q_low or q_low in entry_bad_sql):
                s += 3.0

            if s > 0:
                if e.get("type") == "correction":
                    s += 0.4  # Explicit user corrections take precedence
                elif e.get("type") in ("rule", "policy"):
                    s += 0.5  # Permanent doctrines only when relevant
                elif e.get("type") == "negative_feedback":
                    s += 0.3  # Negative feedback warnings

            if s >= _MIN_SCORE:
                scored.append((s, e))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in scored[:top_k]]

    def get_active_corrections(self, query: str) -> list[dict]:
        """Returns active user corrections or negative feedback relevant to the query."""
        relevant = self.find_relevant(query, top_k=5)
        return [e for e in relevant if e.get("type") in ("correction", "negative_feedback")]

    def build_context_block(self, query: str) -> str:
        """
        Returns a formatted string ready to append to the system prompt,
        or empty string if nothing relevant (zero overhead for unmatched queries).
        """
        entries = self.find_relevant(query)
        if not entries:
            return ""

        parts = []
        for e in entries:
            t = e.get("type")
            if t == "sql_fix":
                parts.append(
                    f"[LEARNED SQL FIX] Avoid: `{e['bad_sql']}` (error: {e['error']}). "
                    f"Use instead: `{e['good_sql']}`"
                )
            elif t == "correction":
                q_str = f"\"{e['query']}\"" if e.get("query") else "this type of request"
                parts.append(
                    f"[CRITICAL USER CORRECTION — OVERRIDES DEFAULT LOGIC] When asked {q_str}, the user explicitly instructed: \"{e['instruction']}\". You MUST adhere to this instruction."
                )
            elif t == "negative_feedback":
                q_str = f"\"{e['query']}\"" if e.get("query") else "this request"
                parts.append(
                    f"[USER REJECTED PREVIOUS RESPONSE] For {q_str}, the user gave 👎 negative feedback to: \"{e['rejected']}\". AVOID this response format, do NOT repeat it, and provide a direct, actionable answer instead."
                )
            elif t == "good_pattern":
                parts.append(
                    f"[KNOWN GOOD PATTERN] For queries like \"{e['query']}\": {e['summary']}"
                )
            elif t in ("rule", "policy"):
                parts.append(
                    f"[PERMANENT FINOPS DOCTRINE] {e['instruction']}"
                )

        if not parts:
            return ""

        return (
            "\n\nCLEO LEARNED MEMORY (from user feedback & past usage — apply strictly):\n"
            + "\n".join(f"- {p}" for p in parts)
        )


# Singleton
_memory: Optional[CleoMemory] = None

def get_memory() -> CleoMemory:
    global _memory
    if _memory is None:
        _memory = CleoMemory()
    return _memory


# ── Cross-session continuation helpers ───────────────────────────────────────
_LAST_QUERY_PATH = Path(os.path.expanduser("~/.cleo/last_query.json"))


def save_last_query(query_type: str, dataset: str, sql: str, time_range: dict) -> None:
    """Persist the most recent successful query so new sessions can continue it."""
    try:
        _LAST_QUERY_PATH.parent.mkdir(parents=True, exist_ok=True)
        _LAST_QUERY_PATH.write_text(
            json.dumps({"query_type": query_type, "dataset": dataset, "sql": sql, "time_range": time_range, "ts": time.time()}, indent=2)
        )
    except Exception:
        pass  # non-critical


def load_last_query() -> dict:
    """Return the last saved query, or {} if missing / stale (>24h)."""
    try:
        data = json.loads(_LAST_QUERY_PATH.read_text())
        if time.time() - data.get("ts", 0) < 86400:  # 24-hour TTL
            return data
    except Exception:
        pass
    return {}


if __name__ == "__main__":
    # Self-check using isolated temp file to avoid altering production memory
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".json") as tf:
        test_path = Path(tf.name)
        m = CleoMemory(memory_path=test_path)
        m.record_correction("show top aws services", "Always include AmazonEC2 and AmazonRDS in the top services list")
        m.record_sql_fix("SELECT * FROM AWS_CUR", "FQ-USR-304", "SELECT lineItem_ProductCode AS svc, SUM(lineItem_UnblendedCost) AS cost FROM AWS_CUR GROUP BY svc ORDER BY cost DESC LIMIT 10")
        m.record_good_pattern("what is my bedrock spend", "Queried AWS_CUR filtering lineItem_ProductCode='AmazonBedrock', returned monthly breakdown with MoM variance")

        ctx = m.build_context_block("show top aws services by cost")
        assert "USER CORRECTION" in ctx, "Should find correction"
        ctx2 = m.build_context_block("SELECT * FROM AWS_CUR")
        assert "LEARNED SQL FIX" in ctx2, "Should find SQL fix"
        ctx3 = m.build_context_block("what is the weather today")
        assert ctx3 == "", "Unrelated query should return empty"

    print("All checks passed.")
