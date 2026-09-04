"""Spatial partitioning advisor — stop O(N²) broadphase before it lands in a game.

Native tool: ``spatial``. CLI: ``python -m daedalus.spatial``.
"""
from __future__ import annotations

import json
import sys
from typing import Any

from .math3d import _num, vec3


def advise(n: Any, dim: Any = 3, *, moving: bool = True, rays: bool = False) -> dict[str, Any]:
    count = max(0, int(_num(n, 0)))
    d = 2 if str(dim).strip() in ("2", "2d") or int(_num(dim, 3)) == 2 else 3
    brute = count * max(0, count - 1) // 2
    if count <= 24:
        rec = "brute_force"
        why = f"N={count} pairs≈{brute} — nested loops are fine for a toy slice."
    elif rays:
        rec = "bvh"
        why = "Many rays / nearest-hit → BVH (or engine PhysicsDirectSpaceState). Not N² ray-vs-all."
    elif d == 2:
        rec = "uniform_grid" if moving else "quadtree"
        why = (
            "2D + similar sizes + motion → uniform grid. "
            "Static sparse 2D with huge size range → quadtree."
        )
    else:
        rec = "uniform_grid" if moving and count < 2000 else "octree"
        why = (
            "3D arcade / similar-sized actors → uniform grid (or Godot/Unity physics). "
            "Large sparse world → octree. Static meshes + rays → BVH."
        )
    return {
        "ok": True,
        "n": count,
        "dim": d,
        "brute_pairs": brute,
        "recommend": rec,
        "why": why,
        "do_not": "for each a: for each b: distance(a,b)  when N is more than a handful",
        "next": "Write original grid/quad/octree/BVH (or use the engine broadphase). math3d ray_aabb for the cheap reject.",
    }


def aabb_overlap(a_min: Any, a_max: Any, b_min: Any, b_max: Any) -> dict[str, Any]:
    amn, amx = vec3(a_min), vec3(a_max)
    bmn, bmx = vec3(b_min), vec3(b_max)
    hit = not (
        amx[0] < bmn[0] or amn[0] > bmx[0]
        or amx[1] < bmn[1] or amn[1] > bmx[1]
        or amx[2] < bmn[2] or amn[2] > bmx[2]
    )
    return {"ok": True, "hit": hit}


def run_action(action: str = "advise", **fields: Any) -> dict[str, Any]:
    act = (action or "advise").strip().lower()
    if act in ("advise", "pick", "recommend"):
        return advise(
            fields.get("n") or fields.get("count") or 0,
            fields.get("dim") or fields.get("dimension") or 3,
            moving=bool(fields.get("moving", True)),
            rays=bool(fields.get("rays") or fields.get("ray")),
        )
    if act in ("aabb", "overlap", "aabb_overlap"):
        try:
            return aabb_overlap(
                fields.get("a_min") or fields.get("amin") or fields.get("a"),
                fields.get("a_max") or fields.get("amax"),
                fields.get("b_min") or fields.get("bmin") or fields.get("b"),
                fields.get("b_max") or fields.get("bmax"),
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:300]}
    if act == "scan":
        from .game_engineer import scan_project, scan_text
        slug = str(fields.get("slug") or "")
        text = fields.get("text")
        if text:
            warns = scan_text(str(text), rel=str(fields.get("path") or "inline"))
            return {"ok": not warns, "warnings": warns, "scanned": 1}
        return scan_project(slug)
    if act in ("catalog", "help", "status"):
        return {
            "ok": True,
            "actions": ["advise", "aabb", "scan"],
            "structures": ["brute_force", "uniform_grid", "quadtree", "octree", "bvh"],
        }
    return {"ok": False, "error": f"unknown action {act}"}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print(json.dumps(run_action("catalog"), indent=2))
        return 0
    if args[0].startswith("{"):
        payload = json.loads(args[0])
        result = run_action(str(payload.pop("action", "advise")), **payload)
    else:
        payload = {"n": args[1]} if len(args) > 1 else {}
        result = run_action(args[0], **payload)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
