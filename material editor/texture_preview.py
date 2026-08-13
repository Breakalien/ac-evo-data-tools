"""
Texture preview: turns a .texture/.dds/plain-image file into a PNG
thumbnail for display in the material editor's linked-texture panels.

.texture/.dds decoding uses Pillow's own DDS reader (BC1-BC7 + the DX10
header, since Pillow 9.2) rather than a third-party pure-Python BC decoder
(texture2ddecoder) or an external Windows-only tool (texconv.exe): both were
tried first, but Pillow's built-in decoder matched texconv's output
pixel-for-pixel on real AC EVO content while adding no dependency (Pillow is
already required here for plain images) and no platform restriction.
"""
from __future__ import annotations

import io
import os

from PIL import Image

from ace_texture import texture_to_dds

PREVIEW_TARGET = 1024


def decode_png(path: str, target: int | None = PREVIEW_TARGET) -> bytes | None:
    """Returns PNG bytes for `path`, thumbnailed so its largest side is at
    most `target` (pass target=None for the original full resolution), or
    None if it can't be decoded (missing sibling .texturemips, or a format
    Pillow's DDS reader doesn't support - e.g. BC6H HDR)."""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in ('.png', '.bmp', '.tga', '.jpg', '.jpeg'):
            with open(path, 'rb') as fh:
                data = fh.read()
        elif ext == '.texture':
            mips_path = os.path.splitext(path)[0] + '.texturemips'
            if not os.path.isfile(mips_path):
                return None
            data = texture_to_dds(open(path, 'rb').read(), open(mips_path, 'rb').read())
        elif ext == '.dds':
            with open(path, 'rb') as fh:
                data = fh.read()
        else:
            return None

        with Image.open(io.BytesIO(data)) as im:
            im = im.convert('RGBA')
            if target is not None:
                im.thumbnail((target, target))
            buf = io.BytesIO()
            im.save(buf, 'PNG')
            return buf.getvalue()
    except Exception:
        return None
