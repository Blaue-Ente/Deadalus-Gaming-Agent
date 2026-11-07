"""Daedalus tools — superpowers wired to existing ADN modules.

Wraps the pre-existing `web_search` (DuckDuckGo), `github_finder`, and the
new PostgreSQL, Object Storage, GitHub connector integrations, and coding tools
so Daedalus can:
  - Search the public internet and GitHub
  - Query the ADN PostgreSQL database (mesh stats, discoveries)
  - Backup ChromaDB to Object Storage
  - Search repos / create GitHub issues
  - Search PyPI / npm packages with version + CVE info
  - Fetch documentation pages as clean text
  - View git history (log + diff) of the ADN repo
  - Run Python snippets in a sandboxed subprocess to verify logic

Discovery approval policy (HYBRID):
  - confidence >= AUTO_APPROVE_THRESHOLD AND has visible source URL
      -> stored as `approved` + also captured as a lesson immediately
  - everything else
      -> stored as `pending` for owner approval via API/UI

No write/exec, no shell, no arbitrary file I/O. Phase 3 territory remains gated.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def _web_search_impl():
    try:
        from argos_data_nexus.modules.web_search import (  # type: ignore
            web_search,
            WebSearchError,
            WebSearchBadRequest,
        )
        return web_search, WebSearchError, WebSearchBadRequest
    except ImportError:
        from .web_search_standalone import (
            web_search,
            WebSearchError,
            WebSearchBadRequest,
        )
        return web_search, WebSearchError, WebSearchBadRequest


def _github_finder_impl():
    try:
        from argos_data_nexus.modules.github_finder import (  # type: ignore
            find_skill,
            GithubFinderError,
            GithubFinderBadRequest,
        )
        return find_skill, GithubFinderError, GithubFinderBadRequest
    except ImportError:
        from .web_search_standalone import (
            github_find_skill,
            GithubFinderError,
            GithubFinderBadRequest,
        )
        return github_find_skill, GithubFinderError, GithubFinderBadRequest


# ---------------------------------------------------------------------------
# PostgreSQL query tool
# ---------------------------------------------------------------------------

async def postgres_query_tool(query: str, session_id: str | None = None) -> dict[str, Any]:
    """Run a read-only SQL query against the ADN PostgreSQL database.

    Uses the SQLGuard (improvements.sql_guard_execute) for:
      • Multi-statement auto-split (run `SELECT a; SELECT b;` correctly)
      • Schema-aware column validation (catches `column does not exist`
        before hitting the DB and gives Дедал the real column list)
      • Curated-table INSERT detection → redirects to NEED_DATA_PROPOSAL
      • Pre-execute read-first check (when session_id provided): blocks
        SQL against tables the session has never read schema for.
    """
    q = query.strip()
    if not q:
        return {"ok": False, "error": "empty query"}

    import re as _re
    q_upper = q.upper()

    # ── Curated-table INSERT/UPDATE/DELETE → guide to NEED_DATA_PROPOSAL ──
    from .improvements import (
        proposal_enforcement_check,
        sql_guard_execute,
        pre_execute_read_first_check,
    )
    pe = proposal_enforcement_check(q)
    if pe:
        log.info("postgres_query: blocked curated-table mutation → %s", pe["table"])
        return pe

    # ── Pre-execute read-first: forbid querying tables w/o NEED_DB_SCHEMA ──
    if session_id:
        rf = pre_execute_read_first_check(session_id, q)
        if rf:
            log.info("postgres_query: read-first block → %s (session=%s)",
                     rf["missing_tables"], session_id[:10])
            return rf

    from .outbox import check_permission

    is_read = q_upper.startswith("SELECT") or q_upper.startswith("WITH")
    if not is_read:
        if not check_permission("execute_raw_sql"):
            return {
                "ok": False,
                "error": "Only SELECT/WITH queries are allowed via NEED_PG without "
                         "execute_raw_sql permission. For curated tables use "
                         "NEED_DATA_PROPOSAL.",
            }
        destructive = ("DROP", "TRUNCATE", "ALTER", "GRANT", "REVOKE")
        destructive_re = _re.compile(r"\b(" + "|".join(destructive) + r")\b")
        hit = destructive_re.search(q_upper)
        if hit:
            return {"ok": False, "error": f"Query contains blocked keyword: {hit.group(1)}"}

        from .host import get_pool

        async def _exec_write(stmt: str) -> dict[str, Any]:
            try:
                pool = await get_pool()
                async with pool.acquire() as conn:
                    status = await conn.execute(stmt)
                    return {
                        "ok": True,
                        "row_count": 0,
                        "rows": [],
                        "query": stmt,
                        "status": status,
                    }
            except Exception as exc:
                return {"ok": False, "error": str(exc), "query": stmt}

        return await sql_guard_execute(q, _exec_write)

    # Block DML keywords inside SELECT (CTE INSERT etc.)
    blocked = ("DROP", "DELETE", "UPDATE", "INSERT", "TRUNCATE", "ALTER", "GRANT", "REVOKE")
    blocked_re = _re.compile(r"\b(" + "|".join(blocked) + r")\b")
    hit = blocked_re.search(q_upper)
    if hit:
        return {"ok": False, "error": f"Query contains blocked keyword: {hit.group(1)}"}

    from .host import get_pool

    async def _exec_one(stmt: str) -> dict[str, Any]:
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch(stmt)
                result = [dict(r) for r in rows]
                return {"ok": True, "row_count": len(result),
                        "rows": result[:50], "query": stmt}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "query": stmt}

    res = await sql_guard_execute(q, _exec_one)
    if res.get("ok"):
        log.info("postgres_query: %d rows / %d stmt | %s",
                 res.get("row_count", 0), res.get("statements", 1), q[:200])
    else:
        log.warning("postgres_query FAILED: %s | query=%s",
                    res.get("error"), q[:200])
    return res


async def query_mesh_stats_tool(days: int = 7) -> dict[str, Any]:
    """Return aggregated mesh API call stats for the last N days."""
    try:
        from argos_data_nexus.db import query_mesh_stats
        return await query_mesh_stats(days=days)
    except Exception as exc:
        return {"days": days, "rows": [], "error": str(exc)}


# ---------------------------------------------------------------------------
# Object Storage tools
# ---------------------------------------------------------------------------

async def backup_chromadb_tool(trigger: str = "daedalus") -> dict[str, Any]:
    """Backup the ChromaDB database to Object Storage (GCS).

    Returns the GCS object path and size. Logs the backup to PostgreSQL.
    """
    import asyncio
    try:
        from argos_data_nexus.storage_service import backup_chromadb, is_configured
        if not is_configured():
            return {"ok": False, "error": "Object Storage not configured (DEFAULT_OBJECT_STORAGE_BUCKET_ID missing)"}
        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: backup_chromadb(trigger=trigger)
        )
        if result.get("ok"):
            try:
                from argos_data_nexus.db import log_chromadb_backup
                await log_chromadb_backup(
                    result["object_path"],
                    size_bytes=result.get("size_bytes"),
                    trigger=trigger,
                )
            except Exception:
                pass
        return result
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def list_storage_objects_tool(prefix: str = "") -> dict[str, Any]:
    """List files stored in Object Storage with an optional prefix filter."""
    import asyncio
    try:
        from argos_data_nexus.storage_service import list_objects, is_configured
        if not is_configured():
            return {"ok": False, "error": "Object Storage not configured"}
        objects = await asyncio.get_event_loop().run_in_executor(
            None, lambda: list_objects(prefix)
        )
        return {"ok": True, "count": len(objects), "objects": objects[:30]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# GitHub tools (via Replit OAuth connector)
# ---------------------------------------------------------------------------

def _run_sync(fn, *args, **kwargs):
    """Run a sync GitHub call in a thread pool."""
    import asyncio, functools
    loop = asyncio.get_event_loop()
    return loop.run_in_executor(None, functools.partial(fn, *args, **kwargs))


async def github_search_repos_tool(query: str, limit: int = 5) -> dict[str, Any]:
    """Search GitHub repositories using the OAuth connector.

    Use this for finding relevant repos — it has access to private repos too.
    """
    try:
        from .host import github
        ghc = github()
        if not ghc.is_configured():
            return {"ok": False, "error": "GitHub connector not configured"}
        repos = await _run_sync(ghc.search_repos, query, limit)
        return {"ok": True, "query": query, "repos": repos}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def github_list_my_repos_tool(limit: int = 15) -> dict[str, Any]:
    """List the authenticated user's GitHub repositories."""
    try:
        from .host import github
        ghc = github()
        if not ghc.is_configured():
            return {"ok": False, "error": "GitHub connector not configured"}
        user = await _run_sync(ghc.get_authenticated_user)
        repos = await _run_sync(ghc.list_user_repos, limit)
        return {"ok": True, "user": user.get("login", ""), "repos": repos}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def github_create_issue_tool(
    owner: str,
    repo: str,
    title: str,
    body: str = "",
    labels: list[str] | None = None,
) -> dict[str, Any]:
    """Create a GitHub issue on behalf of the owner.

    IMPORTANT: Always confirm with the owner before creating issues.
    """
    try:
        from .host import github
        ghc = github()
        if not ghc.is_configured():
            return {"ok": False, "error": "GitHub connector not configured"}
        result = await _run_sync(ghc.create_issue, owner, repo, title, body=body, labels=labels)
        return {
            "ok": True,
            "issue_number": result.get("number"),
            "url": result.get("html_url") or result.get("url") or "",
            "title": result.get("title", ""),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def github_create_pr_tool(
    owner: str,
    repo: str,
    title: str,
    *,
    head: str,
    base: str = "main",
    body: str = "",
) -> dict[str, Any]:
    """Open a GitHub pull request on behalf of the owner."""
    try:
        from .host import github
        ghc = github()
        if not ghc.is_configured():
            return {"ok": False, "error": "GitHub connector not configured"}
        result = await _run_sync(
            ghc.create_pull_request,
            owner, repo, title=title, head=head, base=base, body=body,
        )
        return {
            "ok": True,
            "number": result.get("number"),
            "url": result.get("html_url") or result.get("url") or "",
            "title": result.get("title", ""),
            "existed": result.get("existed"),
        }
    except Exception as extra:
        return {"ok": False, "error": str(extra)}


async def github_list_issues_tool(owner: str, repo: str, state: str = "open") -> dict[str, Any]:
    """List issues for a GitHub repository."""
    try:
        from .host import github
        ghc = github()
        if not ghc.is_configured():
            return {"ok": False, "error": "GitHub connector not configured"}
        issues = await _run_sync(ghc.list_repo_issues, owner, repo, state)
        return {"ok": True, "repo": f"{owner}/{repo}", "issues": issues}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def mesh_call_tool(
    target: str,
    action: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Call a mesh-registered Iyveris service directly from Daedalus.

    Safe to use for read-only actions (ping, capabilities, ask, forecast, catalyst).
    Destructive or write actions require explicit owner approval before calling.
    """
    try:
        from argos_data_nexus import mesh  # type: ignore
    except Exception as exc:
        return {"ok": False, "error": f"mesh module unavailable: {exc}"}

    try:
        reg = mesh._load_registry()
        svc = reg.get("services", {}).get(target)
        if not svc:
            available = sorted(reg.get("services", {}).keys())
            return {"ok": False, "error": f"Service '{target}' not found. Available: {available}"}
        if not svc.get("enabled", True):
            return {"ok": False, "error": f"Service '{target}' is disabled"}
        result = await mesh._call_service(
            svc, action, payload or {}, from_service="daedalus", timeout=timeout
        )
        return {"ok": True, "target": target, "action": action, "result": result}
    except Exception as exc:
        log.warning("mesh_call_tool %s.%s failed: %s", target, action, exc)
        return {"ok": False, "target": target, "action": action, "error": str(exc)}

AUTO_APPROVE_THRESHOLD = 0.85
DEFAULT_WEB_LIMIT = 5
DEFAULT_GH_LIMIT = 5


async def web_search_tool(
    memory,  # DaedalusMemory
    query: str,
    limit: int = DEFAULT_WEB_LIMIT,
    fetch_content: bool = False,
    record_discoveries: bool = True,
    for_self: bool = False,
) -> dict[str, Any]:
    """Run a DuckDuckGo search and optionally record results as discoveries.

    When ``for_self`` is True, ALL results are forced to ``pending`` regardless
    of confidence — improvements meant for Daedalus himself must never be
    auto-approved without owner sign-off.
    """
    _web_search, WebSearchError, WebSearchBadRequest = _web_search_impl()
    try:
        result = await _web_search(query, limit=limit, fetch_content=fetch_content)
    except WebSearchBadRequest as exc:
        return {"ok": False, "error": f"bad_request: {exc}", "results": []}
    except WebSearchError as exc:
        return {"ok": False, "error": f"web_search_failed: {exc}", "results": []}

    discoveries_recorded = 0
    if record_discoveries:
        # Score the top result a bit higher; everything else is pending by default
        for idx, r in enumerate(result.get("results", []) or []):
            url = r.get("url") or ""
            title = r.get("title") or ""
            if not url or not title:
                continue
            if memory.find_discovery_by_url(url):
                continue
            # Confidence drops with rank; top-1 from DDG is rarely > 0.7 without
            # owner approval, so most go to `pending`.
            conf = max(0.4, 0.75 - (idx * 0.1))
            status, lesson_id = _decide_status(conf, url, for_self=for_self)
            did = memory.capture_discovery(
                title=title,
                url=url,
                source="duckduckgo",
                why_useful=(r.get("snippet") or r.get("content_preview") or title)[:400],
                status=status,
                confidence=conf,
                kind="self-improvement" if for_self else "article",
                metadata={"query": query[:300], "for_self": for_self},
            )
            if did:
                discoveries_recorded += 1
                if status == "approved":
                    _capture_as_lesson(memory, title, url, "article", conf, query)

    return {
        "ok": True,
        "query": query,
        "source": result.get("source", "duckduckgo"),
        "result_count": result.get("result_count", 0),
        "results": result.get("results", []),
        "discoveries_recorded": discoveries_recorded,
    }


# ---------------------------------------------------------------------------
# Web fetch tool — reads a URL as clean text (for docs/examples/inspiration)
# ---------------------------------------------------------------------------

def _strip_html(raw_html: str) -> str:
    """Strip HTML tags and decode entities → clean readable text."""
    import html as _html_mod
    import re as _re
    t = _re.sub(r"<(script|style|noscript|header|footer|nav)[^>]*>.*?</(script|style|noscript|header|footer|nav)>",
                " ", raw_html, flags=_re.DOTALL | _re.IGNORECASE)
    t = _re.sub(r"<!--.*?-->", " ", t, flags=_re.DOTALL)
    t = _re.sub(r"<[^>]+>", " ", t)
    t = _html_mod.unescape(t)
    t = _re.sub(r"[ \t]+", " ", t)
    t = _re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


async def web_fetch_tool(url: str, max_chars: int = 10_000) -> dict[str, Any]:
    """Fetch a URL and return its text content (HTML stripped).

    Use this when you already have a URL from NEED_WEB results and want to
    read the actual page content — documentation, README, component examples,
    design patterns, API reference, etc.

    Hardened against modern anti-bot systems via ``scraping_toolkit``:
      - real-browser headers + TLS/HTTP-2 fingerprint impersonation (curl_cffi)
      - Cloudflare challenge / error-code detection (fails loudly, not silently)
      - AI-Labyrinth / empty-shell content validation
    """
    try:
        from argos_data_nexus.modules.scraping_toolkit import fetch_resilient
    except ImportError:
        import httpx
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                text = _strip_html(resp.text)[:max_chars]
            return {"ok": True, "url": url, "text": text, "chars": len(text)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:300], "url": url}

    res = await fetch_resilient(url, validate_content=True)
    if not res.ok:
        return {
            "ok": False,
            "url": url,
            "error": res.reason or f"HTTP {res.status} for {url}",
            "block": res.block,
            "status": res.status,
            "via": res.via,
        }

    ct = res.content_type
    if "text/html" in ct.lower():
        text = _strip_html(res.text)
    elif any(x in ct.lower() for x in ("text/", "application/json", "application/xml")):
        text = res.text
    else:
        text = f"[Non-text content: {ct or 'unknown'}]"

    truncated = len(text) > max_chars
    return {
        "ok": True, "url": url,
        "text": text[:max_chars],
        "chars_total": len(text),
        "truncated": truncated,
        "via": res.via,
    }


# ---------------------------------------------------------------------------
# Web screenshot tool — captures a page as an image for design reference
# ---------------------------------------------------------------------------

async def web_screenshot_tool(url: str) -> dict[str, Any]:
    """Screenshot a URL via thum.io (free, no API key needed).

    Returns a base64-encoded JPEG/PNG image for vision injection into the next
    LLM call.  Use this to see how a UI looks, understand a design system,
    or get visual inspiration for what you're building.
    """
    import httpx
    import base64
    shot_url = f"https://image.thum.io/get/width/1280/noanimate/{url}"
    try:
        async with httpx.AsyncClient(timeout=35.0, follow_redirects=True) as client:
            resp = await client.get(shot_url)
            resp.raise_for_status()
            ct = resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
            if not ct.startswith("image/"):
                return {"ok": False, "error": f"Unexpected content-type: {ct}", "url": url}
            image_b64 = base64.b64encode(resp.content).decode()
            return {"ok": True, "url": url, "image_b64": image_b64, "media_type": ct,
                    "size_kb": len(resp.content) // 1024}
    except httpx.HTTPStatusError as exc:
        return {"ok": False, "error": f"HTTP {exc.response.status_code}", "url": url}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "url": url}


async def github_search_tool(
    memory,  # DaedalusMemory
    query: str,
    language: str | None = None,
    limit: int = DEFAULT_GH_LIMIT,
    min_stars: int = 0,
    record_discoveries: bool = True,
    for_self: bool = False,
) -> dict[str, Any]:
    """Search GitHub via the existing finder and optionally record discoveries.

    ``for_self=True`` forces every result to ``pending`` (no auto-approve).
    """
    _gh_find_skill, GithubFinderError, GithubFinderBadRequest = _github_finder_impl()
    try:
        result = await _gh_find_skill(query, language=language, limit=limit, min_stars=min_stars)
    except GithubFinderBadRequest as exc:
        return {"ok": False, "error": f"bad_request: {exc}", "results": []}
    except GithubFinderError as exc:
        return {"ok": False, "error": f"github_search_failed: {exc}", "results": []}

    discoveries_recorded = 0
    if record_discoveries:
        for r in result.get("results", []) or []:
            url = r.get("url") or r.get("html_url") or ""
            full_name = r.get("full_name") or r.get("name") or ""
            if not url or not full_name:
                continue
            if memory.find_discovery_by_url(url):
                continue
            # GitHub finder already scored these on a 0..100 scale (see
            # github_finder._score_repo). Normalise to 0..1.
            score = r.get("total_score") or r.get("score") or 0
            conf = max(0.45, min(0.95, score / 100.0)) if score else 0.55
            status, _ = _decide_status(conf, url, for_self=for_self)
            why = (r.get("readme_excerpt") or r.get("description") or full_name)[:500]
            did = memory.capture_discovery(
                title=full_name,
                url=url,
                source="github",
                why_useful=why,
                status=status,
                confidence=conf,
                kind="self-improvement" if for_self else "library",
                metadata={
                    "query": query[:300],
                    "stars": int(r.get("stars") or r.get("stargazers_count") or 0),
                    "language": r.get("language") or language or "",
                    "install_command": (r.get("install_command") or "")[:200],
                    "for_self": for_self,
                },
            )
            if did:
                discoveries_recorded += 1
                if status == "approved":
                    _capture_as_lesson(memory, full_name, url, "library", conf, query)

    return {
        "ok": True,
        "query": query,
        "language": language,
        "result_count": result.get("result_count", 0),
        "results": result.get("results", []),
        "discoveries_recorded": discoveries_recorded,
    }


def _decide_status(confidence: float, url: str, for_self: bool = False) -> tuple[str, str | None]:
    """Hybrid policy: auto-approve only if confidence >= threshold AND URL visible.

    Self-improvement discoveries (``for_self=True``) ALWAYS require explicit
    owner approval — they can never be auto-approved regardless of confidence.
    """
    if for_self:
        return "pending", None
    if confidence >= AUTO_APPROVE_THRESHOLD and url:
        return "approved", None
    return "pending", None


# ---------------------------------------------------------------------------
# Coding tools wrappers (async — run sync impls in executor)
# ---------------------------------------------------------------------------

def _run_sync_coding(fn, *args, **kwargs):
    import asyncio, functools
    loop = asyncio.get_event_loop()
    return loop.run_in_executor(None, functools.partial(fn, *args, **kwargs))


async def pypi_search_tool(query: str, limit: int = 6) -> dict[str, Any]:
    """Search PyPI for Python packages."""
    from .coding_tools import pypi_search
    return await _run_sync_coding(pypi_search, query, limit)


async def npm_search_tool(query: str, limit: int = 6) -> dict[str, Any]:
    """Search npm registry for JavaScript/TypeScript packages."""
    from .coding_tools import npm_search
    return await _run_sync_coding(npm_search, query, limit)


async def docs_fetch_tool(url: str, max_chars: int = 5000) -> dict[str, Any]:
    """Fetch a documentation URL and return clean plain text."""
    from .coding_tools import docs_fetch
    return await _run_sync_coding(docs_fetch, url, max_chars)


async def dependency_analyze_tool(package: str, ecosystem: str = "auto") -> dict[str, Any]:
    """Check latest version, release date, and CVE vulnerabilities for a package."""
    from .coding_tools import dependency_analyze
    return await _run_sync_coding(dependency_analyze, package, ecosystem)


async def git_log_tool(n: int = 15, path: str | None = None) -> dict[str, Any]:
    """Return recent git commits in the ADN repo."""
    from .coding_tools import git_log
    return await _run_sync_coding(git_log, n, path)


async def git_diff_tool(path: str | None = None, ref: str = "HEAD") -> dict[str, Any]:
    """Return git diff for a file or the whole working tree."""
    from .coding_tools import git_diff
    return await _run_sync_coding(git_diff, path, ref)


async def code_sandbox_tool(code: str, timeout: int = 10) -> dict[str, Any]:
    """Execute a Python snippet in a sandboxed subprocess and return output."""
    from .coding_tools import code_sandbox
    return await _run_sync_coding(code_sandbox, code, timeout)


# ---------------------------------------------------------------------------
# Self-Awareness tools — let Daedalus discover his own system
# ---------------------------------------------------------------------------

async def list_endpoints_tool(filter_q: str | None = None) -> dict[str, Any]:
    """Introspect FastAPI routes on this process (standalone app or ADN host)."""
    try:
        try:
            from argos_data_nexus.main import app
        except ImportError:
            from app.main import app
        routes: list[dict[str, Any]] = []
        q = (filter_q or "").lower().strip()
        for r in app.routes:
            path = getattr(r, "path", None)
            if not path:
                continue
            methods = sorted(getattr(r, "methods", set()) or set())
            methods = [m for m in methods if m not in ("HEAD", "OPTIONS")]
            name = getattr(r, "name", "") or ""
            if q and q not in path.lower() and q not in name.lower():
                continue
            routes.append({
                "path": path,
                "methods": methods,
                "name": name,
            })
        routes.sort(key=lambda x: x["path"])
        return {"ok": True, "count": len(routes), "filter": filter_q, "routes": routes[:200]}
    except Exception as exc:
        log.warning("list_endpoints failed: %s", exc)
        return {"ok": False, "error": str(exc)}


async def list_modules_tool(area: str | None = None) -> dict[str, Any]:
    """List Python modules under argos_data_nexus/ with their docstrings.

    area: optional sub-path filter (e.g. "daedalus", "modules", "templates").
    Use this to discover what code units exist before proposing changes.
    """
    import ast
    from pathlib import Path
    try:
        from .paths import adn_package_root, package_root, product_root, repo_root
        roots = [package_root(), product_root() / "app"]
        adn = adn_package_root()
        if adn is not None:
            roots.insert(0, adn)
        results: list[dict[str, Any]] = []
        a = (area or "").lower().strip()
        repo = repo_root()
        for root in roots:
            if not root.is_dir():
                continue
            for py in sorted(root.rglob("*.py")):
                try:
                    rel = str(py.relative_to(repo))
                except ValueError:
                    rel = str(py)
                if "/__pycache__/" in rel or rel.endswith("__init__.py"):
                    continue
                if a and a not in rel.lower():
                    continue
                try:
                    src = py.read_text("utf-8", errors="replace")
                    tree = ast.parse(src)
                    doc = ast.get_docstring(tree) or ""
                    names = []
                    for node in tree.body:
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                            if not node.name.startswith("_"):
                                names.append(node.name)
                    results.append({
                        "path": rel,
                        "lines": src.count("\n") + 1,
                        "doc": (doc or "").splitlines()[0][:160] if doc else "",
                        "exports": names[:10],
                    })
                except Exception:
                    results.append({"path": rel, "lines": 0, "doc": "(parse error)", "exports": []})
        return {"ok": True, "count": len(results), "filter": area, "modules": results[:120]}
    except Exception as exc:
        log.warning("list_modules failed: %s", exc)
        return {"ok": False, "error": str(exc)}


async def describe_db_tool(table: str | None = None) -> dict[str, Any]:
    """Describe the SQL schema — SQLite in standalone, Postgres when hosted by ADN."""
    try:
        from .host import get_pool, use_adn_db
        pool = await get_pool()
        import re as _re
        _IDENT_RE = _re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
        async with pool.acquire() as conn:
            if not use_adn_db():
                if table:
                    if not _IDENT_RE.match(table):
                        return {"ok": False, "error": "invalid table name"}
                    rows = await conn.fetch(f'PRAGMA table_info("{table}")')
                    if not rows:
                        return {"ok": False, "error": f"table not found: {table}"}
                    cols = [
                        {
                            "column_name": r.get("name"),
                            "data_type": r.get("type"),
                            "is_nullable": "NO" if r.get("notnull") else "YES",
                            "column_default": r.get("dflt_value"),
                        }
                        for r in rows
                    ]
                    try:
                        cnt = await conn.fetchval(f'SELECT COUNT(*) FROM "{table}"')
                    except Exception:
                        cnt = None
                    return {"ok": True, "engine": "sqlite", "table": table, "row_count": cnt, "columns": cols}
                tables = await conn.fetch(
                    "SELECT name AS table_name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
                out = []
                for t in tables:
                    name = t.get("table_name") or t.get("name")
                    col_rows = await conn.fetch(f'PRAGMA table_info("{name}")')
                    out.append({"table_name": name, "col_count": len(col_rows)})
                return {"ok": True, "engine": "sqlite", "tables": out}
            if table:
                if not _IDENT_RE.match(table):
                    return {"ok": False, "error": "invalid table name"}
                rows = await conn.fetch(
                    """
                    SELECT column_name, data_type, is_nullable, column_default
                    FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = $1
                    ORDER BY ordinal_position
                    """,
                    table,
                )
                if not rows:
                    return {"ok": False, "error": f"table not found: {table}"}
                cols = [dict(r) for r in rows]
                try:
                    cnt = await conn.fetchval(f'SELECT COUNT(*) FROM "{table}"')
                except Exception:
                    cnt = None
                return {"ok": True, "engine": "postgres", "table": table, "row_count": cnt, "columns": cols}
            rows = await conn.fetch(
                """
                SELECT t.table_name,
                       (SELECT COUNT(*) FROM information_schema.columns c
                        WHERE c.table_schema='public' AND c.table_name=t.table_name) AS col_count
                FROM information_schema.tables t
                WHERE t.table_schema = 'public' AND t.table_type = 'BASE TABLE'
                ORDER BY t.table_name
                """
            )
            tables = []
            for r in rows:
                tname = r["table_name"]
                # Table names from information_schema are trusted, but double-check
                # before string interpolation to keep this code defensively safe.
                if not _IDENT_RE.match(tname):
                    cnt = None
                else:
                    try:
                        cnt = await conn.fetchval(f'SELECT COUNT(*) FROM "{tname}"')
                    except Exception:
                        cnt = None
                tables.append({"table": tname, "columns": r["col_count"], "rows": cnt})
            return {"ok": True, "engine": "postgres", "count": len(tables), "tables": tables}
    except Exception as exc:
        log.warning("describe_db failed: %s", exc)
        return {"ok": False, "error": str(exc)}


async def search_code_tool(query: str, limit: int = 30) -> dict[str, Any]:
    """Search the ADN codebase — semantic vector search first, ripgrep keyword fallback.

    SEMANTIC mode (default): finds symbols by *meaning*, not exact text.
      "намери кода за auth"   → finds verify_token, session_middleware, jwt_decode
      "кой обработва proposals" → finds proposals_page, proposals_list, etc.

    KEYWORD fallback: used when the code index is not yet built, or when the
    query looks like an exact identifier (no spaces, all ASCII identifiers).

    Returns both semantic matches (symbols with signatures/docstrings) and
    any keyword hits, ranked by relevance.
    """
    import asyncio as _aio
    import shutil
    from pathlib import Path

    from .paths import repo_root

    q = (query or "").strip()
    if not q or len(q) > 200:
        return {"ok": False, "error": "query must be 1-200 chars"}

    root = repo_root()

    # ── 1. Try semantic search first ─────────────────────────────────────────
    semantic_hits: list[dict] = []
    try:
        from .code_index import semantic_search, get_stats
        stats = get_stats()
        if stats.get("ok") and stats.get("symbols", 0) > 0:
            loop = _aio.get_event_loop()
            semantic_hits = await loop.run_in_executor(
                None, lambda: semantic_search(q, n_results=min(limit, 15))
            )
    except Exception as exc:
        log.debug("semantic_search unavailable: %s", exc)

    # ── 2. Ripgrep keyword search (always run — complements semantic) ─────────
    keyword_hits: list[str] = []
    rg = shutil.which("rg")
    try:
        if rg:
            proc = await _aio.create_subprocess_exec(
                rg, "--no-heading", "--line-number", "--max-count", "5",
                "--max-columns", "200", "-S", "--", q, str(root),
                stdout=_aio.subprocess.PIPE, stderr=_aio.subprocess.PIPE,
            )
        else:
            proc = await _aio.create_subprocess_exec(
                "grep", "-rIn", "--max-count=5", "--", q, str(root),
                stdout=_aio.subprocess.PIPE, stderr=_aio.subprocess.PIPE,
            )
        out, _ = await _aio.wait_for(proc.communicate(), timeout=10)
        text = out.decode("utf-8", errors="replace")
        repo_root = str(root.parent) + "/"
        keyword_hits = [
            ln.replace(repo_root, "")
            for ln in text.splitlines() if ln.strip()
        ][:limit]
    except _aio.TimeoutError:
        pass
    except Exception:
        pass

    # ── 3. Format combined response ───────────────────────────────────────────
    search_type = "semantic+keyword" if semantic_hits else "keyword"
    return {
        "ok": True,
        "query": q,
        "search_type": search_type,
        "semantic_count": len(semantic_hits),
        "keyword_count": len(keyword_hits),
        # Semantic matches — rich: filepath, qualified_name, signature, docstring, score
        "semantic_matches": semantic_hits,
        # Keyword matches — raw grep/rg lines: file:line:snippet
        "keyword_matches": keyword_hits,
    }


# ---------------------------------------------------------------------------
# DeutschesRechtsPortal (DRP) tools
# ---------------------------------------------------------------------------

async def drp_law_tool(abbr: str, paragraph: str, source: str = "auto") -> dict[str, Any]:
    """Fetch the full text of a German law paragraph.

    abbr: e.g. 'BGB', 'GG', 'StGB', 'HGB', 'ZPO'
    paragraph: e.g. '823', '5', '263'
    source: 'auto' (tries GII first, falls back to dejure), 'gii', 'dejure'
    """
    try:
        from argos_data_nexus.modules.deutsches_rechts import fetch_law
        result = await fetch_law(abbr, paragraph, source)
        data = result.get("data", result)
        return {
            "ok": bool(data.get("text")),
            "abbr": abbr,
            "paragraph": paragraph,
            "source": data.get("source"),
            "title": data.get("title", ""),
            "text": data.get("text", ""),
            "linked_decisions": data.get("linked_decisions", []),
            "cache_hit": result.get("cache_hit", False),
        }
    except Exception as exc:
        log.warning("drp_law_tool failed: %s", exc)
        return {"ok": False, "error": str(exc), "abbr": abbr, "paragraph": paragraph}


async def drp_cases_tool(
    query: str,
    source: str = "dejure",
    limit: int = 8,
    rechtsgebiet: str = "",
) -> dict[str, Any]:
    """Search German case law (Rechtsprechung).

    source: 'dejure' (neueste Entscheidungen via dejure.org) or 'bverfg' (BVerfG filter)
    rechtsgebiet: optional area of law for context, e.g. 'arbeitsrecht', 'strafrecht'
    """
    try:
        from argos_data_nexus.modules.deutsches_rechts import search_case_law
        result = await search_case_law(query, source, limit)
        data = result.get("data", result)
        decisions = data.get("results", [])
        lines = [
            f"[{d.get('court','')}] {d.get('date','')} — {d.get('title','')}"
            + (f" ({d.get('url','')})" if d.get("url") else "")
            for d in decisions
        ]
        return {
            "ok": True,
            "query": query,
            "source": source,
            "rechtsgebiet": rechtsgebiet,
            "total": data.get("total", len(decisions)),
            "results": decisions,
            "summary": "\n".join(lines),
            "cache_hit": result.get("cache_hit", False),
        }
    except Exception as exc:
        log.warning("drp_cases_tool failed: %s", exc)
        return {"ok": False, "error": str(exc), "query": query}


async def drp_search_tool(
    query: str,
    rechtsgebiet: str = "",
    limit: int = 5,
) -> dict[str, Any]:
    """Unified German legal research: laws + cases + academic + blogs in one call.

    rechtsgebiet: 'zivilrecht', 'strafrecht', 'arbeitsrecht', 'verfassungsrecht',
                  'verwaltungsrecht', 'europarecht', '' (all areas)
    Returns ranked results + 10 Google-dork queries for manual deep-dive.
    """
    try:
        from argos_data_nexus.modules.deutsches_rechts import unified_search
        result = await unified_search(query, rechtsgebiet, limit_per_source=limit)
        data = result.get("data", result)
        results = data.get("results", [])
        dorks = data.get("google_dorking_queries", [])
        lines = [
            f"[{r.get('channel','?')}][score={r.get('relevance_score',0):.1f}] "
            f"{r.get('title','')}"
            + (f" ({r.get('url','')})" if r.get("url") else "")
            for r in results[:15]
        ]
        return {
            "ok": True,
            "query": query,
            "rechtsgebiet": rechtsgebiet or "alle",
            "total": data.get("total", len(results)),
            "results": results,
            "summary": "\n".join(lines),
            "google_dorking_queries": dorks,
            "dork_hint": (
                f"Для по-задълбочено търсене използвай: {dorks[0]}"
                if dorks else ""
            ),
            "cache_hit": result.get("cache_hit", False),
        }
    except Exception as exc:
        log.warning("drp_search_tool failed: %s", exc)
        return {"ok": False, "error": str(exc), "query": query}


def _capture_as_lesson(memory, title: str, url: str, kind: str, conf: float, query: str) -> None:
    """When a discovery is auto-approved, also promote it to a lesson so it
    influences future context injections immediately."""
    try:
        memory.capture_lesson(
            lesson=f"Useful {kind}: {title} ({url}) — found while searching for '{query}'.",
            kind="discovery",
            confidence=conf,
        )
    except Exception as exc:
        log.debug("auto-promote discovery to lesson failed: %s", exc)
