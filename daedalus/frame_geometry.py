"""Does a visual-proof PNG show a scene, or only HUD on a flat clear-color?

The MiniMax dungeon painted. The Claude SLERP slices painted HUD text and a
navy clear-color — Chromium treated that as a pass because ``dom_text_len``
was high. Geometry lives in the *center* of the frame.
"""
from __future__ import annotations

from pathlib import Path


def frame_shows_scene(path: str | Path, *, min_spread: int = 28, min_hues: int = 6) -> bool:
    """True when the center of the PNG has more than a flat wash of one colour."""
    try:
        from .png_codec import decode_png
        data = Path(path).read_bytes()
        width, height, rgba = decode_png(data)
    except Exception:
        return False
    if width < 16 or height < 16 or len(rgba) < 16:
        return False
    x0, y0 = width // 4, height // 4
    x1, y1 = width - x0, height - y0
    rmin = gmin = bmin = 255
    rmax = gmax = bmax = 0
    hues: set[tuple[int, int, int]] = set()
    for y in range(y0, y1, max(1, (y1 - y0) // 48)):
        row = y * width * 4
        for x in range(x0, x1, max(1, (x1 - x0) // 48)):
            i = row + x * 4
            r, g, b = rgba[i], rgba[i + 1], rgba[i + 2]
            if r < rmin:
                rmin = r
            if r > rmax:
                rmax = r
            if g < gmin:
                gmin = g
            if g > gmax:
                gmax = g
            if b < bmin:
                bmin = b
            if b > bmax:
                bmax = b
            hues.add((r >> 4, g >> 4, b >> 4))
            spread = (rmax - rmin) + (gmax - gmin) + (bmax - bmin)
            if spread >= min_spread or len(hues) >= min_hues:
                return True
    spread = (rmax - rmin) + (gmax - gmin) + (bmax - bmin)
    return spread >= min_spread or len(hues) >= min_hues
