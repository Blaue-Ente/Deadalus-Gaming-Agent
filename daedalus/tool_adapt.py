"""Adapters between live tool strings/dataclasses and daedalus.contracts.

Unused by the agent loop. Do not import this module from agent.py,
native_tools.py, app/, or tool_protocol.execute.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from typing import Any

from daedalus.contracts import (
    ArtifactRef,
    ErrorClass,
    Observation,
    PermissionRequirement,
    SideEffectClass,
    ToolError,
    ToolLogRow,
    ToolObservation,
    ToolRequest,
    ToolResult,
    ToolRisk,
    ToolSource,
    ToolSpec,
    overlay_for,
)
from daedalus.tool_protocol import request_from_marker

_PERM_LINE = re.compile(r"Permission\s*:\s*(\S+)")
_UNSET = object()


def legacy_log_flags(text: str) -> tuple[bool, bool]:
    """Exact native_tools tool_log heuristic: (ok, blocked)."""
    blob = text or ""
    blocked = "BLOCKED" in blob
    ok = blocked or ("FAILED" not in blob and "ERROR" not in blob)
    return ok, blocked


def protocol_ok(text: str) -> bool:
    """Exact tool_protocol.execute ok rule."""
    blob = text or ""
    failed = "FAILED" in blob or "ERROR" in blob or "unknown native tool" in blob
    return not failed


def error_from_legacy_text(name: str, text: str) -> ToolError | None:
    """Typed class for a legacy result string. Independent of ok/blocked flags."""
    blob = text or ""
    tool = (name or "").strip()
    if "unknown native tool" in blob:
        return ToolError(
            error_class=ErrorClass.UNKNOWN_TOOL,
            message=blob.strip().splitlines()[0][:240] if blob.strip() else "unknown native tool",
            tool=tool,
        )
    if "PERMISSION DENIED" in blob:
        perm = ""
        hit = _PERM_LINE.search(blob)
        if hit:
            perm = hit.group(1)
        return ToolError(
            error_class=ErrorClass.PERMISSION_DENIED,
            message=blob.strip().splitlines()[0][:240] if blob.strip() else "permission denied",
            tool=tool,
            perm_class=perm,
            retryable=True,
        )
    if "OFFLINE KIT" in blob:
        return ToolError(
            error_class=ErrorClass.OFFLINE,
            message=blob.strip().splitlines()[0][:240] if blob.strip() else "offline",
            tool=tool,
            retryable=True,
        )
    if "BLOCKED" in blob:
        return ToolError(
            error_class=ErrorClass.TOOL_BLOCKED,
            message=blob.strip().splitlines()[0][:240] if blob.strip() else "blocked",
            tool=tool,
        )
    if "FAILED" in blob or "ERROR" in blob:
        return ToolError(
            error_class=ErrorClass.TOOL_FAILED,
            message=blob.strip().splitlines()[0][:240] if blob.strip() else "failed",
            tool=tool,
        )
    return None


def result_from_legacy_text(
    name: str,
    text: str,
    *,
    source: ToolSource | str = ToolSource.NATIVE,
) -> ToolResult:
    ok, blocked = legacy_log_flags(text)
    return ToolResult(
        name=name,
        ok=ok,
        text=text,
        source=source,
        blocked=blocked,
        error=error_from_legacy_text(name, text),
    )


def log_row_from_legacy(name: str, args: Mapping[str, Any] | None, text: str) -> ToolLogRow:
    ok, blocked = legacy_log_flags(text)
    return ToolLogRow(tool=name, input=dict(args or {}), ok=ok, blocked=blocked)


def _as_mapping(obj: Any) -> dict[str, Any]:
    if isinstance(obj, Mapping):
        return dict(obj)
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    data: dict[str, Any] = {}
    for key in ("name", "args", "source", "marker", "ok", "text", "data", "blocked", "tool", "input", "head"):
        if hasattr(obj, key):
            data[key] = getattr(obj, key)
    return data


def contract_request(runtime_req: Any) -> ToolRequest:
    if isinstance(runtime_req, ToolRequest):
        return runtime_req
    data = _as_mapping(runtime_req)
    return ToolRequest.model_validate(
        {
            "name": data.get("name") or "",
            "args": dict(data.get("args") or {}),
            "source": data.get("source") or ToolSource.NATIVE,
            "marker": data.get("marker") or "",
        }
    )


def contract_result(runtime_res: Any, *, text: str | None = None) -> ToolResult:
    if isinstance(runtime_res, ToolResult) and text is None:
        return runtime_res
    data = _as_mapping(runtime_res)
    blob = text if text is not None else str(data.get("text") or "")
    name = str(data.get("name") or "")
    blocked = data.get("blocked")
    if blocked is None:
        _ok, blocked = legacy_log_flags(blob)
    err = data.get("error")
    if err is None:
        err = error_from_legacy_text(name, blob)
    payload = {
        "name": name,
        "ok": bool(data.get("ok")),
        "text": blob if text is not None else str(data.get("text") or blob),
        "data": dict(data.get("data") or {}),
        "source": data.get("source") or ToolSource.NATIVE,
        "blocked": bool(blocked),
        "error": err,
        "artifacts": data.get("artifacts") or (),
        "risk": data.get("risk"),
    }
    if text is not None:
        payload["text"] = text
    return ToolResult.model_validate(payload)


def observation_from_mapping(row: Mapping[str, Any]) -> ToolObservation:
    args = row.get("args")
    if args is None:
        args = row.get("input") or {}
    name = str(row.get("tool") or row.get("name") or "")
    head = str(row.get("head") or "")
    blob = str(row.get("text") or head)
    if "ok" in row:
        ok = bool(row["ok"])
    else:
        ok, _ = legacy_log_flags(blob)
    if "blocked" in row:
        blocked = bool(row["blocked"])
    else:
        _, blocked = legacy_log_flags(blob)
    err = row.get("error")
    if err is None and blob:
        err = error_from_legacy_text(name, blob)
    return ToolObservation(
        tool=name,
        ok=ok,
        blocked=blocked,
        args=dict(args or {}),
        head=head or blob[:240],
        error=err,
    )


def _meta_field(meta: Any, key: str, default: Any = ()) -> Any:
    if meta is None:
        return default
    if isinstance(meta, Mapping):
        return meta.get(key, default)
    return getattr(meta, key, default)


def spec_from_native(
    raw: Mapping[str, Any],
    *,
    meta: Any = None,
    perm: Any = _UNSET,
) -> ToolSpec:
    name = str(raw.get("name") or "")
    side, risk, overlay_perm = overlay_for(name)
    if perm is _UNSET:
        perm_id = overlay_perm or None
    else:
        perm_id = perm or None
    permission = None
    if perm_id:
        permission = PermissionRequirement(perm_class=str(perm_id), fail_open=True)
    aliases = tuple(str(a) for a in (_meta_field(meta, "aliases", ()) or ()))
    tags = tuple(str(t) for t in (_meta_field(meta, "tags", ()) or ()))
    from daedalus.offline import NETWORK_TOOLS

    return ToolSpec(
        name=name,
        description=str(raw.get("description") or ""),
        input_schema=dict(raw.get("input_schema") or {}),
        side_effect=side,
        risk=risk,
        permission=permission,
        aliases=aliases,
        need_marker=str(_meta_field(meta, "need_marker", "") or ""),
        tags=tags,
        module=str(_meta_field(meta, "module", "") or ""),
        when=str(_meta_field(meta, "when", "") or ""),
        network=name in NETWORK_TOOLS,
    )


def live_specs() -> tuple[ToolSpec, ...]:
    from daedalus.native_tools import NATIVE_TOOL_SPECS, PERM_MAP
    from daedalus.tool_catalog import native_entries

    entries = {e.name: e for e in native_entries()}
    return tuple(
        spec_from_native(raw, meta=entries.get(str(raw["name"])), perm=PERM_MAP.get(str(raw["name"])))
        for raw in NATIVE_TOOL_SPECS
    )


def contract_request_from_marker(
    name: str,
    payload: str,
    *,
    extra: dict[str, Any] | None = None,
) -> ToolRequest | None:
    req = request_from_marker(name, payload, extra=extra)
    if req is None:
        return None
    return contract_request(req)


# Re-export aliases used by tests / future registry tickets.
__all__ = [
    "ArtifactRef",
    "Observation",
    "SideEffectClass",
    "ToolRisk",
    "contract_request",
    "contract_request_from_marker",
    "contract_result",
    "error_from_legacy_text",
    "legacy_log_flags",
    "live_specs",
    "log_row_from_legacy",
    "observation_from_mapping",
    "protocol_ok",
    "result_from_legacy_text",
    "spec_from_native",
]
