#!/usr/bin/env python3
"""
acevo_pb - decode/encode Assetto Corsa EVO content files.

Generic decoder/encoder: reads the protobuf wire format, infers each field's
type, and produces JSON that re-encodes to the exact same bytes.

JSON keys are "<field_number>:<type>", optionally followed by ":<name>" and,
for enums, ":<value_name>". Only the number and the type are used when
encoding, so anything after them is decorative and a file stays editable even
where the schema no longer matches.

  types: msg, str, bytes, f32, f64, i32, i64, varint, packed_varint,
         packed_f32
A repeated field becomes a list.

Usage:
  python acevo_pb.py decode <file> [-o out.json]
  python acevo_pb.py encode <file.json> -o <binary_file>
  python acevo_pb.py batch <folder> -o <json_folder> [--ext .carledsystem]
  python acevo_pb.py check <folder>        # verify round-trip everywhere
"""

import argparse
import json
import struct
import sys
from pathlib import Path

# ---------------------------------------------------------------- wire format


def read_varint(buf, pos):
    result = 0
    shift = 0
    while True:
        if pos >= len(buf):
            raise ValueError("truncated varint")
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7
        if shift > 63:
            raise ValueError("varint too long")


def write_varint(value):
    if value < 0:
        value += 1 << 64
    out = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def iter_fields(buf):
    """Walk a protobuf message, yielding (field_number, wire_type, raw_value)."""
    pos = 0
    while pos < len(buf):
        key, pos = read_varint(buf, pos)
        field, wire = key >> 3, key & 7
        if field == 0:
            raise ValueError("field number 0")
        if wire == 0:
            val, pos = read_varint(buf, pos)
        elif wire == 1:
            if pos + 8 > len(buf):
                raise ValueError("truncated fixed64")
            val, pos = buf[pos:pos + 8], pos + 8
        elif wire == 2:
            length, pos = read_varint(buf, pos)
            if pos + length > len(buf):
                raise ValueError("truncated length-delimited block")
            val, pos = buf[pos:pos + length], pos + length
        elif wire == 5:
            if pos + 4 > len(buf):
                raise ValueError("truncated fixed32")
            val, pos = buf[pos:pos + 4], pos + 4
        else:
            raise ValueError("unsupported wire type %d" % wire)
        yield field, wire, val


# ---------------------------------------------------------------- heuristics


def is_message(data):
    """Does the block parse as a complete, plausible protobuf message?"""
    if not data:
        return False
    try:
        n = 0
        for field, wire, _ in iter_fields(data):
            if field > 4096:
                return False
            n += 1
        return n > 0
    except ValueError:
        return False


def is_text(data):
    """Strict printable ASCII. A binary block can accidentally be valid UTF-8."""
    if not data:
        return False
    return all(0x20 <= b <= 0x7E for b in data)


def as_float(raw4):
    """fixed32 -> float when the magnitude is plausible, otherwise None."""
    f = struct.unpack("<f", raw4)[0]
    if f != f or f in (float("inf"), float("-inf")):
        return None
    a = abs(f)
    if f == 0.0 or 1e-6 <= a <= 1e12:
        return f
    return None


def is_packed_f32(data):
    """A run of packed floats (used by some curve files)."""
    if not data or len(data) % 4 or len(data) < 8:
        return False
    return all(as_float(data[i:i + 4]) is not None for i in range(0, len(data), 4))


def clean_float(f):
    """Round away float32 artefacts for readable JSON, without losing precision."""
    for digits in range(1, 10):
        r = round(f, digits)
        if struct.pack("<f", r) == struct.pack("<f", f):
            return r
    return f


# ---------------------------------------------------------------- decoding


def unpack_varints(data):
    """Decode a packed varint block, or None if it is not one, or if re-encoding
    would not reproduce the exact bytes (non-canonical varints)."""
    out, pos = [], 0
    try:
        while pos < len(data):
            v, pos = read_varint(data, pos)
            out.append(v)
    except ValueError:
        return None
    if b"".join(write_varint(v) for v in out) != data:
        return None
    return out


def decode_value(wire, raw, sub_desc=None, packed=False):
    """-> (type_str, json_value)"""
    if wire == 0:
        return "varint", raw
    if wire == 1:
        d = struct.unpack("<d", raw)[0]
        if d == d and abs(d) < 1e300:
            return "f64", d
        return "i64", struct.unpack("<Q", raw)[0]
    if wire == 5:
        f = as_float(raw)
        if f is not None:
            return "f32", clean_float(f)
        return "i32", struct.unpack("<I", raw)[0]
    # A repeated varint field declared by the schema: trust the schema, since a
    # packed block is otherwise indistinguishable from an opaque byte string.
    if packed and raw:
        vals = unpack_varints(raw)
        if vals is not None:
            return "packed_varint", vals
    # Try the sub-message first: real strings almost always fail to parse as
    # protobuf, whereas the converse is not true.
    if is_message(raw):
        return "msg", decode_message(raw, sub_desc)
    if is_text(raw):
        return "str", raw.decode("ascii")
    if is_packed_f32(raw):
        return "packed_f32", [clean_float(as_float(raw[i:i + 4]))
                              for i in range(0, len(raw), 4)]
    if not raw:
        return "msg", {}
    return "bytes", raw.hex()


# wire types compatible with each protobuf field type
_T = None


def _wire_ok(f, wire):
    global _T
    if _T is None:
        from google.protobuf.descriptor import FieldDescriptor as T
        _T = {
            T.TYPE_DOUBLE: {1}, T.TYPE_FLOAT: {5},
            T.TYPE_INT64: {0}, T.TYPE_UINT64: {0}, T.TYPE_INT32: {0},
            T.TYPE_UINT32: {0}, T.TYPE_SINT32: {0}, T.TYPE_SINT64: {0},
            T.TYPE_BOOL: {0}, T.TYPE_ENUM: {0},
            T.TYPE_FIXED64: {1}, T.TYPE_SFIXED64: {1},
            T.TYPE_FIXED32: {5}, T.TYPE_SFIXED32: {5},
            T.TYPE_STRING: {2}, T.TYPE_BYTES: {2}, T.TYPE_MESSAGE: {2},
            T.TYPE_GROUP: {3, 4},
        }
    allowed = set(_T.get(f.type, set()))
    if f.label == f.LABEL_REPEATED:
        allowed.add(2)          # packed encoding
    return wire in allowed


_RESERVED = None


def reserved_numbers(full_name):
    """Field numbers marked `reserved`, i.e. deleted from the .proto. Reserved
    ranges are not exposed on runtime descriptors, so read them from
    acevo.desc."""
    global _RESERVED
    if _RESERVED is None:
        _RESERVED = {}
        try:
            from google.protobuf import descriptor_pb2
            desc_path = Path(__file__).resolve().parent / "proto" / "acevo.desc"
            fds = descriptor_pb2.FileDescriptorSet()
            fds.ParseFromString(desc_path.read_bytes())

            def walk(msgs, prefix):
                for m in msgs:
                    full = (prefix + "." + m.name) if prefix else m.name
                    nums = set()
                    for r in m.reserved_range:
                        nums.update(range(r.start, r.end))
                    if nums:
                        _RESERVED[full] = nums
                    walk(m.nested_type, full)

            for f in fds.file:
                walk(f.message_type, f.package)
        except Exception:
            pass
    return _RESERVED.get(full_name, ())


def field_info(desc, number, wire):
    """(name, sub_message_descriptor, enum_descriptor, is_packed) for a field
    number.

    The name is only applied when the observed wire type matches the schema;
    otherwise this is an unknown field reusing the same number."""
    if desc is None:
        return None, None, None, False
    f = desc.fields_by_number.get(number)
    if f is None or not _wire_ok(f, wire):
        return None, None, None, False
    from google.protobuf.descriptor import FieldDescriptor as T
    packed = (f.label == f.LABEL_REPEATED and f.type in (
        T.TYPE_ENUM, T.TYPE_BOOL, T.TYPE_INT32, T.TYPE_INT64,
        T.TYPE_UINT32, T.TYPE_UINT64))
    return (f.name,
            f.message_type if f.message_type is not None else None,
            f.enum_type if f.enum_type is not None else None,
            packed)


def short_enum(enum_desc, name):
    """Strip the enum type prefix protoc prepends to each value:
    CarLightingCategory_Main_Lights -> Main_Lights."""
    pre = enum_desc.name + "_"
    return name[len(pre):] if name.startswith(pre) and len(name) > len(pre) else name


def enum_name(enum_desc, value):
    """Name of an enum value, or None when the value is not in the enum."""
    if enum_desc is None or not isinstance(value, int):
        return None
    v = enum_desc.values_by_number.get(value)
    return short_enum(enum_desc, v.name) if v is not None else None


def enum_choices(desc, number):
    """[(value, name)] of an enum field's possible choices, for the UI."""
    if desc is None:
        return None
    f = desc.fields_by_number.get(number)
    if f is None or f.enum_type is None:
        return None
    return [(v.number, short_enum(f.enum_type, v.name)) for v in f.enum_type.values]


def keys_contiguous(keys):
    """Does grouping repeated fields preserve the original order?

    Encoding rewrites every occurrence of a key at the position of its first
    appearance, so order survives if and only if each key's occurrences are
    contiguous."""
    closed = set()
    prev = None
    for k in keys:
        if k != prev:
            if k in closed:
                return False
            if prev is not None:
                closed.add(prev)
            prev = k
    return True


def decode_message(buf, desc=None):
    items = []
    for field, wire, raw in iter_fields(buf):
        name, sub, en, packed = field_info(desc, field, wire)
        if name is None and desc is not None and field in reserved_numbers(desc.full_name):
            # marker, not a name: it starts with "?"
            name = "?reserved"
        typ, val = decode_value(wire, raw, sub, packed)
        ename = enum_name(en, val)
        if ename is None and typ == "packed_varint" and en is not None:
            names = [enum_name(en, v) for v in val]
            if all(names):
                ename = ",".join(names)
        items.append((field, name, typ, val, ename))

    out = {}
    keys = []
    for field, name, typ, val, ename in items:
        key = "%d:%s" % (field, typ)
        if name:
            key += ":" + name
            # decorative, regenerated on every decode
            if ename:
                key += ":" + ename
        keys.append(key)
        if key in out:
            cur = out[key]
            if not (isinstance(cur, list) and
                    (typ not in ("packed_f32", "packed_varint")
                     or (cur and isinstance(cur[0], list)))):
                cur = out[key] = [cur]
            cur.append(val)
        else:
            out[key] = val

    if not keys_contiguous(keys):
        # interleaved fields: fall back to an exact sequential form
        return {"_seq": [{"f": f, "t": t, "v": v, **({"n": n} if n else {}),
                          **({"e": e} if e else {})}
                         for f, n, t, v, e in items]}
    return out


# ---------------------------------------------------------------- encoding


def encode_value(field, typ, val):
    if typ == "varint":
        return write_varint(field << 3) + write_varint(val)
    if typ == "f64":
        return write_varint((field << 3) | 1) + struct.pack("<d", val)
    if typ == "i64":
        return write_varint((field << 3) | 1) + struct.pack("<Q", val)
    if typ == "f32":
        return write_varint((field << 3) | 5) + struct.pack("<f", val)
    if typ == "i32":
        return write_varint((field << 3) | 5) + struct.pack("<I", val)
    if typ == "str":
        payload = val.encode("utf-8")
    elif typ == "bytes":
        payload = bytes.fromhex(val)
    elif typ == "packed_varint":
        payload = b"".join(write_varint(v) for v in val)
    elif typ == "packed_f32":
        payload = b"".join(struct.pack("<f", f) for f in val)
    elif typ == "msg":
        payload = encode_message(val)
    else:
        raise ValueError("unknown type: %s" % typ)
    return write_varint((field << 3) | 2) + write_varint(len(payload)) + payload


def encode_message(obj):
    out = bytearray()
    if "_seq" in obj:
        for it in obj["_seq"]:
            out += encode_value(it["f"], it["t"], it["v"])
        return bytes(out)
    for key, val in obj.items():
        # only the number and the type matter here
        parts = key.split(":")
        field, typ = int(parts[0]), parts[1]
        # a packed block is itself a list, so it is repeated only when nested
        repeated = isinstance(val, list) and (
            typ not in ("packed_f32", "packed_varint")
            or (val and isinstance(val[0], list)))
        for item in (val if repeated else [val]):
            out += encode_value(field, typ, item)
    return bytes(out)


# ---------------------------------------------------------------- commands


def resolve_desc(path):
    """Root message descriptor, when the extracted schemas are available.

    Used only to name fields; decoding itself stays fully generic."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import acevo_decode
    except Exception:
        return None, None
    ext = Path(path).suffix.lower()
    cands = acevo_decode.EXT_MAP.get(ext)
    if cands is None:
        return None, None
    if isinstance(cands, str):
        cands = [cands]
    raw = Path(path).read_bytes()
    for name in cands:
        try:
            cls = acevo_decode.get_class(name)
        except Exception:
            continue
        m = cls()
        try:
            if m.ParseFromString(raw) == len(raw):
                return name, cls.DESCRIPTOR
        except Exception:
            continue
    return None, None


def do_decode(path, out_path=None, quiet=False, names=True):
    raw = Path(path).read_bytes()
    msg_name, desc = resolve_desc(path) if names else (None, None)
    tree = decode_message(raw, desc)
    # proves the decoder invented nothing
    rt = encode_message(tree)
    ok = rt == raw
    doc = {"_file": Path(path).name, "_size": len(raw), "_roundtrip": ok,
           "data": tree}
    if msg_name:
        doc = {"_file": doc["_file"], "_message": msg_name, "_size": len(raw),
               "_roundtrip": ok, "data": tree}
    text = json.dumps(doc, indent=2, ensure_ascii=False)
    if out_path:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text(text, encoding="utf-8")
    elif not quiet:
        print(text)
    return ok


def do_encode(json_path, out_path):
    doc = json.loads(Path(json_path).read_text(encoding="utf-8"))
    tree = doc["data"] if "data" in doc else doc
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_bytes(encode_message(tree))


def do_batch(src, dst, exts):
    src, dst = Path(src), Path(dst)
    ok = fail = 0
    for f in sorted(src.rglob("*")):
        if not f.is_file():
            continue
        if exts and f.suffix.lower() not in exts:
            continue
        rel = f.relative_to(src)
        out = dst / rel.with_suffix(rel.suffix + ".json")
        try:
            if do_decode(f, out, quiet=True):
                ok += 1
            else:
                fail += 1
                print("ROUND-TRIP FAILED: %s" % rel)
        except Exception as e:
            fail += 1
            print("ERROR %s: %s" % (rel, e))
    print("\n%d files decoded, %d problems -> %s" % (ok, fail, dst))


# bulk assets: never protobuf, and big enough to dominate a whole-tree scan
SKIP_EXT = {".mesh", ".texture", ".texturemips", ".scene", ".anim", ".kn5",
            ".dds", ".png", ".jpg", ".wav", ".bank", ".ksanim",
            ".dynamictrackpresetcompressed"}
MAX_SIZE = 1_500_000


def do_check(src, exts):
    src = Path(src)
    stats = {}
    for f in sorted(src.rglob("*")):
        if not f.is_file():
            continue
        if exts and f.suffix.lower() not in exts:
            continue
        # ".texturemips" with no basename has an empty suffix: fall back on the
        # whole name so bulk assets are still skipped
        ext = (f.suffix or f.name).lower()
        if not exts and (ext in SKIP_EXT or f.stat().st_size > MAX_SIZE):
            continue
        s = stats.setdefault(ext, [0, 0, 0])
        s[0] += 1
        try:
            raw = f.read_bytes()
            if encode_message(decode_message(raw)) == raw:
                s[1] += 1
            else:
                s[2] += 1
        except Exception:
            s[2] += 1
    print("%-28s %7s %7s %7s" % ("extension", "total", "ok", "failed"))
    for ext in sorted(stats, key=lambda e: -stats[e][0]):
        t, o, k = stats[ext]
        print("%-28s %7d %7d %7d" % (ext, t, o, k))


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
    b.add_argument("--ext", action="append", default=[])
    c = sub.add_parser("check"); c.add_argument("input"); c.add_argument("--ext", action="append", default=[])

    a = p.parse_args()
    if a.cmd == "decode":
        ok = do_decode(a.input, a.output)
        if a.output:
            print("-> %s (round-trip: %s)" % (a.output, "OK" if ok else "KO"))
    elif a.cmd == "encode":
        do_encode(a.input, a.output)
        print("-> %s" % a.output)
    elif a.cmd == "batch":
        do_batch(a.input, a.output, {x.lower() for x in a.ext})
    elif a.cmd == "check":
        do_check(a.input, {x.lower() for x in a.ext})


if __name__ == "__main__":
    sys.exit(main())
