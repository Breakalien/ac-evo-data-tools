#!/usr/bin/env python3
"""
acevo_decode - decode Assetto Corsa EVO files with their REAL field names.

Fully named JSON, using the schemas extracted from the game executable.

Note: unknown fields are dropped by this path, so files whose content predates
the current schema lose information when re-encoded. Use acevo_pb.py to edit
those. Requires proto/acevo.desc.

Usage:
  python acevo_decode.py decode <file> [-o out.json]
  python acevo_decode.py encode <file.json> -o <binary>   # type from "_message"
  python acevo_decode.py batch <folder> -o <json_folder>
  python acevo_decode.py types                            # extension -> message table
"""

import argparse
import json
import os
import sys
from pathlib import Path

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
from google.protobuf.json_format import MessageToDict, ParseDict

DESC = Path(__file__).resolve().parent / "proto" / "acevo.desc"

# Extension -> root message. A LIST means a polymorphic format: the right
# message is picked per file.
EXT_MAP = {
    ".actor": "ActorData",
    ".aicardata": "AiCarData",
    ".animation": "AnimationData",
    ".brakes": "BrakeDiscData",
    ".brakesystem": "BrakesData",
    ".car": "CarData",
    ".caranalogicsystem": "CarAnalogicSystemData",
    ".cardescription": "GeneralCarData",
    ".cardesign": "CarDesignData",
    ".cardisplaysystem": "CarDisplaySystemData",
    ".carelectronics": "ElectronicsData",
    ".carengine": "EngineData",
    ".carexhaustbackfire": "CarExhaustBackFireData",
    ".carfinalstate": "CarFinalStateData",
    ".carkit": "CarKitData",
    ".carledsystem": "CarLedSystemData",
    ".carlightingsystem": "CarLightingSystemData",
    ".carpart": "CarPartData",
    ".carparticleemitterdata_emitterspack": "CarParticleEmitterData_EmittersPack",
    ".carsetup": "CarSetupData",
    ".carsetuplimits": "CarSetupDataLimits",
    ".carsetupunits": "CarSetupDataUnits",
    ".carshadingdynamicsetting": "CarShadingDynamicSetting",
    ".carshadingglobalsetting": "CarShadingGlobalSetting",
    ".cartests": "AiCarData.TestResult",
    ".carwipersystem": "CarWiperSystemData",
    ".clutch": "ClutchData",
    ".coilover": "CoiloverData",
    ".compatiblepart": "CompatiblePartData",
    ".compatiblerim": "CompatibleRimData",
    ".compatibletyres": "CompatibleTyresData",
    ".compoundgenerator": "TyreCompoundGeneratorData",
    ".curve": "CurveDataEx",
    ".curve4": "CurvesDataEx",
    ".dampercurves": "DamperCurvesList",
    ".design": "DesignData",
    ".driverfinalstate": "DriverFinalStateData",
    ".drivetrain": "DrivetrainData",
    ".gearbox": "GearBoxData",
    ".material": "MaterialData",
    ".mechanicalcarpreset": "MechanicalCarPresetData",
    ".mesh": "MeshData",
    ".oemmultilayercolor": "OemMultilayerColorData",
    ".oemsinglelayercolor": "OemSinglelayerColorData",
    ".pactyre": ["TyresModelData", "TyresData"],
    ".rimmesh": "RimMeshData",
    ".scene": "SceneData",
    ".surface3d": "Surface3dData",
    # wrapper: basic_data + one geometry sub-message chosen among 8
    # (dW_data, strut, axle, multi_link_data, trailing_arm_data, ...)
    ".suspension": "SuspensionGeometryData",
    ".table": "TableData",
    ".texture": "TextureMetadata",
    ".tuningpart": "TuningPart",
    ".turbo": "TurboData",
    # wrapper: name/shortName + tyreData/modelData/thermalData/
    # pressureData/camberData/speedSensitivity/rollingResistance/...
    ".tyre": "TyresCompoundData",
    ".tyremesh": "TyreMeshData",
    ".tyretextures": "TyreTexturesData",
    ".visualcarpreset": "VisualCarPresetData",
    ".wing": "WingData",

    # ---- tracks (content/tracks) ----
    ".aisplinedata": "AISplineData",
    ".aisplinegenomes": "AISplineGenomes",
    ".dynamictrackpreset": "DynamicTrackPresetData",
    ".dynamictrackpresetcompressed": "DynamicTrackPresetCompressedData",
    ".dynamictracksettings": "DynamicTrackSettingsData",
    ".environmentshadingglobalsetting": "EnvironmentShadingGlobalSetting",
    ".fogsettings": "FogSettings",
    ".graphicsoverride": "GraphicsSettingsOverride",
    ".particleemitter": "ParticleEmitterData",
    ".postprocessing": "PostProcessingSettings",
    ".reference": "AISplineReferenceData",
    ".seasondefinition": "SeasonDefinition",
    ".track": "TrackData",
    ".trackcontrolpoints": "TrackControlPoints",
    ".tvcamsettings": "TVCamSettings",
}

# Not protobuf: .bin (splinedata), .data (already JSON), .track_layout,
# and web/font/image assets.

_pool = None


def get_pool():
    global _pool
    if _pool is not None:
        return _pool
    if not DESC.exists():
        # A plain exception, not sys.exit: callers that can degrade gracefully
        # (acevo_pb falls back to numbered fields) must be able to catch it.
        raise RuntimeError(
            "Schemas not found: %s\n"
            "Generate them once from your own copy of the game:\n"
            "  python tools/extract_protos.py "
            "\"<path>/AssettoCorsaEVO.exe\" -o proto -d proto/acevo.desc" % DESC)
    fds = descriptor_pb2.FileDescriptorSet()
    fds.ParseFromString(DESC.read_bytes())
    by_name = {f.name: f for f in fds.file}
    pool = descriptor_pool.DescriptorPool()
    done = set()

    def add(name, stack=()):
        if name in done or name in stack:
            return
        fd = by_name.get(name)
        if fd is None:
            return
        for dep in fd.dependency:
            add(dep, stack + (name,))
        try:
            pool.Add(fd)
            done.add(name)
        except Exception:
            pass

    for n in list(by_name):
        add(n)
    _pool = pool
    return pool


def get_class(name):
    return message_factory.GetMessageClass(get_pool().FindMessageTypeByName(name))


def has_unknown(msg, cls):
    """True when the binary carried fields the schema does not declare."""
    clean = cls()
    clean.CopyFrom(msg)
    clean.DiscardUnknownFields()
    return clean != msg


def resolve(raw, ext):
    """Pick the right message. For polymorphic formats, keep the one that
    re-serialises identically."""
    cands = EXT_MAP.get(ext)
    if cands is None:
        return None, None, None
    if isinstance(cands, str):
        cands = [cands]
    best = None
    for name in cands:
        try:
            cls = get_class(name)
        except KeyError:
            continue
        msg = cls()
        try:
            if msg.ParseFromString(raw) != len(raw):
                continue
            exact = msg.SerializeToString() == raw
        except Exception:
            continue
        if exact:
            return name, msg, True
        if best is None:
            best = (name, msg, False)
    return best if best else (None, None, None)


def decode_file(path):
    raw = Path(path).read_bytes()
    ext = Path(path).suffix.lower()
    name, msg, exact = resolve(raw, ext)
    if name is None:
        raise ValueError("no schema fits %s (extension %s)" % (path, ext))
    doc = {
        "_file": Path(path).name,
        "_message": name,
        "_size": len(raw),
        "_roundtrip_exact": exact,
        "_unknown_fields": has_unknown(msg, get_class(name)),
        "data": MessageToDict(msg, preserving_proto_field_name=True),
    }
    return doc


def do_decode(path, out=None):
    doc = decode_file(path)
    text = json.dumps(doc, indent=2, ensure_ascii=False)
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text, encoding="utf-8")
        print("-> %s  [%s] exact round-trip: %s" %
              (out, doc["_message"], doc["_roundtrip_exact"]))
    else:
        print(text)


def do_encode(json_path, out):
    doc = json.loads(Path(json_path).read_text(encoding="utf-8"))
    msg = get_class(doc["_message"])()
    ParseDict(doc["data"], msg)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_bytes(msg.SerializeToString())
    print("-> %s [%s]" % (out, doc["_message"]))


def do_batch(src, dst):
    src, dst = Path(src), Path(dst)
    ok = skipped = failed = 0
    for f in sorted(src.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in EXT_MAP:
            continue
        rel = f.relative_to(src)
        try:
            doc = decode_file(f)
        except Exception as e:
            failed += 1
            print("ERROR %s: %s" % (rel, e))
            continue
        out = dst / rel.with_suffix(rel.suffix + ".json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
        ok += 1
    print("\n%d files decoded, %d errors -> %s" % (ok, failed, dst))


def do_types():
    print("%-38s %s" % ("extension", "message"))
    for ext in sorted(EXT_MAP):
        v = EXT_MAP[ext]
        print("%-38s %s" % (ext, v if isinstance(v, str) else " | ".join(v)))


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("decode"); d.add_argument("input"); d.add_argument("-o", "--output")
    e = sub.add_parser("encode"); e.add_argument("input"); e.add_argument("-o", "--output", required=True)
    b = sub.add_parser("batch"); b.add_argument("input"); b.add_argument("-o", "--output", required=True)
    sub.add_parser("types")
    a = p.parse_args()
    try:
        if a.cmd == "decode":
            do_decode(a.input, a.output)
        elif a.cmd == "encode":
            do_encode(a.input, a.output)
        elif a.cmd == "batch":
            do_batch(a.input, a.output)
        elif a.cmd == "types":
            do_types()
    except RuntimeError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    sys.exit(main())
