"""
cleo_logger.py — Centralized Verbose Logging for Cleo FinOps Agent
==================================================================
Provides real-time timestamped DEBUG logging to:
  1. Console (stdout) with ANSI colors
  2. Persistent log file (~/.cleo/cleo.log)
  3. In-memory ring buffer for Web GUI live inspection (/api/logs)
"""

import os
import re
import sys
import logging
from collections import deque
from datetime import datetime

APP_DATA_DIR = os.path.expanduser("~/.cleo")
os.makedirs(APP_DATA_DIR, exist_ok=True)
LOG_FILE = os.path.join(APP_DATA_DIR, "cleo.log")

def _lock_down_data_dir():
    """~/.cleo holds chat history, memory, logs and OAuth tokens: owner-only, including files
    written by earlier versions with default (world-readable) permissions."""
    try:
        os.chmod(APP_DATA_DIR, 0o700)
        for name in os.listdir(APP_DATA_DIR):
            path = os.path.join(APP_DATA_DIR, name)
            if os.path.isfile(path) and not os.path.islink(path):
                os.chmod(path, 0o600)
    except OSError:
        pass  # best effort (e.g. Windows ACLs, read-only mounts)

_lock_down_data_dir()

# Secret redaction: debug logs include request/response bodies and OAuth payloads, and are
# written to ~/.cleo/cleo.log and served by /api/logs.
_SECRET_PATTERNS = [
    (re.compile(r"""(?i)((?:client_secret|access_token|refresh_token|id_token|api[_-]?key|password|authorization)["']?\s*[:=]\s*["']?)(?:Bearer\s+)?[^"'\s,}&]+"""), r"\1***"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer ***"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*"), "***jwt***"),
    (re.compile(r"\b(?:sk-ant-|sk-|AIza|ghp_|AKIA)[A-Za-z0-9_-]{16,}"), "***key***"),
]

def redact_secrets(text: str) -> str:
    for pattern, repl in _SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text

class SecretRedactingFilter(logging.Filter):
    def filter(self, record):
        try:
            record.msg = redact_secrets(record.getMessage())
            record.args = ()
        except Exception:
            pass
        return True

LOG_BUFFER = deque(maxlen=2000)

class ColorFormatter(logging.Formatter):
    RESET = "\033[0m"

    def format(self, record):
        timestamp = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        msg = record.getMessage()

        if record.levelno in (logging.ERROR, logging.CRITICAL):
            # Standout red for errors
            return f"\033[31m{timestamp} [{record.levelname:5s}] [{record.name}] {msg}{self.RESET}"
        elif record.levelno == logging.WARNING:
            # Standout yellow for warnings
            return f"\033[33m{timestamp} [{record.levelname:5s}] [{record.name}] {msg}{self.RESET}"
        elif record.levelno == logging.DEBUG:
            # Dim gray for debug
            return f"\033[90m{timestamp} [{record.levelname:5s}] [{record.name}] {msg}{self.RESET}"
        else:
            # Clean default terminal text for INFO (no green ANSI codes)
            return f"{timestamp} [{record.levelname:5s}] [{record.name}] {msg}"

_CONSOLE_INFO_WHITELIST = (
    # LLM load / unload / eviction lifecycle (only actual load & unload events)
    "[mlx load]",
    "[mlx unload]",
    "[ollama unload]",
    "[idle watchdog]",
    "into unified memory",
    "metal cache freed",
    "evicting model",
    "unloading local llm",
    "evicted from memory",
    "loaded into memory and ready",
    # OAuth refresh
    "[oauth refresh]",
    "refreshing cloudhealth access token",
    "access token refreshed",
    "refreshing via oauth",
    "refreshing token via oauth",
    # MCP init & tools
    "[mcp init]",
    "[mcp tools]",
    "[mcp init success]",
    "cloudhealth connection ready",
    "connected to cloudhealth mcp",
    "cloudhealth authentication needed",
    # Server shutdown
    "cleo server shutting down",
    "server shutting down",
)

class ConsoleFilter(logging.Filter):
    """Filters console output to only show errors, warnings, and essential lifecycle INFO logs."""
    def __init__(self, verbose: bool = False):
        super().__init__()
        self.verbose = verbose

    def filter(self, record: logging.LogRecord) -> bool:
        if self.verbose:
            return True
        # Always display errors and warnings
        if record.levelno >= logging.WARNING:
            return True
        # Display only specified lifecycle INFO logs
        if record.levelno == logging.INFO:
            msg = record.getMessage().lower()
            if "already resident" in msg:
                return False
            return any(pat in msg for pat in _CONSOLE_INFO_WHITELIST)
        return False

class PlainFormatter(logging.Formatter):
    def format(self, record):
        timestamp = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        return f"{timestamp} [{record.levelname:5s}] [{record.name}] {record.getMessage()}"

class RingBufferHandler(logging.Handler):
    def __init__(self, buffer_deque):
        super().__init__()
        self.buffer = buffer_deque
        self.setFormatter(PlainFormatter())

    def emit(self, record):
        try:
            msg = self.format(record)
            self.buffer.append(msg)
        except Exception:
            self.handleError(record)

# Root Cleo logger level: default to INFO
LOG_LEVEL_NAME = os.environ.get("CLEO_LOG_LEVEL", "INFO").upper()
_is_verbose = os.environ.get("CLEO_VERBOSE", "").lower() in ("1", "true", "yes")
if _is_verbose:
    LOG_LEVEL_NAME = "DEBUG"

LOG_LEVEL = getattr(logging, LOG_LEVEL_NAME, logging.INFO)

# Root Cleo logger
_root_logger = logging.getLogger("cleo")
_root_logger.setLevel(LOG_LEVEL)

# Console Handler: filtered for concise, non-green lifecycle logs
_console_handler = logging.StreamHandler(sys.stdout)
_console_handler.setLevel(LOG_LEVEL)
_console_handler.setFormatter(ColorFormatter())
_console_handler.addFilter(ConsoleFilter(verbose=_is_verbose))
_console_handler.addFilter(SecretRedactingFilter())
_root_logger.addHandler(_console_handler)

# File Handler
try:
    _file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    _file_handler.setLevel(LOG_LEVEL)
    _file_handler.setFormatter(PlainFormatter())
    _file_handler.addFilter(SecretRedactingFilter())
    try:
        os.chmod(LOG_FILE, 0o600)
    except OSError:
        pass
    _root_logger.addHandler(_file_handler)
except Exception as e:
    sys.stderr.write(f"⚠️ Could not open log file {LOG_FILE}: {e}\n")

# Memory Buffer Handler
_buf_handler = RingBufferHandler(LOG_BUFFER)
_buf_handler.setLevel(LOG_LEVEL)
_buf_handler.addFilter(SecretRedactingFilter())
_root_logger.addHandler(_buf_handler)

# Prevent propagating to parent root to avoid double printing
_root_logger.propagate = False

def get_logger(name: str = "core") -> logging.Logger:
    """Returns a child logger under the 'cleo' namespace."""
    return logging.getLogger(f"cleo.{name}")

def get_recent_logs(limit: int = 200) -> list[str]:
    """Returns the most recent log entries from the ring buffer."""
    entries = list(LOG_BUFFER)
    return entries[-limit:]
