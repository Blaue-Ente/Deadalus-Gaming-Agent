"""A labelled picture with no dependencies (no Pillow, no engine): big black block letters on white.

Used by Settings -> "Test mentor vision": the label is random and never leaves the server, so a model that answers
with it really read the image. Only a small, unambiguous alphabet is used so a reading mistake is the model's."""
from __future__ import annotations

import secrets
import struct
import zlib

ALPHABET = "ACDEFHJKLMNPRTUVWXY347"
_GLYPHS = {
    "A": "01110 10001 10001 11111 10001 10001 10001", "C": "01110 10001 10000 10000 10000 10001 01110",
    "D": "11110 10001 10001 10001 10001 10001 11110", "E": "11111 10000 10000 11110 10000 10000 11111",
    "F": "11111 10000 10000 11110 10000 10000 10000", "H": "10001 10001 10001 11111 10001 10001 10001",
    "J": "00111 00010 00010 00010 00010 10010 01100", "K": "10001 10010 10100 11000 10100 10010 10001",
    "L": "10000 10000 10000 10000 10000 10000 11111", "M": "10001 11011 10101 10101 10001 10001 10001",
    "N": "10001 11001 10101 10011 10001 10001 10001", "P": "11110 10001 10001 11110 10000 10000 10000",
    "R": "11110 10001 10001 11110 10100 10010 10001", "T": "11111 00100 00100 00100 00100 00100 00100",
    "U": "10001 10001 10001 10001 10001 10001 01110", "V": "10001 10001 10001 10001 10001 01010 00100",
    "W": "10001 10001 10001 10101 10101 11011 10001", "X": "10001 10001 01010 00100 01010 10001 10001",
    "Y": "10001 10001 01010 00100 00100 00100 00100", "3": "11110 00001 00001 01110 00001 00001 11110",
    "4": "00010 00110 01010 10010 11111 00010 00010", "7": "11111 00001 00010 00100 01000 01000 01000",
}


def new_label(length: int = 6) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(max(4, min(int(length), 10))))


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def render_png(label: str, scale: int = 10) -> bytes:
    for ch in label:
        if ch not in _GLYPHS:
            raise ValueError(f"no glyph for {ch!r}")
    cell, gap, pad = scale, scale, scale * 3
    width = pad * 2 + len(label) * 5 * cell + (len(label) - 1) * gap
    height = pad * 2 + 7 * cell
    rows = [bytearray(b"\xff" * width) for _ in range(height)]          # 8-bit grey, white
    for i, ch in enumerate(label):
        x0 = pad + i * (5 * cell + gap)
        for gy, line in enumerate(_GLYPHS[ch].split()):
            for gx, bit in enumerate(line):
                if bit != "1":
                    continue
                for dy in range(cell):
                    y = pad + gy * cell + dy
                    for dx in range(cell):
                        rows[y][x0 + gx * cell + dx] = 0
    raw = b"".join(b"\x00" + bytes(r) for r in rows)
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(raw, 9)) + _chunk(b"IEND", b""))
