"""Playable standard — the virtual computer, not a painted Menu.

A Phaser/Vite slice is ready when:

1. Source is not a known trap (Vite Hello, ``<canvas id="game">`` + WEBGL,
   Play.js stub / no input).
2. Kernel Chromium **plays** ``playtest/scenario.json`` (Enter then WASD,
   canvas pixel-diff). Painted Menu alone is not enough.

Mentor is an external evaluator of this standard. It does not assign workers.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_STUB_RE = re.compile(
    r"fill this scene|TODO:?\s*implement|placeholder play|your gameplay here",
    re.I,
)
_INPUT_RE = re.compile(
    r"createCursorKeys|keyboard\.addKeys|addKeys\(|"
    r"keydown-(?:W|A|S|D|UP|LEFT)|"
    r"\bWASD\b|cursors\.|this\.input\.keyboard|"
    r"physics\.add\.(?:sprite|existing|image)",
    re.I,
)
_CANVAS_GAME_RE = re.compile(
    r"<canvas\b[^>]*\bid\s*=\s*['\"]game['\"]|"
    r"<canvas\b[^>]*\bid\s*=\s*['\"]game-canvas['\"]",
    re.I,
)
_WEBGL_RE = re.compile(r"Phaser\.WEBGL")
_PHASER_BOOT_RE = re.compile(r"new\s+Phaser\.Game\b")
_SCENARIO_REL = "playtest/scenario.json"


def _read(path: Path, limit: int = 16000) -> str:
    try:
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""
    return ""


def looks_like_game(
    root: Path | str,
    *,
    kind: str = "",
    package_json: str = "",
    main_js: str = "",
    has_phaser_boot: bool = False,
) -> bool:
    k = (kind or "").strip().lower()
    if k in {"godot", "unity", "phaser"}:
        return True
    blob = f"{package_json}\n{main_js}"
    if has_phaser_boot or _PHASER_BOOT_RE.search(blob):
        return True
    if "phaser" in (package_json or "").lower():
        return True
    marker = Path(root) / ".daedalus-writer-assist.json"
    if marker.is_file() and k in {"vite", "vite-react", "static", ""}:
        return True
    return False


def inspect_tree(root: Path | str, *, kind: str = "") -> dict[str, Any]:
    """Flags the helm and protocol book share. Pure filesystem — no Chromium."""
    base = Path(root)
    html = _read(base / "index.html")
    main = _read(base / "src" / "main.js") or _read(base / "src" / "main.ts")
    pkg = _read(base / "package.json")
    play = _read(base / "src" / "scenes" / "Play.js") or _read(
        base / "src" / "scenes" / "Play.ts"
    )
    has_boot = bool(_PHASER_BOOT_RE.search(f"{html}\n{main}"))
    game = looks_like_game(
        base, kind=kind, package_json=pkg, main_js=main, has_phaser_boot=has_boot,
    )
    canvas_trap = bool(_CANVAS_GAME_RE.search(html) and _WEBGL_RE.search(main))
    play_exists = (base / "src" / "scenes" / "Play.js").is_file() or (
        base / "src" / "scenes" / "Play.ts"
    ).is_file()
    stub = bool(play) and bool(_STUB_RE.search(play))
    incomplete = False
    if game and has_boot:
        if play_exists:
            incomplete = stub or not bool(_INPUT_RE.search(play))
        elif "Play" in main or "./scenes/Play" in main:
            incomplete = True
        elif (base / "src" / "scenes").is_dir():
            incomplete = True
    scenario_missing = bool(game) and not (base / _SCENARIO_REL).is_file()
    source_ok = bool(game) and not canvas_trap and not stub and not incomplete
    if not game:
        source_ok = True
    return {
        "is_game": game,
        "canvas_game_trap": canvas_trap,
        "play_stub": stub,
        "play_incomplete": incomplete,
        "play_exists": play_exists,
        "scenario_missing": scenario_missing,
        "source_playable": source_ok,
        "has_input": bool(play) and bool(_INPUT_RE.search(play)),
    }


def attach_loop_flags(flags: dict[str, Any], loop: Any) -> dict[str, Any]:
    out = dict(flags)
    ran = bool(getattr(loop, "play_bot_ran", False))
    ok = bool(getattr(loop, "play_bot_ok", False))
    gap = str(getattr(loop, "play_bot_gap", "") or "")
    out["play_bot_ran"] = ran
    out["play_bot_ok"] = ok
    out["play_bot_failed"] = ran and not ok and not gap
    out["play_bot_gap"] = bool(gap)
    return out


def may_ship(*, source_playable: bool, is_game: bool, loop: Any, proof_ok: bool) -> bool:
    """True only when a game was played (or a non-game finished its proof)."""
    if not proof_ok:
        return False
    if not source_playable:
        return False
    if not is_game:
        return True
    if getattr(loop, "play_bot_gap", ""):
        return False
    return bool(getattr(loop, "play_bot_ok", False))


def loop_wants_computer_play(loop: Any) -> bool:
    """After a painted proof, drive the scenario bot once (virtual computer)."""
    if loop is None:
        return False
    kind = str(getattr(loop, "kind", "") or "")
    slug = str(getattr(loop, "slug", "") or "")
    if not slug:
        return False
    if not getattr(loop, "_has_writes", lambda: False)():
        return False
    proof_ok = bool(getattr(loop, "proof_ok", False)) or bool(
        getattr(loop, "_has_successful_proof", lambda: False)()
    )
    if not proof_ok:
        return False
    if getattr(loop, "proof_blank", False):
        return False
    if getattr(loop, "play_bot_ran", False) and not getattr(
        loop, "_writes_after_last_proof", lambda: False
    )():
        return False
    try:
        from .playground import project_dir
        root = project_dir(slug)
    except Exception:
        return False
    flags = inspect_tree(root, kind=kind)
    if not flags.get("is_game"):
        return False
    if flags.get("canvas_game_trap") or flags.get("play_stub"):
        return False
    return True
