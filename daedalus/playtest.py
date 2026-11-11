"""Lightweight game playtest — MatrAIx Persona *pattern*, not the 1M dataset.

Upstream inspiration: https://github.com/MatrAIx-ai/MatrAIx-Persona-8B (MIT).

MatrAIx evaluates products with heterogeneous simulated users across a
1,290-dimension schema (background / psychology / capability / behavior /
lifestyle) and four environments (Survey, Chat, Web, App).

Daedalus does **not** vendor Docker, Harbor, or the HuggingFace 1M coreset.
We keep a tiny built-in player panel and ask each persona to critique a
Playground game: loop, controls, first 30s, win/lose, frustration.

Use this after a visual build, before claiming the game is done.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)

# Compact slice of MatrAIx's five top-level groups — game-relevant only.
PERSONAS: list[dict[str, Any]] = [
    {
        "id": "casual_bg",
        "label": "Casual (BG)",
        "background": {"age_band": "25-34", "region": "BG", "language": "bg"},
        "psychology": {"patience": "low", "risk": "low"},
        "capability": {"gaming_skill": "casual"},
        "behavior": {"session_minutes": 10, "tutorial_tolerance": "20s"},
        "lifestyle": {"platform": "phone + laptop"},
        "ask": "Какво да правя в първите 5 секунди? Какво би ме накарало да затворя?",
    },
    {
        "id": "core_pc",
        "label": "Core PC",
        "background": {"age_band": "18-24", "region": "EU", "language": "en"},
        "psychology": {"patience": "medium", "risk": "medium"},
        "capability": {"gaming_skill": "core", "input": "keyboard"},
        "behavior": {"session_minutes": 40, "expects": "tight controls, readable HUD"},
        "lifestyle": {"platform": "PC"},
        "ask": "Are controls obvious? Is there a win/lose? Would you keep playing?",
    },
    {
        "id": "kids",
        "label": "Kids",
        "background": {"age_band": "8-12", "region": "global", "language": "simple"},
        "psychology": {"patience": "very_low", "risk": "low"},
        "capability": {"gaming_skill": "novice", "reading": "short words"},
        "behavior": {"session_minutes": 8, "needs": "color + big targets"},
        "lifestyle": {"platform": "tablet"},
        "ask": "Can a child understand the goal without reading a paragraph?",
    },
    {
        "id": "hardcore",
        "label": "Hardcore",
        "background": {"age_band": "20-35", "region": "global", "language": "en"},
        "psychology": {"patience": "high", "risk": "high"},
        "capability": {"gaming_skill": "expert"},
        "behavior": {"session_minutes": 90, "expects": "depth, juice, fail-forward"},
        "lifestyle": {"platform": "PC"},
        "ask": "Is there a skill ceiling or just a toy? What is missing for a second session?",
    },
    {
        "id": "impatient",
        "label": "Impatient adult",
        "background": {"age_band": "35-50", "region": "EU", "language": "en"},
        "psychology": {"patience": "very_low", "risk": "low"},
        "capability": {"gaming_skill": "lapsed"},
        "behavior": {"session_minutes": 5, "hates": "unclear start, silent fail"},
        "lifestyle": {"platform": "laptop between tasks"},
        "ask": "Would you quit before the first score? Why?",
    },
    {
        "id": "non_gamer",
        "label": "Non-gamer",
        "background": {"age_band": "40-60", "region": "BG", "language": "bg"},
        "psychology": {"patience": "medium", "risk": "very_low"},
        "capability": {"gaming_skill": "none"},
        "behavior": {"needs": "on-screen hint in Bulgarian"},
        "lifestyle": {"platform": "browser"},
        "ask": "Има ли текст, който обяснява бутоните? Страшно ли е празно?",
    },
]

DIMENSIONS = ("loop_clarity", "controls", "first_30s", "win_lose", "frustration")

LlmFn = Callable[[str, str], Awaitable[str]]


def list_personas() -> list[dict[str, Any]]:
    return list(PERSONAS)


def _project_snapshot(slug: str, *, max_chars: int = 5000) -> str:
    from .paths import playground_projects_dir
    from .playground import slugify
    root = playground_projects_dir() / slugify(slug)
    if not root.is_dir():
        return f"(missing project {slug})"
    bits: list[str] = []
    for rel in _snapshot_files(root):
        path = root / rel
        if path.is_file():
            bits.append(f"=== {rel} ===\n{path.read_text(encoding='utf-8', errors='replace')[:1800]}")
    if not bits:
        names = sorted(p.name for p in root.iterdir())[:40]
        bits.append("files: " + ", ".join(names))
    text = "\n\n".join(bits)
    return text[:max_chars]


_KNOWN_FILES = (
    "index.html", "main.js", "src/main.js", "src/App.jsx", "game.js", "README.md",
    "src/racer/vehicle.js", "src/racer/track.js", "src/racer/boot.js",
)
_SCRIPT_SRC = re.compile(r"""<script[^>]+src\s*=\s*["']([^"':?#]+)["']""", re.I)


def _snapshot_files(root: Path) -> list[str]:
    """The files that are the game: index.html, the scripts it loads, the known entry names, then the other scripts.

    Found live (2026-11-11): an Autopilot game kept its code in script.js; only fixed names were read, so the critic
    saw no input handler and no game loop and failed a working game."""
    out: list[str] = []

    def add(rel: str) -> None:
        rel = rel.lstrip("./")
        if rel and rel not in out and (root / rel).is_file():
            out.append(rel)

    add("index.html")
    index = root / "index.html"
    if index.is_file():
        for src in _SCRIPT_SRC.findall(index.read_text(encoding="utf-8", errors="replace")):
            if not src.startswith(("//", "http")):
                add(src)
    for rel in _KNOWN_FILES:
        add(rel)
    others = [p for pat in ("*.js", "src/*.js", "src/**/*.ts", "src/*.jsx") for p in root.glob(pat)]
    for p in sorted(others, key=lambda p: -p.stat().st_size):
        add(p.relative_to(root).as_posix())
    return out


def _heuristic(snapshot: str) -> dict[str, Any]:
    low = snapshot.lower()
    has_canvas = (
        "canvas" in low or "<canvas" in low or "phaser" in low
        or "webglrenderer" in low or "webgpurenderer" in low or "from 'three'" in low
    )
    has_input = any(tok in low for tok in ("keydown", "keyup", "pointer", "touch", "click", "addlistener"))
    has_loop = any(tok in low for tok in ("requestanimationframe", "setinterval", "gameloop", "update("))
    has_win = any(tok in low for tok in (
        "score", "gameover", "win", "lose", "alive", "game over",
        "integrity", "failed", "провал", "off-track", "offtrack",
    ))
    blockers = []
    if not has_canvas:
        blockers.append("Няма canvas/Phaser — първата рисунка може да липсва.")
    if not has_input:
        blockers.append("Не се вижда input handler — контролите може да са неясни.")
    if not has_loop:
        blockers.append("Не се вижда game loop.")
    if not has_win:
        blockers.append("Няма явен win/lose или score.")
    extra_signals: dict[str, Any] = {}
    try:
        from .arcade_craft import fun_signals
        extra = fun_signals(snapshot)
        extra_signals = dict(extra.get("signals") or {})
        blockers.extend(extra.get("blockers") or [])
    except Exception:
        extra_signals = {}
    return {
        "signals": {
            "canvas": has_canvas,
            "input": has_input,
            "loop": has_loop,
            "win_lose": has_win,
            **extra_signals,
        },
        "blockers": blockers,
        "ship_hint": not blockers,
    }


def _parse_json(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(raw[start:end + 1])
                return data if isinstance(data, dict) else None
            except json.JSONDecodeError:
                return None
    return None


async def _persona_turn(
    persona: dict[str, Any],
    snapshot: str,
    extra: str,
    llm_fn: LlmFn | None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": persona["id"],
        "label": persona["label"],
        "ask": persona["ask"],
    }
    if llm_fn is None:
        row["mode"] = "catalog"
        row["note"] = "LLM skipped — persona queued, heuristic only"
        return row
    system = (
        "You are a simulated playtester in the MatrAIx style: stay in character, "
        "judge a small browser game. Reply JSON only: "
        '{"loop_clarity":1-5,"controls":1-5,"first_30s":1-5,"win_lose":1-5,'
        '"frustration":1-5,"would_quit":true|false,"quote":"one sentence","fix":"one concrete fix"}.'
    )
    user = (
        f"Persona {persona['label']} ({persona['id']})\n"
        f"background={persona['background']}\n"
        f"psychology={persona['psychology']}\n"
        f"capability={persona['capability']}\n"
        f"behavior={persona['behavior']}\n"
        f"Question: {persona['ask']}\n\n"
        f"GAME SOURCE (truncated):\n{snapshot}\n"
    )
    if extra:
        user += f"\nBROWSER EXTRACT:\n{extra[:1500]}\n"
    try:
        raw = await llm_fn(system, user)
    except Exception as exc:
        row["error"] = str(exc)[:200]
        return row
    parsed = _parse_json(raw) or {"raw": (raw or "")[:800]}
    row.update(parsed)
    return row


async def run_playtest(
    slug: str,
    *,
    kind: str = "static",
    n: int = 4,
    use_browser: bool = False,
    llm_fn: LlmFn | None = None,
    persona_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Run N simulated players against a Playground project."""
    chosen = PERSONAS
    if persona_ids:
        want = set(persona_ids)
        chosen = [p for p in PERSONAS if p["id"] in want] or PERSONAS
    chosen = chosen[: max(1, min(n, len(PERSONAS)))]

    snapshot = _project_snapshot(slug)
    heuristic = _heuristic(snapshot)
    extra = ""
    if use_browser:
        try:
            from .playground_browser import verify_app
            bv = await verify_app(slug, kind)
            extra = (bv.get("dom_text") or bv.get("summary") or "")[:1500]
        except Exception as exc:
            extra = f"(browser skipped: {exc})"[:200]

    turns: list[dict[str, Any]] = []
    for persona in chosen:
        turns.append(await _persona_turn(persona, snapshot, extra, llm_fn))

    scores: dict[str, list[float]] = {d: [] for d in DIMENSIONS}
    would_quit = 0
    quotes: list[str] = []
    fixes: list[str] = []
    for t in turns:
        for d in DIMENSIONS:
            try:
                scores[d].append(float(t[d]))
            except (KeyError, TypeError, ValueError):
                pass
        if t.get("would_quit") is True:
            would_quit += 1
        if t.get("quote"):
            quotes.append(f"{t.get('label')}: {t['quote']}")
        if t.get("fix"):
            fixes.append(str(t["fix"]))

    avg = {d: (round(sum(v) / len(v), 2) if v else None) for d, v in scores.items()}
    blockers = list(heuristic["blockers"])
    if would_quit >= max(1, len(turns) // 2) and llm_fn:
        blockers.append("Повече от половината playtester-и биха напуснали.")
    ship_ready = heuristic["ship_hint"] and not (would_quit >= max(1, len(turns) // 2) and llm_fn)

    summary = (
        f"playtest {slug}: {len(turns)} personas; "
        + ", ".join(f"{k}={v}" for k, v in avg.items() if v is not None)
        + (f"; quit={would_quit}/{len(turns)}" if llm_fn else "; heuristic-only")
    )
    return {
        "ok": True,
        "source": "https://github.com/MatrAIx-ai/MatrAIx-Persona-8B",
        "license_note": "MIT pattern; no 1M dataset vendored",
        "slug": slug,
        "kind": kind,
        "personas": turns,
        "scores": avg,
        "heuristic": heuristic,
        "blockers": blockers,
        "quotes": quotes,
        "fixes": fixes[:8],
        "ship_ready": ship_ready,
        "summary": summary,
    }


def format_for_prompt(report: dict[str, Any]) -> str:
    lines = ["━━━ PLAYTEST (MatrAIx-style personas) ━━━", report.get("summary") or ""]
    for b in report.get("blockers") or []:
        lines.append(f"  • {b}")
    for q in (report.get("quotes") or [])[:6]:
        lines.append(f"  “{q}”")
    for f in (report.get("fixes") or [])[:4]:
        lines.append(f"  fix: {f}")
    return "\n".join(lines)
