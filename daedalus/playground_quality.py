"""Playground Quality Gate (Phase 6) — measurable quality/gameplay feedback loop.

Until this gate the autopilot pipeline only proved a build was COMPLETE and that
it RUNS. Quality was *requested* in the system prompt but never *measured*, so
builds converged on the lowest acceptable state: "a whole app that boots".

This module adds the missing closed loop:

  1. ``evaluate`` — an INDEPENDENT critic (vision intent → a different model than
     the code build_model) scores the rendered result against a numeric rubric.
  2. ``build_polish_prompt`` — a holistic polish pass that feeds the coder the
     FULL relevant files (not the truncated 14KB/40KB view that silently caused
     gameplay regressions), driven by the critic's concrete patch targets.

Pure-ish by design: every side effect (LLM routing, file read, browser verify)
is injected by the caller, so this stays decoupled from ``admin.py``.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
from typing import Any, Awaitable, Callable

# Rubric dimensions, each scored 0-10 by the independent critic.
RUBRIC_DIMS = [
    "visual_polish",   # composition, spacing, color, typography — "looks designed"
    "coherence",       # consistent theme/style across screens & components
    "asset_usage",     # generated assets actually used (not blank/placeholder)
    "gameplay_loop",   # core loop present & meaningful (or core UX flow for apps)
    "controls",        # inputs responsive & discoverable
    "feedback",        # juice: state changes, animations, score/notifications
    "progression",     # difficulty/score/levels or app depth beyond one screen
]


def quality_threshold() -> float:
    """Minimum acceptable overall score (0-10). Env override, default 7."""
    try:
        return float(os.environ.get("DAEDALUS_QUALITY_THRESHOLD", "7") or "7")
    except Exception:
        return 7.0


def max_polish_passes() -> int:
    """How many holistic polish iterations to attempt. Env override, default 2."""
    try:
        return max(0, int(os.environ.get("DAEDALUS_QUALITY_MAX", "2") or "2"))
    except Exception:
        return 2


def _clamp_score(v: Any) -> float:
    try:
        f = float(v)
    except Exception:
        return 0.0
    return max(0.0, min(10.0, f))


def _salvage_truncated(text: str) -> dict[str, Any]:
    """The scores of a critic reply cut off by the output limit (its long issue list comes last).

    Found live (2026-11-11): a Bulgarian critique used all 1800 tokens, the JSON never closed, and every game scored
    0.0 "unparsed", so the critic failed every build whatever it looked like."""
    out: dict[str, Any] = {}
    dec = json.JSONDecoder()
    i = text.find('"scores"')
    if i >= 0:
        j = text.find("{", i)
        try:
            out["scores"], _ = dec.raw_decode(text[j:])
        except Exception:
            pass
    m = re.search(r'"overall"\s*:\s*(-?\d+(?:\.\d+)?)', text)
    if m:
        out["overall"] = float(m.group(1))
    m = re.search(r'"summary"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
    if m:
        out["summary"] = m.group(1)
    if out:
        out["truncated"] = True
    return out


def parse_quality_result(text: str) -> dict[str, Any]:
    """Extract & normalize the critic JSON into a stable shape.

    Tolerant of either ``{"scores": {...}, ...}`` or a flat ``{dim: n, ...}``.
    ``overall`` is taken verbatim if present, else the mean of provided dims.
    """
    m = re.search(r"\{[\s\S]+\}", text or "")
    raw: dict[str, Any] = {}
    if m:
        try:
            raw = json.loads(m.group(0))
        except Exception:
            raw = _salvage_truncated(text or "")
    scores_in = raw.get("scores") if isinstance(raw.get("scores"), dict) else raw
    scores: dict[str, float] = {}
    if isinstance(scores_in, dict):
        for dim in RUBRIC_DIMS:
            if dim in scores_in:
                scores[dim] = _clamp_score(scores_in.get(dim))
    if "overall" in raw:
        overall = _clamp_score(raw.get("overall"))
    elif scores:
        overall = round(sum(scores.values()) / len(scores), 2)
    else:
        overall = 0.0
    issues = raw.get("issues") or raw.get("problems") or []
    if not isinstance(issues, list):
        issues = []
    patch_targets = raw.get("patch_targets") or raw.get("targets") or []
    if not isinstance(patch_targets, list):
        patch_targets = []
    return {
        "scores": scores,
        "overall": overall,
        "issues": [str(x)[:300] for x in issues][:12],
        "patch_targets": [str(x)[:200] for x in patch_targets][:12],
        "summary": str(raw.get("summary") or "")[:600],
        "parsed": bool(scores or issues),
        "truncated": bool(raw.get("truncated")),
    }


_CRITIC_SYS = (
    "Ти си STRICT senior арт-директор и game designer. Оценяваш ГОТОВ продукт по "
    "обективна рубрика. НЕ си писал този код — нямаш привързаност към него. "
    "Връщаш САМО валиден JSON. Бъди безмилостен, но честен: overall 7+ значи "
    "‘изглежда професионално, бих го пуснал’. Празни екрани, placeholder-и, "
    "липсващи анимации/feedback, generic неполиран UI → ниски оценки."
)


def build_critic_messages(
    *,
    description: str,
    kind: str,
    plan_goals: list[str],
    dom_excerpt: str,
    code_index: str,
    screenshot_b64: str,
) -> list[dict]:
    goals = "\n".join(f"- {g}" for g in plan_goals[:12]) or "(няма явни цели)"
    dims = "\n".join(f'    "{d}": 0-10,' for d in RUBRIC_DIMS)
    text = (
        f"ПЛАТФОРМА: {kind}\n"
        f"ЗАДАЧА НА ПОТРЕБИТЕЛЯ:\n{(description or '')[:1200]}\n\n"
        f"ЦЕЛИ ОТ ПЛАНА:\n{goals}\n\n"
        f"СТРУКТУРА НА ПРОЕКТА (файлове):\n{code_index[:1500]}\n\n"
        f"ВИДИМ ТЕКСТ В РЕАЛНИЯ БРАУЗЪР (DOM):\n{dom_excerpt or '(празно)'}\n\n"
        "Виж приложения screenshot (ако има) и оцени КАЧЕСТВОТО — не само дали "
        "работи. Върни САМО този JSON:\n"
        "{\n"
        '  "scores": {\n'
        f"{dims}\n"
        "  },\n"
        '  "overall": 0-10,\n'
        '  "summary": "1-2 изречения цялостна оценка",\n'
        '  "issues": ["конкретен дефект 1", "дефект 2"],\n'
        '  "patch_targets": ["какво точно да се подобри + кой аспект"]\n'
        "}\n"
        "issues и patch_targets ТРЯБВА да са конкретни и приложими — не ‘подобри "
        "дизайна’, а напр. ‘hero секцията няма contrast — затъмни фона и увеличи "
        "заглавието’ или ‘играта няма hit-feedback — добави particle при сблъсък’.\n"
        "LANGUAGE: write summary, issues and patch_targets in the language of the user's task above "
        "(English for an English task), whatever the language of these instructions."
    )
    try:
        from .arcade_craft import critic_notes
        note = critic_notes(description)
        if note:
            text += "\n\n" + note
    except Exception:
        pass
    parts: list[dict] = [{"type": "text", "text": text}]
    if screenshot_b64:
        parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"},
        })
    return [
        {"role": "system", "content": _CRITIC_SYS},
        {"role": "user", "content": parts},
    ]


PLAY_SHOT = "assets/_gameplay_screenshot.png"


def _fresh_play_shot(slug: str, project_dir: Any) -> Any:
    """The gameplay check's frame, taken after pressing start, when it shows the current code. The critic judged the
    first paint before: live 2026-11-12, a working lantern maze was rejected as "a bare black start menu"."""
    try:
        shot = project_dir / PLAY_SHOT
        if not shot.is_file():
            return None
        from .harness.perception import _project_edit_mtime
        return shot if shot.stat().st_mtime >= _project_edit_mtime(slug) else None
    except Exception:
        return None



async def evaluate(
    *,
    slug: str,
    kind: str,
    description: str,
    plan_goals: list[str],
    bv: dict,
    route_fn: Callable[..., Awaitable[dict]],
    extract_text_fn: Callable[[dict], str],
    read_tree_fn: Callable[[str], list[dict]],
    is_sensitive_fn: Callable[[str], bool],
    project_dir: Any,
    client_id: str,
) -> dict[str, Any]:
    """Run the independent critic against the rendered build.

    Returns ``parse_quality_result(...)`` enriched with ``threshold``,
    ``passed`` and ``had_screenshot``. Never raises — on failure returns an
    unparsed result so the caller can treat it as a soft skip.
    """
    dom_excerpt = (bv.get("dom_text") or "")[:1800] if isinstance(bv, dict) else ""
    idx_lines: list[str] = []
    try:
        for f in read_tree_fn(slug):
            p = f.get("path", "")
            if not p or is_sensitive_fn(p):
                continue
            idx_lines.append(f"- {p}")
    except Exception:
        idx_lines = []
    code_index = "\n".join(idx_lines)
    screenshot_b64 = ""
    in_play = await asyncio.to_thread(_fresh_play_shot, slug, project_dir)     # walks the project
    try:
        shot_rel = bv.get("screenshot_path") if isinstance(bv, dict) else None
        shot_abs = in_play or (project_dir / shot_rel if shot_rel else None)
        if shot_abs is not None and shot_abs.exists():
            screenshot_b64 = base64.b64encode(shot_abs.read_bytes()).decode()
    except Exception:
        screenshot_b64 = ""
    if in_play is not None:
        description = ((description or "") + "\n\n[The screenshot shows the game DURING PLAY, after pressing start "
                       "and playing the input scenario; it is not the start screen.]")
    try:
        res = await route_fn(
            messages=build_critic_messages(
                description=description, kind=kind, plan_goals=plan_goals,
                dom_excerpt=dom_excerpt, code_index=code_index,
                screenshot_b64=screenshot_b64,
            ),
            intent="vision", max_tokens=4000, temperature=0.0,
            client_id=client_id,
        )
        out = parse_quality_result(extract_text_fn(res))
        out["model_used"] = res.get("model", "") if isinstance(res, dict) else ""
    except Exception as exc:  # soft-fail — quality gate never breaks a build
        out = parse_quality_result("")
        out["error"] = str(exc)[:160]
        out["model_used"] = ""
    out["had_screenshot"] = bool(screenshot_b64)
    out["threshold"] = quality_threshold()
    out["passed"] = out["parsed"] and out["overall"] >= out["threshold"]
    return out


# Files worth polishing — skip binary assets, lockfiles and build output.
_POLISH_SKIP_EXT = (
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico",
    ".mp3", ".wav", ".ogg", ".woff", ".woff2", ".ttf", ".lock", ".map",
)
_POLISH_SKIP_DIR = ("node_modules/", "dist/", "build/", ".git/", "assets/")


def _is_polishable(path: str) -> bool:
    low = path.lower()
    if any(low.endswith(e) for e in _POLISH_SKIP_EXT):
        return False
    return not any(d in low for d in _POLISH_SKIP_DIR)


def build_polish_prompt(
    *,
    description: str,
    kind: str,
    critic: dict[str, Any],
    slug: str,
    read_tree_fn: Callable[[str], list[dict]],
    read_file_fn: Callable[..., str],
    is_sensitive_fn: Callable[[str], bool],
    char_budget: int = 90000,
) -> str:
    """Holistic polish prompt: critic notes + FULL relevant source files.

    Unlike the legacy repair path (14KB/file, 40KB total truncation that made
    the coder ‘simplify’ — i.e. silently drop — unseen logic), this feeds whole
    files up to ``char_budget`` so polish never regresses working behaviour.
    """
    issues = "\n".join(f"- {x}" for x in critic.get("issues", [])) or "(няма)"
    targets = "\n".join(f"- {x}" for x in critic.get("patch_targets", [])) or "(няма)"
    scores = critic.get("scores", {}) or {}
    weak = sorted(scores.items(), key=lambda kv: kv[1])[:3]
    weak_txt = ", ".join(f"{k}={v}" for k, v in weak) or "?"
    blocks: list[str] = []
    used = 0
    for f in read_tree_fn(slug):
        p = f.get("path", "")
        if not p or is_sensitive_fn(p) or not _is_polishable(p):
            continue
        try:
            content = read_file_fn(slug, p, max_bytes=char_budget)
        except Exception:
            continue
        block = f"\n===FILE: {p}===\n{content}\n===END===\n"
        if used + len(block) > char_budget:
            continue
        blocks.append(block)
        used += len(block)
    proj_code = "".join(blocks)
    return (
        f"ПЛАТФОРМА: {kind}\n"
        f"Приложението РАБОТИ, но независим арт-директор го оцени като НЕДОСТАТЪЧНО "
        f"полирано (overall {critic.get('overall')}/10). Най-слаби аспекти: {weak_txt}.\n\n"
        f"ЗАДАЧА НА ПОТРЕБИТЕЛЯ:\n{(description or '')[:1000]}\n\n"
        f"ДЕФЕКТИ ЗА ПОПРАВКА:\n{issues}\n\n"
        f"КОНКРЕТНИ ПОДОБРЕНИЯ:\n{targets}\n\n"
        f"ПЪЛЕН ТЕКУЩ КОД (цели файлове — НЕ съкращавай при редакция):\n{proj_code}\n\n"
        "ИНСТРУКЦИИ:\n"
        "- Това е POLISH pass: ПОВИШИ качеството (визия, анимации, feedback, "
        "game feel/UX дълбочина), без да чупиш работещото.\n"
        "- НЕ премахвай функционалност и НЕ ‘опростявай’ логика. НЕ заменяй код с "
        "placeholder/TODO/коментар.\n"
        "- Връщай САМО файловете, които ПРОМЕНЯШ — цели и синтактично завършени, "
        "в ===FILE: path=== ... ===END=== формат."
    )
