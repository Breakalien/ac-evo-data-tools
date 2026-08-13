"""
HTTP API for the material editor (.material files for Assetto Corsa EVO).

Replaces all the Qt glue that used to live in UACEC2's material_page.py:
this module is stateless (like "data editor/routes.py") - every request
carries the full property/texture list it needs, and the server never
holds a "currently open file" in memory. The only thing cached across
requests is a per-project-root ProjectIndex (expensive to build, cheap to
reuse), exactly like the Qt app cached it per open() call.

material_codec.py and material_editor_logic.py do all the actual decoding/
encoding/business-logic work here; this file is just the HTTP <-> Python
translation layer plus a couple of small helpers (content-root guessing,
JSON<->dataclass conversion) that used to live inline in material_page.py.
"""
from __future__ import annotations

import base64
import os
import shutil
from pathlib import Path

import material_codec as mc
import material_editor_logic as logic
import texture_preview
from project_resolver import ProjectIndex
from server import RawResponse  # UI/server.py - added to sys.path by main.py

NO_TEXTURE_PATH = Path(__file__).resolve().parent / "no_texture.png"


# ---------------------------------------------------------------- content root / project index

def _guess_content_root(material_path: str) -> str:
    """A .material lives under <content_root>/.../materials/X.material - walk
    up to (not including) the first "materials" segment to get a root usable
    with ProjectIndex for texture-slot resolution."""
    parts = os.path.normpath(os.path.dirname(material_path)).split(os.sep)
    lower = [p.lower() for p in parts]
    if "materials" in lower:
        idx = lower.index("materials")
        return os.sep.join(parts[:idx]) or os.sep
    return os.path.dirname(material_path)


_INDEX_CACHE_MAX = 4
_index_cache: dict[str, ProjectIndex] = {}
_index_cache_order: list[str] = []


def _get_index(material_path: str) -> ProjectIndex | None:
    root = _guess_content_root(material_path)
    if root in _index_cache:
        return _index_cache[root]
    try:
        index = ProjectIndex(root)
    except Exception:
        return None
    _index_cache[root] = index
    _index_cache_order.append(root)
    if len(_index_cache_order) > _INDEX_CACHE_MAX:
        _index_cache.pop(_index_cache_order.pop(0), None)
    return index


# ---------------------------------------------------------------- JSON <-> dataclass

def _prop_to_json(p: mc.Property, texture_names: set[str], resolved_names: set[str]) -> dict:
    top, sub = logic.categorize_property(p.name)
    linked = logic.linked_texture_name(p.name, texture_names)
    return {
        "name": p.name,
        "kind": p.kind,
        "components": {str(k): v for k, v in p.components.items()},
        "category": top,
        "sub": sub,
        "channel_color": logic.channel_color(p.name),
        "linked_texture": linked,
        "linked_texture_resolved": bool(linked and linked in resolved_names),
        "value_display": logic.format_value(p),
    }


def _prop_from_json(d: dict) -> mc.Property:
    return mc.Property(
        name=d["name"], kind=int(d.get("kind", 0)),
        components={int(k): float(v) for k, v in d.get("components", {}).items()},
    )


def _tex_to_json(t: mc.TextureSlot) -> dict:
    return {"name": t.name, "path": t.path, "channel_color": logic.channel_color(t.name)}


def _tex_from_json(d: dict) -> mc.TextureSlot:
    return mc.TextureSlot(name=d["name"], path=(d.get("path") or None))


def _raw_to_json(r: mc.RawField) -> dict:
    val = base64.b64encode(r.value).decode("ascii") if r.wiretype == "bytes" else r.value
    return {"field_no": r.field_no, "wiretype": r.wiretype, "value": val}


def _raw_from_json(d: dict) -> mc.RawField:
    val = base64.b64decode(d["value"]) if d["wiretype"] == "bytes" else d["value"]
    return mc.RawField(field_no=d["field_no"], wiretype=d["wiretype"], value=val)


def _resolved_texture_names(mf: mc.MaterialFile, index: ProjectIndex | None) -> set[str]:
    if index is None:
        return set()
    return {t.name for t in mf.textures if t.path and index.find_image_asset(t.path)}


def _missing_schema_properties(shader: str, existing_names) -> list[mc.Property]:
    """Property objects for every name the shader's schema knows about that
    isn't already in `existing_names` - as KIND_UNSET placeholders, so they
    show up (toggleable) in the tree instead of being invisible just because
    this particular file's raw property list happens not to include them.

    Safe to add: a Property entry with kind=UNSET encodes to an (almost)
    empty message, exactly like the thousands of "declared but inactive"
    entries real game-authored .material files already carry - see
    material_codec.py's module docstring and _encode_value(KIND_UNSET)."""
    schema = logic.load_schema(shader) if shader else {}
    existing = set(existing_names)
    return [mc.Property(name=n, kind=mc.KIND_UNSET, components={})
            for n in schema if n not in existing]


# ---------------------------------------------------------------- API

def api_shaders(q):
    return {"shaders": logic.KNOWN_SHADERS}


def api_constants(q):
    """Static reference data the frontend needs available immediately at
    page load, independent of any file being open - mirrors the Qt app's
    combo boxes, which were always populated regardless of open-file state."""
    return {
        "shaders": logic.KNOWN_SHADERS,
        "blend_mode_labels": logic.BLEND_MODE_KNOWN_LABELS,
        "blend_mode_opaque": logic.BLEND_MODE_OPAQUE,
        "kind_labels": logic.KIND_LABELS,
    }


def api_schema(q):
    shader = q.get("shader", [""])[0].strip()
    return {"schema": logic.load_schema(shader) if shader else {}}


def api_shader_properties(body):
    """Called when the shader field changes: the new schema, plus any
    property it knows about that the currently-open file doesn't have an
    entry for yet (ready to append to the client's property list - see
    _missing_schema_properties). Without this, switching to a shader whose
    schema includes a property the file never declared would leave that
    property permanently invisible, even with "show disabled" on."""
    shader = (body.get("shader") or "").strip()
    existing_names = [d["name"] for d in body.get("existing", [])]
    texture_names = set(body.get("texture_names") or [])
    missing = _missing_schema_properties(shader, existing_names)
    return {
        "schema": logic.load_schema(shader) if shader else {},
        "missing_properties": [_prop_to_json(pr, texture_names, set()) for pr in missing],
    }


def api_open(q):
    p = Path(q["path"][0])
    if not p.is_file():
        raise ValueError("file not found")
    try:
        mf = mc.decode_material(p)
    except Exception as exc:
        raise ValueError("cannot decode this .material file: %s" % exc)

    index = _get_index(str(p))
    texture_names = {t.name for t in mf.textures}
    resolved_names = _resolved_texture_names(mf, index)
    raw_items = [_raw_to_json(it) for it in mf.items if isinstance(it, mc.RawField)]

    properties = list(mf.properties)
    properties += _missing_schema_properties(mf.shader_name, (p.name for p in properties))

    return {
        "path": str(p), "name": p.name, "dir": str(p.parent),
        "backup": p.with_suffix(p.suffix + ".bak").exists(),
        "shader_name": mf.shader_name,
        "blend_mode": mf.blend_mode,
        "properties": [_prop_to_json(pr, texture_names, resolved_names) for pr in properties],
        "textures": [_tex_to_json(t) for t in mf.textures],
        "raw_items": raw_items,
        "presets": logic.list_presets(mf.shader_name) if mf.shader_name else [],
        "schema": logic.load_schema(mf.shader_name) if mf.shader_name else {},
        "shaders": logic.KNOWN_SHADERS,
        "category_order": logic.CATEGORY_ORDER,
        "blend_mode_labels": logic.BLEND_MODE_KNOWN_LABELS,
        "blend_mode_opaque": logic.BLEND_MODE_OPAQUE,
        "kind_labels": logic.KIND_LABELS,
    }


def api_save(body):
    p = Path(body["path"])
    shader_name = (body.get("shader_name") or "").strip()
    blend_mode = int(body.get("blend_mode", 0))
    properties = [_prop_from_json(d) for d in body.get("properties", [])]
    textures = [_tex_from_json(d) for d in body.get("textures", [])]
    raw_items = [_raw_from_json(d) for d in body.get("raw_items", [])]

    mf = mc.MaterialFile(shader_name=shader_name,
                         items=[*properties, *textures, *raw_items],
                         blend_mode=blend_mode)
    try:
        data = mc.encode_material(mf)
    except Exception as exc:
        raise ValueError("cannot encode this material: %s" % exc)

    made = False
    if p.exists():
        bak = p.with_suffix(p.suffix + ".bak")
        if not bak.exists():
            shutil.copy2(p, bak)
            made = True
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return {"ok": True, "backup_created": made, "path": str(p), "size": len(data)}


def api_texture_thumb(q):
    material_path = q.get("material", [""])[0]
    slot = q.get("slot", [""])[0]
    size = int(q.get("size", [str(texture_preview.PREVIEW_TARGET)])[0])

    png_bytes = None
    try:
        p = Path(material_path)
        if p.is_file() and slot:
            mf = mc.decode_material(p)
            tex = next((t for t in mf.textures if t.name == slot), None)
            if tex and tex.path:
                index = _get_index(str(p))
                resolved = index.find_image_asset(tex.path) if index else None
                if resolved:
                    png_bytes = texture_preview.decode_png(resolved, target=size)
    except Exception:
        png_bytes = None

    if png_bytes is None:
        png_bytes = NO_TEXTURE_PATH.read_bytes()
    return RawResponse(png_bytes, "image/png")


def api_presets(q):
    shader = q.get("shader", [""])[0].strip()
    return {"presets": logic.list_presets(shader) if shader else []}


def api_preset_save(body):
    shader = (body.get("shader") or "").strip()
    name = (body.get("name") or "").strip()
    if not shader or not name:
        raise ValueError("shader and preset name are required")
    save_textures = bool(body.get("save_textures"))
    properties = [_prop_from_json(d) for d in body.get("properties", [])]
    textures = [_tex_from_json(d) for d in body.get("textures", [])]
    blend_mode = int(body.get("blend_mode", 0))

    mf = mc.MaterialFile(shader_name=shader, items=[*properties, *textures], blend_mode=blend_mode)
    path = logic.save_preset(shader, name, mf, save_textures=save_textures)
    return {"ok": True, "path": str(path), "presets": logic.list_presets(shader)}


def api_preset_apply(body):
    shader = (body.get("shader") or "").strip()
    name = (body.get("name") or "").strip()
    try:
        data = logic.load_preset_data(shader, name)
    except FileNotFoundError:
        raise ValueError("preset '%s' not found for shader '%s'" % (name, shader))

    saved_shader = data.get("shader", shader)
    current_shader = (body.get("shader_name") or "").strip()
    if saved_shader != current_shader and not body.get("confirm"):
        return {"ok": False, "shader_mismatch": True, "saved_shader": saved_shader}

    properties = [_prop_from_json(d) for d in body.get("properties", [])]
    textures = [_tex_from_json(d) for d in body.get("textures", [])]
    blend_mode = int(body.get("blend_mode", 0))

    if body.get("load_values"):
        applied, skipped = mc.apply_properties_from_dict(properties, data["properties"])
        mode = "values"
        if body.get("path") is not None and "blend_mode" in data:
            blend_mode = data["blend_mode"]
    else:
        applied, skipped = mc.apply_activation_from_dict(properties, data["properties"])
        mode = "activation"

    tex_applied = tex_skipped = 0
    tex_status = None
    if body.get("load_textures"):
        if "textures" not in data:
            tex_status = "no_textures_in_preset"
        else:
            tex_mode = body.get("texture_mode", "fill")
            tex_applied, tex_skipped = mc.apply_textures_from_dict(textures, data["textures"], tex_mode)

    index = _get_index(body["path"]) if body.get("path") else None
    texture_names = {t.name for t in textures}
    resolved_names = set()
    if index is not None:
        resolved_names = {t.name for t in textures if t.path and index.find_image_asset(t.path)}

    return {
        "ok": True, "mode": mode, "applied": applied, "skipped": skipped,
        "tex_applied": tex_applied, "tex_skipped": tex_skipped, "tex_status": tex_status,
        "blend_mode": blend_mode,
        "properties": [_prop_to_json(pr, texture_names, resolved_names) for pr in properties],
        "textures": [_tex_to_json(t) for t in textures],
    }
GET_ROUTES = {
    "/api/material/shaders": api_shaders,
    "/api/material/constants": api_constants,
    "/api/material/schema": api_schema,
    "/api/material/open": api_open,
    "/api/material/presets": api_presets,
    "/api/material/texture_thumb": api_texture_thumb,
}
POST_ROUTES = {
    "/api/material/save": api_save,
    "/api/material/preset/save": api_preset_save,
    "/api/material/preset/apply": api_preset_apply,
    "/api/material/shader_properties": api_shader_properties,
}


def register(get_routes: dict, post_routes: dict) -> None:
    get_routes.update(GET_ROUTES)
    post_routes.update(POST_ROUTES)
