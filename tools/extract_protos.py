#!/usr/bin/env python3
"""
extract_protos - rebuild Assetto Corsa EVO .proto schemas from the executable.

protoc embeds a serialised FileDescriptorProto per .proto file in the
binary's data section. This locates them (header `0a <len> "<name>.proto"`),
finds each one's end by incremental parsing, validates them with the protobuf
library, and renders readable .proto text.

Run this once, and again after a game update.

Usage:
  python extract_protos.py <exe_path> -o <output_folder>
"""

import argparse
import re
import sys
from pathlib import Path

from google.protobuf import descriptor_pb2

FD = descriptor_pb2.FieldDescriptorProto

TYPES = {
    FD.TYPE_DOUBLE: "double", FD.TYPE_FLOAT: "float", FD.TYPE_INT64: "int64",
    FD.TYPE_UINT64: "uint64", FD.TYPE_INT32: "int32", FD.TYPE_FIXED64: "fixed64",
    FD.TYPE_FIXED32: "fixed32", FD.TYPE_BOOL: "bool", FD.TYPE_STRING: "string",
    FD.TYPE_GROUP: "group", FD.TYPE_MESSAGE: "message", FD.TYPE_BYTES: "bytes",
    FD.TYPE_UINT32: "uint32", FD.TYPE_ENUM: "enum", FD.TYPE_SFIXED32: "sfixed32",
    FD.TYPE_SFIXED64: "sfixed64", FD.TYPE_SINT32: "sint32", FD.TYPE_SINT64: "sint64",
}

# ------------------------------------------------------------------ wire format


def read_varint(buf, pos):
    result = shift = 0
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


def field_boundaries(data, start, max_field):
    """Successive field boundaries from `start`, while the content stays a
    plausible protobuf message."""
    bounds = []
    pos = start
    while pos < len(data):
        try:
            key, p = read_varint(data, pos)
        except ValueError:
            break
        field, wire = key >> 3, key & 7
        if field == 0 or field > max_field:
            break
        if wire == 0:
            try:
                _, p = read_varint(data, p)
            except ValueError:
                break
        elif wire == 2:
            try:
                ln, p = read_varint(data, p)
            except ValueError:
                break
            if p + ln > len(data):
                break
            p += ln
        elif wire == 5:
            p += 4
        elif wire == 1:
            p += 8
        else:
            break
        if p > len(data):
            break
        bounds.append(p)
        pos = p
    return bounds


def extract_at(data, start, expected_name):
    """Return the longest valid FileDescriptorProto starting at `start`."""
    for end in reversed(field_boundaries(data, start, 14)):
        fd = descriptor_pb2.FileDescriptorProto()
        try:
            if fd.ParseFromString(data[start:end]) != end - start:
                continue
        except Exception:
            continue
        if fd.name == expected_name and (fd.message_type or fd.enum_type or fd.service):
            return fd, end
    return None, None


# ------------------------------------------------------------------ .proto rendering


def render_field(f, indent, syntax3, oneofs_used):
    label = ""
    if f.label == FD.LABEL_REPEATED:
        label = "repeated "
    elif f.label == FD.LABEL_REQUIRED:
        label = "required "
    elif not syntax3:
        label = "optional "
    elif f.proto3_optional:
        label = "optional "

    if f.type in (FD.TYPE_MESSAGE, FD.TYPE_ENUM, FD.TYPE_GROUP):
        typename = f.type_name.lstrip(".")
    else:
        typename = TYPES.get(f.type, "unknown")

    opts = []
    if f.default_value:
        opts.append("default = %s" % f.default_value)
    if f.options.packed:
        opts.append("packed = true")
    if f.options.deprecated:
        opts.append("deprecated = true")
    suffix = " [%s]" % ", ".join(opts) if opts else ""

    # map<>: protoc generates a nested MapEntry message
    if f.type == FD.TYPE_MESSAGE and f.label == FD.LABEL_REPEATED:
        entry = oneofs_used.get(f.type_name)
        if entry is not None and entry.options.map_entry:
            k = entry.field[0]
            v = entry.field[1]
            kt = TYPES.get(k.type, "unknown")
            vt = (v.type_name.lstrip(".")
                  if v.type in (FD.TYPE_MESSAGE, FD.TYPE_ENUM) else TYPES.get(v.type))
            return "%smap<%s, %s> %s = %d%s;" % (indent, kt, vt, f.name, f.number, suffix)

    return "%s%s%s %s = %d%s;" % (indent, label, typename, f.name, f.number, suffix)


def render_enum(e, indent, out):
    out.append("%senum %s {" % (indent, e.name))
    if e.options.allow_alias:
        out.append("%s  option allow_alias = true;" % indent)
    for v in e.value:
        out.append("%s  %s = %d;" % (indent, v.name, v.number))
    out.append("%s}" % indent)


def render_message(m, indent, out, syntax3, entries, prefix):
    if m.options.map_entry:
        return  # emitted as a map<> by the parent field
    out.append("%smessage %s {" % (indent, m.name))
    inner = indent + "  "
    full = prefix + "." + m.name

    for e in m.enum_type:
        render_enum(e, inner, out)
    for n in m.nested_type:
        render_message(n, inner, out, syntax3, entries, full)

    oneof_fields = {}
    for f in m.field:
        if f.HasField("oneof_index") and not f.proto3_optional:
            oneof_fields.setdefault(f.oneof_index, []).append(f)

    for f in m.field:
        if f.HasField("oneof_index") and not f.proto3_optional:
            continue
        out.append(render_field(f, inner, syntax3, entries))

    for idx, decl in enumerate(m.oneof_decl):
        if idx not in oneof_fields:
            continue
        out.append("%soneof %s {" % (inner, decl.name))
        for f in oneof_fields[idx]:
            out.append(render_field(f, inner + "  ", syntax3, entries))
        out.append("%s}" % inner)

    for r in m.reserved_range:
        out.append("%sreserved %d to %d;" % (inner, r.start, r.end - 1))
    for n in m.reserved_name:
        out.append('%sreserved "%s";' % (inner, n))
    out.append("%s}" % indent)


def collect_entries(msgs, prefix, into):
    for m in msgs:
        full = prefix + "." + m.name
        into[full] = m
        collect_entries(m.nested_type, full, into)


def render_file(fd):
    syntax3 = fd.syntax == "proto3"
    out = ['syntax = "%s";' % (fd.syntax or "proto2"), ""]
    if fd.package:
        out += ["package %s;" % fd.package, ""]
    for dep in fd.dependency:
        out.append('import "%s";' % dep)
    if fd.dependency:
        out.append("")
    if fd.options.java_package:
        out.append('option java_package = "%s";' % fd.options.java_package)

    entries = {}
    collect_entries(fd.message_type, "." + fd.package if fd.package else "", entries)

    for e in fd.enum_type:
        render_enum(e, "", out)
        out.append("")
    for m in fd.message_type:
        render_message(m, "", out, syntax3,
                       entries, "." + fd.package if fd.package else "")
        out.append("")
    for s in fd.service:
        out.append("service %s {" % s.name)
        for meth in s.method:
            out.append("  rpc %s (%s) returns (%s);" % (
                meth.name, meth.input_type.lstrip("."), meth.output_type.lstrip(".")))
        out.append("}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


# ------------------------------------------------------------------ main


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("exe")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("-d", "--descriptor-set",
                    help="also write a binary FileDescriptorSet (usable without "
                         "protoc, see acevo_decode.py)")
    a = ap.parse_args()

    data = Path(a.exe).read_bytes()
    outdir = Path(a.output)
    outdir.mkdir(parents=True, exist_ok=True)

    seen = {}
    for m in re.finditer(rb"[\w/\.\-]{1,120}\.proto", data):
        s, e = m.start(), m.end()
        ln = e - s
        if ln >= 128 or s < 2 or data[s - 2] != 0x0A or data[s - 1] != ln:
            continue
        name = m.group().decode()
        fd, end = extract_at(data, s - 2, name)
        if fd is None:
            print("  [failed] %s" % name)
            continue
        # keep the richer descriptor
        if name in seen and len(seen[name].SerializeToString()) >= (end - (s - 2)):
            continue
        seen[name] = fd

    total_msg = total_field = 0
    for name, fd in sorted(seen.items()):
        path = outdir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_file(fd), encoding="utf-8")

        def count(msgs):
            nonlocal total_msg, total_field
            for mm in msgs:
                total_msg += 1
                total_field += len(mm.field)
                count(mm.nested_type)
        count(fd.message_type)

    print("%d .proto files rebuilt -> %s" % (len(seen), outdir))
    print("%d messages, %d fields" % (total_msg, total_field))

    if a.descriptor_set:
        fds = descriptor_pb2.FileDescriptorSet()
        for _, fd in sorted(seen.items()):
            fds.file.add().CopyFrom(fd)
        Path(a.descriptor_set).parent.mkdir(parents=True, exist_ok=True)
        Path(a.descriptor_set).write_bytes(fds.SerializeToString())
        print("FileDescriptorSet -> %s" % a.descriptor_set)


if __name__ == "__main__":
    sys.exit(main())
