"""Parse tool calls dumped as JSON/XML in model prose.

Some cheap OpenAI-compatible models ignore native ``tool_calls`` and print::

    {"tool": "math3d", "arguments": {"op": "quat_slerp", ...}}

or Laguna-style ``<tool_call>name<arg_key>k</arg_key><arg_value>v</arg_value>``.
Those must execute like function calls — they are not documentation.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

_DECODER = json.JSONDecoder()

_ALIASES = {
    "playground_write": "write_file",
    "playground_read": "read_file",
    "need_playground_write": "write_file",
    "need_playground_new": "playground_new",
    "need_playground_run": "run_command",
    "shell": "run_command",
}

_XML_CALL = re.compile(
    r"<tool_call>\s*([a-zA-Z0-9_]+)(.*?)</tool_call>",
    re.DOTALL | re.IGNORECASE,
)
_XML_ARG = re.compile(
    r"<arg_key>\s*([^<]+?)\s*</arg_key>\s*<arg_value>(.*?)</arg_value>",
    re.DOTALL | re.IGNORECASE,
)


@dataclass
class ToolDump:
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    start: int = 0
    end: int = 0


def canonical_dump_name(raw: str) -> str:
    name = (raw or "").strip()
    mapped = _ALIASES.get(name.lower())
    if mapped:
        return mapped
    try:
        from .tool_catalog import canonical
        return canonical(name)
    except Exception:
        return name


def _normalize_args(name: str, args: dict[str, Any]) -> dict[str, Any]:
    out = dict(args or {})
    if name == "playground_new":
        if "kind" not in out and out.get("type"):
            out["kind"] = out.get("type")
        if "kind" not in out and out.get("template"):
            out["kind"] = out.get("template")
    if name == "write_file" and "content" not in out and "file" in out:
        out["content"] = out.get("file")
    if name == "math3d" and "op" not in out and out.get("action"):
        out["op"] = out.get("action")
    if name == "run_command" and "command" not in out and out.get("cmd"):
        out["command"] = out.get("cmd")
    return out


def extract_tool_dumps(text: str | None, *, limit: int = 8) -> list[ToolDump]:
    """Extract dumped tool calls in document order. Caps at ``limit``."""
    raw = text or ""
    if not raw.strip():
        return []
    found: list[ToolDump] = []
    occupied: list[tuple[int, int]] = []

    def _overlaps(start: int, end: int) -> bool:
        return any(start < b and end > a for a, b in occupied)

    idx = 0
    while idx < len(raw) and len(found) < limit:
        brace = raw.find("{", idx)
        if brace < 0:
            break
        try:
            obj, end = _DECODER.raw_decode(raw, brace)
        except json.JSONDecodeError:
            idx = brace + 1
            continue
        idx = end
        if not isinstance(obj, dict):
            continue
        tool = obj.get("tool") or obj.get("name")
        args = obj.get("arguments") or obj.get("input") or obj.get("args")
        if not isinstance(tool, str) or not tool.strip():
            continue
        if args is None:
            args = {k: v for k, v in obj.items() if k not in ("tool", "name")}
        if not isinstance(args, dict):
            continue
        name = canonical_dump_name(tool)
        if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{1,40}", name):
            continue
        found.append(ToolDump(name=name, args=_normalize_args(name, args), start=brace, end=end))
        occupied.append((brace, end))

    for m in _XML_CALL.finditer(raw):
        if len(found) >= limit:
            break
        start, end = m.start(), m.end()
        if _overlaps(start, end):
            continue
        name = canonical_dump_name(m.group(1))
        args: dict[str, Any] = {}
        for km, vm in _XML_ARG.findall(m.group(2) or ""):
            args[(km or "").strip()] = (vm or "").strip()
        found.append(ToolDump(name=name, args=_normalize_args(name, args), start=start, end=end))
        occupied.append((start, end))

    found.sort(key=lambda d: d.start)
    return found[:limit]


def strip_tool_dumps(text: str, dumps: list[ToolDump]) -> str:
    if not dumps:
        return text or ""
    out = text or ""
    for d in sorted(dumps, key=lambda x: x.start, reverse=True):
        out = out[: d.start] + out[d.end :]
    return out.strip()
