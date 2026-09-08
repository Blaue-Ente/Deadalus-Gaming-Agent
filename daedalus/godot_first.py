"""Godot-first pipeline — one small slice must pass the whole order.

Critical order: preflight → contract → context → slice → runtime →
visual → review → export → publish.

Skip is not success. Preview HTML is not Play. Vite/Phaser are not this
kind. A campaign ``world/`` kit waits until this slice is green, unless the
owner asked for epic.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PINNED_VERSION = "4.3"
PINNED_RENDERER = "forward_plus"
CONTRACT_REL = "studio/godot_contract.json"
CONTEXT_REL = "studio/build_context.json"
MANIFEST_REL = "studio/release_manifest.json"
PRESETS_REL = "export_presets.cfg"

STAGES = (
    "preflight",
    "contract",
    "context",
    "slice",
    "runtime",
    "visual",
    "review",
    "export",
    "publish",
)

REQUIRED_FILES = (
    "project.godot",
    "scenes/main.tscn",
    "scripts/player.gd",
    "scripts/autoload/game.gd",
)

_GODOT_ASK = re.compile(r"\b(godot|gdscript|\.tscn|метroid|metroidvania)\b", re.I)
_HTML_GAME = re.compile(
    r"\bimport\s+Phaser\b|from ['\"]phaser['\"]|require\(['\"]phaser['\"]\)|"
    r"document\.getelementbyid\(['\"]c['\"]\)|"
    r"<canvas[\s>]|new\s+phaser\.game",
    re.I,
)


def coerce_kind(kind: str, name: str = "", description: str = "", path: str = "") -> str:
    """kind=godot never falls to Vite/HTML. Godot words / .gd/.tscn force it."""
    k = (kind or "").strip().lower()
    if k == "godot":
        return "godot"
    blob = f"{name} {description} {path} {kind}"
    if _GODOT_ASK.search(blob):
        return "godot"
    rel = (path or "").replace("\\", "/").lower()
    if rel.endswith((".gd", ".tscn")) or rel.endswith("project.godot"):
        return "godot"
    return k or "static"


def allow_world_kit(kind: str, description: str = "", effort: str = "auto") -> bool:
    """Do not start a campaign folder before a Godot slice is the job."""
    if (kind or "").strip().lower() != "godot":
        return True
    if (effort or "").strip().lower() == "epic":
        return True
    blob = (description or "").lower()
    return bool(re.search(r"\b(campaign|world kit|epic|кампания)\b", blob))


def playbook_notes() -> str:
    return (
        "Engine adapter of the shared capability pipeline "
        "(plan → context → build → runtime → gameplay → visual → export → publish). "
        "Godot-first order: preflight → contract → context → slice → runtime → "
        "visual → review → export → publish. "
        "kind=godot. Scaffold already has project.godot. "
        "Patch with edit_file; do not rewrite whole .gd/.tscn each turn. "
        "engine({action: pipeline, slug}) walks the order. "
        "Preview HTML is not Play. Missing Godot/export is FAIL, not skip. "
        "Vite/HTML are a different adapter — never a Godot fallback. "
        "No world/ campaign until this slice is green (unless epic)."
    )


def _read(root: Path, rel: str) -> str:
    path = Path(root) / rel
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def file_hash(root: Path, rel: str) -> str:
    data = (Path(root) / rel).read_bytes() if (Path(root) / rel).is_file() else b""
    return hashlib.sha256(data).hexdigest()[:16]


def godot_version(binary: str) -> str:
    if not binary:
        return ""
    try:
        proc = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=12,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    text = ((proc.stdout or "") + " " + (proc.stderr or "")).strip()
    m = re.search(r"\b(\d+\.\d+(?:\.\d+)?)", text)
    return m.group(1) if m else text[:40]


def renderer_of(root: Path) -> str:
    proj = _read(root, "project.godot")
    m = re.search(r'renderer/rendering_method="([^"]+)"', proj)
    if m:
        return m.group(1)
    if 'PackedStringArray("4.3", "Forward Plus")' in proj or "Forward Plus" in proj:
        return PINNED_RENDERER
    return PINNED_RENDERER if proj else ""


def export_templates_dir() -> Path | None:
    env = (os.environ.get("GODOT_EXPORT_TEMPLATES") or "").strip()
    if env:
        p = Path(env)
        return p if p.is_dir() else None
    home = Path.home()
    candidates = [
        home / ".local/share/godot/export_templates" / f"{PINNED_VERSION}.stable",
        home / ".local/share/godot/export_templates" / f"{PINNED_VERSION}.stable.official",
        Path("/usr/share/godot/export_templates") / f"{PINNED_VERSION}.stable",
        home / "Library/Application Support/Godot/export_templates" / f"{PINNED_VERSION}.stable",
    ]
    for p in candidates:
        if p.is_dir():
            return p
    parent = home / ".local/share/godot/export_templates"
    if parent.is_dir():
        kids = [c for c in parent.iterdir() if c.is_dir() and PINNED_VERSION in c.name]
        if kids:
            return kids[0]
    return None


def preflight(root: Path | None = None) -> dict[str, Any]:
    """Stage 0. Missing engine or headless runner → fail closed."""
    from .godot_runtime_probe import godot_bin

    binary = godot_bin()
    version = godot_version(binary) if binary else ""
    templates = export_templates_dir()
    renderer = renderer_of(root) if root else PINNED_RENDERER
    major_ok = version.startswith(PINNED_VERSION) if version else False
    pin_strict = (os.environ.get("DAEDALUS_GODOT_PIN") or "1").strip().lower() not in {
        "0", "false", "no", "off",
    }
    version_ok = bool(version) and (major_ok or (version.startswith("4.") and not pin_strict))
    if version and version.startswith("3."):
        version_ok = False
    engine_ok = bool(binary) and version_ok
    export_ok = bool(templates)
    blockers: list[str] = []
    if not binary:
        blockers.append("godot_missing")
    elif not version_ok:
        blockers.append(f"godot_version:{version or 'unknown'} (want {PINNED_VERSION})")
    skipped = not binary
    ok = engine_ok and not blockers
    return {
        "stage": "preflight",
        "ok": ok,
        "skipped": skipped,
        "binary": binary,
        "version": version,
        "pinned": PINNED_VERSION,
        "renderer": renderer or PINNED_RENDERER,
        "headless": bool(binary),
        "export_templates": str(templates) if templates else "",
        "export_capability": export_ok,
        "blockers": blockers,
        "error": (
            "Godot 4.3 is not on PATH (set GODOT_BIN). Files are not Play."
            if not binary
            else ("; ".join(blockers) if blockers else "")
        ),
    }


def export_presets_text(*, slug: str, linux_path: str, web_path: str) -> str:
    return (
        "[preset.0]\n"
        "name=\"Linux\"\n"
        "platform=\"Linux\"\n"
        "runnable=true\n"
        "dedicated_server=false\n"
        f"export_path=\"{linux_path}\"\n"
        "encrypt_pck=false\n"
        "\n"
        "[preset.0.options]\n"
        "binary_format/embed_pck=true\n"
        "binary_format/architecture=\"x86_64\"\n"
        "\n"
        "[preset.1]\n"
        "name=\"Web\"\n"
        "platform=\"Web\"\n"
        "runnable=false\n"
        f"export_path=\"{web_path}\"\n"
        "\n"
        "[preset.1.options]\n"
        "html/export_icon=true\n"
        f"variant/godot_slug=\"{slug}\"\n"
    )


def ensure_contract(root: Path, *, slug: str = "", name: str = "") -> dict[str, Any]:
    """Stage 1. Required files, main scene, export preset, release stub."""
    root = Path(root)
    slug = slug or root.name
    missing = [rel for rel in REQUIRED_FILES if not (root / rel).is_file()]
    proj = _read(root, "project.godot")
    main_scene = 'run/main_scene="res://scenes/main.tscn"' in proj
    features_43 = "4.3" in proj
    linux_path = f"build/linux/{slug}.x86_64"
    web_path = "build/web/index.html"
    presets = root / PRESETS_REL
    if not presets.is_file() or "name=\"Linux\"" not in _read(root, PRESETS_REL):
        presets.write_text(
            export_presets_text(slug=slug, linux_path=linux_path, web_path=web_path),
            encoding="utf-8",
        )
    ignore = root / ".gitignore"
    if ignore.is_file():
        text = ignore.read_text(encoding="utf-8")
        if re.search(r"^export_presets\.cfg\s*$", text, re.M):
            ignore.write_text(
                re.sub(r"^export_presets\.cfg\s*\n?", "", text, flags=re.M),
                encoding="utf-8",
            )
    contract = {
        "kind": "godot",
        "engine": f"Godot {PINNED_VERSION}",
        "renderer": renderer_of(root) or PINNED_RENDERER,
        "entry": "res://scenes/main.tscn",
        "autoload": ["Game", "DaedalusBridge"],
        "required_files": list(REQUIRED_FILES),
        "export_preset": "Linux",
        "html_is_not_play": True,
        "slug": slug,
        "name": name or slug,
    }
    _write_json(root / CONTRACT_REL, contract)
    if not (root / MANIFEST_REL).is_file():
        _write_json(root / MANIFEST_REL, {
            "slug": slug,
            "version": "0.0.0",
            "artifact": "",
            "sha256": "",
            "published": False,
            "previous": "",
        })
    html_game = False
    for rel in ("index.html", "studio/preview/index.html"):
        body = _read(root, rel)
        if rel == "index.html" and _HTML_GAME.search(body):
            html_game = True
    blockers = list(missing)
    if not main_scene:
        blockers.append("main_scene")
    if not features_43:
        blockers.append("features_4.3")
    if html_game:
        blockers.append("html_gameplay")
    return {
        "stage": "contract",
        "ok": not blockers,
        "skipped": False,
        "missing": missing,
        "blockers": blockers,
        "contract": CONTRACT_REL,
        "export_presets": PRESETS_REL,
        "main_scene": main_scene,
        "html_is_not_play": True,
    }


def write_context(root: Path, *, plan: str = "") -> dict[str, Any]:
    """Stage 2. Plan + interfaces + version hashes for small patches."""
    root = Path(root)
    hashes = {rel: file_hash(root, rel) for rel in REQUIRED_FILES}
    hashes["scripts/hud.gd"] = file_hash(root, "scripts/hud.gd")
    ctx = {
        "plan": plan or playbook_notes(),
        "interfaces": {
            "Game": "autoload fail/win/reset/score",
            "Player": "CharacterBody2D/3D + move_and_slide",
            "HUD": "CanvasLayer — no gameplay logic",
        },
        "hashes": hashes,
        "updated": datetime.now(timezone.utc).isoformat(),
        "patch_rule": "edit_file for existing .gd/.tscn — do not rewrite the whole file.",
    }
    _write_json(root / CONTEXT_REL, ctx)
    return {"stage": "context", "ok": True, "skipped": False, "hashes": hashes}


def rewrite_is_blind(root: Path, rel: str, content: str, *, min_lines: int = 40) -> bool:
    """True when a write would dump a whole existing Godot file."""
    existing = _read(root, rel)
    if not existing or not content:
        return False
    old_lines = [ln for ln in existing.splitlines() if ln.strip()]
    if len(old_lines) < min_lines:
        return False
    new_set = set(ln.strip() for ln in content.splitlines() if ln.strip())
    keep = sum(1 for ln in old_lines if ln.strip() in new_set)
    return keep < max(8, int(len(old_lines) * 0.35))


def slice_static(root: Path) -> dict[str, Any]:
    """Stage 3. One scene: player, camera, collision, mechanic, win/lose, restart, HUD."""
    from .godot_dod import dod_v2_static

    root = Path(root)
    dod = dod_v2_static(root)
    tscn = _read(root, "scenes/main.tscn")
    player = _read(root, "scripts/player.gd")
    game = _read(root, "scripts/autoload/game.gd")
    hud = _read(root, "scripts/hud.gd") + tscn
    extra: list[dict[str, Any]] = []

    def add(cid: str, ok: bool, detail: str) -> None:
        extra.append({"id": cid, "ok": ok, "detail": detail})

    add("hud", "CanvasLayer" in tscn or "HUD" in tscn or "Label" in hud, "HUD CanvasLayer/Label")
    add("win", "func win" in game or "is_won" in game, "Game.win / is_won")
    add("lose", "func fail" in game or "is_failed" in game, "Game.fail")
    add("restart", "func reset" in game and "reload_current_scene" in game, "Game.reset")
    add("mechanic", bool(re.search(r"Goal|Plate|Pit|Door|Coin", tscn)), "one interactable")
    add("no_phaser", not _HTML_GAME.search(player + game), "no Phaser/HTML in GDScript")
    blocking = list(dod.get("blocking") or [])
    for c in extra:
        if not c["ok"] and c["id"] not in blocking:
            blocking.append(c["id"])
    checks = list(dod.get("checks") or []) + extra
    return {
        "stage": "slice",
        "ok": not blocking,
        "skipped": False,
        "blockers": blocking,
        "checks": checks,
        "is_3d": dod.get("is_3d"),
    }


def gameplay_probe(root: Path) -> dict[str, Any]:
    """Input / collision / win-lose. Missing Godot is skip=fail, not a file pass."""
    root = Path(root)
    player = _read(root, "scripts/player.gd")
    tscn = _read(root, "scenes/main.tscn")
    game = _read(root, "scripts/autoload/game.gd")
    pf = preflight(root)
    skipped = bool(pf.get("skipped")) or not pf.get("ok")
    blockers: list[str] = []
    if skipped:
        blockers.append("gameplay_skipped")
    if not re.search(r"move_and_slide|InputMap|ui_left|ui_right|Input\.get", player):
        blockers.append("input")
    if not re.search(r"Collision|Area2D|Area3D|CharacterBody", tscn + player):
        blockers.append("collision")
    if "func win" not in game and "func fail" not in game:
        blockers.append("win_lose")
    return {
        "stage": "gameplay",
        "ok": not blockers,
        "skipped": skipped,
        "blockers": blockers,
        "error": pf.get("error") or ("" if not blockers else "gameplay probe failed"),
    }


def runtime_verify(root: Path) -> dict[str, Any]:
    """Stage 4. Headless/bridge. Skip = fail."""
    from .godot_runtime_probe import godot_bin, run_runtime

    pf = preflight(root)
    if not pf.get("ok"):
        skipped = bool(pf.get("skipped")) or not pf.get("binary")
        return {
            "stage": "runtime",
            "ok": False,
            "skipped": skipped,
            "blockers": ["runtime_skipped"] if skipped else list(pf.get("blockers") or ["preflight"]),
            "error": pf.get("error") or "Godot missing — skip is not Play.",
            "preflight": pf,
        }
    info = run_runtime(root, frames=24)
    skipped = bool(info.get("skipped"))
    live = info.get("live") or {}
    game = live.get("game") or {}
    tree_ok = bool((live.get("move") or {}).get("path") or live.get("ok"))
    extra_block: list[str] = []
    if skipped:
        extra_block.append("runtime_skipped")
    if not info.get("ok"):
        extra_block.extend(str(x) for x in ((info.get("dod") or {}).get("blocking") or ["runtime"]))
    # Interaction / win-lose are file-backed when the bridge cannot fail the player safely.
    game_keys = set(game) if isinstance(game, dict) else set()
    if godot_bin() and info.get("bridge") and "failed" not in game_keys and "failed" not in str(game):
        # ping.game uses "failed" from is_failed
        pass
    ok = bool(info.get("ok")) and not skipped and not extra_block
    return {
        "stage": "runtime",
        "ok": ok,
        "skipped": skipped,
        "blockers": extra_block or list((info.get("dod") or {}).get("blocking") or []),
        "bridge": bool(info.get("bridge")),
        "ran": bool(info.get("ran")),
        "dod": info.get("dod"),
        "live": live,
        "tree_ok": tree_ok,
        "error": info.get("error") or info.get("bridge_error") or ("" if ok else "runtime failed"),
    }


def visual_quality(root: Path, *, slug: str = "") -> dict[str, Any]:
    """Stage 5. Screenshot of the running game. Low score → not a pass."""
    root = Path(root)
    tscn = _read(root, "scenes/main.tscn")
    frames: list[str] = []
    proof = root / "studio" / "proof"
    if proof.is_dir():
        frames = [p.name for p in sorted(proof.glob("*.png"))]
    shot_ok = False
    for name in frames:
        path = proof / name
        try:
            from .frame_geometry import frame_shows_scene
            if frame_shows_scene(path):
                shot_ok = True
                break
        except Exception:
            if path.is_file() and path.stat().st_size > 800:
                shot_ok = True
    camera = "Camera2D" in tscn or "Camera3D" in tscn
    hud = "HUD" in tscn or "CanvasLayer" in tscn
    materials = "albedo" in tscn or "Polygon2D" in tscn or "StandardMaterial3D" in tscn
    pf = preflight(root)
    skipped = bool(pf.get("skipped"))
    blockers: list[str] = []
    if skipped:
        blockers.append("visual_skipped")
    if not camera:
        blockers.append("camera")
    if not hud:
        blockers.append("hud")
    if pf.get("ok") and not shot_ok:
        blockers.append("screenshot")
    # No LLM critic here — quality_gate runs when DAEDALUS_QUALITY is on.
    score = (int(camera) + int(hud) + int(materials) + int(shot_ok or not pf.get("ok"))) * 2.5
    ok = not blockers
    return {
        "stage": "visual",
        "ok": ok,
        "skipped": skipped,
        "blockers": blockers,
        "score": score,
        "camera": camera,
        "hud": hud,
        "materials": materials,
        "screenshot": shot_ok,
        "frames": frames[:6],
        "error": "visual skipped — Godot missing" if skipped else "",
    }


def _stale_after_write(root: Path) -> bool:
    ctx_path = Path(root) / CONTEXT_REL
    if not ctx_path.is_file():
        return False
    try:
        ctx = json.loads(ctx_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    old = ctx.get("hashes") or {}
    for rel in REQUIRED_FILES:
        prev = str(old.get(rel) or "")
        if prev and prev != file_hash(root, rel):
            return True
    return False


def review_gate(rows: dict[str, dict[str, Any]], *, root: Path | None = None) -> dict[str, Any]:
    """Stage 6. Only exact APPROVED with zero blockers ships."""
    blockers: list[str] = []
    for name in ("preflight", "contract", "slice", "runtime", "visual"):
        row = rows.get(name) or {}
        if row.get("skipped"):
            blockers.append(f"{name}_skipped")
        if row.get("ok") is False:
            blockers.extend(f"{name}:{b}" for b in (row.get("blockers") or [name])[:4])
        if row.get("ok") is None:
            blockers.append(f"{name}_missing")
    if root is not None and _stale_after_write(root):
        blockers.append("stale_evidence")
    # Unique, stable
    seen: list[str] = []
    for b in blockers:
        if b not in seen:
            seen.append(b)
    approved = not seen
    verdict = "APPROVED" if approved else "BLOCKED"
    return {
        "stage": "review",
        "ok": approved,
        "skipped": False,
        "verdict": verdict,
        "blockers": seen,
        "error": "" if approved else f"{verdict}: {', '.join(seen[:8])}",
    }


def export_release(root: Path, *, slug: str = "", preset: str = "Linux") -> dict[str, Any]:
    """Stage 7. Real Godot export, then smoke the artifact outside the project."""
    from .godot_runtime_probe import godot_bin

    root = Path(root)
    slug = slug or root.name
    pf = preflight(root)
    templates = pf.get("export_capability")
    binary = pf.get("binary") or godot_bin()
    if not pf.get("ok") or not binary:
        return {
            "stage": "export",
            "ok": False,
            "skipped": True,
            "blockers": ["export_skipped"],
            "error": pf.get("error") or "Godot missing — cannot export.",
        }
    if not templates:
        return {
            "stage": "export",
            "ok": False,
            "skipped": True,
            "blockers": ["export_templates"],
            "error": (
                "Godot export templates for 4.3 are missing "
                "(set GODOT_EXPORT_TEMPLATES). Export capability is required."
            ),
        }
    ensure_contract(root, slug=slug)
    dest_dir = root / "build" / "linux"
    dest_dir.mkdir(parents=True, exist_ok=True)
    artifact = dest_dir / f"{slug}.x86_64"
    cmd = [
        str(binary),
        "--headless",
        "--path",
        str(root),
        "--export-release",
        preset,
        str(artifact),
    ]
    try:
        proc = subprocess.run(
            cmd, cwd=str(root), capture_output=True, text=True, timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "stage": "export",
            "ok": False,
            "skipped": False,
            "blockers": ["export_failed"],
            "error": str(exc)[:300],
            "command": " ".join(cmd),
        }
    exists = artifact.is_file() and artifact.stat().st_size > 64
    smoke = {"ok": False, "outside_workspace": False}
    if exists:
        smoke = _smoke_outside(artifact)
    ok = exists and proc.returncode == 0 and bool(smoke.get("ok"))
    return {
        "stage": "export",
        "ok": ok,
        "skipped": False,
        "blockers": [] if ok else ["export_or_smoke"],
        "artifact": str(artifact) if exists else "",
        "size": artifact.stat().st_size if exists else 0,
        "returncode": proc.returncode,
        "stdout_tail": (proc.stdout or "")[-800:],
        "stderr_tail": (proc.stderr or "")[-800:],
        "smoke": smoke,
        "command": " ".join(cmd),
        "error": "" if ok else "export or independent smoke failed",
    }


def _smoke_outside(artifact: Path) -> dict[str, Any]:
    """Run the exported binary from a temp dir that is not the Playground."""
    if not os.access(artifact, os.X_OK):
        try:
            artifact.chmod(artifact.stat().st_mode | 0o111)
        except OSError:
            pass
    with tempfile.TemporaryDirectory(prefix="daedalus-godot-smoke-") as tmp:
        dest = Path(tmp) / artifact.name
        shutil.copy2(artifact, dest)
        try:
            dest.chmod(dest.stat().st_mode | 0o111)
        except OSError:
            pass
        cmd = [str(dest), "--headless", "--quit-after", "20"]
        try:
            proc = subprocess.run(
                cmd, cwd=tmp, capture_output=True, text=True, timeout=40,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"ok": False, "outside_workspace": True, "error": str(exc)[:200]}
        return {
            "ok": proc.returncode == 0,
            "outside_workspace": True,
            "returncode": proc.returncode,
            "cwd": tmp,
        }


def publish_artifact(
    root: Path,
    *,
    slug: str = "",
    export_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Stage 8. Publish only the Stage 7 artifact. Keep previous for rollback."""
    from .paths import data_root

    root = Path(root)
    slug = slug or root.name
    row = export_row or {}
    src = Path(str(row.get("artifact") or ""))
    if not row.get("ok") or not src.is_file():
        return {
            "stage": "publish",
            "ok": False,
            "skipped": False,
            "blockers": ["no_immutable_artifact"],
            "error": "Publish requires a green Stage 7 export. Preview HTML is not a release.",
        }
    blob = src.read_bytes()
    digest = hashlib.sha256(blob).hexdigest()
    rel_dir = data_root() / "releases" / slug
    current = rel_dir / "current"
    previous = rel_dir / "previous"
    rel_dir.mkdir(parents=True, exist_ok=True)
    prev_hash = ""
    if current.is_dir():
        if previous.exists():
            shutil.rmtree(previous, ignore_errors=True)
        shutil.move(str(current), str(previous))
        man_prev = previous / "manifest.json"
        if man_prev.is_file():
            try:
                prev_hash = str(json.loads(man_prev.read_text(encoding="utf-8")).get("sha256") or "")
            except Exception:
                prev_hash = ""
    current.mkdir(parents=True, exist_ok=True)
    dest = current / src.name
    shutil.copy2(src, dest)
    version = _next_version(root)
    manifest = {
        "slug": slug,
        "version": version,
        "artifact": dest.name,
        "sha256": digest,
        "bytes": len(blob),
        "previous": prev_hash,
        "published_at": datetime.now(timezone.utc).isoformat(),
        "url": f"/releases/{slug}/current/{src.name}",
    }
    _write_json(current / "manifest.json", manifest)
    _write_json(root / MANIFEST_REL, manifest)
    return {
        "stage": "publish",
        "ok": True,
        "skipped": False,
        "blockers": [],
        "version": version,
        "sha256": digest,
        "artifact": str(dest),
        "previous": prev_hash,
        "url": manifest["url"],
        "rollback": str(previous) if prev_hash else "",
    }


def _next_version(root: Path) -> str:
    man = _read(root, MANIFEST_REL)
    try:
        cur = str((json.loads(man) if man else {}).get("version") or "0.0.0")
    except Exception:
        cur = "0.0.0"
    parts = [int(x) if x.isdigit() else 0 for x in cur.split(".")[:3]]
    while len(parts) < 3:
        parts.append(0)
    parts[2] += 1
    return ".".join(str(x) for x in parts)


def run_pipeline(slug: str, *, through: str = "publish", plan: str = "") -> dict[str, Any]:
    """Walk the Godot-first order. ``through`` stops after that stage (inclusive)."""
    from . import playground as pg

    slug = (slug or "").strip()
    root = pg.project_dir(slug)
    want = (through or "publish").strip().lower()
    if want not in STAGES:
        want = "publish"
    stop_at = STAGES.index(want)
    rows: dict[str, dict[str, Any]] = {}

    rows["preflight"] = preflight(root)
    if stop_at >= 1:
        rows["contract"] = ensure_contract(root, slug=slug)
    if stop_at >= 2:
        rows["context"] = write_context(root, plan=plan)
    if stop_at >= 3:
        rows["slice"] = slice_static(root)
    if stop_at >= 4:
        rows["runtime"] = runtime_verify(root)
    if stop_at >= 5:
        rows["visual"] = visual_quality(root, slug=slug)
    if stop_at >= 6:
        rows["review"] = review_gate(rows, root=root)
    if stop_at >= 7:
        if (rows.get("review") or {}).get("ok"):
            rows["export"] = export_release(root, slug=slug)
        else:
            rows["export"] = {
                "stage": "export",
                "ok": False,
                "skipped": False,
                "blockers": ["review_not_approved"],
                "error": "Export waits for APPROVED review.",
            }
    if stop_at >= 8:
        rows["publish"] = publish_artifact(root, slug=slug, export_row=rows.get("export"))

    ordered = [rows[s] for s in STAGES if s in rows]
    ok = all(r.get("ok") for r in ordered) and not any(r.get("skipped") for r in ordered)
    return {
        "ok": ok,
        "slug": slug,
        "kind": "godot",
        "through": want,
        "stages": {s: rows[s] for s in STAGES if s in rows},
        "blockers": [
            b
            for r in ordered
            for b in (r.get("blockers") or ([r["stage"]] if r.get("ok") is False else []))
        ],
    }


def engine_pipeline_action(action: str, slug: str, **_fields: Any) -> dict[str, Any]:
    act = (action or "").strip().lower()
    from . import playground as pg

    if not slug:
        return {"ok": False, "error": "slug required"}
    root = pg.project_dir(slug)
    if act == "preflight":
        return preflight(root)
    if act == "contract":
        return ensure_contract(root, slug=slug)
    if act == "context":
        return write_context(root)
    if act in {"slice", "dod"}:
        return slice_static(root)
    if act in {"pipeline", "godot_first"}:
        pipe = run_pipeline(slug)
        try:
            from .capability import record_from_engine
            record_from_engine(slug, pipe)
        except Exception:
            pass
        return pipe
    if act == "review":
        pipe = run_pipeline(slug, through="review")
        return pipe.get("stages", {}).get("review") or pipe
    if act == "export":
        return export_release(root, slug=slug)
    if act == "publish":
        pipe = run_pipeline(slug, through="publish")
        return pipe.get("stages", {}).get("publish") or pipe
    if act == "runtime_dod":
        return runtime_verify(root)
    return {"ok": False, "error": f"unknown pipeline action {act!r}"}
