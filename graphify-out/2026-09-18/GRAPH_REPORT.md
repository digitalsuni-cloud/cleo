# Graph Report - cleo  (2026-09-18)

## Corpus Check
- 14 files · ~22,821 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 3 file(s) not represented in the graph (top: (none) 2, .example 1)

## Summary
- 194 nodes · 330 edges · 23 communities (19 shown, 4 thin omitted)
- Extraction: 98% EXTRACTED · 2% INFERRED · 0% AMBIGUOUS · INFERRED: 6 edges (avg confidence: 0.87)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- cleo_server.py
- cleo_agent.py
- OAuth2Helper
- chat
- MCPClient
- Cleo — CloudHealth FinOps AI Agent
- cleo_gui.py
- CloudHealthDirectEngine
- Cost History Endpoint
- .__init__
- CloudHealth GraphQL API Guide
- CloudHealth FlexReports SQL Guide
- rules/graphify.md
- workflows/graphify.md
- init_mcp_if_authenticated
- _load_config
- ._call_active_llm
- get
- list_engines
- get_recent_logs
- clear_session
- log_requests
- startup

## God Nodes (most connected - your core abstractions)
1. `OAuth2Helper` - 15 edges
2. `MCPClient` - 13 edges
3. `init_mcp_if_authenticated()` - 12 edges
4. `chat()` - 9 edges
5. `_load_config()` - 8 edges
6. `CloudHealthDirectEngine` - 8 edges
7. `AIClient` - 8 edges
8. `Cleo — CloudHealth FinOps AI Agent` - 8 edges
9. `get_logger()` - 6 edges
10. `auth_submit_code()` - 6 edges

## Surprising Connections (you probably didn't know these)
- `set_engine()` --uses--> `AIClient`  [INFERRED]
  cleo_server.py → cleo_agent.py
- `init_mcp_if_authenticated()` --calls--> `_load_config()`  [EXTRACTED]
  cleo_server.py → cleo_agent.py
- `list_engines()` --calls--> `_load_config()`  [EXTRACTED]
  cleo_server.py → cleo_agent.py
- `init_mcp_if_authenticated()` --calls--> `MCPClient`  [EXTRACTED]
  cleo_server.py → cleo_agent.py
- `init_mcp_if_authenticated()` --calls--> `get_installed_mlx_models()`  [EXTRACTED]
  cleo_server.py → cleo_agent.py

## Import Cycles
- None detected.

## Communities (23 total, 4 thin omitted)

### Community 0 - "cleo_server.py"
Cohesion: 0.19
Nodes (10): _bg_pull_mlx_model(), _bg_pull_model(), download_model(), DownloadRequest, cleo_server.py — High-Performance GUI & HTTP API Server for Cleo FinOps Agent…, fastapi, fastapi_responses, pydantic (+2 more)

### Community 1 - "cleo_agent.py"
Cohesion: 0.12
Nodes (24): base64, ColorFormatter, get_logger(), cleo_logger.py — Centralized Verbose Logging for Cleo FinOps Agent…, Returns a child logger under the 'cleo' namespace., collections, concurrent_futures, csv (+16 more)

### Community 2 - "OAuth2Helper"
Cohesion: 0.16
Nodes (7): OAuth2Helper, Clears stored token files from disk., Exchanges authorization code and PKCE verifier for access token., Generates PKCE verifier, challenge, state, and full authorization URL., Runs the interactive OAuth 2.0 PKCE flow in terminal., A dependency-free OAuth 2.0 helper with PKCE & RFC 7591 Dynamic Client…, Dynamically registers a new confidential OAuth client with CloudHealth via RFC…

### Community 3 - "chat"
Cohesion: 0.28
Nodes (9): BaseModel, build_system_prompt(), chat(), tracked_call_tool(), ChatRequest, ChatResponse, EngineSelection, TokenUpdateRequest (+1 more)

### Community 4 - "MCPClient"
Cohesion: 0.13
Nodes (10): AIClient, _csv_to_markdown(), extract_requested_service(), MCPClient, parse_query_time_context(), Robust MCP Client for CloudHealth. Communicates via direct HTTP JSON-RPC to…, Clears all cached MCP tool responses., Extracts AWS service productCode and display label from query text. (+2 more)

### Community 5 - "Cleo — CloudHealth FinOps AI Agent"
Cohesion: 0.18
Nodes (10): 1. CloudHealth Authentication, 2. AI Engine Setup, Architecture, Cleo — CloudHealth FinOps AI Agent, Config Files, Example Questions, First-Time Setup, Installation (+2 more)

### Community 6 - "cleo_gui.py"
Cohesion: 0.32
Nodes (7): argparse, is_port_in_use(), main(), cleo_gui.py — Desktop GUI Application for Cleo FinOps Agent…, start_server(), socket, threading

### Community 8 - "Cost History Endpoint"
Cohesion: 0.33
Nodes (5): CloudHealth REST API Guide, Cost History Endpoint, Example Request, Headers Required, Query Parameters

### Community 10 - "CloudHealth GraphQL API Guide"
Cohesion: 0.40
Nodes (4): CloudHealth GraphQL API Guide, Common Queries, Important Concept, Introspection

### Community 11 - "CloudHealth FlexReports SQL Guide"
Cohesion: 0.50
Nodes (3): Basic Structure, CloudHealth FlexReports SQL Guide, GraphQL Execution

### Community 14 - "init_mcp_if_authenticated"
Cohesion: 0.24
Nodes (10): get_access_token(), auth_submit_code(), CodeSubmission, health(), init_mcp_if_authenticated(), _load_pending_verifiers(), oauth_callback(), Attempts to connect to CloudHealth MCP with stored access token. (+2 more)

### Community 15 - "_load_config"
Cohesion: 0.43
Nodes (7): _load_config(), _save_config(), auth_logout(), Disconnects CloudHealth session and purges stored tokens., save_token(), set_engine(), post

### Community 16 - "._call_active_llm"
Cohesion: 0.17
Nodes (11): call_anthropic_api(), call_gemini_api(), call_mlx_generate(), call_ollama_chat(), call_openai_api(), Attempts to call the configured LLM engine. Returns (response_text,…, Invokes local Apple Silicon MLX model using unified memory., Invokes local Ollama chat API. (+3 more)

### Community 17 - "get"
Cohesion: 0.29
Nodes (7): auth_login(), get_download_status(), get_tools(), Generates the PKCE authorization URL for Cleo's independent OAuth client.…, _save_pending_verifier(), serve_gui(), get

### Community 18 - "list_engines"
Cohesion: 0.33
Nodes (6): get_installed_mlx_models(), get_installed_ollama_models(), Scans local Hugging Face cache for downloaded MLX models (equivalent to…, Returns list of installed models from local Ollama daemon., _check_ollama_alive(), list_engines()

### Community 19 - "get_recent_logs"
Cohesion: 0.50
Nodes (4): get_recent_logs(), Returns the most recent log entries from the ring buffer., get_logs(), Returns the latest backend verbose logs for in-GUI inspection.

### Community 20 - "clear_session"
Cohesion: 0.67
Nodes (3): clear_session(), _save_sessions(), delete

### Community 21 - "log_requests"
Cohesion: 0.67
Nodes (3): log_requests(), middleware, Request

### Community 22 - "startup"
Cohesion: 0.67
Nodes (3): shutdown(), startup(), on_event

## Knowledge Gaps
- **18 isolated node(s):** `graphify`, `Workflow: graphify`, `Requirements`, `Installation`, `1. CloudHealth Authentication` (+13 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 78 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **4 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `OAuth2Helper` connect `OAuth2Helper` to `cleo_agent.py`?**
  _High betweenness centrality (0.141) - this node is a cross-community bridge._
- **Why does `MCPClient` connect `MCPClient` to `cleo_server.py`, `cleo_agent.py`, `init_mcp_if_authenticated`, `CloudHealthDirectEngine`?**
  _High betweenness centrality (0.080) - this node is a cross-community bridge._
- **Why does `CloudHealthDirectEngine` connect `CloudHealthDirectEngine` to `cleo_agent.py`?**
  _High betweenness centrality (0.053) - this node is a cross-community bridge._
- **What connects `graphify`, `Workflow: graphify`, `Requirements` to the rest of the system?**
  _18 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `cleo_agent.py` be split into smaller, more focused modules?**
  _Cohesion score 0.12169312169312169 - nodes in this community are weakly interconnected._
- **Should `MCPClient` be split into smaller, more focused modules?**
  _Cohesion score 0.1286549707602339 - nodes in this community are weakly interconnected._