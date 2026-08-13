"""
HTTP API for the data editor (browse/decode/edit/save AC EVO data files).

Ported from ac-evo-data-tools-main's acevo_ui.py: the security/serving parts
of that file moved to UI/server.py (generic, reusable by any module), and
what remains here is the module-specific logic (kind detection, listing,
open/save per format) plus a route table this module contributes to the
shared server.

Handles protobuf files via acevo_pb, .extended_splinedata.bin via
acevo_spline, and text files as-is.
"""
from __future__ import annotations

import os
import shutil
import string
from pathlib import Path

HERE = Path(__file__).resolve().parent

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

START_DIR = None


def set_start_dir(path: Path) -> None:
    global START_DIR
    START_DIR = path


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


GET_ROUTES = {
    "/api/data/ls": api_ls,
    "/api/data/open": api_open,
    "/api/data/search": api_search,
    "/api/data/drives": api_drives,
}
POST_ROUTES = {
    "/api/data/save": api_save,
}


def register(get_routes: dict, post_routes: dict) -> None:
    get_routes.update(GET_ROUTES)
    post_routes.update(POST_ROUTES)


def schema_status() -> dict:
    """Whether protobuf schemas were found (surfaced by main.py at startup)."""
    ok = acevo_decode is not None and acevo_decode.DESC.exists()
    return {"ok": ok, "path": str(acevo_decode.DESC) if acevo_decode else None}
