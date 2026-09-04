"""Procedural kit SFX (WAV) and a tiny 5×7 ASCII bitmap font.

No paid APIs. Howler / Web Audio can play the WAVs. Not licensed music —
never emit a fake MP3.
"""
from __future__ import annotations

import json
import math
import struct
import wave
from io import BytesIO
from pathlib import Path
from typing import Any

from .png_codec import encode_rgba

SR = 22050
SFX_NAMES = ("blip", "hit", "boost", "pickup", "fail", "ui")
SFX_DIR = "assets/sfx"
FONT_PNG = "assets/font.png"
FONT_JSON = "assets/font.json"
GLYPH_W = 5
GLYPH_H = 7
FIRST_CH = 32
LAST_CH = 126
CELL_W = 6
CELL_H = 8
COLS = 16


def write_sfx(root: Path, names: tuple[str, ...] = SFX_NAMES) -> dict[str, Any]:
    base = Path(root) / SFX_DIR
    base.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for name in names:
        rel = f"{SFX_DIR}/{name}.wav"
        (Path(root) / rel).write_bytes(render_sfx(name))
        written.append(rel)
    return {"ok": True, "files": written, "rate": SR, "channels": 1}


def render_sfx(name: str) -> bytes:
    name = (name or "blip").strip().lower()
    if name == "hit":
        samples = _noise(int(0.12 * SR), amp=0.45)
        samples = _envelope(samples, attack=0.002, release=0.1)
    elif name == "boost":
        samples = _sweep(220, 880, 0.28, wave="saw", amp=0.28)
    elif name == "pickup":
        a = _tone(523.25, 0.07, wave="sine", amp=0.35)
        b = _tone(659.25, 0.07, wave="sine", amp=0.35)
        c = _tone(783.99, 0.12, wave="sine", amp=0.32)
        samples = a + b + c
        samples = _envelope(samples, attack=0.005, release=0.08)
    elif name == "fail":
        samples = _sweep(320, 90, 0.35, wave="square", amp=0.22)
    elif name == "ui":
        samples = _tone(880, 0.04, wave="sine", amp=0.28) + _tone(1320, 0.05, wave="sine", amp=0.22)
        samples = _envelope(samples, attack=0.002, release=0.04)
    else:
        samples = _sweep(660, 1320, 0.09, wave="sine", amp=0.32)
        samples = _envelope(samples, attack=0.004, release=0.05)
    return pcm_wav(samples)


def pcm_wav(samples: list[float], *, rate: int = SR) -> bytes:
    buf = BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        frames = b"".join(
            struct.pack("<h", max(-32767, min(32767, int(s * 32767))))
            for s in samples
        )
        wf.writeframes(frames)
    return buf.getvalue()


def wav_ok(data: bytes) -> bool:
    return len(data) > 44 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"


def write_font(root: Path) -> dict[str, Any]:
    png, meta = render_font()
    base = Path(root)
    png_path = base / FONT_PNG
    json_path = base / FONT_JSON
    png_path.parent.mkdir(parents=True, exist_ok=True)
    png_path.write_bytes(png)
    json_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return {"ok": True, "png": FONT_PNG, "json": FONT_JSON, "glyphs": len(meta["glyphs"])}


def render_font() -> tuple[bytes, dict[str, Any]]:
    n = LAST_CH - FIRST_CH + 1
    rows = (n + COLS - 1) // COLS
    width, height = COLS * CELL_W, rows * CELL_H
    canvas = bytearray(width * height * 4)
    glyphs: dict[str, dict[str, int]] = {}
    for i in range(n):
        ch = FIRST_CH + i
        bits = glyph_bits(ch)
        col, row = i % COLS, i // COLS
        ox, oy = col * CELL_W, row * CELL_H
        top = GLYPH_W * GLYPH_H - 1
        for gy in range(GLYPH_H):
            for gx in range(GLYPH_W):
                bit = (bits >> (top - (gy * GLYPH_W + gx))) & 1
                if not bit:
                    continue
                px = ox + gx
                py = oy + gy
                di = (py * width + px) * 4
                canvas[di : di + 4] = b"\xff\xff\xff\xff"
        glyphs[chr(ch)] = {"x": ox, "y": oy, "w": GLYPH_W, "h": GLYPH_H}
    png = encode_rgba(width, height, bytes(canvas))
    meta = {
        "image": "font.png",
        "glyph_width": GLYPH_W,
        "glyph_height": GLYPH_H,
        "first": FIRST_CH,
        "last": LAST_CH,
        "columns": COLS,
        "glyphs": glyphs,
    }
    return png, meta


def glyph_bits(code: int) -> int:
    """35-bit 5×7 glyph, row-major, MSB of each row is the left pixel."""
    table = _GLYPHS
    if code in table:
        return table[code]
    if 97 <= code <= 122:
        return table.get(code - 32, _BOX)
    return _BOX if 33 <= code <= 126 else 0


def _pack(rows: tuple[str, ...]) -> int:
    n = 0
    for row in rows:
        for ch in row:
            n = (n << 1) | (1 if ch == "#" else 0)
    return n


def _tone(freq: float, dur: float, *, wave: str = "sine", amp: float = 0.3) -> list[float]:
    n = max(1, int(dur * SR))
    out = []
    for i in range(n):
        t = i / SR
        phase = (freq * t) % 1.0
        if wave == "square":
            v = 1.0 if phase < 0.5 else -1.0
        elif wave == "saw":
            v = 2.0 * phase - 1.0
        else:
            v = math.sin(2 * math.pi * freq * t)
        out.append(amp * v)
    return out


def _sweep(f0: float, f1: float, dur: float, *, wave: str = "sine", amp: float = 0.3) -> list[float]:
    n = max(1, int(dur * SR))
    out = []
    phase = 0.0
    for i in range(n):
        freq = f0 + (f1 - f0) * (i / max(1, n - 1))
        phase += freq / SR
        frac = phase % 1.0
        if wave == "square":
            v = 1.0 if frac < 0.5 else -1.0
        elif wave == "saw":
            v = 2.0 * frac - 1.0
        else:
            v = math.sin(2 * math.pi * phase)
        out.append(amp * v)
    return out


def _noise(n: int, *, amp: float = 0.3) -> list[float]:
    # LCG so tests are deterministic
    x = 1234567
    out = []
    for _ in range(n):
        x = (1103515245 * x + 12345) & 0x7FFFFFFF
        out.append(amp * ((x / 0x7FFFFFFF) * 2.0 - 1.0))
    return out


def _envelope(samples: list[float], *, attack: float, release: float) -> list[float]:
    n = len(samples)
    a = max(1, int(attack * SR))
    r = max(1, int(release * SR))
    out = []
    for i, s in enumerate(samples):
        if i < a:
            g = i / a
        elif i > n - r:
            g = max(0.0, (n - i) / r)
        else:
            g = 1.0
        out.append(s * g)
    return out


_BOX = _pack((
    "#####",
    "#   #",
    "#   #",
    "#   #",
    "#   #",
    "#   #",
    "#####",
))

_GLYPHS: dict[int, int] = {
    32: 0,
    33: _pack(("  #  ", "  #  ", "  #  ", "  #  ", "  #  ", "     ", "  #  ")),
    48: _pack((" ### ", "#   #", "#  ##", "# # #", "##  #", "#   #", " ### ")),
    49: _pack(("  #  ", " ##  ", "  #  ", "  #  ", "  #  ", "  #  ", "#####")),
    50: _pack((" ### ", "#   #", "    #", "  ## ", " #   ", "#    ", "#####")),
    51: _pack((" ### ", "#   #", "    #", " ### ", "    #", "#   #", " ### ")),
    52: _pack(("#   #", "#   #", "#   #", "#####", "    #", "    #", "    #")),
    53: _pack(("#####", "#    ", "#    ", "#### ", "    #", "#   #", " ### ")),
    54: _pack((" ### ", "#    ", "#    ", "#### ", "#   #", "#   #", " ### ")),
    55: _pack(("#####", "    #", "   # ", "  #  ", "  #  ", "  #  ", "  #  ")),
    56: _pack((" ### ", "#   #", "#   #", " ### ", "#   #", "#   #", " ### ")),
    57: _pack((" ### ", "#   #", "#   #", " ####", "    #", "    #", " ### ")),
    65: _pack((" ### ", "#   #", "#   #", "#####", "#   #", "#   #", "#   #")),
    66: _pack(("#### ", "#   #", "#   #", "#### ", "#   #", "#   #", "#### ")),
    67: _pack((" ### ", "#   #", "#    ", "#    ", "#    ", "#   #", " ### ")),
    68: _pack(("#### ", "#   #", "#   #", "#   #", "#   #", "#   #", "#### ")),
    69: _pack(("#####", "#    ", "#    ", "#### ", "#    ", "#    ", "#####")),
    70: _pack(("#####", "#    ", "#    ", "#### ", "#    ", "#    ", "#    ")),
    71: _pack((" ### ", "#   #", "#    ", "# ###", "#   #", "#   #", " ### ")),
    72: _pack(("#   #", "#   #", "#   #", "#####", "#   #", "#   #", "#   #")),
    73: _pack((" ### ", "  #  ", "  #  ", "  #  ", "  #  ", "  #  ", " ### ")),
    74: _pack(("  ###", "   # ", "   # ", "   # ", "   # ", "#  # ", " ##  ")),
    75: _pack(("#   #", "#  # ", "# #  ", "##   ", "# #  ", "#  # ", "#   #")),
    76: _pack(("#    ", "#    ", "#    ", "#    ", "#    ", "#    ", "#####")),
    77: _pack(("#   #", "## ##", "# # #", "#   #", "#   #", "#   #", "#   #")),
    78: _pack(("#   #", "##  #", "# # #", "#  ##", "#   #", "#   #", "#   #")),
    79: _pack((" ### ", "#   #", "#   #", "#   #", "#   #", "#   #", " ### ")),
    80: _pack(("#### ", "#   #", "#   #", "#### ", "#    ", "#    ", "#    ")),
    81: _pack((" ### ", "#   #", "#   #", "#   #", "# # #", "#  # ", " ## #")),
    82: _pack(("#### ", "#   #", "#   #", "#### ", "# #  ", "#  # ", "#   #")),
    83: _pack((" ### ", "#   #", "#    ", " ### ", "    #", "#   #", " ### ")),
    84: _pack(("#####", "  #  ", "  #  ", "  #  ", "  #  ", "  #  ", "  #  ")),
    85: _pack(("#   #", "#   #", "#   #", "#   #", "#   #", "#   #", " ### ")),
    86: _pack(("#   #", "#   #", "#   #", "#   #", "#   #", " # # ", "  #  ")),
    87: _pack(("#   #", "#   #", "#   #", "# # #", "# # #", "## ##", "#   #")),
    88: _pack(("#   #", "#   #", " # # ", "  #  ", " # # ", "#   #", "#   #")),
    89: _pack(("#   #", "#   #", " # # ", "  #  ", "  #  ", "  #  ", "  #  ")),
    90: _pack(("#####", "    #", "   # ", "  #  ", " #   ", "#    ", "#####")),
}
