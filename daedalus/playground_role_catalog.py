"""Studio production role catalog — maps document §7 workers to orchestrator slots."""
from __future__ import annotations

import os
from typing import Any

# Core orchestrator roles (existing env: DAEDALUS_ORCH_MODEL_*)
CORE_ROLES = ("planner", "builder", "game_builder", "reviewer", "asset")

# Extended studio roles — map to core slot + specialized prompt addendum
STUDIO_ROLES: dict[str, dict[str, str]] = {
    "architect": {
        "slot": "planner",
        "label": "Game Architect",
        "addendum": (
            "Роля: Senior Game Architect. Дефинирай модули, client vs backend граници, "
            "data flow, рискове. Не пиши имплементация — само структура и зависимости."
        ),
    },
    "producer": {
        "slot": "planner",
        "label": "Producer / PM",
        "addendum": (
            "Роля: Game Producer. Разбий на epics/tasks с acceptance criteria и зависимости. "
            "Един micro-етап = една проверима задача."
        ),
    },
    "systems_designer": {
        "slot": "planner",
        "label": "Systems Designer",
        "addendum": (
            "Роля: Systems Designer. Правила, формули, edge cases — capture/evolution/combat/economy. "
            "Data-driven; без hardcoded IDs в логика."
        ),
    },
    "gameplay_programmer": {
        "slot": "game_builder",
        "label": "Gameplay Programmer",
        "addendum": (
            "Роля: Gameplay Programmer. Модулен код, тестируем, без static globals. "
            "Services за gameplay; UI само вика services."
        ),
    },
    "ui_designer": {
        "slot": "builder",
        "label": "UI/UX Designer",
        "addendum": (
            "Роля: Mobile UI/UX. Екрани, навигация, mobile constraints. "
            "Без business logic в UI компоненти."
        ),
    },
    "backend_engineer": {
        "slot": "builder",
        "label": "Backend Engineer",
        "addendum": (
            "Роля: Backend Engineer. Auth stub, cloud save API, server time, security. "
            "REST/JSON; отделни route и service слоеве."
        ),
    },
    "data_designer": {
        "slot": "planner",
        "label": "Database Designer",
        "addendum": (
            "Роля: Data Designer. Definition vs Instance разделение. "
            "JSON schema за creatures, items, quests, loot tables."
        ),
    },
    "combat_designer": {
        "slot": "systems_designer",
        "label": "Combat Designer",
        "addendum": (
            "Роля: Combat Designer. Auto-battle loop, abilities, cooldowns, status effects, "
            "balancing parameters в data файлове."
        ),
    },
    "balance_designer": {
        "slot": "systems_designer",
        "label": "Economy / Balance",
        "addendum": (
            "Роля: Economy Designer. XP curves, currency sources/sinks, drop rates — в JSON/TS tables."
        ),
    },
    "tester": {
        "slot": "reviewer",
        "label": "QA / Test Engineer",
        "addendum": (
            "Роля: QA Engineer. Test cases, edge cases, regression checklist. "
            "Unit tests за чисти системи (XP, damage, inventory)."
        ),
    },
    "npc_ai": {
        "slot": "game_builder",
        "label": "NPC / AI Programmer",
        "addendum": (
            "Роля: NPC AI. Godot NavigationAgent2D / waypoints. Един враг с четим intent. "
            "Без огромен behaviour tree в M3."
        ),
    },
    "vfx_artist": {
        "slot": "asset",
        "label": "VFX",
        "addendum": (
            "Роля: VFX. Godot GPUParticles2D или Three.js particles. Един ефект на етап (hit, jump, boost)."
        ),
    },
    "audio_designer": {
        "slot": "asset",
        "label": "Audio",
        "addendum": (
            "Роля: Audio. Layer 2–3 SFX. Godot AudioBus / Howler. Без фалшиви лицензирани парчета."
        ),
    },
    "documenter": {
        "slot": "reviewer",
        "label": "Documentation",
        "addendum": (
            "Роля: Technical Writer. Purpose, architecture, usage, dependencies, TODOs."
        ),
    },
    "devops": {
        "slot": "builder",
        "label": "DevOps / Build",
        "addendum": (
            "Роля: DevOps. CI YAML, versioning, build scripts — без secrets в repo."
        ),
    },
    "asset_prompt": {
        "slot": "asset",
        "label": "Asset Prompt Engineer",
        "addendum": (
            "Роля: AI Art Prompt Engineer. Единен art style, naming convention CRE_/ITM_/ABL_."
        ),
    },
    "platform_engineer": {
        "slot": "builder",
        "label": "Platform Engineer",
        "addendum": (
            "Роля: Platform Engineer (Expo/Vite). Scenes, addressables-like loading, mobile perf."
        ),
    },
    # Future Unity bridge — consumes Daedalus artifacts, does not codegen full game here
    "unity_bridge": {
        "slot": "builder",
        "label": "Unity Bridge",
        "addendum": (
            "Роля: Unity Integration. Генерирай C# stubs + ScriptableObject JSON от Daedalus DATA_SCHEMA. "
            "Не пиши пълен Unity проект — само bridge файлове в studio/unity/."
        ),
    },
}

# File-path heuristics → studio role
_PATH_ROLE_HINTS: list[tuple[tuple[str, ...], str]] = [
    (("test", "spec", "__tests__"), "tester"),
    (("combat", "battle", "ability"), "combat_designer"),
    (("inventory", "item", "loot"), "gameplay_programmer"),
    (("creature", "hero", "enemy", "team"), "gameplay_programmer"),
    (("screen", "ui", "component", "hud", "menu"), "ui_designer"),
    (("save", "cloud", "auth", "backend", "api"), "backend_engineer"),
    (("balance", "economy", "xp", "progression"), "balance_designer"),
    (("schema", "data/", "definitions"), "data_designer"),
    ((".github", "ci", "workflow", "eas.json"), "devops"),
    (("studio/unity",), "unity_bridge"),
    (("scenes/", ".tscn", "scripts/"), "gameplay_programmer"),
    (("gpu_particles", "vfx", "shader"), "vfx_artist"),
    (("audio", "sfx", "bus"), "audio_designer"),
    (("npc", "navigation", "agent2d"), "npc_ai"),
]


def infer_studio_role(stage: dict[str, Any], *, default: str = "gameplay_programmer") -> str:
    """Pick studio role from stage metadata or file paths."""
    explicit = stage.get("studio_role") or stage.get("model_role")
    if explicit and explicit in STUDIO_ROLES:
        return str(explicit)
    blob = " ".join([
        str(stage.get("title", "")),
        str(stage.get("goal", "")),
        str(stage.get("instructions", "")),
        " ".join(stage.get("files") or []),
    ]).lower()
    for needles, role in _PATH_ROLE_HINTS:
        if any(n in blob for n in needles):
            return role
    if stage.get("uses_visuals"):
        return "asset_prompt"
    return default


def orchestrator_slot(studio_role: str) -> str:
    """Map studio role to core MODEL_ROLES slot for LLM routing."""
    meta = STUDIO_ROLES.get(studio_role, {})
    slot = meta.get("slot", "builder")
    if slot in CORE_ROLES:
        return slot
    # systems_designer, combat_designer → planner for routing
    parent = STUDIO_ROLES.get(slot, {})
    return parent.get("slot", "builder") if parent else "builder"


def role_system_addendum(studio_role: str) -> str:
    meta = STUDIO_ROLES.get(studio_role, {})
    label = meta.get("label", studio_role)
    add = meta.get("addendum", "")
    if not add:
        return ""
    return f"\n━━━ STUDIO ROLE: {label} ━━━\n{add}\n"


def resolve_studio_model(studio_role: str, default_model: str) -> str:
    """Env override: DAEDALUS_STUDIO_MODEL_<ROLE> or fall back to orchestrator slot."""
    key = f"DAEDALUS_STUDIO_MODEL_{studio_role.upper()}"
    env = os.environ.get(key, "").strip()
    if env:
        return env
    slot = orchestrator_slot(studio_role)
    slot_key = f"DAEDALUS_ORCH_MODEL_{slot.upper()}"
    return os.environ.get(slot_key, "").strip() or default_model


def annotate_stage_roles(stages: list[dict[str, Any]], *, is_game: bool) -> list[dict[str, Any]]:
    """Set studio_role + model_role on each stage when studio mode is active."""
    default = "gameplay_programmer" if is_game else "builder"
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        role = infer_studio_role(stage, default=default)
        stage.setdefault("studio_role", role)
        if not stage.get("model_role") or stage.get("model_role") in CORE_ROLES:
            stage["model_role"] = orchestrator_slot(role)
    return stages
