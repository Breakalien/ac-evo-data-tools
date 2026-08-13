"""
Logique non-UI de l'editeur de fichiers .material (categorisation des
proprietes, recherche, presets, schema de types, lien propriete<->texture,
coloration par canal). Extrait de l'ancien editeur customtkinter pour etre
reutilisable depuis une UI Qt sans dependance a un toolkit graphique.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import material_codec as mc

PRESETS_DIR = Path(__file__).parent / "presets"
SCHEMA_DIR = Path(__file__).parent / "schema"

# Root "blend_mode" field (protobuf field 2, see material_codec.py's module
# docstring and champ_cache_blendmode.pdf) - independent of the named
# "blendMode" property. Verified against 800 real .material files: only the
# Opaque pairing below is a confirmed 1:1 mapping (633/633 opaque-flagged
# files have blendMode property == 0.0, zero exceptions). Every other value
# was observed paired with DIFFERENT blendMode property values depending on
# shader/context (e.g. blend_mode=2 seen with property 0.0 on one
# UberBiplanarMaterial file and 1.0 on VehicleGlass files) - there is no
# single universal mapping beyond Opaque, so only Opaque is auto-synced by
# the editor; every other value is shown for visibility/manual editing only.
BLEND_MODE_OPAQUE = 0
BLEND_MODE_KNOWN_LABELS = {
    0: "0 - Opaque",
    1: "1 - seen on foliage/glass/fabric (no confirmed fixed mapping)",
    2: "2 - seen on glass/windows (no confirmed fixed mapping)",
    4: "4 - seen on grass/subsurface (no confirmed fixed mapping)",
    5: "5 - seen on smoke (Smoke)",
}

KIND_LABELS = {
    mc.KIND_UNSET: "disabled",
    mc.KIND_SCALAR: "scalar",
    mc.KIND_VEC2: "vec2",
    mc.KIND_VEC3: "vec3",
    mc.KIND_VEC4: "vec4",
}
LABEL_TO_KIND = {v: k for k, v in KIND_LABELS.items()}

# Catalogue complet des shaders AC EVO (vehicules + pistes/scenery), issu de
# I:\CLAUDE\etude materials\shader_data.json (scan de fichiers .material reels
# du jeu, cars + tracks). Les 10 premiers (vehicules) restent ceux reperes
# dans le contenu officiel + mods (ks_citroen_ra, ks_nsx_r_spo_5) ; les
# suivants (pistes/scenery/effets) ont ete ajoutes en meme temps que le
# support du "Cas" = track dans l'onglet Cablage.
KNOWN_SHADERS = [
    "UberVehicleMaterial",
    "UberVehicleInteriorMaterial",
    "VehicleGlass",
    "VehicleGlassInterior",
    "VehicleRim",
    "VehicleLight",
    "VehicleLed",
    "VehicleDisc",
    "VehicleWindow",
    "VehicleWindshield",
    "CrossImpostor",
    "DefaultMaterial",
    "DefaultTranslucent",
    "DynamicTrack",
    "DynamicTrackMultilayer",
    "Fence",
    "Glass",
    "Grass",
    "IdealLine",
    "Impostor",
    "InteriorMapping",
    "Particle",
    "Retroreflective",
    "Semaphore",
    "Skidmarks",
    "Subsurface",
    "TerrainNew",
    "TrafficVehicleGlass",
    "TrafficVehicleMaterial",
    "UberBiplanarMaterial",
    "UberInteriorMaterial",
    "UberMaterial",
    "VehicleTyre",
]

# Regroupement semantique des ~650 proprietes du shader Uber*. Chaque regle
# est (categorie, sous-categorie ou None, mots-cles requis [tous doivent
# matcher]). Premiere regle qui matche l'emporte ; l'ordre fixe la priorite
# de matching (pas l'ordre d'affichage, voir CATEGORY_ORDER plus bas). Les
# sous-categories ne sont ajoutees que la ou c'est utile (grosses categories,
# plusieurs "sujets" melanges) ; ailleurs sub=None -> la propriete est un
# enfant direct de la categorie.
#
# Toute propriete "normal" est centralisee dans une seule categorie
# "Normal / Relief", avec une sous-categorie qui indique QUEL calque elle
# affecte (vernis, dommages, saletes, detail tiling, ou surface de base) -
# plutot que d'etre eparpillee dans chacune de ces categories.
CATEGORY_RULES = [
    ("Normal / Relief", "Damage", ("damage", "normal")),
    ("Normal / Relief", "Dirt / Grime", ("dirt", "normal")),
    ("Normal / Relief", "Dirt / Grime", ("grime", "normal")),
    ("Normal / Relief", "Detail (tiling layer)", ("detail", "normal")),
    ("Normal / Relief", "Clear Coat", ("clearcoat", "normal")),
    ("Normal / Relief", "Surface (base)", ("normal",)),

    ("Damage", "Color", ("damage", "basecolor")),
    ("Damage", "Zones / Masks", ("damage", "area")),
    ("Damage", "Other", ("damage",)),

    ("Dirt / Grime", "Color", ("dirt", "basecolor")),
    ("Dirt / Grime", "Color", ("grime", "basecolor")),
    ("Dirt / Grime", "Other", ("dirt",)),
    ("Dirt / Grime", "Other", ("grime",)),

    ("Splatmap (livery mask)", None, ("splatmap",)),
    ("Scuff", None, ("scuff",)),
    ("Rain", None, ("rain",)),

    ("Detail (tiling layer)", "Roughness", ("detail", "roughness")),
    ("Detail (tiling layer)", "Other", ("detail",)),

    ("Clear Coat", "Roughness", ("clearcoat", "roughness")),
    ("Clear Coat", "Color / General", ("clearcoat",)),

    ("Base Color", None, ("basecolor",)),
    ("Metalness", None, ("metalness",)),
    ("Roughness", None, ("roughness",)),
    ("Roughness", None, ("gloss",)),
    ("Reflectance", None, ("reflectance",)),
    ("Reflectance", None, ("f0percentage",)),
    ("Anisotropy", None, ("anisotropy",)),
    ("Ambient Occlusion", None, ("ambientocclusion",)),
    ("Ambient Occlusion", None, ("aomap",)),
    ("Emissive", None, ("emissive",)),
    ("Opacity / Transparency", None, ("opacity",)),
    ("Opacity / Transparency", None, ("transpar",)),
    ("Opacity / Transparency", None, ("blendmode",)),
    ("Height / Parallax", None, ("height",)),
    ("Modulation", None, ("modulation",)),
    ("Fabric / Micro-detail (fiber, shadow)", None, ("microshadow",)),
    ("Fabric / Micro-detail (fiber, shadow)", None, ("microfiber",)),
    ("Fabric / Micro-detail (fiber, shadow)", None, ("lightwrap",)),
    ("Triplanar / Projection", None, ("triplanar",)),
    ("UV (scale/offset/channel/rotation)", None, ("uvscale",)),
    ("UV (scale/offset/channel/rotation)", None, ("uvoffset",)),
    ("UV (scale/offset/channel/rotation)", None, ("uvchannel",)),
    ("UV (scale/offset/channel/rotation)", None, ("rotateuv",)),
    ("UV (scale/offset/channel/rotation)", None, ("uvrotationangle",)),
    ("UV (scale/offset/channel/rotation)", None, ("blendcontrast",)),
    ("UV (scale/offset/channel/rotation)", None, ("custompbruv",)),
    ("Channel activation", None, ("_enable",)),
    ("Internal / reserved code", None, ("code",)),
]
# Display order in the tree - independent of the matching order above.
CATEGORY_ORDER = [
    "Damage",
    "Dirt / Grime",
    "Splatmap (livery mask)",
    "Scuff",
    "Rain",
    "Detail (tiling layer)",
    "Clear Coat",
    "Base Color",
    "Normal / Relief",
    "Metalness",
    "Roughness",
    "Reflectance",
    "Anisotropy",
    "Ambient Occlusion",
    "Emissive",
    "Opacity / Transparency",
    "Height / Parallax",
    "Modulation",
    "Fabric / Micro-detail (fiber, shadow)",
    "Triplanar / Projection",
    "UV (scale/offset/channel/rotation)",
    "Channel activation",
    "Internal / reserved code",
    "Other",
]


def categorize_property(name: str) -> tuple[str, str | None]:
    low = name.lower()
    for top, sub, keys in CATEGORY_RULES:
        if all(k in low for k in keys):
            return top, sub
    return "Other", None


def format_value(prop: mc.Property) -> str:
    if prop.kind == mc.KIND_UNSET:
        return "disabled"
    return ", ".join(f"{prop.components.get(i, 0.0):g}" for i in range(1, prop.kind + 1))


# -- Filtre de recherche -----------------------------------------------------
# Recherche multi-mots (chaque mot doit matcher, dans n'importe quel ordre,
# quelque part dans le "haystack"), insensible a la casse. Plus tolerant
# qu'une simple sous-chaine : "clear coat" (avec espace) retrouve bien
# "ClearCoatNormal" par exemple.
def search_tokens(raw: str) -> list[str]:
    return [t for t in raw.strip().lower().split() if t]


def matches_tokens(haystack: str, tokens: list[str]) -> bool:
    if not tokens:
        return True
    low = haystack.lower()
    return all(tok in low for tok in tokens)


# -- Coloration par canal (Base_/Red_/Green_/Blue_) --------------------------
# Meme convention de prefixe que project_resolver.CHANNEL_PREFIXES : les
# shaders UberVehicleMaterial* splittent plusieurs canaux de peinture
# (melanges par couleur de vertex), chacun avec ses proprietes prefixees.
CHANNEL_TEXT_COLORS = {
    "red": "#e05a4e",
    "green": "#5ec26a",
    "blue": "#5a9ee0",
}
# "Base" is deliberately handled separately from the loop above: unlike
# Red/Green/Blue (which have real camelCase variants like
# "RedOverrideReplace_BaseColor"), "Base" only ever means a channel prefix
# when followed by a literal underscore - "Basecolor" (no underscore) is a
# different, standalone property (the material's overall base colour, not
# part of the Base_/Red_/Green_/Blue_ channel-split system) and must NOT be
# colored as a channel.
BASE_TEXT_COLOR = "#d9c04e"

# Couleur de texte pour une propriete desactivee (kind == KIND_UNSET), visible
# uniquement quand "Afficher aussi les proprietes desactivees" est coche -
# volontairement plus sombre que le texte normal pour bien la distinguer des
# proprietes actives, y compris quand elle a aussi un prefixe de canal.
DISABLED_TEXT_COLOR = "#6a6d72"

# Couleur de la colonne "Texture liee" quand la propriete pointe reellement
# vers une texture assignee et resolue (pas juste un concept de texture sans
# fichier reel derriere - voir MaterialPage._preview_cache).
LINKED_TEXTURE_COLOR = "#5ec26a"


def channel_color(prop_name: str) -> str | None:
    """Couleur de texte (hex) pour une propriete dont le nom commence par un
    canal Red/Green/Blue/Base_ - pas seulement "Red_..." mais aussi les
    variantes sans underscore comme "RedOverrideReplace_BaseColor" ou
    "RedIgnore_...". Detecte via une frontiere de style camelCase : le
    mot-couleur doit etre suivi de la fin du nom, d'un underscore, ou d'une
    majuscule - jamais d'une minuscule (ce qui exclurait un mot anglais qui
    commencerait pareil par coincidence). "Base" est un cas a part : seul
    "Base_" (underscore litteral) compte, "Basecolor" est ignore. None si
    aucun canal ne matche."""
    if prop_name[:5].lower() == "base_":
        return BASE_TEXT_COLOR
    for word, color in CHANNEL_TEXT_COLORS.items():
        if prop_name[:len(word)].lower() == word:
            rest = prop_name[len(word):]
            if not rest or not rest[0].islower():
                return color
    return None


# -- Lien propriete -> slot de texture -------------------------------------
# Deduit par pattern sur le nom (canal Red/Green/Blue/Base + "concept"), verifie
# contre les VRAIS noms de slots de texture du fichier ouvert (pas une simple
# supposition). Volontairement imparfait : les reglages qui ne pilotent aucune
# texture particuliere (Splatmap_*, Triplanar, Code_*Pad, reglages globaux...)
# restent honnetement "non lie" plutot que d'etre rattaches au hasard.
TEX_SYSTEMS = ("damage", "dirt", "grime", "scuff", "rain", "splatmap")
TEX_CONCEPTS = [
    "ClearCoatNormal", "ClearCoatRoughness", "ClearCoat",
    "AmbientOcclusion", "BaseColor", "DetailMask", "Anisotropy",
    "Metalness", "Roughness", "Height", "Emissive", "Opacity", "Normal", "Reflectance",
]
TEX_ALIAS = {
    "Metalness": "txMetalness", "Roughness": "txRoughness", "Normal": "txNormal",
    "Height": "txHeight", "AmbientOcclusion": "txAO",
}


def _find_concept(s: str) -> str | None:
    low = s.lower()
    for c in TEX_CONCEPTS:
        if c.lower() in low:
            return c
    return None


def linked_texture_name(prop_name: str, texture_names: set[str]) -> str | None:
    low = prop_name.lower()
    system = next((s for s in TEX_SYSTEMS if s in low), None)
    if system:
        candidates = [t for t in texture_names if system in t.lower()]
        concept = _find_concept(prop_name)
        if concept:
            exact = [t for t in candidates if concept.lower() in t.lower()]
            if len(exact) == 1:
                return exact[0]
        if len(candidates) == 1:
            return candidates[0]
        return prop_name if prop_name in texture_names else None

    channel, rest = None, prop_name
    for ch in ("Red", "Green", "Blue", "Base"):
        if prop_name.startswith(ch):
            channel, rest = ch, prop_name[len(ch):]
            break
    else:
        if prop_name.startswith("ks"):
            rest = prop_name[2:]

    concept = _find_concept(rest)
    if concept is None:
        return None

    candidates = []
    if channel:
        candidates.append(f"{channel}_{concept}Map")
    candidates.append(f"{concept}Map")
    if concept == "BaseColor":
        candidates.append("txDiffuse")
    if concept in TEX_ALIAS:
        candidates.append(TEX_ALIAS[concept])

    return next((c for c in candidates if c in texture_names), None)


# -- Presets (etat des proprietes, par shader) --------------------------------
def _safe_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", name.strip())


def preset_dir_for_shader(shader: str) -> Path:
    return PRESETS_DIR / _safe_filename(shader)


def list_presets(shader: str) -> list[str]:
    d = preset_dir_for_shader(shader)
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.json"))


def save_preset(shader: str, name: str, mf: mc.MaterialFile, save_textures: bool = False) -> Path:
    d = preset_dir_for_shader(shader)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{_safe_filename(name)}.json"
    payload = {"shader": shader, "properties": mc.properties_to_dict(mf.properties), "blend_mode": mf.blend_mode}
    if save_textures:
        payload["textures"] = mc.textures_to_dict(mf.textures)
    path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return path


def load_preset_data(shader: str, name: str) -> dict:
    path = preset_dir_for_shader(shader) / f"{_safe_filename(name)}.json"
    return json.loads(path.read_text(encoding="utf-8"))


# -- Schema de types (scalar/vec2/vec3/vec4) par propriete, par shader -------
_schema_cache: dict[str, dict[str, int]] = {}


def load_schema(shader: str) -> dict[str, int]:
    """Type (scalar/vec2/vec3/vec4) connu pour chaque propriete de `shader`,
    construit a l'avance par build_schema.py en scannant les .material reels
    du jeu. Vide si le shader n'a jamais ete scanne."""
    if shader in _schema_cache:
        return _schema_cache[shader]
    path = SCHEMA_DIR / f"{_safe_filename(shader)}.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    _schema_cache[shader] = data
    return data


def guess_kind(shader: str, prop_name: str) -> tuple[int, bool]:
    """Type devine pour activer `prop_name`. Renvoie (kind, connu_avec_certitude)."""
    schema = load_schema(shader)
    if prop_name in schema:
        return schema[prop_name], True
    return mc.KIND_SCALAR, False
