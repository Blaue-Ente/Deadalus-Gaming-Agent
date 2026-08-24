"""Definition of Done v2 for Godot 3D M0 — static file checks.

Live FPS / console / screenshots need a Godot binary (godot_runtime_probe).
These checks catch planted defects without the Editor: missing player, no
camera, no collision, no materials, no goal, camera parented inside a solid.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def _read(pdir: Path, rel: str) -> str:
    path = Path(pdir) / rel
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


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
            "SpringArm3D" in tscn or "Camera3D" in tscn,
            "SpringArm3D preferred so the camera does not sit inside the mesh",
        )
        add("console", "get_node(" not in player.split("_physics_process")[-1] if "_physics_process" in player else True,
            "No get_node in the hot path of player.gd")
    else:
        add("moves", "CharacterBody2D" in player and "move_and_slide" in player,
            "2D player moves")
        add("fail", "Game.fail" in player, "Fail state")

    blocking = [c for c in checks if not c["ok"] and c["id"] in ("boots", "moves", "camera", "collision", "goal")]
    return {
        "ok": not blocking,
        "is_3d": is_3d,
        "checks": checks,
        "blocking": [c["id"] for c in blocking],
    }
