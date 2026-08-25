"""Game platform recommendations for Playground — hints, kind picker, autopilot injects.

Helps users who do not write full technical briefs: surfaces Vite/Expo/React/Static
guidance and concrete stack choices (Phaser, PixiJS, expo-av, etc.).
"""
from __future__ import annotations

import re
from typing import Any

# Canonical guide (also stored in skill catalog as skill:gamedev:platform-picker)
PLATFORM_PICKER_GUIDE = """
GAME PLATFORM PICKER — official Playground recommendations

1) STATIC (HTML/CSS/JS, no build)
   • Best for: simple puzzle, word, text games, quick prototypes.
   • Tech: single index.html + <canvas> or DOM; CDN libs (Pixi/Three) via <script>.

2) VITE (Vanilla JS + npm)
   • Best for: browser/web games needing HMR and bundling.
   • Pair with: Phaser 3 or PixiJS for high-performance 2D; matter-js physics; howler.js audio.
   • Game loop lives in canvas — NOT in React.

3) VITE-REACT (React 18 + Vite)
   • Best for: menus, inventory, HUD, meta-game UI around a canvas core.
   • CRITICAL: React Virtual DOM is too slow for hundreds of moving sprites.
   • Pattern: React for screens/HUD; gameplay in <canvas ref> + requestAnimationFrame (or Phaser/Pixi instance).
   • Libraries: phaser (npm), pixi.js, @react-three/fiber for 3D UI shell only.

4) EXPO (React Native)
   • Best for: iOS/Android mobile games (App Store / Play Store direction).
   • Use: View, Text, StyleSheet, Animated, expo-av (sound), expo-gl (3D).
   • GRAPHICS/GAMES: @shopify/react-native-skia — <Canvas>, <Circle>, <Path>, <Group>;
     useFrameCallback for 60fps game loop. This is an OFFICIAL Expo package.
   • NEVER: window, document, AudioContext, HTML canvas.getContext('2d'), vite.config.
   • NOTE: <Canvas> from Skia is a RN component (PascalCase), NOT HTML <canvas>.

5) GODOT 4 (GDScript, local Editor)
   • Best for: 2D Metroidvania / RPG / desktop games. Text .tscn + .gd — LLM-friendly.
   • Scaffold writes project.godot, scenes/main.tscn, scripts/player.gd.
   • Cloud does not run the Editor. Open the folder in Godot 4.3 locally.
   • Meshes: blender_jobs → GLB in assets/. Optional MCP: python -m daedalus.blender_mcp.

QUICK PICK:
• Browser arcade / 2D web → vite + Phaser or PixiJS
• Browser 3D / F-Zero-class → vite + Three.js WebGPU
• 2D desktop Metroidvania / Godot → godot
• Browser with rich menus → vite-react + canvas gameplay module
• Mobile touch game → expo
• Simple puzzle in one file → static
"""

_STAGING: dict[str, list[str]] = {
    "static": [
        "index.html — structure + <canvas id='game'>",
        "style.css — minimal UI",
        "game.js — rAF loop, input, FSM, score",
    ],
    "vite": [
        "package.json — three (3D / racer) OR phaser / pixi.js + vite",
        "src/main.js — boot WebGPURenderer / Phaser.Game / PIXI.Application",
        "src/scenes/GameScene.js — update/render loop (or src/track.js + src/VehicleFactory.js)",
        "src/scenes/MenuScene.js — title + start",
    ],
    "vite-react": [
        "src/App.jsx — phase FSM (MENU | PLAYING | GAME_OVER)",
        "src/components/GameCanvas.jsx — <canvas ref> + game loop (NOT useState per frame)",
        "src/components/HUD.jsx — score/lives (React)",
        "src/components/MenuScreen.jsx — start/settings",
    ],
    "expo": [
        "App.jsx + app.json + package.json + babel.config.js",
        "src/screens/GameScreen.jsx — touch input + game state",
        "src/components/HUD.jsx — View/Text overlays",
        "src/audio/sounds.js — expo-av Audio.Sound",
    ],
    "godot": [
        "project.godot — name, main scene, autoload Game, physics layers",
        "scenes/main.tscn — player + floor + pit",
        "scripts/player.gd — move/jump/fail",
        "scripts/autoload/game.gd — score/fail/reset",
    ],
}

_LIBRARIES: dict[str, list[str]] = {
    "static": ["Canvas 2D API", "PixiJS CDN (optional)", "Howler CDN (optional)"],
    "vite": ["phaser", "pixi.js", "three", "matter-js", "howler"],
    "vite-react": ["phaser + react", "pixi.js", "three / @react-three/fiber", "howler"],
    "expo": ["expo-av", "expo-haptics", "@shopify/react-native-skia (2D games)", "expo-gl (3D)"],
    "godot": ["GDScript", "NavigationAgent2D", "GPUParticles2D", "blender_jobs GLB"],
}

_UI_SHORT: dict[str, str] = {
    "static": "🧩 Прости пъзели/думи — един HTML файл, canvas или DOM. Без npm.",
    "vite": "🎮 Браузър игра — Vite + Phaser/PixiJS. Canvas loop, npm пакети, HMR.",
    "vite-react": "🖥️ Меню/HUD в React, gameplay в canvas. Не рендерирай sprites в JSX.",
    "expo": "📱 Мобилна игра — React Native. View/Text, expo-av. Без Web Audio/HTML.",
    "unity": "🎮 Unity C# / URP — локален Unity Hub, scaffold + Git.",
    "godot": "🎮 Godot 4 — .tscn/.gd, локален Editor. blender_jobs за GLB.",
    "auto": "🧠 Дедал избира: web → vite/vite-react, Godot 2D → godot, mobile → expo.",
}

_GAME_WORDS = re.compile(
    r"\b(игр[аи]|game|arcade|arena|арен|battle|битк|platformer|shooter|puzzle|пъзел|"
    r"rpg|snake|tetris|minecraft|boss|level|score|sprite|canvas|phaser|pixi|expo|"
    r"racing|racer|f-?zero|wipeout|hover|three\.?js|webgpu|"
    r"mobile|мобил|ios|android|hero|герой|enemy|враг)\b",
    re.I,
)

_MOBILE_WORDS = re.compile(
    r"\b(expo|react\s*native|ios|android|app\s*store|play\s*store|мобил|телефон|tablet)\b",
    re.I,
)

_WEB_ENGINE_WORDS = re.compile(r"\b(phaser|pixi|webgl|canvas|three\.?js|браузър|browser)\b", re.I)

_REACT_UI_WORDS = re.compile(
    r"\b(menu|меню|inventory|инвентар|hud|dashboard|react|компонент)\b", re.I,
)

_SIMPLE_WORDS = re.compile(
    r"\b(puzzle|пъзел|word|думи|text|текст|quiz|кръстослов|simple|прост)\b", re.I,
)


def is_likely_game(text: str) -> bool:
    return bool(_GAME_WORDS.search(text or ""))


_UNITY_WORDS = re.compile(
    r"\b(unity|unity3d|urp|scriptableobject|c#|csharp|monobehaviour|gameobject)\b",
    re.I,
)

_GODOT_WORDS = re.compile(
    r"\b(godot|gdscript|metroidvania|\.tscn|tscn)\b",
    re.I,
)

def suggest_kind_heuristic(name: str = "", description: str = "") -> tuple[str, str]:
    """Rule-based kind suggestion when user picks auto or for UI hints."""
    blob = f"{name} {description}".strip()
    if not blob:
        return "static", "По подразбиране — static прототип"

    if _UNITY_WORDS.search(blob):
        return "unity", "Unity C# / URP — отвори в Unity Hub"

    if _GODOT_WORDS.search(blob):
        from .game_factory import wants_godot_3d_slice
        if wants_godot_3d_slice(name, description):
            return "godot", "Godot 4 3D room — local Editor, headless probe"
        return "godot", "Godot 4 — текстови .tscn / GDScript, локален Editor"

    from .game_factory import wants_godot_3d_slice as _want3
    if _want3(name, description):
        return "godot", "Godot 4 3D puzzle room — не Vite/Three"

    if _MOBILE_WORDS.search(blob):
        if re.search(r"\b(3d|urp|scriptable|collection rpg|open world)\b", blob, re.I):
            return "unity", "Сериозна mobile/3D игра → Unity"
        return "expo", "Мобилна игра → Expo (React Native)"

    if _SIMPLE_WORDS.search(blob) and not _WEB_ENGINE_WORDS.search(blob):
        return "static", "Проста текст/пъзел игра → static без build"

    if _REACT_UI_WORDS.search(blob) and _WEB_ENGINE_WORDS.search(blob):
        return "vite-react", "React UI + canvas/WebGL gameplay → vite-react"

    if _WEB_ENGINE_WORDS.search(blob) or re.search(
        r"\b(2d|3d|arcade|shooter|platformer|racing|racer|f-?zero|wipeout|hover)\b", blob, re.I
    ):
        if re.search(r"\b(3d|webgpu|three|f-?zero|wipeout|racing|racer|hover)\b", blob, re.I):
            return "vite", "3D arcade / racer → Vite + Three.js (WebGPU/TSL като FM1)"
        return "vite", "Браузър игра → Vite + Phaser/Pixi/canvas"

    if is_likely_game(blob):
        if _REACT_UI_WORDS.search(blob) and not re.search(
            r"\b(arcade|arena|battle|битк|shooter|platformer|minecraft|sprite)\b", blob, re.I
        ):
            return "vite-react", "React меню/HUD + отделен canvas модул → vite-react"
        return "vite", "Браузър игра → Vite + Phaser/Pixi (не чист React div gameplay)"

    return "vite-react", "Интерактивно приложение → vite-react"


def resolve_kind(kind: str, name: str = "", description: str = "") -> tuple[str, str]:
    """Resolve effective kind + reason (auto → heuristic)."""
    k = (kind or "static").strip()
    if k == "auto":
        return suggest_kind_heuristic(name, description)
    reasons = {
        "static": "Избрана платформа: static",
        "vite": "Избрана платформа: Vite + npm",
        "vite-react": "Избрана платформа: Vite + React",
        "expo": "Избрана платформа: Expo / React Native",
        "unity": "Избрана платформа: Unity C# (локален Unity Hub)",
        "godot": "Избрана платформа: Godot 4 (локален Editor)",
        "fastapi": "Backend API",
        "streamlit": "Python data app",
        "node": "Node.js server",
    }
    return k, reasons.get(k, k)


def get_hints(kind: str = "auto", name: str = "", description: str = "") -> dict[str, Any]:
    """Hints for Playground UI and APIs."""
    effective, reason = resolve_kind(kind, name, description)
    game = is_likely_game(f"{name} {description}")
    arch = ""
    if effective == "vite-react" and game:
        arch = (
            "Раздели: GameCanvas.jsx (canvas + loop) + HUD/Menu като React компоненти. "
            "Game state в useRef, не useState на всеки frame."
        )
    elif effective == "vite" and game:
        if re.search(r"\b(3d|webgpu|three|racing|racer|f-?zero)\b", f"{name} {description}", re.I):
            arch = "Three.js WebGPURenderer + TSL materials — game loop in rAF, not React. Spline track + VehicleFactory."
        else:
            arch = "Phaser.Scene или Pixi Application — централен game loop, не React."
    elif effective == "expo" and game:
        arch = "GameScreen с View layout; звук с expo-av; без AudioContext/canvas DOM."

    return {
        "kind": kind,
        "effective_kind": effective,
        "reason": reason,
        "is_game": game,
        "ui_short": _UI_SHORT.get(effective if kind != "auto" else "auto", _UI_SHORT["auto"]),
        "ui_detail": _UI_SHORT.get(effective, ""),
        "libraries": _LIBRARIES.get(effective, []) if game else [],
        "staging": _STAGING.get(effective, []) if game else [],
        "architecture": arch,
        "guide_excerpt": PLATFORM_PICKER_GUIDE.strip()[:1200],
    }


def brief_enrichment(kind: str, name: str = "", criteria: str = "") -> str:
    """Extra paragraphs for ai-gen-desc when project looks like a game."""
    if not is_likely_game(f"{name} {criteria}"):
        return ""
    effective, reason = resolve_kind(kind, name, criteria)
    hints = get_hints(kind, name, criteria)
    libs = ", ".join(hints.get("libraries") or []) or "подходящи за платформата"
    staging = "\n".join(f"  - {s}" for s in (hints.get("staging") or [])[:6])
    arch = hints.get("architecture") or ""
    body = (
        f"\n\n━━━ GAME PLATFORM GUIDE (автоматично — {reason}) ━━━\n"
        f"Ефективна платформа: {effective}\n"
        f"Препоръчани библиотеки: {libs}\n"
        f"Архитектура: {arch}\n"
        f"Предложена файлова структура:\n{staging}\n"
        f"Спазвай PLATFORM_PICKER правилата — не смесвай web APIs в Expo.\n"
    )
    try:
        from .design_arena import expand_brief
        arena = expand_brief(f"{name} {criteria}".strip(), kind=effective)
        extra = arena.get("prompt_block") or ""
        if extra:
            return body + extra
    except Exception:
        pass
    return body


def autopilot_research_addendum(kind: str, *, slug: str = "") -> str:
    from .playground_agent_guards import outbox_guardrails_block

    base = outbox_guardrails_block(slug=slug, kind=kind)
    if kind == "expo":
        return base + (
            "• EXPO ИГРА: търси expo-av, React Native Animated, View/Text HUD. "
            "NEED_SKILL_CATALOG: expo game OR platform picker. "
            "НЕ ползвай web gamedev skills (Canvas, AudioContext).\n"
        )
    if kind in ("vite", "vite-react", "static"):
        return base + (
            "• WEB ИГРА: 2D → Phaser/PixiJS/canvas; 3D/racing/F-Zero → Three.js WebGPU "
            "(src/racer/) — НЕ Phaser. "
            "NEED_SKILL_CATALOG: arcade craft OR three-webgpu-tsl OR game platform picker.\n"
        )
    return base


def game_review_system_addendum(kind: str) -> str:
    """Extra rules for LLM code reviewer on game projects."""
    if kind not in ("static", "vite", "vite-react", "expo", "unity", "godot"):
        return ""
    if kind == "godot":
        return (
            "\nGODOT 4 — ПРАВИЛА ЗА REVIEWER:\n"
            "- extends CharacterBody2D / Node2D / Control — ОДОБРЕНО\n"
            "- [gd_scene format=3] .tscn — ОДОБРЕНО\n"
            "- Autoload Game за score/fail — ОДОБРЕНО\n"
            "- signal.connect(callable) (Godot 4) — ОДОБРЕНО\n"
            "- ЗАБРАНЕНО: Phaser, React, HTML gameplay, Unity C# в този kind\n"
            "- Файлът НЕ се изпълнява в browser sandbox — Godot Hub локално\n"
        )
    if kind == "unity":
        return (
            "\nUNITY (C# + URP) — ПРАВИЛА ЗА REVIEWER:\n"
            "- C# класове с MonoBehaviour, ScriptableObject, Rigidbody, Collider, Animator — ОДОБРЕНО\n"
            "- using UnityEngine; using UnityEngine.UI; using System.Collections; — ОДОБРЕНО\n"
            "- [SerializeField], [CreateAssetMenu], [RequireComponent] атрибути — ОДОБРЕНО\n"
            "- Services pattern (GameEntry.cs, SaveService.cs, EventBus.cs) — ОДОБРЕНО\n"
            "- partial class, abstract class, interface в отделни файлове — ОДОБРЕНО\n"
            "- Assets/_Project/Code/ структура — ОЧАКВАНА\n"
            "- ScriptableObject data files (*.cs wrapper + JSON registry) — ОДОБРЕНО\n"
            "- НЕ отхвърляй Unity API само защото не е web/React\n"
            "- ЗАБРАНЕНО: React/JSX, npm package.json main entry, Expo, HTML gameplay, Python\n"
            "- Файлът НЕ се изпълнява в browser — само се генерира и push-ва към Unity Hub\n"
        )
    if kind == "expo":
        return (
            "\nИГРА НА EXPO (React Native) — ВАЖНО ЗА REVIEWER:\n"
            "- @shopify/react-native-skia е ОФИЦИАЛЕН Expo пакет — ОДОБРЕНО и ПРЕПОРЪЧИТЕЛНО за 2D игри\n"
            "- import { Canvas, Circle, Path, Group, Rect } from '@shopify/react-native-skia' — ОДОБРЕНО\n"
            "- import { useFrameCallback } from '@shopify/react-native-skia' — ОДОБРЕНО за game loop\n"
            "- <Canvas style={{ flex:1 }}> е React Native компонент (НЕ HTML <canvas>) — ОДОБРЕНО\n"
            "- <Circle>, <Path>, <Group>, <Rect> вътре в <Canvas> — ОДОБРЕНО (Skia JSX, не HTML/SVG)\n"
            "- useFrameCallback(() => { ... }) замества requestAnimationFrame — ОДОБРЕНО\n"
            "- НЕ отхвърляй код само защото съдържа 'Canvas' или '@shopify/react-native-skia'\n"
            "- ЗАБРАНЕНО: window, document, canvas.getContext('2d'), react-native-reanimated, gesture-handler\n"
            "- ЗАБРАНЕНО: requestAnimationFrame директно (без Skia wrapper)\n"
            "- ЗАБРАНЕНО: HTML lowercase тагове — <div>, <span>, <canvas> (lowercase), <img>, <button>\n"
            "- РАЗРЕШЕНО: View, Text, Pressable, Image, StyleSheet + Skia Canvas компоненти\n"
            "- PanResponder за touch/drag — НЕ gesture-handler\n"
            "- expo-av за звук — НЕ AudioContext\n"
        )
    if kind == "vite":
        return (
            "\nИГРА НА VITE — 2D: Phaser 3 е валиден. 3D/racing: Three.js WebGPU/WebGL е ПРЕПОРЪЧАНИЯТ път:\n"
            "- import * as THREE from 'three', WebGLRenderer / WebGPURenderer — ОДОБРЕНО\n"
            "- CatmullRomCurve3, TubeGeometry, InstancedMesh, MeshStandardMaterial — ОДОБРЕНО\n"
            "- src/racer/track.js + vehicle.js (VehicleFactory) — ОДОБРЕНО\n"
            "- import Phaser from 'phaser', class X extends Phaser.Scene — ОДОБРЕНО за 2D\n"
            "- this.load.image / this.add.image / preloadImages от assetLoader.js — ОДОБРЕНО за 2D\n"
            "- Entity файлове (Projectile.js, Player.js) НЕ трябва да съдържат load.image — OK\n"
            "- НЕ отхвърляй Three.js или Phaser само защото платформата е 'vite'\n"
            "- ЗАБРАНЕНО за 3D brief: pixel-art default, Phaser boot, LLM vertex-dump hero mesh\n"
        )
    if kind == "vite-react":
        return (
            "\nИГРА НА VITE-REACT:\n"
            "- React за меню/HUD; gameplay в canvas или Phaser instance — ОДОБРЕНО\n"
            "- <img src=\"assets/...\"> или Phaser/canvas drawImage за спрайтове — ОДОБРЕНО\n"
            "- import Phaser from 'phaser' в GameCanvas.jsx — ОДОБРЕНО\n"
        )
    return (
        "\nSTATIC ИГРА: canvas drawImage или <img src=\"assets/...\"> за спрайтове.\n"
    )


def autopilot_plan_addendum(kind: str) -> str:
    """Append to planner system prompt for game projects."""
    if kind not in ("static", "vite", "vite-react", "expo", "godot"):
        return ""
    hints = get_hints(kind, description="game")
    staging = "\n".join(f"  • {s}" for s in (hints.get("staging") or []))
    libs = ", ".join(hints.get("libraries") or [])
    return (
        f"\n━━━ GAME BUILD GUIDE ({kind}) ━━━\n"
        f"Библиотеки: {libs}\n"
        f"{hints.get('architecture') or ''}\n"
        f"Файлова структура:\n{staging}\n"
        "ВИЗУАЛИ: поне един етап с uses_visuals=true (герой, враг, фон). "
        "Build кодът ТРЯБВА да реферира assets/*.png — без CSS gradient placeholders.\n"
    )


def kind_detect_system_prompt() -> str:
    """Shared kind-detection system prompt with game platform rules."""
    return (
        "Ти си platform architect. Анализираш описание и избираш НАЙ-ПОДХОДЯЩАТА платформа.\n\n"
        + PLATFORM_PICKER_GUIDE
        + "\n\nОТГОВОРИ САМО С ВАЛИДЕН JSON:\n"
        '{"kind":"<static|vite|vite-react|fastapi|streamlit|node|expo|unity|godot>","reason":"<1 изречение BG>"}'
    )
