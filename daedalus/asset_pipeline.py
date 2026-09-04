"""Asset pipeline v2 — atlas, tileset, SFX, font, fail-closed 3D jobs.

Native tool ``assets``. 2D kit needs no paid APIs. Hero meshes need a key.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .atlas import (
    ATLAS_JSON,
    TILESET_JSON,
    load_png_sprites,
    validate_atlas,
    validate_tileset,
    write_atlas,
    write_tileset,
)
from .mesh_jobs import (
    music_handoff,
    poll as poll_mesh,
    providers,
    submit as submit_mesh,
    write_greybox,
)
from .sfx_kit import write_font, write_sfx


def run_action(action: str, slug: str, **fields: Any) -> dict[str, Any]:
    """Native tool entry. Writes files under the playground slug."""
    from . import playground as pg

    action = (action or "").strip().lower()
    if action == "providers":
        return {"ok": True, "providers": providers()}
    if action == "music":
        return music_handoff()

    root = pg.project_dir(slug)
    if action in ("status",) and fields.get("job_id"):
        return poll_mesh(str(fields.get("job_id") or ""))
    if not slug:
        return {"ok": False, "error": "slug required"}
    if action != "status" and not root.exists():
        root.mkdir(parents=True, exist_ok=True)

    try:
        if action == "atlas":
            sprites = _sources(root, fields.get("sources"))
            result = write_atlas(root, sprites)
            _sidecars(root)
            return result
        if action == "tileset":
            sprites = _sources(root, fields.get("sources"))
            result = write_tileset(root, sprites)
            _sidecars(root)
            return result
        if action == "sfx":
            result = write_sfx(root)
            _sidecars(root)
            return result
        if action == "font":
            result = write_font(root)
            _sidecars(root)
            return result
        if action == "greybox":
            return write_greybox(root, name=str(fields.get("name") or "greybox_proxy"))
        if action == "mesh":
            return submit_mesh(
                slug,
                str(fields.get("prompt") or ""),
                name=str(fields.get("name") or "hero"),
                provider=str(fields.get("provider") or "auto"),
                transport=fields.get("transport"),
            )
    except (ValueError, FileNotFoundError, OSError) as exc:
        return {"ok": False, "error": str(exc)[:240]}
    if action == "status":
        job_id = str(fields.get("job_id") or "")
        if job_id:
            return poll_mesh(job_id, transport=fields.get("transport"))
        return _project_status(root)
    return {"ok": False, "error": f"unknown action {action}"}


def prove_assets(root: Path | str, *, wants_3d: bool = False) -> dict[str, Any]:
    """Validate claimed atlas/tileset/GLB. Missing optional kit is not a fail."""
    base = Path(root)
    errors: list[str] = []
    warnings: list[str] = []
    assets = base / "assets"
    if (base / ATLAS_JSON).is_file():
        errors.extend(validate_atlas(base))
    if (base / TILESET_JSON).is_file():
        errors.extend(validate_tileset(base))
    contract = base / "studio" / "asset_contract.json"
    if contract.is_file():
        try:
            raw = json.loads(contract.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"asset_contract.json: {exc}"[:160])
            raw = {}
        for row in (raw.get("assets") if isinstance(raw, dict) else None) or []:
            if not isinstance(row, dict):
                continue
            target = str(row.get("export_target") or "")
            rel = target.replace("res://", "")
            if not rel.endswith(".glb"):
                continue
            path = base / rel
            if not path.is_file() or path.stat().st_size < 8:
                errors.append(f"claimed GLB missing or empty: {rel}")
            elif path.read_bytes()[:4] != b"glTF":
                errors.append(f"{rel} is not a GLB")
    glbs = list(assets.glob("*.glb")) if assets.is_dir() else []
    if wants_3d and not glbs:
        if providers().get("any_mesh"):
            warnings.append("3D brief has no GLB yet — poll assets({action:status}) or greybox")
        else:
            warnings.append(
                "3D brief has no GLB; mesh submit needs FAL_KEY/TRIPO_API_KEY/MESHY_API_KEY "
                "(greybox is a cube, not a hero). Procedural racers may omit a hero mesh."
            )
    empty = [
        p for p in glbs
        if p.stat().st_size < 8 or p.read_bytes()[:4] != b"glTF"
    ]
    for p in empty:
        errors.append(f"empty or invalid GLB: assets/{p.name}")
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "atlas": (base / ATLAS_JSON).is_file(),
        "tileset": (base / TILESET_JSON).is_file(),
        "glb": [p.name for p in glbs],
    }


def _project_status(root: Path) -> dict[str, Any]:
    assets = root / "assets"
    sfx = sorted(p.name for p in (assets / "sfx").glob("*.wav")) if (assets / "sfx").is_dir() else []
    glbs = sorted(p.name for p in assets.glob("*.glb")) if assets.is_dir() else []
    jobs = []
    idx = assets / "_jobs.json"
    if idx.is_file():
        try:
            raw = json.loads(idx.read_text(encoding="utf-8"))
            jobs = raw.get("jobs") if isinstance(raw, dict) else []
        except (OSError, json.JSONDecodeError):
            jobs = []
    return {
        "ok": True,
        "providers": providers(),
        "atlas": (root / ATLAS_JSON).is_file(),
        "tileset": (root / TILESET_JSON).is_file(),
        "font": (root / "assets" / "font.png").is_file(),
        "sfx": sfx,
        "glb": glbs,
        "jobs": jobs,
        "prove": prove_assets(root),
    }


def _sources(root: Path, rels: Any) -> list[tuple[str, int, int, bytes]] | None:
    if not rels:
        return None
    if isinstance(rels, str):
        try:
            rels = json.loads(rels)
        except json.JSONDecodeError:
            rels = [r.strip() for r in rels.split(",") if r.strip()]
    if not isinstance(rels, list) or not rels:
        return None
    return load_png_sprites(root, [str(x) for x in rels])


def _sidecars(root: Path) -> None:
    if not (root / "project.godot").is_file():
        return
    try:
        from .godot_bridge import ensure_import_sidecars
        ensure_import_sidecars(root)
    except Exception:
        pass


# re-export for tests
__all__ = ["run_action", "prove_assets", "providers"]
