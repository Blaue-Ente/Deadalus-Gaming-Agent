"""Studio Production Mode — milestones, presets (any game genre), plan finalization."""
from __future__ import annotations

import re
from typing import Any

from .playground_context_pack import generate_context_pack
from .playground_feature_brief import enrich_stages_with_briefs
from .playground_integration_catalog import integrations_for_preset
from .playground_role_catalog import annotate_stage_roles

# Studio presets — ANY game theme, not only creature/collection
STUDIO_PRESETS: dict[str, dict[str, Any]] = {
    "custom": {
        "label": "Custom Game (any genre)",
        "recommended_kind": "unity",
        "description": "User-defined genre — milestones adapt from description.",
    },
    "action": {
        "label": "Action / Shooter / Arena",
        "recommended_kind": "unity",
        "description": "Combat, waves, bosses, weapons, score, levels.",
    },
    "rpg_collection": {
        "label": "RPG / Collection / Team battler",
        "recommended_kind": "unity",
        "description": "Entities, team, auto/real-time combat, XP, inventory — any theme.",
    },
    "creature_rpg": {
        "label": "Creature RPG (alias)",
        "recommended_kind": "unity",
        "description": "Alias for rpg_collection — monsters, pets, heroes, etc.",
    },
    "puzzle": {
        "label": "Puzzle / Casual",
        "recommended_kind": "vite",
        "description": "Levels, mechanics, hints, star ratings.",
    },
    "strategy": {
        "label": "Strategy / Tactics",
        "recommended_kind": "unity",
        "description": "Units, grid/RT, resources, AI opponents.",
    },
    "survival": {
        "label": "Survival / Crafting",
        "recommended_kind": "unity",
        "description": "Resources, crafting, hunger, world threats.",
    },
    "platformer": {
        "label": "Platformer / Runner",
        "recommended_kind": "vite",
        "description": "Movement, levels, obstacles, score.",
    },
    "racing": {
        "label": "Racing / Hover / F-Zero-class",
        "recommended_kind": "vite",
        "description": "Browser 3D racer — spline track, handling, boost, rivals. Web first (FM1 path).",
    },
    "open_world": {
        "label": "Open World / Exploration",
        "recommended_kind": "unity",
        "description": "Map, quests, NPCs, progression — phased MVP.",
    },
    "multiplayer": {
        "label": "Multiplayer / PvP",
        "recommended_kind": "unity",
        "description": "Netcode-ready architecture, match, sync (MVP offline-first).",
    },
        "hybrid_mobile": {
        "label": "Hybrid Mobile (Web shell → iOS)",
        "recommended_kind": "expo",
        "description": "Expo/Vite prototype + Framework7/PakePlus iOS path.",
    },
    "godot_2d": {
        "label": "Godot 2D (Metroidvania / RPG / platformer)",
        "recommended_kind": "godot",
        "description": "Godot 4.3 text scenes + GDScript. Local Editor; blender_jobs for GLB.",
    },
    "backend_api": {
        "label": "Game Backend API",
        "recommended_kind": "fastapi",
        "description": "Auth, cloud save, economy, leaderboards.",
    },
}

# Generic game milestones — genre-agnostic labels
GAME_MILESTONES: list[dict[str, Any]] = [
    {
        "id": "M0",
        "title": "Foundation",
        "goal": "Boot, main menu, scene loading, save v1, navigation",
        "keywords": ("boot", "menu", "save", "navigation", "scaffold", "entry", "layout"),
    },
    {
        "id": "M1",
        "title": "Core Entities",
        "goal": "Definitions, instances, collection/roster, core data model",
        "keywords": ("entity", "definition", "instance", "collection", "roster", "hero", "unit", "card"),
    },
    {
        "id": "M2",
        "title": "Core Loop",
        "goal": "Primary gameplay loop (combat, puzzle, race — per genre)",
        "keywords": ("combat", "battle", "puzzle", "loop", "gameplay", "level", "match"),
    },
    {
        "id": "M3",
        "title": "Progression",
        "goal": "XP, unlocks, upgrades, currency, items",
        "keywords": ("xp", "level", "progression", "upgrade", "currency", "item", "unlock"),
    },
    {
        "id": "M4",
        "title": "Meta / Hub",
        "goal": "Hub scene, crafting, shop, timers, secondary systems",
        "keywords": ("hub", "base", "shop", "craft", "meta", "camp", "home"),
    },
    {
        "id": "M5",
        "title": "Quests / Objectives",
        "goal": "Quest definitions, dailies, achievements",
        "keywords": ("quest", "objective", "daily", "achievement", "mission"),
    },
    {
        "id": "M6",
        "title": "Backend / Live",
        "goal": "Auth, cloud save, remote config, analytics",
        "keywords": ("auth", "cloud", "backend", "firebase", "remote", "analytics"),
    },
    {
        "id": "M7",
        "title": "Polish",
        "goal": "VFX, SFX, tutorial, balancing, UX",
        "keywords": ("vfx", "sfx", "tutorial", "polish", "balance", "animation"),
    },
    {
        "id": "M8",
        "title": "Release",
        "goal": "Build pipeline, iOS/Android signing docs, store prep",
        "keywords": ("build", "release", "ios", "android", "sign", "deploy", "ci"),
    },
]

PLATFORMER_MILESTONES: list[dict[str, Any]] = [
    {"id": "M0", "title": "Foundation", "goal": "Scaffold, input, game loop", "keywords": ("scaffold", "input")},
    {"id": "M1", "title": "Player", "goal": "Movement, physics, controls", "keywords": ("player", "movement", "jump")},
    {"id": "M2", "title": "Levels", "goal": "Stages, obstacles, enemies", "keywords": ("level", "tile", "obstacle")},
    {"id": "M3", "title": "Game Feel", "goal": "Score, lives, game over", "keywords": ("score", "hud", "game over")},
]

RACING_MILESTONES: list[dict[str, Any]] = [
    {
        "id": "M0",
        "title": "Vertical slice",
        "goal": "One ship, one camera, one track segment, accelerate/steer, fail state",
        "keywords": ("scaffold", "ship", "camera", "vehicle", "boot"),
    },
    {
        "id": "M1",
        "title": "Handling",
        "goal": "Boost, brakes, lap, integrity — gas has consequences",
        "keywords": ("boost", "turbo", "lap", "handling", "damage"),
    },
    {
        "id": "M2",
        "title": "Track reading",
        "goal": "Wayfinding chevrons, visible pads, dummy rival",
        "keywords": ("track", "checkpoint", "chevron", "rival", "ai"),
    },
    {
        "id": "M3",
        "title": "Arcade feel",
        "goal": "HUD, audio bus, difficulty, mobile landscape",
        "keywords": ("hud", "audio", "mobile", "touch", "polish"),
    },
]

BACKEND_MILESTONES: list[dict[str, Any]] = [
    {"id": "M0", "title": "API Skeleton", "goal": "App, health, schemas", "keywords": ("main", "health")},
    {"id": "M1", "title": "Auth", "goal": "Login, tokens", "keywords": ("auth", "login")},
    {"id": "M2", "title": "Player Data", "goal": "Profile, cloud save", "keywords": ("player", "save")},
    {"id": "M3", "title": "Economy", "goal": "Inventory, currencies", "keywords": ("inventory", "economy")},
]

GODOT_MILESTONES: list[dict[str, Any]] = [
    {
        "id": "M0",
        "title": "Architecture",
        "goal": "project.godot, autoload Game, physics layers, main scene",
        "keywords": ("scaffold", "project.godot", "autoload", "boot"),
    },
    {
        "id": "M1",
        "title": "Gameplay",
        "goal": "Player move/jump, floor, fail/reset",
        "keywords": ("player", "move", "jump", "fail"),
    },
    {
        "id": "M2",
        "title": "UI / HUD",
        "goal": "On-screen goal, HP/score — no gameplay logic in Control",
        "keywords": ("hud", "ui", "score"),
    },
    {
        "id": "M3",
        "title": "NPC / AI",
        "goal": "One enemy or companion with NavigationAgent2D",
        "keywords": ("npc", "enemy", "navigation"),
    },
]

_PRESET_MILESTONES: dict[str, list[dict[str, Any]]] = {
    "custom": GAME_MILESTONES,
    "action": GAME_MILESTONES,
    "rpg_collection": GAME_MILESTONES,
    "creature_rpg": GAME_MILESTONES,
    "puzzle": GAME_MILESTONES[:5],
    "strategy": GAME_MILESTONES,
    "survival": GAME_MILESTONES,
    "platformer": PLATFORMER_MILESTONES,
    "racing": RACING_MILESTONES,
    "open_world": GAME_MILESTONES,
    "multiplayer": GAME_MILESTONES,
    "hybrid_mobile": GAME_MILESTONES[:6],
    "backend_api": BACKEND_MILESTONES,
    "godot_2d": GODOT_MILESTONES,
}

STUDIO_PLANNER_ADDENDUM = """
━━━ STUDIO PRODUCTION MODE ━━━
Връщай план с milestone структура:
- "studio": true
- "studio_preset": "<preset>"
- "milestones": [{"id":"M0","title":"...","goal":"..."}, ...]
- Всеки stage: "milestone_id":"M0", "studio_role":"gameplay_programmer"|...
- Играта може да е ВСЯКА тема (action, puzzle, RPG, racing, horror, etc.) — не само creatures
- По един deliverable на stage; files[] задължително
- Unity kind: paths under Assets/_Project/Code/
- Godot kind: scenes/*.tscn + scripts/*.gd (едно на етап)
- MVP = първите 4 milestones
"""

STUDIO_PLAN_JSON_FORMAT = (
    'ФОРМАТ (studio): {"title":"...","studio":true,"studio_preset":"custom",'
    '"milestones":[{"id":"M0","title":"Foundation","goal":"..."}],'
    '"stages":[{"id":1,"milestone_id":"M0","studio_role":"platform_engineer",'
    '"title":"...","goal":"...","instructions":"...","uses_visuals":false,"files":["path"]}]}'
)


def list_presets() -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for k, v in STUDIO_PRESETS.items():
        if k == "creature_rpg":
            continue  # alias hidden from primary list
        if k in seen:
            continue
        seen.add(k)
        row = {"id": k, **{kk: vv for kk, vv in v.items() if kk != "description"}}
        row["integrations"] = integrations_for_preset(k)
        out.append(row)
    return out


def milestones_for_preset(preset: str) -> list[dict[str, Any]]:
    key = preset
    if preset == "creature_rpg":
        key = "rpg_collection"
    return [dict(m) for m in _PRESET_MILESTONES.get(key, GAME_MILESTONES)]


def infer_studio_preset(description: str) -> str:
    blob = (description or "").lower()
    if any(w in blob for w in ("fastapi", "backend api", "cloud save api", "playfab server")):
        return "backend_api"
    if any(w in blob for w in ("framework7", "pakeplus", "hybrid ios", "web shell")):
        return "hybrid_mobile"
    if any(
        w in blob
        for w in (
            "racing", "racer", "f-zero", "fzero", "wipeout", "hover",
            "circuit", "grand prix", "състезан", "писта",
        )
    ):
        return "racing"
    if any(
        w in blob
        for w in ("godot", "gdscript", "metroidvania", ".tscn", "tscn")
    ):
        return "godot_2d"
    if any(w in blob for w in ("platformer", "jump", "runner", "mario", "side-scroll")):
        return "platformer"
    if any(w in blob for w in ("puzzle", "match-3", "casual", "tetris", "судоку")):
        return "puzzle"
    if any(w in blob for w in ("strategy", "tactics", "rts", "turn-based strategy")):
        return "strategy"
    if any(w in blob for w in ("survival", "craft", "minecraft", "hunger", "voxel")):
        return "survival"
    if any(w in blob for w in ("open world", "exploration", "quest giver", "mmorpg")):
        return "open_world"
    if any(w in blob for w in ("multiplayer", "pvp", "co-op", "netcode")):
        return "multiplayer"
    if any(w in blob for w in ("shooter", "fps", "action", "arena", "beat")):
        return "action"
    if any(
        w in blob
        for w in (
            "rpg", "collection", "creature", "pokemon", "покемон", "team", "gacha",
            "card game", "auto-battle", "hero", "pet",
        )
    ):
        return "rpg_collection"
    if any(w in blob for w in ("unity", "c#", "scriptableobject", "urp")):
        return "custom"
    return "custom"


def _score_milestone(blob: str, milestone: dict[str, Any]) -> int:
    score = 0
    for kw in milestone.get("keywords") or ():
        if kw.lower() in blob:
            score += 2
    title = (milestone.get("title") or "").lower()
    if title and title in blob:
        score += 3
    return score


def assign_milestone_ids(
    stages: list[dict[str, Any]],
    milestones: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not milestones:
        return stages
    default_mid = milestones[0].get("id", "M0")
    for i, stage in enumerate(stages):
        if not isinstance(stage, dict):
            continue
        if stage.get("milestone_id"):
            continue
        blob = " ".join([
            str(stage.get("title", "")),
            str(stage.get("goal", "")),
            str(stage.get("instructions", "")),
            " ".join(stage.get("files") or []),
        ]).lower()
        best = default_mid
        best_score = 0
        for m in milestones:
            sc = _score_milestone(blob, m)
            if sc > best_score:
                best_score = sc
                best = m.get("id", default_mid)
        if best_score == 0 and len(milestones) > 1:
            idx = min(i * len(milestones) // max(len(stages), 1), len(milestones) - 1)
            best = milestones[idx].get("id", default_mid)
        stage["milestone_id"] = best
    return stages


def finalize_studio_plan(
    plan: dict[str, Any],
    *,
    kind: str,
    preset: str,
    description: str,
    project_name: str = "Studio Project",
    is_game: bool = True,
) -> dict[str, Any]:
    plan = dict(plan)
    plan["studio"] = True
    if preset == "creature_rpg":
        preset = "rpg_collection"
    plan["studio_preset"] = preset
    milestones = plan.get("milestones")
    if not isinstance(milestones, list) or not milestones:
        milestones = milestones_for_preset(preset)
        plan["milestones"] = milestones

    stages = plan.get("stages")
    if not isinstance(stages, list):
        return plan

    assign_milestone_ids(stages, milestones)
    annotate_stage_roles(stages, is_game=is_game)
    enrich_stages_with_briefs(stages, milestones)
    plan["stages"] = stages
    plan["suggested_integrations"] = integrations_for_preset(preset)

    pack = generate_context_pack(
        project_name=project_name,
        kind=kind,
        preset=preset,
        description=description,
        milestones=milestones,
    )
    plan["context_files"] = list(pack.keys())
    plan["context_pack"] = pack
    return plan


def studio_planner_addendum(preset: str) -> str:
    key = "rpg_collection" if preset == "creature_rpg" else preset
    meta = STUDIO_PRESETS.get(key, STUDIO_PRESETS["custom"])
    ms = milestones_for_preset(preset)
    ms_lines = "\n".join(f"  - {m['id']}: {m['title']} — {m['goal']}" for m in ms[:6])
    integrations = ", ".join(integrations_for_preset(key)[:4])
    return (
        STUDIO_PLANNER_ADDENDUM.strip()
        + f"\n\nPreset: {key} — {meta.get('label', key)}"
        + f"\nПрепоръчителен kind: {meta.get('recommended_kind', 'unity')}"
        + f"\nIntegrations: {integrations}"
        + f"\nMilestones (MVP = first 4):\n{ms_lines}"
    )


def recommended_kind_for_preset(preset: str) -> str:
    key = "rpg_collection" if preset == "creature_rpg" else preset
    return str(STUDIO_PRESETS.get(key, {}).get("recommended_kind", "unity"))


def strip_ephemeral_plan_fields(plan: dict[str, Any]) -> dict[str, Any]:
    out = dict(plan)
    out.pop("context_pack", None)
    return out


def studio_combined_sys(combined_sys: str, context_files: dict[str, str]) -> str:
    from .playground_context_pack import context_prompt_block
    block = context_prompt_block(context_files)
    return (combined_sys + block) if block else combined_sys


def augment_stage_for_studio(stage: dict[str, Any]) -> dict[str, Any]:
    from .playground_feature_brief import brief_prompt_block
    from .playground_role_catalog import role_system_addendum
    out = dict(stage)
    parts = [out.get("instructions") or ""]
    role = str(out.get("studio_role") or "")
    if role:
        parts.append(role_system_addendum(role))
    parts.append(brief_prompt_block(out))
    out["instructions"] = "\n".join(p for p in parts if p).strip()
    return out
