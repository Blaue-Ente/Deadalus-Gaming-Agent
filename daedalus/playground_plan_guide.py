"""Granular autopilot plan guidance for every Playground kind.

Centralizes stage templates and planner rules so autopilot produces
8–12 small stages (1–2 files each) instead of monolithic build steps.
"""
from __future__ import annotations

from typing import Any

# Shared granularity rules appended to every planner system prompt.
PLAN_GRANULARITY_RULES = """
ПРАВИЛА ЗА ГРАНУЛИРАНЕ — КРИТИЧНО:
• Всеки етап пише САМО 1–3 файла (посочени в полето 'files').
• Прост проект (landing, calculator): 3–5 етапа.
• Среден проект (dashboard, auth, CRUD): 5–7 етапа.
• Сложен проект (игра, multi-screen, 3D): 8–12 етапа — по един компонент/модул на етап.
• 'files' е ЗАДЪЛЖИТЕЛНО за всеки етап — конкретни пътища (напр. ['src/components/HUD.jsx']).
• Предпочитай повече малки етапи пред малко големи. LLM call ≈ max 3 файла, ~200 реда/файл.
• goal: едно изречение какво работи след етапа.
• instructions: тех. инструкции СЪОБРАЗЕНИ с платформата — без файлове от друга платформа.
• uses_visuals: true само ако ТОЧНО този етап генерира/ползва PNG спрайтове.
"""

PLAN_JSON_FORMAT = (
    'ФОРМАТ: {"title":"...","stages":['
    '{"id":1,"title":"...","goal":"...","instructions":"...","uses_visuals":false,"files":["path"]}'
    "]}"
)

# Human labels for plan/create prompts — keys must match playground ``kind``.
PLAN_KIND_LABELS: dict[str, str] = {
    "static": "статичен уеб сайт / HTML+CSS+JS (без build стъпки)",
    "vite": "Vite + Vanilla JS (ES модули, npm)",
    "vite-react": "Vite + React 18 + JSX (npm, без CDN React)",
    "fastapi": "FastAPI (Python ASGI, uvicorn)",
    "streamlit": "Streamlit (Python, st.* API)",
    "node": "Node.js (Express / http, npm)",
    "expo": "Expo / React Native (JSX компоненти, без HTML)",
    "unity": "Unity (C# URP — отвори в Unity Hub локално)",
    "godot": "Godot 4.3 (GDScript + .tscn — локален Editor)",
}


def resolve_plan_kind(kind: str | None) -> tuple[str, str]:
    """Return ``(kind_key, kind_label)`` for planner prompts vs ``_KIND_RULES`` lookup."""
    key = (kind or "static").strip() or "static"
    return key, PLAN_KIND_LABELS.get(key, key)


_KIND_ADDENDA: dict[str, str] = {
    "static": """
━━━ STATIC PLAN (HTML/CSS/JS) ━━━
Типични етапи (по 1–2 файла):
  1) index.html — структура + link/script тагове (без inline CSS/JS ако е сложно)
  2) style.css — layout, цветове, responsive
  3) script.js — ядро логика / game loop
  4) modules/ui.js или components/*.js — отделни UI модули
  5) modules/game.js — gameplay (ако е игра)
Игри: Canvas/WebGL в отделен файл; rAF loop; НЕ монолитен 500+ реда index.html.
Държи playtest/scenario.json — ботът доказва движение (pixel-diff) и win/lose.
""",
    "vite": """
━━━ VITE PLAN (Vanilla JS + npm) ━━━
Типични етапи:
  1) package.json + vite.config.js + index.html
  2) src/main.js — entry point
  3) src/scenes/MenuScene.js (или src/ui/menu.js)
  4) src/scenes/GameScene.js — gameplay loop
  5) src/lib/audio.js / src/lib/input.js — helpers
Игри 2D: Phaser boot в отделен файл; assets({action: atlas|sfx|font}) за kit, после assets/*.png в uses_visuals етап.
Игри 3D/racing: Three.js в src/racer/ (boot, track, vehicle, world) — НЕ Phaser, НЕ pixel-art.
Ако проектът вече има racer scaffold, етап 1 е да го пуснеш, не да го триеш.
Web игри: държи playtest/scenario.json (вход → canvas_changed / win / lose). Click-Start не е бот.
""",
    "vite-react": """
━━━ VITE-REACT PLAN ━━━
Типични етапи:
  1) package.json, vite.config.js, index.html, src/main.jsx
  2) src/App.jsx — routing / phase FSM (MENU | PLAY | GAME_OVER)
  3) src/components/MenuScreen.jsx
  4) src/components/GameCanvas.jsx — canvas ref + game loop (НЕ useState per frame)
  5) src/components/HUD.jsx — score/lives
  6) src/hooks/useGameState.js — game logic отделно от UI
Игри: React само за меню/HUD; gameplay в canvas/Phaser — НЕ div sprites.
""",
    "fastapi": """
━━━ FASTAPI PLAN ━━━
Типични етапи:
  1) requirements.txt + main.py (app = FastAPI(), health route)
  2) routers/api.py или routes/items.py — CRUD endpoints
  3) models/schemas.py — Pydantic models
  4) templates/index.html + static/style.css (ако има UI)
  5) services/logic.py — бизнес логика отделно от routes
main.py остава тънък — uvicorn търси `app`.
""",
    "streamlit": """
━━━ STREAMLIT PLAN ━━━
Типични етапи:
  1) requirements.txt + streamlit_app.py — layout skeleton
  2) pages/ или секции в app — sidebar + main view
  3) components/charts.py — Plotly/графики helper
  4) data/mock_data.py — sample dataset
Разделяй widgets на логически секции — не 400+ реда в един файл.
""",
    "node": """
━━━ NODE PLAN ━━━
Типични етапи:
  1) package.json + index.js — express listen + /health
  2) routes/api.js — REST endpoints
  3) lib/db.js или services/store.js
  4) public/index.html (ако serve-ва static)
""",
    "expo": """
━━━ EXPO PLAN (React Native + Expo Router) ━━━
Типични етапи:
  1) app.json, package.json, babel.config.js (scaffold — не презаписвай)
  2) src/app/_layout.tsx — Stack + providers
  3) src/app/index.tsx — home screen
  4) src/components/*.tsx — по ЕДИН компонент на етап
  5) src/lib/store.ts — Zustand
MAX ~200 реда/файл; Pressable + StyleSheet; без web APIs.
""",
    "unity": """
━━━ UNITY PLAN (C# URP) ━━━
Типични етапи:
  1) Packages/manifest.json (scaffold — не презаписвай)
  2) Assets/_Project/Code/Core/Bootstrap/GameEntry.cs
  3) Assets/_Project/Code/Core/Services/*.cs — по един service
  4) Assets/_Project/Code/Gameplay/*.cs — core loop (any genre)
  5) Assets/_Project/Data/ScriptableObjects/ + Registry/*.json
  6) Assets/_Project/UI/*.cs — screens
Всяка игра (action, puzzle, RPG, racing…) — data-driven C#, MAX ~200 реда/файл.
""",
    "godot": """
━━━ GODOT PLAN (GDScript + .tscn) ━━━
Типични етапи:
  1) project.godot (scaffold — не презаписвай autoload/layers)
  2) scenes/main.tscn
  3) scripts/player.gd
  4) scripts/autoload/game.gd
  5) scenes/ui/hud.tscn
  6) scripts/npc/enemy.gd
Един .tscn или .gd на етап. GLB от tools/blender/, не vertex dumps.
""",
}

_GAME_EXTRA: dict[str, str] = {
    "static": """
ИГРА: етапи Hero UI, game loop, collision, score — отделни JS модули.
uses_visuals за hero/enemy/background PNG в assets/.
""",
    "vite": """
ИГРА: Phaser.Scene по един на етап (Boot, Menu, Game). assets/*.png задължително.
""",
    "vite-react": """
ИГРА: GameCanvas.jsx отделно; Hero/Enemy като canvas sprites, НЕ JSX divs.
""",
    "expo": """
ИГРА: Hero.jsx, Enemy.jsx, Joystick.jsx, GameArena.jsx — по 1 файл/етап.
PanResponder за input; Animated API — без gesture-handler/reanimated.
""",
    "unity": """
ИГРА: Assets/_Project/Code/Gameplay/ — по 1 система/файл; ScriptableObjects за data.
uses_visuals за sprites в Assets/_Project/Art/.
""",
    "godot": """
ИГРА: CharacterBody2D + fail в Game autoload. HUD чете Game, не дублира state.
uses_visuals за assets/*.png или GLB import.
""",
}


def autopilot_plan_addendum(kind: str, *, is_game: bool = False) -> str:
    """Kind-specific granular staging hints for the planner."""
    if kind == "unity":
        from .unity_platform_guide import autopilot_plan_addendum as _unity_add
        base = _unity_add(kind, is_game=is_game)
    elif kind == "godot":
        from .godot_platform_guide import autopilot_plan_addendum as _godot_add
        base = _godot_add(kind, is_game=is_game)
    else:
        base = _KIND_ADDENDA.get(kind, "")
    if is_game and kind in _GAME_EXTRA:
        base += _GAME_EXTRA[kind]
    return base.strip()


def plan_granularity_rules() -> str:
    return PLAN_GRANULARITY_RULES.strip()


def plan_json_format() -> str:
    return PLAN_JSON_FORMAT


def build_planner_system_prompt(
    kind: str,
    kind_rules_block: str,
    *,
    is_game: bool = False,
    extra_addendum: str = "",
    ultra: bool = False,
    studio: bool = False,
    studio_preset: str = "custom",
    description: str = "",
    slug: str = "",
) -> str:
    """Assemble full planner system prompt for a given kind."""
    kind_add = autopilot_plan_addendum(kind, is_game=is_game)
    studio_add = ""
    ultra_add = ""
    if studio:
        from .playground_studio_mode import (
            STUDIO_PLAN_JSON_FORMAT as _studio_fmt,
            studio_planner_addendum as _studio_add_fn,
        )
        json_fmt = _studio_fmt
        studio_add = _studio_add_fn(studio_preset)
        if ultra:
            from .playground_ultra_plan import ULTRA_PLANNER_ADDENDUM as _ultra_add
            studio_add += "\n\n" + _ultra_add.strip()
    elif ultra:
        from .playground_ultra_plan import ULTRA_PLANNER_ADDENDUM as _ultra_add
        from .playground_ultra_plan import ULTRA_PLAN_JSON_FORMAT as _ultra_fmt
        json_fmt = _ultra_fmt
        ultra_add = _ultra_add.strip()
    else:
        json_fmt = plan_json_format()
        ultra_add = ""
    parts = [
        "Ти си строителен плановик. Връщаш САМО валиден JSON строителен план.",
        json_fmt,
        "",
        "━━━ ПЛАТФОРМА (ЗАДЪЛЖИТЕЛНО спазвай) ━━━",
        kind_rules_block,
    ]
    if studio_add:
        parts.extend(["", studio_add])
    elif ultra_add:
        parts.extend(["", ultra_add])
    if kind_add:
        parts.extend(["", kind_add])
    if extra_addendum:
        parts.extend(["", extra_addendum.strip()])
    if slug:
        try:
            from .dissection import prompt_block_for_slug
            specimen = prompt_block_for_slug(slug)
            if specimen:
                parts.extend(["", specimen])
        except Exception:
            pass
    if is_game:
        try:
            from .arcade_craft import planner_addendum as _craft_plan
            from .studio_shelf import prompt_block as _shelf_block
            desc = f"{studio_preset} {kind} {description} {extra_addendum}"
            parts.extend(["", _craft_plan(kind, desc), _shelf_block(desc, limit=8)])
            try:
                from .game_engineer import planner_addendum as _eng_plan
                eng = _eng_plan(kind, desc)
                if eng and eng not in "\n".join(parts):
                    parts.append(eng)
            except Exception:
                pass
            if kind == "godot":
                from .godot_platform_guide import pipeline_prompt
                from .blender_jobs import prompt_block as _blend
                from .game_factory import planner_addendum as _factory
                parts.extend(["", pipeline_prompt(), _blend(), _factory(kind, desc)])
        except Exception:
            pass
    parts.extend([
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        plan_granularity_rules() + (
            "\n• STUDIO: milestone_id + studio_role на всеки stage; MVP = първите 4 milestones."
            if studio else (
                "\n• ULTRA: всеки етап = 1 файл, micro:true, fragment_command задължителен."
                if ultra else ""
            )
        ),
        "",
        "- Всичко на български",
        "- Започни директно с { — никакъв друг текст",
        "",
        "SCOPE REALISM — КРИТИЧНО:",
        "- Playground поддържа multi-file проекти. НЯМА технически лимит за сложност.",
        "- Enterprise scope → POC в гранулирани етапи; Full scope → v2.",
        "- НЕ казвай 'невъзможно в Playground'.",
        "- Невъзможна native технология → web алтернатива (WebXR и т.н.).",
    ])
    return "\n".join(parts)


def _default_files_for_kind(kind: str, stage_index: int, title: str) -> list[str]:
    """Fallback file hints when LLM omits 'files' field."""
    t = (title or "").lower()
    if kind == "vite" and any(
        w in t for w in ("ship", "track", "vehicle", "racer", "boost", "slice", "handling")
    ):
        racer_defaults = [
            ["package.json", "vite.config.js", "index.html"],
            ["src/main.js", "src/racer/boot.js"],
            ["src/racer/track.js"],
            ["src/racer/vehicle.js"],
        ]
        return list(racer_defaults[min(stage_index, len(racer_defaults) - 1)])
    defaults: dict[str, list[list[str]]] = {
        "static": [
            ["index.html"],
            ["style.css"],
            ["script.js"],
            ["modules/game.js"],
        ],
        "vite": [
            ["package.json", "vite.config.js", "index.html"],
            ["src/main.js"],
            ["src/scenes/MenuScene.js"],
            ["src/scenes/GameScene.js"],
        ],
        "vite-react": [
            ["package.json", "vite.config.js", "index.html", "src/main.jsx"],
            ["src/App.jsx"],
            ["src/components/MenuScreen.jsx"],
            ["src/components/GameCanvas.jsx"],
        ],
        "fastapi": [
            ["requirements.txt", "main.py"],
            ["routers/api.py"],
            ["models/schemas.py"],
        ],
        "streamlit": [
            ["requirements.txt", "streamlit_app.py"],
            ["data/mock_data.py"],
        ],
        "node": [
            ["package.json", "index.js"],
            ["routes/api.js"],
        ],
        "expo": [
            ["src/app/_layout.tsx"],
            ["src/app/index.tsx"],
            ["src/components/Component.tsx"],
            ["src/lib/store.ts"],
        ],
        "unity": [
            ["Assets/_Project/Code/Core/Bootstrap/GameEntry.cs"],
            ["Assets/_Project/Code/Core/Save/SaveService.cs"],
            ["Assets/_Project/Code/Gameplay/GameLoop.cs"],
            ["Assets/_Project/UI/MainMenuController.cs"],
        ],
        "godot": [
            ["project.godot", "scenes/main.tscn"],
            ["scripts/player.gd"],
            ["scripts/autoload/game.gd"],
            ["scenes/ui/hud.tscn"],
        ],
    }
    pool = defaults.get(kind, [["index.html"]])
    idx = min(stage_index, len(pool) - 1)
    return list(pool[idx])


def enrich_plan(plan: dict[str, Any], kind: str, description: str = "") -> dict[str, Any]:
    """Post-process planner JSON: ensure files[] on every stage, normalize ids."""
    stages = plan.get("stages")
    if not isinstance(stages, list):
        return plan
    try:
        from .task_effort import classify
        dec = classify(description or str(plan.get("title") or ""))
        plan["effort"] = dec.get("tier")
    except Exception:
        dec = {}
    for i, stage in enumerate(stages):
        if not isinstance(stage, dict):
            continue
        stage.setdefault("id", i + 1)
        stage.setdefault("status", "pending")
        files = stage.get("files")
        if not files or not isinstance(files, list):
            stage["files"] = _default_files_for_kind(kind, i, stage.get("title", ""))
        elif len(files) > 3:
            # Planner ignored granularity — keep first 3, log via caller if needed
            stage["files"] = files[:3]
    if dec.get("world_kit"):
        have = " ".join(
            " ".join(str(p) for p in (s.get("files") or []))
            for s in stages if isinstance(s, dict)
        )
        if "world/bible.md" not in have:
            stages.insert(0, {
                "id": 0,
                "title": "World bible + map",
                "goal": "Campaign kit exists and the first region is named.",
                "instructions": "Write world/bible.md, world/map.json, world/quests.json, characters/*.dialogue.json, levels/graph.json. Prove with world({action: prove}). Do not invent the whole game loop here.",
                "uses_visuals": False,
                "files": ["world/bible.md", "world/map.json", "world/quests.json", "levels/graph.json"],
                "status": "pending",
            })
        for i, stage in enumerate(stages):
            if isinstance(stage, dict):
                stage["id"] = i + 1
    return plan


def min_stages_for_complexity(kind: str, *, is_game: bool, description: str) -> int:
    """Suggest minimum stage count when validating coarse plans."""
    try:
        from .task_effort import classify
        dec = classify(description)
        if dec.get("tier") == "epic":
            return 12
        if dec.get("tier") == "whisper":
            return 0
    except Exception:
        dec = {}
    try:
        from .arcade_craft import min_stages
        n = min_stages(kind, is_game=is_game, description=description)
        floor = int((dec or {}).get("min_stages") or 0)
        return max(n, floor) if floor else n
    except Exception:
        blob = (description or "").lower()
        complex_words = ("игра", "game", "3d", "multi", "dashboard", "auth", "arena", "battle")
        if is_game or any(w in blob for w in complex_words):
            return 8 if kind in ("expo", "vite-react", "vite", "unity", "godot") else 6
        if kind in ("fastapi", "streamlit", "node"):
            return 4
        return 3
