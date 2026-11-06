"""Screenshot and entrypoint integrity — honest visual proof."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .png_codec import decode_png

# Few distinct colours ≈ solid / blank frame; real scenes spread pixels.
_MIN_UNIQUE_FOR_NONBLANK = 12
# Near-black raster without HUD text often means WebGL did not paint.
_MAX_AVG_BRIGHTNESS_DARK_TRAP = 28.0


def png_color_stats(path: Path | str | None) -> dict[str, Any] | None:
    """Decode PNG → unique colour count + average brightness (0–255 per channel)."""
    if not path:
        return None
    p = Path(path)
    try:
        data = p.read_bytes()
    except OSError:
        return None
    if len(data) < 64:
        return None
    try:
        w, h, rgba = decode_png(data)
    except Exception:
        return None
    n = w * h
    if n <= 0:
        return None
    step = max(1, n // 4000)
    uniq: set[tuple[int, int, int, int]] = set()
    r_sum = g_sum = b_sum = 0
    sampled = 0
    for i in range(0, n, step):
        o = i * 4
        r, g, b, a = rgba[o : o + 4]
        uniq.add((r, g, b, a))
        r_sum += r
        g_sum += g
        b_sum += b
        sampled += 1
    if sampled <= 0:
        return None
    avg = (r_sum + g_sum + b_sum) / (3 * sampled)
    return {"width": w, "height": h, "unique_colors": len(uniq), "avg_brightness": avg, "bytes": len(data)}


def screenshot_is_suspect_blank(stats: dict[str, Any] | None) -> bool:
    """True when the frame is a near-solid dark raster (failed paint / blank)."""
    if not stats:
        return False
    u = int(stats.get("unique_colors") or 0)
    avg = float(stats.get("avg_brightness") or 0.0)
    if u <= 3 and avg < _MAX_AVG_BRIGHTNESS_DARK_TRAP:
        return True
    if u < _MIN_UNIQUE_FOR_NONBLANK and avg < 12.0:
        return True
    return False


_SCRIPT_SRC_RE = re.compile(
    r"""<script[^>]+src\s*=\s*(['"])(?P<path>[^'"]+)\1""",
    re.I,
)


def _norm_script_ref(href: str) -> str:
    s = (href or "").strip()
    if not s or s.startswith(("data:", "blob:", "javascript:", "http://", "https://", "//")):
        return ""
    s = s.split("?", 1)[0].split("#", 1)[0]
    return s.lstrip("/")


def html_script_refs(html: str) -> list[str]:
    return [_norm_script_ref(m.group("path")) for m in _SCRIPT_SRC_RE.finditer(html or "")]


_SEED_PLAY_MARKER = "generateTexture('walker'"


def entrypoint_integrity_failures(
    *,
    index_html: str,
    root: Path,
    writer_assist_seeded: bool = False,
) -> list[str]:
    """Detect harness false-passes: untouched kernel Phaser seed or orphan game entry."""
    root = Path(root)
    reasons: list[str] = []

    marker = root / ".daedalus-writer-assist.json"
    seeded = writer_assist_seeded or marker.is_file()

    if seeded:
        try:
            play_js = (root / "src" / "scenes" / "Play.js").read_text(encoding="utf-8", errors="replace")
        except OSError:
            play_js = ""
        if play_js and _SEED_PLAY_MARKER in play_js:
            reasons.append("scaffold_left_in_place")

    refs = html_script_refs(index_html)
    normalized = [r.replace("\\", "/").lower() for r in refs if r]
    if (root / "game.js").is_file() and not any(r.endswith("game.js") for r in normalized):
        reasons.append("orphan_game_js_not_loaded")

    return reasons
