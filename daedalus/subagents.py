"""Tool-using sub-agents — isolated loops with files + shell, then return.

``workers.py`` slots are stateless LLM calls that must never emit NEED_*.
These sub-agents *do* get ``read_file`` / ``list_dir`` / ``write_file`` /
``run_command``, jailed to a Playground slug, with a hard round cap.

Master emits::

    <NEED_SUBAGENT: slug | task description>

or the native tool ``spawn_subagent``.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

log = logging.getLogger(__name__)

_SUB_RE = re.compile(
    r"<NEED_SUBAGENT:\s*([^|>]{1,80})\s*\|\s*(.+?)\s*>",
    re.DOTALL,
)

MAX_PARALLEL = 3
DEFAULT_ROUNDS = 6
SUBAGENT_TOOLS = ("read_file", "list_dir", "write_file", "run_command")

_SYSTEM = """You are a Daedalus sub-agent. You have tools. The master agent
delegated ONE concrete task. Finish it, then stop.

Rules:
- write_file ONLY inside the given playground slug.
- run_command is jailed; no sudo, no secrets.
- Do not chat. Do the work. When done, reply with a short summary (no tools).
- If blocked, say BLOCKED: reason.
"""


def parse_subagent_markers(raw: str) -> list[dict[str, str]]:
    if not raw or "NEED_SUBAGENT" not in raw:
        return []
    return [{"slug": s.strip(), "task": t.strip()} for s, t in _SUB_RE.findall(raw)][:MAX_PARALLEL]


def _specs() -> list[dict[str, Any]]:
    from .native_tools import NATIVE_TOOL_SPECS
    return [t for t in NATIVE_TOOL_SPECS if t["name"] in SUBAGENT_TOOLS]


async def run_subagent(
    slug: str,
    task: str,
    *,
    memory: Any = None,
    session_id: str = "",
    max_rounds: int = DEFAULT_ROUNDS,
    read_file_fn: Any = None,
) -> dict[str, Any]:
    slug = (slug or "").strip()
    task = (task or "").strip()
    if not slug or not task:
        return {"ok": False, "error": "slug and task required"}
    if read_file_fn is None:
        from .agent import _read_file_tool
        read_file_fn = _read_file_tool

    from .agent import _call_claude_with_tools
    from .native_tools import execute_native_tool

    system = _SYSTEM + f"\nPlayground slug: {slug}\n"
    user = f"Task:\n{task}\n\nAlways pass slug={slug!r} to write_file and run_command."
    messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
    log_rows: list[dict[str, Any]] = []
    final = ""
    rounds = max(1, min(int(max_rounds or DEFAULT_ROUNDS), 12))

    for _ in range(rounds):
        try:
            resp = await _call_claude_with_tools(system, messages, _specs())
        except Exception as exc:
            return {"ok": False, "slug": slug, "error": str(exc)[:240], "log": log_rows}
        content = resp.get("content") or []
        assistant: list[dict[str, Any]] = []
        uses: list[dict[str, Any]] = []
        texts: list[str] = []
        for block in content:
            if block.get("type") == "text":
                texts.append(block.get("text") or "")
                assistant.append(block)
            elif block.get("type") == "tool_use":
                uses.append(block)
                assistant.append(block)
        if not uses:
            final = "\n".join(texts).strip()
            break
        messages.append({"role": "assistant", "content": assistant})
        results: list[dict[str, Any]] = []
        for tu in uses:
            name = tu.get("name") or ""
            args = tu.get("input") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"raw": args}
            if not isinstance(args, dict):
                args = {}
            # Jail writes/commands to the delegated slug.
            if name in ("write_file", "run_command"):
                args["slug"] = slug
            text = await execute_native_tool(
                name, args,
                memory=memory, session_id=session_id or "",
                read_file_fn=read_file_fn,
            )
            log_rows.append({"tool": name, "ok": "FAILED" not in text and "ERROR" not in text})
            results.append({
                "type": "tool_result",
                "tool_use_id": tu.get("id") or "",
                "content": text,
            })
        messages.append({"role": "user", "content": results})
    else:
        final = final or "(round cap reached)"

    return {
        "ok": True,
        "slug": slug,
        "task": task[:200],
        "summary": final[:4000],
        "tool_calls": log_rows,
        "rounds": len(log_rows),
    }


async def run_subagents_parallel(
    calls: list[dict[str, str]],
    *,
    memory: Any = None,
    session_id: str = "",
    read_file_fn: Any = None,
) -> list[dict[str, Any]]:
    import asyncio
    jobs = [
        run_subagent(
            c.get("slug") or "",
            c.get("task") or "",
            memory=memory,
            session_id=session_id,
            read_file_fn=read_file_fn,
        )
        for c in (calls or [])[:MAX_PARALLEL]
    ]
    if not jobs:
        return []
    return list(await asyncio.gather(*jobs, return_exceptions=False))


def format_subagent_results(results: list[dict[str, Any]]) -> str:
    parts = []
    for r in results:
        head = f"[{r.get('slug')}] {'OK' if r.get('ok') else 'FAIL'}"
        body = r.get("summary") or r.get("error") or ""
        parts.append(f"{head}\n{body}")
    return "\n\n".join(parts)
