"""
HTTP API for the Light Color tab: batch-edit a fixed set of light-color
properties across a whole car folder at once - the vec4 light-color
properties of every "materials/**/*_FIXED.material" file (via
material_codec.py, from "material editor/"), and the vec3 emitter colors
declared in the car's ".actor" file (via generic protobuf, acevo_pb.py, from
"data editor/").

This tab owns none of those codecs itself - it only imports them (both
sibling folders are on sys.path, see main.py) - but every piece of business
logic specific to THIS tab (which fields, how "*_FIXED.material" is found,
how the light list is located inside a .actor tree, the override_color
patch-on-change rule) lives here and nowhere else, so the tab stays a single
self-contained, independently updatable unit.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import material_codec as mc   # "material editor/" - added to sys.path by main.py
import acevo_pb                # "data editor/" - added to sys.path by main.py


# ---------------------------------------------------------------- materials

# Fixed, known set of vec4 light-color properties - not schema-driven like
# the Material Editor tab: this tab intentionally only ever touches these.
LIGHT_COLOR_FIELDS = [
    "DaylightColor", "LowBeamColor", "HighBeamColor",
    "RearLightColor", "BrakeLightColor", "ReverseLightColor",
    "FrontIndicatorColor", "RearIndicatorColor",
    "LFSpecialLightColor", "RFSpecialLightColor", "LRSpecialLightColor", "RRSpecialLightColor",
]


def _find_fixed_materials(car_root: Path) -> list[Path]:
    """Every "*_FIXED.material" under `car_root/materials` (recursive). Glob
    matching on the literal ".material" suffix already excludes
    "*_FIXED.material.bak" backups on its own - a plain substring search
    would not (a backup's name still *contains* "_FIXED.material", just not
    at the end)."""
    materials_dir = car_root / "materials"
    if not materials_dir.is_dir():
        raise ValueError('no "materials" folder under %s' % car_root)
    return sorted(p for p in materials_dir.rglob("*_FIXED.material") if p.is_file())


def api_materials_scan(q):
    folder = Path(q["dir"][0])
    if not folder.is_dir():
        raise ValueError("folder not found: %s" % folder)
    files = _find_fixed_materials(folder)

    values = {}
    if files:
        try:
            mf = mc.decode_material(files[0])
            by_name = {p.name: p for p in mf.properties}
            for field in LIGHT_COLOR_FIELDS:
                prop = by_name.get(field)
                if prop is not None and prop.kind == mc.KIND_VEC4:
                    values[field] = {str(k): v for k, v in prop.components.items()}
        except Exception:
            pass  # scan still reports the file list even if the first file is unreadable

    return {"files": [str(f) for f in files], "count": len(files), "values": values}


def api_materials_apply(body):
    folder = Path(body["dir"])
    if not folder.is_dir():
        raise ValueError("folder not found: %s" % folder)
    values = body.get("values") or {}
    fields = {name: {int(k): float(v) for k, v in comp.items()}
              for name, comp in values.items() if name in LIGHT_COLOR_FIELDS}
    if not fields:
        raise ValueError("no light-color values to apply")

    results = []
    for p in _find_fixed_materials(folder):
        try:
            mf = mc.decode_material(p)
            by_name = {pr.name: pr for pr in mf.properties}
            for name, components in fields.items():
                prop = by_name.get(name)
                if prop is None:
                    prop = mc.Property(name=name, kind=mc.KIND_VEC4, components=dict(components))
                    mf.items.append(prop)
                else:
                    prop.kind = mc.KIND_VEC4
                    prop.components = dict(components)

            data = mc.encode_material(mf)
            made = False
            bak = p.with_suffix(p.suffix + ".bak")
            if not bak.exists():
                shutil.copy2(p, bak)
                made = True
            p.write_bytes(data)
            results.append({"path": str(p), "ok": True, "backup_created": made})
        except Exception as exc:
            results.append({"path": str(p), "ok": False, "error": str(exc)})

    n_ok = sum(1 for r in results if r["ok"])
    return {"ok": True, "n_files": len(results), "n_updated": n_ok, "results": results}


# ---------------------------------------------------------------- .actor light emitters
#
# ActorData's light list looks like (real example, schema-named):
#   "2:msg:lights": [
#     {
#       "1:str:debug_name": "front left",
#       "3:msg:transform": {...},
#       "7:msg:color": {"1:f32:x": 0.882, "2:f32:y": 0.914, "3:f32:z": 1},
#     },
#     ...
#   ]
# Matching is done by FIELD NUMBER (2 for the list, 1/7/8 inside each entry),
# never by name: acevo_pb only resolves names when "data editor/proto/acevo.desc"
# has been extracted (see the Settings tab), and encode_message only ever
# looks at the number+type prefix of a key anyway (see its own docstring).


def _get_field(d, number, typ):
    """Reads field `number` (of wire-decoded type `typ`) from a decode_message
    dict, regardless of whether it fell into the normal named-key form or the
    "_seq" fallback (interleaved fields - see acevo_pb.decode_message)."""
    if "_seq" in d:
        for item in d["_seq"]:
            if item["f"] == number and item["t"] == typ:
                return item["v"]
        return None
    key = _find_field_key(d, number, typ)
    return d.get(key) if key else None


def _set_field(d, number, typ, name_hint, value):
    """In-place equivalent of _get_field - overwrites the field if present,
    otherwise adds it (as "<number>:<typ>:<name_hint>", acevo_pb only reads
    the number+type when re-encoding, the name is purely decorative)."""
    if "_seq" in d:
        for item in d["_seq"]:
            if item["f"] == number and item["t"] == typ:
                item["v"] = value
                return
        entry = {"f": number, "t": typ, "v": value}
        if name_hint:
            entry["n"] = name_hint
        d["_seq"].append(entry)
        return
    key = _find_field_key(d, number, typ)
    if key is None:
        key = "%d:%s:%s" % (number, typ, name_hint) if name_hint else "%d:%s" % (number, typ)
    d[key] = value


def _find_field_key(d, number, typ):
    prefix = "%d:%s" % (number, typ)
    for key in d:
        if key == prefix or key.startswith(prefix + ":"):
            return key
    return None


def _find_by_name(node, name):
    """First value anywhere in the tree whose decode_message key names it
    `name` (only possible when the schema resolved field names)."""
    if isinstance(node, dict):
        if "_seq" in node:
            for item in node["_seq"]:
                if item.get("n") == name:
                    return item["v"]
            for item in node["_seq"]:
                found = _find_by_name(item["v"], name)
                if found is not None:
                    return found
            return None
        for key, val in node.items():
            parts = key.split(":")
            if len(parts) >= 3 and parts[2] == name:
                return val
        for val in node.values():
            found = _find_by_name(val, name)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_by_name(item, name)
            if found is not None:
                return found
    return None


def _find_by_number_and_shape(node, number, typ, predicate):
    """First list-valued field `number` (of type `typ`) anywhere in the tree
    whose every element satisfies `predicate` - used when there is no
    schema-resolved name to go by (see _find_lights_array)."""
    if isinstance(node, dict):
        candidates = (
            [item["v"] for item in node["_seq"] if item["f"] == number and item["t"] == typ]
            if "_seq" in node else
            [val for key, val in node.items()
             if key == "%d:%s" % (number, typ) or key.startswith("%d:%s:" % (number, typ))]
        )
        for val in candidates:
            if isinstance(val, list) and all(isinstance(e, dict) and predicate(e) for e in val):
                return val
        children = [it["v"] for it in node["_seq"]] if "_seq" in node else list(node.values())
        for val in children:
            found = _find_by_number_and_shape(val, number, typ, predicate)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_by_number_and_shape(item, number, typ, predicate)
            if found is not None:
                return found
    return None


def _find_lights_array(tree):
    named = _find_by_name(tree, "lights")
    if isinstance(named, list):
        return named
    return _find_by_number_and_shape(
        tree, 2, "msg", lambda entry: _get_field(entry, 7, "msg") is not None)


def _get_color_rgb(color_dict):
    return (_get_field(color_dict, 1, "f32") or 0.0,
            _get_field(color_dict, 2, "f32") or 0.0,
            _get_field(color_dict, 3, "f32") or 0.0)


def _set_color_rgb(color_dict, r, g, b):
    _set_field(color_dict, 1, "f32", "x", r)
    _set_field(color_dict, 2, "f32", "y", g)
    _set_field(color_dict, 3, "f32", "z", b)


def _find_actor_file(folder: Path) -> Path | None:
    """The single .actor file at the root of `folder` (not recursive - the
    car's .actor always sits next to its "materials" folder, not inside it)."""
    matches = sorted(p for p in folder.glob("*.actor") if p.is_file())
    return matches[0] if matches else None


def _backup_once(path: Path) -> bool:
    bak = path.with_suffix(path.suffix + ".bak")
    if bak.exists():
        return False
    shutil.copy2(path, bak)
    return True


def api_actor_scan(q):
    folder = Path(q["dir"][0])
    if not folder.is_dir():
        raise ValueError("folder not found: %s" % folder)
    path = _find_actor_file(folder)
    if path is None:
        return {"path": None, "lights": []}

    tree = acevo_pb.decode_message(path.read_bytes(), acevo_pb.resolve_desc(str(path))[1])
    lights = _find_lights_array(tree) or []

    out = []
    for i, entry in enumerate(lights):
        color = _get_field(entry, 7, "msg") or {}
        r, g, b = _get_color_rgb(color)
        out.append({
            "index": i,
            "debug_name": _get_field(entry, 1, "str") or ("light %d" % i),
            "color": {"r": r, "g": g, "b": b},
        })
    return {"path": str(path), "lights": out}


def api_actor_apply(body):
    folder = Path(body["dir"])
    if not folder.is_dir():
        raise ValueError("folder not found: %s" % folder)
    edits = body.get("lights") or []
    if not edits:
        return {"ok": True, "path": None, "n_lights_changed": 0}

    path = _find_actor_file(folder)
    if path is None:
        raise ValueError("no .actor file at the root of this folder")

    tree = acevo_pb.decode_message(path.read_bytes(), acevo_pb.resolve_desc(str(path))[1])
    lights = _find_lights_array(tree)
    if lights is None:
        raise ValueError("could not locate the light list in this .actor file")

    changed = 0
    for e in edits:
        idx = e["index"]
        if not (0 <= idx < len(lights)):
            continue
        entry = lights[idx]
        color = _get_field(entry, 7, "msg")
        if color is None:
            color = {}
            _set_field(entry, 7, "msg", "color", color)
        _set_color_rgb(color, e["r"], e["g"], e["b"])
        _set_field(entry, 8, "varint", "override_color", 1)
        changed += 1

    data = acevo_pb.encode_message(tree)
    made = _backup_once(path)
    path.write_bytes(data)
    return {"ok": True, "path": str(path), "n_lights_changed": changed, "backup_created": made}


GET_ROUTES = {
    "/api/light_color/materials/scan": api_materials_scan,
    "/api/light_color/actor/scan": api_actor_scan,
}
POST_ROUTES = {
    "/api/light_color/materials/apply": api_materials_apply,
    "/api/light_color/actor/apply": api_actor_apply,
}


def register(get_routes: dict, post_routes: dict) -> None:
    get_routes.update(GET_ROUTES)
    post_routes.update(POST_ROUTES)
