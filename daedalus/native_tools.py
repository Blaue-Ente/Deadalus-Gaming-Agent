"""Native Anthropic/OpenAI tool API for Daedalus (Phase 2 — pi-inspired).

Top discovery tools exposed as structured tool_use blocks instead of NEED_* text
markers. ``before_tool_call`` maps to the existing outbox permission system.

Enable with DAEDALUS_NATIVE_TOOLS=1 (optional; markers remain the default path).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable, Awaitable

log = logging.getLogger(__name__)

# Tool name → outbox permission class (None = always allowed)
PERM_MAP: dict[str, str | None] = {
    "read_file": None,
    "web_search": "web_search",
    "postgres_query": "postgres_query",
    "code_search": None,
    "list_endpoints": None,
    "list_modules": None,
}

NATIVE_TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "read_file",
        "description": "Read a source file from the Daedalus (or ADN) codebase (max ~8k chars).",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path e.g. daedalus/agent.py"},
                "line_start": {"type": "integer", "description": "Optional 1-based start line"},
                "line_end": {"type": "integer", "description": "Optional 1-based end line"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "web_search",
        "description": "Search the web for documentation, APIs, or examples.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "postgres_query",
        "description": "Run a read-only SQL query (SQLite standalone, Postgres when hosted by ADN).",
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string"},
            },
            "required": ["sql"],
        },
    },
    {
        "name": "code_search",
        "description": "Semantic + keyword search across the codebase.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_endpoints",
        "description": "List HTTP routes matching a filter.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filter": {"type": "string", "default": "*"},
            },
        },
    },
    {
        "name": "list_modules",
        "description": "List Python modules in the Daedalus product (and ADN when present).",
        "input_schema": {
            "type": "object",
            "properties": {
                "filter": {"type": "string", "default": "*"},
            },
        },
    },
]


def native_tools_enabled() -> bool:
    return os.environ.get("DAEDALUS_NATIVE_TOOLS", "").lower() in ("1", "true", "yes")


def before_tool_call(tool_name: str, *, marker_name: str | None = None) -> tuple[bool, str]:
    """Permission gate — mirrors agent.py _perm_gate for native tools.

    Returns (allowed, denial_message).
    """
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
    """Execute one native tool and return text result for the LLM."""
    allowed, denial = before_tool_call(name)
    if not allowed:
        return denial

    from .tools import (
        web_search_tool, postgres_query_tool, search_code_tool,
        list_endpoints_tool, list_modules_tool,
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

        if name == "web_search":
            q = str(args.get("query", ""))
            ws = await web_search_tool(memory, q, limit=5)
            lines = [f"── web_search: {q} ──"]
            for res in ws.get("results") or []:
                lines.append(
                    f"  • {res.get('title', '')} — {res.get('url', '')}\n"
                    f"    {str(res.get('snippet', ''))[:140]}"
                )
            return "\n".join(lines) if len(lines) > 1 else f"── web_search: no results ──"

        if name == "postgres_query":
            sql = str(args.get("sql", ""))
            r = await postgres_query_tool(sql, session_id=session_id)
            if r.get("ok"):
                rows = "\n".join(str(row) for row in (r.get("rows") or [])[:20])
                return f"── postgres ({r['row_count']} rows) ──\n{rows}"
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
    max_rounds: int = 3,
) -> tuple[str | None, list[dict[str, Any]]]:
    """Run up to ``max_rounds`` native tool rounds. Returns (result_text, tool_log).

    Returns (None, []) if no tools were called — caller should use marker path.
    """
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_content}]
    tool_log: list[dict[str, Any]] = []
    all_results: list[str] = []

    for _round in range(max_rounds):
        resp = await call_with_tools_fn(system, messages, NATIVE_TOOL_SPECS)
        stop_reason = resp.get("stop_reason", "")
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
            # Model finished without more tools
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
            tool_log.append({"tool": tname, "input": tinput, "ok": "FAILED" not in result_text})
            tool_result_blocks.append({
                "type": "tool_result",
                "tool_use_id": tid,
                "content": result_text,
            })

        messages.append({"role": "user", "content": tool_result_blocks})

        if stop_reason == "end_turn" and not tool_uses:
            break

    if all_results:
        return "━━━ NATIVE TOOL RESULTS ━━━\n" + "\n\n".join(all_results), tool_log
    return None, []
