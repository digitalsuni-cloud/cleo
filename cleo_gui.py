#!/usr/bin/env python3
"""
cleo_gui.py — Desktop GUI Application for Cleo FinOps Agent
================================================================================
Launches Cleo as a native macOS desktop window (using pywebview / WKWebView),
connecting seamlessly to the CloudHealth MCP backend.

Usage:
  python3 cleo_gui.py            # Launches native Desktop GUI window
  python3 cleo_gui.py --browser  # Launches in your default web browser
"""

import os
import sys

# Auto-switch to project .venv if available and not currently active
_venv_python = os.path.abspath(os.path.join(os.path.dirname(__file__), ".venv", "bin", "python3"))
if os.path.exists(_venv_python) and os.path.abspath(sys.executable) != _venv_python:
    if sys.argv and sys.argv[0] != "-c":
        os.execv(_venv_python, [_venv_python] + sys.argv)

import time
import socket
import threading
import argparse
import webbrowser

PORT = int(os.environ.get("PORT", 8080))
HOST = "127.0.0.1"

def is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((HOST, port)) == 0

def start_server():
    import uvicorn
    from cleo_server import app
    _default_level = os.environ.get("CLEO_LOG_LEVEL", "warning").lower()
    _log_level = "debug" if os.environ.get("CLEO_VERBOSE", "").lower() in ("1", "true", "yes") else _default_level
    config = uvicorn.Config(app, host=HOST, port=PORT, log_level=_log_level)
    server = uvicorn.Server(config)
    server.run()

def main():
    parser = argparse.ArgumentParser(description="Cleo — CloudHealth FinOps Agent GUI")
    parser.add_argument("--browser", "-b", action="store_true", help="Launch in web browser instead of native desktop window")
    parser.add_argument("--port", type=int, default=PORT, help=f"Port to run on (default: {PORT})")
    args = parser.parse_args()

    # 0. Check for updates on startup before starting backend or GUI
    try:
        from cleo_server import _auto_update_on_startup
        _auto_update_on_startup()
    except Exception:
        pass

    port = args.port
    gui_url = f"http://{HOST}:{port}"

    # 1. Start backend server in background if not already running
    if not is_port_in_use(port):
        print(f"🚀  Starting Cleo FinOps background engine on {gui_url} ...")
        t = threading.Thread(target=start_server, daemon=True)
        t.start()
        
        # Wait up to 5s for server to start
        for _ in range(50):
            if is_port_in_use(port):
                break
            time.sleep(0.1)
    else:
        print(f"ℹ️   Cleo server is already running on {gui_url}")

    # 2. Open Desktop GUI or Browser
    use_native_window = not args.browser
    if use_native_window:
        try:
            import webview
            print("✨  Launching Cleo Native Desktop App...")
            window = webview.create_window(
                title="Cleo — CloudHealth FinOps Agent",
                url=gui_url,
                width=1280,
                height=850,
                min_size=(900, 600),
                confirm_close=False,
                background_color="#0a0c10"
            )
            webview.start(debug=False)
            return
        except Exception as e:
            print(f"⚠️   Could not initialize native window ({e}), falling back to browser.")

    # Fallback to browser
    print(f"🌐  Opening Cleo in your default browser: {gui_url}")
    webbrowser.open(gui_url)
    print("\nPress Ctrl+C to stop the Cleo server.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n👋  Cleo stopped.")

if __name__ == "__main__":
    main()
