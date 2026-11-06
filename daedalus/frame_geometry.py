"""Lightweight frame / screenshot analysis for pipeline visual stages.

PNG proofs: prefer entropy + size heuristics (no heavy deps). When Pillow is
available, delegate hue analysis for better scene detection.
"""
from __future__ import annotations

from pathlib import Path


def frame_shows_scene(path: Path | str) -> bool:
    """True when a saved frame likely shows non-trivial image content."""
    p = Path(path)
    if not p.is_file():
        return False
    try:
        size = p.stat().st_size
    except OSError:
        return False
    if size < 900:
        return False
    try:
        from PIL import Image

        with Image.open(p) as im:
            im = im.convert("RGB")
            hist = im.histogram()
            # 256 bins per channel — sum non-zero bins as a cheap entropy proxy
            entropy_bins = sum(1 for x in hist if x > 0)
            return entropy_bins >= 48
    except Exception:
        return size >= 4000
