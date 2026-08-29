"""Design Arena practices as a Daedalus adapter — not a dump of their harness.

Upstream: https://designarena.ai
Open harness (MIT, do not vendor): https://github.com/Design-Arena/agent-runner
Methodology: https://notes.designarena.ai/methodology/

Design Arena is a crowdsourced ELO arena: two models get the same prompt,
build in parallel, humans vote on the *playable* output. Agent Runner traces
every tool call. That is a benchmark platform, not a product we should clone.

What we take (ideas only, original Daedalus text):

1. Thin prompt → playable brief *before* files (loop, controls, win/lose, art, platform).
2. Judge the running artifact, not the prompt. Pairwise A/B when two slugs exist.
3. Same scaffold per kind; errors feed back into the loop; watchers stay out of autopilot.
4. Micro-evals: specific signals (canvas, input, loop, fail) — not "looks nice".

What we do **not** copy:

- Agent Runner itself (competing harness).
- Vercel / Supabase tool surface.
- Their published enhancement system prompts (copyrighted methodology page).
- Crowdsourced ELO UI / private lab prompts.
- Single-file HTML as the default game path (Daedalus already prefers multi-file + verify).
"""
from __future__ import annotations

import re
from typing import Any

SOURCE = "https://designarena.ai"
RUNNER = "https://github.com/Design-Arena/agent-runner"
NOTES = "https://notes.designarena.ai/methodology/"

_GAME_HINT = re.compile(
    r"\b(игр[аи]|game|arcade|platformer|shooter|puzzle|пъзел|змия|snake|"
    r"racer|racing|f-?zero|godot|phaser|canvas)\b",
    re.I,
)
_RACER = re.compile(r"\b(racer|racing|f-?zero|wipeout|hover\s*ship)\b", re.I)
_SNAKE = re.compile(r"\b(змия|snake)\b", re.I)
_PLATFORMER = re.compile(r"\b(platformer|платформ|metroid)\b", re.I)
_PUZZLE = re.compile(r"\b(puzzle|пъзел|match-?3|wordle)\b", re.I)
_APP_HINT = re.compile(
    r"\b(app|приложение|dashboard|todo|login|crud|сайт|website|landing)\b",
    re.I,
)


def looks_like_game(prompt: str) -> bool:
    return bool(_GAME_HINT.search(prompt or ""))


def expand_brief(prompt: str, *, kind: str = "") -> dict[str, Any]:
    """Turn a one-liner into a playable brief. Heuristic, works offline.

    Inspired by Design Arena's *idea* of prompt enhancement (thin → specific)
    without copying their system prompts.
    """
    raw = (prompt or "").strip()
    if not raw:
        return {"ok": False, "error": "empty prompt"}
    game = looks_like_game(raw)
    kind_key = (kind or "").strip() or ("vite" if game else "static")
    loop = "input → update → render → visible fail/win"
    controls = "keyboard (arrows/WASD) + click/tap fallback"
    win_lose = "one fail state the player can see; one score or goal"
    art = "2D: real assets/*.png referenced in code. 3D: GLB / greybox, no CSS-gradient hero."
    if _SNAKE.search(raw):
        loop = "grid step on interval; grow on eat; die on wall/self"
        controls = "arrow keys / WASD, no diagonal unless specified"
        win_lose = "eat food → score++; hit self/wall → game over + restart"
        art = "canvas cells or small sprites; readable head vs body"
        kind_key = kind_key if kind else "static"
    elif _RACER.search(raw):
        loop = "fixed timestep: steer → move on spline → collide → fail/boost"
        controls = "left/right + boost; visible off-track fail"
        win_lose = "off-track or crash is a fail; lap/checkpoint is a win slice"
        art = "Vite + Three.js WebGPU (src/racer/). Not Phaser. Not pixel-art default."
        kind_key = kind_key if kind else "vite"
    elif _PLATFORMER.search(raw):
        loop = "move/jump → gravity → collide tiles → camera follow"
        controls = "left/right + jump; coyote time if easy"
        win_lose = "fall/spikes fail; flag/door is the M0 win"
        art = "tilemap + one player sprite; Godot 4 if desktop, Vite/Phaser if browser"
        kind_key = kind_key if kind else "godot"
    elif _PUZZLE.search(raw):
        loop = "select → resolve → check solved"
        controls = "click/tap primary; undo if cheap"
        win_lose = "solved board vs stuck; restart"
        art = "high-contrast pieces; no tiny hit targets"
        kind_key = kind_key if kind else "static"
    elif not game:
        loop = "open screen → do the one job → see saved/updated state"
        controls = "obvious primary button; empty/error/success states"
        win_lose = "empty state + error state + success (not a blank page)"
        art = "layout + type hierarchy; no fake stock photography as the product"

    brief = {
        "ok": True,
        "source": SOURCE,
        "license_note": "adapter original; Design Arena is attribution-only",
        "intent": raw[:500],
        "is_game": game,
        "kind": kind_key,
        "loop": loop,
        "controls": controls,
        "win_lose": win_lose,
        "art": art,
        "feasibility": (
            "M0 only: one verb, one fail, one screen that plays. "
            "Not a campaign bible (biomes / 12 chapters / console SKUs)."
        ),
        "verify": (
            "smoke + browser (visual kinds) + playtest. "
            "Do not claim готово on code that was never run."
        ),
    }
    brief["prompt_block"] = format_brief_block(brief)
    return brief


def format_brief_block(brief: dict[str, Any]) -> str:
    if not brief.get("ok"):
        return ""
    kind = brief.get("kind") or "?"
    return (
        "\n━━━ PLAYABLE BRIEF (Design Arena practice, Daedalus text) ━━━\n"
        f"Intent: {brief.get('intent')}\n"
        f"Kind: {kind}  game={brief.get('is_game')}\n"
        f"Loop: {brief.get('loop')}\n"
        f"Controls: {brief.get('controls')}\n"
        f"Win/lose: {brief.get('win_lose')}\n"
        f"Art: {brief.get('art')}\n"
        f"Feasibility: {brief.get('feasibility')}\n"
        f"Verify: {brief.get('verify')}\n"
        "Fill these before writing files. Do not skip to a 500-line index.html.\n"
    )


def lessons() -> dict[str, Any]:
    """What we keep vs skip — for Studio / native reach_status-style doctor."""
    return {
        "ok": True,
        "source": SOURCE,
        "runner": RUNNER,
        "notes": NOTES,
        "keep": [
            "Thin prompt → playable brief (loop, controls, win/lose, art, platform) before files.",
            "Judge the running build. Pairwise A/B when two slugs exist.",
            "Same scaffold per kind. Feed tool errors back. No hanging watchers in autopilot.",
            "Micro-evals: canvas/input/loop/fail — not vague polish.",
        ],
        "skip": [
            "Do not vendor Design-Arena/agent-runner.",
            "Do not add Vercel/Supabase-only tools.",
            "Do not copy their published enhancement prompts.",
            "Do not make single-file HTML the default game path.",
            "Leaderboard API needs a key + attribution; not required for building.",
        ],
        "daedalus_already": [
            "Playground scaffolds per kind",
            "Autopilot plan → code → smoke → browser",
            "job_contract Definition of Done",
            "playtest personas + arcade fun gate",
            "Godot 4 + Vite/Three racer paths",
        ],
    }
