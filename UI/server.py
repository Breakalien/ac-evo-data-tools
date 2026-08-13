"""
Generic HTTP server for ACE AIO: static file serving + a JSON API route
registry that independent modules (data editor, material editor, ...) fill
in at startup. No module-specific logic lives here - this is UI plumbing
only, reused as-is by every tool that gets added later.

Adapted from ac-evo-data-tools-main's acevo_ui.py, with the acevo-specific
API handlers (api_ls, api_open, ...) split out into "data editor/routes.py".

Security: listens on 127.0.0.1 only, checks the Host header, rejects
foreign Origins, and requires a session token drawn at startup and stored
as a cookie - same model as the original acevo_ui.py.
"""
from __future__ import annotations

import json
import mimetypes
import secrets
from dataclasses import dataclass
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

STATIC_DIR = Path(__file__).resolve().parent / "static"

TOKEN = secrets.token_urlsafe(24)

# Filled in by main.py before the server starts, via register_get/register_post
# or by updating these dicts directly (e.g. data_editor_routes.register(...)).
GET_ROUTES: dict = {}
POST_ROUTES: dict = {}

# Each tab keeps its own frontend under "<tab folder>/static/" instead of
# living inside UI/static/ - this dict maps a URL prefix (e.g. "data-editor")
# to that folder, so every tab stays a genuinely self-contained, independently
# updatable unit. UI/static/ itself only ever holds the shared shell
# (index.html, style.css, shell.js, filebrowser.js, resizable.js).
STATIC_MOUNTS: dict[str, Path] = {}


def register_static(prefix: str, folder: Path) -> None:
    STATIC_MOUNTS[prefix.strip("/")] = folder


def _resolve_static_file(url_path: str) -> Path | None:
    rel = url_path.lstrip("/")
    prefix, _, tail = rel.partition("/")
    if prefix in STATIC_MOUNTS:
        base = STATIC_MOUNTS[prefix].resolve()
        f = (base / tail).resolve()
    else:
        base = STATIC_DIR.resolve()
        f = (base / rel).resolve()
    if base != f and base not in f.parents:
        return None  # path traversal guard
    return f if f.is_file() else None


@dataclass
class RawResponse:
    """A route handler returns this instead of a dict to send bytes with an
    explicit content type (e.g. a texture thumbnail as PNG) instead of JSON."""
    body: bytes
    content_type: str


def register_get(path: str, handler) -> None:
    GET_ROUTES[path] = handler


def register_post(path: str, handler) -> None:
    POST_ROUTES[path] = handler


class Handler(BaseHTTPRequestHandler):
    server_version = "ace-aio"

    def log_message(self, fmt, *args):
        pass

    # -- access controls

    def _host_ok(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost", "[::1]", "::1")

    def _origin_ok(self):
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        return urlparse(origin).hostname in ("127.0.0.1", "localhost", "::1")

    def _token_ok(self):
        c = SimpleCookie(self.headers.get("Cookie") or "")
        if "ace_token" in c and secrets.compare_digest(c["ace_token"].value, TOKEN):
            return True
        hdr = self.headers.get("X-Ace-Token")
        return bool(hdr) and secrets.compare_digest(hdr, TOKEN)

    def _send(self, code, body, ctype="application/json; charset=utf-8", cookie=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False, allow_nan=True).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _send_result(self, result):
        if isinstance(result, RawResponse):
            return self._send(200, result.body, result.content_type)
        return self._send(200, result)

    # -- routes

    def do_GET(self):
        u = urlparse(self.path)
        if not self._host_ok():
            return self._send(403, {"error": "host not allowed"})
        try:
            if u.path in ("/", "/index.html"):
                q = parse_qs(u.query)
                given = q.get("t", [""])[0]
                if not (secrets.compare_digest(given, TOKEN) or self._token_ok()):
                    return self._send(403, "Missing or invalid token. Open the "
                                           "address printed in the console.",
                                      "text/plain; charset=utf-8")
                page = (STATIC_DIR / "index.html").read_bytes()
                return self._send(200, page, "text/html; charset=utf-8",
                                  cookie="ace_token=%s; Path=/; SameSite=Strict"
                                         % TOKEN)

            if u.path in GET_ROUTES:
                if not (self._token_ok() and self._origin_ok()):
                    return self._send(403, {"error": "missing token or rejected origin"})
                return self._send_result(GET_ROUTES[u.path](parse_qs(u.query)))

            f = _resolve_static_file(u.path)
            if f is None:
                return self._send(404, {"error": "not found"})
            ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            return self._send(200, f.read_bytes(), ctype + "; charset=utf-8")
        except Exception as e:
            return self._send(400, {"error": str(e)})

    def do_POST(self):
        u = urlparse(self.path)
        if not self._host_ok():
            return self._send(403, {"error": "host not allowed"})
        if not (self._token_ok() and self._origin_ok()):
            return self._send(403, {"error": "missing token or rejected origin"})
        try:
            if u.path not in POST_ROUTES:
                return self._send(404, {"error": "unknown route"})
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
            return self._send_result(POST_ROUTES[u.path](body))
        except Exception as e:
            return self._send(400, {"error": str(e)})


class Server(ThreadingHTTPServer):
    # On Windows, SO_REUSEADDR lets two processes bind the same port, and a
    # stale instance would keep answering with outdated code.
    allow_reuse_address = False
    daemon_threads = True


def create_server(host: str, port: int) -> Server:
    return Server((host, port), Handler)
