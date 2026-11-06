"""Slim local build prompt when Ollama's window cannot hold the full one.

The OpenAI-compatible ``/v1`` route ignores ``num_ctx``. A tool-bearing
playground build prompt is about 3.8k tokens before the user turn, and
Ollama's default loaded window is 4096, so the server drops the head of
the prompt (system + tools) with no error. This module probes ``/api/show``
and ``/api/ps`` and, when the window is too small, swaps in a short system
prompt and a short tool list.

Whisper and craft stay on the local brain. Epic is a separate provider
path in ``task_effort`` (cloud for that turn when a key is set). The
wardrobe default model id is not changed here.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from .local_llm import (
    probe_ollama_model,
    tools_capability_warning,
)
from .scratch_journal import estimate_tokens

log = logging.getLogger(__name__)

# Measured on the playground Vite/Phaser build prompt (system + tool schemas)
# before the user turn. Doctor uses this floor; a live turn uses its own size.
BUILD_PROMPT_TOKENS = 3800
REPLY_RESERVE_TOKENS = 768
DOCTOR_CONTEXT_FLOOR = BUILD_PROMPT_TOKENS + REPLY_RESERVE_TOKENS

_SLIM_TOOL_NAMES = ("playground_new", "write_file", "edit_file", "read_file", "list_dir")

SLIM_LOCAL_SYSTEM = (
    "You are Daedalus on a small local context window. "
    "Build one playable slice with playground_new and write_file. "
    "Do not map the harness or search the disk. "
    "Use tool calls, not a plan essay."
)

_warned: set[str] = set()


def clear_warning_log() -> None:
    _warned.clear()


def _warn_once(key: str, message: str) -> None:
    if key in _warned:
        return
    _warned.add(key)
    log.warning("%s", message)


def context_warning(model: str, context: int, source: str, prompt_tokens: int) -> str:
    return (
        f"Ollama model {model} context is {context} tokens ({source}), "
        f"smaller than the tool-bearing build prompt (~{prompt_tokens} tokens before the reply). "
        "Using the slim local profile instead of a silently truncated prompt. "
        "Whisper and craft stay on this local brain. "
        "Epic uses a cloud provider for that turn when a cloud key is set. "
        "To widen the window, set OLLAMA_CONTEXT_LENGTH=32768 on the Ollama process and restart it. "
        "The OpenAI /v1 endpoint ignores num_ctx."
    )


def _prompt_tokens(system: str, tools: list[dict[str, Any]] | None) -> int:
    blob = json.dumps(tools or [], ensure_ascii=False, default=str)
    return estimate_tokens(system) + estimate_tokens(blob)


def _shrink_tool(spec: dict[str, Any]) -> dict[str, Any]:
    schema = spec.get("input_schema") if isinstance(spec.get("input_schema"), dict) else {}
    props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = [str(r) for r in (schema.get("required") or []) if isinstance(r, str)]
    keys = list(required)
    for key in props:
        if key not in keys:
            keys.append(key)
    short: dict[str, Any] = {}
    for key in keys[:6]:
        raw = props.get(key)
        kind = "string"
        if isinstance(raw, dict) and raw.get("type"):
            kind = str(raw["type"])
        short[key] = {"type": kind}
    desc = str(spec.get("description") or "")
    if len(desc) > 140:
        desc = desc[:137].rstrip() + "..."
    return {
        "name": spec.get("name"),
        "description": desc,
        "input_schema": {
            "type": "object",
            "properties": short,
            "required": [r for r in required if r in short],
        },
    }


def slim_tool_specs(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    rows = [t for t in (tools or []) if isinstance(t, dict) and t.get("name")]
    named = [t for t in rows if t.get("name") in _SLIM_TOOL_NAMES]
    if named:
        order = {name: i for i, name in enumerate(_SLIM_TOOL_NAMES)}
        chosen = sorted(named, key=lambda t: order.get(str(t.get("name")), 99))
    else:
        chosen = rows[:4]
    return [_shrink_tool(t) for t in chosen]


def _message_tokens(messages: list[dict[str, Any]]) -> int:
    if not messages:
        return 0
    return estimate_tokens(json.dumps(messages, ensure_ascii=False, default=str))


def _trim_messages(messages: list[dict[str, Any]], token_budget: int) -> tuple[list[dict[str, Any]], bool]:
    """Shrink oldest string contents so the system prompt stays inside the window.

    Ollama drops the front of an over-long prompt. The system message is first,
    so an untrimmed user turn would erase the tool instructions.
    """
    import copy

    out = copy.deepcopy(messages or [])
    if token_budget <= 0 or _message_tokens(out) <= token_budget:
        return out, False
    changed = False
    for _ in range(12):
        if _message_tokens(out) <= token_budget:
            break
        shrunk = False
        for msg in out:
            content = msg.get("content")
            if isinstance(content, str) and len(content) > 180:
                msg["content"] = content[: max(120, len(content) // 2)] + "\n[trimmed to fit local context]"
                changed = True
                shrunk = True
                break
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    text = block.get("text")
                    if isinstance(text, str) and len(text) > 180:
                        block["text"] = text[: max(120, len(text) // 2)] + "\n[trimmed to fit local context]"
                        changed = True
                        shrunk = True
                        break
                if shrunk:
                    break
        if not shrunk:
            break
    return out, changed


def fit_ollama_turn(
    system: str,
    tools: list[dict[str, Any]] | None,
    *,
    model: str,
    base_url: str = "",
    max_tokens: int = 1024,
    messages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return system, tools, messages, and max_tokens that fit the probed window.

    When the probe fails, the original prompt is kept and ``warnings`` says why.
    A small window replaces the prompt with ``SLIM_LOCAL_SYSTEM`` and a short
    tool list. ``max_tokens`` is capped so the completion cannot eat the prompt.
    """
    original_tools = list(tools or [])
    original_messages = list(messages or [])
    probe = probe_ollama_model(model, base_url or None)
    warnings: list[str] = []
    if probe.get("warning"):
        warnings.append(str(probe["warning"]))
    if not probe.get("ok"):
        return {
            "system": system,
            "tools": original_tools,
            "messages": original_messages,
            "max_tokens": max_tokens,
            "slim": False,
            "trimmed": False,
            "warnings": warnings,
            "probe": probe,
        }

    ctx = int(probe.get("context_length") or 0)
    full_tokens = _prompt_tokens(system, original_tools)
    slim = bool(ctx) and ctx < full_tokens + REPLY_RESERVE_TOKENS
    used_system = system
    used_tools = original_tools
    prompt_tokens = full_tokens
    if slim:
        used_system = SLIM_LOCAL_SYSTEM
        used_tools = slim_tool_specs(original_tools)
        prompt_tokens = _prompt_tokens(used_system, used_tools)
        message = context_warning(
            str(probe.get("model") or model),
            ctx,
            str(probe.get("context_source") or "unknown"),
            full_tokens,
        )
        warnings.append(message)
        _warn_once(f"ctx:{probe.get('model')}:{ctx}", message)

    trimmed = False
    used_messages = original_messages
    if ctx:
        room = ctx - prompt_tokens - REPLY_RESERVE_TOKENS
        used_messages, trimmed = _trim_messages(original_messages, max(128, room))
        if trimmed:
            note = (
                f"Trimmed the Ollama conversation to fit a {ctx}-token window "
                "so the system prompt is not dropped."
            )
            warnings.append(note)
            _warn_once(f"trim:{probe.get('model')}:{ctx}", note)
        capped = max(256, ctx - prompt_tokens - 32)
        max_tokens = min(int(max_tokens), capped)

    return {
        "system": used_system,
        "tools": used_tools,
        "messages": used_messages,
        "max_tokens": int(max_tokens),
        "slim": slim,
        "trimmed": trimmed,
        "warnings": warnings,
        "probe": probe,
    }


def doctor_findings(model: str | None = None, base_url: str | None = None) -> list[dict[str, Any]]:
    """Connectors rows. Empty when Ollama cannot be asked (the down-row covers that)."""
    probe = probe_ollama_model(model, base_url)
    if not probe.get("ok"):
        if probe.get("warning"):
            return [{
                "id": "ollama-probe-failed",
                "level": "medium",
                "title": "Could not read the Ollama model",
                "body": str(probe["warning"]),
                "group": "llm",
            }]
        return []
    rows: list[dict[str, Any]] = []
    name = str(probe.get("model") or model or "")
    if not probe.get("tools"):
        rows.append({
            "id": "ollama-no-tools",
            "level": "high",
            "title": f"{name} does not advertise tools",
            "body": tools_capability_warning(
                name,
                list(probe.get("capabilities") or []),
                reported=bool(probe.get("capabilities_reported")),
            ),
            "group": "llm",
        })
    ctx = probe.get("context_length")
    if isinstance(ctx, int) and ctx < DOCTOR_CONTEXT_FLOOR:
        rows.append({
            "id": "ollama-small-context",
            "level": "high",
            "title": f"Ollama context is {ctx} tokens",
            "body": context_warning(
                name, ctx, str(probe.get("context_source") or ""), BUILD_PROMPT_TOKENS,
            ),
            "group": "llm",
        })
    return rows


def probe_summary(model: str | None = None, base_url: str | None = None) -> dict[str, Any]:
    """Sanitized brain card. No secrets."""
    probe = probe_ollama_model(model, base_url)
    return {
        "ok": bool(probe.get("ok")),
        "model": probe.get("model") or "",
        "tools": probe.get("tools"),
        "context_length": probe.get("context_length"),
        "context_source": probe.get("context_source") or "",
        "model_context": probe.get("model_context"),
        "warning": str(probe.get("warning") or ""),
        "error": str(probe.get("error") or ""),
    }
