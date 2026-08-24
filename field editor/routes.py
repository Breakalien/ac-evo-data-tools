"""
HTTP API for the Field Editor tab: add/remove fields on a protobuf content
file, at any nesting depth, without touching values - Data Editor stays the
place to edit values, this tab stays the place to shape which fields exist.

Reuses "data editor"'s own /api/data/open and /api/data/save as-is for
reading and writing files (both already work on the same generic {path,
data} tree acevo_pb.decode_message/encode_message produce) - this module
only adds the one thing neither of those already does: given a message name
and the field numbers a file's root already has, which of that message's
OTHER declared fields (from the real extracted schema) could still be added,
and what does an empty/default value for each look like.

Works at any nesting depth via `path`: a comma-separated list of field
numbers from the root message down to the target node (e.g. "5,2" = the
root's field 5's message type's field 2). Array indices never appear in it -
every item of a repeated field shares the same message type, so resolving
"what fields does THIS node's type declare" only ever needs field numbers,
never which item. The frontend walks the actual JSON tree itself to build
this path and to know which fields are already present at that node; this
module only walks the schema side (the descriptor tree) to answer "what
else could go here."
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from google.protobuf.descriptor import FieldDescriptor as T

import acevo_decode      # noqa: E402  ("data editor/" - added to sys.path by main.py)
import acevo_pb          # noqa: E402  ("data editor/" - added to sys.path by main.py)

# FieldDescriptor.type -> (acevo_pb type token, default JSON value). Mirrors
# the wire-type branches acevo_pb.decode_value already produces when reading
# real files, so a field we add here decodes back the same way next time.
# TYPE_GROUP has no entry - unsupported (unused, ancient protobuf feature).
_TYPE_DEFAULTS = {
    T.TYPE_DOUBLE: ("f64", 0.0),
    T.TYPE_FLOAT: ("f32", 0.0),
    T.TYPE_INT64: ("varint", 0),
    T.TYPE_UINT64: ("varint", 0),
    T.TYPE_INT32: ("varint", 0),
    T.TYPE_UINT32: ("varint", 0),
    T.TYPE_SINT32: ("varint", 0),
    T.TYPE_SINT64: ("varint", 0),
    T.TYPE_BOOL: ("varint", 0),
    T.TYPE_ENUM: ("varint", 0),
    T.TYPE_FIXED64: ("i64", 0),
    T.TYPE_SFIXED64: ("i64", 0),
    T.TYPE_FIXED32: ("i32", 0),
    T.TYPE_SFIXED32: ("i32", 0),
    T.TYPE_STRING: ("str", ""),
    T.TYPE_BYTES: ("bytes", ""),
    T.TYPE_MESSAGE: ("msg", {}),
}


def _default_for_field(f):
    """(json_key, default_value) for a freshly-added field, or (None, None)
    if its type isn't supported (see _TYPE_DEFAULTS)."""
    entry = _TYPE_DEFAULTS.get(f.type)
    if entry is None:
        return None, None
    typ, default = entry
    key = "%d:%s:%s" % (f.number, typ, f.name)
    if f.label == f.LABEL_REPEATED:
        default = [default]
    return key, default


def _resolve_nested_descriptor(desc, path):
    """Walk `path` (field numbers) from `desc`, following each field's own
    declared message type, to reach a nested node's descriptor. None if any
    step is missing or isn't message-typed (path is stale - the field it
    named no longer exists, or existed but as a scalar)."""
    for number in path:
        f = desc.fields_by_number.get(number)
        if f is None or f.message_type is None:
            return None
        desc = f.message_type
    return desc


def api_addable_fields(q):
    msg_name = q.get("message", [""])[0].strip()
    present_raw = q.get("present", [""])[0]
    present = {int(x) for x in present_raw.split(",") if x.strip().lstrip("-").isdigit()}
    path_raw = q.get("path", [""])[0]
    path = [int(x) for x in path_raw.split(",") if x.strip().lstrip("-").isdigit()]

    if not msg_name:
        return {"addable": []}
    try:
        desc = acevo_decode.get_pool().FindMessageTypeByName(msg_name)
    except Exception:
        return {"addable": []}
    if path:
        desc = _resolve_nested_descriptor(desc, path)
        if desc is None:
            return {"addable": []}

    reserved = set(acevo_pb.reserved_numbers(desc.full_name))
    out = []
    for f in desc.fields:
        if f.number in present or f.number in reserved:
            continue
        key, default = _default_for_field(f)
        if key is None:
            continue
        out.append({"number": f.number, "name": f.name, "key": key, "default": default})
    out.sort(key=lambda item: item["number"])
    return {"addable": out}


# ---------------------------------------------------------------- bulk cleanup ("Clean all datas")
# Same recursive rules as the frontend's own "Remove reserved"/"Remove empty"
# buttons (see field editor/static/app.js) - ported to Python here so an
# entire folder tree can be cleaned in one request instead of one open/save
# round trip per file. A repeated field's items are only ever cleaned
# internally, never dropped, even if one ends up empty - removing one would
# change the field's count, a different (and riskier) kind of edit.

def _remove_reserved(node) -> int:
    count = 0
    if isinstance(node, dict) and "_seq" in node:
        kept = []
        for item in node["_seq"]:
            if item.get("n") == "?reserved":
                count += 1
                continue
            if item.get("t") == "msg":
                v = item["v"]
                for sub in (v if isinstance(v, list) else [v]):
                    count += _remove_reserved(sub)
            kept.append(item)
        node["_seq"] = kept
        return count
    if not isinstance(node, dict):
        return count
    for key in list(node.keys()):
        parts = key.split(":")
        typ = parts[1] if len(parts) > 1 else None
        name = parts[2] if len(parts) > 2 else None
        if name == "?reserved":
            del node[key]
            count += 1
            continue
        if typ == "msg":
            v = node[key]
            for sub in (v if isinstance(v, list) else [v]):
                count += _remove_reserved(sub)
    return count


def _is_empty_msg(v) -> bool:
    if not isinstance(v, dict):
        return False
    if "_seq" in v:
        return len(v["_seq"]) == 0
    return len(v) == 0


def _remove_empty(node) -> int:
    count = 0
    if isinstance(node, dict) and "_seq" in node:
        kept = []
        for item in node["_seq"]:
            if item.get("t") == "msg":
                v = item["v"]
                if isinstance(v, list):
                    for sub in v:
                        count += _remove_empty(sub)
                else:
                    count += _remove_empty(v)
                    if _is_empty_msg(v):
                        count += 1
                        continue
            kept.append(item)
        node["_seq"] = kept
        return count
    if not isinstance(node, dict):
        return count
    for key in list(node.keys()):
        parts = key.split(":")
        typ = parts[1] if len(parts) > 1 else None
        if typ != "msg":
            continue
        v = node[key]
        if isinstance(v, list):
            for sub in v:
                count += _remove_empty(sub)
        else:
            count += _remove_empty(v)
            if _is_empty_msg(v):
                del node[key]
                count += 1
    return count


def _backup_once(path: Path) -> bool:
    bak = path.with_suffix(path.suffix + ".bak")
    if not bak.exists():
        shutil.copy2(path, bak)
        return True
    return False


def _walk_proto_files(root: Path):
    proto_exts = set(acevo_decode.EXT_MAP)
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            p = Path(dirpath) / fn
            if p.suffix.lower() in proto_exts:
                yield p


def api_clean_all(body):
    root = Path(body["root"])
    if not root.is_dir():
        raise ValueError("folder not found: %s" % root)
    clean_empty = bool(body.get("clean_empty"))
    clean_reserved = bool(body.get("clean_reserved"))
    create_backup = bool(body.get("create_backup"))

    scanned = changed = errors = 0
    reserved_removed = empty_removed = 0
    results = []
    for p in _walk_proto_files(root):
        scanned += 1
        try:
            raw = p.read_bytes()
            msg_name, desc = acevo_pb.resolve_desc(str(p))
            tree = acevo_pb.decode_message(raw, desc)
            n_res = _remove_reserved(tree) if clean_reserved else 0
            n_emp = _remove_empty(tree) if clean_empty else 0
            if n_res or n_emp:
                new_bytes = acevo_pb.encode_message(tree)
                if create_backup:
                    _backup_once(p)
                p.write_bytes(new_bytes)
                changed += 1
                reserved_removed += n_res
                empty_removed += n_emp
                results.append({"path": str(p), "reserved": n_res, "empty": n_emp})
        except Exception as exc:
            errors += 1
            results.append({"path": str(p), "error": str(exc)})
    return {
        "scanned": scanned, "changed": changed, "errors": errors,
        "reserved_removed": reserved_removed, "empty_removed": empty_removed,
        "results": results,
    }


def api_restore_backups(body):
    root = Path(body["root"])
    if not root.is_dir():
        raise ValueError("folder not found: %s" % root)
    restored = errors = 0
    results = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            if not fn.endswith(".bak"):
                continue
            bak = Path(dirpath) / fn
            original = bak.with_suffix("")
            try:
                shutil.copy2(bak, original)
                restored += 1
            except Exception as exc:
                errors += 1
                results.append({"path": str(bak), "error": str(exc)})
    return {"restored": restored, "errors": errors, "results": results}


def api_delete_backups(body):
    root = Path(body["root"])
    if not root.is_dir():
        raise ValueError("folder not found: %s" % root)
    deleted = errors = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            if not fn.endswith(".bak"):
                continue
            try:
                (Path(dirpath) / fn).unlink()
                deleted += 1
            except Exception:
                errors += 1
    return {"deleted": deleted, "errors": errors}


GET_ROUTES = {
    "/api/field-editor/addable": api_addable_fields,
}
POST_ROUTES = {
    "/api/field-editor/clean_all": api_clean_all,
    "/api/field-editor/restore_backups": api_restore_backups,
    "/api/field-editor/delete_backups": api_delete_backups,
}


def register(get_routes: dict, post_routes: dict) -> None:
    get_routes.update(GET_ROUTES)
    post_routes.update(POST_ROUTES)
