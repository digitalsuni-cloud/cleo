"""
cleo_logger.py — Centralized Verbose Logging for Cleo FinOps Agent
==================================================================
Provides real-time timestamped DEBUG logging to:
  1. Console (stdout) with ANSI colors
  2. Persistent log file (~/.cleo/cleo.log)
  3. In-memory ring buffer for Web GUI live inspection (/api/logs)
"""

import os
import sys
import logging
from collections import deque
from datetime import datetime

APP_DATA_DIR = os.path.expanduser("~/.cleo")
os.makedirs(APP_DATA_DIR, exist_ok=True)
LOG_FILE = os.path.join(APP_DATA_DIR, "cleo.log")

LOG_BUFFER = deque(maxlen=2000)

class ColorFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG: "\033[36m",     # Cyan
        logging.INFO: "\033[32m",      # Green
        logging.WARNING: "\033[33m",   # Yellow
        logging.ERROR: "\033[31m",     # Red
        logging.CRITICAL: "\033[41m\033[37m", # Red background
    }
    RESET = "\033[0m"

    def format(self, record):
        color = self.COLORS.get(record.levelno, self.RESET)
        timestamp = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        return f"{color}{timestamp} [{record.levelname:5s}] [{record.name}] {record.getMessage()}{self.RESET}"

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

# Root Cleo logger level: default to WARNING, enable DEBUG only if CLEO_VERBOSE is set
LOG_LEVEL_NAME = os.environ.get("CLEO_LOG_LEVEL", "WARNING").upper()
if os.environ.get("CLEO_VERBOSE", "").lower() in ("1", "true", "yes"):
    LOG_LEVEL_NAME = "DEBUG"

LOG_LEVEL = getattr(logging, LOG_LEVEL_NAME, logging.INFO)

# Root Cleo logger
_root_logger = logging.getLogger("cleo")
_root_logger.setLevel(LOG_LEVEL)

# Console Handler
_console_handler = logging.StreamHandler(sys.stdout)
_console_handler.setLevel(LOG_LEVEL)
_console_handler.setFormatter(ColorFormatter())
_root_logger.addHandler(_console_handler)

# File Handler
try:
    _file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    _file_handler.setLevel(LOG_LEVEL)
    _file_handler.setFormatter(PlainFormatter())
    _root_logger.addHandler(_file_handler)
except Exception as e:
    sys.stderr.write(f"⚠️ Could not open log file {LOG_FILE}: {e}\n")

# Memory Buffer Handler
_buf_handler = RingBufferHandler(LOG_BUFFER)
_buf_handler.setLevel(LOG_LEVEL)
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
