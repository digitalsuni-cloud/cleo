"""
cleo_llm.py — LLM Engine Integration, Model Catalogs, and Hardware Detection.
Zero external runtime dependencies: uses Python standard library (urllib, platform, ctypes)
and optional local acceleration runtimes (mlx_lm on Apple Silicon, Ollama on POSIX/Windows).
"""
import os
import sys
import re
import json
import time
import platform
import subprocess
import urllib.request
import urllib.parse
import urllib.error
from typing import Optional, Dict, Any, List

try:
    from cleo_logger import get_logger
    logger = get_logger("llm")
except ImportError:
    import logging
    logger = logging.getLogger("cleo.llm")
    logger.setLevel(logging.INFO)

# ── Local & Public Model Catalogs ──────────────────────────────────────────────
MLX_MODELS = [
    {"id": "mlx:mlx-community/Qwen3.5-9B-MLX-4bit", "repo_id": "mlx-community/Qwen3.5-9B-MLX-4bit", "name": "Qwen3.5-9B-4bit (MLX)", "size": "5.5 GB", "tier": "default", "desc": "Default recommended Apple Silicon Metal model (~5.5 GB RAM)"},
    {"id": "mlx:mlx-community/Qwen3.5-4B-4bit", "repo_id": "mlx-community/Qwen3.5-4B-4bit", "name": "Qwen3.5-4B-4bit (MLX)", "size": "2.6 GB", "tier": "smaller", "desc": "Ultra-fast lightweight model for Apple Silicon (~2.6 GB RAM)"},
]

LOCAL_MODELS = [
    {"id": "qwen2.5:7b", "name": "Qwen2.5-7B-Instruct-4bit", "size": "4.7 GB", "tier": "default", "desc": "Default recommended local model for FinOps (~5 GB RAM)"},
    {"id": "qwen2.5:3b", "name": "Qwen2.5-3B-Instruct-4bit", "size": "1.9 GB", "tier": "smaller", "desc": "Lightweight & ultra-fast local model (~2 GB RAM)"},
    {"id": "hf.co/bartowski/Qwen_Qwen3.5-9B-GGUF:Q4_K_M", "name": "Qwen3.5-9B-4bit (GGUF)", "size": "5.5 GB", "tier": "bigger", "desc": "Qwen3.5 local model via HuggingFace (~5.5 GB RAM)"},
    {"id": "hf.co/bartowski/Qwen_Qwen3.5-4B-GGUF:Q4_K_M", "name": "Qwen3.5-4B-4bit (GGUF)", "size": "2.6 GB", "tier": "smaller", "desc": "Lightweight Qwen3.5 local model via HuggingFace (~2.6 GB RAM)"},
]

PUBLIC_ENGINES = [
    {"id": "gemini", "name": "Google Gemini", "default_model": "gemini-3.5-flash", "env_var": "GEMINI_API_KEY", "desc": "Google Gemini Cloud AI"},
    {"id": "openai", "name": "OpenAI", "default_model": "gpt-4o", "env_var": "OPENAI_API_KEY", "desc": "OpenAI Cloud AI"},
    {"id": "anthropic", "name": "Anthropic Claude", "default_model": "claude-3-7-sonnet-latest", "env_var": "ANTHROPIC_API_KEY", "desc": "Anthropic Claude Cloud AI"},
]

AI_ENGINES = {
    "direct": ("Direct FinOps Router", "direct"),
    "mlx:mlx-community/Qwen3.5-9B-MLX-4bit": ("Qwen3.5-9B-4bit (MLX Default)", "mlx:mlx-community/Qwen3.5-9B-MLX-4bit"),
    "mlx:mlx-community/Qwen3.5-4B-4bit": ("Qwen3.5-4B-4bit (MLX)", "mlx:mlx-community/Qwen3.5-4B-4bit"),
    "ollama:hf.co/bartowski/Qwen_Qwen3.5-9B-GGUF:Q4_K_M": ("Qwen3.5-9B-4bit (Ollama)", "ollama:hf.co/bartowski/Qwen_Qwen3.5-9B-GGUF:Q4_K_M"),
    "ollama:hf.co/bartowski/Qwen_Qwen3.5-4B-GGUF:Q4_K_M": ("Qwen3.5-4B-4bit (Ollama)", "ollama:hf.co/bartowski/Qwen_Qwen3.5-4B-GGUF:Q4_K_M"),
    # Compatibility aliases
    "mlx:mlx-community/Qwen2.5-7B-Instruct-4bit": ("Qwen2.5-7B-Instruct-4bit (MLX)", "mlx:mlx-community/Qwen2.5-7B-Instruct-4bit"),
    "mlx:mlx-community/Qwen2.5-3B-Instruct-4bit": ("Qwen2.5-3B-Instruct-4bit (MLX)", "mlx:mlx-community/Qwen2.5-3B-Instruct-4bit"),
    "ollama:qwen2.5:7b": ("Qwen2.5-7B-Instruct-4bit (Ollama)", "ollama:qwen2.5:7b"),
    "ollama:qwen2.5:3b": ("Qwen2.5-3B-Instruct-4bit (Ollama)", "ollama:qwen2.5:3b"),
    "gemini": ("Google Gemini", "gemini"),
    "openai": ("OpenAI", "openai"),
    "anthropic": ("Anthropic Claude", "anthropic"),
}

# ── Auto-Detect System Hardware & Model Recommendation ─────────────────────────
def detect_system_info() -> dict:
    """
    Auto-detects the operating system, CPU architecture, and total physical memory (RAM).
    Works reliably on macOS (Apple Silicon / Intel), Windows, and Linux via standard library.
    Suggests the optimal local model tier based on detected RAM headroom and hardware acceleration.
    """
    sys_name = platform.system()
    machine = platform.machine()
    is_mac = (sys_name == "Darwin")
    is_apple_silicon = is_mac and machine in ("arm64", "aarch64")
    is_windows = (sys_name == "Windows")
    is_linux = (sys_name == "Linux")

    # Friendly OS display label
    if is_apple_silicon:
        os_display = "macOS (Apple Silicon)"
        chip_label = "Apple Silicon Unified Memory"
        icon = "🍎"
    elif is_mac:
        os_display = "macOS (Intel x86_64)"
        chip_label = "Intel x86_64"
        icon = "🍎"
    elif is_windows:
        bit_str = "64-bit" if machine in ("AMD64", "x86_64", "ARM64") else "32-bit"
        os_display = f"Windows ({bit_str})"
        chip_label = f"Windows {machine}"
        icon = "🪟"
    elif is_linux:
        os_display = f"Linux ({machine})"
        chip_label = f"Linux {machine}"
        icon = "🐧"
    else:
        os_display = f"{sys_name} ({machine})"
        chip_label = f"{sys_name} {machine}"
        icon = "💻"

    total_ram_bytes = 0
    try:
        if is_windows:
            import ctypes
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            total_ram_bytes = stat.ullTotalPhys
        else:
            # POSIX (macOS & Linux)
            total_ram_bytes = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except Exception:
        pass

    if total_ram_bytes <= 0:
        if is_mac:
            try:
                out = subprocess.check_output(["sysctl", "-n", "hw.memsize"]).decode().strip()
                total_ram_bytes = int(out)
            except Exception:
                pass
        elif is_linux:
            try:
                with open("/proc/meminfo", "r") as f:
                    for line in f:
                        if line.startswith("MemTotal:"):
                            kb = int(line.split()[1])
                            total_ram_bytes = kb * 1024
                            break
            except Exception:
                pass

    total_ram_gb = round(total_ram_bytes / (1024 ** 3), 1) if total_ram_bytes > 0 else 0.0

    # Determine recommended model based on OS & detected memory
    if is_apple_silicon:
        if total_ram_gb >= 24:
            rec_id = "mlx:mlx-community/Qwen3.5-9B-MLX-4bit"
            rec_name = "Qwen3.5-9B-4bit (MLX)"
            rec_reason = f"Apple Silicon with {total_ram_gb} GB Unified Memory detected. Qwen3.5-9B fits natively in unified memory with high-speed Metal acceleration."
        elif total_ram_gb >= 12:
            rec_id = "mlx:mlx-community/Qwen3.5-9B-MLX-4bit"
            rec_name = "Qwen3.5-9B-4bit (MLX)"
            rec_reason = f"Apple Silicon with {total_ram_gb} GB Unified Memory detected. Qwen3.5-9B (~5.5 GB RAM) is the recommended sweet spot for local FinOps reasoning."
        else:
            rec_id = "mlx:mlx-community/Qwen3.5-4B-4bit"
            rec_name = "Qwen3.5-4B-4bit (MLX)"
            rec_reason = f"Apple Silicon with {total_ram_gb} GB Unified Memory detected. Qwen3.5-4B (~2.6 GB RAM) is lightweight and runs fast with zero memory pressure."
    else:
        # Windows / Linux / Intel Mac (Ollama Runtime)
        os_label = "Windows" if is_windows else ("Linux" if is_linux else "Intel Mac")
        if total_ram_gb >= 24:
            rec_id = "ollama:qwen2.5:7b"
            rec_name = "Qwen2.5-7B-Instruct-4bit (Ollama)"
            rec_reason = f"{os_label} with {total_ram_gb} GB RAM detected. High memory headroom supports Qwen2.5-7B (~4.7 GB) or Qwen3.5-9B for complex multi-cloud query planning."
        elif total_ram_gb >= 12:
            rec_id = "ollama:qwen2.5:7b"
            rec_name = "Qwen2.5-7B-Instruct-4bit (Ollama)"
            rec_reason = f"{os_label} with {total_ram_gb} GB RAM detected. Qwen2.5-7B (~4.7 GB) is the recommended sweet spot for local inference with ample headroom for OS tasks."
        elif total_ram_gb > 0:
            rec_id = "ollama:qwen2.5:3b"
            rec_name = "Qwen2.5-3B-Instruct-4bit (Ollama)"
            rec_reason = f"{os_label} with {total_ram_gb} GB RAM detected. Qwen2.5-3B (~1.9 GB) is lightweight and ensures fast execution without memory paging."
        else:
            rec_id = "ollama:qwen2.5:7b"
            rec_name = "Qwen2.5-7B-Instruct-4bit (Ollama)"
            rec_reason = f"{os_label} detected. Qwen2.5-7B is the standard recommended local model."

    return {
        "os": sys_name,
        "os_display": os_display,
        "icon": icon,
        "machine": machine,
        "chip_label": chip_label,
        "is_apple_silicon": is_apple_silicon,
        "is_windows": is_windows,
        "is_mac": is_mac,
        "is_linux": is_linux,
        "total_ram_bytes": total_ram_bytes,
        "total_ram_gb": total_ram_gb,
        "recommended_engine_id": rec_id,
        "recommended_model_name": rec_name,
        "recommendation_reason": rec_reason
    }

# ── Local Apple Silicon (MLX) Support ─────────────────────────────────────────
_mlx_models_cache: Dict[str, Any] = {}

def get_installed_mlx_models() -> list[dict]:
    """Scans local Hugging Face cache for downloaded MLX models (equivalent to mlx_lm.manage --scan)."""
    if platform.system() != "Darwin" or platform.machine() not in ("arm64", "aarch64"):
        return []
    installed = {}
    try:
        from huggingface_hub import scan_cache_dir
        info = scan_cache_dir()
        for repo in info.repos:
            if repo.repo_type == "model" and ("mlx" in repo.repo_id.lower() or "mlx" in str(repo.repo_path).lower()):
                blobs_p = os.path.join(str(repo.repo_path), "blobs")
                has_incomplete = False
                if os.path.isdir(blobs_p):
                    try:
                        has_incomplete = any(f.endswith(".incomplete") or f.endswith(".tmp") for f in os.listdir(blobs_p))
                    except Exception:
                        pass
                
                # Model is only complete if it has no incomplete blobs and weights exceed 500MB
                is_complete = not has_incomplete and repo.size_on_disk >= (500 * 1024 * 1024)
                installed[repo.repo_id] = {
                    "repo_id": repo.repo_id,
                    "size": repo.size_on_disk_str if is_complete else "Incomplete",
                    "path": str(repo.repo_path),
                    "downloaded": is_complete,
                    "is_downloading": has_incomplete
                }
    except Exception as e:
        logger.debug(f"[MLX Scan] scan_cache_dir: {e}")

    # Fallback to direct directory scan of ~/.cache/huggingface/hub/models--*
    hub_dir = os.path.expanduser("~/.cache/huggingface/hub")
    if os.path.isdir(hub_dir):
        for entry in os.listdir(hub_dir):
            if entry.startswith("models--") and "mlx" in entry.lower():
                parts = entry[8:].split("--")
                repo_id = "/".join(parts)
                if repo_id not in installed:
                    full_p = os.path.join(hub_dir, entry)
                    size_bytes = 0
                    has_incomplete = False
                    try:
                        blobs_p = os.path.join(full_p, "blobs")
                        if os.path.isdir(blobs_p):
                            has_incomplete = any(f.endswith(".incomplete") or f.endswith(".tmp") for f in os.listdir(blobs_p))
                        for root, _, files in os.walk(full_p):
                            for f in files:
                                size_bytes += os.path.getsize(os.path.join(root, f))
                        size_str = f"{round(size_bytes / (1024**3), 1)}G"
                    except Exception:
                        size_str = "Local"
                    
                    is_complete = not has_incomplete and size_bytes >= (500 * 1024 * 1024)
                    installed[repo_id] = {
                        "repo_id": repo_id,
                        "size": size_str if is_complete else "Incomplete",
                        "path": full_p,
                        "downloaded": is_complete,
                        "is_downloading": has_incomplete
                    }

    return list(installed.values())

def estimate_token_count(text: str) -> int:
    """Estimates LLM BPE token count for text using word/subword heuristics."""
    if not text:
        return 0
    pieces = re.findall(r"\w+|[^\w\s]", text)
    return max(1, int(len(pieces) * 1.15))

def call_mlx_generate(model_id: str, messages: list[dict], max_tokens: int = 6144, stats_out: dict = None, on_token = None) -> str:
    """Invokes local Apple Silicon MLX model using unified memory."""
    global _mlx_models_cache
    try:
        import mlx_lm
    except ImportError:
        raise RuntimeError("mlx_lm is not installed in the Python environment.")

    clean_id = model_id.removeprefix("mlx:").strip()
    if clean_id not in _mlx_models_cache:
        logger.info(f"⚡ [MLX Load] Loading {clean_id} into unified memory...")
        sys.stdout.flush()
        # Suppress HuggingFace/tqdm progress bars so Cleo's own log lines are visible.
        import os as _os
        _prev_hf_bar = _os.environ.get("HF_HUB_DISABLE_PROGRESS_BARS")
        _os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
        try:
            import huggingface_hub.utils as _hfu
            _hfu.disable_progress_bars()
        except Exception:
            pass
        _tqdm_orig = None
        try:
            import tqdm.auto as _tqdm_auto
            _tqdm_orig = _tqdm_auto.tqdm

            class _SilentTqdm(_tqdm_orig):
                def __init__(self, *a, **kw):
                    kw["disable"] = True
                    super().__init__(*a, **kw)

            _tqdm_auto.tqdm = _SilentTqdm
        except Exception:
            pass
        try:
            model, tokenizer = mlx_lm.load(clean_id)
        finally:
            # Restore tqdm and env var
            if _tqdm_orig is not None:
                try:
                    import tqdm.auto as _tqdm_auto
                    _tqdm_auto.tqdm = _tqdm_orig
                except Exception:
                    pass
            if _prev_hf_bar is None:
                _os.environ.pop("HF_HUB_DISABLE_PROGRESS_BARS", None)
            else:
                _os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = _prev_hf_bar
        _mlx_models_cache[clean_id] = (model, tokenizer)
        logger.info(f"✅ [MLX Ready] {clean_id} loaded into memory and ready.")
        sys.stdout.flush()
    else:
        logger.debug(f"[MLX Ready] {clean_id} already resident in memory.")
        model, tokenizer = _mlx_models_cache[clean_id]

    thinking_enabled = False  # disabled until thinking-strip is reliable
    clean_messages = [
        {"role": m.get("role", "user"), "content": m.get("content") or ""}
        for m in messages
        if isinstance(m, dict) and m.get("role")
    ]
    try:
        prompt = tokenizer.apply_chat_template(
            clean_messages, tokenize=False, add_generation_prompt=True, enable_thinking=False
        )
    except TypeError:
        prompt = tokenizer.apply_chat_template(clean_messages, tokenize=False, add_generation_prompt=True)
    t0 = time.perf_counter()
    if on_token is not None and hasattr(mlx_lm, "stream_generate"):
        chunks = []
        for segment in mlx_lm.stream_generate(model, tokenizer, prompt=prompt, max_tokens=max_tokens):
            c = segment.text
            chunks.append(c)
            on_token(c)
        resp = "".join(chunks)
    else:
        resp = mlx_lm.generate(model, tokenizer, prompt=prompt, max_tokens=max_tokens)
    dur = max(0.01, time.perf_counter() - t0)
    ans = resp.strip()

    if thinking_enabled:
        logger.debug(f"[MLX Raw] first 200 chars: {repr(ans[:200])}")
        if "</think>" in ans:
            ans = ans.split("</think>", 1)[1].strip()
        elif ans.startswith("<think>"):
            ans = re.sub(r"<think>.*?</think>", "", ans, flags=re.DOTALL).strip()
        else:
            salvage = re.search(
                r'\n(?:Revised (?:Bullet|Answer|Response) \d*:?|Final (?:Answer|Response|Draft):?|Here (?:are|is) the (?:final|revised|clean))',
                ans, flags=re.IGNORECASE
            )
            if salvage:
                ans = ans[salvage.start():].strip()
                logger.debug("[MLX Raw] Salvaged final section from truncated thinking block")
            else:
                logger.warning("[MLX Raw] </think> tag missing and no salvage marker found")

    if stats_out is not None:
        try:
            p_tok = len(tokenizer.encode(prompt))
            raw_tok = len(tokenizer.encode(resp.strip()))
            c_tok = len(tokenizer.encode(ans))
            stats_out["prompt_tokens"] = p_tok
            stats_out["completion_tokens"] = c_tok
            stats_out["total_tokens"] = p_tok + raw_tok
            stats_out["tokens_per_sec"] = round(raw_tok / dur, 1)
            stats_out["duration_secs"] = round(dur, 2)
            if thinking_enabled and raw_tok != c_tok:
                logger.debug(f"[MLX Stats] raw={raw_tok} tok, clean={c_tok} tok, {stats_out['tokens_per_sec']} tok/s")
        except Exception:
            pass

    return ans

def unload_mlx_models(model_id: Optional[str] = None):
    """Unloads MLX model(s) from memory and releases Apple Silicon Metal unified memory cache."""
    global _mlx_models_cache
    if not _mlx_models_cache:
        return
    if model_id:
        clean_id = model_id.removeprefix("mlx:").strip()
        if clean_id in _mlx_models_cache:
            logger.info(f"🧹 [MLX Unload] Evicting model '{clean_id}' from unified memory...")
            sys.stdout.flush()
            _mlx_models_cache.pop(clean_id, None)
    else:
        logger.info(f"🧹 [MLX Unload] Evicting {len(_mlx_models_cache)} MLX model(s) from unified memory...")
        sys.stdout.flush()
        _mlx_models_cache.clear()

    import gc
    gc.collect()
    try:
        import mlx.core as mx
        if hasattr(mx, "clear_cache"):
            mx.clear_cache()
        elif hasattr(mx, "metal") and hasattr(mx.metal, "clear_cache"):
            mx.metal.clear_cache()
    except Exception:
        pass
    logger.info("✅ [MLX Unload] MLX unified memory and Metal cache freed.")
    sys.stdout.flush()

# ── LLM Client Callers (Zero-Dependency via urllib) ──────────────────────────
def normalize_ollama_url(raw: str = None) -> str:
    """
    Normalizes OLLAMA_HOST / base URL for robust local communication across OSes (Windows, Mac, Linux).
    - Ensures http:// or https:// scheme (defaulting to http://).
    - If host is 0.0.0.0 or :: (server bind address), converts to 127.0.0.1 for client connectivity.
    - If only port is given (e.g. '11434'), expands to http://127.0.0.1:11434.
    - Replaces localhost with 127.0.0.1 on Windows to prevent IPv6 ::1 connection refused issues.
    """
    if not raw:
        raw = os.environ.get("OLLAMA_HOST", "").strip()
    if not raw:
        return "http://127.0.0.1:11434"
    raw = raw.strip().rstrip("/")
    if re.match(r'^:?\d+$', raw):
        port = raw.lstrip(":")
        return f"http://127.0.0.1:{port}"
    if not (raw.startswith("http://") or raw.startswith("https://")):
        raw = f"http://{raw}"
    try:
        parsed = urllib.parse.urlparse(raw)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 11434
        scheme = parsed.scheme or "http"
        if host in ("0.0.0.0", "::", "0"):
            host = "127.0.0.1"
        elif platform.system() == "Windows" and host == "localhost":
            host = "127.0.0.1"
        return f"{scheme}://{host}:{port}"
    except Exception:
        return raw

def get_ollama_opener():
    """
    Returns a urllib opener that explicitly bypasses system and environment proxies
    (HTTP_PROXY, HTTPS_PROXY) so local loopback requests to Ollama always reach
    the local daemon directly instead of getting intercepted or blocked by corporate proxies.
    """
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))

OLLAMA_BASE_URL = normalize_ollama_url()

def get_installed_ollama_models() -> list[dict]:
    """Returns list of installed models from local Ollama daemon."""
    candidates = [
        OLLAMA_BASE_URL,
        "http://127.0.0.1:11434",
        "http://localhost:11434"
    ]
    opener = get_ollama_opener()
    seen = set()
    for base in candidates:
        if not base or base in seen:
            continue
        seen.add(base)
        try:
            req = urllib.request.Request(f"{base}/api/tags", headers={"Accept": "application/json", "User-Agent": "Cleo-FinOps/1.0"})
            with opener.open(req, timeout=2.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("models", [])
        except Exception:
            continue
    return []

def call_ollama_chat(model: str, messages: list[dict], timeout: float = 60.0, stats_out: dict = None, on_token = None) -> str:
    """Invokes local Ollama chat API, supporting live token streaming."""
    logger.debug(f"[Ollama Chat] Invoking model '{model}'...")
    use_stream = on_token is not None
    payload = json.dumps({"model": model, "messages": messages, "stream": use_stream}).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_BASE_URL}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"}
    )
    t0 = time.perf_counter()
    opener = get_ollama_opener()
    with opener.open(req, timeout=timeout) as resp:
        if use_stream:
            accumulated = []
            for raw_line in resp:
                if not raw_line:
                    continue
                try:
                    chunk_data = json.loads(raw_line.decode("utf-8"))
                    c = chunk_data.get("message", {}).get("content", "")
                    if c:
                        accumulated.append(c)
                        on_token(c)
                    if chunk_data.get("done") and stats_out is not None:
                        p_tok = chunk_data.get("prompt_eval_count", 0)
                        c_tok = chunk_data.get("eval_count", 0)
                        eval_dur_ns = chunk_data.get("eval_duration", 0)
                        stats_out["prompt_tokens"] = p_tok
                        stats_out["completion_tokens"] = c_tok
                        stats_out["total_tokens"] = (p_tok or 0) + (c_tok or 0)
                        if eval_dur_ns and eval_dur_ns > 0:
                            stats_out["tokens_per_sec"] = round(c_tok / (eval_dur_ns / 1e9), 1)
                except Exception:
                    pass
            dur = max(0.01, time.perf_counter() - t0)
            if stats_out is not None:
                stats_out["duration_secs"] = round(dur, 2)
                if "tokens_per_sec" not in stats_out and dur > 0 and stats_out.get("completion_tokens"):
                    stats_out["tokens_per_sec"] = round(stats_out["completion_tokens"] / dur, 1)
            return "".join(accumulated)
        else:
            data = json.loads(resp.read().decode("utf-8"))
            dur = max(0.01, time.perf_counter() - t0)
            ans = data.get("message", {}).get("content", "")
            if stats_out is not None:
                p_tok = data.get("prompt_eval_count", 0)
                c_tok = data.get("eval_count", 0)
                eval_dur_ns = data.get("eval_duration", 0)
                stats_out["prompt_tokens"] = p_tok
                stats_out["completion_tokens"] = c_tok
                stats_out["total_tokens"] = (p_tok or 0) + (c_tok or 0)
                if eval_dur_ns and eval_dur_ns > 0:
                    stats_out["tokens_per_sec"] = round(c_tok / (eval_dur_ns / 1e9), 1)
                elif c_tok and dur > 0:
                    stats_out["tokens_per_sec"] = round(c_tok / dur, 1)
                stats_out["duration_secs"] = round(dur, 2)
            return ans

def call_gemini_api(api_key: str, messages: list[dict], model: str = "gemini-3.5-flash", stats_out: dict = None) -> str:
    """Invokes Google Gemini REST API with automatic model fallback."""
    contents = []
    system_instruction = None
    for m in messages:
        if m.get("role") == "system":
            system_instruction = {"parts": [{"text": m.get("content", "")}]}
        else:
            role = "user" if m.get("role") == "user" else "model"
            contents.append({"role": role, "parts": [{"text": m.get("content", "")}]})
    body = {"contents": contents}
    if system_instruction:
        body["systemInstruction"] = system_instruction
    payload = json.dumps(body).encode("utf-8")

    # Ordered list of models to try in case of deprecation, timeout, or 404/503
    candidate_models = [model]
    for m_fallback in ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.8-flash"]:
        if m_fallback not in candidate_models:
            candidate_models.append(m_fallback)

    last_error = None
    for target_m in candidate_models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{target_m}:generateContent"
        req = urllib.request.Request(url, data=payload, headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        })
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=15.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                dur = max(0.01, time.perf_counter() - t0)
                if stats_out is not None and "usageMetadata" in data:
                    um = data["usageMetadata"]
                    p_tok = um.get("promptTokenCount", 0)
                    c_tok = um.get("candidatesTokenCount", 0)
                    tot_tok = um.get("totalTokenCount", p_tok + c_tok)
                    stats_out["prompt_tokens"] = p_tok
                    stats_out["completion_tokens"] = c_tok
                    stats_out["total_tokens"] = tot_tok
                    stats_out["duration_secs"] = round(dur, 2)
                    if c_tok and dur > 0:
                        stats_out["tokens_per_sec"] = round(c_tok / dur, 1)
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    return "".join([p.get("text", "") for p in parts])
                return ""
        except urllib.error.HTTPError as e:
            last_error = e
            err_msg = ""
            try:
                err_msg = e.read().decode("utf-8")
            except Exception:
                pass
            # If model is deprecated / 404 / 503 overloaded / 429 quota, try the next model candidate
            if e.code in (404, 400, 429, 503) or "no longer available" in err_msg.lower():
                continue
            raise RuntimeError(f"Gemini API HTTP {e.code}: {err_msg or e.reason}")
        except (TimeoutError, urllib.error.URLError, Exception) as e:
            last_error = e
            # If read timed out on one model, try next candidate
            continue

    if last_error:
        raise last_error
    return ""

def call_openai_api(api_key: str, messages: list[dict], model: str = "gpt-4o", stats_out: dict = None) -> str:
    """Invokes OpenAI Chat Completions REST API."""
    url = "https://api.openai.com/v1/chat/completions"
    payload = json.dumps({"model": model, "messages": messages}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    })
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=30.0) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        dur = max(0.01, time.perf_counter() - t0)
        if stats_out is not None and "usage" in data:
            u = data["usage"]
            p_tok = u.get("prompt_tokens", 0)
            c_tok = u.get("completion_tokens", 0)
            tot_tok = u.get("total_tokens", p_tok + c_tok)
            stats_out["prompt_tokens"] = p_tok
            stats_out["completion_tokens"] = c_tok
            stats_out["total_tokens"] = tot_tok
            stats_out["duration_secs"] = round(dur, 2)
            if c_tok and dur > 0:
                stats_out["tokens_per_sec"] = round(c_tok / dur, 1)
        return data.get("choices", [{}])[0].get("message", {}).get("content", "")

def call_anthropic_api(api_key: str, messages: list[dict], model: str = "claude-3-7-sonnet-latest", stats_out: dict = None) -> str:
    """Invokes Anthropic Messages REST API."""
    url = "https://api.anthropic.com/v1/messages"
    system_text = ""
    chat_msgs = []
    for m in messages:
        if m.get("role") == "system":
            system_text += m.get("content", "") + "\n"
        else:
            chat_msgs.append({"role": m.get("role"), "content": m.get("content")})
    body = {
        "model": model,
        "max_tokens": 4096,
        "messages": chat_msgs
    }
    if system_text:
        body["system"] = system_text.strip()
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01"
    })
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=30.0) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        dur = max(0.01, time.perf_counter() - t0)
        if stats_out is not None and "usage" in data:
            u = data["usage"]
            p_tok = u.get("input_tokens", 0)
            c_tok = u.get("output_tokens", 0)
            stats_out["prompt_tokens"] = p_tok
            stats_out["completion_tokens"] = c_tok
            stats_out["total_tokens"] = p_tok + c_tok
            stats_out["duration_secs"] = round(dur, 2)
            if c_tok and dur > 0:
                stats_out["tokens_per_sec"] = round(c_tok / dur, 1)
        content_items = data.get("content", [])
        return "".join([c.get("text", "") for c in content_items if c.get("type") == "text"])
