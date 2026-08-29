"""Native Anthropic/OpenAI tool API for Daedalus.

Files, shell, search, fetch, image gen, and playground writes as structured
tool calls. ``NEED_*`` markers remain the fallback path in ``agent.py``.

Enabled by default. Set ``DAEDALUS_NATIVE_TOOLS=0`` to disable.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)

PERM_MAP: dict[str, str | None] = {
    "read_file": None,
    "list_dir": None,
    "web_search": "web_search",
    "web_fetch": "web_search",
    "web_read": "web_search",
    "reach_search": "web_search",
    "reach_status": None,
    "postgres_query": "postgres_query",
    "code_search": None,
    "list_endpoints": None,
    "list_modules": None,
    "run_command": None,
    "write_file": None,
    "generate_image": None,
    "remember": None,
    "browser": None,
    "mcp_call": None,
    "spawn_subagent": None,
    "playtest": None,
    "design_brief": None,
    "mirofish": None,
}

NATIVE_TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "read_file",
        "description": "Read a source file (harness or playground). Optional line range.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "line_start": {"type": "integer"},
                "line_end": {"type": "integer"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "list_dir",
        "description": "List files in a directory inside the Daedalus jail.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative directory, default '.'"},
            },
        },
    },
    {
        "name": "write_file",
        "description": (
            "Write a file in a Playground project (slug required). "
            "Do not use this to patch the Daedalus harness — propose instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string"},
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["slug", "path", "content"],
        },
    },
    {
        "name": "run_command",
        "description": (
            "Run a shell command in the product/playground jail. "
            "Use for pytest, npm, python, ls, git status. Secrets are stripped from env."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "cwd": {"type": "string"},
                "slug": {"type": "string", "description": "Playground project slug as cwd"},
                "timeout": {"type": "integer"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "web_search",
        "description": "Search the web for documentation, APIs, or examples.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "web_fetch",
        "description": "Fetch a URL and return cleaned text (docs, README, API reference).",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "max_chars": {"type": "integer"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "generate_image",
        "description": "Generate a game/app asset image and save it under playground assets or data/generated.",
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "slug": {"type": "string"},
                "filename": {"type": "string"},
                "size": {"type": "string", "description": "e.g. 512x512"},
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "remember",
        "description": "Store an owner preference forever (language, stack, style, name).",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "kind": {"type": "string"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "code_search",
        "description": "Semantic + keyword search across the codebase.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "postgres_query",
        "description": "Read-only SQL (SQLite standalone, Postgres when hosted by ADN).",
        "input_schema": {
            "type": "object",
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"],
        },
    },
    {
        "name": "list_endpoints",
        "description": "List HTTP routes matching a filter.",
        "input_schema": {
            "type": "object",
            "properties": {"filter": {"type": "string", "default": "*"}},
        },
    },
    {
        "name": "list_modules",
        "description": "List Python modules in the Daedalus product.",
        "input_schema": {
            "type": "object",
            "properties": {"filter": {"type": "string", "default": "*"}},
        },
    },
    {
        "name": "web_read",
        "description": (
            "Read a URL as clean markdown via Jina Reader (Agent Reach web channel). "
            "Use instead of web_fetch when you need readable article text."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "max_chars": {"type": "integer"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "reach_search",
        "description": (
            "Search the live web or Reddit via the Reach layer. "
            "channel=web uses DuckDuckGo; channel=reddit finds public threads "
            "(DuckDuckGo site:reddit.com, or rdt-cli / owner cookie if present)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "channel": {
                    "type": "string",
                    "description": "auto | web | reddit",
                },
                "subreddit": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "reach_status",
        "description": "Probe internet backends (Jina, DuckDuckGo, Reddit, yt-dlp, gh).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "browser",
        "description": (
            "Interactive Chromium session: goto, click, type, extract, screenshot, close. "
            "Use for exploring a live page, not just smoke verify."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "goto | click | type | extract | screenshot | close",
                },
                "url": {"type": "string"},
                "selector": {"type": "string"},
                "text": {"type": "string"},
                "slug": {"type": "string", "description": "Playground slug for screenshots"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "mcp_call",
        "description": "List or call a tool on an optional MCP server from $DAEDALUS_DATA/mcp.json.",
        "input_schema": {
            "type": "object",
            "properties": {
                "server": {"type": "string"},
                "tool": {"type": "string"},
                "arguments": {"type": "object"},
                "list_tools": {"type": "boolean"},
            },
        },
    },
    {
        "name": "spawn_subagent",
        "description": (
            "Run an isolated sub-agent that can read/write playground files and run commands. "
            "Use for a bounded file task. Writes are jailed to slug."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string"},
                "task": {"type": "string"},
                "max_rounds": {"type": "integer"},
            },
            "required": ["slug", "task"],
        },
    },
    {
        "name": "playtest",
        "description": (
            "MatrAIx-style simulated players critique a Playground game "
            "(loop, controls, first 30s, win/lose). Pass slug_b to A/B two builds."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string"},
                "slug_b": {
                    "type": "string",
                    "description": "Optional second slug — pairwise compare",
                },
                "kind": {"type": "string"},
                "n": {"type": "integer"},
                "use_browser": {"type": "boolean"},
            },
            "required": ["slug"],
        },
    },
    {
        "name": "design_brief",
        "description": (
            "Expand a one-line game/app prompt into a playable brief "
            "(loop, controls, win/lose, art, M0 feasibility) before writing files."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "kind": {"type": "string"},
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "mirofish",
        "description": (
            "Talk to an optional MiroFish-Offline sidecar (AGPL, separate process). "
            "Ping, list projects, or submit GDD/press-release text."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "ping | list | submit"},
                "text": {"type": "string"},
                "requirement": {"type": "string"},
                "project_name": {"type": "string"},
            },
            "required": ["action"],
        },
    },
]


def native_tools_enabled() -> bool:
    flag = os.environ.get("DAEDALUS_NATIVE_TOOLS", "").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return False
    return True


def openai_tool_specs(specs: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    out = []
    for t in specs or NATIVE_TOOL_SPECS:
        out.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
            },
        })
    return out


def to_openai_messages(system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert unified (Anthropic-style) tool turns into OpenAI chat messages."""
    oa: list[dict[str, Any]] = []
    if system:
        oa.append({"role": "system", "content": system})
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        if isinstance(content, str):
            oa.append({"role": role, "content": content})
            continue
        if not isinstance(content, list):
            continue
        if role == "assistant":
            text_parts = [b.get("text", "") for b in content if b.get("type") == "text"]
            tool_uses = [b for b in content if b.get("type") == "tool_use"]
            row: dict[str, Any] = {"role": "assistant", "content": "\n".join(text_parts) or None}
            if tool_uses:
                row["tool_calls"] = [
                    {
                        "id": tu.get("id") or f"call_{i}",
                        "type": "function",
                        "function": {
                            "name": tu.get("name"),
                            "arguments": json.dumps(tu.get("input") or {}, ensure_ascii=False),
                        },
                    }
                    for i, tu in enumerate(tool_uses)
                ]
            oa.append(row)
        else:
            for block in content:
                if block.get("type") == "tool_result":
                    oa.append({
                        "role": "tool",
                        "tool_call_id": block.get("tool_use_id") or "",
                        "content": block.get("content") or "",
                    })
                elif block.get("type") == "text":
                    oa.append({"role": "user", "content": block.get("text") or ""})
    return oa


def before_tool_call(tool_name: str, *, marker_name: str | None = None) -> tuple[bool, str]:
    perm = PERM_MAP.get(tool_name)
    if perm is None:
        return True, ""
    try:
        from .outbox import check_permission, format_permission_denied
        if check_permission(perm):
            return True, ""
        label = marker_name or f"native:{tool_name}"
        return False, format_permission_denied(perm, tool=label)
    except Exception as exc:
        log.debug("before_tool_call failed open: %s", exc)
        return True, ""


async def execute_native_tool(
    name: str,
    args: dict[str, Any],
    *,
    memory: Any,
    session_id: str,
    read_file_fn: Callable[..., dict],
) -> str:
    allowed, denial = before_tool_call(name)
    if not allowed:
        return denial

    try:
        from .offline import deny
        blocked = deny(name)
        if blocked:
            return blocked
    except Exception:
        pass

    from .tools import (
        web_search_tool, postgres_query_tool, search_code_tool,
        list_endpoints_tool, list_modules_tool, web_fetch_tool,
    )

    try:
        if name == "read_file":
            path = str(args.get("path", ""))
            ls = args.get("line_start")
            le = args.get("line_end")
            r = read_file_fn(path, int(ls) if ls else None, int(le) if le else None)
            if r.get("ok"):
                trunc = " [TRUNCATED]" if r.get("truncated") else ""
                return f"── read_file {r['path']}{trunc} ──\n{r['content']}"
            return f"── read_file FAILED: {r.get('error')} ──"

        if name == "list_dir":
            from .shell_center import resolve_cwd
            rel = str(args.get("path") or ".")
            try:
                root = resolve_cwd(rel)
            except ValueError as exc:
                return f"── list_dir FAILED: {exc} ──"
            names = []
            for p in sorted(root.iterdir())[:80]:
                names.append(p.name + ("/" if p.is_dir() else ""))
            return f"── list_dir {root} ──\n" + "\n".join(names)

        if name == "write_file":
            from . import playground as pg
            slug = str(args.get("slug") or "")
            path = str(args.get("path") or "")
            content = str(args.get("content") or "")
            if not slug or not path:
                return "── write_file FAILED: slug and path required ──"
            pg.write_file(slug, path, content)
            return f"── write_file OK {slug}/{path} ({len(content)} chars) ──"

        if name == "run_command":
            from .shell_center import run_command
            r = run_command(
                str(args.get("command") or ""),
                cwd=str(args.get("cwd") or "") or None,
                slug=str(args.get("slug") or ""),
                timeout=args.get("timeout"),
            )
            body = (r.get("stdout") or "") + (("\n" + r["stderr"]) if r.get("stderr") else "")
            status = "OK" if r.get("ok") else "FAILED"
            return (
                f"── run_command {status} exit={r.get('exit_code')} cwd={r.get('cwd')} ──\n"
                f"{body[:8000]}"
            )

        if name == "web_search":
            q = str(args.get("query", ""))
            ws = await web_search_tool(memory, q, limit=5)
            lines = [f"── web_search: {q} ──"]
            for res in ws.get("results") or []:
                lines.append(
                    f"  • {res.get('title', '')} — {res.get('url', '')}\n"
                    f"    {str(res.get('snippet', ''))[:140]}"
                )
            return "\n".join(lines) if len(lines) > 1 else "── web_search: no results ──"

        if name == "web_fetch":
            url = str(args.get("url") or "")
            max_chars = int(args.get("max_chars") or 8000)
            r = await web_fetch_tool(url, max_chars=max_chars)
            if r.get("ok"):
                return f"── web_fetch {url} ──\n{(r.get('text') or '')[:max_chars]}"
            return f"── web_fetch FAILED: {r.get('error')} ──"

        if name == "web_read":
            from .reach import looks_like_rss, read_rss, web_read, youtube_captions
            url = str(args.get("url") or "")
            max_chars = int(args.get("max_chars") or 8000)
            low = url.lower()
            if "youtube.com" in low or "youtu.be" in low:
                yt = youtube_captions(url, max_chars=max_chars)
                if yt.get("ok"):
                    return f"── youtube_captions {url} ──\n{yt.get('text')}"
            if looks_like_rss(url):
                rss = read_rss(url)
                if rss.get("ok"):
                    lines = [f"── rss {url} ──"]
                    for it in rss.get("items") or []:
                        lines.append(f"  • {it.get('title')} — {it.get('link')}")
                    return "\n".join(lines)
            r = await web_read(url, max_chars=max_chars)
            if r.get("ok"):
                return f"── web_read [{r.get('backend')}] {r.get('jina_url') or url} ──\n{r.get('text')}"
            return f"── web_read FAILED: {r.get('error')} ──"

        if name == "reach_search":
            from .reach import reach_search
            r = await reach_search(
                str(args.get("query") or ""),
                channel=str(args.get("channel") or "auto"),
                subreddit=str(args.get("subreddit") or ""),
                limit=int(args.get("limit") or 6),
            )
            lines = [
                f"── reach_search {r.get('channel') or ''} {r.get('backend') or ''} ──",
                str(r.get("query") or ""),
            ]
            if r.get("note"):
                lines.append(str(r["note"]))
            if r.get("error") and not r.get("results"):
                lines.append("error: " + str(r["error"]))
            for hit in r.get("results") or []:
                lines.append("• " + str(hit.get("title") or "") + (
                    f"  r/{hit.get('subreddit')}" if hit.get("subreddit") else ""
                ))
                if hit.get("url"):
                    lines.append("  " + str(hit["url"]))
            return "\n".join(lines)

        if name == "reach_status":
            from .reach import reach_status
            st = reach_status()
            lines = ["── reach_status ──"]
            for ch, info in (st.get("channels") or {}).items():
                flag = "UP" if info.get("ready") else "DOWN"
                lines.append(f"  {flag}  {ch}  {info.get('backend') or '—'}  {info.get('note')}")
            if st.get("doctor") and st["doctor"].get("output"):
                lines.append((st["doctor"].get("output") or "")[:1500])
            return "\n".join(lines)

        if name == "browser":
            from .browser_session import run_action
            r = await run_action(
                str(args.get("action") or "extract"),
                url=str(args.get("url") or ""),
                selector=str(args.get("selector") or ""),
                text=str(args.get("text") or ""),
                slug=str(args.get("slug") or ""),
            )
            if r.get("ok"):
                text = r.get("text") or r.get("path") or r.get("title") or ""
                return f"── browser {r.get('action')} OK {r.get('url') or ''} ──\n{text}"
            return f"── browser FAILED: {r.get('error')} ──"

        if name == "mcp_call":
            from . import mcp_client
            server = str(args.get("server") or "")
            if args.get("list_tools") or not args.get("tool"):
                if not server:
                    return "── mcp ──\n" + json.dumps(mcp_client.status(), ensure_ascii=False, indent=2)[:3000]
                r = await mcp_client.list_tools(server)
            else:
                r = await mcp_client.call_tool(
                    server,
                    str(args.get("tool") or ""),
                    args.get("arguments") if isinstance(args.get("arguments"), dict) else {},
                )
            return f"── mcp_call ──\n{json.dumps(r, ensure_ascii=False, default=str)[:6000]}"

        if name == "spawn_subagent":
            from .subagents import run_subagent
            r = await run_subagent(
                str(args.get("slug") or ""),
                str(args.get("task") or ""),
                memory=memory,
                session_id=session_id,
                max_rounds=int(args.get("max_rounds") or 6),
                read_file_fn=read_file_fn,
            )
            status = "OK" if r.get("ok") else "FAILED"
            return f"── spawn_subagent {status} {r.get('slug')} ──\n{r.get('summary') or r.get('error')}"

        if name == "playtest":
            slug_b = str(args.get("slug_b") or "").strip()
            kind = str(args.get("kind") or "static")
            n = int(args.get("n") or 4)
            use_browser = bool(args.get("use_browser"))
            if slug_b:
                from .playtest import compare_playtests
                r = await compare_playtests(
                    str(args.get("slug") or ""),
                    slug_b,
                    kind=kind,
                    n=n,
                    use_browser=use_browser,
                )
                if not r.get("ok"):
                    return f"── playtest A/B FAILED: {r.get('error')} ──"
                return (
                    f"── playtest A/B winner={r.get('winner')} ──\n"
                    f"{r.get('summary')}\n"
                    f"A blockers: {r.get('a', {}).get('blockers')}\n"
                    f"B blockers: {r.get('b', {}).get('blockers')}"
                )
            from .playtest import format_for_prompt, run_playtest
            r = await run_playtest(
                str(args.get("slug") or ""),
                kind=kind,
                n=n,
                use_browser=use_browser,
            )
            return "── playtest ──\n" + format_for_prompt(r)

        if name == "design_brief":
            from .design_arena import expand_brief
            r = expand_brief(
                str(args.get("prompt") or ""),
                kind=str(args.get("kind") or ""),
            )
            if not r.get("ok"):
                return f"── design_brief FAILED: {r.get('error')} ──"
            return "── design_brief ──\n" + (r.get("prompt_block") or "")

        if name == "mirofish":
            from . import mirofish_client as mf
            action = str(args.get("action") or "ping").lower()
            if action == "list":
                r = await mf.list_projects()
            elif action == "submit":
                r = await mf.submit_document(
                    str(args.get("text") or ""),
                    requirement=str(args.get("requirement") or ""),
                    project_name=str(args.get("project_name") or "Daedalus GDD"),
                )
            else:
                r = await mf.ping()
            return f"── mirofish {action} ──\n{json.dumps(r, ensure_ascii=False, default=str)[:4000]}"

        if name == "generate_image":
            from .image_client import generate_image
            from .paths import data_root, playground_projects_dir
            prompt = str(args.get("prompt") or "")
            slug = str(args.get("slug") or "")
            filename = str(args.get("filename") or "asset.png")
            size = str(args.get("size") or "512x512")
            img = await generate_image(prompt=prompt, size=size)
            images = img.get("images") or []
            if not images:
                return f"── generate_image FAILED: no images ({img}) ──"
            first = images[0]
            raw_b64 = first.get("b64") or ""
            dest_dir = (playground_projects_dir() / slug / "assets") if slug else (data_root() / "generated")
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / filename
            if raw_b64:
                import base64
                dest.write_bytes(base64.b64decode(raw_b64))
            elif first.get("url"):
                import httpx
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.get(first["url"])
                    resp.raise_for_status()
                    dest.write_bytes(resp.content)
            else:
                return "── generate_image FAILED: no b64/url ──"
            return f"── generate_image OK {dest} ──\nprovider={img.get('provider')} model={img.get('model')}"

        if name == "remember":
            from .preferences import add as pref_add
            row = pref_add(str(args.get("text") or ""), kind=str(args.get("kind") or "preference"))
            return f"── remember OK {row.get('id')} ── {row.get('text')}"

        if name == "postgres_query":
            sql = str(args.get("sql", ""))
            r = await postgres_query_tool(sql, session_id=session_id)
            if r.get("ok"):
                rows = "\n".join(str(row) for row in (r.get("rows") or [])[:20])
                return f"── postgres ({r.get('row_count', 0)} rows) ──\n{rows}"
            return f"── postgres FAILED: {r.get('error')} ──"

        if name == "code_search":
            q = str(args.get("query", ""))
            r = await search_code_tool(q)
            lines = [f"── code_search: {q} ──"]
            for m in (r.get("semantic_matches") or [])[:5]:
                lines.append(f"  [sem] {m.get('filepath', '')} → {m.get('qualified_name', '')}")
            for m in (r.get("keyword_matches") or [])[:5]:
                lines.append(f"  [kw] {m.get('file', '')}:{m.get('line', '')}")
            return "\n".join(lines)

        if name == "list_endpoints":
            flt = str(args.get("filter", "*"))
            r = await list_endpoints_tool(flt)
            return f"── endpoints ──\n{r.get('text', str(r))[:3000]}"

        if name == "list_modules":
            flt = str(args.get("filter", "*"))
            r = await list_modules_tool(flt)
            return f"── modules ──\n{r.get('text', str(r))[:3000]}"

        return f"── unknown native tool: {name} ──"
    except Exception as exc:
        log.warning("native tool %s failed: %s", name, exc)
        return f"── {name} ERROR: {exc} ──"


async def run_native_discovery_pass(
    system: str,
    user_content: str,
    call_with_tools_fn: Callable[..., Awaitable[dict[str, Any]]],
    *,
    memory: Any,
    session_id: str,
    read_file_fn: Callable[..., dict],
    max_rounds: int = 8,
) -> tuple[str | None, list[dict[str, Any]]]:
    """Run native tool rounds. Returns (result_text, tool_log).

    Returns (None, []) if no tools were called — caller uses the marker path.
    """
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_content}]
    tool_log: list[dict[str, Any]] = []
    all_results: list[str] = []

    for _round in range(max_rounds):
        resp = await call_with_tools_fn(system, messages, NATIVE_TOOL_SPECS)
        content_blocks = resp.get("content", [])

        assistant_content: list[dict[str, Any]] = []
        tool_uses: list[dict[str, Any]] = []
        text_parts: list[str] = []

        for block in content_blocks:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
                assistant_content.append(block)
            elif block.get("type") == "tool_use":
                tool_uses.append(block)
                assistant_content.append(block)

        if not tool_uses:
            final = "\n".join(text_parts).strip()
            if all_results:
                combined = (
                    "━━━ NATIVE TOOL RESULTS ━━━\n"
                    + "\n\n".join(all_results)
                    + ("\n\n━━━ MODEL FOLLOW-UP ━━━\n" + final if final else "")
                )
                return combined, tool_log
            return None, []

        messages.append({"role": "assistant", "content": assistant_content})

        tool_result_blocks: list[dict[str, Any]] = []
        for tu in tool_uses:
            tid = tu.get("id", "")
            tname = tu.get("name", "")
            tinput = tu.get("input") or {}
            if isinstance(tinput, str):
                try:
                    tinput = json.loads(tinput)
                except json.JSONDecodeError:
                    tinput = {"raw": tinput}

            result_text = await execute_native_tool(
                tname, tinput,
                memory=memory, session_id=session_id,
                read_file_fn=read_file_fn,
            )
            all_results.append(result_text)
            tool_log.append({
                "tool": tname,
                "input": {k: v for k, v in tinput.items() if k != "content"},
                "ok": "FAILED" not in result_text and "ERROR" not in result_text,
            })
            tool_result_blocks.append({
                "type": "tool_result",
                "tool_use_id": tid,
                "content": result_text,
            })

        messages.append({"role": "user", "content": tool_result_blocks})

    if all_results:
        return "━━━ NATIVE TOOL RESULTS ━━━\n" + "\n\n".join(all_results), tool_log
    return None, []
