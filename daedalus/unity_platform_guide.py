"""Unity (C#) platform guide for Daedalus Playground planner and builds."""
from __future__ import annotations

UNITY_PLAN_ADDENDUM = """
━━━ UNITY PLAN (C# + URP) ━━━
Типични етапи:
  1) Packages/manifest.json + ProjectSettings (scaffold — не презаписвай без причина)
  2) Assets/_Project/Code/Core/Bootstrap/GameEntry.cs
  3) Assets/_Project/Code/Core/Services/SaveService.cs
  4) Assets/_Project/Code/Gameplay/ — по ЕДИН системен файл на етап
  5) Assets/_Project/Data/ScriptableObjects/ — definitions (SO classes + sample assets as .cs wrappers)
  6) Assets/_Project/Scenes/ — scene setup instructions in comments if .unity too large
  7) Assets/_Project/UI/ — uGUI / UI Toolkit screens
Игри (ВСЯКА тема — RPG, action, puzzle, survival, racing):
  - ScriptableObjects за static data; plain C# classes за runtime/save
  - Services pattern; NO giant singletons
  - Data в Assets/_Project/Data/Registry/*.json + importer stubs
  - MAX ~200 реда/файл; partial classes OK
ЗАБРАНЕНО: React, JSX, npm package.json as main entry, Expo, HTML gameplay.
"""

UNITY_FOLDER_STRUCTURE = """
Assets/_Project/
├── Art/
├── Audio/
├── Code/
│   ├── Core/          Bootstrap, Events, Services, Save, Utilities
│   ├── Gameplay/      Domain systems (combat, inventory, world — any genre)
│   ├── UI/
│   └── Editor/
├── Data/
│   ├── ScriptableObjects/
│   └── Registry/      entities.json, levels.json
├── Prefabs/
├── Scenes/
└── Settings/
Packages/manifest.json
ProjectSettings/
studio/                  Daedalus docs + unity bridge
"""

def autopilot_plan_addendum(kind: str, *, is_game: bool = False) -> str:
    if kind != "unity":
        return ""
    return UNITY_PLAN_ADDENDUM


def kind_rules_block() -> str:
    return f"""[ПЛАТФОРМА: UNITY (C# + URP Mobile/PC)]
• Език: C# only. Unity LTS target.
• Структура:
{UNITY_FOLDER_STRUCTURE}
• Static data: ScriptableObject classes + JSON registry under Assets/_Project/Data/Registry/
• Runtime: serializable classes, SaveService with version migration
• UI: Unity uGUI or UI Toolkit — NO web/React
• Scenes: Boot → MainMenu → Game (names in Assets/_Project/Scenes/)
• AGENT не пуска Unity Editor — генерира C# + JSON + studio docs
• ЗАБРАНЕНО: NEED_PLAYGROUND_RUN / ▶ Run за unity — Editor-ът не работи в ADN cloud
• След build: NEED_PLAYGROUND_PUSH → GitHub/GitLab → Unity Hub → Open project
• Open project locally: Unity Hub → Open → project folder
• Build: File → Build Settings → Android/iOS (local machine)
• Follow studio/PROJECT_RULES.md and studio/DATA_SCHEMA.md
"""
