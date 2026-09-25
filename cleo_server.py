#!/usr/bin/env python3
"""
cleo_server.py — High-Performance GUI & HTTP API Server for Cleo FinOps Agent
================================================================================
Provides a complete, modern Web UI and REST API for Cleo, with native
OAuth 2.0 PKCE handshake support for CloudHealth MCP, detailed verbose logging,
and live diagnostic inspection.
"""
from __future__ import annotations

import sys, os, subprocess

_base_dir = os.path.dirname(os.path.abspath(__file__))
_venv_dir = os.path.join(_base_dir, ".venv")
_venv_python = os.path.join(_venv_dir, "bin", "python3") if os.name != "nt" else os.path.join(_venv_dir, "Scripts", "python.exe")

def _is_python_ready(py_bin: str) -> bool:
    if not os.path.exists(py_bin):
        return False
    try:
        subprocess.check_call([py_bin, "-c", "import fastapi; import uvicorn; import httpx"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False

def _setup_and_activate_venv():
    # If running outside our .venv, ensure .venv is built and fully healthy, then switch to it.
    if os.path.abspath(sys.executable) != os.path.abspath(_venv_python):
        if not _is_python_ready(_venv_python):
            # Check if pip works inside the existing venv
            has_pip = False
            if os.path.exists(_venv_python):
                try:
                    subprocess.check_call([_venv_python, "-m", "pip", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    has_pip = True
                except Exception:
                    has_pip = False

            if not has_pip:
                # Existing venv is broken or missing pip (e.g. Debian/Ubuntu ensurepip missing).
                # Rebuild cleanly with --without-pip + bootstrap get-pip.py (zero sudo needed!)
                import shutil
                if os.path.exists(_venv_dir):
                    shutil.rmtree(_venv_dir, ignore_errors=True)

                print(f"⚙️  Setting up isolated virtual environment in .venv ...")
                venv_ok = False
                try:
                    subprocess.check_call([sys.executable, "-m", "venv", _venv_dir], stderr=subprocess.DEVNULL)
                    venv_ok = True
                except Exception:
                    shutil.rmtree(_venv_dir, ignore_errors=True)
                    try:
                        print("ℹ️  Creating virtual environment with --without-pip (no sudo needed)...")
                        subprocess.check_call([sys.executable, "-m", "venv", "--without-pip", _venv_dir])
                        if os.path.exists(_venv_python):
                            print("📥 Bootstrapping pip into .venv ...")
                            import urllib.request
                            get_pip_path = os.path.join(_venv_dir, "get-pip.py")
                            def _dl_hook(blocks, block_size, total_size):
                                if total_size > 0:
                                    pct = min(100, int(blocks * block_size * 100 / total_size))
                                    print(f"\r📥 Downloading get-pip.py: {pct}%", end="", flush=True)
                            urllib.request.urlretrieve("https://bootstrap.pypa.io/get-pip.py", get_pip_path, reporthook=_dl_hook)
                            print("\r📥 Downloading get-pip.py: 100% (done)")
                            subprocess.check_call([_venv_python, get_pip_path, "--no-warn-script-location"])
                            if os.path.exists(get_pip_path):
                                os.remove(get_pip_path)
                            venv_ok = True
                    except Exception as e:
                        print(f"⚠️  Could not auto-create isolated .venv: {e}")

            # Install requirements inside .venv
            if os.path.exists(_venv_python):
                req_file = os.path.join(_base_dir, "requirements.txt")
                if os.path.exists(req_file):
                    print(f"📦 Installing required packages from requirements.txt ...")
                    subprocess.check_call([_venv_python, "-m", "pip", "install", "--upgrade", "pip", "--progress-bar", "on"])
                    subprocess.check_call([_venv_python, "-m", "pip", "install", "-r", req_file, "--progress-bar", "on"])
                else:
                    print("⚠️  No requirements.txt found. Installing fallback packages...")
                    subprocess.check_call([_venv_python, "-m", "pip", "install", "fastapi", "uvicorn[standard]", "httpx", "--progress-bar", "on"])

                import platform
                try:
                    if sys.platform == "darwin" and platform.machine() == "arm64":
                        print("🍎 Apple Silicon detected. Auto-installing 'mlx-lm' for local Qwen support...")
                        subprocess.check_call([_venv_python, "-m", "pip", "install", "mlx-lm", "huggingface_hub", "--progress-bar", "on"])
                    elif sys.platform == "win32":
                        print("🪟 Windows detected. Auto-installing 'llama-cpp-python' and 'transformers' for local Qwen support...")
                        subprocess.check_call([_venv_python, "-m", "pip", "install", "huggingface_hub", "transformers", "llama-cpp-python", "--progress-bar", "on"])
                    else:
                        print("🐧 Linux detected. Auto-installing local LLM packages for Qwen support...")
                        print("   ℹ️  Note: Compiling llama-cpp-python may take 1-2 minutes; this is optional for local LLM mode.")
                        subprocess.check_call([_venv_python, "-m", "pip", "install", "huggingface_hub", "transformers", "llama-cpp-python", "--progress-bar", "on"])
                except Exception as e:
                    print(f"⚠️  Note: Could not auto-install optional local LLM packages ({e}). The core server will still start.")

                print("✅ Setup complete! Starting Cleo Server...\n")

        # Relaunch script using the venv python if available
        if os.path.exists(_venv_python):
            if sys.argv and sys.argv[0] != "-c":
                if os.name == "nt":
                    subprocess.check_call([_venv_python] + sys.argv)
                    sys.exit(0)
                else:
                    os.execv(_venv_python, [_venv_python] + sys.argv)

    # Fallback safety: if running inside an environment that still lacks core dependencies, install them directly
    try:
        import fastapi
        import uvicorn
        import httpx
    except ImportError:
        print("📦 Installing required packages into active environment...")
        req_file = os.path.join(_base_dir, "requirements.txt")
        if os.path.exists(req_file):
            subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", req_file, "--progress-bar", "on"])
        else:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "fastapi", "uvicorn[standard]", "httpx", "--progress-bar", "on"])

_setup_and_activate_venv()

import json, uuid, time, argparse, urllib.parse, urllib.request, base64, hashlib, socket, subprocess, platform, shutil
from datetime import datetime, timezone
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel
import uvicorn

import atexit
from cleo_logger import get_logger, get_recent_logs
logger = get_logger("server")
import threading

from cleo_agent import (
    MCPClient, AIClient, build_system_prompt, run_agent_turn,
    get_access_token, _load_config, _save_config, AI_ENGINES, auth_helper,
    STANDARD_CH_TOOLS, LOCAL_CLIENT_ID, LOCAL_REDIRECT_URI,
    LOCAL_MODELS, PUBLIC_ENGINES, get_installed_ollama_models, OLLAMA_BASE_URL,
    MLX_MODELS, get_installed_mlx_models, call_mlx_generate, unload_mlx_models, estimate_token_count,
    crawl_and_cache_all_datasource_metadata, split_thinking_and_response
)

def unload_ollama_models(model_name: Optional[str] = None):
    """Tells local Ollama daemon to immediately evict loaded models from RAM/VRAM."""
    if not _check_ollama_alive():
        return
    try:
        targets = []
        if model_name:
            targets.append(model_name.removeprefix("ollama:").strip())
        else:
            req = urllib.request.Request(f"{OLLAMA_BASE_URL}/api/ps")
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                for item in data.get("models", []):
                    name = item.get("name") or item.get("model")
                    if name:
                        targets.append(name)

        for m in targets:
            logger.info(f"🧹 [Ollama Unload] Evicting model '{m}' from VRAM/RAM...")
            payload = json.dumps({"model": m, "keep_alive": 0}).encode("utf-8")
            req = urllib.request.Request(
                f"{OLLAMA_BASE_URL}/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                pass
            logger.info(f"✅ [Ollama Unload] Model '{m}' evicted from memory.")
    except Exception as e:
        logger.warning(f"Could not evict Ollama models: {e}")

def _on_process_shutdown():
    try:
        unload_mlx_models()
        unload_ollama_models()
    except Exception:
        pass

atexit.register(_on_process_shutdown)

# ── Idle LLM unload watchdog ──────────────────────────────────────────────────
# Unload local model from memory after 75s of inactivity; reload happens
# automatically on the next chat request via lazy-load in call_mlx_generate.
_last_activity: float = 0.0
_IDLE_UNLOAD_SECS = 75  # midpoint of 60-90s window

def _idle_watchdog():
    while True:
        time.sleep(30)
        if _last_activity and (time.time() - _last_activity) > _IDLE_UNLOAD_SECS:
            try:
                from cleo_agent import _mlx_models_cache
                if _mlx_models_cache:
                    logger.info("💤 [Idle Watchdog] No activity for 75s — unloading local LLM from memory.")
                    unload_mlx_models()
                    unload_ollama_models()
            except Exception as e:
                logger.debug(f"[Idle Watchdog] {e}")

threading.Thread(target=_idle_watchdog, daemon=True, name="idle-unload-watchdog").start()

# Ensure workspace virtualenv site-packages are accessible
_cleo_root = os.path.dirname(os.path.abspath(__file__))
_venv_lib = os.path.join(_cleo_root, ".venv", "lib")
if os.path.isdir(_venv_lib):
    for _d in os.listdir(_venv_lib):
        _sp = os.path.join(_venv_lib, _d, "site-packages")
        if os.path.isdir(_sp) and _sp not in sys.path:
            sys.path.insert(0, _sp)
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 60)
    logger.info("  🤖  Cleo FinOps Agent Server Started (Verbose Logging Active)")
    logger.info("=" * 60)
    if init_mcp_if_authenticated():
        logger.info(f"✅ CloudHealth Connection Ready ({len(_tools)} tools available)")
    else:
        logger.info("ℹ️ CloudHealth authentication needed (use Web GUI to connect)")
    yield
    # Server shutdown: evict any loaded local models and close MCP
    logger.info("🛑 Cleo server shutting down. Releasing local LLM models from memory...")
    if _mcp:
        try:
            _mcp.close()
        except Exception:
            pass
    unload_mlx_models()
    unload_ollama_models()

app = FastAPI(
    title="Cleo — CloudHealth FinOps AI",
    description="Intelligent FinOps Assistant powered by CloudHealth MCP",
    version="2.1.0",
    lifespan=lifespan,
)

# Check for local MLX models on Apple Silicon
_init_mlx = get_installed_mlx_models()
_ready_mlx = {m["repo_id"]: m for m in _init_mlx if m.get("downloaded")}
if "mlx-community/Qwen3.5-9B-MLX-4bit" in _ready_mlx:
    _init_engine_key = "mlx:mlx-community/Qwen3.5-9B-MLX-4bit"
    _init_engine_label = "Qwen3.5-9B-4bit (MLX Default)"
elif "mlx-community/Qwen2.5-7B-Instruct-4bit" in _ready_mlx:
    _init_engine_key = "mlx:mlx-community/Qwen2.5-7B-Instruct-4bit"
    _init_engine_label = "Qwen2.5-7B-Instruct-4bit (MLX Default)"
elif _ready_mlx:
    first_repo = list(_ready_mlx.keys())[0]
    _init_engine_key = f"mlx:{first_repo}"
    _init_engine_label = f"{first_repo.split('/')[-1]} (MLX)"
else:
    _init_engine_key = "ollama:qwen2.5:7b"
    _init_engine_label = "Qwen2.5-7B-Instruct-4bit (Ollama)"

# Global runtime state
_mcp: Optional[MCPClient] = None
_ai:  Optional[AIClient]  = None
_tools: list[dict]     = []

SESSIONS_CACHE_FILE = os.path.expanduser("~/.cleo/sessions.json")
MAX_SAVED_SESSIONS = 100

def _generate_chat_title(prompt: str) -> str:
    p = (prompt or "").strip()
    if not p:
        return "New Conversation"
    prefixes = [
        "what are the ", "what is the ", "what's the ", "what's my ", "what is my ",
        "show our ", "show me the ", "show me ", "show ", "can you ", "please ",
        "get the ", "get ", "list all ", "list ", "tell me about ", "tell me ",
        "try listing the ", "try listing ", "find ", "check "
    ]
    p_lower = p.lower()
    for prefix in prefixes:
        if p_lower.startswith(prefix):
            p = p[len(prefix):].strip()
            p_lower = p.lower()
            break
    if p:
        p = p[0].upper() + p[1:]
    p = p.rstrip("?. ")
    return (p[:46] + "...") if len(p) > 46 else (p or "New Conversation")

def _get_active_session_id() -> Optional[str]:
    if not _sessions:
        return None
    sorted_sids = sorted(
        _sessions.keys(),
        key=lambda sid: _sessions[sid].get("updated_at", "") if isinstance(_sessions[sid], dict) else "",
        reverse=True
    )
    return sorted_sids[0] if sorted_sids else None

def _normalize_session(sid: str, sdata: any) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    if isinstance(sdata, list):
        title = "New Conversation"
        for m in sdata:
            if m.get("role") == "user" and m.get("content"):
                title = _generate_chat_title(m["content"])
                break
        return {
            "id": sid,
            "title": title,
            "created_at": now,
            "updated_at": now,
            "messages": sdata
        }
    elif isinstance(sdata, dict):
        if "messages" not in sdata or not isinstance(sdata["messages"], list):
            sdata["messages"] = []
        if "id" not in sdata:
            sdata["id"] = sid
        if "created_at" not in sdata:
            sdata["created_at"] = now
        if "updated_at" not in sdata:
            sdata["updated_at"] = sdata.get("created_at", now)
        if "title" not in sdata or not sdata["title"]:
            title = "New Conversation"
            for m in sdata.get("messages", []):
                if m.get("role") == "user" and m.get("content"):
                    title = _generate_chat_title(m["content"])
                    break
            sdata["title"] = title
        if "pinned" not in sdata:
            sdata["pinned"] = False
        return sdata
    return {
        "id": sid,
        "title": "New Conversation",
        "created_at": now,
        "updated_at": now,
        "messages": [],
        "pinned": False
    }

def _load_sessions() -> dict:
    if os.path.exists(SESSIONS_CACHE_FILE):
        try:
            with open(SESSIONS_CACHE_FILE, "r") as f:
                raw = json.load(f)
                if isinstance(raw, dict):
                    normalized = {}
                    for sid, sdata in raw.items():
                        normalized[sid] = _normalize_session(sid, sdata)
                    # Keep sorted by updated_at descending, up to MAX_SAVED_SESSIONS
                    sorted_items = sorted(
                        normalized.items(),
                        key=lambda item: item[1].get("updated_at", ""),
                        reverse=True
                    )
                    return dict(sorted_items[:MAX_SAVED_SESSIONS])
        except Exception as e:
            logger.warning(f"Could not load sessions cache: {e}")
    return {}

def _save_sessions():
    global _sessions
    try:
        os.makedirs(os.path.dirname(SESSIONS_CACHE_FILE), exist_ok=True)
        sorted_items = sorted(
            _sessions.items(),
            key=lambda item: item[1].get("updated_at", "") if isinstance(item[1], dict) else "",
            reverse=True
        )
        if len(sorted_items) > MAX_SAVED_SESSIONS:
            _sessions = dict(sorted_items[:MAX_SAVED_SESSIONS])
        with open(SESSIONS_CACHE_FILE, "w") as f:
            json.dump(_sessions, f, indent=2)
    except Exception as e:
        logger.warning(f"Could not save sessions cache: {e}")

_sessions: dict        = _load_sessions()
_server_start          = datetime.now(timezone.utc).isoformat()
_active_engine_key     = _init_engine_key
_engine_label          = _init_engine_label
_model_downloads: dict = {}  # model_id -> {"status": "downloading"|"completed"|"failed", "progress": 0, "error": ""}
_oauth_verifiers: dict = {}  # state -> {verifier, client_id, redirect_uri}
_last_error: str       = ""

VERIFIER_CACHE_FILE = os.path.expanduser("~/.cleo/pkce_verifier.json")

def _format_bytes(b: int) -> str:
    """Formats byte count to human-readable string (KB, MB, GB)."""
    if b >= 1024 ** 3:
        return f"{b / (1024 ** 3):.2f} GB"
    elif b >= 1024 ** 2:
        return f"{b / (1024 ** 2):.1f} MB"
    elif b >= 1024:
        return f"{round(b / 1024)} KB"
    return f"{b} B"

def _get_hf_blobs_size(repo_id: str) -> int:
    """Calculates the total size of downloaded blobs on disk for an HF repo."""
    try:
        hf_cache = os.environ.get("HUGGINGFACE_HUB_CACHE") or os.path.expanduser(
            os.path.join(os.environ.get("HF_HOME", "~/.cache/huggingface"), "hub")
        )
        repo_folder = f"models--{repo_id.replace('/', '--')}"
        blobs_dir = os.path.join(hf_cache, repo_folder, "blobs")
        if not os.path.isdir(blobs_dir):
            return 0
        total = 0
        with os.scandir(blobs_dir) as it:
            for entry in it:
                try:
                    if entry.is_file(follow_symlinks=False) and not entry.name.endswith(".lock"):
                        total += entry.stat().st_size
                except OSError:
                    pass
        return total
    except Exception:
        return 0

def _bg_pull_mlx_model(repo_id: str):
    global _model_downloads
    _model_downloads[repo_id] = {
        "status": "downloading",
        "progress": 1,
        "status_detail": "Connecting to Hugging Face...",
        "error": ""
    }

    # 1. Determine total size in bytes
    total_bytes = 0
    try:
        from huggingface_hub import HfApi
        api = HfApi()
        info = api.model_info(repo_id, files_metadata=True, timeout=5.0)
        total_bytes = sum(s.size for s in (info.siblings or []) if s.size)
    except Exception as e:
        logger.debug(f"[MLX Download] Could not get model metadata for {repo_id}: {e}")

    if not total_bytes or total_bytes <= 0:
        known_sizes = {
            "mlx-community/Qwen3.5-9B-MLX-4bit": 5977074591,
            "mlx-community/Qwen3.5-4B-4bit": 3061130647,
            "mlx-community/Qwen2.5-7B-Instruct-4bit": 4600000000,
            "mlx-community/Qwen2.5-Coder-32B-Instruct-4bit": 19800000000,
        }
        total_bytes = known_sizes.get(repo_id, int(3.0 * (1024 ** 3)))

    total_str = _format_bytes(total_bytes)
    _model_downloads[repo_id]["status_detail"] = f"Starting download ({total_str})..."

    # 2. Start snapshot_download in a background worker thread
    worker_error = []
    def _worker():
        try:
            from huggingface_hub import snapshot_download
            snapshot_download(repo_id=repo_id)
        except Exception as err:
            worker_error.append(err)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()

    # 3. Sample disk progress in real-time while download proceeds
    last_bytes = _get_hf_blobs_size(repo_id)
    last_time = time.time()
    speed_mb = 0.0

    while thread.is_alive():
        time.sleep(0.4)
        curr_bytes = _get_hf_blobs_size(repo_id)
        now = time.time()
        dt = now - last_time

        if dt >= 0.8:
            diff = max(0, curr_bytes - last_bytes)
            instant_speed = (diff / (1024 * 1024)) / dt
            speed_mb = (0.7 * speed_mb) + (0.3 * instant_speed) if speed_mb > 0 else instant_speed
            last_bytes = curr_bytes
            last_time = now

        curr_str = _format_bytes(curr_bytes)
        pct = min(99.0, round((curr_bytes / total_bytes) * 100, 1)) if total_bytes > 0 else 5.0
        pct = max(1.0, pct)

        if speed_mb >= 0.1:
            detail = f"Downloading: {curr_str} / {total_str} ({speed_mb:.1f} MB/s)"
        elif curr_bytes > 0:
            detail = f"Downloading: {curr_str} / {total_str}"
        else:
            detail = "Downloading weights to local cache..."

        _model_downloads[repo_id] = {
            "status": "downloading",
            "progress": pct,
            "status_detail": detail,
            "error": ""
        }

    thread.join()

    if worker_error:
        err = worker_error[0]
        logger.error(f"❌ [MLX Download Failed] {repo_id}: {err}")
        _model_downloads[repo_id] = {
            "status": "failed",
            "progress": 0,
            "error": str(err),
            "status_detail": f"Failed: {err}"
        }
    else:
        logger.info(f"✅ [MLX Download Complete] {repo_id}")
        _model_downloads[repo_id] = {
            "status": "completed",
            "progress": 100,
            "status_detail": "Download complete",
            "error": ""
        }

def _check_ollama_alive() -> bool:
    try:
        req = urllib.request.Request(f"{OLLAMA_BASE_URL}/api/tags")
        with urllib.request.urlopen(req, timeout=1.5):
            return True
    except Exception:
        return False

def _find_ollama_bin() -> Optional[str]:
    """Locates the Ollama executable on the system, checking PATH and common installation locations."""
    bin_name = "ollama.exe" if platform.system() == "Windows" else "ollama"
    found = shutil.which(bin_name)
    if found and os.path.isfile(found) and (platform.system() == "Windows" or os.access(found, os.X_OK)):
        return found

    home = os.path.expanduser("~")
    candidates = []
    if platform.system() == "Darwin":
        candidates = [
            "/opt/homebrew/bin/ollama",
            "/usr/local/bin/ollama",
            os.path.join(home, "Applications", "Ollama.app", "Contents", "Resources", "ollama"),
            "/Applications/Ollama.app/Contents/Resources/ollama",
            os.path.join(home, ".local", "bin", "ollama"),
        ]
    elif platform.system() == "Linux":
        candidates = [
            os.path.join(home, ".local", "bin", "ollama"),
            "/usr/local/bin/ollama",
            "/usr/bin/ollama",
            "/bin/ollama",
        ]
    elif platform.system() == "Windows":
        local_app = os.environ.get("LOCALAPPDATA", os.path.join(home, "AppData", "Local"))
        candidates = [
            os.path.join(local_app, "Programs", "Ollama", "ollama.exe"),
            os.path.join(home, "AppData", "Local", "Programs", "Ollama", "ollama.exe"),
        ]

    for cand in candidates:
        if os.path.isfile(cand) and (platform.system() == "Windows" or os.access(cand, os.X_OK)):
            return cand
    return None

def _start_ollama_daemon(timeout: float = 25.0) -> bool:
    """Spawns Ollama daemon in the background if not already running."""
    if _check_ollama_alive():
        return True

    bin_path = _find_ollama_bin()
    if not bin_path:
        return False

    logger.info(f"🚀 Starting Ollama daemon ({bin_path} serve)...")
    try:
        if platform.system() == "Windows":
            subprocess.Popen(
                [bin_path, "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "DETACHED_PROCESS", 0)
            )
        else:
            subprocess.Popen(
                [bin_path, "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
    except Exception as e:
        logger.error(f"❌ Failed to spawn Ollama daemon: {e}")
        return False

    t0 = time.time()
    while time.time() - t0 < timeout:
        if _check_ollama_alive():
            logger.info("✅ Ollama daemon started successfully.")
            return True
        time.sleep(0.5)

    logger.warning(f"⚠️ Timed out after {timeout}s waiting for Ollama daemon to respond.")
    return False

def _install_ollama(status_cb=None) -> tuple[bool, str]:
    """Downloads and installs Ollama without requiring sudo."""
    system = platform.system()
    home = os.path.expanduser("~")
    cache_dir = os.path.join(home, ".cleo", "cache")
    os.makedirs(cache_dir, exist_ok=True)

    if status_cb:
        status_cb(5, "Downloading Ollama CLI...")

    if system == "Darwin":
        zip_path = os.path.join(cache_dir, "Ollama-darwin.zip")
        url = "https://ollama.com/download/Ollama-darwin.zip"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Cleo-FinOps/1.0)"})
            with urllib.request.urlopen(req, timeout=180.0) as resp:
                total_size = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                chunk_size = 65536
                with open(zip_path, "wb") as f:
                    while True:
                        chunk = resp.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if status_cb and total_size > 0:
                            pct = round((downloaded / total_size) * 100, 1)
                            mb_cur = round(downloaded / (1024 * 1024), 1)
                            mb_tot = round(total_size / (1024 * 1024), 1)
                            status_cb(round(5 + (pct * 0.7), 1), f"Downloading Ollama ({mb_cur}/{mb_tot} MB)...")
        except Exception as e:
            return False, f"Failed to download Ollama package: {e}"

        if status_cb:
            status_cb(80, "Extracting Ollama...")

        apps_dir = os.path.join(home, "Applications")
        os.makedirs(apps_dir, exist_ok=True)
        res = subprocess.run(["/usr/bin/ditto", "-xk", zip_path, apps_dir], capture_output=True, text=True)
        if res.returncode != 0:
            res = subprocess.run(["unzip", "-q", "-o", zip_path, "-d", apps_dir], capture_output=True, text=True)
            if res.returncode != 0:
                return False, f"Failed to extract Ollama: {res.stderr or 'Extraction error'}"

        installed_bin = os.path.join(apps_dir, "Ollama.app", "Contents", "Resources", "ollama")
        if not os.path.isfile(installed_bin):
            return False, "Ollama binary not found in extracted app bundle."

        try:
            os.chmod(installed_bin, 0o755)
            local_bin = os.path.join(home, ".local", "bin")
            os.makedirs(local_bin, exist_ok=True)
            symlink = os.path.join(local_bin, "ollama")
            if os.path.islink(symlink) or os.path.exists(symlink):
                os.remove(symlink)
            os.symlink(installed_bin, symlink)
        except Exception as e:
            logger.warning(f"Could not symlink to ~/.local/bin: {e}")

        try:
            os.remove(zip_path)
        except Exception:
            pass

        if status_cb:
            status_cb(95, "Ollama CLI installed successfully.")
        return True, ""

    elif system == "Linux":
        arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "amd64"
        tar_zst_path = os.path.join(cache_dir, f"ollama-linux-{arch}.tar.zst")
        url = f"https://github.com/ollama/ollama/releases/latest/download/ollama-linux-{arch}.tar.zst"
        local_dir = os.path.join(home, ".local")
        bin_dir = os.path.join(local_dir, "bin")
        os.makedirs(bin_dir, exist_ok=True)

        # 1. Download release archive directly
        try:
            if status_cb:
                status_cb(10, f"Downloading Ollama Linux ({arch})...")
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Cleo-FinOps/1.0)"})
            with urllib.request.urlopen(req, timeout=180.0) as resp:
                total_size = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                chunk_size = 65536
                with open(tar_zst_path, "wb") as f:
                    while True:
                        chunk = resp.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if status_cb and total_size > 0:
                            pct = round((downloaded / total_size) * 100, 1)
                            mb_cur = round(downloaded / (1024 * 1024), 1)
                            mb_tot = round(total_size / (1024 * 1024), 1)
                            status_cb(round(10 + (pct * 0.65), 1), f"Downloading Ollama ({mb_cur}/{mb_tot} MB)...")
        except Exception as e:
            logger.warning(f"Direct download of Ollama archive failed: {e}")
            # Fallback to install script ONLY if non-interactive sudo is available or root
            if os.geteuid() == 0:
                try:
                    if status_cb:
                        status_cb(30, "Running official install script (root mode)...")
                    subprocess.run(
                        ["sh", "-c", "curl -fsSL https://ollama.com/install.sh | sh"],
                        capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL
                    )
                    if _find_ollama_bin():
                        return True, ""
                except Exception:
                    pass
            return False, f"Failed to download Ollama package: {e}"

        # 2. Extract into ~/.local (non-sudo user space)
        if status_cb:
            status_cb(80, "Extracting Ollama into ~/.local...")

        extracted = False
        # Try method A: system zstd + tar
        try:
            res = subprocess.run(
                ["sh", "-c", f"zstd -d '{tar_zst_path}' --stdout | tar -xf - -C '{local_dir}'"],
                capture_output=True, text=True, timeout=60
            )
            if res.returncode == 0:
                extracted = True
        except Exception:
            pass

        # Try method B: tar with --zstd
        if not extracted:
            try:
                res = subprocess.run(
                    ["tar", "--zstd", "-xf", tar_zst_path, "-C", local_dir],
                    capture_output=True, text=True, timeout=60
                )
                if res.returncode == 0:
                    extracted = True
            except Exception:
                pass

        # Try method C: Python zstandard package
        if not extracted:
            try:
                import zstandard as zstd
            except ImportError:
                if status_cb:
                    status_cb(82, "Installing decompression helper...")
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "zstandard", "-q"],
                    capture_output=True, timeout=45
                )
                try:
                    import zstandard as zstd
                except ImportError:
                    zstd = None

            if zstd:
                try:
                    import tarfile
                    dctx = zstd.ZstdDecompressor()
                    with open(tar_zst_path, "rb") as ifh:
                        with dctx.stream_reader(ifh) as reader:
                            with tarfile.open(fileobj=reader, mode="r|") as tar:
                                tar.extractall(path=local_dir)
                    extracted = True
                except Exception as ze:
                    logger.warning(f"Python zstandard extraction failed: {ze}")

        # Clean up temporary archive file
        try:
            os.remove(tar_zst_path)
        except Exception:
            pass

        installed_bin = os.path.join(bin_dir, "ollama")
        if not os.path.isfile(installed_bin):
            cand = _find_ollama_bin()
            if cand:
                installed_bin = cand
            else:
                return False, "Ollama binary could not be extracted (requires zstd or python zstandard library)."

        try:
            os.chmod(installed_bin, 0o755)
        except Exception:
            pass

        if status_cb:
            status_cb(95, "Ollama CLI installed successfully.")
        return True, ""

    elif system == "Windows":
        installer_path = os.path.join(cache_dir, "OllamaSetup.exe")
        url = "https://ollama.com/download/OllamaSetup.exe"
        try:
            urllib.request.urlretrieve(url, installer_path)
            if status_cb:
                status_cb(50, "Running Ollama installer...")
            res = subprocess.run([installer_path, "/silent"], capture_output=True, timeout=180)
            if _find_ollama_bin():
                return True, ""
        except Exception as e:
            return False, f"Windows installer failed: {e}"
        return False, "Please install Ollama from https://ollama.com/download"

    return False, f"Unsupported OS platform: {system}"

def _bg_pull_model(model_name: str):
    global _model_downloads
    _model_downloads[model_name] = {
        "status": "downloading",
        "progress": 0,
        "status_detail": "Checking Ollama service...",
        "error": ""
    }

    # 1. Ensure Ollama service is active (installing and starting if needed)
    if not _check_ollama_alive():
        ollama_bin = _find_ollama_bin()
        if not ollama_bin:
            logger.info("Ollama binary not found. Starting automatic installation...")
            def _install_status(pct: float, msg: str):
                _model_downloads[model_name]["progress"] = round(pct * 0.15, 1)
                _model_downloads[model_name]["status_detail"] = msg

            ok, err_msg = _install_ollama(status_cb=_install_status)
            if not ok:
                logger.error(f"❌ [Ollama Install Failed]: {err_msg}")
                _model_downloads[model_name] = {
                    "status": "failed",
                    "progress": 0,
                    "error": err_msg,
                    "status_detail": f"Install failed: {err_msg}"
                }
                return

        _model_downloads[model_name]["progress"] = 15
        _model_downloads[model_name]["status_detail"] = "Starting Ollama service..."
        started = _start_ollama_daemon()
        if not started:
            err = "Failed to start Ollama daemon ('ollama serve')"
            logger.error(f"❌ {err}")
            _model_downloads[model_name] = {
                "status": "failed",
                "progress": 0,
                "error": err,
                "status_detail": err
            }
            return

    # 2. Pull the model weights from Ollama
    _model_downloads[model_name]["status_detail"] = f"Connecting to Ollama to pull '{model_name}'..."
    url = f"{OLLAMA_BASE_URL}/api/pull"
    payload = json.dumps({"name": model_name, "stream": True}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=1800.0) as resp:
            for line in resp:
                if not line:
                    continue
                try:
                    chunk = json.loads(line.decode("utf-8"))
                    completed = chunk.get("completed", 0)
                    total = chunk.get("total", 0)
                    status_text = chunk.get("status", "")
                    if total > 0:
                        raw_pct = (completed / total) * 100
                        scaled_pct = round(15 + (raw_pct * 0.84), 1)
                        _model_downloads[model_name]["progress"] = min(99.0, scaled_pct)
                        comp_str = _format_bytes(completed)
                        tot_str = _format_bytes(total)
                        _model_downloads[model_name]["status_detail"] = f"Downloading: {comp_str} / {tot_str}"
                    elif status_text:
                        _model_downloads[model_name]["status_detail"] = status_text
                    if status_text == "success":
                        _model_downloads[model_name]["status"] = "completed"
                        _model_downloads[model_name]["progress"] = 100
                        _model_downloads[model_name]["status_detail"] = "Download complete"
                except Exception:
                    pass
        _model_downloads[model_name]["status"] = "completed"
        _model_downloads[model_name]["progress"] = 100
        _model_downloads[model_name]["status_detail"] = "Download complete"
        logger.info(f"✅ [Ollama Download Complete] Model '{model_name}' successfully downloaded!")
    except Exception as e:
        logger.error(f"❌ [Ollama Download Failed] Model '{model_name}': {e}")
        _model_downloads[model_name] = {"status": "failed", "progress": 0, "error": str(e), "status_detail": f"Failed: {e}"}

def _save_pending_verifier(state: str, info: dict):
    global _oauth_verifiers
    _oauth_verifiers[state] = info
    try:
        with open(VERIFIER_CACHE_FILE, "w") as f:
            json.dump(_oauth_verifiers, f)
        os.chmod(VERIFIER_CACHE_FILE, 0o600)
    except Exception:
        pass

def _load_pending_verifiers() -> dict:
    if os.path.exists(VERIFIER_CACHE_FILE):
        try:
            with open(VERIFIER_CACHE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

# ── CSRF Protection: reject cross-origin state-changing requests ──────────────
# Cleo is a local single-user server with no auth layer; without this, any web
# page the user visits in a browser could silently POST/DELETE to it (e.g.
# overwrite API keys via /api/tokens) using the browser's ambient access.
_MUTATING_METHODS = {"POST", "PUT", "DELETE", "PATCH"}

@app.middleware("http")
async def csrf_protection(request: Request, call_next):
    if request.method in _MUTATING_METHODS:
        origin = request.headers.get("origin")
        if origin:
            origin_host = origin.split("://", 1)[-1].rstrip("/")
            request_host = request.headers.get("host", "")
            if origin_host != request_host:
                logger.warning(
                    f"[CSRF] Rejected cross-origin {request.method} {request.url.path} "
                    f"(Origin={origin!r}, Host={request_host!r})"
                )
                return JSONResponse(status_code=403, content={"detail": "Cross-origin requests are not allowed."})
    return await call_next(request)

# ── HTTP Request Logging Middleware ───────────────────────────────────────────
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    path = request.url.path
    query = request.url.query
    full_path = f"{path}?{query}" if query else path
    is_poll = path in ("/health", "/api/logs", "/favicon.ico")
    if not is_poll:
        logger.info(f"[HTTP IN]  {request.method} {full_path}")
    try:
        response = await call_next(request)
        dur_ms = round((time.time() - start) * 1000, 1)
        if not is_poll:
            logger.info(f"[HTTP OUT] {request.method} {full_path} -> {response.status_code} ({dur_ms}ms)")
        return response
    except Exception as e:
        dur_ms = round((time.time() - start) * 1000, 1)
        logger.error(f"[HTTP ERR] {request.method} {full_path} -> {e} ({dur_ms}ms)")
        raise

def init_mcp_if_authenticated() -> bool:
    """Attempts to connect to CloudHealth MCP with stored access token."""
    global _mcp, _tools, _ai, _active_engine_key, _engine_label, _last_error
    token = get_access_token(interactive=False)
    if not token:
        logger.debug("[MCP Init Check] No CloudHealth access token found in storage")
        _mcp = None
        _tools = []
        return False
    try:
        logger.info("[MCP Init] Initializing CloudHealth connection with stored access token...")
        _mcp = MCPClient(token, token_refresher=lambda: get_access_token(interactive=False))
        info = _mcp.initialize()
        _tools = _mcp.list_tools()
        threading.Thread(target=crawl_and_cache_all_datasource_metadata, args=(_mcp,), daemon=True).start()
        
        cfg = _load_config()
        mlx_inst = get_installed_mlx_models()
        ready_mlx = {m["repo_id"]: m for m in mlx_inst if m.get("downloaded")}
        if "mlx-community/Qwen3.5-9B-MLX-4bit" in ready_mlx:
            default_engine = "mlx:mlx-community/Qwen3.5-9B-MLX-4bit"
        elif "mlx-community/Qwen2.5-7B-Instruct-4bit" in ready_mlx:
            default_engine = "mlx:mlx-community/Qwen2.5-7B-Instruct-4bit"
        elif ready_mlx:
            default_engine = f"mlx:{list(ready_mlx.keys())[0]}"
        else:
            default_engine = "ollama:qwen2.5:7b"

        engine_key = cfg.get("AI_ENGINE") or os.environ.get("AI_ENGINE", default_engine)
        _active_engine_key = engine_key
        
        if engine_key == "direct":
            _engine_label = "Direct FinOps Router"
        elif engine_key == "mlx:mlx-community/Qwen3.5-9B-MLX-4bit":
            _engine_label = "Qwen3.5-9B-4bit (MLX Default)"
        elif engine_key == "mlx:mlx-community/Qwen3.5-4B-4bit":
            _engine_label = "Qwen3.5-4B-4bit (MLX)"
        elif engine_key == "mlx:mlx-community/Qwen2.5-7B-Instruct-4bit":
            _engine_label = "Qwen2.5-7B-Instruct-4bit (MLX)"
        elif engine_key.startswith("mlx:"):
            _engine_label = f"{engine_key.removeprefix('mlx:').split('/')[-1]} (MLX)"
        elif engine_key in ("ollama:hf.co/bartowski/Qwen_Qwen3.5-9B-GGUF:Q4_K_M", "ollama:qwen3.5:9b"):
            _engine_label = "Qwen3.5-9B-4bit (Ollama)"
        elif engine_key in ("ollama:hf.co/bartowski/Qwen_Qwen3.5-4B-GGUF:Q4_K_M", "ollama:qwen3.5:4b"):
            _engine_label = "Qwen3.5-4B-4bit (Ollama)"
        elif engine_key == "ollama:qwen2.5:7b":
            _engine_label = "Qwen2.5-7B-Instruct-4bit (Ollama)"
        elif engine_key.startswith("ollama:"):
            _engine_label = f"Ollama ({engine_key.split(':', 1)[1]})"
        else:
            pe = next((p for p in PUBLIC_ENGINES if p["id"] == engine_key), None)
            _engine_label = pe["name"] if pe else engine_key

        _ai = AIClient(engine_key, cfg, tools=_tools)
        _last_error = ""
        logger.info(f"✅ [MCP Init Success] Connected to CloudHealth MCP with {len(_tools)} tools! Engine: {_engine_label}")
        return True
    except Exception as e:
        _last_error = str(e)
        _mcp = None
        _tools = []
        logger.error(f"❌ [MCP Init Error] {e}")
        return False



# ── Authentication Endpoints ──────────────────────────────────────────────────

@app.get("/auth/login")
def auth_login():
    """
    Generates the PKCE authorization URL for Cleo's independent OAuth client.
    Directly redirects to CloudHealth login and returns automatically
    to http://127.0.0.1:8080/oauth-callback without any third-party pages.
    """
    cid = auth_helper.client_id
    ruri = auth_helper.redirect_uri

    auth_url, verifier, params = auth_helper.generate_auth_params(client_id=cid, redirect_uri=ruri)
    state = params["state"]
    
    info = {
        "verifier": verifier,
        "client_id": cid,
        "redirect_uri": ruri,
        "timestamp": time.time()
    }
    _save_pending_verifier(state, info)
    logger.info(f"[OAuth Login] Redirecting directly to CloudHealth (client: {cid[:20]}..., state: {state})")
    return RedirectResponse(url=auth_url)

class CodeSubmission(BaseModel):
    code: str

@app.post("/auth/submit-code")
def auth_submit_code(sub: CodeSubmission):
    """Exchanges an authorization code (or full callback URL) for access token."""
    global _last_error
    raw_code = sub.code.strip()
    if not raw_code:
        raise HTTPException(status_code=400, detail="Empty authorization code provided")

    # Auto-extract code if user pasted a full redirect URL
    code = raw_code
    if "code=" in raw_code:
        parsed = urllib.parse.urlparse(raw_code)
        qs = urllib.parse.parse_qs(parsed.query or parsed.fragment)
        if "code" in qs:
            code = qs["code"][0].strip()

    logger.info(f"[OAuth Submit Code] Processing authorization code ({code[:10]}...{code[-6:] if len(code)>16 else ''})")
    
    # Retrieve pending verifier from memory or disk
    all_verifiers = dict(_oauth_verifiers)
    all_verifiers.update(_load_pending_verifiers())
    
    verifier_info = None
    if all_verifiers:
        sorted_keys = sorted(all_verifiers.keys(), key=lambda k: all_verifiers[k].get("timestamp", 0), reverse=True)
        verifier_info = all_verifiers[sorted_keys[0]]

    verifier = verifier_info.get("verifier") if verifier_info else ""
    client_id = verifier_info.get("client_id", auth_helper.client_id) if verifier_info else auth_helper.client_id
    redirect_uri = verifier_info.get("redirect_uri", auth_helper.redirect_uri) if verifier_info else auth_helper.redirect_uri

    mcp_data = auth_helper.exchange_code(
        code=code, 
        verifier=verifier, 
        client_id=client_id, 
        redirect_uri=redirect_uri
    )
    if not mcp_data:
        err = getattr(auth_helper, "last_error", "") or "Token exchange failed."
        _last_error = err
        logger.error(f"[OAuth Submit Code Failed] {err}")
        raise HTTPException(status_code=400, detail=err)

    # Clean up verifiers
    _oauth_verifiers.clear()
    if os.path.exists(VERIFIER_CACHE_FILE):
        try:
            os.remove(VERIFIER_CACHE_FILE)
        except Exception:
            pass

    # Initialize MCP
    success = init_mcp_if_authenticated()
    if not success:
        err = _last_error or "CloudHealth MCP initialization failed."
        raise HTTPException(status_code=403, detail=err)

    return {
        "success": True,
        "mcp_connected": success,
        "tools_count": len(_tools),
        "tools": _tools,
        "last_error": ""
    }

@app.post("/auth/logout")
def auth_logout():
    """Disconnects CloudHealth session and purges stored tokens."""
    global _mcp, _tools, _ai, _sessions, _last_error, _oauth_verifiers
    logger.info("[OAuth Logout] Disconnecting CloudHealth session and purging stored tokens...")
    auth_helper.clear_tokens()
    if os.path.exists(VERIFIER_CACHE_FILE):
        try:
            os.remove(VERIFIER_CACHE_FILE)
        except Exception:
            pass
    _mcp = None
    _tools = []
    # ponytail: do NOT wipe persisted session histories on cloudhealth logout
    _oauth_verifiers.clear()
    _last_error = ""
    return {"status": "logged_out", "message": "CloudHealth disconnected and tokens cleared."}

@app.get("/oauth-callback")
def oauth_callback(code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None):
    """Handles automatic callback from CloudHealth."""
    global _last_error
    if error or not code:
        err_msg = error or "No authorization code received from CloudHealth."
        _last_error = err_msg
        logger.error(f"[OAuth Callback Error] {err_msg}")
        return HTMLResponse(content=f"""
        <html><body style="font-family:sans-serif;background:#0f1117;color:#f87171;display:flex;align-items:center;justify-content:center;height:100vh;">
            <div style="background:#1a1d27;padding:32px;border-radius:12px;text-align:center;max-width:500px;border:1px solid #2d3148;">
                <h2>Authentication Failed</h2>
                <p style="color:#94a3b8;margin:16px 0;">{err_msg}</p>
                <a href="/" style="color:#6366f1;text-decoration:none;font-weight:600;">Return to Cleo</a>
            </div>
        </body></html>
        """, status_code=400)

    all_verifiers = dict(_oauth_verifiers)
    all_verifiers.update(_load_pending_verifiers())
    verifier_info = all_verifiers.pop(state, None) if state else None
    if not verifier_info:
        logger.error(f"[OAuth Callback Error] Unknown or missing state parameter: {state!r}")
        _last_error = "OAuth callback rejected: state parameter did not match a pending authorization request."
        return HTMLResponse(content=f"""
        <html><body style="font-family:sans-serif;background:#0f1117;color:#f87171;display:flex;align-items:center;justify-content:center;height:100vh;">
            <div style="background:#1a1d27;padding:32px;border-radius:12px;text-align:center;max-width:500px;border:1px solid #2d3148;">
                <h2>Authentication Failed</h2>
                <p style="color:#94a3b8;margin:16px 0;">{_last_error}</p>
                <a href="/" style="color:#6366f1;text-decoration:none;font-weight:600;">Return to Cleo</a>
            </div>
        </body></html>
        """, status_code=400)

    verifier = verifier_info.get("verifier", "") if verifier_info else ""
    client_id = verifier_info.get("client_id", auth_helper.client_id) if verifier_info else auth_helper.client_id
    redirect_uri = verifier_info.get("redirect_uri", auth_helper.redirect_uri) if verifier_info else auth_helper.redirect_uri

    logger.info(f"[OAuth Callback] Exchanging code ({code[:10]}...{code[-6:]}) with client {client_id[:25]}...")
    mcp_data = auth_helper.exchange_code(
        code=code, 
        verifier=verifier, 
        client_id=client_id, 
        redirect_uri=redirect_uri
    )
    if not mcp_data:
        err = getattr(auth_helper, "last_error", "") or "Token exchange failed."
        _last_error = err
        logger.error(f"[OAuth Callback Exchange Error] {err}")
        return HTMLResponse(content=f"""
        <html><body style="font-family:sans-serif;background:#0f1117;color:#f87171;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;">
            <div style="background:#1a1d27;padding:36px;border-radius:16px;text-align:center;max-width:540px;border:1px solid #2d3148;">
                <h2 style="color:#f87171;margin-bottom:12px;">Authentication Issue</h2>
                <p style="color:#94a3b8;font-size:0.9rem;margin-bottom:24px;word-break:break-all;">{err}</p>
                <a href="/auth/login" style="background:#6366f1;color:white;padding:10px 22px;border-radius:8px;text-decoration:none;font-weight:600;display:inline-block;">Retry Connection</a>
            </div>
        </body></html>
        """, status_code=400)

    # Initialize MCP client with new token
    init_mcp_if_authenticated()

    return HTMLResponse(content="""
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="utf-8">
      <title>Cleo Connected</title>
      <meta http-equiv="refresh" content="1;url=/">
      <style>
        body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #0f1117; color: #f8fafc;
               display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
        .box { background: #1a1d27; padding: 40px; border-radius: 16px; text-align: center; border: 1px solid #2d3148; }
        h1 { color: #10b981; margin-bottom: 8px; font-size: 1.5rem; }
        p { color: #94a3b8; font-size: 0.95rem; }
      </style>
    </head>
    <body>
      <div class="box">
        <div style="font-size: 2.5rem; margin-bottom: 12px;">✅</div>
        <h1>Successfully Connected to CloudHealth!</h1>
        <p>Loading your CloudHealth FinOps datasets...</p>
      </div>
    </body>
    </html>
    """)

# ── API Routes ────────────────────────────────────────────────────────────────

@app.get("/api/logs")
def get_logs(limit: int = 150):
    """Returns the latest backend verbose logs for in-GUI inspection."""
    return {"logs": get_recent_logs(limit)}

@app.get("/health")
def health():
    global _mcp, _tools, _last_error
    token = get_access_token(interactive=False)
    has_token = bool(token)
    if has_token and (not _mcp or not _tools):
        init_mcp_if_authenticated()

    is_connected = bool(_mcp and _tools and not getattr(_mcp, "_use_fallback", False))
    
    if is_connected:
        mcp_status = "connected"
    elif _last_error:
        mcp_status = "unauthorized"
    else:
        mcp_status = "disconnected"

    return {
        "status": "ok" if is_connected else ("error" if _last_error else "unauthenticated"),
        "mcp": mcp_status,
        "has_token": has_token,
        "tools_count": len(_tools) if is_connected else 0,
        "tools": _tools if is_connected else [],
        "ai_engine": _engine_label,
        "ai_engine_key": _active_engine_key,
        "active_sessions": len(_sessions),
        "last_error": _last_error
    }

# ── n8n Orchestrator Compatibility Endpoints ────────────────────────────────
@app.api_route("/start", methods=["GET", "POST"])
def start_model(model: str = "qwen-3b"):
    """Compatibility endpoint for n8n orchestrator to activate/preload a model."""
    global _active_engine_key, _engine_label, _ai
    logger.info(f"🚀 [n8n /start] Requested model: {model}")
    target_engine = None
    label = model
    if "3b" in model.lower():
        target_engine = "mlx:mlx-community/Qwen2.5-3B-Instruct-4bit"
        label = "Qwen2.5-3B-Instruct-4bit (MLX)"
    elif "7b" in model.lower() or "deep" in model.lower():
        target_engine = "mlx:mlx-community/Qwen2.5-7B-Instruct-4bit"
        label = "Qwen2.5-7B-Instruct-4bit (MLX)"
    elif model.startswith("mlx:") or model.startswith("ollama:") or model in ("gemini", "openai", "anthropic"):
        target_engine = model
        label = model

    if target_engine and target_engine != _active_engine_key:
        old_engine = _active_engine_key
        _active_engine_key = target_engine
        _engine_label = label
        cfg = _load_config()
        _ai = AIClient(_active_engine_key, cfg, tools=_tools)
        if old_engine.startswith("mlx:"):
            unload_mlx_models(old_engine)
        elif old_engine.startswith("ollama:"):
            unload_ollama_models(old_engine)
        logger.info(f"🔄 [n8n /start] Switched active engine to {_engine_label}")

    return {"status": "ok", "model": model, "active_engine": _active_engine_key}

@app.api_route("/stop", methods=["GET", "POST"])
def stop_model(model: str = "qwen-3b"):
    """Compatibility endpoint for n8n orchestrator to stop/release a model from memory."""
    logger.info(f"🛑 [/stop] Requested stop for model: {model}")
    if model.startswith("mlx:") or "mlx" in model.lower():
        unload_mlx_models(model)
    else:
        unload_ollama_models(model)
    return {"status": "ok", "model": model, "stopped": True}

@app.api_route("/stop_all", methods=["GET", "POST"])
def stop_all_models():
    """Compatibility endpoint for n8n orchestrator to stop/release all models from memory."""
    logger.info("🛑 [/stop_all] Requested stop for all models")
    unload_mlx_models()
    unload_ollama_models()
    return {"status": "ok", "stopped": True}

@app.api_route("/mcp", methods=["GET", "POST"])
async def mcp_endpoint(request: Request):
    """
    Model Context Protocol (MCP) JSON-RPC 2.0 and SSE endpoint for external orchestrators (e.g. n8n).
    """
    global _mcp, _tools
    if not _mcp or not _tools:
        init_mcp_if_authenticated()

    if request.method == "GET":
        accept = request.headers.get("accept", "")
        if "text/event-stream" in accept:
            async def event_generator():
                yield "event: endpoint\ndata: /mcp\n\n"
            return StreamingResponse(event_generator(), media_type="text/event-stream")
        return {"status": "ok", "mcp": "ready" if _mcp else "unauthenticated", "tools_count": len(_tools)}

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}, "id": None})

    method = body.get("method")
    req_id = body.get("id")
    params = body.get("params", {})

    logger.info(f"🔌 [n8n /mcp] JSON-RPC method: {method} (id: {req_id})")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {"listChanged": False}
                },
                "serverInfo": {
                    "name": "cleo-cloudhealth-mcp",
                    "version": "2.1.0"
                }
            }
        }
    elif method in ("notifications/initialized", "initialized"):
        return Response(status_code=204)
    elif method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    elif method == "tools/list":
        tools_to_return = _tools if _tools else STANDARD_CH_TOOLS
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": tools_to_return
            }
        }
    elif method == "tools/call":
        tool_name = params.get("name")
        tool_args = params.get("arguments", {})
        if not tool_name:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32602, "message": "Missing tool name in params"}
            }
        try:
            if _mcp:
                res = _mcp.call_tool(tool_name, tool_args)
            else:
                res = {
                    "content": [{"type": "text", "text": "CloudHealth MCP is not authenticated. Please authenticate via the Cleo Web UI."}],
                    "isError": True
                }
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": res
            }
        except Exception as e:
            logger.error(f"❌ [n8n /mcp] Tool call error for {tool_name}: {e}")
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Error executing tool '{tool_name}': {str(e)}"}],
                    "isError": True
                }
            }
    else:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"}
        }

@app.get("/api/tools")
def get_tools():
    return {"tools": _tools}

_customers_cache: dict = {"token": None, "data": []}

@app.get("/api/customers")
def get_channel_customers():
    """Returns org list; re-fetches only when the auth token changes."""
    global _customers_cache
    token = get_access_token(interactive=False) or ""
    if _customers_cache["token"] == token and token:
        return {"customers": _customers_cache["data"]}
    if not _mcp or not token:
        return {"customers": []}
    try:
        # list_orgs works in all tenants; list_channel_customers only in partner channels
        res = _mcp.call_tool("list_orgs", {})
        txt = (res.get("content") or [{}])[0].get("text", "") if isinstance(res, dict) else ""
        if not txt:
            raise ValueError("empty list_orgs response")
        orgs = json.loads(txt)
        cleaned = [
            {"id": o.get("id", ""), "name": (o.get("name") or "").strip(), "status": "ACTIVE"}
            for o in (orgs if isinstance(orgs, list) else [])
            if isinstance(o, dict) and o.get("name")
        ]
        _customers_cache = {"token": token, "data": cleaned}
        return {"customers": cleaned}
    except Exception as e:
        logger.warning(f"[API Customers] {e}")
        return {"customers": _customers_cache["data"]}  # return stale on error

@app.get("/api/engines")
def list_engines():
    cfg = _load_config()
    
    # 1. Curated Local MLX Models (Apple Silicon)
    mlx_installed = get_installed_mlx_models()
    installed_mlx_map = {m["repo_id"]: m for m in mlx_installed}
    
    mlx_list = []
    for mm in MLX_MODELS:
        repo_id = mm["repo_id"]
        inst_meta = installed_mlx_map.get(repo_id, {})
        is_inst = inst_meta.get("downloaded", False)
        is_active_download = inst_meta.get("is_downloading", False)
        dl_info = _model_downloads.get(repo_id, {})
        is_downloading = dl_info.get("status") == "downloading" or is_active_download

        if is_downloading:
            status = "downloading"
            progress = dl_info.get("progress", 0) or 5
            status_detail = dl_info.get("status_detail") or "Downloading model weights..."
            is_downloaded = False
        elif is_inst:
            status = "completed"
            progress = 100
            status_detail = "Download complete"
            is_downloaded = True
        else:
            status = dl_info.get("status", "")
            progress = dl_info.get("progress", 0)
            status_detail = dl_info.get("status_detail", "")
            is_downloaded = False

        mlx_list.append({
            "id": mm["id"],
            "model_id": repo_id,
            "name": mm["name"],
            "size": inst_meta.get("size") if is_downloaded else mm["size"],
            "tier": mm["tier"],
            "desc": mm["desc"],
            "downloaded": is_downloaded,
            "download_status": status,
            "download_progress": progress,
            "status_detail": status_detail,
            "error": dl_info.get("error", "")
        })

    # 2. Curated Local Ollama Models
    ollama_models = get_installed_ollama_models()
    ollama_running = bool(ollama_models) or _check_ollama_alive()
    installed_names = [m.get("name", "") for m in ollama_models]

    local_list = []
    for m in LOCAL_MODELS:
        mid = m["id"]
        is_downloaded = any(mid in name or name.startswith(mid) for name in installed_names)
        dl_info = _model_downloads.get(mid, {})
        is_downloading = dl_info.get("status") == "downloading"

        if is_downloading:
            status = "downloading"
            progress = dl_info.get("progress", 0) or 5
            status_detail = dl_info.get("status_detail") or "Downloading model weights..."
            is_downloaded = False
        elif is_downloaded:
            status = "completed"
            progress = 100
            status_detail = "Download complete"
        else:
            status = dl_info.get("status", "")
            progress = dl_info.get("progress", 0)
            status_detail = dl_info.get("status_detail", "")

        local_list.append({
            "id": f"ollama:{mid}",
            "model_id": mid,
            "name": m["name"],
            "size": m["size"],
            "tier": m["tier"],
            "desc": m["desc"],
            "downloaded": is_downloaded,
            "download_status": status,
            "download_progress": progress,
            "status_detail": status_detail,
            "error": dl_info.get("error", "")
        })

    # 3. LLMs Already Available in the System (Other detected MLX & Ollama models)
    system_models = []

    # Detected MLX models in cache not in curated catalog
    catalog_mlx_repos = {mm["repo_id"] for mm in MLX_MODELS}
    for repo_id, meta in installed_mlx_map.items():
        if repo_id not in catalog_mlx_repos and meta.get("downloaded", False):
            short_name = repo_id.split("/")[-1]
            system_models.append({
                "id": f"mlx:{repo_id}",
                "model_id": repo_id,
                "name": f"{short_name} (MLX)",
                "framework": "MLX",
                "size": meta.get("size", "Local"),
                "tier": "installed",
                "desc": f"Detected in Apple Silicon cache: {repo_id}",
                "downloaded": True,
                "download_status": "completed",
                "download_progress": 100
            })

    # Detected Ollama models in daemon not in curated catalog
    catalog_ollama_ids = {lm["id"] for lm in LOCAL_MODELS}
    for om in ollama_models:
        name = om.get("name", "")
        is_curated = any(name == cid or name.startswith(cid + ":") or name.startswith(cid) for cid in catalog_ollama_ids)
        if not is_curated and name:
            size_gb = round(om.get("size", 0) / (1024**3), 1)
            details = om.get("details", {})
            param_size = details.get("parameter_size", "")
            desc_extra = f" • {param_size}" if param_size else ""
            system_models.append({
                "id": f"ollama:{name}",
                "model_id": name,
                "name": f"{name} (Ollama)",
                "framework": "Ollama",
                "size": f"{size_gb} GB" if size_gb > 0 else "Local",
                "tier": "installed",
                "desc": f"Detected in local Ollama service{desc_extra}",
                "downloaded": True,
                "download_status": "completed",
                "download_progress": 100
            })

    # 4. Public Cloud LLMs
    public_list = []
    for pe in PUBLIC_ENGINES:
        token = cfg.get(pe["env_var"]) or os.environ.get(pe["env_var"], "")
        has_token = bool(token.strip())
        public_list.append({
            "id": pe["id"],
            "name": pe["name"],
            "model": pe.get("default_model", ""),
            "env_var": pe["env_var"],
            "desc": pe["desc"],
            "configured": has_token,
            "token_preview": (token[:4] + "..." + token[-4:]) if len(token) > 8 else ("Configured" if has_token else "")
        })

    return {
        "active_engine": _active_engine_key,
        "active_label": _engine_label,
        "mlx_available": True,
        "mlx_models": mlx_list,
        "ollama_running": ollama_running,
        "ollama_installed": bool(_find_ollama_bin()),
        "local_models": local_list,
        "system_models": system_models,
        "public_engines": public_list,
        "direct_engine": {
            "id": "direct",
            "name": "Direct FinOps Router (Built-in)",
            "desc": "Built-in intelligent CloudHealth MCP query engine (No API key needed)"
        }
    }

class EngineSelection(BaseModel):
    engine: str

@app.post("/api/engine")
def set_engine(req: EngineSelection):
    global _active_engine_key, _engine_label, _ai

    if req.engine.startswith("mlx:"):
        repo_id = req.engine.removeprefix("mlx:").strip()
        if _model_downloads.get(repo_id, {}).get("status") == "downloading":
            raise HTTPException(status_code=400, detail=f"Model '{repo_id}' is currently downloading. Please wait for the download to finish.")
    elif req.engine.startswith("ollama:"):
        model_part = req.engine.split(":", 1)[1]
        if _model_downloads.get(model_part, {}).get("status") == "downloading":
            raise HTTPException(status_code=400, detail=f"Model '{model_part}' is currently downloading. Please wait for the download to finish.")

    old_engine = _active_engine_key
    _active_engine_key = req.engine
    
    if req.engine == "direct":
        _engine_label = "Direct FinOps Router"
    elif req.engine == "mlx:mlx-community/Qwen3.5-9B-MLX-4bit":
        _engine_label = "Qwen3.5-9B-4bit (MLX Default)"
    elif req.engine == "mlx:mlx-community/Qwen3.5-4B-4bit":
        _engine_label = "Qwen3.5-4B-4bit (MLX)"
    elif req.engine == "mlx:mlx-community/Qwen2.5-7B-Instruct-4bit":
        _engine_label = "Qwen2.5-7B-Instruct-4bit (MLX)"
    elif req.engine.startswith("mlx:"):
        model_part = req.engine.removeprefix("mlx:").split("/")[-1]
        _engine_label = f"{model_part} (MLX)"
    elif req.engine in ("ollama:hf.co/bartowski/Qwen_Qwen3.5-9B-GGUF:Q4_K_M", "ollama:qwen3.5:9b"):
        _engine_label = "Qwen3.5-9B-4bit (Ollama)"
    elif req.engine in ("ollama:hf.co/bartowski/Qwen_Qwen3.5-4B-GGUF:Q4_K_M", "ollama:qwen3.5:4b"):
        _engine_label = "Qwen3.5-4B-4bit (Ollama)"
    elif req.engine == "ollama:qwen2.5:7b":
        _engine_label = "Qwen2.5-7B-Instruct-4bit (Ollama)"
    elif req.engine.startswith("ollama:"):
        model_part = req.engine.split(":", 1)[1]
        _engine_label = f"Ollama ({model_part})"
    elif req.engine in ("gemini", "openai", "anthropic"):
        pe = next((p for p in PUBLIC_ENGINES if p["id"] == req.engine), None)
        _engine_label = pe["name"] if pe else req.engine.capitalize()
    else:
        _engine_label = req.engine

    _save_config({"AI_ENGINE": req.engine})
    cfg = _load_config()
    _ai = AIClient(req.engine, cfg, tools=_tools)

    # Evict previous local model from RAM/VRAM when switching to another engine
    if old_engine and old_engine != req.engine:
        if old_engine.startswith("mlx:"):
            unload_mlx_models(old_engine)
        elif old_engine.startswith("ollama:"):
            unload_ollama_models(old_engine)

    if req.engine.startswith("ollama:") and not _check_ollama_alive():
        threading.Thread(target=_start_ollama_daemon, daemon=True).start()
    logger.info(f"🔄 [AI Engine Switched] Active engine is now: {_engine_label} ({req.engine})")
    return {"status": "ok", "engine": req.engine, "label": _engine_label}

class DownloadRequest(BaseModel):
    model: str

@app.post("/api/models/download")
def download_model(req: DownloadRequest):
    model = req.model
    if model.startswith("mlx:") or "mlx-community" in model or "mlx" in model:
        repo_id = model.removeprefix("mlx:").strip()
        existing = _model_downloads.get(repo_id, {})
        if existing.get("status") == "downloading":
            return {"status": "already_downloading", "progress": existing.get("progress", 0)}
        t = threading.Thread(target=_bg_pull_mlx_model, args=(repo_id,), daemon=True)
        t.start()
        return {"status": "started", "model": repo_id, "type": "mlx"}

    existing = _model_downloads.get(model, {})
    if existing.get("status") == "downloading":
        return {"status": "already_downloading", "progress": existing.get("progress", 0)}

    t = threading.Thread(target=_bg_pull_model, args=(model,), daemon=True)
    t.start()
    return {"status": "started", "model": model, "type": "ollama"}

@app.get("/api/models/download-status")
def get_download_status():
    return {"downloads": _model_downloads}

class TokenUpdateRequest(BaseModel):
    engine: str
    token: str

@app.post("/api/tokens")
def save_token(req: TokenUpdateRequest):
    global _ai
    env_map = {
        "gemini": "GEMINI_API_KEY",
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY"
    }
    env_var = env_map.get(req.engine.lower())
    if not env_var:
        raise HTTPException(status_code=400, detail=f"Unknown public LLM engine: {req.engine}")

    _save_config({env_var: req.token.strip()})
    cfg = _load_config()
    if _ai:
        _ai.cfg = cfg
    logger.info(f"🔑 [Token Saved] Updated token for engine: {req.engine}")
    return {"status": "ok", "engine": req.engine}

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    history: Optional[list[dict]] = None

class ToolCallLog(BaseModel):
    tool: str
    arguments: dict
    result_snippet: str

class ChatResponse(BaseModel):
    response: str
    thinking: Optional[str] = None
    session_id: str
    tool_calls: list[ToolCallLog]
    title: Optional[str] = None
    duration_secs: Optional[float] = None
    tokens: Optional[int] = None
    tokens_per_sec: Optional[float] = None

_cancelled_sessions: set[str] = set()

class StopChatRequest(BaseModel):
    session_id: Optional[str] = None

@app.post("/chat/stop")
def stop_chat(req: StopChatRequest):
    if req.session_id:
        _cancelled_sessions.add(req.session_id)
        logger.info(f"[Chat] Stop signal received for session: {req.session_id}")
    return {"status": "ok", "stopped": True}

@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    global _mcp, _ai, _tools, _last_activity
    _last_activity = time.time()  # reset idle watchdog countdown
    if not _mcp or not _tools:
        if not init_mcp_if_authenticated():
            raise HTTPException(
                status_code=401, 
                detail="CloudHealth is not authenticated. Please click 'Connect CloudHealth' in the top header."
            )

    if not _ai:
        raise HTTPException(status_code=503, detail="AI engine is initializing.")

    # ponytail: reset fallback flag each request so transient errors don't permanently poison channelCustomerId queries
    if _mcp:
        _mcp._use_fallback = False

    # Session continuity (like Gemini):
    # If session_id is omitted, empty, or "active", continue the most recent ongoing session.
    # Only spawn a new session if "new" is explicitly requested or if no session exists yet.
    session_id = req.session_id
    if session_id in (None, "", "active", "current"):
        session_id = _get_active_session_id()
    if not session_id or session_id == "new":
        session_id = str(uuid.uuid4())

    _cancelled_sessions.discard(session_id)
    now_iso = datetime.now(timezone.utc).isoformat()

    if session_id not in _sessions:
        title = _generate_chat_title(req.message)
        _sessions[session_id] = {
            "id": session_id,
            "title": title,
            "created_at": now_iso,
            "updated_at": now_iso,
            "messages": [
                {"role": "system", "content": build_system_prompt(_tools)}
            ]
        }
        if req.history:
            for h in req.history:
                if h.get("role") in ("user", "assistant") and h.get("content"):
                    _sessions[session_id]["messages"].append({"role": h["role"], "content": h["content"]})

    session_entry = _sessions[session_id]
    if isinstance(session_entry, list):
        session_entry = _normalize_session(session_id, session_entry)
        _sessions[session_id] = session_entry

    messages = session_entry["messages"]

    # Refresh system prompt with live real-time calendar and prompt rules
    if messages and messages[0].get("role") == "system":
        messages[0]["content"] = build_system_prompt(_tools)

    # Auto-title session if it was previously untitled
    if session_entry.get("title") in ("New Conversation", "New Chat", "Untitled Chat", ""):
        session_entry["title"] = _generate_chat_title(req.message)

    messages.append({"role": "user", "content": req.message})
    session_entry["updated_at"] = now_iso

    tool_log: list[ToolCallLog] = []
    original_call_tool = _mcp.call_tool

    def tracked_call_tool(name: str, arguments: dict) -> dict:
        if session_id in _cancelled_sessions:
            raise RuntimeError("Query cancelled by user.")
        result = original_call_tool(name, arguments)
        tool_log.append(ToolCallLog(
            tool=name,
            arguments=arguments,
            result_snippet=str(result)[:400]
        ))
        return result

    _mcp.call_tool = tracked_call_tool
    t0 = time.perf_counter()
    try:
        response = run_agent_turn(_mcp, _ai, messages)
    except Exception as e:
        if session_id in _cancelled_sessions or "cancelled by user" in str(e).lower():
            logger.info(f"[Chat] Query cancelled by user for session {session_id}")
            if messages and messages[-1].get("role") == "user" and messages[-1].get("content") == req.message:
                messages.pop()
            raise HTTPException(status_code=499, detail="Query cancelled by user.")
        raise
    finally:
        _mcp.call_tool = original_call_tool
        _cancelled_sessions.discard(session_id)

    duration_secs = max(0.01, round(time.perf_counter() - t0, 2))
    stats = getattr(_ai, "last_stats", {}) or {}
    total_tokens = stats.get("tokens") or (stats.get("prompt_tokens", 0) + stats.get("completion_tokens", 0))
    if not total_tokens:
        p_tok = estimate_token_count(" ".join([m.get("content", "") for m in messages if isinstance(m, dict)]))
        c_tok = estimate_token_count(response)
        total_tokens = p_tok + c_tok
        tokens_per_sec = round(c_tok / max(duration_secs, 0.05), 1)
    else:
        tokens_per_sec = stats.get("tokens_per_sec") or round(stats.get("completion_tokens", total_tokens) / max(duration_secs, 0.05), 1)

    now_ts = int(datetime.now(timezone.utc).timestamp())

    # Separate any internal reasoning / thinking process so the UI stays clean
    thinking = getattr(_ai, "last_thinking", "") or ""
    clean_resp, extra_thinking = split_thinking_and_response(response)
    if extra_thinking:
        thinking = (thinking + "\n\n" + extra_thinking).strip() if thinking else extra_thinking
        response = clean_resp

    # Persist assistant response to session messages for multi-turn conversational context
    messages.append({
        "role": "assistant",
        "content": response,
        "thinking": thinking,  # Preserved for export and troubleshooting
        "timestamp": now_ts,
        "duration_secs": duration_secs,
        "tokens": total_tokens,
        "tokens_per_sec": tokens_per_sec,
        "tool_calls": [t.dict() for t in tool_log]
    })

    if len(messages) > 40:
        messages[:] = [messages[0]] + messages[-30:]

    session_entry["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save_sessions()

    return ChatResponse(
        response=response,
        thinking=thinking,
        session_id=session_id,
        tool_calls=tool_log,
        title=session_entry.get("title"),
        duration_secs=duration_secs,
        tokens=total_tokens,
        tokens_per_sec=tokens_per_sec
    )

@app.get("/api/sessions")
def list_sessions():
    summaries = []
    sorted_items = sorted(
        _sessions.items(),
        key=lambda item: (
            1 if (isinstance(item[1], dict) and item[1].get("pinned")) else 0,
            item[1].get("updated_at", "") if isinstance(item[1], dict) else ""
        ),
        reverse=True
    )
    for sid, s in sorted_items[:MAX_SAVED_SESSIONS]:
        if isinstance(s, list):
            s = _normalize_session(sid, s)
            _sessions[sid] = s
        msgs = [m for m in s.get("messages", []) if m.get("role") in ("user", "assistant")]
        msg_count = len(msgs)
        last_msg = ""
        for m in reversed(msgs):
            if m.get("content"):
                last_msg = m["content"][:100]
                break
        summaries.append({
            "id": sid,
            "title": s.get("title", "New Conversation"),
            "created_at": s.get("created_at", ""),
            "updated_at": s.get("updated_at", ""),
            "message_count": msg_count,
            "preview": last_msg,
            "pinned": bool(s.get("pinned", False))
        })
    return {"sessions": summaries, "total": len(_sessions), "max_limit": MAX_SAVED_SESSIONS}

@app.get("/api/session/{session_id}")
def get_session(session_id: str):
    if session_id not in _sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    s = _sessions[session_id]
    if isinstance(s, list):
        s = _normalize_session(session_id, s)
        _sessions[session_id] = s
    client_messages = []
    for m in s.get("messages", []):
        if m.get("role") not in ("user", "assistant"):
            continue
        item = {
            "role": m["role"],
            "content": m["content"],
            "timestamp": m.get("timestamp"),
            "duration_secs": m.get("duration_secs"),
            "tokens": m.get("tokens"),
            "tokens_per_sec": m.get("tokens_per_sec"),
            "tool_calls": m.get("tool_calls", [])
        }
        if item["role"] == "assistant":
            if item["tokens"] is None and item["content"]:
                item["tokens"] = estimate_token_count(item["content"])
            if item["duration_secs"] is None and item["tokens"]:
                item["duration_secs"] = max(0.6, round(item["tokens"] / 75.0, 1))
            if item["tokens_per_sec"] is None and item["duration_secs"] and item["tokens"]:
                item["tokens_per_sec"] = round(item["tokens"] / item["duration_secs"], 1)
        client_messages.append(item)
    return {
        "id": session_id,
        "title": s.get("title", "New Conversation"),
        "created_at": s.get("created_at", ""),
        "updated_at": s.get("updated_at", ""),
        "messages": client_messages,
        "pinned": bool(s.get("pinned", False))
    }

@app.post("/api/session/{session_id}/pin")
def toggle_pin_session(session_id: str):
    if session_id not in _sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    s = _sessions[session_id]
    if isinstance(s, list):
        s = _normalize_session(session_id, s)
        _sessions[session_id] = s
    s["pinned"] = not s.get("pinned", False)
    _save_sessions()
    return {"status": "ok", "session_id": session_id, "pinned": s["pinned"]}

@app.delete("/session/{session_id}")
@app.delete("/api/session/{session_id}")
def clear_session(session_id: str):
    if session_id in _sessions:
        del _sessions[session_id]
        _save_sessions()
    return {"status": "cleared", "session_id": session_id}

@app.delete("/api/sessions")
def clear_all_sessions():
    _sessions.clear()
    _save_sessions()
    return {"status": "cleared", "total": 0}

# ── Self-Learning Memory ───────────────────────────────────────────────────────

from cleo_memory import get_memory as _get_memory

class FeedbackRequest(BaseModel):
    session_id: str
    message_index: int
    rating: str          # "good" | "bad"
    correction: Optional[str] = None   # filled when rating == "bad"

@app.post("/api/feedback")
def submit_feedback(req: FeedbackRequest):
    mem = _get_memory()
    session = _sessions.get(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    msgs = session.get("messages", [])
    # Find the user msg and assistant msg around the given index
    user_msgs  = [m for m in msgs if m.get("role") == "user"]
    asst_msgs  = [m for m in msgs if m.get("role") == "assistant"]
    idx = req.message_index
    user_query = user_msgs[idx]["content"] if idx < len(user_msgs) else ""
    asst_reply = asst_msgs[idx]["content"] if idx < len(asst_msgs) else ""

    if req.rating == "good":
        # Summarise the reply to ~200 chars for the memory store
        summary = asst_reply[:200].replace("\n", " ").strip()
        mem.record_good_pattern(user_query, summary)
        return {"status": "recorded", "type": "good_pattern"}
    elif req.rating == "bad" and req.correction:
        mem.record_correction(user_query, req.correction)
        return {"status": "recorded", "type": "correction"}
    return {"status": "ignored"}

@app.get("/api/memory")
def get_memory_entries():
    return {"entries": _get_memory().all_entries()}

@app.delete("/api/memory/{entry_id}")
def delete_memory_entry(entry_id: str):
    deleted = _get_memory().delete(entry_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"status": "deleted"}

# ── Auto-Update via GitHub ────────────────────────────────────────────────────

CLEO_GITHUB_REPO = os.environ.get("CLEO_GITHUB_REPO", "digitalsuni-cloud/cleo")
_CLEO_ROOT       = os.path.dirname(os.path.abspath(__file__))
_update_state: dict = {"status": "idle", "latest_sha": "", "current_sha": "", "update_available": False, "message": ""}

def _git_sha() -> str:
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=_CLEO_ROOT, stderr=subprocess.DEVNULL
        ).decode().strip()
        if sha:
            return sha
    except Exception:
        pass
    ver_path = os.path.join(_CLEO_ROOT, ".version")
    if os.path.exists(ver_path):
        try:
            with open(ver_path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            pass
    return ""

def _get_latest_remote_sha() -> str:
    """Retrieve latest commit SHA via git ls-remote (fast, no rate-limits) or GitHub API."""
    try:
        res = subprocess.run(
            ["git", "ls-remote", f"https://github.com/{CLEO_GITHUB_REPO}.git", "refs/heads/main"],
            capture_output=True, text=True, timeout=10
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip().split()[0]
    except Exception:
        pass
    try:
        api_url = f"https://api.github.com/repos/{CLEO_GITHUB_REPO}/commits/main"
        req = urllib.request.Request(api_url, headers={"Accept": "application/vnd.github+json", "User-Agent": "Cleo-FinOps/2.1"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        return data.get("sha", "")
    except Exception:
        pass
    return ""

def _apply_curl_update() -> tuple[bool, str]:
    """Download latest repository archive via curl and extract into _CLEO_ROOT."""
    tarball_url = f"https://github.com/{CLEO_GITHUB_REPO}/archive/refs/heads/main.tar.gz"
    # 1. Standard curl piped to tar (preserves .env and local untracked configs)
    try:
        cmd = f"curl -sL --max-time 90 '{tarball_url}' | tar -xz --strip-components=1 -C '{_CLEO_ROOT}'"
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
        if res.returncode == 0:
            return True, "Successfully downloaded and applied latest update via curl."
        curl_err = res.stderr.strip() or f"curl exit code {res.returncode}"
    except Exception as e:
        curl_err = str(e)

    # 2. Python in-memory urllib + tarfile fallback
    try:
        import io, tarfile
        req = urllib.request.Request(tarball_url, headers={"User-Agent": "Cleo-FinOps/2.1"})
        with urllib.request.urlopen(req, timeout=90) as resp:
            tar_bytes = io.BytesIO(resp.read())
        with tarfile.open(fileobj=tar_bytes, mode="r:gz") as tar:
            for member in tar.getmembers():
                parts = member.name.split("/", 1)
                if len(parts) > 1 and parts[1]:
                    member.name = parts[1]
                    tar.extract(member, path=_CLEO_ROOT)
        return True, "Successfully updated via Python tarfile extraction."
    except Exception as pe:
        return False, f"Curl failed ({curl_err}) and python extraction failed ({pe})"

def _check_update_bg():
    """Background: compare local HEAD to GitHub latest commit SHA on main."""
    global _update_state
    try:
        current = _git_sha()
        latest = _get_latest_remote_sha()
        if not latest:
            _update_state = {
                "status": "error",
                "latest_sha": "",
                "current_sha": current[:7] if current else "unknown",
                "update_available": False,
                "message": "Could not check remote repository status",
            }
            return

        update_available = (not current) or (latest != current)
        _update_state = {
            "status": "ok",
            "current_sha": current[:7] if current else "unknown",
            "latest_sha": latest[:7],
            "update_available": update_available,
            "message": "Update available — click 'Update' to pull the latest code." if update_available else "Cleo is up to date.",
        }
        logger.info(f"[Update] current={current[:7] if current else 'unknown'} latest={latest[:7]} update_available={update_available}")
    except Exception as e:
        _update_state = {"status": "error", "latest_sha": "", "current_sha": _git_sha()[:7], "update_available": False, "message": str(e)}
        logger.warning(f"[Update] Check failed: {e}")

@app.get("/api/update/check")
def check_update():
    """Check GitHub for a newer version of Cleo."""
    # Run synchronously so UI gets a fresh result on demand; background check runs at startup too.
    _check_update_bg()
    return _update_state

@app.post("/api/update/apply")
def apply_update():
    """Pull the latest code from GitHub (via git pull or curl fallback) and restart."""
    current_sha = _git_sha()
    latest_sha = _get_latest_remote_sha()
    method_used = "git"
    output = ""
    git_ok = False

    # 1. Attempt git pull if this is a git repository
    if os.path.exists(os.path.join(_CLEO_ROOT, ".git")):
        try:
            # First attempt: fast-forward pull
            result = subprocess.run(
                ["git", "pull", "--ff-only"],
                cwd=_CLEO_ROOT, capture_output=True, text=True, timeout=45
            )
            if result.returncode == 0:
                git_ok = True
                output = result.stdout.strip()
            else:
                logger.warning(f"[Update] 'git pull --ff-only' failed ({result.stderr.strip()}). Trying 'git pull origin main'...")
                res_main = subprocess.run(
                    ["git", "pull", "origin", "main"],
                    cwd=_CLEO_ROOT, capture_output=True, text=True, timeout=45
                )
                if res_main.returncode == 0:
                    git_ok = True
                    output = res_main.stdout.strip()
                else:
                    logger.warning(f"[Update] 'git pull origin main' failed ({res_main.stderr.strip()}). Trying 'git fetch & reset'...")
                    res_fetch = subprocess.run(
                        ["git", "fetch", "origin", "main"],
                        cwd=_CLEO_ROOT, capture_output=True, text=True, timeout=45
                    )
                    if res_fetch.returncode == 0:
                        res_hard = subprocess.run(
                            ["git", "reset", "--hard", "origin/main"],
                            cwd=_CLEO_ROOT, capture_output=True, text=True, timeout=30
                        )
                        if res_hard.returncode == 0:
                            git_ok = True
                            output = res_hard.stdout.strip()
        except Exception as e:
            logger.warning(f"[Update] Git pull execution error: {e}")

    # 2. If git failed or directory is not a git repository, fallback to curl download
    if not git_ok:
        logger.info("[Update] git pull failed or unavailable. Falling back to curl download...")
        method_used = "curl"
        ok, msg = _apply_curl_update()
        if not ok:
            raise HTTPException(status_code=500, detail=f"Update failed: {msg}")
        output = msg

    new_sha = _git_sha()
    if not new_sha and latest_sha:
        new_sha = latest_sha
    if new_sha:
        try:
            with open(os.path.join(_CLEO_ROOT, ".version"), "w", encoding="utf-8") as f:
                f.write(new_sha + "\n")
        except Exception:
            pass

    logger.info(f"[Update] Applied via {method_used}: {current_sha[:7] if current_sha else 'unknown'} → {new_sha[:7] if new_sha else 'latest'}")
    return {
        "status": "updated",
        "method": method_used,
        "from_sha": current_sha[:7] if current_sha else "unknown",
        "to_sha": new_sha[:7] if new_sha else "latest",
        "output": output,
        "message": f"Update applied successfully (via {method_used}). Restart Cleo (Ctrl+C → python3 cleo_server.py) to load the new version.",
    }

# ── Port Fallback ─────────────────────────────────────────────────────────────

_PORT_CANDIDATES = [8080, 8081, 8082, 8083, 8090, 8888, 9090, 9191]

def _find_free_port(preferred: int = 8080) -> int:
    """Return `preferred` if it's free, otherwise the first free candidate."""
    for port in [preferred] + [p for p in _PORT_CANDIDATES if p != preferred]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    # Last resort: let the OS pick
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

def _auto_update_on_startup():
    """Checks remote GitHub repo on startup and automatically fetches & applies updates."""
    if os.environ.get("CLEO_NO_UPDATE", "").lower() in ("1", "true", "yes") or "--no-update" in sys.argv:
        return

    if os.environ.get("_CLEO_RESTARTED_FROM_UPDATE") == "1":
        os.environ.pop("_CLEO_RESTARTED_FROM_UPDATE", None)
        return

    try:
        current_sha = _git_sha()
        latest_sha = _get_latest_remote_sha()
        if not latest_sha:
            return

        if current_sha and current_sha[:7] == latest_sha[:7]:
            return

        print(f"🔄 [Auto-Update] Newer version available ({current_sha[:7] if current_sha else 'unknown'} → {latest_sha[:7]}). Fetching latest code...")

        git_ok = False
        if os.path.exists(os.path.join(_CLEO_ROOT, ".git")):
            try:
                res = subprocess.run(["git", "pull", "--ff-only"], cwd=_CLEO_ROOT, capture_output=True, text=True, timeout=30)
                if res.returncode == 0:
                    git_ok = True
                else:
                    res2 = subprocess.run(["git", "pull", "origin", "main"], cwd=_CLEO_ROOT, capture_output=True, text=True, timeout=30)
                    if res2.returncode == 0:
                        git_ok = True
                    else:
                        subprocess.run(["git", "fetch", "origin", "main"], cwd=_CLEO_ROOT, capture_output=True, timeout=30)
                        res3 = subprocess.run(["git", "reset", "--hard", "origin/main"], cwd=_CLEO_ROOT, capture_output=True, timeout=15)
                        if res3.returncode == 0:
                            git_ok = True
            except Exception:
                pass

        if not git_ok:
            ok, msg = _apply_curl_update()
            if not ok:
                print(f"⚠️  [Auto-Update] Could not apply update: {msg}")
                return

        new_sha = _git_sha() or latest_sha
        try:
            with open(os.path.join(_CLEO_ROOT, ".version"), "w", encoding="utf-8") as f:
                f.write(new_sha + "\n")
        except Exception:
            pass

        print(f"🚀 [Auto-Update] Successfully updated to {new_sha[:7]}! Reloading Cleo...")
        env = os.environ.copy()
        env["_CLEO_RESTARTED_FROM_UPDATE"] = "1"
        if os.name == "nt":
            subprocess.Popen([sys.executable] + sys.argv, env=env)
            sys.exit(0)
        else:
            os.execve(sys.executable, [sys.executable] + sys.argv, env)
    except Exception as e:
        print(f"⚠️  [Auto-Update] Note: update check skipped ({e}). Continuing startup...")

# ── GUI Dashboard ─────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def serve_gui():
    ui_path = os.path.join(os.path.dirname(__file__), "cleo_ui.html")
    if os.path.exists(ui_path):
        with open(ui_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Cleo UI template not found</h1>", status_code=500)

if __name__ == "__main__":
    _auto_update_on_startup()

    _default_level = os.environ.get("CLEO_LOG_LEVEL", "warning").lower()
    _uvicorn_log_level = "debug" if os.environ.get("CLEO_VERBOSE", "").lower() in ("1", "true", "yes") else _default_level

    # Port resolution
    _preferred_port = int(os.environ.get("PORT", 8080))
    _port = _find_free_port(_preferred_port)
    if _port != _preferred_port:
        print(f"⚠️  Port {_preferred_port} is in use. Starting Cleo on port {_port} instead.")
    print(f"🌐 Cleo Web UI → http://127.0.0.1:{_port}")

    # Background update check — non-blocking, fires 5 s after startup
    def _delayed_update_check():
        time.sleep(5)
        _check_update_bg()
    threading.Thread(target=_delayed_update_check, daemon=True, name="update-check").start()

    uvicorn.run("cleo_server:app", host="127.0.0.1", port=_port, reload=False, log_level=_uvicorn_log_level)

