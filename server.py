"""BRAIN Backend — REST API, private Studio, and optional API-key gateway."""

import hmac
import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from key_store import KeyStoreNotConfigured, create_key, list_keys, revoke_key, validate_key
from providers import PROVIDERS, VOICES, ProviderNotConfigured

PORT = int(os.environ.get("PORT", "8765"))
MAX_BODY = 256 * 1024
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(ROOT_DIR, "static")
WEB_EXTS = {".html", ".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".txt", ".map", ".json", ".mp3", ".wav", ".mp4"}


def send_json(handler, status, obj):
    body = json.dumps(obj).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def send_bytes(handler, status, content_type, data):
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(data)


def api_key_required():
    return os.environ.get("BRAIN_REQUIRE_API_KEY", "false").lower() == "true"


class Handler(BaseHTTPRequestHandler):
    server_version = "BrainBackend/3.0"

    def log_message(self, fmt, *args):
        print(f"[{self.command} {self.path}]", fmt % args)

    def read_json_body(self):
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            raise ValueError("invalid Content-Length")
        if length > MAX_BODY:
            raise ValueError("body too large")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode() or "{}")
        except json.JSONDecodeError:
            raise ValueError("invalid JSON body")

    def is_admin(self):
        expected = os.environ.get("BRAIN_ADMIN_SECRET")
        supplied = self.headers.get("X-BRAIN-ADMIN", "")
        return bool(expected) and hmac.compare_digest(supplied, expected)

    def require_admin(self):
        if not os.environ.get("BRAIN_ADMIN_SECRET"):
            send_json(self, 503, {"ok": False, "error": "Set BRAIN_ADMIN_SECRET on Render before managing API keys."})
            return False
        if not self.is_admin():
            send_json(self, 401, {"ok": False, "error": "Admin authorization required."})
            return False
        return True

    def require_api_key(self):
        if not api_key_required():
            return True
        authorization = self.headers.get("Authorization", "")
        token = authorization.removeprefix("Bearer ").strip() if authorization.startswith("Bearer ") else ""
        try:
            valid = validate_key(token)
        except KeyStoreNotConfigured as error:
            send_json(self, 503, {"ok": False, "error": str(error)})
            return False
        if not valid:
            send_json(self, 401, {"ok": False, "error": "A valid BRAIN API key is required."})
            return False
        return True

    def run_provider(self, key, payload):
        if not self.require_api_key():
            return
        provider = PROVIDERS[key]
        try:
            result = provider.run(payload)
        except ProviderNotConfigured as error:
            send_json(self, 501, {"ok": False, "error": str(error), "provider": provider.name})
            return
        except ValueError as error:
            send_json(self, 400, {"ok": False, "error": str(error)})
            return
        except Exception as error:
            print(f"Provider {key} failed: {error}")
            send_json(self, 502, {"ok": False, "error": f"provider failed: {error}"})
            return
        if "data" in result:
            send_bytes(self, 200, result["content_type"], result["data"])
        else:
            send_json(self, 200, {"ok": True, **result["json"]})

    def serve_static(self, rel_path):
        safe = os.path.normpath(rel_path).lstrip("/")
        if os.path.splitext(safe)[1].lower() not in WEB_EXTS:
            send_json(self, 404, {"ok": False, "error": "not found"})
            return
        for base in (STATIC_DIR, ROOT_DIR):
            full = os.path.join(base, safe)
            if full.startswith(base) and os.path.isfile(full):
                content_type, _ = mimetypes.guess_type(full)
                with open(full, "rb") as asset:
                    send_bytes(self, 200, content_type or "application/octet-stream", asset.read())
                return
        send_json(self, 404, {"ok": False, "error": "not found"})

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            send_json(self, 200, {
                "ok": True, "service": "brain-backend",
                "api_key_required": api_key_required(),
                "providers": {key: provider.name for key, provider in PROVIDERS.items()},
            })
        elif path == "/v1/voices":
            send_json(self, 200, {"ok": True, "voices": [{"id": key, "label": key.title()} for key in VOICES]})
        elif path == "/v1/admin/keys":
            if not self.require_admin():
                return
            try:
                send_json(self, 200, {"ok": True, "keys": list_keys()})
            except KeyStoreNotConfigured as error:
                send_json(self, 503, {"ok": False, "error": str(error)})
        elif path in {"/", "/index.html"}:
            self.serve_static("index.html")
        elif path.startswith("/static/"):
            self.serve_static(path[len("/static/"):])
        else:
            send_json(self, 404, {"ok": False, "error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            payload = self.read_json_body()
        except ValueError as error:
            send_json(self, 400, {"ok": False, "error": str(error)})
            return
        if path == "/v1/admin/keys":
            if not self.require_admin():
                return
            try:
                send_json(self, 201, {"ok": True, **create_key(payload.get("label"))})
            except (KeyStoreNotConfigured, ValueError) as error:
                send_json(self, 400 if isinstance(error, ValueError) else 503, {"ok": False, "error": str(error)})
            return
        if path.startswith("/v1/admin/keys/") and path.endswith("/revoke"):
            if not self.require_admin():
                return
            try:
                key_id = int(path.split("/")[4])
                revoke_key(key_id)
                send_json(self, 200, {"ok": True})
            except (KeyStoreNotConfigured, ValueError) as error:
                send_json(self, 400 if isinstance(error, ValueError) else 503, {"ok": False, "error": str(error)})
            return
        routes = {
            "/v1/speak": "tts", "/v1/images/generate": "image",
            "/v1/videos/generate": "video", "/v1/chat": "chat",
        }
        provider_key = routes.get(path)
        if provider_key:
            self.run_provider(provider_key, payload)
        else:
            send_json(self, 404, {"ok": False, "error": "not found"})


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"BRAIN backend live on http://0.0.0.0:{PORT}")
    server.serve_forever()
