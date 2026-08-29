"""Agent Reach-inspired internet capability layer.

Upstream: https://github.com/Panniantong/Agent-Reach (MIT).

We do **not** vendor that CLI. Daedalus copies the *capability-layer* idea:
pick a working backend per channel, probe it, and fall back.

Zero-config defaults (same idea as Agent Reach ``channels/web.py``):
- Read a page: Jina Reader ``https://r.jina.ai/{url}`` — no API key.
- Search the web: DuckDuckGo HTML — no API key.
- Find Reddit threads: DuckDuckGo ``site:reddit.com`` — Reddit's anonymous
  JSON is blocked (Agent Reach, live-checked 2026). Logged-in ``rdt`` /
  ``DAEDALUS_REDDIT_COOKIE`` are optional owner-driven backends.

Optional PATH backends: ``agent-reach``, ``yt-dlp``, ``gh``, ``rdt``, ``mcporter``.
Do not ``pip install agent-reach`` from PyPI — that package is unrelated.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

log = logging.getLogger(__name__)

JINA_PREFIX = "https://r.jina.ai/"
_TIMEOUT = 25
_UA = (
    "Daedalus-Reach/0.4 (+https://gitlab.com/Blaue-Ente/deadalus-agent-0; "
    "coding-agent)"
)

# Idle gardener: two of these per tick, rotating. English queries hit more
# of the public web / Reddit; the journal is still written in the product.
GARDEN_QUERIES: tuple[dict[str, str], ...] = (
    {"channel": "web", "query": "open source coding agent 2026"},
    {"channel": "reddit", "query": "local LLM coding assistant", "subreddit": "LocalLLaMA"},
    {"channel": "web", "query": "Phaser Godot indie game engine tools"},
    {"channel": "reddit", "query": "2d 3d indie game jam tools", "subreddit": "gamedev"},
    {"channel": "web", "query": "agent memory RAG coding assistant"},
    {"channel": "reddit", "query": "self improving coding agent", "subreddit": "MachineLearning"},
    {"channel": "web", "query": "MCP model context protocol servers"},
    {"channel": "reddit", "query": "open source game AI NPC", "subreddit": "gamedev"},
)


def jina_reader_url(url: str) -> str:
    """Build the Jina Reader URL for a page. Idempotent if already a Jina URL."""
    raw = (url or "").strip()
    if not raw:
        raise ValueError("url required")
    if raw.startswith(JINA_PREFIX):
        return raw
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    return JINA_PREFIX + raw


def _which(name: str) -> str | None:
    return shutil.which(name)


def _run(argv: list[str], timeout: int = 20) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        out = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
        return {
            "ok": proc.returncode == 0,
            "exit_code": proc.returncode,
            "output": out[:8000],
            "cmd": argv[0],
        }
    except FileNotFoundError:
        return {"ok": False, "error": f"{argv[0]} not found", "cmd": argv[0]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout", "cmd": argv[0]}
    except Exception as extra:
        return {"ok": False, "error": str(extra)[:200], "cmd": argv[0]}


def _user_agent() -> str:
    custom = (os.environ.get("DAEDALUS_REDDIT_USER_AGENT") or "").strip()
    return custom or _UA


def _reddit_cookie() -> str:
    return (os.environ.get("DAEDALUS_REDDIT_COOKIE") or "").strip()


def normalize_subreddit(name: str) -> str:
    """``r/gamedev`` / ``/r/gamedev/`` → ``gamedev``."""
    text = (name or "").strip().strip("/")
    if text.lower().startswith("r/"):
        text = text[2:]
    return "".join(ch for ch in text if ch.isalnum() or ch in "_-")[:80]


def reddit_search_url(query: str, *, subreddit: str = "", limit: int = 8) -> str:
    """old.reddit.com JSON search URL (needs a logged-in cookie in practice)."""
    q = (query or "").strip()
    params = {
        "q": q,
        "limit": str(max(1, min(int(limit or 8), 25))),
        "sort": "relevance",
        "t": "year",
        "raw_json": "1",
    }
    sub = normalize_subreddit(subreddit)
    if sub:
        params["restrict_sr"] = "1"
        return f"https://old.reddit.com/r/{sub}/search.json?{urlencode(params)}"
    return f"https://old.reddit.com/search.json?{urlencode(params)}"


def reddit_web_query(query: str, *, subreddit: str = "") -> str:
    """DuckDuckGo query that finds Reddit threads without Reddit login."""
    q = (query or "").strip()
    sub = normalize_subreddit(subreddit)
    if sub:
        return f"site:reddit.com/r/{sub} {q}".strip()
    return f"site:reddit.com {q}".strip()


def infer_channel(query: str, channel: str = "auto") -> str:
    ch = (channel or "auto").strip().lower()
    if ch in ("reddit", "web"):
        return ch
    q = (query or "").lower()
    if "reddit.com" in q or q.startswith("r/") or " r/" in f" {q}" or "reddit" in q:
        return "reddit"
    return "web"


def rotate_garden_queries(cursor: int, n: int = 2) -> tuple[list[dict[str, str]], int]:
    total = len(GARDEN_QUERIES)
    if total == 0 or n <= 0:
        return [], 0
    start = int(cursor or 0) % total
    count = min(int(n), total)
    picked = [dict(GARDEN_QUERIES[(start + i) % total]) for i in range(count)]
    return picked, (start + count) % total


def _rdt_credential_present() -> bool:
    path = Path.home() / ".config" / "rdt-cli" / "credential.json"
    try:
        return path.is_file() and path.stat().st_size > 2
    except OSError:
        return False


def _reddit_channel_status() -> dict[str, Any]:
    rdt = _which("rdt")
    cookie = bool(_reddit_cookie())
    if rdt:
        backend = "rdt-cli"
        note = (
            "rdt on PATH. Search uses the owner's saved Reddit session "
            "(~/.config/rdt-cli). Fallback: DuckDuckGo site:reddit.com."
        )
        if not _rdt_credential_present() and not cookie:
            note = (
                "rdt on PATH but no credential.json — run `rdt login` on the "
                "owner machine, or set DAEDALUS_REDDIT_COOKIE. "
                "Until then: DuckDuckGo site:reddit.com."
            )
    elif cookie:
        backend = "reddit-cookie"
        note = "DAEDALUS_REDDIT_COOKIE set — old.reddit.com JSON, then DDG fallback."
    else:
        backend = "duckduckgo-site"
        note = (
            "Anonymous Reddit JSON is blocked. Default: DuckDuckGo "
            "site:reddit.com. Optional: install rdt-cli, or set "
            "DAEDALUS_REDDIT_COOKIE from Cookie-Editor (reddit_session). "
            "Cookies stay on this machine."
        )
    return {
        "backend": backend,
        "ready": True,
        "note": note,
        "rdt": bool(rdt),
        "cookie": cookie,
        "native_json": False,
    }


def reach_status() -> dict[str, Any]:
    """Doctor-style probe: which channels are actually available on this machine."""
    cli = _which("agent-reach")
    doctor: dict[str, Any] | None = None
    if cli:
        doctor = _run([cli, "doctor"], timeout=40)
    mcporter = _which("mcporter")
    channels = {
        "web": {
            "backend": "jina-reader",
            "ready": True,
            "note": "https://r.jina.ai/{url} — no key",
        },
        "search": {
            "backend": "duckduckgo",
            "ready": True,
            "note": "html.duckduckgo.com — no key. Exa/mcporter is optional.",
        },
        "reddit": _reddit_channel_status(),
        "youtube": {
            "backend": "yt-dlp" if _which("yt-dlp") else None,
            "ready": bool(_which("yt-dlp")),
            "note": "install yt-dlp for captions; or `agent-reach`",
        },
        "github": {
            "backend": "gh" if _which("gh") else None,
            "ready": bool(_which("gh")),
            "note": "install GitHub CLI for private repos / issues",
        },
        "rss": {
            "backend": "stdlib-xml",
            "ready": True,
            "note": "RSS/Atom via stdlib",
        },
        "exa": {
            "backend": "mcporter" if mcporter else None,
            "ready": False,
            "note": (
                "optional Exa MCP via mcporter — Agent Reach search upgrade. "
                "DuckDuckGo stays the zero-config default."
            ),
        },
        "agent-reach": {
            "backend": cli,
            "ready": bool(cli),
            "note": (
                "optional CLI: https://github.com/Panniantong/Agent-Reach "
                "(not the unrelated PyPI package)"
            ),
        },
    }
    return {
        "ok": True,
        "source": "https://github.com/Panniantong/Agent-Reach",
        "license": "MIT (upstream); Daedalus adapter is original code",
        "channels": channels,
        "doctor": doctor,
    }


async def web_read(url: str, *, max_chars: int = 8000) -> dict[str, Any]:
    """Read a page as markdown via Jina, falling back to the existing fetch tool."""
    target = jina_reader_url(url)
    text = ""
    err = ""
    try:
        import httpx
        headers = {"User-Agent": _user_agent()}
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(target, headers=headers)
            if resp.status_code < 400 and (resp.text or "").strip():
                text = resp.text
            else:
                err = f"jina HTTP {resp.status_code}"
    except Exception as extra:
        err = str(extra)[:200]

    if not text:
        try:
            from .tools import web_fetch_tool
            r = await web_fetch_tool(url if url.startswith("http") else "https://" + url, max_chars=max_chars)
            if r.get("ok"):
                return {
                    "ok": True,
                    "url": url,
                    "backend": "web_fetch",
                    "jina_url": target,
                    "text": (r.get("text") or "")[:max_chars],
                    "fallback_from": err or "jina empty",
                }
            err = (err + " / " + str(r.get("error") or "fetch failed")).strip(" /")
        except Exception as extra:
            err = (err + " / " + str(extra)[:160]).strip(" /")
        return {"ok": False, "url": url, "jina_url": target, "error": err or "unreadable"}

    return {
        "ok": True,
        "url": url,
        "backend": "jina",
        "jina_url": target,
        "text": text[:max_chars],
    }


def youtube_captions(url: str, *, max_chars: int = 6000) -> dict[str, Any]:
    """Extract title + auto captions via yt-dlp when installed."""
    ytdlp = _which("yt-dlp")
    if not ytdlp:
        return {
            "ok": False,
            "error": "yt-dlp not installed",
            "hint": "pip install yt-dlp  — or install Agent Reach",
        }
    meta = _run([ytdlp, "--skip-download", "--print", "%(title)s", "--print", "%(id)s", url], timeout=40)
    caps = _run(
        [ytdlp, "--skip-download", "--write-auto-sub", "--sub-lang", "en.*,bg.*,en",
         "--sub-format", "vtt", "--print", "%(requested_subtitles)s", url],
        timeout=60,
    )
    body = (meta.get("output") or "") + "\n" + (caps.get("output") or "")
    return {
        "ok": bool(meta.get("ok") or caps.get("ok")),
        "backend": "yt-dlp",
        "text": body[:max_chars],
        "error": None if (meta.get("ok") or caps.get("ok")) else (caps.get("error") or meta.get("error")),
    }


def github_view(repo: str) -> dict[str, Any]:
    """``gh repo view`` when the official CLI is present."""
    gh = _which("gh")
    if not gh:
        return {"ok": False, "error": "gh CLI not installed", "hint": "https://cli.github.com"}
    return _run([gh, "repo", "view", repo, "--json", "name,description,url,defaultBranchRef"], timeout=20)


def read_rss(url: str, *, limit: int = 8) -> dict[str, Any]:
    """Parse RSS/Atom with stdlib XML (Agent Reach rss channel analogue)."""
    import xml.etree.ElementTree as ET
    from urllib.request import Request, urlopen

    raw_url = (url or "").strip()
    if not raw_url.startswith(("http://", "https://")):
        return {"ok": False, "error": "http(s) URL required"}
    try:
        req = Request(raw_url, headers={"User-Agent": _user_agent()})
        with urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310 — http(s) only, checked above
            xml_bytes = resp.read(400_000)
    except Exception as extra:
        return {"ok": False, "error": str(extra)[:200]}
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as extra:
        return {"ok": False, "error": f"not rss/atom: {extra}"}

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items: list[dict[str, str]] = []
    for item in root.findall(".//item")[:limit]:
        items.append({
            "title": (item.findtext("title") or "").strip(),
            "link": (item.findtext("link") or "").strip(),
        })
    if not items:
        for entry in root.findall("atom:entry", ns)[:limit] or root.findall("{http://www.w3.org/2005/Atom}entry")[:limit]:
            link_el = entry.find("{http://www.w3.org/2005/Atom}link")
            href = (link_el.get("href") if link_el is not None else "") or ""
            title_el = entry.find("{http://www.w3.org/2005/Atom}title")
            items.append({
                "title": (title_el.text or "").strip() if title_el is not None else "",
                "link": href,
            })
    return {"ok": True, "backend": "rss", "url": raw_url, "items": items}


def looks_like_rss(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(tok in path for tok in ("/rss", "/feed", ".xml", "/atom")) or "rss" in url.lower()


def _clip_hits(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows[: max(1, min(int(limit or 6), 15))]:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        url = str(row.get("url") or row.get("link") or "").strip()
        snippet = str(row.get("snippet") or row.get("selftext") or row.get("body") or "").strip()
        item = {
            "title": title,
            "url": url,
            "snippet": snippet[:280],
        }
        sub = str(row.get("subreddit") or "").strip()
        if sub:
            item["subreddit"] = normalize_subreddit(sub) or sub
        out.append(item)
    return out


async def web_search_reach(query: str, *, limit: int = 6) -> dict[str, Any]:
    """Zero-config web search (DuckDuckGo HTML). Does not record discoveries."""
    q = (query or "").strip()
    if not q:
        return {"ok": False, "channel": "web", "error": "empty query", "results": []}
    try:
        from .web_search_standalone import web_search as ddg
        result = await ddg(q, limit=max(1, min(int(limit or 6), 15)))
    except Exception as extra:
        return {
            "ok": False,
            "channel": "web",
            "backend": "duckduckgo",
            "query": q,
            "error": str(extra)[:240],
            "results": [],
        }
    hits = _clip_hits(list(result.get("results") or []), limit)
    return {
        "ok": True,
        "channel": "web",
        "backend": str(result.get("source") or "duckduckgo"),
        "query": q,
        "results": hits,
        "result_count": len(hits),
    }


def _hits_from_rdt(payload: Any) -> list[dict[str, Any]]:
    rows: list[Any] = []
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            rows = data
        elif isinstance(data, dict):
            for key in ("posts", "results", "items", "children"):
                if isinstance(data.get(key), list):
                    rows = data[key]
                    break
        if not rows:
            for key in ("posts", "results", "items"):
                if isinstance(payload.get(key), list):
                    rows = payload[key]
                    break
    hits: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        inner = row.get("data") if isinstance(row.get("data"), dict) else row
        if not isinstance(inner, dict):
            continue
        title = str(inner.get("title") or inner.get("name") or "")
        url = str(inner.get("url") or inner.get("permalink") or inner.get("link") or "")
        if url.startswith("/"):
            url = "https://www.reddit.com" + url
        snippet = str(
            inner.get("selftext")
            or inner.get("body")
            or inner.get("snippet")
            or inner.get("text")
            or ""
        )[:280]
        if title or url:
            hits.append({
                "title": title,
                "url": url,
                "snippet": snippet,
                "subreddit": str(inner.get("subreddit") or ""),
            })
    return hits


def _reddit_via_rdt(query: str, *, subreddit: str = "", limit: int = 8) -> dict[str, Any] | None:
    rdt = _which("rdt")
    if not rdt:
        return None
    argv = [rdt, "search", query, "--json", "--compact", "-n", str(max(1, min(int(limit or 8), 15)))]
    sub = normalize_subreddit(subreddit)
    if sub:
        argv.extend(["-r", sub])
    raw = _run(argv, timeout=40)
    if not raw.get("ok"):
        return {
            "ok": False,
            "backend": "rdt-cli",
            "error": (raw.get("error") or raw.get("output") or "rdt failed")[:240],
            "results": [],
        }
    text = (raw.get("output") or "").strip()
    payload: Any = None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        for line in reversed(text.splitlines()):
            line = line.strip()
            if line.startswith("{") or line.startswith("["):
                try:
                    payload = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
    hits = _clip_hits(_hits_from_rdt(payload), limit) if payload is not None else []
    if not hits:
        return {
            "ok": False,
            "backend": "rdt-cli",
            "error": "rdt returned no posts",
            "results": [],
        }
    return {
        "ok": True,
        "backend": "rdt-cli",
        "results": hits,
        "result_count": len(hits),
    }


def _hits_from_reddit_listing(payload: Any) -> list[dict[str, Any]]:
    children: list[Any] = []
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict) and isinstance(data.get("children"), list):
            children = data["children"]
    hits: list[dict[str, Any]] = []
    for row in children:
        if not isinstance(row, dict):
            continue
        inner = row.get("data") if isinstance(row.get("data"), dict) else {}
        title = str(inner.get("title") or "")
        permalink = str(inner.get("permalink") or "")
        url = str(inner.get("url") or "")
        if permalink.startswith("/"):
            thread = "https://www.reddit.com" + permalink
        else:
            thread = url
        snippet = str(inner.get("selftext") or "")[:280]
        if title or thread:
            hits.append({
                "title": title,
                "url": thread or url,
                "snippet": snippet,
                "subreddit": str(inner.get("subreddit") or ""),
            })
    return hits


async def _reddit_via_json(query: str, *, subreddit: str = "", limit: int = 8) -> dict[str, Any] | None:
    cookie = _reddit_cookie()
    if not cookie:
        return None
    import httpx

    url = reddit_search_url(query, subreddit=subreddit, limit=limit)
    headers = {
        "User-Agent": _user_agent(),
        "Cookie": cookie if cookie.lower().startswith("reddit_session=") else f"reddit_session={cookie}",
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code >= 400:
                return {
                    "ok": False,
                    "backend": "reddit-cookie",
                    "error": f"reddit JSON HTTP {resp.status_code}",
                    "results": [],
                }
            payload = resp.json()
    except Exception as extra:
        return {
            "ok": False,
            "backend": "reddit-cookie",
            "error": str(extra)[:240],
            "results": [],
        }
    hits = _clip_hits(_hits_from_reddit_listing(payload), limit)
    if not hits:
        return {
            "ok": False,
            "backend": "reddit-cookie",
            "error": "reddit JSON empty",
            "results": [],
        }
    return {
        "ok": True,
        "backend": "reddit-cookie",
        "results": hits,
        "result_count": len(hits),
    }


async def reddit_search(query: str, *, subreddit: str = "", limit: int = 8) -> dict[str, Any]:
    """Find Reddit threads. Login backends first, DuckDuckGo site: filter last."""
    q = (query or "").strip()
    if not q:
        return {"ok": False, "channel": "reddit", "error": "empty query", "results": []}
    attempts: list[str] = []

    rdt_hit = _reddit_via_rdt(q, subreddit=subreddit, limit=limit)
    if rdt_hit is not None:
        attempts.append("rdt-cli")
        if rdt_hit.get("ok") and rdt_hit.get("results"):
            return {"channel": "reddit", "query": q, "subreddit": normalize_subreddit(subreddit), **rdt_hit}
        log.info("reach: rdt search failed (%s), falling back", rdt_hit.get("error"))

    json_hit = await _reddit_via_json(q, subreddit=subreddit, limit=limit)
    if json_hit is not None:
        attempts.append("reddit-cookie")
        if json_hit.get("ok") and json_hit.get("results"):
            return {"channel": "reddit", "query": q, "subreddit": normalize_subreddit(subreddit), **json_hit}
        log.info("reach: reddit JSON failed (%s), falling back", json_hit.get("error"))

    ddg_q = reddit_web_query(q, subreddit=subreddit)
    web = await web_search_reach(ddg_q, limit=limit)
    attempts.append("duckduckgo-site")
    hits = _clip_hits(list(web.get("results") or []), limit)
    return {
        "ok": bool(web.get("ok")) and bool(hits),
        "channel": "reddit",
        "backend": "duckduckgo-site",
        "query": q,
        "subreddit": normalize_subreddit(subreddit),
        "ddg_query": ddg_q,
        "attempts": attempts,
        "results": hits,
        "result_count": len(hits),
        "error": None if hits else (web.get("error") or "no reddit hits"),
        "note": (
            "Anonymous Reddit JSON is blocked; these are public threads "
            "found via DuckDuckGo. Owner login (rdt / cookie) unlocks native Reddit."
        ),
    }


async def reach_search(
    query: str,
    *,
    channel: str = "auto",
    subreddit: str = "",
    limit: int = 6,
) -> dict[str, Any]:
    """Search web or Reddit through the Reach capability layer."""
    q = (query or "").strip()
    if not q:
        return {"ok": False, "error": "empty query", "results": []}
    chosen = infer_channel(q, channel)
    if chosen == "reddit":
        return await reddit_search(q, subreddit=subreddit, limit=limit)
    return await web_search_reach(q, limit=limit)


def urls_from_reach(payload: dict[str, Any] | list[dict[str, Any]] | None) -> list[str]:
    """Collect http(s) URLs from one search result or a list of them."""
    blobs: list[Any] = []
    if isinstance(payload, list):
        blobs.extend(payload)
    elif isinstance(payload, dict):
        blobs.append(payload)
        blobs.extend(payload.get("results") or [])
        for row in payload.get("searches") or []:
            blobs.append(row)
            if isinstance(row, dict):
                blobs.extend(row.get("results") or [])
    seen: set[str] = set()
    out: list[str] = []
    for item in blobs:
        candidates: list[str] = []
        if isinstance(item, str):
            candidates.append(item)
        elif isinstance(item, dict):
            for key in ("url", "link", "href"):
                val = item.get(key)
                if val:
                    candidates.append(str(val))
        for raw in candidates:
            url = raw.strip()
            if url.startswith("http://") or url.startswith("https://"):
                if url not in seen:
                    seen.add(url)
                    out.append(url)
    return out
