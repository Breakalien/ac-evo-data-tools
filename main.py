#!/usr/bin/env python3
"""
ACE AIO - Assetto Corsa EVO tools, all in one browser UI.

Single process, single HTTP server: serves the shared web shell (UI/static)
and mounts both the API routes and the frontend (own "static/" folder) that
each independent tab module contributes ("data editor", "material editor",
"light color", "settings"). Each module is a fully self-contained,
independently updatable unit - own backend, own frontend, nothing of its own
living anywhere else - this file is only the thin composition root that
wires them together, kept deliberately small so PyInstaller has one obvious
entry point to freeze.

Usage:
  python main.py [--dir <start folder>] [--port 8765] [--no-browser]
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# url prefix -> module folder. The prefix is used both for its API routes
# (registered by each module's own routes.py) and for its frontend, served
# from "<folder>/static/" - see ui_server.register_static below.
MODULES = {
    "data-editor": ROOT / "data editor",
    "material-editor": ROOT / "material editor",
    "light-color": ROOT / "light color",
    "settings": ROOT / "settings",
}

# Every module folder goes on sys.path so its files can import each other
# (and, deliberately, each other's sibling modules - e.g. "light color"
# reuses material_codec.py and acevo_pb.py as libraries) with plain flat
# imports, the same convention each module already used standalone. Folder
# names contain spaces, so they can only be reached this way, never as
# dotted packages.
sys.path.insert(0, str(ROOT / "UI"))
for folder in MODULES.values():
    sys.path.insert(0, str(folder))

import server as ui_server      # noqa: E402  (UI/server.py)
import settings_store           # noqa: E402  (UI/settings_store.py - generic KV store, shared infra)


def _load_routes_module(name: str, folder: Path):
    spec = importlib.util.spec_from_file_location(name, folder / "routes.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=str(ROOT / "content"),
                    help="folder opened on startup in the data editor tab")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()

    routes = {}
    for prefix, folder in MODULES.items():
        mod = _load_routes_module(prefix.replace("-", "_") + "_routes", folder)
        mod.register(ui_server.GET_ROUTES, ui_server.POST_ROUTES)
        ui_server.register_static(prefix, folder / "static")
        routes[prefix] = mod
    settings_store.register(ui_server.GET_ROUTES, ui_server.POST_ROUTES)

    data_routes = routes["data-editor"]
    d = Path(a.dir).expanduser()
    data_routes.set_start_dir(d.resolve() if d.is_dir() else Path.home())

    try:
        srv = ui_server.create_server("127.0.0.1", a.port)
    except OSError as e:
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

    schema = data_routes.schema_status()
    if not schema["ok"]:
        print("NOTE: protobuf schemas not found (%s)." % schema["path"])
        print("      Files still open and save byte-exactly, but fields show as")
        print("      numbers instead of names. Generate them from the Settings tab,")
        print("      or once via: python \"data editor/extract_protos.py\" "
              "\"<path>/AssettoCorsaEVO.exe\" -o \"data editor/proto\" "
              "-d \"data editor/proto/acevo.desc\"\n")

    url = "http://127.0.0.1:%d/?t=%s" % (a.port, ui_server.TOKEN)
    print("start folder : %s" % data_routes.START_DIR)
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
