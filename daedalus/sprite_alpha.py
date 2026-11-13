"""Generated images, made ready for the game: PNG at the requested size, sprites on a transparent background.

Owner, 2026-11-12. Image models answer with an opaque picture (the frog on a near-white field), often JPEG and
larger than the game asked for. ``finalize_image`` is the one place both image paths (the agent's generate_image
tool and the owner's asset buttons) turn that into what the game uses:

- a **sprite** (any name that is not a scene): the background is found from the border (it must be nearly one
  colour; otherwise the picture is a scene and is kept), every pixel connected to the border that is close to it
  becomes transparent (a flood fill, so a white eye inside the frog stays), the rest is cropped, fitted into the
  requested size without upscaling and centred on a transparent canvas of exactly that size;
- a **scene** (backgrounds, skies, tiles, textures, walls, maps... by name): never cut; covered to exactly the
  requested size (scaled and centre-cropped, so the aspect is never stretched).

Always PNG. Pillow does the work (optional for Daedalus); without it the bytes are kept as they are and the report
says so, so the caller keeps the provider's own format and extension.
"""
from __future__ import annotations

import io
import re
from typing import Any

TOLERANCE = 38            # colour distance (0-441) still counted as background
BORDER_UNIFORMITY = 0.85  # share of border pixels that must match the background colour
_SCENE_WORDS = {"bg", "background", "backdrop", "sky", "skies", "tile", "tileset", "texture", "floor", "wall",
                "ground", "terrain", "scene", "map", "parallax", "panorama", "cover", "splash", "title", "landscape",
                "level", "room", "environment", "env", "pattern"}
_TOKEN = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")


def _tokens(name: str) -> list[str]:
    """'backgrounds/forestBg-2.png' -> ['backgrounds', 'forest', 'bg', '2', 'png'] (camelCase and plurals too)."""
    return [t.lower() for t in _TOKEN.findall(name or "")]


def wants_alpha(filename: str) -> bool:
    """A sprite by its name: no token (folder or file, singular or plural, camelCase) names a scene."""
    for tok in _tokens(filename):
        if tok in _SCENE_WORDS or tok.rstrip("s") in _SCENE_WORDS or tok.endswith("bg"):
            return False
    return True


def _png(im) -> bytes:
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _cut_background(im) -> tuple[Any, dict[str, Any]]:
    """(RGBA image with the border-connected background transparent, report). ``im`` is RGBA."""
    from PIL import Image, ImageDraw

    w, h = im.size
    rgb = im.convert("RGB")
    px = rgb.load()
    border = ([px[x, y] for x in range(w) for y in (0, h - 1)] +
              [px[x, y] for y in range(h) for x in (0, w - 1)])
    buckets: dict[tuple[int, int, int], int] = {}
    for c in border:                                   # rounded, so JPEG noise does not split the colour
        key = (c[0] // 16, c[1] // 16, c[2] // 16)
        buckets[key] = buckets.get(key, 0) + 1
    top = max(buckets, key=buckets.get)
    members = [c for c in border if (c[0] // 16, c[1] // 16, c[2] // 16) == top]
    bg = tuple(sum(c[i] for c in members) // len(members) for i in range(3))
    tol2 = TOLERANCE * TOLERANCE
    close = [((c[0] - bg[0]) ** 2 + (c[1] - bg[1]) ** 2 + (c[2] - bg[2]) ** 2) <= tol2 for c in border]
    uniform = sum(close) / max(1, len(border))
    report: dict[str, Any] = {"alpha": False, "background": "#%02x%02x%02x" % bg, "border_match": round(uniform, 2)}
    if uniform < BORDER_UNIFORMITY:
        report["note"] = "the border is not one colour: a scene, kept as it is"
        return im, report
    # Pillow's flood fill: paint the background from every border pixel that matches it, in a colour the picture
    # does not use, then make exactly those pixels transparent. (finalize_image caps the size first: it is not fast.)
    used = {c for _n, c in (rgb.getcolors(w * h) or [])}
    marker = next(m for m in ((255, 0, 254), (1, 254, 3), (254, 1, 2), (3, 2, 253)) if m not in used)
    seeds = [(x, y) for x in range(w) for y in (0, h - 1)] + [(x, y) for y in range(h) for x in (0, w - 1)]
    for (x, y), ok in zip(seeds, close):
        if ok and px[x, y] != marker:
            ImageDraw.floodfill(rgb, (x, y), marker, thresh=TOLERANCE)
    r, g, b = rgb.split()
    is_bg = Image.merge("RGB", (r.point(lambda v: 255 if v == marker[0] else 0),
                                g.point(lambda v: 255 if v == marker[1] else 0),
                                b.point(lambda v: 255 if v == marker[2] else 0))).convert("L")
    is_bg = is_bg.point(lambda v: 255 if v == 255 else 0)       # all three channels matched
    out = im.copy()
    alpha = out.getchannel("A")
    alpha.paste(0, mask=is_bg)
    out.putalpha(alpha)
    box = out.getbbox()
    if box is None:
        return im, {**report, "note": "nothing left after removing the background: kept as it was"}
    out = out.crop(box)
    report.update(alpha=True, cropped_to=list(out.size))
    return out, report


def finalize_image(data: bytes, filename: str, size: tuple[int, int] | None) -> tuple[bytes, dict[str, Any]]:
    """(bytes, report). ``report['png']`` says whether the bytes are PNG now; without Pillow they are unchanged
    (``png`` False) and the caller keeps the provider's format and extension. Never raises."""
    try:
        from PIL import Image, ImageOps
    except Exception:
        return data, {"png": False, "alpha": False, "note": "Pillow is not installed: the image is kept as delivered"}
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
        im = im.convert("RGBA")
    except Exception as exc:
        return data, {"png": False, "alpha": False, "note": f"not an image Pillow can read: {exc}"}
    sprite = wants_alpha(filename)
    report: dict[str, Any] = {"kind": "sprite" if sprite else "scene"}
    if sprite:
        if size:      # the sprite ends at most the requested size: cut a copy at twice that, not the full render
            limit = max(1024, 2 * max(size))
            if max(im.size) > limit:
                im.thumbnail((limit, limit), Image.LANCZOS)
        im, cut = _cut_background(im)
        report.update(cut)
        if size:
            tw, th = size
            scale = min(tw / im.width, th / im.height, 1.0)           # never upscale (pixel art stays crisp)
            if scale < 1.0:
                im = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))), Image.LANCZOS)
            canvas = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
            canvas.paste(im, ((tw - im.width) // 2, (th - im.height) // 2), im)
            im = canvas
    else:
        report["alpha"] = False
        if size:
            im = ImageOps.fit(im, size, Image.LANCZOS)                 # cover, centre-cropped: no stretching
    report.update(png=True, size=list(im.size))
    return _png(im), report


def prepare_sprite(data: bytes, *, size: tuple[int, int] | None = None) -> tuple[bytes, dict[str, Any]]:
    """A sprite, whatever its name (kept for callers that know they hold one)."""
    return finalize_image(data, "sprite.png", size)
