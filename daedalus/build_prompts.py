"""Playground/Game Studio system prompts — usable without ADN admin.py.

When hosted by ArgosDataNexus, delegates to ``admin._build_combined_sys`` so
ADN keeps a single prompt source. Standalone Docker uses the copy below.
"""
from __future__ import annotations

from typing import Any

_DIRECT_PROVIDERS = frozenset({
    "novita", "openrouter", "alltoken", "poolside", "nvidia",
    "openai", "anthropic", "groq", "gemini",
})

_AI_BUILD_SYS_BASE = """\
Ти си ЕКСПЕРТЕН full-stack/game developer. Строиш РЕАЛНИ, ПЪЛНИ, РАБОТЕЩИ приложения и игри.

ТВЪРДИ ПРАВИЛА:
1. Пиши ПЪЛЕН работещ код. НИКОГА "// TODO", "...rest of code...", "// добави тук", "[Placeholder for...]".
2. За игра: цял game loop, rendering, input, collision, score, levels, restart, audio feedback — ВСИЧКО в детайл.
3. Богат, завършен резултат — не demo/skeleton. Изиграваема, полирана игра от първия път с реален gameplay.
4. За 3D: пълна сцена (камера, светлини, материали, сенки), анимиран render loop, физика/движение, particle ефекти където пасва.
5. НЕ пиши вътрешни разсъждения. Започни директно с ===FILE===.
6. СПАЗВАЙ ПЛАТФОРМАТА от [ПЛАТФОРМА: ...] блока — web API са ЗАБРАНЕНИ в Expo/React Native.

ФОРМАТ НА ОТГОВОРА (ЗАДЪЛЖИТЕЛЕН — нищо извън блоковете):
За всеки файл:
===FILE: relative/path===
<пълно съдържание на файла>
===END===
"""

_WEB_GAME_ARCH_RULES = """\
GAME ARCHITECTURE (WEB — static / vite / vite-react САМО):
▸ GAME LOOP: requestAnimationFrame + delta-time.
▸ FSM: MENU→PLAYING→PAUSED→GAME_OVER→WIN
▸ INPUT: Set-based keyboard + touch normalized to canvas coords.
▸ AUDIO: Web Audio API или Howler.js — НЕ ползвай в Expo.
▸ HUD: canvas overlay или React компонент — според платформата.
▸ REACT GAMES (vite-react): game state в useRef; score/phase в useState за HUD.
▸ СПРАЙТОВЕ: <img src="/assets/player.png"> или canvas drawImage — ЗАБРАНЕНО div+linear-gradient placeholders.
▸ Ако assets/*.png съществуват в проекта — ЗАДЪЛЖИТЕЛНО ги реферирай в кода.
"""

_EXPO_GAME_ARCH_RULES = """\
GAME ARCHITECTURE (EXPO / React Native САМО):
▸ КРИТИЧНО: НИКОГА window, document, AudioContext, requestAnimationFrame, canvas.getContext, localStorage, HTML тагове.
▸ GAME LOOP: Animated.loop / setInterval + setState — НЕ requestAnimationFrame.
▸ INPUT: Pressable / TouchableOpacity + PanResponder от react-native.
▸ AUDIO: expo-av + expo-haptics — не Web Audio.
▸ HUD: View + Text + StyleSheet с position absolute.
▸ СПРАЙТОВЕ: <Image source={require('../assets/hero.png')} /> — assets/*.png.
"""

_KIND_RULES: dict[str, str] = {
    "static": """\
[ПЛАТФОРМА: STATIC (HTML/CSS/JS)]
• Прост проект: index.html с inline <style> и <script>.
• Сложен проект: index.html + style.css + script.js — всеки файл синтактично завършен.
• CDN библиотеки (Three.js, GSAP, Phaser, Howler) са OK чрез <script src="https://cdn...">
• НИКОГА ES import/export без bundler.
""",
    "vite": """\
[ПЛАТФОРМА: VITE (Vanilla JS + npm)]
• Задължителни файлове: index.html, main.js, vite.config.js с base:'./'
• index.html: <script type="module" src="/main.js"></script>
• ЗА ИГРИ 2D: Phaser 3 (npm phaser) + src/game/; спрайтове от assets/*.png.
• ЗА ИГРИ 3D / racing / F-Zero: Three.js (WebGPURenderer с WebGL fallback) + src/racer/. НЕ Phaser. НЕ pixel-art default. Запази M0 scaffold (ship/track/fail/boost) и надграждай.
""",
    "vite-react": """\
[ПЛАТФОРМА: VITE + REACT 18]
• Задължителни: index.html, src/main.jsx, src/App.jsx, vite.config.js с @vitejs/plugin-react
• React от npm — НИКОГА CDN React.
""",
    "fastapi": """\
[ПЛАТФОРМА: FASTAPI]
• main.py с `app = FastAPI()`, requirements.txt с fastapi и uvicorn[standard].
""",
    "streamlit": """\
[ПЛАТФОРМА: STREAMLIT]
• streamlit_app.py с import streamlit as st, requirements.txt със streamlit.
""",
    "node": """\
[ПЛАТФОРМА: NODE.JS]
• index.js + package.json с "scripts": {"start": "node index.js"}
""",
    "expo": "",
    "unity": "",
    "godot": "",
}

_MODE_APPLIES_TO = {"static", "vite", "vite-react", "node", "fastapi"}

_CODING_MODE_CRITERIA: dict[str, str] = {
    "lite": "[РЕЖИМ: БЪРЗО] прост, бързо зареждащ се код без излишни зависимости.\n",
    "pro": "[РЕЖИМ: БАЛАНСИРАНО] полиран UX, responsive, error/loading states.\n",
    "premium": (
        "[РЕЖИМ: ПРЕМИУМ] максимално визуално качество. ЗА ИГРИ: assets/*.png "
        "за герои/врагове/фон — НЕ CSS gradient квадрати.\n"
    ),
}


def _adn_admin() -> Any | None:
    from .host import adn_available, standalone_mode
    if standalone_mode() or not adn_available():
        return None
    try:
        from argos_data_nexus import admin
        return admin
    except Exception:
        return None


def build_combined_sys(kind: str, coding_mode: str) -> str:
    """Kind-aware + mode-aware builder system prompt."""
    admin = _adn_admin()
    if admin is not None:
        fn = getattr(admin, "_build_combined_sys", None)
        if fn:
            return fn(kind, coding_mode)
    kind_block = _KIND_RULES.get(kind, _KIND_RULES["static"])
    if kind == "expo":
        from .expo_platform_guide import expo_build_rules
        kind_block = expo_build_rules()
        game_arch = _EXPO_GAME_ARCH_RULES
    elif kind == "unity":
        from .unity_platform_guide import UNITY_PLAN_ADDENDUM, kind_rules_block
        kind_block = kind_rules_block()
        game_arch = UNITY_PLAN_ADDENDUM
    elif kind == "godot":
        from .godot_platform_guide import GODOT_PLAN_ADDENDUM, kind_rules_block
        kind_block = kind_rules_block()
        game_arch = GODOT_PLAN_ADDENDUM
    elif kind in ("static", "vite", "vite-react"):
        game_arch = _WEB_GAME_ARCH_RULES
    else:
        game_arch = ""
    mode_block = ""
    if kind in _MODE_APPLIES_TO:
        mode_block = _CODING_MODE_CRITERIA.get(coding_mode, "")
    parts = [_AI_BUILD_SYS_BASE, game_arch, kind_block]
    if mode_block:
        parts.append(mode_block)
    return "\n".join(p for p in parts if p)


def resolve_build_model(prompt: str = "") -> tuple[str | None, str]:
    admin = _adn_admin()
    if admin is not None:
        fn = getattr(admin, "_resolve_build_model", None)
        if fn:
            return fn(prompt)
    from .llm_config import get_config, is_nvidia_free_only
    from . import model_registry as reg

    try:
        if is_nvidia_free_only():
            cfg = get_config()
            prov, mdl = cfg.get("provider", ""), cfg.get("model", "")
            if mdl and prov == "nvidia":
                return f"nvidia:{mdl}", f"NVIDIA free: {mdl}"
        if reg.get_auto_mode():
            best = reg.select_for_task(prompt, budget_hint="balanced", required_caps=["code"])
            if best and best.get("provider") in _DIRECT_PROVIDERS:
                return (
                    f"{best['provider']}:{best['model_id']}",
                    f"Auto: {best.get('display_name', best['model_id'])}",
                )
            return None, "Auto: code routing"
        cfg = get_config()
        prov, mdl = cfg.get("provider", ""), cfg.get("model", "")
        if mdl and (prov in _DIRECT_PROVIDERS or "/" in mdl):
            ov = f"{prov}:{mdl}" if prov in _DIRECT_PROVIDERS else mdl
            return ov, f"Пресет: {mdl}"
    except Exception:
        pass
    return None, "Default (code routing)"


def resolve_build_model_for_project(
    prompt: str = "",
    *,
    is_game: bool = False,
    coding_mode: str = "lite",
) -> tuple[str | None, str]:
    admin = _adn_admin()
    if admin is not None:
        fn = getattr(admin, "_resolve_build_model_for_project", None)
        if fn:
            return fn(prompt, is_game=is_game, coding_mode=coding_mode)
    model, label = resolve_build_model(prompt)
    if not is_game:
        return model, label
    from . import model_registry as reg
    try:
        task = prompt or "browser game phaser canvas sprites assets"
        best = reg.select_for_task(task, budget_hint="quality", required_caps=["code"])
        if best and best.get("provider") and best.get("model_id"):
            prov = best["provider"]
            if prov in _DIRECT_PROVIDERS:
                ov = f"{prov}:{best['model_id']}"
                return ov, f"Game: {best.get('display_name', best['model_id'])}"
    except Exception:
        pass
    if model:
        return model, f"Game: {label}"
    return "alltoken:deepseek-v3.2", "Game: DeepSeek V3.2 (fallback)"
