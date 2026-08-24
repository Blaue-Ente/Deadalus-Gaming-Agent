"""Game factory doctrine — Week 1 of the Daedalus game-dev roadmap.

Opinion, not a second orchestrator: one engine path for 3D worlds (Godot),
one art adapter (Blender emit), four cycles later, brutal checks now.

Does not vendor CrewAI, live Editor MCP, Terrain3D, or TRELLIS. Those wait
until Gate 1 (prompt → playable 3D room, 3×) is real on a machine with Godot.
"""
from __future__ import annotations

import re
from typing import Any

ANTI_BUILD: tuple[str, ...] = (
    "Own engine — Godot already exists.",
    "Own 3D generative foundation model — obsolete in two months.",
    "One-sentence prompt-to-game magic — Daedalus wins with a cycle.",
    "Five parallel Godot MCP bridges — pin one; headless first.",
    "Silent self-editing of the harness — keep the Approve gate.",
)

GATE1 = (
    "Prompt → playable 3D room with one puzzle, three times in a row, no human in the loop. "
    "Auto-catch planted defects (missing material, broken collision, unreachable goal). "
    "Record token/time budget."
)

KPI_DAY30: dict[str, str] = {
    "autonomous_builds": "3/5",
    "playable_without_softlock": "3 min",
    "connected_puzzles": "1",
    "assets_pass_contract": "50%",
    "fps_mid_machine": "45",
    "human_interventions": "<10",
    "playtest_agents_finish": "1/4",
}

_GODOT3_RE = re.compile(
    r"\b("
    r"godot\s*3d|3d\s*godot|"
    r"3d\s+(room|puzzle|slice|dungeon)|"
    r"(room|puzzle|dungeon)\s+3d|"
    r"characterbody3d|navmesh|"
    r"puzzle room|играема 3d|3d стая|"
    r"first[- ]person"
    r")\b",
    re.I,
)
_RACER_RE = re.compile(
    r"\b(racer|racing|f-?zero|wipeout|hover\s+(racer|ship))\b",
    re.I,
)
_2D_FORCE_RE = re.compile(r"\b(2d|metroidvania|side[- ]scroll)\b", re.I)


def blob_of(*parts: Any) -> str:
    return " ".join(str(p or "") for p in parts)


def wants_godot_3d_slice(name: str = "", description: str = "") -> bool:
    """True when a Godot project should boot the 3D puzzle-room M0, not 2D.

    Tight: racing stays Vite. 'Godot 4 GDScript dungeon' without 3D stays 2D.
    """
    blob = blob_of(name, description)
    if _RACER_RE.search(blob):
        return False
    if _GODOT3_RE.search(blob):
        return True
    if (
        re.search(r"\bgodot\b", blob, re.I)
        and re.search(r"\b3d\b", blob, re.I)
        and not _2D_FORCE_RE.search(blob)
    ):
        return True
    return False


def planner_addendum(kind: str = "", description: str = "") -> str:
    if kind != "godot" and not wants_godot_3d_slice("", description):
        return ""
    lines = [
        "━━━ GAME FACTORY (Godot 3D, Week 1) ━━━",
        "Headless first. File fallback always. Do not start with Editor automation.",
        "M0: one room, one CharacterBody3D, one camera, one puzzle (plate → door), debug overlay.",
        "Read studio/scene_index.json — not the whole .tscn — when the tree grows.",
        "Blender: primitives, scale, pivot, collision hint, Y-up GLB. No textures/gen yet.",
        "DoD v2: boots, moves, camera no clip, materials present, collision, goal visible from spawn, console clean.",
        "NEVER: five MCP bridges, CrewAI second loop, raw generative mesh into the scene.",
        "Browser F-Zero stays Vite + Three.js. This path is desktop 3D / puzzle room.",
        "Gate 1: " + GATE1,
        "Anti-build: " + "; ".join(ANTI_BUILD[:3]),
    ]
    return "\n".join(lines)
