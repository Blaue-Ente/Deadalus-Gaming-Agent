"""Reference Scout — research a game/app by name or URL for Playground builds.

Turns «направи като Flappy Bird» or an itch.io link into a compact brief block
injected into ai-gen-desc, autopilot research, and asset auto-pack.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from .playground import project_dir, write_file, _safe_join
from .playground_assets import ensure_assets_dir

log = logging.getLogger(__name__)

REFERENCE_REL = "assets/_reference.json"
_URL_RE = re.compile(r"^https?://", re.I)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_reference(raw: str) -> str:
    ref = (raw or "").strip()
    if not ref:
        raise ValueError("празна референция")
    if ref.startswith("www."):
        ref = "https://" + ref
    elif not _URL_RE.match(ref) and "." in ref and " " not in ref and "/" in ref:
        # bare domain/path without scheme
        ref = "https://" + ref.lstrip("/")
    return ref


def classify_reference(raw: str) -> tuple[str, str]:
    """Return (type, normalized_value) where type is ``url`` or ``name``."""
    ref = normalize_reference(raw)
    if _URL_RE.match(ref):
        return "url", ref
    return "name", ref


def build_search_queries(reference: str, *, project_name: str = "") -> list[str]:
    """Targeted DuckDuckGo queries for game reference research."""
    kind, value = classify_reference(reference)
    if kind == "url":
        host = urlparse(value).netloc.replace("www.", "")
        return [
            f"{value} game description gameplay",
            f"site:{host} game" if host else f"{value} mechanics art style",
        ]
    # Game / app name
    base = value
    queries = [
        f'"{base}" game mechanics gameplay features',
        f'"{base}" visual style art pixel sprites screenshots',
        f"{base} indie game review",
    ]
    if project_name and project_name.lower() not in base.lower():
        queries.append(f"{base} clone inspired by {project_name}")
    return queries[:4]


def format_brief_block(data: dict[str, Any]) -> str:
    """Compact injectable block for planners and builders."""
    title = data.get("title") or data.get("reference") or "Reference"
    lines = [
        f"🎯 РЕФЕРЕНЦИЯ — «{title}» (Reference Scout)",
        f"Резюме: {data.get('summary', '').strip()}",
    ]
    mech = data.get("mechanics") or []
    if mech:
        lines.append("Механики: " + "; ".join(mech[:8]))
    if data.get("visual_style"):
        lines.append(f"Визуален стил: {data['visual_style']}")
    if data.get("scope_hints"):
        lines.append(f"MVP насоки: {data['scope_hints']}")
    sources = data.get("sources") or []
    if sources:
        lines.append("Източници:")
        for s in sources[:4]:
            t = (s.get("title") or "").strip()
            u = (s.get("url") or "").strip()
            if t or u:
                lines.append(f"  • {t} ({u})")
    lines.append(
        "ВАЖНО: Вдъхнови се от референцията, но НЕ копирай код/асети 1:1 — оригинална имплементация."
    )
    return "\n".join(lines)


def _reference_path(slug: str):
    return _safe_join(project_dir(slug), REFERENCE_REL)


def load_reference(slug: str) -> dict[str, Any] | None:
    path = _reference_path(slug)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def save_reference(slug: str, data: dict[str, Any]) -> None:
    ensure_assets_dir(slug)
    data = dict(data)
    data["saved_at"] = _now_iso()
    write_file(slug, REFERENCE_REL, json.dumps(data, ensure_ascii=False, indent=2))


async def _collect_sources(
    memory,
    queries: list[str],
    *,
    limit_per_query: int = 4,
) -> list[dict[str, str]]:
    from .tools import web_search_tool

    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for q in queries:
        try:
            ws = await web_search_tool(
                memory, q, limit=limit_per_query,
                fetch_content=False, record_discoveries=False, for_self=True,
            )
        except Exception as exc:
            log.debug("reference scout search failed %r: %s", q, exc)
            continue
        for r in (ws.get("results") or []):
            url = (r.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            out.append({
                "title": (r.get("title") or "").strip(),
                "url": url,
                "snippet": (r.get("snippet") or r.get("content_preview") or "")[:400],
            })
        if len(out) >= 10:
            break
    return out[:10]


async def _fetch_url_context(url: str) -> str:
    from .tools import web_fetch_tool
    try:
        res = await web_fetch_tool(url, max_chars=6000)
        if res.get("ok") and res.get("text"):
            return (res["text"] or "")[:5000]
    except Exception as exc:
        log.debug("reference fetch %s: %s", url, exc)
    return ""


async def _synthesize_reference(
    reference: str,
    ref_type: str,
    *,
    project_name: str,
    description: str,
    sources: list[dict[str, str]],
    page_text: str = "",
) -> dict[str, Any]:
    from . import daedalus_mesh as mesh

    src_lines = []
    for s in sources[:8]:
        src_lines.append(
            f"- {s.get('title','')}: {s.get('snippet','')[:200]} ({s.get('url','')})"
        )
    user = (
        f"РЕФЕРЕНЦИЯ: {reference} (тип: {ref_type})\n"
        f"Проект: {project_name or '(нов)'}\n"
        f"Описание от потребителя: {description or '(няма)'}\n\n"
    )
    if page_text:
        user += f"СЪДЪРЖАНИЕ ОТ URL (отрязък):\n{page_text[:3500]}\n\n"
    if src_lines:
        user += "УЕБ ИЗТОЧНИЦИ:\n" + "\n".join(src_lines) + "\n\n"
    user += (
        "Върни САМО валиден JSON:\n"
        '{"title":"...","summary":"2-3 изречения на български","mechanics":["..."],"'
        'visual_style":"...","scope_hints":"какво е must-have MVP vs full game"}'
    )
    sys = (
        "Ти си game designer researcher. Анализираш референция (игра/приложение) "
        "и извличаш actionable brief за AI разработчик. Български за summary/mechanics/scope. "
        "visual_style на английски (за image prompts). Без markdown."
    )
    try:
        res = await mesh.route(
            messages=[{"role": "system", "content": sys}, {"role": "user", "content": user}],
            model="alltoken:deepseek-v3.2",
            intent="chat",
            max_tokens=1200,
            temperature=0.3,
            client_id="reference-scout/synth",
        )
        text = ""
        for blk in (res.get("content") or []):
            if blk.get("text"):
                text = blk["text"]
                break
        m = re.search(r"\{[\s\S]+\}", text)
        if m:
            parsed = json.loads(m.group(0))
            if isinstance(parsed, dict):
                return parsed
    except Exception as exc:
        log.warning("reference scout synthesis failed: %s", exc)

    # Fallback without LLM
    return {
        "title": reference[:80],
        "summary": f"Референция: {reference}. Вдъхнови се от механиките и визията.",
        "mechanics": [],
        "visual_style": "match reference game art style",
        "scope_hints": "Core loop first, polish later",
    }


async def scout_reference(
    reference: str,
    *,
    project_name: str = "",
    description: str = "",
    memory=None,
    save_slug: str | None = None,
) -> dict[str, Any]:
    """Research a reference by name or URL. Optionally persist to project."""
    ref_type, ref_value = classify_reference(reference)
    queries = build_search_queries(ref_value, project_name=project_name)

    page_text = ""
    if ref_type == "url":
        page_text = await _fetch_url_context(ref_value)

    sources: list[dict[str, str]] = []
    if memory is not None:
        sources = await _collect_sources(memory, queries)
        if ref_type == "url" and not page_text:
            # try first search result as fallback fetch
            for s in sources[:2]:
                if s.get("url"):
                    page_text = await _fetch_url_context(s["url"])
                    if page_text:
                        break

    synth = await _synthesize_reference(
        ref_value, ref_type,
        project_name=project_name,
        description=description,
        sources=sources,
        page_text=page_text,
    )

    result: dict[str, Any] = {
        "ok": True,
        "reference": ref_value,
        "reference_type": ref_type,
        "title": synth.get("title") or ref_value,
        "summary": synth.get("summary") or "",
        "mechanics": synth.get("mechanics") or [],
        "visual_style": synth.get("visual_style") or "",
        "scope_hints": synth.get("scope_hints") or "",
        "sources": sources,
        "queries": queries,
    }
    result["brief_block"] = format_brief_block(result)

    if save_slug:
        save_reference(save_slug, result)

    return result
