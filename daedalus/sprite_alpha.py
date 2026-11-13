"""A generated sprite on a transparent background, sized for the game, free and local (owner, 2026-11-12).

Image models answer with an opaque picture: the frog on a white (or near-white) field, often JPEG and larger than the
game asked for. A sprite must sit on the game's own background. ``prepare_sprite``:

1. finds the background colour from the image's border (it must be nearly uniform; otherwise the picture is a scene,
   not a sprite, and is kept as it is),
2. clears every pixel connected to the border that is close to that colour (a flood fill, so a white eye inside the
   frog stays white), with a soft edge,
3. crops to what is left and fits it into the requested size, saved as PNG.

Pillow does the work when it is installed (it is optional for Daedalus); without it the image is returned unchanged
with a note that says so. Backgrounds, skies, tiles and textures are never cut out.
"""
from __future__ import annotations

import io
import re
from typing import Any

_SCENE_NAME = re.compile(r"(?:^|[/_\-.])(?:bg|background|backdrop|sky|tile|tiles|tileset|texture|floor|wall|scene|"
                         r"map|parallax|panorama|cover|splash|title)(?:[/_\-.]|\d|$)", re.I)
TOLERANCE = 38            # colour distance (0-441) still counted as background
BORDER_UNIFORMITY = 0.85  # share of border pixels that must match the background colour


def wants_alpha(filename: str) -> bool:
    """A sprite by its name: not a background, sky, tile or texture."""
    return not _SCENE_NAME.search(filename or "")


def _dist(a: tuple[int, ...], b: tuple[int, ...]) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5


def prepare_sprite(data: bytes, *, size: tuple[int, int] | None = None) -> tuple[bytes, dict[str, Any]]:
    """(PNG bytes, report). Never raises: anything it cannot do leaves the image as it was and says why."""
    try:
        from PIL import Image
    except Exception:
        return data, {"alpha": False, "note": "Pillow is not installed: the sprite keeps its background"}
    try:
        im = Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception as exc:
        return data, {"alpha": False, "note": f"not an image Pillow can read: {exc}"}
    w, h = im.size
    px = im.load()
    border = [px[x, y] for x in range(w) for y in (0, h - 1)] + [px[x, y] for y in range(h) for x in (0, w - 1)]
    # The background is the most common border colour, rounded so JPEG noise does not split it.
    buckets: dict[tuple[int, int, int], int] = {}
    for c in border:
        key = (c[0] // 16, c[1] // 16, c[2] // 16)
        buckets[key] = buckets.get(key, 0) + 1
    top = max(buckets, key=buckets.get)
    members = [c for c in border if (c[0] // 16, c[1] // 16, c[2] // 16) == top]
    bg = tuple(sum(c[i] for c in members) // len(members) for i in range(3))
    uniform = sum(1 for c in border if _dist(c, bg) <= TOLERANCE) / max(1, len(border))
    report: dict[str, Any] = {"alpha": False, "background": "#%02x%02x%02x" % bg, "border_match": round(uniform, 2)}
    if uniform < BORDER_UNIFORMITY:
        report["note"] = "the border is not one colour: a scene, kept as it is"
        out = im
    else:
        seen = bytearray(w * h)
        stack = [(x, y) for x in range(w) for y in (0, h - 1)] + [(x, y) for y in range(h) for x in (0, w - 1)]
        while stack:
            x, y = stack.pop()
            i = y * w + x
            if seen[i]:
                continue
            seen[i] = 1
            c = px[x, y]
            d = _dist(c, bg)
            if d > TOLERANCE * 1.6:
                continue
            # Soft edge: full transparency deep in the background, partial near the sprite.
            alpha = 0 if d <= TOLERANCE else int(255 * (d - TOLERANCE) / (TOLERANCE * 0.6))
            px[x, y] = (c[0], c[1], c[2], min(c[3], alpha))
            if d <= TOLERANCE:
                if x > 0:
                    stack.append((x - 1, y))
                if x < w - 1:
                    stack.append((x + 1, y))
                if y > 0:
                    stack.append((x, y - 1))
                if y < h - 1:
                    stack.append((x, y + 1))
        box = im.getbbox()
        if box is None:
            return data, {**report, "note": "nothing left after removing the background: kept as it was"}
        out = im.crop(box)
        report.update(alpha=True, cropped_to=list(out.size))
    if size:
        tw, th = size
        scale = min(tw / out.width, th / out.height, 1.0)
        if scale < 1.0:
            out = out.resize((max(1, int(out.width * scale)), max(1, int(out.height * scale))), Image.LANCZOS)
        report["size"] = list(out.size)
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), report
