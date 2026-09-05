"""Wiring hub — game engineer knowledge must run on the build path, not only sit in RAG.

Used by the chat agent, autopilot, Game Studio gates, job contract, and planner
addenda. Native tools remain ``math3d`` / ``spatial`` / ``game_lint`` / ``profiler``.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_GAME_RE = re.compile(
    r"\b(игра|game|godot|unity|phaser|three\.?js|webgpu|racer|racing|"
    r"gdscript|\.tscn|gameplay|platformer|shooter|rpg|voxel|"
    r"playground|canvas|webgl|demo|snake|tetris|pong|змия|"
    r"състезан|писта|камера)\b",
    re.I,
)
_HEAVY_RE = re.compile(
    r"\b(quaternion|slerp|ray[- ]?cast|ray[- ]?triangle|octree|quadtree|bvh|"
    r"simd|lag compensation|client-side prediction|broadphase|"
    r"custom physics|shader|glsl|hlsl)\b",
    re.I,
)
_NESTED_COLLIDE = re.compile(
    r"for\s*\([^;)]+\)\s*\{.{0,800}?for\s*\([^;)]+\).{0,800}?"
    r"(distance|length\s*\(|collid|overlaps|intersects|vector3\.distance)",
    re.I | re.S,
)
_NESTED_JS = re.compile(
    r"for\s*\(\s*(const|let|var)\s+\w+\s+of\s+\w+.\{0,600}?"
    r"for\s*\(\s*(const|let|var)\s+\w+\s+of\s+\w+.\{0,600}?"
    r"(distanceTo|manhattan|collid|overlaps)",
    re.I | re.S,
)

_SCAN_SUFFIXES = {".js", ".jsx", ".ts", ".tsx", ".gd", ".cs", ".cpp", ".cc", ".h", ".hpp"}

PROMPT_BLOCK = """\
━━━ GAME ENGINEER CONTRACT (harness — use the tools) ━━━
You are building a game. Knowledge in RAG is not enough — call tools.
1. Custom 3D math (SLERP, rays, look-at, projection) → math3d with claimed.
2. Collision / many entities → spatial(action=advise) BEFORE nested for-loops. Grid/quad/octree/BVH; O(N²) only for tiny N.
3. C++ vectors → GLM or DirectXMath (game_canon ids glm / directxmath), then game_lint. Not a home-grown vec class.
4. Frame spikes → profiler (kind=tracy for Tracy CSV). RenderDoc captures happen on the owner's machine — do not fake them here.
5. Need a worked engine example → game_canon study/open/chunk (milo-path, awesome-engine, engine-arch-samples, unity-chop wiki). Steal structure; never copy assets, books, or Chop Chop.
Load skill senior_tools if the body is not already in context.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""


def looks_like_game(text: str, *, kind: str = "", intent: str = "") -> bool:
    blob = f"{text or ''} {kind or ''} {intent or ''}"
    if (kind or "").lower() in ("godot", "unity", "phaser"):
        return True
    if "playground" in blob.lower():
        return True
    return bool(_GAME_RE.search(blob))


def looks_like_heavy_eng(text: str) -> bool:
    """"Heavy" only when the owner asked for it — "не прави 3D" is not a 3D ask."""
    from .tool_router import strip_negations

    kept, _dropped = strip_negations(text or "")
    return bool(_HEAVY_RE.search(kept))


def extra_skill_ids(text: str, intent: str = "", hint: str = "", kind: str = "") -> list[str]:
    """Skills that must be in extra_ids so disclose actually lists them on game jobs."""
    blob = f"{text or ''} {intent or ''} {hint or ''} {kind or ''}"
    if not looks_like_game(blob, kind=kind, intent=intent):
        return []
    ids = ["senior_tools", "spatial_simd"]
    low = blob.lower()
    if any(k in low for k in ("3d", "quaternion", "slerp", "shader", "camera", "matrix", "ray")):
        ids.append("transform_math")
    if any(k in low for k in ("collision", "physics", "octree", "bvh", "simd", "cache", "entity", "n^2", "broadphase")):
        ids.append("data_oriented_design")
    if any(k in low for k in ("multiplayer", "netcode", "lag", "prediction")):
        ids.append("netcode")
    ids.append("game_canon")
    # unique, stable order
    seen: list[str] = []
    for i in ids:
        if i not in seen:
            seen.append(i)
    return seen


def prefer_skill_id(text: str, *, kind: str = "", intent: str = "") -> str | None:
    if looks_like_game(text, kind=kind, intent=intent):
        return "senior_tools"
    return None


def prompt_block(text: str = "", *, kind: str = "", intent: str = "") -> str:
    if not looks_like_game(text, kind=kind, intent=intent):
        return ""
    return PROMPT_BLOCK


def attach_to_sys(sys: str, *, text: str = "", kind: str = "", intent: str = "") -> str:
    """Append the engineer contract to a builder/planner system prompt."""
    block = prompt_block(text, kind=kind, intent=intent)
    if not block:
        return sys
    if block in (sys or ""):
        return sys
    return f"{sys.rstrip()}\n\n{block}"


def planner_addendum(kind: str = "", description: str = "") -> str:
    if not looks_like_game(description, kind=kind):
        return ""
    return (
        "━━━ GAME ENGINEER (wired) ━━━\n"
        "Plan stages that call math3d / spatial when the slice has custom math or many colliders. "
        "C++: GLM or DirectXMath. Profile with Tracy CSV → profiler kind=tracy. "
        "Architecture/events/FSM: game_canon study those ids (chunk C++ samples). "
        "RenderDoc is owner-side. No O(N²) broadphase past a handful of actors."
    )


def _iter_source(root: Path) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if not root.is_dir():
        return out
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in _SCAN_SUFFIXES:
            continue
        if any(p in path.parts for p in ("node_modules", ".godot", "Library", "bin")):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if len(text) > 200_000:
            continue
        out.append((str(path.relative_to(root)).replace("\\", "/"), text))
    return out


def scan_text(text: str, *, rel: str = "") -> list[str]:
    warnings: list[str] = []
    if _NESTED_COLLIDE.search(text or "") or _NESTED_JS.search(text or ""):
        where = f" in {rel}" if rel else ""
        warnings.append(
            f"nested loop + distance/collision{where} — likely O(N²) broadphase; call spatial(action=advise)"
        )
    return warnings


def scan_project(slug: str) -> dict[str, Any]:
    if not slug:
        return {"ok": True, "scanned": 0, "warnings": []}
    try:
        from . import playground as pg
        root = pg.project_dir(slug)
    except Exception:
        return {"ok": True, "scanned": 0, "warnings": []}
    warnings: list[str] = []
    n = 0
    for rel, text in _iter_source(root):
        n += 1
        warnings.extend(scan_text(text, rel=rel))
        if len(warnings) >= 8:
            break
    return {
        "ok": len(warnings) == 0,
        "slug": slug,
        "scanned": n,
        "warnings": warnings[:8],
        "note": "static scan; not a profiler. Use spatial + profiler on the hot path.",
    }


def tools_used(tool_calls: list[dict[str, Any]] | None) -> set[str]:
    names: set[str] = set()
    for c in tool_calls or []:
        names.add(str(c.get("tool") or c.get("name") or "").lower())
    return names


def missing_eng_tools(user_message: str, tool_calls: list[dict[str, Any]] | None) -> str:
    """If the owner asked for heavy 3D/physics work, require at least one eng tool."""
    if not looks_like_heavy_eng(user_message):
        return ""
    used = tools_used(tool_calls)
    eng = {"math3d", "spatial", "game_lint", "profiler", "game_canon"}
    if used & eng:
        return ""
    return (
        "Тежък 3D/physics/SIMD срез без math3d, spatial, game_lint, profiler или game_canon "
        "— извикай инструмента преди готово."
    )
