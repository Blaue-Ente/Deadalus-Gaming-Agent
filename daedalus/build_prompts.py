"""Playground/Game Studio system prompts — usable without ADN admin.py.

When hosted by ArgosDataNexus, delegates to ``admin._build_combined_sys`` so
ADN keeps a single prompt source. Standalone Docker uses the copy below.
"""
from __future__ import annotations

import os
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
    try:
        from .game_engineer import prompt_block
        # Kind alone is not enough for vite/static apps; "игра" marks Studio game builds.
        blob = "игра" if kind in ("godot", "unity", "expo") else ""
        eng = prompt_block(blob, kind=kind)
        if eng:
            parts.append(eng)
    except Exception:
        pass
    return "\n".join(p for p in parts if p)


def _provider_can_call_cloud(provider: str) -> bool:
    """True when this process has an API key for a billable / remote provider."""
    from .llm_config import is_loopback_gateway, resolve_provider_api_key
    from .local_llm import is_local_provider

    p = (provider or "").strip().lower()
    if not p or is_local_provider(p) or is_loopback_gateway(p):
        return True
    return bool(resolve_provider_api_key(p))


def _tag_from_get_config() -> tuple[str | None, str]:
    """Use Connectors/wardrobe brain (no whisper/epic/run-budget overlay)."""
    from .llm_config import get_brain_config
    from .local_llm import is_local_provider

    try:
        cfg = get_brain_config()
    except Exception:
        return None, ""
    prov = str(cfg.get("provider") or "").strip().lower()
    mdl = str(cfg.get("model") or "").strip()
    if not mdl:
        return None, ""
    if is_local_provider(prov):
        return f"{prov}:{mdl}", f"effective LLM: {prov}/{mdl}"
    if _provider_can_call_cloud(prov):
        return f"{prov}:{mdl}", f"effective LLM: {prov}/{mdl}"
    return None, ""


def _wardrobe_local_build_tag() -> tuple[str | None, str]:
    """When the wardrobe is in local mode, pin the build tag to the active Ollama model."""
    from .llm_wardrobe import load, wants_local
    from .local_llm import LOCAL_PROVIDERS, active_ollama_model, is_local_provider, normalize_provider

    if not wants_local():
        return None, ""
    data = load()
    backend = normalize_provider(str(data.get("backend") or "auto"))
    if backend == "cloud":
        return None, ""
    model = (active_ollama_model() or "").strip()
    if not model:
        return None, ""
    prov = backend if backend in LOCAL_PROVIDERS and backend not in ("", "auto") else "ollama"
    if not is_local_provider(prov):
        prov = "ollama"
    return f"{prov}:{model}", f"Game: wardrobe local ({model})"


def _daedalus_build_model_env_tag() -> tuple[str | None, str]:
    """Parse ``DAEDALUS_BUILD_MODEL`` (optional ``provider:model`` or bare Ollama id)."""
    from .llm_config import PROVIDER_DEFAULTS
    from .local_llm import (
        LOCAL_PROVIDERS,
        _looks_cloud_model_id,
        is_local_provider,
        normalize_provider,
    )

    raw = (os.environ.get("DAEDALUS_BUILD_MODEL") or "").strip()
    if not raw:
        return None, ""
    low = raw.lower()
    # Longest provider prefix wins (e.g. poolside before po).
    names = sorted(
        {*PROVIDER_DEFAULTS.keys(), *[p for p in LOCAL_PROVIDERS if p]},
        key=len,
        reverse=True,
    )
    for name in names:
        prefix = name + ":"
        if low.startswith(prefix):
            prov = normalize_provider(name)
            rest = raw[len(prefix) :].strip()
            if not rest:
                return None, ""
            if is_local_provider(prov):
                return f"{prov}:{rest}", f"DAEDALUS_BUILD_MODEL: {prov}:{rest}"
            if _provider_can_call_cloud(prov):
                return f"{prov}:{rest}", f"DAEDALUS_BUILD_MODEL: {prov}:{rest}"
            return None, ""
    # Bare model id — local tag (e.g. qwen2.5-coder:7b) defaults to Ollama; cloud-shaped
    # ids bind to DAEDALUS_BUILD_PROVIDER / wardrobe / effective cloud when a key exists.
    explicit = (os.environ.get("DAEDALUS_BUILD_PROVIDER") or "").strip().lower()
    if explicit:
        prov = normalize_provider(explicit)
        if is_local_provider(prov):
            return f"{prov}:{raw}", f"DAEDALUS_BUILD_MODEL: {prov}:{raw}"
        if _provider_can_call_cloud(prov):
            return f"{prov}:{raw}", f"DAEDALUS_BUILD_MODEL: {prov}:{raw}"
        return None, ""
    if not _looks_cloud_model_id(raw):
        return f"ollama:{raw}", f"DAEDALUS_BUILD_MODEL: ollama:{raw}"
    w_tag, w_label = _wardrobe_local_build_tag()
    if w_tag:
        prov_m, _, model_m = w_tag.partition(":")
        if model_m:
            return f"{prov_m}:{raw}", f"DAEDALUS_BUILD_MODEL (wardrobe {prov_m}): {raw}"
    gc_tag, _ = _tag_from_get_config()
    if gc_tag:
        prov_g, _, _ = gc_tag.partition(":")
        if prov_g and not is_local_provider(prov_g) and _provider_can_call_cloud(prov_g):
            return f"{prov_g}:{raw}", f"DAEDALUS_BUILD_MODEL: {prov_g}:{raw}"
    return None, ""


def _local_ollama_fallback_tag() -> tuple[str, str]:
    from .local_llm import active_ollama_model

    m = (active_ollama_model() or "qwen2.5-coder:7b").strip()
    return f"ollama:{m}", f"Game: local Ollama fallback ({m})"


def fallback_build_model_tag() -> tuple[str, str]:
    """Repair/build last resort when ``resolve_*`` returns empty — mirrors brain, not a hardcoded cloud id."""
    from .llm_config import get_brain_config

    try:
        cfg = get_brain_config()
    except Exception as exc:
        from .llm_config import OPENROUTER_DEFAULT_MODEL
        return (
            f"openrouter:{OPENROUTER_DEFAULT_MODEL}",
            f"fallback (no brain): {str(exc)[:120]}",
        )
    prov = str(cfg.get("provider") or "").strip().lower()
    mdl = str(cfg.get("model") or "").strip()
    if not mdl:
        from .llm_config import OPENROUTER_DEFAULT_MODEL
        return (
            f"openrouter:{OPENROUTER_DEFAULT_MODEL}",
            "fallback: empty model in brain config",
        )
    if prov in _DIRECT_PROVIDERS:
        return f"{prov}:{mdl}", f"fallback: brain {prov}/{mdl}"
    if "/" in mdl:
        return mdl, f"fallback: brain {mdl}"
    return (
        "openrouter:anthropic/claude-sonnet-4-5",
        f"fallback: non-gateway brain ({prov})",
    )


def _first_registry_cloud_with_key(task: str, *, budget_hint: str) -> tuple[str | None, str]:
    from . import model_registry as reg
    from .llm_config import is_loopback_gateway
    from .local_llm import is_local_provider

    for entry in reg.iter_candidates_for_task(
        task, budget_hint=budget_hint, required_caps=["code"],
    ):
        prov = str(entry.get("provider") or "").strip().lower()
        mid = str(entry.get("model_id") or "").strip()
        if not prov or not mid:
            continue
        if is_local_provider(prov) or is_loopback_gateway(prov):
            continue
        if not _provider_can_call_cloud(prov):
            continue
        return (
            f"{prov}:{mid}",
            f"Game: {entry.get('display_name', mid)}",
        )
    return None, ""


def resolve_build_model(prompt: str = "") -> tuple[str | None, str]:
    admin = _adn_admin()
    if admin is not None:
        fn = getattr(admin, "_resolve_build_model", None)
        if fn:
            return fn(prompt)
    from .llm_config import get_brain_config, is_nvidia_free_only
    from . import model_registry as reg

    try:
        if is_nvidia_free_only():
            cfg = get_brain_config()
            prov, mdl = cfg.get("provider", ""), cfg.get("model", "")
            if mdl and prov == "nvidia":
                return f"nvidia:{mdl}", f"NVIDIA free: {mdl}"
        if reg.get_auto_mode():
            best = reg.select_for_task(prompt, budget_hint="balanced", required_caps=["code"])
            if best and best.get("provider") in _DIRECT_PROVIDERS:
                prov = str(best.get("provider") or "").strip().lower()
                if _provider_can_call_cloud(prov):
                    return (
                        f"{best['provider']}:{best['model_id']}",
                        f"Auto: {best.get('display_name', best['model_id'])}",
                    )
            return None, "Auto: code routing"
        cfg = get_brain_config()
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
    if not is_game:
        return resolve_build_model(prompt)

    task = prompt or "browser game phaser canvas sprites assets"

    # 1. Explicit process override (highest priority)
    try:
        env_tag, env_label = _daedalus_build_model_env_tag()
        if env_tag:
            return env_tag, env_label
    except Exception:
        pass

    # 2. NVIDIA free-only policy — never auto-route to Poolside or other clouds
    try:
        from .llm_config import get_brain_config, is_nvidia_free_only

        if is_nvidia_free_only():
            cfg = get_brain_config()
            prov, mdl = str(cfg.get("provider") or ""), str(cfg.get("model") or "")
            if prov == "nvidia" and mdl:
                return f"nvidia:{mdl}", f"Game: NVIDIA free — {mdl}"
    except Exception:
        pass

    # 3. Local wardrobe (owner picked Ollama in Studio — beats silent cloud defaults)
    try:
        w_tag, w_label = _wardrobe_local_build_tag()
        if w_tag:
            return w_tag, w_label
    except Exception:
        pass

    # 4. Effective LLM from DB/env — the model that already has credentials (Connectors)
    try:
        gc_tag, gc_label = _tag_from_get_config()
        if gc_tag:
            return gc_tag, f"Game: {gc_label}"
    except Exception:
        pass

    from . import model_registry as reg

    # 5. Auto registry: only providers with keys (skip Poolside when POOLSIDE_API is unset)
    try:
        if reg.get_auto_mode():
            cloud_tag, cloud_label = _first_registry_cloud_with_key(task, budget_hint="quality")
            if cloud_tag:
                return cloud_tag, cloud_label
            loc_tag, loc_label = _local_ollama_fallback_tag()
            return loc_tag, loc_label + " (no cloud API keys — local Ollama)"
    except Exception:
        pass

    # 6. Non-auto: curated pick from registry when the provider is callable
    try:
        best = reg.select_for_task(task, budget_hint="quality", required_caps=["code"])
        if best and best.get("provider") and best.get("model_id"):
            prov = str(best.get("provider") or "").strip().lower()
            mid = str(best.get("model_id") or "").strip()
            if prov in _DIRECT_PROVIDERS and mid and _provider_can_call_cloud(prov):
                return (
                    f"{prov}:{mid}",
                    f"Game: {best.get('display_name', mid)}",
                )
    except Exception:
        pass

    # 7. Same rules as generic resolve_build_model (balanced/auto path)
    model, label = resolve_build_model(prompt)
    if model and ":" in model:
        _prov, _mid = model.split(":", 1)
        if _mid.strip() and _provider_can_call_cloud(_prov.strip().lower()):
            return model, f"Game: {label}"

    loc_tag, loc_label = _local_ollama_fallback_tag()
    return loc_tag, loc_label + " (fallback)"
