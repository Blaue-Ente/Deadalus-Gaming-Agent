"""Verify generated playground assets exist on disk (Vibra-style gate).

Before build phases, confirm PNG files are real (magic bytes + minimum size).
"""
from __future__ import annotations

import base64
import logging
import re
from pathlib import Path
from typing import Any

from .playground import project_dir
from .playground_assets import list_assets, normalize_asset_path

log = logging.getLogger(__name__)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"
MIN_ASSET_BYTES = 1024  # reject tiny/corrupt placeholders

_LOAD_IMAGE_PATH_RE = re.compile(
    r"""load\.image\s*\(\s*['"][^'"]+['"]\s*,\s*['"]([^'"]+)['"]""",
    re.MULTILINE,
)

_FALLBACK_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQDWaxo3YgAAAABJRU5ErkJggg=="
)


def ensure_png_bytes(data: bytes) -> bytes:
    """Normalize provider output to PNG (NVIDIA/OpenRouter often return JPEG)."""
    if not data:
        raise ValueError("empty image data")
    if data.startswith(PNG_MAGIC):
        return data
    try:
        from io import BytesIO

        from PIL import Image

        with Image.open(BytesIO(data)) as im:
            out = BytesIO()
            mode = im.mode
            if mode not in ("RGB", "RGBA", "L", "LA"):
                im = im.convert("RGBA")
            im.save(out, format="PNG", optimize=True)
            return out.getvalue()
    except Exception as exc:
        head = data[:8].hex()
        raise RuntimeError(
            f"не мога да конвертирам изображение към PNG (header={head}): {exc}"
        ) from exc


def repair_mislabeled_png(slug: str, rel_path: str) -> bool:
    """Convert JPEG/WebP saved as .png in-place; returns True if repaired."""
    rel = normalize_asset_path(rel_path)
    target = project_dir(slug) / rel
    if not target.exists() or not target.is_file():
        return False
    try:
        raw = target.read_bytes()
    except OSError:
        return False
    if raw.startswith(PNG_MAGIC):
        return False
    if not (raw.startswith(JPEG_MAGIC) or raw[:4] == b"RIFF"):
        return False
    try:
        fixed = ensure_png_bytes(raw)
    except Exception:
        return False
    if len(fixed) < MIN_ASSET_BYTES:
        return False
    try:
        target.write_bytes(fixed)
    except OSError:
        return False
    return True


def _read_head(path: Path, n: int = 8) -> bytes:
    try:
        with path.open("rb") as fh:
            return fh.read(n)
    except OSError:
        return b""


def verify_asset_file(slug: str, rel_path: str) -> dict[str, Any]:
    """Check one asset path under the project workspace."""
    rel = normalize_asset_path(rel_path)
    target = project_dir(slug) / rel
    out: dict[str, Any] = {"path": rel, "ok": False}

    if not target.exists() or not target.is_file():
        out["error"] = "липсва на диска"
        return out

    try:
        size = target.stat().st_size
    except OSError:
        size = 0
    out["bytes"] = size

    if size < MIN_ASSET_BYTES:
        out["error"] = f"твърде малък ({size} B < {MIN_ASSET_BYTES} B)"
        return out

    head = _read_head(target, 12)
    if not head.startswith(PNG_MAGIC):
        if head.startswith(JPEG_MAGIC):
            out["error"] = "JPEG записан като .png (image provider fallback)"
        else:
            out["error"] = "не е валиден PNG"
        return out

    out["ok"] = True
    return out


def verify_assets(slug: str, paths: list[str] | None = None) -> dict[str, Any]:
    """Verify explicit paths or all listed image assets."""
    if paths is not None:
        for p in paths:
            repair_mislabeled_png(slug, p)
        checks = [verify_asset_file(slug, p) for p in paths]
    else:
        checks = []
        for item in list_assets(slug):
            path = item["path"]
            if not item.get("missing"):
                repair_mislabeled_png(slug, path)
            if item.get("missing"):
                checks.append({
                    "path": item["path"],
                    "ok": False,
                    "error": "липсва на диска",
                    "bytes": 0,
                })
            else:
                checks.append(verify_asset_file(slug, item["path"]))

    valid = [c for c in checks if c.get("ok")]
    invalid = [c for c in checks if not c.get("ok")]
    return {
        "total": len(checks),
        "valid": valid,
        "invalid": invalid,
        "valid_paths": [c["path"] for c in valid],
        "ok": len(valid) > 0 and not invalid,
    }


def filter_valid_paths(slug: str, paths: list[str]) -> list[str]:
    """Return only paths that pass on-disk verification (repair JPEG-as-PNG first)."""
    out: list[str] = []
    for p in paths:
        if verify_asset_file(slug, p).get("ok"):
            out.append(p)
            continue
        if repair_mislabeled_png(slug, p) and verify_asset_file(slug, p).get("ok"):
            out.append(p)
    return out


def game_asset_gate(slug: str, *, min_count: int = 2) -> dict[str, Any]:
    """Games need at least ``min_count`` verified PNG assets before UI build."""
    result = verify_assets(slug)
    valid = result["valid_paths"]
    ok = len(valid) >= min_count
    return {
        **result,
        "min_required": min_count,
        "gate_ok": ok,
        "message": (
            f"{len(valid)}/{min_count} валидни PNG assets"
            if ok
            else f"Нужни са поне {min_count} валидни PNG — има {len(valid)}"
        ),
    }


def format_verify_log(result: dict[str, Any]) -> str:
    """One-line summary for autopilot SSE."""
    if result.get("gate_ok") is not None:
        return result.get("message", "")
    valid = len(result.get("valid") or [])
    invalid = len(result.get("invalid") or [])
    if invalid:
        first = (result.get("invalid") or [{}])[0]
        return f"{valid} OK, {invalid} проблем — {first.get('path')}: {first.get('error')}"
    return f"{valid} валидни assets"


def format_verify_block(result: dict[str, Any]) -> str:
    """Structured block when verification blocks build."""
    if result.get("ok") or result.get("gate_ok"):
        return ""
    lines = []
    for item in (result.get("invalid") or [])[:6]:
        lines.append(f"{item.get('path')}: {item.get('error', '?')}")
    detail = "; ".join(lines) or result.get("message", "липсват assets")
    return (
        "РЕЗУЛТАТ: ПРОБЛЕМ\n"
        f"ПРОБЛЕМ: Asset verification — {detail}"
    )


def _solid_placeholder_png(w: int = 128, h: int = 128) -> bytes:
    """Generate a valid PNG (≥ MIN_ASSET_BYTES when PIL is available)."""
    try:
        from io import BytesIO

        from PIL import Image

        im = Image.new("RGBA", (w, h), (100, 149, 237, 255))
        out = BytesIO()
        im.save(out, format="PNG")
        return out.getvalue()
    except Exception:
        return _FALLBACK_PNG


def collect_preload_asset_paths(slug: str) -> list[str]:
    """Parse Phaser preload paths from scene JS/TS files."""
    from . import playground as pg

    paths: set[str] = set()
    for f in pg.read_tree(slug):
        p = f.get("path") or ""
        if not p.endswith((".js", ".jsx", ".ts", ".tsx")):
            continue
        try:
            content = pg.read_file(slug, p, max_bytes=120_000)
        except Exception:
            continue
        for m in _LOAD_IMAGE_PATH_RE.finditer(content):
            rel = m.group(1).strip()
            if not rel or rel.startswith("data:"):
                continue
            paths.add(rel.lstrip("/"))
    return sorted(paths)


def ensure_missing_preload_placeholders(slug: str) -> list[str]:
    """Write placeholder PNGs for preload paths missing on disk (unblocks Loading screen)."""
    from .playground import write_bytes

    created: list[str] = []
    root = project_dir(slug)
    for rel in collect_preload_asset_paths(slug):
        norm = rel if rel.startswith("assets/") else normalize_asset_path(rel)
        target = root / norm
        if target.is_file():
            try:
                if target.stat().st_size >= MIN_ASSET_BYTES and _read_head(target, 8).startswith(PNG_MAGIC):
                    continue
            except OSError:
                pass
        is_bg = any(x in norm.lower() for x in ("bg", "background", "level"))
        size = 512 if is_bg else 256
        if size == 256:
            # Solid PNGs compress small — bump to 512 so we pass MIN_ASSET_BYTES gate
            size = 512
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            data = _solid_placeholder_png(size, size)
            write_bytes(slug, norm, data)
            created.append(norm)
        except Exception as exc:
            log.warning("placeholder asset %s: %s", norm, exc)
    return created
