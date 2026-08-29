"""Godot 4 platform guide — text scenes, GDScript, CLI export.

Distilled from the CrewAI-Godot harness idea: same roles and phases, but
Daedalus already has planner → stages → verify. We do not vendor CrewAI.
"""
from __future__ import annotations

GODOT_PLAN_ADDENDUM = """
━━━ GODOT 4 PLAN (GDScript + .tscn) ━━━
Типични етапи (по 1–2 файла):
  1) project.godot (scaffold — не презаписвай input map / autoload без причина)
  2) scenes/main.tscn + scripts/player.gd — M0 vertical slice
  3) scripts/autoload/game.gd — fail/score/reset
  4) scenes/ui/hud.tscn — HP/score, без gameplay logic
  5) scripts/npc/*.gd — Recast-style waypoints или NavigationAgent2D
  6) assets/*.glb from tools/blender/ — import via glb_instance_tscn, не vertex dumps
Един .tscn ИЛИ един .gd на етап. НЕ цял проект в един prompt.
ЗАБРАНЕНО: Phaser, React, HTML gameplay, C# Unity в този kind.
Cloud: Godot Editor не се пуска тук. Scaffold + Git → локален Godot 4.3.
Ако `godot` е в PATH: godot --headless --path . --quit  (syntax check).
"""

GODOT_FOLDER_STRUCTURE = """
project.godot
scenes/main.tscn
scenes/ui/
scripts/player.gd
scripts/autoload/game.gd
scripts/npc/
assets/          PNG / GLB (from blender_jobs or Mixamo)
tools/blender/   generated bpy jobs — run locally
studio/preview/  status page in Daedalus
"""

# CrewAI harness had 7 agents → Daedalus studio_role names.
GODOT_CREW_ROLES: tuple[str, ...] = (
    "architect",
    "gameplay_programmer",
    "ui_designer",
    "npc_ai",
    "vfx_artist",
    "audio_designer",
    "asset_prompt",
    "tester",
)

# CrewAI harness had 8 phases → Game Studio milestones when kind=godot.
GODOT_PHASES: tuple[dict[str, str], ...] = (
    {"id": "M0", "title": "Architecture", "goal": "project.godot, autoload, layers, one main scene"},
    {"id": "M1", "title": "Gameplay", "goal": "Player move/jump, floor, fail/reset"},
    {"id": "M2", "title": "UI/HUD", "goal": "On-screen goal, HP/score, pause — no logic in Control scripts"},
    {"id": "M3", "title": "NPC / AI", "goal": "One enemy or companion with NavigationAgent2D"},
    {"id": "M4", "title": "VFX", "goal": "One GPUParticles2D / shader for juice (hit, jump)"},
    {"id": "M5", "title": "Audio", "goal": "Bus + 2–3 layered SFX; no fake licensed BGM"},
    {"id": "M6", "title": "Assets", "goal": "Import GLB/PNG; blender_jobs emit, Mixamo for humanoids"},
    {
        "id": "M7",
        "title": "QA",
        "goal": (
            "Headless check if godot in PATH; _exit_tree/disconnect; no get_node in _process; "
            "type hints; pooling"
        ),
    },
)


def kind_rules_block() -> str:
    return f"""[ПЛАТФОРМА: GODOT 4.3 (GDScript)]
• Текстови сцени (.tscn) и .gd — LLM чете/пише директно.
• Структура:
{GODOT_FOLDER_STRUCTURE}
• Input: ui_* defaults OK в M0; после custom actions в project.godot.
• Physics layers: world=1 player=2 enemies=3 pickups=4
• Autoload Game = scripts/autoload/game.gd
• 3D hero meshes: Blender jobs → GLB в assets/, после glb_instance_tscn — не raw vertices.
• QA: _exit_tree + queue_free + disconnect; cache nodes (без get_node в _process);
  type hints; pooling за bullets/VFX; coyote time + jump buffer на CharacterBody2D.
• AGENT не пуска Godot Editor в cloud. Локално: Godot 4.3 → Open folder.
• Optional CLI: godot --headless --path . --quit
• ЗАБРАНЕНО: NEED_PLAYGROUND_RUN за godot в sandbox без binary.
"""


def autopilot_plan_addendum(kind: str, *, is_game: bool = False) -> str:
    if kind != "godot":
        return ""
    return GODOT_PLAN_ADDENDUM.strip()


def pipeline_prompt() -> str:
    lines = [
        "━━━ GODOT PIPELINE (Daedalus roles, not CrewAI) ━━━",
        "One role per stage. Do not spawn a second orchestrator.",
    ]
    for phase, role in zip(GODOT_PHASES, GODOT_CREW_ROLES):
        lines.append(f"- {phase['id']} [{role}]: {phase['goal']}")
    lines.append("Verify after M1: player can move, fail exists, HUD readable.")
    lines.append(
        "QA notes: _exit_tree / queue_free / signal disconnect; "
        "never get_node() in _process; type hints; object pooling; coyote + jump buffer."
    )
    return "\n".join(lines)
