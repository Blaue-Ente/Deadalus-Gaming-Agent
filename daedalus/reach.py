"""Agent Reach-inspired internet capability layer.

Upstream: https://github.com/Panniantong/Agent-Reach (MIT).

We do **not** vendor that CLI. Daedalus copies the *capability-layer* idea:
pick a working backend per channel, probe it, and fall back.

Zero-config default (same as Agent Reach ``channels/web.py``):
Jina Reader ``https://r.jina.ai/{url}`` — no API key.

Optional backends if the owner installed them on PATH:
``agent-reach``, ``yt-dlp``, ``gh``, ``curl``.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from typing import Any
from urllib.parse import urlparse

log = logging.getLogger(__name__)

JINA_PREFIX = "https://r.jina.ai/"
_TIMEOUT = 25


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
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200], "cmd": argv[0]}


def reach_status() -> dict[str, Any]:
    """Doctor-style probe: which channels are actually available on this machine."""
    chromium_for_vital = ""
    try:
        from .playground_browser import chromium_executable
        chromium_for_vital = chromium_executable() or ""
    except Exception:
        chromium_for_vital = ""
    cli = _which("agent-reach")
    doctor: dict[str, Any] | None = None
    if cli:
        doctor = _run([cli, "doctor"], timeout=40)
    channels = {
        "web": {
            "backend": "jina-reader",
            "ready": True,
            "note": "https://r.jina.ai/{url} — no key",
        },
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
        "agent-reach": {
            "backend": cli,
            "ready": bool(cli),
            "note": "optional CLI: https://github.com/Panniantong/Agent-Reach",
        },
        "chromium": {
            "backend": chromium_for_vital or None,
            "ready": bool(chromium_for_vital),
            "note": (
                "Render-and-See / visual proof. Install: pip install -e '.[playwright]' "
                "then python -m playwright install chromium, or set DAEDALUS_CHROMIUM."
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
        headers = {"User-Agent": "Daedalus-Reach/0.3 (+https://gitlab.com/Blaue-Ente/deadalus-agent-0)"}
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(target, headers=headers)
            if resp.status_code < 400 and (resp.text or "").strip():
                text = resp.text
            else:
                err = f"jina HTTP {resp.status_code}"
    except Exception as exc:
        err = str(exc)[:200]

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
        except Exception as exc:
            err = (err + " / " + str(exc)[:160]).strip(" /")
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
        req = Request(raw_url, headers={"User-Agent": "Daedalus-Reach/0.3"})
        with urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310 — http(s) only, checked above
            xml_bytes = resp.read(400_000)
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        return {"ok": False, "error": f"not rss/atom: {exc}"}

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
