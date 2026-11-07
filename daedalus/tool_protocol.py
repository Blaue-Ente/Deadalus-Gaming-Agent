"""One typed tool protocol for native tools and NEED_* markers.

Native names are canonical. Markers are a text encoding of the same calls.
Do not add new NEED_* names — extend ``NATIVE_TOOL_SPECS`` instead.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

# Canonical native name ← leftover marker (no new markers).
MARKER_TO_NATIVE: dict[str, str] = {
    "NEED_READ_FILE": "read_file",
    "NEED_WEB": "web_search",
    "NEED_SEARCH": "web_search",
    "NEED_WEB_FETCH": "web_fetch",
    "NEED_CODE_SEARCH": "code_search",
    "NEED_ENDPOINTS": "list_endpoints",
    "NEED_MODULES": "list_modules",
    "NEED_PG": "postgres_query",
    "NEED_PLAYGROUND_WRITE": "write_file",
    "NEED_PLAYGROUND_EDIT": "edit_file",
    "NEED_PLAYGROUND_RUN": "run_command",
    "NEED_PLAYGROUND_NEW": "playground_new",
    "NEED_PLAYGROUND_READ": "read_file",
    "NEED_SUBAGENT": "spawn_subagent",
    "NEED_VERIFY": "playtest",
}


@dataclass
class ToolRequest:
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    source: str = "native"
    marker: str = ""


@dataclass
class ToolResult:
    name: str
    ok: bool
    text: str
    data: dict[str, Any] = field(default_factory=dict)
    source: str = "native"

    def as_log(self) -> dict[str, Any]:
        return {"tool": self.name, "ok": self.ok, "source": self.source}


def canonical_name(raw_name: str) -> str:
    name = (raw_name or "").strip()
    return MARKER_TO_NATIVE.get(name, name)


def request_from_marker(name: str, payload: str, *, extra: dict[str, Any] | None = None) -> ToolRequest | None:
    """Map a parsed NEED_* payload to a native ToolRequest, or None if unmapped."""
    native = MARKER_TO_NATIVE.get(name)
    if not native:
        return None
    args: dict[str, Any] = dict(extra or {})
    blob = (payload or "").strip()
    if native == "read_file":
        args.setdefault("path", blob.split("|", 1)[0].strip())
    elif native in ("web_search", "code_search"):
        args.setdefault("query", blob)
    elif native == "web_fetch":
        args.setdefault("url", blob)
    elif native in ("list_endpoints", "list_modules"):
        args.setdefault("filter", blob or "*")
    elif native == "postgres_query":
        args.setdefault("sql", blob)
    elif native == "write_file":
        pass  # extra must provide slug/path/content
    elif native == "run_command":
        args.setdefault("slug", blob)
        args.setdefault("command", "true")
    elif native == "playground_new":
        parts = [p.strip() for p in blob.split("|")]
        if parts:
            args.setdefault("name", parts[0])
        if len(parts) > 1 and parts[1]:
            args.setdefault("kind", parts[1])
        if len(parts) > 2 and parts[2]:
            args.setdefault("description", parts[2])
    elif native == "spawn_subagent":
        parts = [p.strip() for p in blob.split("|")]
        if parts:
            args.setdefault("slug", parts[0])
        if len(parts) > 1:
            args.setdefault("task", parts[1])
        if len(parts) > 2:
            args.setdefault("role", parts[2])
    elif native == "playtest":
        args.setdefault("slug", blob)
    return ToolRequest(name=native, args=args, source="marker", marker=name)


async def execute(
    request: ToolRequest,
    *,
    memory: Any = None,
    session_id: str = "",
    read_file_fn: Callable[..., dict] | None = None,
) -> ToolResult:
    from .native_tools import execute_native_tool

    reader = read_file_fn or _jail_read_file
    text = await execute_native_tool(
        request.name,
        request.args,
        memory=memory,
        session_id=session_id,
        read_file_fn=reader,
    )
    failed = "FAILED" in text or "ERROR" in text or "unknown native tool" in text
    return ToolResult(
        name=request.name,
        ok=not failed,
        text=text,
        source=request.source,
    )


def _jail_read_file(path: str, line_start: int | None = None, line_end: int | None = None) -> dict:
    from pathlib import Path

    from .paths import product_root
    from .shell_center import resolve_cwd

    rel = (path or "").strip() or "."
    try:
        cand = Path(rel)
        if cand.is_absolute():
            target = cand.resolve()
            product_root().resolve()  # jail root exists
            target.relative_to(product_root().resolve())
        else:
            root = resolve_cwd(".")
            target = (root / rel).resolve()
            target.relative_to(root.resolve())
    except (ValueError, OSError) as exc:
        return {"ok": False, "error": str(exc)[:200]}
    if not target.is_file():
        return {"ok": False, "error": f"file not found: {rel}"}
    text = target.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines(keepends=True)
    if line_start is not None and line_end is not None:
        text = "".join(lines[max(0, line_start - 1):line_end])
    return {"ok": True, "path": rel, "content": text[:8000], "truncated": len(text) > 8000}
