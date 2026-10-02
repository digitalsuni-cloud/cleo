"""
test_streaming.py — Integration self-check for Cleo SSE streaming and non-streaming responses.
"""
import json
from fastapi.testclient import TestClient
import cleo_server
from cleo_server import app
from cleo_agent import AIClient

class MockMCP:
    def call_tool(self, name, args):
        return {"content": [{"text": "[]"}]}
    @property
    def _use_fallback(self): return False
    @_use_fallback.setter
    def _use_fallback(self, v): pass

def run_checks():
    cleo_server._mcp = MockMCP()
    cleo_server._tools = [{"name": "list_orgs", "description": "List orgs"}]
    cleo_server._ai = AIClient("direct", {}, cleo_server._tools)

    client = TestClient(app)

    # 1. Non-streaming JSON verification
    r1 = client.post("/chat", json={"message": "hello", "stream": False})
    assert r1.status_code == 200, f"Expected 200, got {r1.status_code}"
    data1 = r1.json()
    assert "response" in data1, "Expected response field in JSON output"
    print("✓ Non-streaming JSON mode verified successfully.")

    # 2. Live SSE Streaming verification
    r2 = client.post("/chat", json={"message": "hello", "stream": True})
    assert r2.status_code == 200, f"Expected 200, got {r2.status_code}"
    assert "text/event-stream" in r2.headers.get("content-type", "")
    lines = [line for line in r2.text.splitlines() if line.startswith("data: ")]
    assert len(lines) >= 2, f"Expected at least 2 SSE events, got {len(lines)}"

    # Parse and verify event stream contents
    types = [json.loads(line.replace("data: ", "")).get("type") for line in lines]
    assert "delta" in types, "Stream must deliver delta token events"
    assert "done" in types, "Stream must deliver terminal done event with metadata"
    print(f"✓ Streaming SSE verified successfully ({len(lines)} events: {set(types)}).")

if __name__ == "__main__":
    run_checks()
    print("All streaming checks passed.")
