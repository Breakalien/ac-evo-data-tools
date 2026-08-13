#!/usr/bin/env python3
"""
acevo_spline - read/write Assetto Corsa EVO .extended_splinedata.bin files.

In-house binary format holding a vehicle's pre-computed racing line on a
track. Structure:

  uint32  version                       (1, 2 or 3)
  string  .aicardata path               (1 length byte + bytes)
  float   track length (m)              -- versions 2 and 3 only
  string  .ideal_line path              (1 length byte + bytes)
  uint64  number of points
  point[] fixed-size records, per version:
            v1 = 128 bytes (32 columns), v2 = 176 (44), v3 = 180 (45)
  float   tail value
  uint32  42                            (constant marker)

Every column is a 4-byte word. Identified columns are named (see COLS); the
rest keep a generic col_NN name.

Usage:
  python acevo_spline.py info   <file|folder>
  python acevo_spline.py decode <file> [-o out.json] [--csv out.csv]
  python acevo_spline.py encode <file.json> -o <file.bin>
  python acevo_spline.py check  <folder>
"""

import argparse
import csv
import glob
import json
import math
import os
import struct
import sys
from pathlib import Path

STRIDE = {1: 128, 2: 176, 3: 180}

# Identified columns, per version. Everything else stays col_NN.
COLS = {
    # v1 has no position/direction block; its columns 1..17 map to v3 7..23
    1: {1: "progress", 3: "curvature_radius", 9: "speed_ms"},
    3: {1: "pos_x", 2: "pos_y", 3: "pos_z",
        4: "dir_x", 5: "dir_y", 6: "dir_z",
        7: "progress", 9: "curvature_radius", 15: "speed_ms"},
}
COLS[2] = COLS[3]

# Columns holding integers rather than floats. Exported as integers, written
# back as uint32.
INT_COLS = {1: {8, 22, 23}, 3: {0, 14, 29, 30}}
INT_COLS[2] = INT_COLS[3]


def col_names(ver):
    n = STRIDE[ver] // 4
    named = COLS.get(ver, {})
    return [named.get(i, "col_%02d" % i) for i in range(n)]


# ---------------------------------------------------------------- reading


def parse_header(d):
    ver = struct.unpack_from("<I", d, 0)[0]
    if ver not in STRIDE:
        raise ValueError("unknown version: %d" % ver)
    o = 4
    n = d[o]; o += 1
    aicardata = d[o:o + n].decode("latin1"); o += n
    length = None
    if ver >= 2:
        length = struct.unpack_from("<f", d, o)[0]; o += 4
    n = d[o]; o += 1
    ideal_line = d[o:o + n].decode("latin1"); o += n
    count = struct.unpack_from("<Q", d, o)[0]; o += 8
    return ver, aicardata, length, ideal_line, count, o


def word(d, off, as_int=False):
    """Value of a 4-byte word: integer for declared integer columns, otherwise
    float, or the uint32 as a hex string when the pattern is not a finite
    float."""
    if as_int:
        return struct.unpack_from("<I", d, off)[0]
    f = struct.unpack_from("<f", d, off)[0]
    if f == f and abs(f) != float("inf"):
        return f
    return "0x%08X" % struct.unpack_from("<I", d, off)[0]


def pack_word(v):
    if isinstance(v, str):
        return struct.pack("<I", int(v, 16))
    if isinstance(v, int) and not isinstance(v, bool):
        return struct.pack("<I", v)
    return struct.pack("<f", v)


def decode(path):
    d = Path(path).read_bytes()
    ver, aicar, length, ideal, count, o = parse_header(d)
    st = STRIDE[ver]
    end = o + count * st
    if len(d) < end + 8:
        raise ValueError("truncated file")
    names = col_names(ver)
    ints = INT_COLS.get(ver, set())
    points = [[word(d, o + i * st + c * 4, c in ints) for c in range(st // 4)]
              for i in range(count)]
    tail_f = word(d, end)
    tail_marker = struct.unpack_from("<I", d, end + 4)[0]
    extra = d[end + 8:]
    return {
        "_file": Path(path).name,
        "version": ver,
        "aicardata": aicar,
        "track_length_m": length,
        "ideal_line": ideal,
        "count": count,
        "columns": names,
        "tail_value": tail_f,
        "tail_marker": tail_marker,
        "trailing_extra": extra.hex(),
        "points": points,
    }


def encode(doc):
    ver = doc["version"]
    out = bytearray(struct.pack("<I", ver))
    a = doc["aicardata"].encode("latin1")
    out += bytes([len(a)]) + a
    if ver >= 2:
        out += struct.pack("<f", doc["track_length_m"])
    b = doc["ideal_line"].encode("latin1")
    out += bytes([len(b)]) + b
    out += struct.pack("<Q", doc["count"])
    for row in doc["points"]:
        for v in row:
            out += pack_word(v)
    out += pack_word(doc["tail_value"])
    out += struct.pack("<I", doc["tail_marker"])
    out += bytes.fromhex(doc.get("trailing_extra", ""))
    return bytes(out)


# ---------------------------------------------------------------- commands


def do_info(target):
    paths = ([target] if os.path.isfile(target)
             else sorted(glob.glob(os.path.join(target, "**", "*.bin"), recursive=True)))
    print("%-40s %3s %8s %11s %11s" % ("track", "ver", "points", "length", "sum dist"))
    for p in paths:
        try:
            doc = decode(p)
        except Exception as e:
            print("%-40s  ERROR: %s" % (Path(p).name[:40], e))
            continue
        dist = ""
        if doc["version"] >= 3:
            pts = [(r[1], r[2], r[3]) for r in doc["points"]]
            tot = sum(math.dist(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts)))
            dist = "%.1f m" % tot
        print("%-40s %3d %8d %11s %11s" % (
            doc["ideal_line"].split("\\")[-1][:40], doc["version"], doc["count"],
            "%.1f m" % doc["track_length_m"] if doc["track_length_m"] else "-", dist))


def do_decode(path, out, csv_out):
    doc = decode(path)
    ok = encode(doc) == Path(path).read_bytes()
    doc["_roundtrip"] = ok
    if csv_out:
        Path(csv_out).parent.mkdir(parents=True, exist_ok=True)
        with open(csv_out, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(doc["columns"])
            w.writerows(doc["points"])
        print("-> %s (%d points)" % (csv_out, doc["count"]))
    if out or not csv_out:
        text = json.dumps(doc, indent=2, ensure_ascii=False)
        if out:
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            Path(out).write_text(text, encoding="utf-8")
            print("-> %s  round-trip: %s" % (out, "OK" if ok else "KO"))
        else:
            head = dict(doc); head["points"] = head["points"][:3]
            head["_note"] = "first 3 points; use -o or --csv for the whole file"
            print(json.dumps(head, indent=2, ensure_ascii=False))


def do_encode(json_path, out):
    doc = json.loads(Path(json_path).read_text(encoding="utf-8"))
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_bytes(encode(doc))
    print("-> %s" % out)


def do_check(target):
    paths = sorted(glob.glob(os.path.join(target, "**", "*.bin"), recursive=True))
    ok = bad = 0
    for p in paths:
        try:
            raw = Path(p).read_bytes()
            if encode(decode(p)) == raw:
                ok += 1
            else:
                bad += 1
                print("ROUND-TRIP FAILED: %s" % p)
        except Exception as e:
            bad += 1
            print("ERROR %s: %s" % (p, e))
    print("\n%d files exact, %d problems" % (ok, bad))


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("info"); i.add_argument("input")
    d = sub.add_parser("decode"); d.add_argument("input")
    d.add_argument("-o", "--output"); d.add_argument("--csv")
    e = sub.add_parser("encode"); e.add_argument("input"); e.add_argument("-o", "--output", required=True)
    c = sub.add_parser("check"); c.add_argument("input")
    a = p.parse_args()
    if a.cmd == "info":
        do_info(a.input)
    elif a.cmd == "decode":
        do_decode(a.input, a.output, a.csv)
    elif a.cmd == "encode":
        do_encode(a.input, a.output)
    elif a.cmd == "check":
        do_check(a.input)


if __name__ == "__main__":
    sys.exit(main())
