"""DuckDuckGo HTML search — used when ADN ``web_search`` is not importable."""
from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import quote_plus, unquote

import httpx

log = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_HREF = re.compile(r'uddg=([^&"]+)', re.I)
_TITLE = re.compile(r'class="result__a"[^>]*>(.*?)</a>', re.I | re.S)
_SNIP = re.compile(r'class="result__snippet"[^>]*>(.*?)</(?:a|td|div)>', re.I | re.S)
_TAG = re.compile(r"<[^>]+>")


class WebSearchError(Exception):
    pass


class WebSearchBadRequest(WebSearchError):
    pass


class GithubFinderError(Exception):
    pass


class GithubFinderBadRequest(GithubFinderError):
    pass


def _clean(html: str) -> str:
    return re.sub(r"\s+", " ", _TAG.sub(" ", html)).strip()


async def web_search(query: str, limit: int = 5, fetch_content: bool = False) -> dict[str, Any]:
    q = (query or "").strip()
    if not q:
        raise WebSearchBadRequest("empty query")
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(q)}"
    try:
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True, headers={"User-Agent": _UA}) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
    except Exception as exc:
        raise WebSearchError(str(exc)[:300]) from exc

    titles = _TITLE.findall(html)
    snippets = _SNIP.findall(html)
    hrefs = _HREF.findall(html)
    results: list[dict[str, str]] = []
    for i, title_html in enumerate(titles[: max(1, limit)]):
        href = unquote(hrefs[i]) if i < len(hrefs) else ""
        snip = _clean(snippets[i]) if i < len(snippets) else ""
        results.append({
            "title": _clean(title_html),
            "url": href,
            "snippet": snip,
        })
    return {
        "ok": True,
        "source": "duckduckgo",
        "query": q,
        "result_count": len(results),
        "results": results,
    }


async def github_find_skill(
    query: str,
    language: str = "",
    limit: int = 5,
    min_stars: int = 0,
) -> dict[str, Any]:
    q = (query or "").strip()
    if not q:
        raise GithubFinderBadRequest("empty query")
    parts = [q]
    if language:
        parts.append(f"language:{language}")
    if min_stars:
        parts.append(f"stars:>={min_stars}")
    try:
        async with httpx.AsyncClient(timeout=12.0, headers={"User-Agent": "Daedalus-agent"}) as client:
            resp = await client.get(
                "https://api.github.com/search/repositories",
                params={"q": " ".join(parts), "per_page": max(1, min(limit, 10))},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        raise GithubFinderError(str(exc)[:300]) from exc
    items = []
    for repo in data.get("items") or []:
        items.append({
            "title": repo.get("full_name") or repo.get("name"),
            "url": repo.get("html_url") or "",
            "snippet": repo.get("description") or "",
            "stars": repo.get("stargazers_count") or 0,
        })
    return {"ok": True, "source": "github", "result_count": len(items), "results": items}
