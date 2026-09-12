"""ToolRegistry — register / resolve / authorize / execute / describe.

P0.04 migrates only read_file, list_dir, and code_search. Live dispatch still
goes through execute_native_tool; this module is the dual-path for those three.
Do not import this module from agent.py.
"""
from __future__ import annotations

import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from daedalus.contracts import PermissionDecision, ToolRequest, ToolResult, ToolSpec

READ_TOOLS: frozenset[str] = frozenset({"read_file", "list_dir", "code_search"})

Handler = Callable[[dict[str, Any], "ToolContext"], Awaitable[str]]


@dataclass
class ToolContext:
    memory: Any = None
    session_id: str = ""
    read_file_fn: Callable[..., dict] | None = None


@dataclass
class RegisteredTool:
    name: str
    handler: Handler
    spec: ToolSpec


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, name: str, handler: Handler, spec: ToolSpec) -> None:
        key = (name or "").strip()
        if not key:
            raise ValueError("tool name required")
        self._tools[key] = RegisteredTool(name=key, handler=handler, spec=spec)

    def resolve(self, raw_name: str) -> RegisteredTool | None:
        key = _canonical(raw_name)
        if not key:
            return None
        return self._tools.get(key)

    def get_exact(self, name: str) -> RegisteredTool | None:
        return self._tools.get(name)

    def authorize(self, name: str, ctx: ToolContext | None = None) -> PermissionDecision:
        del ctx  # reserved; jail stays in the handler
        hit = self.resolve(name)
        tool = hit.name if hit else (name or "").strip()
        from daedalus.native_tools import PERM_MAP, before_tool_call

        allowed, denial = before_tool_call(tool)
        perm = PERM_MAP.get(tool) or ""
        if not allowed:
            return PermissionDecision(
                allowed=False, perm_class=str(perm), fail_open=True, reason=denial,
            )
        try:
            from daedalus.offline import deny
            blocked = deny(tool)
        except Exception:
            blocked = None
        if blocked:
            return PermissionDecision(
                allowed=False, perm_class=str(perm), fail_open=True, reason=blocked,
            )
        return PermissionDecision(allowed=True, perm_class=str(perm), fail_open=True)

    async def execute(self, request: Any, ctx: ToolContext | None = None) -> ToolResult:
        from daedalus.tool_adapt import contract_request, result_from_legacy_text

        req = request if isinstance(request, ToolRequest) else contract_request(request)
        context = ctx or ToolContext()
        decision = self.authorize(req.name, context)
        if not decision.allowed:
            return result_from_legacy_text(req.name, decision.reason)
        hit = self.resolve(req.name)
        if hit is None:
            return result_from_legacy_text(
                req.name, f"── unknown native tool: {req.name} ──",
            )
        try:
            text = await hit.handler(dict(req.args or {}), context)
        except Exception as exc:
            text = f"── {hit.name} ERROR: {exc} ──"
        return result_from_legacy_text(hit.name, text)

    def describe(self, name: str) -> ToolSpec | None:
        hit = self.resolve(name)
        return None if hit is None else hit.spec


def registry_enabled() -> bool:
    flag = os.environ.get("DAEDALUS_TOOL_REGISTRY", "").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return False
    return True


def _canonical(raw_name: str) -> str:
    from daedalus.tool_catalog import canonical
    from daedalus.tool_protocol import canonical_name

    name = (raw_name or "").strip()
    if not name:
        return ""
    mapped = canonical(name)
    if mapped:
        return mapped
    return canonical_name(name)


def _wrap_sync(fn: Callable[..., str]) -> Handler:
    async def handler(args: dict[str, Any], ctx: ToolContext) -> str:
        return fn(args, session_id=ctx.session_id, read_file_fn=ctx.read_file_fn)

    return handler


def _wrap_async(fn: Callable[..., Awaitable[str]]) -> Handler:
    async def handler(args: dict[str, Any], ctx: ToolContext) -> str:
        return await fn(args, session_id=ctx.session_id, read_file_fn=ctx.read_file_fn)

    return handler


def bootstrap_read_tools(registry: ToolRegistry | None = None) -> ToolRegistry:
    """Register the P0.04 read tools. Imports native helpers lazily."""
    from daedalus.native_tools import (
        NATIVE_TOOL_SPECS,
        PERM_MAP,
        _run_code_search,
        _run_list_dir,
        _run_read_file,
    )
    from daedalus.tool_adapt import spec_from_native
    from daedalus.tool_catalog import native_entries

    target = registry if registry is not None else ToolRegistry()
    handlers: Mapping[str, Handler] = {
        "read_file": _wrap_sync(_run_read_file),
        "list_dir": _wrap_sync(_run_list_dir),
        "code_search": _wrap_async(_run_code_search),
    }
    entries = {e.name: e for e in native_entries()}
    by_name = {str(t["name"]): t for t in NATIVE_TOOL_SPECS}
    for name, handler in handlers.items():
        raw = by_name[name]
        spec = spec_from_native(raw, meta=entries.get(name), perm=PERM_MAP.get(name))
        target.register(name, handler, spec)
    return target


_default: ToolRegistry | None = None
_bootstrapped = False


def default_registry() -> ToolRegistry:
    global _default, _bootstrapped
    if _default is None:
        _default = ToolRegistry()
    if not _bootstrapped:
        bootstrap_read_tools(_default)
        _bootstrapped = True
    return _default


def reset_registry_for_tests() -> None:
    global _default, _bootstrapped
    _default = None
    _bootstrapped = False


async def try_dispatch(
    name: str,
    args: dict[str, Any],
    *,
    memory: Any,
    session_id: str,
    read_file_fn: Callable[..., dict],
) -> str | None:
    """Live hook for execute_native_tool. Exact native names only (no aliases)."""
    if not registry_enabled():
        return None
    if name not in READ_TOOLS:
        return None
    hit = default_registry().get_exact(name)
    if hit is None:
        return None
    ctx = ToolContext(memory=memory, session_id=session_id, read_file_fn=read_file_fn)
    decision = default_registry().authorize(name, ctx)
    if not decision.allowed:
        return decision.reason
    return await hit.handler(args, ctx)


__all__ = [
    "READ_TOOLS",
    "RegisteredTool",
    "ToolContext",
    "ToolRegistry",
    "bootstrap_read_tools",
    "default_registry",
    "registry_enabled",
    "reset_registry_for_tests",
    "try_dispatch",
]
