"""
Construit un "schema" par shader : pour chaque nom de propriete, quel type
(scalar/vec2/vec3/vec4) il utilise quand il est actif. Deduit en scannant
tous les .material reels disponibles (jeu officiel + mods extraits) et en
retenant, pour chaque propriete, le type le plus souvent observe parmi les
occurrences ou elle n'est PAS desactivee.

Resultat : schema/<shader>.json  ->  {"NomPropriete": kind_int, ...}

Sert a deviner automatiquement le type quand l'utilisateur active une
propriete actuellement desactivee dans l'editeur (au lieu de lui demander
de choisir scalar/vec2/vec3/vec4 a l'aveugle).

Usage: python build_schema.py [dossier_racine]
Le dossier racine par defaut est le dossier "cars" d'Assetto Corsa EVO.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import material_codec as mc

DEFAULT_ROOT = Path(r"C:\Users\Alien\Saved Games\ACE\mods")
SCHEMA_DIR = Path(__file__).parent / "schema"


def build(root: Path) -> None:
    files = list(root.rglob("*.material"))
    print(f"Scan de {len(files)} fichiers .material sous {root} ...")

    # shader -> nom propriete -> Counter(kind -> occurrences)
    votes: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
    errors = 0

    for f in files:
        try:
            mf = mc.decode_material(f)
        except Exception:
            errors += 1
            continue
        for prop in mf.properties:
            if prop.kind == mc.KIND_UNSET:
                continue
            votes[mf.shader_name][prop.name][prop.kind] += 1

    SCHEMA_DIR.mkdir(exist_ok=True)
    for shader, prop_votes in votes.items():
        schema = {name: counter.most_common(1)[0][0] for name, counter in prop_votes.items()}
        safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in shader)
        path = SCHEMA_DIR / f"{safe}.json"
        path.write_text(json.dumps(schema, indent=1, sort_keys=True), encoding="utf-8")
        print(f"  {shader:32s} -> {len(schema):4d} proprietes connues  ({path.name})")

    if errors:
        print(f"({errors} fichiers ignores car illisibles)")


if __name__ == "__main__":
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_ROOT
    build(root)
