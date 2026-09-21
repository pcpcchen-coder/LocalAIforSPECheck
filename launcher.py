"""Start the local application and open its browser UI without duplicate servers."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

APP_ID = "LocalAIforSPECheck"
HOST = "127.0.0.1"
ROOT = Path(__file__).resolve().parent


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_health(port: int) -> dict | None:
    """Use an explicit loopback URL and bypass system proxy settings."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(f"http://{HOST}:{port}/api/health", timeout=1) as response:
            payload = json.loads(response.read(4096))
        return payload if isinstance(payload, dict) else None
    except (OSError, ValueError, urllib.error.URLError):
        return None


def is_our_server(port: int) -> bool:
    health = read_health(port)
    return bool(health and health.get("status") == "ok" and health.get("app") == APP_ID)


def port_is_busy(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((HOST, port))
        except OSError:
            return True
    return False


def open_when_ready(port: int, stop: threading.Event) -> None:
    for _ in range(60):
        if stop.is_set():
            return
        if is_our_server(port):
            webbrowser.open(f"http://{HOST}:{port}")
            return
        stop.wait(0.25)
    print(f"Browser did not open automatically. Visit http://{HOST}:{port}", flush=True)


def main(argv: list[str] | None = None) -> int:
    if sys.version_info < (3, 11):
        print("Python 3.11 or newer is required. Please ask IT to complete the README setup.")
        return 1
    parser = argparse.ArgumentParser(description="Start LocalAIforSPECheck on this computer only.")
    parser.add_argument("--port", type=int, default=8765, help="Local browser port (default: 8765)")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser automatically")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")

    os.chdir(ROOT)
    url = f"http://{HOST}:{args.port}"
    if is_our_server(args.port):
        print(f"LocalAIforSPECheck is already running: {url}")
        if not args.no_browser:
            webbrowser.open(url)
        return 0
    if port_is_busy(args.port):
        print(f"Port {args.port} is in use by a different service. Nothing was stopped.")
        print("Ask IT to choose another port: python launcher.py --port 8766")
        return 1
    try:
        import uvicorn
    except ImportError:
        print("Dependencies are missing. Ask IT to run: python -m pip install -r requirements.txt")
        return 1

    print(f"LocalAIforSPECheck: {url}", flush=True)
    print("Keep this window open while working. Press Ctrl+C to stop the application.", flush=True)
    print("If the browser does not open, copy the address above into your browser.", flush=True)
    stop = threading.Event()
    if not args.no_browser:
        threading.Thread(target=open_when_ready, args=(args.port, stop), daemon=True).start()
    try:
        uvicorn.run("spec_check.app:app", host=HOST, port=args.port, workers=1, reload=False)
    finally:
        stop.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
