"""Arcade-craft doctrine — how Daedalus builds games at FM1 / F-Zero fidelity.

Distilled from:
- r/ClaudeCode case (ActionLittle4176, ~4 months, no-coder, browser racer)
- Live dissection of Formula Minus One (https://fm1.moises.cloud, v0.8)

This is executable policy, not a blog post. Planner, GDD, playtest, critic,
and gates import it. No LLM, no network.
"""
from __future__ import annotations

import re
from typing import Any

FIELD_SOURCE = "formula-minus-one"
FIELD_URL = "https://fm1.moises.cloud/"
FIELD_VERSION = "v0.8"

# What FM1 actually is — verified from live HTML + hashed Vite chunks.
FM1_STACK: dict[str, str] = {
    "product": "Formula Minus One — futuristic hover racer in the browser",
    "build": "Vite (hashed ES modules, PWA, landscape-only mobile)",
    "renderer": "Three.js WebGPURenderer + TSL (Three.js Shading Language)",
    "fallback": "Custom RendererBackend; WebGPU device + StandardNodeLibrary",
    "vehicles": "VehicleFactory with per-pilot handling profiles (not one generic ship)",
    "tracks": "Named levels (Mist City, Aqua, Aaru Gate, Órbita) + story cups",
    "environment": "Procedural buildings / pyramids; FFT ocean (Poseidon TSL + FFT-Ocean)",
    "audio": "Web Audio (OscillatorNode engine/wind); no Howler in the bundle",
    "physics": "Custom arcade (no Rapier/cannon/Ammo in the shipped chunks)",
    "input": "Keyboard, gamepad, touch stick, device tilt, phone-as-pad via WebRTC",
    "modes": "Championship, story cups, time trial, elimination, 1v1, daily challenge, ghost, replay",
    "feel": "Turbo, Overboost, slipstream, barrel roll, airbrakes, integrity/fail, last-lap ceremony",
}

_FIDELITY_RE = re.compile(
    r"\b(f-?zero|wipeout|star wars racer|formula minus|fm1|"
    r"futuristic racer|hover|anti-?grav|wipe-out|"
    r"polished|cinematic|ultra|pbr|volumetric|"
    r"three\.?js|webgpu|tsl)\b",
    re.I,
)
_RACER_RE = re.compile(
    r"\b(racer|racing|race|circuit|lap|track|grand prix|"
    r"състезан|писта|обиколк|hover|f-?zero|wipeout)\b",
    re.I,
)
# Tight: used to pick the Three.js racer scaffold. Do not match "track tasks".
_RACER_SCAFFOLD_RE = re.compile(
    r"\b(racer|racing|f-?zero|fzero|wipeout|anti-?grav|"
    r"hover\s+(racer|race|ship|craft)|formula minus|"
    r"grand prix|състезан|писта|обиколк)\b",
    re.I,
)
_PIXEL_TRAP_RE = re.compile(r"\b(pixel art|8-?bit|low.?fi template|css gradient hero)\b", re.I)
_MESH_FROM_LLM_RE = re.compile(
    r"\b(write (a )?(gltf|obj) by hand|hardcoded vertices for (the )?(ship|car|hero))\b",
    re.I,
)


def blob_of(*parts: Any) -> str:
    return " ".join(str(p or "") for p in parts)


def wants_high_fidelity(text: str) -> bool:
    return bool(_FIDELITY_RE.search(text or ""))


def looks_like_racer(text: str) -> bool:
    return bool(_RACER_RE.search(text or ""))


def wants_racer_scaffold(name: str = "", description: str = "") -> bool:
    """True when a new Vite project should boot the Three.js M0 slice, not Phaser.

    Tighter than ``looks_like_racer`` so 'track your tasks' stays a vanilla Vite app.
    """
    return bool(_RACER_SCAFFOLD_RE.search(blob_of(name, description)))


def looks_like_campaign_bible(text: str) -> bool:
    """True for Shadow-Veins-class GDDs: biomes, chapters, console SKUs before M0 is fun.

    A dumped sample GDD (3D Action RPG, 5 biomes, 12 chapters, PS5/Xbox, FMOD)
    is a campaign bible — not an M0 template.
    """
    low = (text or "").lower()
    hits = 0
    if re.search(r"\b\d+\s*biomes?\b", low):
        hits += 1
    if re.search(r"\b\d+\s*chapters?\b", low):
        hits += 1
    if re.search(r"\b\d+\s*magic schools?\b", low):
        hits += 1
    if re.search(r"\b\d+\+?\s*side quests?\b", low):
        hits += 1
    if re.search(r"\b(ps5|playstation 5|xbox series|dedicated servers?)\b", low):
        hits += 1
    if re.search(r"\b(fmod|wwise)\b", low):
        hits += 1
    if re.search(r"\b(dark souls|elden ring|dishonored)\b", low):
        hits += 1
    return hits >= 2


def fidelity_traps(text: str) -> list[str]:
    """LLM defaults that kill arcade-grade games."""
    blob = text or ""
    traps: list[str] = []
    if wants_high_fidelity(blob) or looks_like_racer(blob):
        if _PIXEL_TRAP_RE.search(blob):
            traps.append(
                "Pixel-art / CSS-gradient default — FM1-class games need PBR/procedural 3D, not 8-bit."
            )
        if _MESH_FROM_LLM_RE.search(blob):
            traps.append(
                "Do not invent hero meshes as raw vertex dumps. Sketch → image → Tripo/Meshy/Hunyuan, "
                "or procedural CSG/spline for crisp architecture."
            )
    return traps


def vertical_slice(genre: str = "", idea: str = "") -> str:
    """The first playable moment — never the full city."""
    blob = blob_of(genre, idea).lower()
    if looks_like_racer(blob) or "racing" in blob:
        return (
            "M0 vertical slice: ONE ship, ONE camera, ONE track segment, accelerate/steer, "
            "a fail state (off-track or integrity), a visible boost pad. No city, no 16-grid, no story cups yet. "
            "Files: src/main.js, src/racer/boot.js, src/racer/track.js, src/racer/vehicle.js, src/racer/world.js. "
            "Keep the Playground racer scaffold — extend it, do not replace with Phaser."
        )
    return (
        "M0 vertical slice: one player, one verb, one fail/win, on-screen goal. "
        "Ship that loop before menus, meta, or extra modes."
    )


def asset_route(text: str) -> dict[str, str]:
    """Which asset path to take. LLMs are bad at meshes-from-zero."""
    blob = (text or "").lower()
    if looks_like_racer(blob) or wants_high_fidelity(blob):
        return {
            "hero": "assets({action:mesh}) with FAL_KEY/TRIPO_API_KEY/MESHY_API_KEY (Tripo/Meshy/Hunyuan). Fail closed without a key — greybox is a cube, not a hero. Remesh <15k tris.",
            "architecture": "Procedural (extrude, instanced facades, splines). Photogrammetry is blurry at speed.",
            "textures": "PBR sets (Polyhaven) or generated albedo/rough/normal — not a single JPEG wrapped on a box.",
            "sfx": "assets({action:sfx}) kit WAV + Howler/Web Audio. No fake licensed music.",
            "music": "Suno on the owner's machine; drop assets/music.mp3. Daedalus does not submit Suno.",
        }
    return {
        "hero": "assets({action:atlas}) or generated PNG sprites via load.image / drawImage.",
        "architecture": "assets({action:tileset}) or simple canvas layers.",
        "textures": "Consistent style bible in assets/_style_bible.json.",
        "sfx": "assets({action:sfx}) kit WAV. Howler or Web Audio one-shots.",
        "music": "Optional loop; never fake licensed tracks.",
    }


def min_stages(kind: str, *, is_game: bool, description: str) -> int:
    if wants_high_fidelity(description) or looks_like_racer(description):
        return 12 if kind in ("vite", "vite-react", "unity", "expo", "godot") else 8
    if is_game:
        return 8 if kind in ("expo", "vite-react", "vite", "unity", "godot") else 6
    return 3


def planner_addendum(kind: str = "", description: str = "") -> str:
    """Injected into the autopilot planner for game (especially 3D/racing) jobs."""
    hi = wants_high_fidelity(description) or looks_like_racer(description)
    if not hi and not description:
        try:
            from .game_engineer import planner_addendum as _eng
            return _eng(kind, description)
        except Exception:
            return ""
    lines = [
        "━━━ ARCADE CRAFT (FM1 / vibe-racer field notes) ━━━",
        "Never 'build me this game'. Hundreds of tiny verified steps. Plan with a strong model; implement cheaply.",
        vertical_slice("", description),
        "Zero trust: smoke + browser + playtest/scenario.json bot after each playable slice. Cul-de-sac (same error 3×) → change approach, don't enlarge the prompt.",
        "FUN is a gate: wayfinding from distance, fail states, visible pickups, skill ceiling. Hammering gas with no risk is not a game.",
        "Web first (Vite + Three.js). Unity/Godot later only if shipping to Steam/stores.",
        "Mobile web: landscape lock, touch stick, load-budget, PWA. FM1 spent versions on this.",
    ]
    if hi:
        lines.extend([
            "Renderer: Three.js WebGPU + TSL materials when the brief is 3D arcade (that is what FM1 ships). Phaser is the wrong default.",
            "Scaffold: Vite projects matching this brief already contain a playable M0 (spline + hover ship + fail + boost). Edit those files.",
            "Hero meshes: assets({action:mesh}) only with a key; never LLM vertices. Environments: procedural for crisp edges at speed.",
            "Audio: assets({action:sfx}) kit WAV + Howler. Instrumental music dropped as files. No AI-lyric BGM.",
            "Quality tiers (Performance / Quality / Ultra) with real knobs: shadows, post-FX, particles, scenery density.",
        ])
        for trap in fidelity_traps(description):
            lines.append(f"TRAP: {trap}")
        lines.append("Keep playtest/scenario.json (hold keys → canvas_changed / lose). Do not delete the racer scaffold's copy.")
    route = asset_route(description)
    lines.append("Assets: " + "; ".join(f"{k}={v}" for k, v in route.items()))
    if kind:
        lines.append(f"Playground kind in this job: {kind}")
    text = "\n".join(lines)
    try:
        from .game_engineer import planner_addendum as _eng
        extra = _eng(kind, description)
        if extra and extra not in text:
            text = f"{text}\n{extra}"
    except Exception:
        pass
    return text


def gdd_section(brainstorm: dict[str, Any] | None) -> str:
    """Markdown block appended to GDD Lite."""
    b = brainstorm or {}
    idea = blob_of(b.get("genre"), b.get("idea"), b.get("unique_hook"), b.get("reference_games"))
    route = asset_route(idea)
    lines = [
        "## Arcade craft (полеви бележки)",
        "",
        f"Референтен жив продукт: [{FIELD_SOURCE} {FIELD_VERSION}]({FIELD_URL}) — Formula Minus One.",
        "",
        f"- **Vertical slice:** {vertical_slice(b.get('genre', ''), idea)}",
        f"- **Hero assets:** {route['hero']}",
        f"- **Среда:** {route['architecture']}",
        f"- **SFX / музика:** {route['sfx']} {route['music']}",
        "- **Fun gate:** посока на пистата от далеч, fail (damage/off-track), видими boost pads, последствия от газта.",
        "- **Мобилен уеб:** landscape, touch, PWA. Не казвай готово без телефонен проход.",
        "- **Cul-de-sac:** 3 еднакви грешки → смени подхода (процедурно вместо mesh, опростен shader, по-малък slice).",
        "",
    ]
    for trap in fidelity_traps(idea):
        lines.append(f"- ⚠️ {trap}")
        lines.append("")
    if looks_like_campaign_bible(idea):
        lines.append(
            "- ⚠️ Campaign bible (biomes/chapters/console SKUs) is a scope warning, not an M0 template."
        )
        lines.append("")
    return "\n".join(lines)


def scope_warnings(brainstorm: dict[str, Any] | None) -> list[str]:
    b = brainstorm or {}
    idea = blob_of(b.get("genre"), b.get("idea"), b.get("reference_games"), b.get("unique_hook"))
    out: list[str] = []
    systems = b.get("systems") or []
    if looks_like_racer(idea) or wants_high_fidelity(idea):
        out.append(
            "High-fidelity racer: M0 is one ship on one segment. Championship/story/1v1 wait until the slice is fun."
        )
        if any(str(s).lower() in ("multiplayer", "netcode_mvp", "matchmaking") for s in systems):
            out.append("Multiplayer is a later wall — FM1 shipped single-player first. Keep MVP offline.")
        out.extend(fidelity_traps(idea))
    if looks_like_campaign_bible(idea):
        out.append(
            "Campaign-bible GDD (multi-biome / 12 chapters / console SKUs): that is not M0. "
            "Ship one verb + one fail first. Do not seed Game Studio from a Souls-like campaign doc."
        )
    return out


def fun_signals(snapshot: str, *, racer: bool | None = None) -> dict[str, Any]:
    """Playtest extras — wayfinding, fail, pickups, challenge. Conservative: blockers only for racers.

    ``racer`` should come from the owner's ask when the caller knows it; the
    source-based guess ignores CSS ``:hover`` (a snake game is not a hovercraft).
    """
    low = (snapshot or "").lower()
    if racer is None:
        racer = looks_like_racer(low.replace(":hover", "")) or "turbo" in low or "lap" in low
    has_fail = any(tok in low for tok in ("gameover", "game over", "crash", "damage", "integrity", "offtrack", "off-track", "destroyed"))
    has_way = any(tok in low for tok in ("checkpoint", "chevron", "arrow", "spline", "racingline", "racing_line", "waypoint"))
    has_pickup = any(tok in low for tok in ("boost", "turbo", "pad", "pickup", "item"))
    has_risk = any(tok in low for tok in ("brake", "airbrake", "damage", "integrity", "energy", "overheat", "cooldown"))
    signals = {
        "fail_state": has_fail,
        "wayfinding": has_way,
        "pickup": has_pickup,
        "risk": has_risk,
        "racer": racer,
    }
    blockers: list[str] = []
    if racer:
        if not has_fail:
            blockers.append("Няма fail/damage — газ без последствия не е състезание.")
        if not has_way:
            blockers.append("Няма wayfinding (chevron/checkpoint) — пистата трябва да се чете отдалеч.")
        if has_pickup and "boost" in low and "emissive" not in low and "glow" not in low:
            blockers.append("Boost pads трябва да светят отдалеч — иначе се пропускат.")
        if not has_risk:
            blockers.append("Липсва риск при газта (спирачка/уязвимост/ресурс).")
    return {
        "signals": signals,
        "blockers": blockers,
        "ship_hint": not blockers,
    }


def critic_notes(description: str = "") -> str:
    if not (wants_high_fidelity(description) or looks_like_racer(description) or "игра" in (description or "").lower() or "game" in (description or "").lower()):
        return ""
    return (
        "Arcade-craft critic extras: penalize unclear track/goal from 3s away, invisible pickups, "
        "no fail state, pixel-art default on a 3D brief, placeholder cubes as hero ships, "
        "and 'always win by holding accelerate'. Reward readable HUD, juice (trails, camera punch), "
        "and a skill ceiling."
    )


def dead_end_advice(repeat_count: int = 3) -> str:
    return (
        f"Cul-de-sac ({repeat_count}× same failure): stop enlarging the prompt. "
        "Switch approach — procedural mesh instead of GLB, simpler shader, smaller slice, "
        "fresh session for a new feature (keep a long session only for the bug). "
        "If 2 distinct approaches failed, NEED_BLOCKED with what you tried."
    )


def handoff_lines() -> str:
    return (
        "ARCADE CRAFT: one deliverable per stage; verify before the next. "
        "Do not regenerate the project. Do not default to pixel art on a 3D brief. "
        + dead_end_advice()
    )
