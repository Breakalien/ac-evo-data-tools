"""
Generic app-wide key/value settings store - plain JSON on disk, same pattern
UACEC2's app/settings.py used. Lives in UI/ (shared infra, not owned by any
one tab) since any tab may want to remember a preference this way; the
Settings tab (see "settings/routes.py") is just its main/first consumer,
currently for a single key (acevo_dir).
"""
from __future__ import annotations

import json
from pathlib import Path

SETTINGS_PATH = Path(__file__).resolve().parent.parent / "settings.json"

DEFAULTS = {
    "acevo_dir": None,   # Assetto Corsa EVO install folder, used to extract .proto schemas
}


def load() -> dict:
    data = dict(DEFAULTS)
    try:
        data.update(json.loads(SETTINGS_PATH.read_text(encoding="utf-8")))
    except Exception:
        pass
    return data


def save(data: dict) -> None:
    SETTINGS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def api_get_settings(q):
    return load()


def api_save_settings(body):
    data = load()
    data.update(body)
    save(data)
    return {"ok": True, **data}


GET_ROUTES = {"/api/settings": api_get_settings}
POST_ROUTES = {"/api/settings": api_save_settings}


def register(get_routes: dict, post_routes: dict) -> None:
    get_routes.update(GET_ROUTES)
    post_routes.update(POST_ROUTES)
