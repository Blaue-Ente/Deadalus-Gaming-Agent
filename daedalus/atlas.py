"""Sprite atlas + tileset packer. Procedural 16×16 kit when no sources exist.

Phaser-style frames: ``{name, x, y, w, h}``. No Pillow — PNG via ``png_codec``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .png_codec import decode_png, encode_rgba

DEFAULT_TILES = ("floor", "wall", "door", "hazard", "water", "grass", "dirt", "crate")
TILE = 16
PAD = 1
ATLAS_REL = "assets/atlas.png"
ATLAS_JSON = "assets/atlas.json"
TILESET_REL = "assets/tileset.png"
TILESET_JSON = "assets/tileset.json"


def pack_sprites(
    sprites: list[tuple[str, int, int, bytes]],
    *,
    padding: int = PAD,
    max_width: int = 256,
) -> tuple[bytes, dict[str, Any]]:
    """Shelf-pack RGBA sprites. Returns PNG bytes + atlas JSON."""
    if not sprites:
        raise ValueError("no sprites to pack")
    ordered = sorted(sprites, key=lambda s: (-s[2], -s[1], s[0]))
    x = padding
    y = padding
    row_h = 0
    width = padding
    frames: list[dict[str, Any]] = []
    placed: list[tuple[str, int, int, int, int, bytes]] = []
    for name, w, h, px in ordered:
        if w < 1 or h < 1:
            raise ValueError(f"sprite {name!r} has empty size")
        if x + w + padding > max_width and x > padding:
            y += row_h + padding
            x = padding
            row_h = 0
        placed.append((name, x, y, w, h, px))
        frames.append({"name": name, "x": x, "y": y, "w": w, "h": h})
        x += w + padding
        row_h = max(row_h, h)
        width = max(width, x)
    height = y + row_h + padding
    canvas = bytearray(width * height * 4)
    for name, sx, sy, w, h, px in placed:
        _blit(canvas, width, sx, sy, w, h, px)
    png = encode_rgba(width, height, bytes(canvas))
    meta = {
        "image": "atlas.png",
        "width": width,
        "height": height,
        "padding": padding,
        "frames": frames,
    }
    return png, meta


def frames_overlap(frames: list[dict[str, Any]]) -> bool:
    boxes = []
    for fr in frames:
        x, y, w, h = int(fr["x"]), int(fr["y"]), int(fr["w"]), int(fr["h"])
        boxes.append((x, y, x + w, y + h, str(fr.get("name") or "")))
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            if a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]:
                return True
    return False


def pack_tileset(
    tiles: list[tuple[str, int, int, bytes]],
    *,
    tile: int = TILE,
    columns: int | None = None,
) -> tuple[bytes, dict[str, Any]]:
    if not tiles:
        raise ValueError("no tiles")
    n = len(tiles)
    cols = columns or min(8, n)
    rows = (n + cols - 1) // cols
    width, height = cols * tile, rows * tile
    canvas = bytearray(width * height * 4)
    out_tiles: list[dict[str, Any]] = []
    for i, (name, w, h, px) in enumerate(tiles):
        if w != tile or h != tile:
            raise ValueError(f"tile {name!r} must be {tile}x{tile}")
        col, row = i % cols, i // cols
        _blit(canvas, width, col * tile, row * tile, tile, tile, px)
        out_tiles.append({"id": i, "name": name, "x": col * tile, "y": row * tile})
    png = encode_rgba(width, height, bytes(canvas))
    meta = {
        "image": "tileset.png",
        "tilewidth": tile,
        "tileheight": tile,
        "columns": cols,
        "tiles": out_tiles,
    }
    return png, meta


def default_kit(tile: int = TILE) -> list[tuple[str, int, int, bytes]]:
    return [(name, tile, tile, paint_tile(name, tile)) for name in DEFAULT_TILES]


def paint_tile(name: str, size: int = TILE) -> bytes:
    buf = bytearray(size * size * 4)
    for y in range(size):
        for x in range(size):
            r, g, b, a = _pixel(name, x, y, size)
            i = (y * size + x) * 4
            buf[i : i + 4] = bytes((r, g, b, a))
    return bytes(buf)


def write_atlas(root: Path, sprites: list[tuple[str, int, int, bytes]] | None = None) -> dict[str, Any]:
    kit = sprites if sprites is not None else default_kit()
    png, meta = pack_sprites(kit)
    return _write_pair(root, ATLAS_REL, ATLAS_JSON, png, meta)


def write_tileset(root: Path, tiles: list[tuple[str, int, int, bytes]] | None = None) -> dict[str, Any]:
    kit = tiles if tiles is not None else default_kit()
    png, meta = pack_tileset(kit)
    return _write_pair(root, TILESET_REL, TILESET_JSON, png, meta)


def load_png_sprites(root: Path, rels: list[str]) -> list[tuple[str, int, int, bytes]]:
    out: list[tuple[str, int, int, bytes]] = []
    for rel in rels:
        path = Path(root) / rel
        if not path.is_file():
            raise FileNotFoundError(rel)
        w, h, px = decode_png(path.read_bytes())
        name = Path(rel).stem
        out.append((name, w, h, px))
    return out


def validate_atlas(root: Path) -> list[str]:
    return _validate_sheet(root, ATLAS_JSON, kind="atlas")


def validate_tileset(root: Path) -> list[str]:
    return _validate_sheet(root, TILESET_JSON, kind="tileset")


def _validate_sheet(root: Path, rel: str, *, kind: str) -> list[str]:
    path = Path(root) / rel
    issues: list[str] = []
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"{rel}: {exc}"[:160]]
    if not isinstance(meta, dict):
        return [f"{rel} must be an object"]
    image = str(meta.get("image") or "")
    png_path = path.parent / image if image else None
    if not png_path or not png_path.is_file():
        issues.append(f"{kind} image missing: {image or '?'}")
        return issues
    try:
        w, h, _px = decode_png(png_path.read_bytes())
    except ValueError as exc:
        issues.append(f"{kind} png: {exc}")
        return issues
    if kind == "atlas":
        frames = meta.get("frames") or []
        if not isinstance(frames, list) or not frames:
            issues.append("atlas.frames is empty")
            return issues
        if frames_overlap(frames):
            issues.append("atlas frames overlap")
        for fr in frames:
            if not isinstance(fr, dict):
                issues.append("atlas frame is not an object")
                continue
            x, y, fw, fh = (int(fr.get(k) or 0) for k in ("x", "y", "w", "h"))
            if fw < 1 or fh < 1 or x < 0 or y < 0 or x + fw > w or y + fh > h:
                issues.append(f"frame {fr.get('name')!r} outside image")
    else:
        tw, th = int(meta.get("tilewidth") or 0), int(meta.get("tileheight") or 0)
        cols = int(meta.get("columns") or 0)
        tiles = meta.get("tiles") or []
        if tw < 1 or th < 1 or cols < 1:
            issues.append("tileset grid is incomplete")
        elif w != cols * tw:
            issues.append(f"tileset width {w} != columns*{tw}")
        if not isinstance(tiles, list) or not tiles:
            issues.append("tileset.tiles is empty")
    return issues


def _write_pair(root: Path, png_rel: str, json_rel: str, png: bytes, meta: dict[str, Any]) -> dict[str, Any]:
    base = Path(root)
    png_path = base / png_rel
    json_path = base / json_rel
    png_path.parent.mkdir(parents=True, exist_ok=True)
    png_path.write_bytes(png)
    json_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return {"ok": True, "png": png_rel, "json": json_rel, **{k: meta[k] for k in meta if k != "frames" and k != "tiles"}, "count": len(meta.get("frames") or meta.get("tiles") or [])}


def _blit(canvas: bytearray, canvas_w: int, x: int, y: int, w: int, h: int, src: bytes) -> None:
    for row in range(h):
        dest = ((y + row) * canvas_w + x) * 4
        start = row * w * 4
        canvas[dest : dest + w * 4] = src[start : start + w * 4]


def _pixel(name: str, x: int, y: int, size: int) -> tuple[int, int, int, int]:
    edge = x == 0 or y == 0 or x == size - 1 or y == size - 1
    if name == "floor":
        chk = (x // 4 + y // 4) & 1
        return (120, 92, 72, 255) if chk else (96, 74, 58, 255)
    if name == "wall":
        brick = (y % 5 == 4) or ((x + (4 if (y // 5) & 1 else 0)) % 8 == 7)
        return (40, 40, 48, 255) if brick or edge else (70, 70, 82, 255)
    if name == "door":
        if 10 <= x <= 12 and 9 <= y <= 11:
            return (220, 180, 40, 255)
        return (50, 32, 20, 255) if edge else (139, 90, 43, 255)
    if name == "hazard":
        stripe = ((x + y) // 3) & 1
        return (192, 57, 43, 255) if stripe else (230, 126, 34, 255)
    if name == "water":
        wave = (y + (x // 3)) % 5 == 0
        return (41, 128, 185, 255) if wave else (52, 152, 219, 255)
    if name == "grass":
        blade = (x * 3 + y) % 7 == 0
        return (39, 174, 96, 255) if blade else (46, 204, 113, 255)
    if name == "dirt":
        spec = (x * 7 + y * 3) % 11 == 0
        return (139, 90, 43, 255) if spec else (160, 82, 45, 255)
    # crate
    if edge or x == y or x + y == size - 1:
        return (90, 60, 20, 255)
    return (196, 163, 90, 255)
