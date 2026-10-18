# ── Cleo FinOps Agent — Container Image ───────────────────────────────────────
# Runs cleo_server.py (HTTP API + web UI) backed by an Ollama local model.
# For cloud LLM engines (OpenAI/Anthropic/Gemini) set AI_ENGINE and the
# corresponding API key env var — Ollama is not needed in that case.
FROM python:3.12-slim

# System deps (curl for healthcheck, ca-certs for HTTPS)
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (layer-cached unless requirements change)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir openai anthropic google-generativeai

# Copy application code
# All runtime modules (the server imports cleo_query/charts/llm/mcp/market_data too) + the FinOps
# reference library that cleo_finops_refs.py reads from .agents/skills/cloud-finops
COPY cleo_*.py cleo_ui.html ./
COPY auth/ ./auth/
COPY .agents/skills/cloud-finops ./.agents/skills/cloud-finops

# Create non-root user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# ── Runtime configuration ─────────────────────────────────────────────────────
# OAuth token: mount your ~/.cleo/oauth_tokens.json as a K8s Secret at this path
ENV CLEO_TOKENS_FILE=/run/secrets/cleo-token/oauth_tokens.json

# AI engine: use 'ollama' for a local model or 'openai'/'anthropic'/'gemini' for cloud
ENV AI_ENGINE=ollama
ENV OLLAMA_HOST=http://ollama:11434
ENV OLLAMA_MODEL=qwen2.5:7b

# Pass cloud LLM API keys via K8s Secrets / env
# ENV OPENAI_API_KEY=...
# ENV ANTHROPIC_API_KEY=...
# ENV GEMINI_API_KEY=...

ENV PORT=8080
# Bind all interfaces INSIDE the container so a published port can reach it. Cleo has no login:
# publish it on loopback only (docker run -p 127.0.0.1:8080:8080 ...; see docker-compose.yml).
# Requests are also rejected unless Host is localhost/127.0.0.1 (add others via CLEO_ALLOWED_HOSTS).
ENV CLEO_HOST=0.0.0.0
EXPOSE 8080

# Healthcheck — waits up to 60s for startup (model loading takes time)
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=4 \
  CMD curl -sf http://localhost:8080/health | grep -q '"status":"ok"' || exit 1

CMD ["python", "cleo_server.py", "--no-browser", "--no-update"]
