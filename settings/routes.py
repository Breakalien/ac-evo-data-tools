"""
HTTP API for the Settings tab.

The generic settings.json key/value store itself (GET/POST /api/settings)
is shared UI infra - see "UI/settings_store.py" - since any tab could use it
to remember a preference. What's actually specific to the Settings tab is
the proto-schema extraction feature below: it drives "data editor/extract_protos.py"
(a library that belongs to data editor, since its only output is data
editor's own "proto/acevo.desc"), but the HTTP-facing glue - locating the
game exe, deciding where the descriptor set goes - is this tab's own concern.
"""
from __future__ import annotations

import os
from pathlib import Path

import extract_protos  # "data editor/" - added to sys.path by main.py

DATA_EDITOR_DIR = Path(__file__).resolve().parent.parent / "data editor"


def _find_acevo_exe(folder: Path, max_depth: int = 3) -> Path | None:
    """AssettoCorsaEVO.exe, at the root of `folder` or in a subfolder (some
    installs keep it a level or two down, e.g. under a build/version folder).

    Depth-bounded on purpose: an unbounded search (Path.rglob) can wander
    into a huge unrelated tree for minutes if the user points this at the
    wrong folder (e.g. their whole user profile) instead of failing fast."""
    direct = folder / "AssettoCorsaEVO.exe"
    if direct.is_file():
        return direct
    base_depth = len(folder.resolve().parts)
    for dirpath, dirnames, filenames in os.walk(folder):
        depth = len(Path(dirpath).parts) - base_depth
        if depth >= max_depth:
            dirnames[:] = []
            continue
        for fn in filenames:
            if fn.lower() == "assettocorsaevo.exe":
                return Path(dirpath) / fn
    return None


def api_extract_protos(body):
    folder = Path(body["dir"])
    if not folder.is_dir():
        raise ValueError("folder not found: %s" % folder)
    exe = _find_acevo_exe(folder)
    if exe is None:
        raise ValueError(
            "AssettoCorsaEVO.exe not found in this folder (neither at the root nor in subfolders)")

    outdir = DATA_EDITOR_DIR / "proto"
    desc_path = outdir / "acevo.desc"
    log: list[str] = []
    result = extract_protos.extract(str(exe), str(outdir), str(desc_path), log=log)
    return {"ok": True, "exe": str(exe), "log": log, **result}


POST_ROUTES = {
    "/api/settings/extract_protos": api_extract_protos,
}


def register(get_routes: dict, post_routes: dict) -> None:
    post_routes.update(POST_ROUTES)
