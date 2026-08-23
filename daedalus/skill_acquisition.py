"""Daedalus Skill Acquisition — анализира GitHub репо и генерира план за интеграция.

Дедал може да:
1. Анализира GitHub репо (публично) и разбере какво прави
2. Предложи план в чата как ще го интегрира и какъв ще е ефектът
3. Проактивно да заяви нужда от ново умение

Тригери (маркери в LLM отговора):
  <NEED_SKILL: https://github.com/owner/repo>
  <NEED_SKILL: https://github.com/owner/repo | optional context hint>

Резултатът се инжектира обратно в LLM контекста под:
  ━━━ TOOL RESULTS — SKILL ANALYSIS ━━━
"""
from __future__ import annotations
from .paths import data_root

import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
_HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}

# Where acquired skill records are stored
DATA_ROOT = data_root()
SKILLS_LOG = DATA_ROOT / "acquired_skills.json"


def _gh_get(path: str, timeout: float = 10.0) -> dict[str, Any]:
    url = f"{GITHUB_API}{path}" if path.startswith("/") else path
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        r = client.get(url, headers=_HEADERS)
        if r.status_code == 404:
            return {"_error": "not found", "_status": 404}
        if r.status_code == 403:
            return {"_error": "rate limited or private repo", "_status": 403}
        r.raise_for_status()
        return r.json()


def _parse_github_url(url: str) -> tuple[str, str] | None:
    """Extract (owner, repo) from a GitHub URL."""
    import re
    url = url.strip().rstrip("/")
    m = re.search(r"github\.com/([^/\s]+)/([^/\s#?]+)", url)
    if not m:
        return None
    owner = m.group(1)
    repo = m.group(2).removesuffix(".git")
    return owner, repo


def _decode_content(raw: str) -> str:
    import base64
    try:
        return base64.b64decode(raw.replace("\n", "")).decode("utf-8", errors="replace")
    except Exception:
        return ""


def analyze_github_repo(url_and_hint: str) -> dict[str, Any]:
    """Fetch repo metadata, README, top-level file tree, and language breakdown.

    ``url_and_hint`` is the raw marker content, which may be:
      - ``https://github.com/owner/repo``
      - ``https://github.com/owner/repo | some context hint``
    """
    parts = url_and_hint.split("|", 1)
    url = parts[0].strip()
    hint = parts[1].strip() if len(parts) > 1 else ""

    parsed = _parse_github_url(url)
    if not parsed:
        return {"ok": False, "error": f"Cannot parse GitHub URL: {url!r}"}
    owner, repo = parsed
    slug = f"{owner}/{repo}"

    try:
        # 1. Repo metadata
        meta = _gh_get(f"/repos/{owner}/{repo}")
        if "_error" in meta:
            return {"ok": False, "error": meta["_error"], "url": url}

        # 2. Top-level file tree
        tree_data = _gh_get(f"/repos/{owner}/{repo}/contents/")
        tree: list[dict[str, str]] = []
        if isinstance(tree_data, list):
            for item in tree_data[:40]:
                tree.append({
                    "name": item.get("name", ""),
                    "type": item.get("type", ""),
                    "size": item.get("size", 0),
                })

        # 3. README
        readme_text = ""
        readme_data = _gh_get(f"/repos/{owner}/{repo}/contents/README.md")
        if "content" in readme_data:
            readme_text = _decode_content(readme_data["content"])[:3000]
        elif not readme_data.get("_error"):
            # try README.rst
            readme_data2 = _gh_get(f"/repos/{owner}/{repo}/contents/README.rst")
            if "content" in readme_data2:
                readme_text = _decode_content(readme_data2["content"])[:3000]

        # 4. Languages
        langs_data = _gh_get(f"/repos/{owner}/{repo}/languages")
        languages: dict[str, int] = langs_data if isinstance(langs_data, dict) else {}

        return {
            "ok": True,
            "slug": slug,
            "url": url,
            "hint": hint,
            "name": meta.get("name", repo),
            "description": meta.get("description") or "",
            "stars": meta.get("stargazers_count", 0),
            "forks": meta.get("forks_count", 0),
            "open_issues": meta.get("open_issues_count", 0),
            "license": (meta.get("license") or {}).get("name") or "none",
            "default_branch": meta.get("default_branch", "main"),
            "topics": meta.get("topics") or [],
            "languages": languages,
            "primary_language": meta.get("language") or "",
            "tree": tree,
            "readme": readme_text,
            "fetched_at": time.time(),
        }

    except Exception as exc:
        log.warning("skill_acquisition.analyze_github_repo failed for %s: %s", url, exc)
        return {"ok": False, "error": str(exc), "url": url}


def format_skill_analysis_for_prompt(analysis: dict[str, Any]) -> str:
    """Format the analysis into a string injected into the LLM follow-up prompt."""
    if not analysis.get("ok"):
        return f"[skill_analyze] FAILED: {analysis.get('error', 'unknown')}\nURL: {analysis.get('url', '?')}"

    lines = [
        f"[skill_analyze] SUCCESS — {analysis['slug']} ★{analysis['stars']}",
        f"  Description : {analysis['description'] or '(no description)'}",
        f"  Language    : {analysis['primary_language']} | all: {', '.join(analysis['languages'].keys())}",
        f"  License     : {analysis['license']}",
        f"  Topics      : {', '.join(analysis['topics']) or '—'}",
        f"  Forks       : {analysis['forks']}  Open issues: {analysis['open_issues']}",
    ]
    if analysis.get("hint"):
        lines.append(f"  Owner hint  : {analysis['hint']}")

    lines.append("\nTop-level file tree:")
    for item in analysis.get("tree", [])[:30]:
        icon = "📁" if item["type"] == "dir" else "📄"
        size = f"  ({item['size']} B)" if item["type"] == "file" and item.get("size") else ""
        lines.append(f"  {icon} {item['name']}{size}")

    if analysis.get("readme"):
        lines.append("\nREADME (first 2000 chars):")
        lines.append(analysis["readme"][:2000])

    lines.append(
        "\n\nBased on this analysis, produce a SKILL ACQUISITION PLAN for Nikolai in Bulgarian. "
        "Structure it as:\n"
        "1. Какво прави репото (2-3 изречения)\n"
        "2. Какво ново умение би ти дало (конкретни capability-та)\n"
        "3. Как ще го интегрираш в ADN (конкретни файлове, pattern, effort estimate)\n"
        "4. Какъв ще е ефектът за Iyveris (кои apps ще се възползват)\n"
        "5. Твоята препоръка (да/не и защо)\n"
        "6. Ако смяташ да го правиш — предложи да създадеш self_improve proposal "
        "(skill: edit_modules) и попитай за approve.\n"
        "Be concrete and honest about limitations. Communicate in Bulgarian."
    )

    return "\n".join(lines)


def log_skill_interest(slug: str, analysis: dict[str, Any]) -> None:
    """Record that Daedalus analyzed this repo (for later reference)."""
    try:
        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        existing: list[dict[str, Any]] = []
        if SKILLS_LOG.exists():
            try:
                existing = json.loads(SKILLS_LOG.read_text("utf-8"))
            except Exception:
                pass
        existing.append({
            "slug": slug,
            "url": analysis.get("url"),
            "description": analysis.get("description", ""),
            "stars": analysis.get("stars", 0),
            "primary_language": analysis.get("primary_language", ""),
            "analyzed_at": time.time(),
            "ok": analysis.get("ok", False),
        })
        SKILLS_LOG.write_text(json.dumps(existing[-50:], indent=2), "utf-8")
    except Exception as exc:
        log.debug("skill interest log failed: %s", exc)
