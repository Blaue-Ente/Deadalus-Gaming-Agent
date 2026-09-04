"""Definition of Done v2 for Godot slices — static files + signal wiring.

Live FPS / movement / screenshots need a Godot binary (godot_bridge).
Static checks still catch planted defects without the Editor: missing player,
broken ``[connection]`` methods, no camera, no collision, no goal.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_CONN_RE = re.compile(
    r'^\[connection\s+signal="([^"]+)"\s+from="([^"]+)"\s+to="([^"]+)"\s+method="([^"]+)"',
    re.M,
)
_EXT_SCRIPT_RE = re.compile(
    r'^\[ext_resource type="Script"[^\]]*path="(?:res://)?([^"]+)"[^\]]*id="([^"]+)"',
    re.M,
)
_NODE_RE = re.compile(r'^\[node name="([^"]+)"', re.M)
_FUNC_RE = re.compile(r"^func\s+(\w+)\s*\(", re.M)
_EXT_RES_LINE = re.compile(r'ExtResource\("([^"]+)"\)')


def _read(pdir: Path, rel: str) -> str:
    path = Path(pdir) / rel
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _funcs(text: str) -> set[str]:
    return set(_FUNC_RE.findall(text or ""))


def script_methods(pdir: Path) -> dict[str, set[str]]:
    root = Path(pdir)
    out: dict[str, set[str]] = {}
    for path in root.rglob("*.gd"):
        if ".godot" in path.parts:
            continue
        try:
            rel = path.relative_to(root).as_posix()
            out[rel] = _funcs(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
    return out


def _node_scripts(tscn: str) -> dict[str, str]:
    exts = {eid: rel for rel, eid in _EXT_SCRIPT_RE.findall(tscn or "")}
    mapping: dict[str, str] = {}
    current = ""
    for line in (tscn or "").splitlines():
        m = _NODE_RE.match(line)
        if m:
            current = m.group(1)
            continue
        if current and "script = ExtResource(" in line:
            found = _EXT_RES_LINE.search(line)
            if found:
                mapping[current] = exts.get(found.group(1), "")
    return mapping


def tscn_connections(tscn: str) -> list[dict[str, str]]:
    return [
        {"signal": sig, "from": src, "to": dst, "method": method}
        for sig, src, dst, method in _CONN_RE.findall(tscn or "")
    ]


def dod_signals_static(pdir: Path) -> dict[str, Any]:
    """Fail when a .tscn connection points at a method that does not exist."""
    root = Path(pdir)
    methods = script_methods(root)
    all_funcs: set[str] = set()
    for names in methods.values():
        all_funcs |= names
    issues: list[str] = []
    seen = 0
    for path in sorted(root.rglob("*.tscn")):
        if ".godot" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        node_scripts = _node_scripts(text)
        root_name = ""
        first = _NODE_RE.search(text)
        if first:
            root_name = first.group(1)
        rel = path.relative_to(root).as_posix()
        for conn in tscn_connections(text):
            seen += 1
            target = conn["to"]
            if target in {".", root_name}:
                script_rel = node_scripts.get(root_name, "")
            else:
                script_rel = node_scripts.get(target, "")
            method = conn["method"]
            if script_rel and script_rel in methods:
                if method not in methods[script_rel]:
                    issues.append(
                        f"{rel}: {conn['from']}->{target}.{method} missing in {script_rel}"
                    )
                continue
            if method not in all_funcs:
                issues.append(
                    f"{rel}: {conn['from']}->{target}.{method} not in any .gd"
                )
    return {
        "ok": not issues,
        "connections": seen,
        "issues": issues,
    }


def dod_v2_static(pdir: Path) -> dict[str, Any]:
    """Warn/fail-shaped checks. ok=False only when the slice cannot boot."""
    root = Path(pdir)
    tscn = _read(root, "scenes/main.tscn")
    player = _read(root, "scripts/player.gd")
    proj = _read(root, "project.godot")
    checks: list[dict[str, Any]] = []

    def add(cid: str, ok: bool, detail: str) -> None:
        checks.append({"id": cid, "ok": ok, "detail": detail})

    add("boots", bool(proj) and "run/main_scene=" in proj and bool(tscn),
        "project.godot + scenes/main.tscn")
    is_3d = "CharacterBody3D" in tscn or "CharacterBody3D" in player
    if is_3d:
        add("moves", "CharacterBody3D" in player and "move_and_slide" in player,
            "CharacterBody3D + move_and_slide")
        add("camera", "Camera3D" in tscn, "Camera3D in main scene")
        add("collision", "CollisionShape3D" in tscn or "use_collision = true" in tscn,
            "CollisionShape3D or CSG use_collision")
        add(
            "materials",
            "StandardMaterial3D" in tscn or "albedo_color" in tscn or "CSGBox3D" in tscn,
            "Assigned material or CSG albedo (no missing-material pink)",
        )
        add(
            "goal",
            any(tok in tscn for tok in ('name="Goal"', 'name="Door"', 'name="Plate"')),
            "Goal/Door/Plate present — landmark from spawn",
        )
        add(
            "camera_clip",
            "Camera3D" in tscn,
            "Camera3D present (follow-cam offset; SpringArm is optional)",
        )
        add("console", "get_node(" not in player.split("_physics_process")[-1] if "_physics_process" in player else True,
            "No get_node in the hot path of player.gd")
    else:
        add("moves", "CharacterBody2D" in player and "move_and_slide" in player,
            "2D player moves")
        add("fail", "Game.fail" in player, "Fail state")

    sig = dod_signals_static(root)
    add(
        "signals",
        bool(sig["ok"]),
        "; ".join(sig["issues"][:4]) or f"{sig['connections']} tscn connections",
    )

    blocking_ids = ("boots", "moves", "camera", "collision", "goal", "signals")
    blocking = [c for c in checks if not c["ok"] and c["id"] in blocking_ids]
    return {
        "ok": not blocking,
        "is_3d": is_3d,
        "checks": checks,
        "blocking": [c["id"] for c in blocking],
        "signals": sig,
    }


def merge_runtime(static: dict[str, Any], live: dict[str, Any], console_flags: list[str]) -> dict[str, Any]:
    """Static DoD plus live bridge checks. FPS-0 in headless is warn-only."""
    checks = list(static.get("checks") or [])
    live_checks = list(live.get("checks") or [])
    checks.extend(live_checks)
    add_ids = {c["id"] for c in live_checks}
    if "console_clean" not in add_ids:
        checks.append({
            "id": "console_clean",
            "ok": not console_flags,
            "detail": console_flags[:4],
        })
    blocking = list(static.get("blocking") or [])
    for cid in live.get("blocking") or []:
        if cid not in blocking:
            blocking.append(cid)
    if console_flags and "console_clean" not in blocking:
        blocking.append("console_clean")
    return {
        "ok": not blocking,
        "is_3d": static.get("is_3d"),
        "checks": checks,
        "blocking": blocking,
        "live": live,
        "signals": static.get("signals"),
    }
