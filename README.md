# AC EVO Data Tools

Read and edit Assetto Corsa EVO content files.

The game's data files are **not encrypted** — they are serialised Protocol
Buffers messages. Better still, the complete `.proto` schemas are embedded in
the game executable, so fields can be shown by their real names rather than by
number.

```
main_lights[0].shared_lights[1]
  category         = Flashing
  light_function   = light_highbeam
  intensity_when_on = 20.0
  blinker_name     = "ON_200ms_200ms"
```

Everything round-trips **byte for byte**: a file written back without changes is
identical to the original.

## Requirements

Python 3.9+ and the protobuf runtime:

```bash
pip install protobuf
```

## First run

The schemas are **not shipped** with these tools: they are extracted from the
game executable, so you generate them once from your own copy of the game.

```bash
python tools/extract_protos.py "<path to>/AssettoCorsaEVO.exe" -o proto -d proto/acevo.desc
```

This writes `proto/acevo.desc` (used by the tools) and 90 readable `.proto`
files (useful as a reference). Repeat it after a game update.

Without it everything still works and stays byte-exact, but fields show as
numbers instead of names.

## Quick start

```bash
python tools/acevo_ui.py
```

Open the address printed in the console — it carries a session token, so it
cannot be guessed. Or double-click `tools/acevo_ui.bat` on Windows.

Point it at your extracted game content with `--dir`, or just paste any path
into the address bar once it is open.

## The interface

- **Explorer** — browse any folder, bookmarks and recent list, search by
  filename scoped to the current folder.
- **Structured view** — collapsible tree, every value editable. Enum fields get
  a dropdown of the schema's real values.
- **Raw JSON tab** — for bulk edits, with *Apply to form*.
- **Find in file** (`Ctrl+F`) — searches field names *and* values, hides
  non-matching rows and expands the path to each hit. Works in both views.
- **Save** (`Ctrl+S`) — writes a `.bak` alongside the file on first save.

Fields shown in orange as `reserved` are field numbers the game's `.proto`
reserves, meaning they were deleted from the schema. Older content files still
carry values for them; the game ignores them. They stay editable and are
rewritten exactly.

## Command line

| Tool | Purpose |
|---|---|
| `acevo_pb.py` | Generic decode/encode. **Byte-exact, use this to mod.** |
| `acevo_decode.py` | Fully named JSON, easier to read |
| `acevo_spline.py` | `.extended_splinedata.bin` (AI racing lines) ⇄ JSON/CSV |
| `acevo_layout.py` | `.track_layout` (track edges) ⇄ JSON/CSV |
| `extract_protos.py` | Rebuild the schemas from the game executable |

```bash
python tools/acevo_pb.py decode <file> -o out.json
python tools/acevo_pb.py encode out.json -o <file>
python tools/acevo_pb.py check <folder>          # verify round-trip

python tools/acevo_spline.py info <folder>
python tools/acevo_spline.py decode <file> --csv out.csv

python tools/acevo_layout.py info <folder>
```

`acevo_pb.py` keys look like `"3:f32:intensity_when_on"`. Only the number and
the type are used when encoding, so the name is decorative — a file stays
editable even where the schema no longer matches its content.

> **Which decoder?** `acevo_decode.py` is nicer to read but drops fields the
> current schema does not declare, so re-encoding from it can lose data. For
> editing, use `acevo_pb.py`.

## Note on the schemas

`protoc` crashes on the full set of extracted schemas, so the tools build their
descriptor pool directly from `acevo.desc` rather than going through `protoc`.

## Coverage

| Content | Files | Round-trip |
|---|---|---|
| `content/cars` | 1 879 | 1 879 |
| `content/tracks` | 5 204 | 5 204 |

Roughly 75 file types are recognised, including `.car`, `.carengine`,
`.suspension`, `.carledsystem`, `.carlightingsystem`, `.curve`, `.aisplinedata`,
`.scene` and `.material`.

Not protobuf, handled separately: `.extended_splinedata.bin` and
`.track_layout` (in-house binary formats, see the tools above), `.data`
(already JSON), `.fbx`, `.json`, `.csv`.

Files above ~1.5 MB are slow to open in the generic decoder, which builds a
Python dictionary tree many times the file size. The largest `.scene` files
reach 85 MB.

## Security

The UI exposes the file system for reading *and* writing, and any web page open
in your browser can send requests to `127.0.0.1`. Four safeguards:

| Control | Prevents |
|---|---|
| Listens on `127.0.0.1` only | access from the network |
| `Host` header check | DNS rebinding |
| Foreign `Origin` rejected | requests from another site |
| Random session token (`SameSite=Strict` cookie) | use of the API behind your back |

Do not share the printed URL: it contains the token.

Address reuse is disabled, so a second instance on the same port fails with a
clear message instead of silently shadowing the first one.

## License

MIT. See `LICENSE`.

This is an unofficial, fan-made tool. It is not affiliated with or endorsed by
Kunos Simulazioni. It ships no game data: the schemas are generated on your
machine from your own copy of the game.

## Backups

The first save of any file copies it to `<name>.<ext>.bak`. Later saves do not
overwrite that backup. Back up your content folder anyway before editing.
