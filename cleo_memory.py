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
    """Lowercase word tokens, 3+ chars, strip punctuation."""
    return {w for w in re.findall(r"[a-z]{3,}", text.lower()) if w not in _STOPWORDS}

_STOPWORDS = {
    "the","and","for","that","this","with","from","have","are","was","were",
    "what","when","where","how","who","will","can","does","did","per","not",
    "its","you","your","our","their","show","tell","give","get","use","used",
}


def _score(query_tokens: set[str], entry_tokens: set[str]) -> float:
    if not entry_tokens:
        return 0.0
    return len(query_tokens & entry_tokens) / len(entry_tokens | query_tokens)


class CleoMemory:
    def __init__(self):
        _MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    def _load(self):
        if _MEMORY_PATH.exists():
            try:
                self._data: list[dict] = json.loads(_MEMORY_PATH.read_text("utf-8"))
            except Exception:
                self._data = []
        else:
            self._data = []

    def _save(self):
        # Trim oldest entries if over cap
        if len(self._data) > _MAX_ENTRIES:
            self._data = self._data[-_MAX_ENTRIES:]
        _MEMORY_PATH.write_text(json.dumps(self._data, indent=2), encoding="utf-8")

    def _add(self, entry: dict):
        entry["id"]  = str(uuid.uuid4())[:8]
        entry["ts"]  = int(time.time())
        self._data.append(entry)
        self._save()

    # ── Public write methods ──────────────────────────────────────────────────

    def record_sql_fix(self, bad_sql: str, error: str, good_sql: str):
        """Auto-called when a SQL query fails and a fix is found."""
        self._add({
            "type":     "sql_fix",
            "bad_sql":  bad_sql[:400],
            "error":    error[:200],
            "good_sql": good_sql[:400],
            "search_text": f"{bad_sql} {error}",
        })

    def record_correction(self, user_query: str, correction: str):
        """Called when user submits 👎 feedback with a correction text."""
        self._add({
            "type":        "correction",
            "query":       user_query[:300],
            "instruction": correction[:500],
            "search_text": f"{user_query} {correction}",
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
        """Return up to top_k entries most relevant to query by token overlap."""
        qtok = _tokenize(query)
        if not qtok:
            return []
        scored = []
        for e in self._data:
            etok = _tokenize(e.get("search_text", ""))
            s = _score(qtok, etok)
            if e.get("type") in ("rule", "policy") and s > 0:
                s += 0.5
            if s >= _MIN_SCORE:
                scored.append((s, e))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in scored[:top_k]]

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
                parts.append(
                    f"[USER CORRECTION] When asked: \"{e['query']}\", the correct approach is: {e['instruction']}"
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
            "\n\nCLEO LEARNED MEMORY (from past usage — apply precisely):\n"
            + "\n".join(f"- {p}" for p in parts)
        )


# Singleton
_memory: Optional[CleoMemory] = None

def get_memory() -> CleoMemory:
    global _memory
    if _memory is None:
        _memory = CleoMemory()
    return _memory


if __name__ == "__main__":
    # Self-check
    m = CleoMemory()
    m.record_correction("show top aws services", "Always include AmazonEC2 and AmazonRDS in the top services list")
    m.record_sql_fix("SELECT * FROM AWS_CUR", "FQ-USR-304", "SELECT lineItem_ProductCode AS svc, SUM(lineItem_UnblendedCost) AS cost FROM AWS_CUR GROUP BY svc ORDER BY cost DESC LIMIT 10")
    m.record_good_pattern("what is my bedrock spend", "Queried AWS_CUR filtering lineItem_ProductCode='AmazonBedrock', returned monthly breakdown with MoM variance")

    ctx = m.build_context_block("show top aws services by cost")
    assert "USER CORRECTION" in ctx, "Should find correction"
    ctx2 = m.build_context_block("SELECT * FROM AWS_CUR")
    assert "LEARNED SQL FIX" in ctx2, "Should find SQL fix"
    ctx3 = m.build_context_block("what is the weather today")
    assert ctx3 == "", "Unrelated query should return empty"

    # Cleanup test entries
    for e in m.all_entries():
        m.delete(e["id"])

    print("All checks passed.")
