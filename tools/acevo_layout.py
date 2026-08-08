#!/usr/bin/env python3
"""
acevo_layout - read/write Assetto Corsa EVO .track_layout files.

Describes the two edges of a track layout, left and right, sampled as 3D
points. Structure:

  uint32   version                  1 in all 4 known files
  float[6] two identical Vector3    reference point (repeated)
  uint32   0                        role undetermined, always zero
  uint32   count                    number of records minus one
  record[] fixed-size records: 96 or 128 bytes
  ...                               i.e. 6 or 8 quadruples (x, y, z, s)

A record holds N quadruples for edge A then as many for edge B (N = 3 for
96 bytes, 4 for 128). Each quadruple is a point (x, y, z) plus a fourth value
that increases along the edge; its exact meaning is undetermined.

Usage:
  python acevo_layout.py info   <file|folder>
  python acevo_layout.py decode <file> [-o out.json] [--csv out.csv]
  python acevo_layout.py encode <file.json> -o <file>
  python acevo_layout.py check  <folder>
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

HEADER = 36


def parse(path):
    d = Path(path).read_bytes()
    if len(d) < HEADER:
        raise ValueError("file too short")
    version = struct.unpack_from("<I", d, 0)[0]
    ref = struct.unpack_from("<6f", d, 4)
    zero = struct.unpack_from("<I", d, 28)[0]
    count = struct.unpack_from("<I", d, 32)[0]
    rest = len(d) - HEADER
    stride = None
    for s in (96, 128):
        if rest and rest % s == 0:
            stride = s
            break
    if stride is None:
        raise ValueError("unrecognised record size (%d bytes left)" % rest)
    n = rest // stride
    per = stride // 16                      # quadruples per record
    half = per // 2
    quads = struct.unpack_from("<%df" % (rest // 4), d, HEADER)
    recs = [quads[i * per * 4:(i + 1) * per * 4] for i in range(n)]

    def side(rec, start):
        return [tuple(rec[(start + k) * 4:(start + k) * 4 + 4]) for k in range(half)]

    a, b = [], []
    for r in recs:
        a += side(r, 0)
        b += side(r, half)
    return {
        "_file": Path(path).name,
        "version": version,
        "reference": list(ref),
        "field_28": zero,
        "count": count,
        "stride": stride,
        "records": n,
        "quads_per_record": per,
        "edge_a": [list(p) for p in a],
        "edge_b": [list(p) for p in b],
    }


def build(doc):
    out = bytearray(struct.pack("<I", doc["version"]))
    out += struct.pack("<6f", *doc["reference"])
    out += struct.pack("<I", doc["field_28"])
    out += struct.pack("<I", doc["count"])
    per, half = doc["quads_per_record"], doc["quads_per_record"] // 2
    A, B = doc["edge_a"], doc["edge_b"]
    for i in range(doc["records"]):
        for p in A[i * half:(i + 1) * half]:
            out += struct.pack("<4f", *p)
        for p in B[i * half:(i + 1) * half]:
            out += struct.pack("<4f", *p)
    return bytes(out)


def length_of(points):
    return sum(math.dist(points[i][:3], points[i + 1][:3])
               for i in range(len(points) - 1))


def width_of(a, b):
    m = min(len(a), len(b))
    if not m:
        return 0.0
    w = sorted(math.dist(a[i][:3], b[i][:3]) for i in range(m))
    return w[m // 2]


def do_info(target):
    paths = ([target] if os.path.isfile(target)
             else sorted(glob.glob(os.path.join(target, "**", "*.track_layout"),
                                   recursive=True)))
    print("%-34s %3s %8s %8s %11s %11s %9s"
          % ("file", "ver", "records", "points", "length A", "length B", "width"))
    for p in paths:
        try:
            d = parse(p)
        except Exception as e:
            print("%-34s ERROR: %s" % (Path(p).name[:34], e))
            continue
        print("%-34s %3d %8d %8d %9.1f m %9.1f m %7.2f m"
              % (Path(p).name[:34], d["version"], d["records"], len(d["edge_a"]),
                 length_of(d["edge_a"]), length_of(d["edge_b"]),
                 width_of(d["edge_a"], d["edge_b"])))


def do_decode(path, out, csv_out):
    d = parse(path)
    d["_roundtrip"] = build(d) == Path(path).read_bytes()
    if csv_out:
        Path(csv_out).parent.mkdir(parents=True, exist_ok=True)
        with open(csv_out, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["edge", "index", "x", "y", "z", "col_3"])
            for name, pts in (("A", d["edge_a"]), ("B", d["edge_b"])):
                for i, p in enumerate(pts):
                    w.writerow([name, i] + list(p))
        print("-> %s (%d points)" % (csv_out, len(d["edge_a"]) + len(d["edge_b"])))
    if out or not csv_out:
        text = json.dumps(d, indent=2, ensure_ascii=False)
        if out:
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            Path(out).write_text(text, encoding="utf-8")
            print("-> %s  round-trip: %s" % (out, "OK" if d["_roundtrip"] else "KO"))
        else:
            head = dict(d)
            head["edge_a"] = head["edge_a"][:3]
            head["edge_b"] = head["edge_b"][:3]
            head["_note"] = "first 3 points of each edge; use -o or --csv for everything"
            print(json.dumps(head, indent=2, ensure_ascii=False))


def do_encode(json_path, out):
    doc = json.loads(Path(json_path).read_text(encoding="utf-8"))
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_bytes(build(doc))
    print("-> %s" % out)


def do_check(target):
    paths = sorted(glob.glob(os.path.join(target, "**", "*.track_layout"), recursive=True))
    ok = bad = 0
    for p in paths:
        try:
            if build(parse(p)) == Path(p).read_bytes():
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
