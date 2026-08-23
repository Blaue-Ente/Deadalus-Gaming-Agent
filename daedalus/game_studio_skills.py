"""Game Studio skill prompts — CCGS-inspired templates for brainstorm, GDD, gates.

Pure string templates (no LLM dependency). Used by game_studio.py and future GS-B gates.
"""
from __future__ import annotations

GDD_SECTIONS: tuple[str, ...] = (
    "overview",
    "core_loop",
    "player_experience",
    "systems",
    "progression",
    "content_scope",
    "technical",
    "mvp_scope",
)

BRAINSTORM_GENRES: tuple[str, ...] = (
    "action",
    "puzzle",
    "rpg",
    "strategy",
    "platformer",
    "survival",
    "casual",
    "multiplayer",
    "other",
)

AUDIENCE_OPTIONS: tuple[str, ...] = (
    "kids",
    "casual",
    "core",
    "hardcore",
    "all_ages",
)

PLATFORM_HINTS: tuple[str, ...] = (
    "web",
    "mobile",
    "desktop",
    "cross_platform",
)

# First-wave CCGS skill slugs (for GS-B guided integration)
CCGS_SKILL_SLUGS: tuple[str, ...] = (
    "brainstorm",
    "map-systems",
    "design-system",
    "create-epics",
    "create-stories",
    "dev-story",
    "gate-check",
    "smoke-check",
    "balance-check",
    "scope-check",
)

SYSTEM_SUGGESTIONS: dict[str, list[str]] = {
    "action": ["combat", "weapons", "waves", "score", "lives"],
    "puzzle": ["grid", "levels", "hints", "star_rating", "undo"],
    "rpg": ["entities", "inventory", "xp", "team", "skills"],
    "strategy": ["units", "grid", "resources", "ai", "fog_of_war"],
    "platformer": ["movement", "physics", "levels", "checkpoints", "collectibles"],
    "survival": ["crafting", "hunger", "resources", "world_gen", "threats"],
    "casual": ["sessions", "tutorial", "daily_reward", "simple_controls"],
    "multiplayer": ["matchmaking", "sync", "lobby", "netcode_mvp"],
    "other": ["core_loop", "ui", "save", "audio"],
}

BRAINSTORM_PROMPT = """
You are a game design facilitator. Given user inputs, structure a concise brainstorm:
- genre, hook, audience, platform
- 3-sentence pitch
- core loop (verb → reward → repeat)
- 3–5 key systems
- risks / scope warnings
Output bullet points in Bulgarian. No code.
""".strip()

MAP_SYSTEMS_PROMPT = """
Map the brainstorm into concrete game systems:
- Input, Game loop, Entities, Progression, UI/HUD, Audio, Save
For each: purpose, MVP scope (yes/no), dependencies.
""".strip()


def section_title(section_id: str) -> str:
    titles = {
        "overview": "Обзор",
        "core_loop": "Основен цикъл",
        "player_experience": "Играчно изживяване",
        "systems": "Системи",
        "progression": "Прогресия",
        "content_scope": "Обхват на съдържанието",
        "technical": "Технически избори",
        "mvp_scope": "MVP обхват (M0–M3)",
    }
    return titles.get(section_id, section_id.replace("_", " ").title())


def systems_for_genre(genre: str) -> list[str]:
    key = (genre or "other").lower().strip()
    if key in SYSTEM_SUGGESTIONS:
        return list(SYSTEM_SUGGESTIONS[key])
    for k, systems in SYSTEM_SUGGESTIONS.items():
        if k in key or key in k:
            return list(systems)
    return list(SYSTEM_SUGGESTIONS["other"])
