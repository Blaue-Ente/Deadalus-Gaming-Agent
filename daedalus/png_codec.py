"""Minimal RGBA PNG encode/decode (stdlib zlib). No Pillow.

Encoding always writes 8-bit RGBA, filter 0. Decoding accepts 8-bit RGB/RGBA
with scanline filters 0–4. Interlace and indexed colour fail closed.
"""
from __future__ import annotations

import struct
import zlib

SIGNATURE = b"\x89PNG\r\n\x1a\n"


def encode_rgba(width: int, height: int, pixels: bytes) -> bytes:
    """``pixels`` is row-major RGBA8, length ``width * height * 4``."""
    if width < 1 or height < 1:
        raise ValueError("png size must be positive")
    expect = width * height * 4
    if len(pixels) != expect:
        raise ValueError(f"pixel buffer {len(pixels)} != {expect}")
    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)
        raw.extend(pixels[y * stride : (y + 1) * stride])
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        SIGNATURE
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + _chunk(b"IEND", b"")
    )


def decode_png(data: bytes) -> tuple[int, int, bytes]:
    """Return ``(width, height, rgba8)``."""
    if not data.startswith(SIGNATURE):
        raise ValueError("not a PNG")
    pos = len(SIGNATURE)
    width = height = bit_depth = color_type = interlace = None
    idat = bytearray()
    while pos < len(data):
        if pos + 8 > len(data):
            raise ValueError("truncated PNG chunk")
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        tag = data[pos + 4 : pos + 8]
        start = pos + 8
        end = start + length
        if end + 4 > len(data):
            raise ValueError("truncated PNG data")
        payload = data[start:end]
        crc = struct.unpack(">I", data[end : end + 4])[0]
        expect = zlib.crc32(tag + payload) & 0xFFFFFFFF
        if crc != expect:
            raise ValueError("PNG CRC mismatch")
        pos = end + 4
        if tag == b"IHDR":
            width, height, bit_depth, color_type, comp, filt, interlace = struct.unpack(
                ">IIBBBBB", payload
            )
            if comp != 0 or filt != 0:
                raise ValueError("unsupported PNG compression/filter method")
        elif tag == b"IDAT":
            idat.extend(payload)
        elif tag == b"IEND":
            break
        elif tag[:1].isupper():
            raise ValueError(f"unsupported critical PNG chunk {tag!r}")
    if width is None or height is None:
        raise ValueError("PNG missing IHDR")
    if interlace:
        raise ValueError("interlaced PNG is not supported")
    if bit_depth != 8 or color_type not in (2, 6):
        raise ValueError("only 8-bit RGB/RGBA PNG is supported")
    bpp = 3 if color_type == 2 else 4
    inflated = zlib.decompress(bytes(idat))
    rgba = _unfilter(width, height, bpp, inflated)
    if color_type == 2:
        rgba = _rgb_to_rgba(rgba, width, height)
    return width, height, rgba


def _chunk(tag: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(tag + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", crc)


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _unfilter(width: int, height: int, bpp: int, raw: bytes) -> bytes:
    stride = width * bpp
    expect = height * (stride + 1)
    if len(raw) < expect:
        raise ValueError("PNG IDAT too short")
    out = bytearray(height * stride)
    prev = bytearray(stride)
    src = 0
    for y in range(height):
        ftype = raw[src]
        src += 1
        row = bytearray(raw[src : src + stride])
        src += stride
        if ftype == 0:
            pass
        elif ftype == 1:
            for i in range(stride):
                left = row[i - bpp] if i >= bpp else 0
                row[i] = (row[i] + left) & 255
        elif ftype == 2:
            for i in range(stride):
                row[i] = (row[i] + prev[i]) & 255
        elif ftype == 3:
            for i in range(stride):
                left = row[i - bpp] if i >= bpp else 0
                row[i] = (row[i] + ((left + prev[i]) // 2)) & 255
        elif ftype == 4:
            for i in range(stride):
                left = row[i - bpp] if i >= bpp else 0
                up = prev[i]
                ul = prev[i - bpp] if i >= bpp else 0
                row[i] = (row[i] + _paeth(left, up, ul)) & 255
        else:
            raise ValueError(f"unknown PNG filter {ftype}")
        dest = y * stride
        out[dest : dest + stride] = row
        prev = row
    return bytes(out)


def _rgb_to_rgba(rgb: bytes, width: int, height: int) -> bytes:
    out = bytearray(width * height * 4)
    si = di = 0
    n = width * height
    for _ in range(n):
        out[di : di + 3] = rgb[si : si + 3]
        out[di + 3] = 255
        si += 3
        di += 4
    return bytes(out)
