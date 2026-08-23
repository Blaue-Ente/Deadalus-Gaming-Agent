"""Studio context package — PROJECT_RULES, ARCHITECTURE, DATA_SCHEMA, PROJECT_CONTEXT."""
from __future__ import annotations

from typing import Any

PROJECT_RULES_PATH = "studio/PROJECT_RULES.md"
ARCHITECTURE_PATH = "studio/ARCHITECTURE.md"
DATA_SCHEMA_PATH = "studio/DATA_SCHEMA.md"
PROJECT_CONTEXT_PATH = "studio/PROJECT_CONTEXT.md"
GAME_DESIGN_PATH = "studio/GAME_DESIGN.md"
ROADMAP_PATH = "studio/ROADMAP.md"

CONTEXT_PATHS = (
    PROJECT_RULES_PATH,
    ARCHITECTURE_PATH,
    DATA_SCHEMA_PATH,
    PROJECT_CONTEXT_PATH,
    GAME_DESIGN_PATH,
    ROADMAP_PATH,
)


def _project_rules(kind: str, preset: str) -> str:
    return f"""# PROJECT RULES

## Engine / Platform
{kind} (Daedalus Playground Studio)

## Language
C# (Unity) | TypeScript (Expo/Vite) | Python (FastAPI) — per `kind`

## Architecture
- Data-driven systems — ANY game genre (action, puzzle, RPG, racing, horror, etc.)
- Static definitions (ScriptableObjects / JSON); runtime data in versioned save
- Services for gameplay operations
- UI must not contain business logic
- Local save first; cloud save later

## Coding Rules
- No single giant manager classes
- No hardcoded entity/item IDs in logic
- No direct backend calls from UI
- All save data must be versioned
- All features need acceptance criteria (see feature briefs)
- Use events or store for cross-system communication

## AI Rules
- Do not rewrite unrelated files
- Always list changed files
- Always provide test cases when adding systems
- Always explain integration steps
- Follow studio/*.md context files

## Studio Preset
{preset}
"""


def _architecture(kind: str, preset: str) -> str:
    return f"""# ARCHITECTURE

## Layers
```
Presentation (UI Screens, Components)
    ↓ calls
Gameplay Services (domain-specific: Combat, Puzzle, Inventory, Progression, etc.)
    ↓ uses
Data (definitions + runtime save)
    ↓ optional sync
Backend (auth, cloud save, Firebase) — milestone M6
```

## Core Services (adapt names to genre)
- SaveService — versioned save, migration
- ConfigService — static game config
- EntityService — roster/collection (units, cards, heroes, items — any theme)
- GameplayService — primary loop (combat, puzzle, race, etc.)
- InventoryService — items, stacks
- ProgressionService — XP, unlocks, upgrades
- QuestService — objectives (later)
- EconomyService — currencies (later)

## Flow
Boot → Main Menu → Hub/Level Select → Core Loop → Rewards → Save

## Platform
Kind: `{kind}` | Preset: `{preset}`

## Unity
Open in Unity Hub when `kind=unity`. Integrations: `studio/integrations/INTEGRATIONS.md`
"""


def _data_schema(preset: str) -> str:
    entity_block = """
### EntityDefinition (static — any genre)
- id, displayName, category, rarity/tags
- baseStats or attributes map
- abilities[], rules[], asset references

### EntityInstance (runtime / save)
- instanceId, definitionId, nickname/label
- level, xp/progress, unlockedFeatures[], equippedIds[]
- createdAt
"""
    registry_path = (
        "Assets/_Project/Data/Registry/"
        if preset not in ("backend_api", "hybrid_mobile")
        else "src/registry/"
    )
    return f"""# DATA SCHEMA

## Principle
**Definition** = design-time. **Instance** = per-player progress.

{preset} preset — generic entity model (not creature-only):

{entity_block}

### ItemDefinition / ItemInstance
- id, displayName, stackable, maxStack, category, effects

### SaveFile v1
```json
{{
  "version": 1,
  "playerId": "local",
  "progress": {{}},
  "entities": [],
  "inventory": [],
  "quests": []
}}
```

Registry: `{registry_path}entities.json`, `levels.json`
"""


def _project_context(
    project_name: str,
    kind: str,
    preset: str,
    description: str,
) -> str:
    return f"""# PROJECT CONTEXT

Project Name: {project_name}
Engine/Platform: {kind}
Studio Preset: {preset}

Description:
{description or "(no description)"}

Core Loop (example — adapt to your genre):
1. Player prepares loadout / level / team
2. Enters core gameplay (battle, puzzle, race, etc.)
3. Earns rewards / progress
4. Upgrades / unlocks content
5. Saves and continues

Architecture Rules:
- Services for game logic; UI only calls services
- Data-driven balancing
- Offline-first save; cloud later
- No hardcoded IDs in logic

Current Task:
(See active plan stage / feature brief)
"""


def _game_design(preset: str, description: str) -> str:
    preset_labels = {
        "rpg_collection": "RPG / collection / team battler (any theme)",
        "action": "Action, shooter, arena",
        "puzzle": "Puzzle / casual levels",
        "strategy": "Strategy / tactics",
        "survival": "Survival / crafting",
        "platformer": "Platformer / runner",
        "open_world": "Open world (phased MVP)",
        "multiplayer": "Multiplayer-ready architecture",
        "hybrid_mobile": "Hybrid web → native iOS",
        "backend_api": "Game backend services",
        "custom": "Custom genre from description",
    }
    label = preset_labels.get(preset, preset)
    body = f"""## Genre / Preset
{label}

## MVP Scope
- Core loop playable (offline-first)
- Save/load
- One content vertical slice
- Platform-appropriate build path (Unity Hub / Expo / FastAPI)

## Out of Scope (v1)
- Full open world, MMO, GPS/AR unless explicitly requested
- 100+ content entities before loop is fun
"""
    return f"# GAME DESIGN (lite)\n\n{body}\n\n## Vision\n{description or 'TBD'}\n"


def _roadmap(milestones: list[dict[str, Any]]) -> str:
    lines = ["# ROADMAP\n"]
    for m in milestones:
        mid = m.get("id", "?")
        title = m.get("title", "")
        goal = m.get("goal", "")
        lines.append(f"## {mid}: {title}\n{goal}\n")
    return "\n".join(lines)


def generate_context_pack(
    *,
    project_name: str,
    kind: str,
    preset: str,
    description: str,
    milestones: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    """Return path → markdown content for studio docs."""
    ms = milestones or []
    return {
        PROJECT_RULES_PATH: _project_rules(kind, preset) + "\n",
        ARCHITECTURE_PATH: _architecture(kind, preset) + "\n",
        DATA_SCHEMA_PATH: _data_schema(preset) + "\n",
        PROJECT_CONTEXT_PATH: _project_context(project_name, kind, preset, description) + "\n",
        GAME_DESIGN_PATH: _game_design(preset, description) + "\n",
        ROADMAP_PATH: _roadmap(ms) + "\n",
    }


def context_prompt_block(files: dict[str, str], *, max_chars: int = 6000) -> str:
    """Inject studio docs into build system prompt."""
    if not files:
        return ""
    parts = ["\n━━━ STUDIO CONTEXT (follow these rules) ━━━"]
    budget = max_chars
    priority = (PROJECT_RULES_PATH, ARCHITECTURE_PATH, DATA_SCHEMA_PATH, PROJECT_CONTEXT_PATH)
    for path in priority:
        content = (files.get(path) or "").strip()
        if not content:
            continue
        chunk = content[: min(1500, budget)]
        parts.append(f"\n### {path}\n{chunk}")
        budget -= len(chunk)
        if budget <= 0:
            break
    return "\n".join(parts) + "\n"


def load_context_from_trunk(trunk_files: dict[str, str]) -> dict[str, str]:
    return {p: trunk_files[p] for p in CONTEXT_PATHS if p in trunk_files}


async def write_context_pack_to_project(slug: str, pack: dict[str, str]) -> list[str]:
    """Persist studio markdown files to project workspace."""
    from . import playground as pg

    written: list[str] = []
    for path, content in pack.items():
        try:
            pg.write_file(slug, path, content)
            written.append(path)
        except Exception:
            pass
    return written
