"""Capture a window and judge it honestly (sha256, size, blank or not, how much changed)."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ...png_codec import decode_png
from ...visual_integrity import png_color_stats, screenshot_is_suspect_blank
from .errors import PlayError


def sha256_file(path: Path | str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def capture_window(display: str, window_id: str, out: Path) -> dict[str, Any]:
    """Window pixels as the X server holds them (what a player sees), not what the engine believes it drew."""
    tool = shutil.which("import")
    if not tool:
        raise PlayError("tool_missing", "ImageMagick `import` is needed to capture a window", tool="import")
    out.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "DISPLAY": display}
    try:
        res = subprocess.run([tool, "-window", str(window_id), f"PNG24:{out}"], env=env, capture_output=True, timeout=20)
    except subprocess.TimeoutExpired:
        raise PlayError("window_missing", "window capture timed out", window=window_id) from None
    if res.returncode != 0 or not out.is_file() or out.stat().st_size < 64:
        raise PlayError("window_missing", (res.stderr or b"").decode("utf-8", "replace")[-200:] or "capture failed",
                        window=window_id)
    return describe_frame(out)


def describe_frame(path: Path) -> dict[str, Any]:
    stats = png_color_stats(path)
    if not stats:
        # Not "blank": we could not read it at all. Saying blank would hide a tooling problem as a game problem.
        raise PlayError("frame_unreadable", f"captured file is not a readable 8-bit PNG: {path.name}")
    unique = int(stats.get("unique_colors") or 0)
    blank = screenshot_is_suspect_blank(stats) or unique <= 2
    return {
        "path": str(path), "sha256": sha256_file(path), "bytes": path.stat().st_size,
        "width": stats.get("width"), "height": stats.get("height"),
        "unique_colors": unique, "avg_brightness": stats.get("avg_brightness"),
        "blank": bool(blank),
    }


def changed_fraction(a: Path | str, b: Path | str, *, tolerance: int = 12) -> float:
    """Share of pixels whose colour differs by more than ``tolerance`` between two frames (0.0 … 1.0)."""
    wa, ha, pa = decode_png(Path(a).read_bytes())
    wb, hb, pb = decode_png(Path(b).read_bytes())
    if (wa, ha) != (wb, hb):
        return 1.0
    n = wa * ha
    if n <= 0:
        return 0.0
    step = max(1, n // 20000)
    diff = total = 0
    for i in range(0, n, step):
        o = i * 4
        total += 1
        if (abs(pa[o] - pb[o]) > tolerance or abs(pa[o + 1] - pb[o + 1]) > tolerance
                or abs(pa[o + 2] - pb[o + 2]) > tolerance):
            diff += 1
    return diff / total if total else 0.0
