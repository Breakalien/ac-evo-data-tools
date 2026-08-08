#!/usr/bin/env python3
"""
acevo_ui - file explorer and editor for Assetto Corsa EVO.

A small HTTP server (standard library only) plus a web page: browse any
folder, open a file, edit its decoded content and save it.

Handles protobuf files via acevo_pb, .extended_splinedata.bin via acevo_spline,
and text files as-is.

Security: this UI exposes the file system, so the server listens on 127.0.0.1
only, checks the Host header, rejects foreign Origins, and requires a session
token drawn at startup and stored as a cookie.

Usage:
  python acevo_ui.py [--dir <start folder>] [--port 8765] [--no-browser]
"""

import argparse
import json
import mimetypes
import os
import secrets
import shutil
import string
import sys
import threading
import webbrowser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import acevo_pb          # noqa: E402
import acevo_spline      # noqa: E402

try:
    import acevo_decode  # noqa: E402
    PROTO_EXTS = set(acevo_decode.EXT_MAP)
except Exception:
    acevo_decode = None
    PROTO_EXTS = set()

TEXT_EXTS = {".data", ".js", ".css", ".html", ".less", ".ts", ".txt", ".json",
             ".ini", ".xml", ".md", ".cfg", ".log", ".csv", ".lua", ".py",
             ".proto", ".yml", ".yaml", ".toml", ".bat", ".cmd", ".sh",
             ".c", ".cpp", ".h", ".hpp", ".cs", ".glsl", ".hlsl", ".fx",
             ".shader", ".config", ".settings", ".svg", ".tsv"}

SPLINE_PAGE = 200        # points sent per page
MAX_ENTRIES = 4000       # guard against huge folders
MAX_TEXT = 8_000_000     # beyond this the text editor becomes unusable

TOKEN = secrets.token_urlsafe(24)
START_DIR = None


# ---------------------------------------------------------------- helpers


def list_drives():
    """Available drives (Windows). On other systems, the root."""
    if os.name != "nt":
        return ["/"]
    out = []
    for letter in string.ascii_uppercase:
        d = "%s:\\" % letter
        if os.path.exists(d):
            out.append(d)
    return out


def resolve_dir(raw):
    if not raw:
        return None                     # None = drive list
    p = Path(raw).expanduser()
    try:
        p = p.resolve()
    except OSError:
        pass
    return p


def kind_of(path):
    name = path.name.lower()
    ext = path.suffix.lower()
    if name.endswith("splinedata.bin"):
        return "spline"
    if ext in PROTO_EXTS:
        return "proto"
    if ext in TEXT_EXTS:
        return "text"
    return None


def sniff_text(path, probe=4096):
    """Is a file with an unknown extension text? Only used when opening a file,
    never while listing."""
    try:
        with open(path, "rb") as fh:
            chunk = fh.read(probe)
    except OSError:
        return False
    if not chunk or b"\x00" in chunk:
        return False
    try:
        chunk.decode("utf-8")
    except UnicodeDecodeError:
        # a multi-byte sequence may be cut off at the end of the probe
        try:
            chunk[:-3].decode("utf-8")
        except UnicodeDecodeError:
            return False
    return True


def collect_enums(desc, out=None, seen=None, ambiguous=None):
    """Every enum-typed field, indexed by field name: {name: [[value, label]]}.

    Same-named fields carrying different enums are dropped rather than offering
    a wrong set of choices."""
    out = {} if out is None else out
    seen = set() if seen is None else seen
    ambiguous = set() if ambiguous is None else ambiguous
    if desc is None or desc.full_name in seen:
        return out
    seen.add(desc.full_name)
    for f in desc.fields:
        if f.enum_type is not None:
            choices = [[v.number, acevo_pb.short_enum(f.enum_type, v.name)]
                       for v in f.enum_type.values]
            if f.name in out and out[f.name] != choices:
                ambiguous.add(f.name)
            out[f.name] = choices
        elif f.message_type is not None:
            collect_enums(f.message_type, out, seen, ambiguous)
    for n in ambiguous:
        out.pop(n, None)
    return out


# Inferred convention, not in the schema: these arrays hold one value per
# value of the given enum, which lets each index be labelled.
INDEX_LABELS = {
    ("CarLightingSystemData", "intensity_filters"): "CarLightingFunction",
    ("CarLedSystemData", "intensity_filters"): "LedLight",
}


def index_labels(msg_name):
    """{field_name: [label per index]} for enum-indexed arrays."""
    out = {}
    for (m, field), enum in INDEX_LABELS.items():
        if m != msg_name:
            continue
        try:
            ed = acevo_pb.get_pool_enum(enum) if hasattr(acevo_pb, "get_pool_enum") \
                else acevo_decode.get_pool().FindEnumTypeByName(enum)
        except Exception:
            continue
        vals = sorted(ed.values, key=lambda v: v.number)
        if [v.number for v in vals] == list(range(len(vals))):
            out[field] = [acevo_pb.short_enum(ed, v.name) for v in vals]
    return out


def backup_once(path):
    bak = path.with_suffix(path.suffix + ".bak")
    if not bak.exists():
        shutil.copy2(path, bak)
        return True
    return False


# ---------------------------------------------------------------- API


def api_drives(q):
    return {"drives": list_drives(), "start": str(START_DIR) if START_DIR else "",
            "home": str(Path.home())}


def api_ls(q):
    raw = q.get("path", [""])[0]
    d = resolve_dir(raw)
    if d is None:
        return {"path": "", "parent": None, "isRoot": True,
                "dirs": [{"name": x, "path": x} for x in list_drives()],
                "files": [], "truncated": False}
    if not d.is_dir():
        raise ValueError("folder not found: %s" % d)

    dirs, files, truncated = [], [], False
    try:
        with os.scandir(d) as it:
            for e in it:
                if len(dirs) + len(files) >= MAX_ENTRIES:
                    truncated = True
                    break
                try:
                    if e.is_dir():
                        dirs.append({"name": e.name, "path": e.path})
                    else:
                        p = Path(e.path)
                        files.append({"name": e.name, "path": e.path,
                                      "kind": kind_of(p), "size": e.stat().st_size})
                except OSError:
                    continue
    except PermissionError:
        raise ValueError("access denied: %s" % d)

    dirs.sort(key=lambda x: x["name"].lower())
    files.sort(key=lambda x: (x["kind"] is None, x["name"].lower()))
    parent = None if d.parent == d else str(d.parent)
    return {"path": str(d), "parent": parent, "isRoot": False,
            "dirs": dirs, "files": files, "truncated": truncated}


def api_open(q):
    p = Path(q["path"][0])
    if not p.is_file():
        raise ValueError("file not found")
    kind = kind_of(p)
    if kind is None and sniff_text(p):
        kind = "text"          # unknown extension but textual content
    info = {"path": str(p), "name": p.name, "kind": kind,
            "dir": str(p.parent), "size": p.stat().st_size,
            "backup": p.with_suffix(p.suffix + ".bak").exists()}

    if kind == "proto":
        raw = p.read_bytes()
        msg_name, desc = acevo_pb.resolve_desc(str(p))
        tree = acevo_pb.decode_message(raw, desc)
        info["message"] = msg_name
        info["roundtrip"] = acevo_pb.encode_message(tree) == raw
        info["data"] = tree
        info["enums"] = collect_enums(desc) if desc is not None else {}
        info["index_labels"] = index_labels(msg_name) if msg_name else {}
        return info

    if kind == "spline":
        doc = acevo_spline.decode(str(p))
        start = int(q.get("start", [0])[0])
        info.update({k: doc[k] for k in ("version", "aicardata", "ideal_line",
                                         "track_length_m", "count", "columns",
                                         "tail_value", "tail_marker")})
        info["roundtrip"] = acevo_spline.encode(doc) == p.read_bytes()
        info["start"] = start
        info["page"] = SPLINE_PAGE
        info["points"] = doc["points"][start:start + SPLINE_PAGE]
        return info

    if kind == "text":
        if p.stat().st_size > MAX_TEXT:
            raise ValueError("file too large for the editor (%d bytes)"
                             % p.stat().st_size)
        with open(p, encoding="utf-8", errors="replace", newline="") as fh:
            info["text"] = fh.read()
        return info

    raise ValueError("unsupported binary content (%s) - known formats: protobuf, "
                     "splinedata, text" % (p.suffix or "no extension"))


def api_save(body):
    p = Path(body["path"])
    if not p.is_file():
        raise ValueError("file not found")
    kind = kind_of(p)
    if kind is None and "text" in body:
        kind = "text"
    made = backup_once(p)

    if kind == "proto":
        data = acevo_pb.encode_message(body["data"])
        p.write_bytes(data)
        return {"ok": True, "backup_created": made, "size": len(data)}

    if kind == "spline":
        doc = acevo_spline.decode(str(p))
        for k in ("aicardata", "ideal_line", "tail_marker"):
            if k in body:
                doc[k] = body[k]
        if body.get("track_length_m") is not None:
            doc["track_length_m"] = body["track_length_m"]
        if "tail_value" in body:
            doc["tail_value"] = body["tail_value"]
        for idx, row in (body.get("patch") or {}).items():
            doc["points"][int(idx)] = row
        data = acevo_spline.encode(doc)
        p.write_bytes(data)
        return {"ok": True, "backup_created": made, "size": len(data)}

    if kind == "text":
        # the <textarea> always returns LF
        with open(p, encoding="utf-8", errors="replace", newline="") as fh:
            original = fh.read()
        text = body["text"].replace("\r\n", "\n").replace("\r", "\n")
        crlf = original.count("\r\n")
        if crlf and crlf >= original.count("\n") - crlf:
            text = text.replace("\n", "\r\n")
        with open(p, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        return {"ok": True, "backup_created": made}

    raise ValueError("unsupported format")


def api_search(q):
    term = q.get("q", [""])[0].lower().strip()
    root = resolve_dir(q.get("path", [""])[0])
    if len(term) < 2 or root is None or not root.is_dir():
        return {"results": [], "truncated": False}
    out = []
    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: None):
        dirnames.sort()
        for fn in sorted(filenames):
            if term in fn.lower():
                p = Path(dirpath) / fn
                out.append({"name": fn, "path": str(p), "kind": kind_of(p)})
                if len(out) >= 300:
                    return {"results": out, "truncated": True}
    return {"results": out, "truncated": False}


ROUTES = {"/api/ls": api_ls, "/api/open": api_open, "/api/search": api_search,
          "/api/drives": api_drives}


# ---------------------------------------------------------------- server


class Handler(BaseHTTPRequestHandler):
    server_version = "acevo_ui"

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
        if "acevo_token" in c and secrets.compare_digest(c["acevo_token"].value, TOKEN):
            return True
        hdr = self.headers.get("X-Acevo-Token")
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
                page = (HERE / "ui" / "index.html").read_bytes()
                return self._send(200, page, "text/html; charset=utf-8",
                                  cookie="acevo_token=%s; Path=/; SameSite=Strict"
                                         % TOKEN)

            if u.path in ROUTES:
                if not (self._token_ok() and self._origin_ok()):
                    return self._send(403, {"error": "missing token or rejected origin"})
                return self._send(200, ROUTES[u.path](parse_qs(u.query)))

            f = (HERE / "ui" / u.path.lstrip("/")).resolve()
            if (HERE / "ui").resolve() not in f.parents or not f.is_file():
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
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n).decode("utf-8"))
            if u.path == "/api/save":
                return self._send(200, api_save(body))
            return self._send(404, {"error": "unknown route"})
        except Exception as e:
            return self._send(400, {"error": str(e)})


class Server(ThreadingHTTPServer):
    # On Windows, SO_REUSEADDR lets two processes bind the same port, and a
    # stale instance would keep answering with outdated code.
    allow_reuse_address = False
    daemon_threads = True


def main():
    global START_DIR
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=str(HERE.parent / "content"),
                    help="folder opened on startup")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()

    d = Path(a.dir).expanduser()
    START_DIR = d.resolve() if d.is_dir() else Path.home()

    try:
        srv = Server(("127.0.0.1", a.port), Handler)
    except OSError as e:
        # Either an instance is listening (must be closed) or the port is in
        # TIME_WAIT after a recent shutdown (just wait). Try to connect.
        import socket
        busy = False
        try:
            with socket.create_connection(("127.0.0.1", a.port), timeout=0.4):
                busy = True
        except OSError:
            pass
        if busy:
            sys.exit("Port %d is already in use by another instance.\n"
                     "Close it (or its console window), or start with\n"
                     "  --port %d" % (a.port, a.port + 1))
        sys.exit("Port %d is not reusable yet (%s).\n"
                 "No instance is listening: recent connections are still in\n"
                 "TIME_WAIT. Try again in a minute or two, or start right away\n"
                 "with  --port %d"
                 % (a.port, e.__class__.__name__, a.port + 1))

    if acevo_decode is None or not acevo_decode.DESC.exists():
        print("NOTE: schemas not found (%s)."
              % (acevo_decode.DESC if acevo_decode else "proto/acevo.desc"))
        print("      Files still open and save byte-exactly, but fields show as")
        print("      numbers instead of names. Generate the schemas once with:")
        print("      python tools/extract_protos.py "
              "\"<path>/AssettoCorsaEVO.exe\" -o proto -d proto/acevo.desc\n")

    url = "http://127.0.0.1:%d/?t=%s" % (a.port, TOKEN)
    print("start folder : %s" % START_DIR)
    print("interface    : %s" % url)
    print("(the address contains a session token: do not share it)")
    print("Ctrl+C to stop.")
    if not a.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
