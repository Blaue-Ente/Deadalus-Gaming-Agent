"""Daedalus Implementation Plan Generator.

When the owner approves a discovery, Daedalus generates a structured
implementation plan that maps the discovery (a library, article, tool,
pattern, MCP server, AI agent, etc.) to one or more Iyveris services
where it makes sense to integrate it.

The plan contains:
  - target services (matched by capability/keyword overlap)
  - the "why" (what value this brings)
  - concrete integration steps
  - files / endpoints / packages needed
  - links to the source repo/article + any related docs
  - risks and open questions

Plans are persisted as JSON files in ``data/daedalus/plans/`` so they
survive restarts and can be listed/regenerated from the admin UI.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import data_root

log = logging.getLogger(__name__)


def _plans_dir() -> Path:
    path = data_root() / "plans"
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Service matching (heuristic — keyword + capability overlap)
# ---------------------------------------------------------------------------

# Stop-words we don't want to count as signal
_STOP = {
    "the", "a", "an", "and", "or", "of", "for", "with", "to", "in", "on",
    "is", "are", "this", "that", "it", "as", "by", "from", "at", "be",
    "ai", "tool", "library", "open", "source", "github", "project",
    "framework", "api", "model", "models", "use", "using", "new",
}


def _tokenize(text: str) -> set[str]:
    """Lowercase word-set, no punctuation, no stop-words, min 3 chars."""
    if not text:
        return set()
    words = re.findall(r"[a-zA-Zа-яА-Я0-9_-]{3,}", text.lower())
    return {w for w in words if w not in _STOP}


def match_services(
    discovery: dict[str, Any],
    registry: dict[str, Any],
    max_results: int = 3,
    min_score: int = 1,
) -> list[dict[str, Any]]:
    """Score every service in the registry against the discovery.

    Score = (# capability matches × 3) + (# keyword matches × 2) +
            (# description-word matches × 1)

    Returns top ``max_results`` services with score >= ``min_score``,
    each annotated with its match score and the matching tokens.
    """
    meta = discovery.get("metadata", {}) or {}
    content = discovery.get("content", "") or ""
    blob = " ".join([
        meta.get("title", ""),
        meta.get("url", ""),
        meta.get("kind", ""),
        content,
    ])
    disc_tokens = _tokenize(blob)
    if not disc_tokens:
        return []

    services = registry.get("services", {}) or {}
    scored: list[dict[str, Any]] = []
    for slug, svc in services.items():
        if not svc.get("enabled", True):
            continue
        caps = {c.lower() for c in (svc.get("capabilities") or [])}
        kws = {k.lower() for k in (svc.get("keywords") or [])}
        desc_tokens = _tokenize(svc.get("description", ""))

        cap_hits = disc_tokens & {c.replace("-", "") for c in caps} | (disc_tokens & caps)
        kw_hits = disc_tokens & kws
        desc_hits = disc_tokens & desc_tokens

        score = len(cap_hits) * 3 + len(kw_hits) * 2 + len(desc_hits)
        if score < min_score:
            continue
        scored.append({
            "slug": slug,
            "name": svc.get("name", slug),
            "description": svc.get("description", ""),
            "base_url": svc.get("base_url", ""),
            "capabilities": svc.get("capabilities", []),
            "score": score,
            "matched_capabilities": sorted(cap_hits),
            "matched_keywords": sorted(kw_hits),
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:max_results]


# ---------------------------------------------------------------------------
# Plan generation (LLM)
# ---------------------------------------------------------------------------

_PLAN_SYSTEM = """You are Daedalus, the architect of the Iyveris ecosystem.
The owner has just APPROVED an open-source discovery (library, tool, MCP server,
AI agent, paper, pattern, etc.) and you must produce a concrete, actionable
implementation plan for integrating it into one or more Iyveris services.

You speak the owner's language (Bulgarian). Be precise, not preachy.
Never invent file paths or endpoints you have no evidence of — when you are
guessing, say "(предположение — да се потвърди)".

Output STRICT MARKDOWN with the following section headers, in this order:

## Резюме
2–3 изречения какво е находката и защо е одобрена.

## Целеви услуги
За всяка матчната услуга — отделен под-раздел `### <Service Name> (<slug>)`:
- **Защо тук:** какво конкретно подобрява в тази услуга
- **Какво се интегрира:** функция / endpoint / data flow
- **Файлове за пипане (предполагаеми):** списък
- **Пакети / зависимости:** pip / npm имена ако има
- **Стъпки:** номерирани, конкретни, малки PR-able стъпки

## Линкове
- Source: <url на находката>
- Допълнителни ресурси които агентът трябва да отвори преди да започне

## Рискове и въпроси
- Какво може да се счупи
- Open въпроси към собственика преди старт

## Estimated effort
Малко (≤2ч) / Средно (½ ден) / Голямо (1+ дни) — с едно изречение защо.
"""


def _build_plan_prompt(
    discovery: dict[str, Any],
    matched: list[dict[str, Any]],
) -> str:
    meta = discovery.get("metadata", {}) or {}
    content = discovery.get("content", "") or ""

    if matched:
        svc_block = "\n".join([
            f"- **{s['name']}** (slug=`{s['slug']}`, score={s['score']})\n"
            f"  - capabilities: {', '.join(s['capabilities'])}\n"
            f"  - matched on: caps={s['matched_capabilities']}, keywords={s['matched_keywords']}\n"
            f"  - description: {s['description'][:240]}"
            for s in matched
        ])
    else:
        svc_block = (
            "_Никоя услуга не match-на автоматично. Прецени дали находката е:_\n"
            "- общо-приложима (Дедал може да я ползва вътрешно)\n"
            "- пилотен проект за нова услуга\n"
            "- или просто не е за нас сега."
        )

    return f"""ОДОБРЕНА НАХОДКА
================
- Заглавие: {meta.get('title', '?')}
- URL: {meta.get('url', '?')}
- Тип: {meta.get('kind', '?')}
- Източник: {meta.get('source', '?')}
- Confidence: {meta.get('confidence', '?')}
- Защо е полезна (от scanner-а): {content[:1500]}

КАНДИДАТ-УСЛУГИ (по keyword/capability matching)
================================================
{svc_block}

Изготви имплементационния план според формата от system prompt-а."""


async def generate_plan(
    discovery: dict[str, Any],
    registry: dict[str, Any],
    daedalus,  # DaedalusAgent — typed loosely to avoid circular import
) -> dict[str, Any]:
    """Generate an implementation plan for an approved discovery.

    Returns the saved plan dict (also written to disk).
    """
    from .agent import _call_claude  # local import to avoid circular

    matched = match_services(discovery, registry)
    prompt = _build_plan_prompt(discovery, matched)

    try:
        markdown = await _call_claude(
            system=_PLAN_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        log.exception("Plan generation LLM call failed")
        markdown = (
            f"## Грешка при генериране\n\n"
            f"LLM грешка: `{exc}`\n\n"
            f"Натисни 'Regenerate' за нов опит."
        )

    plan = {
        "id": f"plan:{uuid.uuid4().hex[:12]}",
        "discovery_id": discovery.get("id", ""),
        "discovery_title": (discovery.get("metadata") or {}).get("title", ""),
        "discovery_url": (discovery.get("metadata") or {}).get("url", ""),
        "discovery_kind": (discovery.get("metadata") or {}).get("kind", ""),
        "matched_services": [
            {"slug": s["slug"], "name": s["name"], "score": s["score"]}
            for s in matched
        ],
        "markdown": markdown,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "draft",  # draft | accepted | done | obsolete
    }
    save_plan(plan)
    log.info("Daedalus plan generated id=%s discovery=%s services=%d",
             plan["id"], plan["discovery_id"], len(matched))
    return plan


# ---------------------------------------------------------------------------
# Storage (JSON files, one per plan)
# ---------------------------------------------------------------------------

def _plan_path(plan_id: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9_:-]", "_", plan_id)
    return _plans_dir() / f"{safe}.json"


def save_plan(plan: dict[str, Any]) -> None:
    path = _plan_path(plan["id"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)


def get_plan(plan_id: str) -> dict[str, Any] | None:
    path = _plan_path(plan_id)
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def list_plans(discovery_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    plans: list[dict[str, Any]] = []
    for p in _plans_dir().glob("plan:*.json"):
        try:
            with open(p, encoding="utf-8") as f:
                plan = json.load(f)
        except Exception:
            continue
        if discovery_id and plan.get("discovery_id") != discovery_id:
            continue
        plans.append({
            "id": plan["id"],
            "discovery_id": plan.get("discovery_id", ""),
            "discovery_title": plan.get("discovery_title", ""),
            "discovery_url": plan.get("discovery_url", ""),
            "matched_services": plan.get("matched_services", []),
            "status": plan.get("status", "draft"),
            "created_at": plan.get("created_at", ""),
        })
    plans.sort(key=lambda x: x["created_at"], reverse=True)
    return plans[:limit]


def set_plan_status(plan_id: str, status: str) -> bool:
    if status not in ("draft", "accepted", "done", "obsolete"):
        return False
    plan = get_plan(plan_id)
    if not plan:
        return False
    plan["status"] = status
    plan["status_changed_at"] = datetime.now(timezone.utc).isoformat()
    save_plan(plan)
    return True


def delete_plan(plan_id: str) -> bool:
    path = _plan_path(plan_id)
    if path.exists():
        path.unlink()
        return True
    return False
