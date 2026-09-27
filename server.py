"""
BRAIN Backend — REST API server. Stdlib only, zero dependencies.

Run:
    python3 server.py            # serves on http://localhost:8765
    PORT=9000 python3 server.py  # custom port

Endpoints:
    GET  /health
    GET  /v1/voices
    POST /v1/speak            {text, voice?, speed?}            -> audio/mpeg
    POST /v1/images/generate  {prompt, size?}                  -> image/png
    POST /v1/videos/generate  {prompt}                         -> 501 until configured
    POST /v1/chat             {messages:[{role, content}]}      -> {reply}

Errors are JSON: {"ok": false, "error": "..."} with an HTTP status code.
Binary successes return the raw bytes with the right Content-Type.
"""

import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

from providers import PROVIDERS, VOICES, ProviderNotConfigured

PORT = int(os.environ.get("PORT", "8765"))
MAX_BODY = 256 * 1024  # 256 KB
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(ROOT_DIR, "static")
# Web-asset extensions allowed when serving files (never .py / .env).
WEB_EXTS = {".html", ".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".webp",
            ".svg", ".ico", ".txt", ".map", ".json", ".mp3", ".wav"}


def send_json(handler, status, obj):
    body = json.dumps(obj).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def send_bytes(handler, status, content_type, data):
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


class Handler(BaseHTTPRequestHandler):
    server_version = "BrainBackend/1.0"

    def log_message(self, fmt, *args):  # quieter logs
        print(f"[{self.command} {self.path}]", fmt % args)

    # -- helpers -----------------------------------------------------
    def read_json_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length > MAX_BODY:
            raise ValueError("body too large")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode() or "{}")
        except json.JSONDecodeError:
            raise ValueError("invalid JSON body")

    def run_provider(self, key, payload):
        provider = PROVIDERS[key]
        try:
            result = provider.run(payload)
        except ProviderNotConfigured as e:
            send_json(self, 501, {"ok": False, "error": str(e), "provider": provider.name})
            return
        except ValueError as e:
            send_json(self, 400, {"ok": False, "error": str(e)})
            return
        except Exception as e:
            send_json(self, 502, {"ok": False, "error": f"provider failed: {e}"})
            return
        if "data" in result:  # binary payload
            send_bytes(self, 200, result["content_type"], result["data"])
        else:  # json payload
            send_json(self, 200, {"ok": True, **result["json"]})

    # -- routes ------------------------------------------------------
    def serve_static(self, rel_path):
        # Prevent directory traversal; only serve web-asset files.
        safe = os.path.normpath(rel_path).lstrip("/")
        if os.path.splitext(safe)[1].lower() not in WEB_EXTS:
            send_json(self, 404, {"ok": False, "error": "not found"})
            return
        # Look in static/ first, then repo root (for flat phone uploads).
        for base in (STATIC_DIR, ROOT_DIR):
            full = os.path.join(base, safe)
            if full.startswith(base) and os.path.isfile(full):
                ctype, _ = mimetypes.guess_type(full)
                with open(full, "rb") as f:
                    send_bytes(self, 200, ctype or "application/octet-stream", f.read())
                return
        send_json(self, 404, {"ok": False, "error": "not found"})

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            send_json(self, 200, {
                "ok": True,
                "service": "brain-backend",
                "providers": {k: p.name for k, p in PROVIDERS.items()},
            })
        elif path == "/v1/voices":
            send_json(self, 200, {"ok": True, "voices": [
                {"id": "smooth", "label": "Smooth (default)"},
                {"id": "warm", "label": "Warm"},
            ]})
        elif path == "/" or path == "/index.html":
            self.serve_static("index.html")
        elif path.startswith("/static/"):
            self.serve_static(path[len("/static/"):])
        else:
            send_json(self, 404, {"ok": False, "error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            payload = self.read_json_body()
        except ValueError as e:
            send_json(self, 400, {"ok": False, "error": str(e)})
            return

        if path == "/v1/speak":
            self.run_provider("tts", payload)
        elif path == "/v1/images/generate":
            self.run_provider("image", payload)
        elif path == "/v1/videos/generate":
            self.run_provider("video", payload)
        elif path == "/v1/chat":
            self.run_provider("chat", payload)
        else:
            send_json(self, 404, {"ok": False, "error": "not found"})


if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"BRAIN backend live on http://localhost:{PORT}")
    print("Providers:", ", ".join(f"{k}={p.name}" for k, p in PROVIDERS.items()))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
