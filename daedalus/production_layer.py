"""Production layer vs the prototype wall.

M0–M3 is a playable prototype. A systems map with real files, pause/settings,
and persist is the production layer. Claiming store-ready on a prototype fails.
Empty ``design/systems.json`` is not a production layer.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SYSTEMS_REL = "design/systems.json"
RUNTIME_REL = "src/layer/runtime.js"
MAP_VERSION = 1

_SHIP_CLAIM = re.compile(
    r"store[- ]ready|production[- ]ready|release\s*candidate|"
    r"upload to (steam|the store)|publish to (the )?(app store|steam|play)|"
    r"готово за (стора|steam|продажб|пазара)|"
    r"не е прототип|not a prototype|"
    r"production layer|продукционен слой",
    re.I,
)

_SKIP_DIR = frozenset({"node_modules", ".git", "dist", "build", ".godot"})
_CODE_SUFFIX = frozenset({".js", ".mjs", ".cjs", ".ts", ".tsx", ".gd", ".cs", ".html"})


def looks_like_ship_claim(text: str) -> bool:
    """Strong store/ship language. Does not match a racer 'ship' mesh."""
    return bool(_SHIP_CLAIM.search(text or ""))


def empty_map(*, layer: str = "prototype", systems: list[Any] | None = None) -> dict[str, Any]:
    return {
        "version": MAP_VERSION,
        "layer": "production" if layer == "production" else "prototype",
        "systems": [_normalize_system(s) for s in (systems or []) if _normalize_system(s)],
    }


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s[:48] or "system")


def _normalize_system(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, str) and raw.strip():
        sid = _slug(raw)
        return {
            "id": sid,
            "purpose": raw.strip()[:240],
            "mvp": True,
            "files": [],
            "status": "planned",
        }
    if not isinstance(raw, dict):
        return None
    sid = _slug(str(raw.get("id") or raw.get("name") or raw.get("purpose") or "system"))
    files = raw.get("files") if isinstance(raw.get("files"), list) else []
    status = str(raw.get("status") or "planned").strip().lower()
    if status not in ("planned", "prototype", "implemented", "production"):
        status = "planned"
    return {
        "id": sid,
        "purpose": str(raw.get("purpose") or raw.get("name") or sid)[:240],
        "mvp": bool(raw.get("mvp", True)),
        "files": [str(f).replace("\\", "/").lstrip("/") for f in files if str(f).strip()],
        "status": status,
    }


def map_path(root: Path | str) -> Path:
    return Path(root) / SYSTEMS_REL


def load_map(root: Path | str) -> dict[str, Any] | None:
    path = map_path(root)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    layer = str(raw.get("layer") or "prototype").strip().lower()
    systems = raw.get("systems") if isinstance(raw.get("systems"), list) else []
    return empty_map(layer=layer, systems=systems)


def write_map(root: Path | str, doc: dict[str, Any]) -> Path:
    path = map_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def seed_map(
    root: Path | str,
    *,
    systems: list[Any] | None = None,
    layer: str = "prototype",
) -> dict[str, Any]:
    """Write a prototype systems map. Does not inject gameplay files."""
    existing = load_map(root)
    if existing and existing.get("systems"):
        return {"ok": True, "path": str(map_path(root)), "seeded": False, "map": existing}
    doc = empty_map(layer=layer, systems=systems)
    write_map(root, doc)
    return {"ok": True, "path": str(map_path(root)), "seeded": True, "map": doc}


def write_runtime(root: Path | str, *, layer: str = "prototype") -> dict[str, Any]:
    path = Path(root) / RUNTIME_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and "export function persist" in path.read_text(encoding="utf-8"):
        return {"ok": True, "path": str(path), "wrote": False}
    tagged = "production" if layer == "production" else "prototype"
    path.write_text(_RUNTIME_JS.replace("__LAYER__", tagged), encoding="utf-8")
    return {"ok": True, "path": str(path), "wrote": True}


def promote(root: Path | str) -> dict[str, Any]:
    """Mark the map production only when prove passes."""
    proof = prove_production(root, require_production=True)
    if not proof.get("ok"):
        return {"ok": False, "error": "prototype wall — evidence missing", "prove": proof}
    doc = load_map(root) or empty_map(layer="production")
    doc["layer"] = "production"
    write_map(root, doc)
    runtime = Path(root) / RUNTIME_REL
    if runtime.is_file():
        text = runtime.read_text(encoding="utf-8")
        runtime.write_text(text.replace('LAYER = "prototype"', 'LAYER = "production"'), encoding="utf-8")
    proof = prove_production(root, require_production=True)
    return {"ok": True, "layer": "production", "prove": proof}


def status(root: Path | str) -> dict[str, Any]:
    proof = prove_production(root)
    doc = load_map(root)
    systems = list((doc or {}).get("systems") or [])
    return {
        "ok": True,
        "layer": (doc or {}).get("layer") or "prototype",
        "map": bool(systems),
        "runtime": (Path(root) / RUNTIME_REL).is_file(),
        "systems": len(systems),
        "ids": [str(s.get("id") or "") for s in systems if s.get("id")],
        "wall": proof.get("wall") or "prototype",
        "prove_ok": bool(proof.get("ok")),
        "errors": proof.get("errors") or [],
        "warnings": proof.get("warnings") or [],
        "evidence": proof.get("evidence") or {},
    }


def prove_production(
    root: Path | str,
    *,
    claim: str = "",
    build_mode: str = "",
    require_production: bool = False,
) -> dict[str, Any]:
    """Fail closed for fake production. Pre-build absence does not block planning."""
    base = Path(root)
    errors: list[str] = []
    warnings: list[str] = []
    doc = load_map(base)
    wants = require_production or looks_like_ship_claim(claim) or str(build_mode).lower() == "production"
    layer = str((doc or {}).get("layer") or "prototype")
    if layer == "production":
        wants = True

    has_game = _has_game_files(base)
    systems = list((doc or {}).get("systems") or [])

    if not doc or not systems:
        if wants:
            errors.append("empty systems map is not a production layer")
        elif not has_game:
            warnings.append("pre-build — production-check does not block planning")
        else:
            warnings.append("prototype wall — no design/systems.json yet")
        return _result(
            ok=not errors,
            layer=layer,
            wall="hit" if errors else "prototype",
            errors=errors,
            warnings=warnings,
            systems=0,
        )

    implemented = 0
    for row in systems:
        files = list(row.get("files") or [])
        status_s = str(row.get("status") or "planned")
        if status_s in ("planned",) and not files:
            continue
        missing = [f for f in files if not (base / f).is_file()]
        if files and missing:
            errors.append(f"{row.get('id')}: missing {', '.join(missing[:4])}")
        elif files and not missing:
            implemented += 1
        elif status_s in ("implemented", "production") and not files:
            errors.append(f"{row.get('id')}: claimed {status_s} with no files")

    evidence = scan_evidence(base)
    if wants:
        if implemented < 1:
            errors.append("production layer needs at least one system with files that exist")
        if not evidence.get("persist"):
            errors.append("persist missing — localStorage/save or src/layer/runtime.js persist")
        if not evidence.get("pause") and not evidence.get("settings"):
            errors.append("pause/settings missing — not a production layer")
        if layer != "production" and looks_like_ship_claim(claim):
            errors.append("prototype wall — store-ready claims need layer=production and evidence")

    wall = "clear" if (wants and not errors and layer == "production") else ("hit" if errors else "prototype")
    if layer == "prototype" and not wants and not errors:
        warnings.append("honest prototype — M0–M3 is not a store build")

    return _result(
        ok=not errors,
        layer=layer,
        wall=wall,
        errors=errors,
        warnings=warnings,
        systems=len(systems),
        implemented=implemented,
        evidence=evidence,
    )


def scan_evidence(root: Path | str) -> dict[str, bool]:
    persist = False
    pause = False
    settings = False
    runtime = Path(root) / RUNTIME_REL
    blobs: list[str] = []
    if runtime.is_file():
        try:
            blobs.append(runtime.read_text(encoding="utf-8")[:8000])
        except OSError:
            pass
    for path in _iter_code(Path(root)):
        try:
            blobs.append(path.read_text(encoding="utf-8")[:4000])
        except OSError:
            continue
        if len(blobs) > 40:
            break
    text = "\n".join(blobs).lower()
    persist = any(
        tok in text
        for tok in ("localstorage", "sessionstorage", "function persist", "export function persist")
    )
    pause = any(tok in text for tok in ("function pause", "export function pause", "ispaused", "paused ="))
    settings = any(tok in text for tok in ("function settings", "applysettings", "export function settings"))
    return {"persist": persist, "pause": pause, "settings": settings, "runtime": runtime.is_file()}


def run_action(action: str, slug: str, **fields: Any) -> dict[str, Any]:
    from . import playground as pg

    action = (action or "status").strip().lower()
    if not slug:
        return {"ok": False, "error": "slug required"}
    root = pg.project_dir(slug)
    if action in ("seed", "runtime", "map", "promote") and not root.exists():
        root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir() and action not in ("seed", "runtime", "map"):
        return {"ok": False, "error": f"no project {slug}"}

    if action == "status":
        return {"ok": True, **status(root)}
    if action == "prove":
        return prove_production(
            root,
            claim=str(fields.get("claim") or ""),
            build_mode=str(fields.get("build_mode") or ""),
        )
    if action == "seed":
        systems = fields.get("systems")
        if isinstance(systems, str):
            systems = [s.strip() for s in systems.split(",") if s.strip()]
        return seed_map(root, systems=systems if isinstance(systems, list) else None)
    if action == "runtime":
        return write_runtime(root, layer=str(fields.get("layer") or "prototype"))
    if action == "map":
        systems = fields.get("systems")
        layer = str(fields.get("layer") or "prototype")
        if isinstance(systems, str):
            try:
                systems = json.loads(systems)
            except json.JSONDecodeError:
                systems = [s.strip() for s in systems.split(",") if s.strip()]
        doc = empty_map(layer=layer, systems=systems if isinstance(systems, list) else None)
        write_map(root, doc)
        return {"ok": True, "path": str(map_path(root)), "map": doc}
    if action == "promote":
        return promote(root)
    if action == "pipeline":
        from .capability import production_pipeline_action
        return production_pipeline_action(
            slug,
            through=str(fields.get("through") or "publish"),
            kind=str(fields.get("kind") or ""),
            target=str(fields.get("target") or ""),
            text=str(fields.get("text") or fields.get("claim") or ""),
            plan=str(fields.get("plan") or ""),
        )
    return {"ok": False, "error": f"unknown action {action}"}


def _has_game_files(root: Path) -> bool:
    if not root.is_dir():
        return False
    return (
        (root / "index.html").is_file()
        or (root / "src").is_dir()
        or (root / "playtest").is_dir()
        or (root / "project.godot").is_file()
    )


def _iter_code(root: Path):
    if not root.is_dir():
        return
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in _CODE_SUFFIX:
            continue
        if any(part in _SKIP_DIR for part in path.parts):
            continue
        yield path


def _result(
    *,
    ok: bool,
    layer: str,
    wall: str,
    errors: list[str],
    warnings: list[str],
    systems: int,
    implemented: int = 0,
    evidence: dict[str, bool] | None = None,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "layer": layer,
        "wall": wall,
        "errors": errors,
        "warnings": warnings,
        "systems": systems,
        "implemented": implemented,
        "evidence": evidence or {},
    }


_RUNTIME_JS = """/** Daedalus production layer — persist / pause / settings. Not a store listing. */
export const LAYER = "__LAYER__";

const KEY = "daedalus-layer";

export function isPrototype() {
  return LAYER !== "production";
}

export function persist(slot, data) {
  try {
    localStorage.setItem(KEY + ":" + slot, JSON.stringify(data));
    return true;
  } catch {
    return false;
  }
}

export function restore(slot) {
  try {
    const raw = localStorage.getItem(KEY + ":" + slot);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

let paused = false;

export function pause() {
  paused = true;
  persist("pause", { paused });
  return paused;
}

export function resume() {
  paused = false;
  persist("pause", { paused });
  return paused;
}

export function isPaused() {
  return paused;
}

export function settings(patch) {
  const cur = restore("settings") || { mute: false, volume: 1 };
  const next = Object.assign({}, cur, patch || {});
  persist("settings", next);
  return next;
}
"""
