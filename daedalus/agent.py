"""Daedalus — main orchestrator.

Pulls together:
- DaedalusMemory (ChromaDB persistent storage)
- DaedalusKnowledge (boot-time skill+plan indexer)
- Claude LLM (via Replit AI Integrations proxy)
- Web search + GitHub finder (existing ADN modules) for live context
- Worker agents (parallel stateless LLM calls via NEED_WORKER markers)

Public API:
- chat(message, session_id)             — conversational (plain text reply)
- generate(task, session_id, language)  — code generation (JSON reply)
- explain(code, session_id, language)   — explain existing code
- review(code, session_id, language, focus) — code review
- stats()                               — memory + knowledge stats
- clear_session(session_id)             — wipe one session's memory

All entry points automatically:
1. Retrieve relevant knowledge (skills/plans) via semantic search
2. Retrieve relevant past memory across all sessions
3. Pull recent chronological history of current session
4. Inject all of the above into the LLM context
5. Capture the new user turn and assistant response into memory
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from .identity import DAEDALUS_PERSONA, build_persona
from . import dod as _dod
from . import scratch_journal as _journal
from . import task_state as _task_state
from . import error_brain as _error_brain
from . import playbooks as _playbooks
from . import plan_lock as _plan_lock
from . import daedalusignore as _daedalusignore
from .improvements import (
    get_error_tracker,
    call_with_fallback,
    record_tool_error_for_promotion,
    record_outcome,
    question_budget_check_and_increment,
    mark_schema_read,
    needs_read_first_hint,
    select_skills,
    review_gate_critique,
    detect_intent_without_action,
)
from .knowledge import DaedalusKnowledge
from .memory import DaedalusMemory
from .memory_mem0 import Mem0Memory
from .workers import parse_worker_markers, call_workers_parallel, format_worker_results
from .mesh_tools import format_mesh_for_prompt, get_mesh_snapshot
from .reflection import run_reflection_cycle
from . import continuity as _continuity
from . import mode as _mode
from .tools import (
    web_search_tool, github_search_tool,
    postgres_query_tool, query_mesh_stats_tool,
    backup_chromadb_tool, list_storage_objects_tool,
    github_search_repos_tool, github_list_my_repos_tool,
    github_create_issue_tool, github_list_issues_tool,
    pypi_search_tool, npm_search_tool, docs_fetch_tool,
    dependency_analyze_tool, git_log_tool, git_diff_tool,
    code_sandbox_tool,
    list_endpoints_tool, list_modules_tool,
    describe_db_tool, search_code_tool,
    drp_law_tool, drp_cases_tool, drp_search_tool,
    web_fetch_tool, web_screenshot_tool,
)
from .batch_verify import (
    extract_batch_block, extract_verify_block,
    execute_batch, execute_verify,
    BATCH_BLOCK_RE, VERIFY_BLOCK_RE,
)
from .context_compaction import maybe_compact_session_context, track_files_from_text
from .edit_diff import extract_edit_markers, process_need_edit
from .native_tools import native_tools_enabled, run_native_discovery_pass
from .need_scan import scan_need_markers
from .session_branching import build_branch_context_block, branch_session as _branch_session_fn
from .steering_queue import pop_steering_messages

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class DaedalusError(Exception):
    """Base error for Daedalus."""


class DaedalusBadRequest(DaedalusError):
    """Invalid user input — maps to HTTP 400."""


# ---------------------------------------------------------------------------
# LLM client — AsyncAnthropic (native) + OpenAI-compat + prompt caching
# ---------------------------------------------------------------------------

_MODEL = "claude-sonnet-4-6"   # fallback if llm_config not available
_MAX_TOKENS = 8192

from .llm_config import get_config


async def _call_claude(system: str, messages: list[dict[str, Any]],
                       *,
                       use_cache_override: bool | None = None) -> str:
    """Unified async LLM call with rate-limit fallback chain.

    Tries primary provider first; on 429 / overload falls back to
    replit_openai gpt-5.4 → replit_gemini gemini-2.5-flash automatically.
    Cheap writers (DeepSeek / MiniMax / GLM) often have no image input —
    a proof PNG then 404s. Retry the same turn without vision; the text
    proof summary is enough for RETURN TO WRITER.
    """
    from .llm_client import (
        is_no_image_support,
        messages_have_images,
        strip_images_from_messages,
    )

    try:
        return await call_with_fallback(
            _call_claude_primary, system, messages,
            use_cache_override=use_cache_override,
        )
    except Exception as exc:
        if is_no_image_support(exc) and messages_have_images(messages):
            log.warning("persona LLM has no vision; retrying without proof frames")
            stripped = strip_images_from_messages(messages)
            return await call_with_fallback(
                _call_claude_primary, system, stripped,
                use_cache_override=use_cache_override,
            )
        raise


async def _call_claude_via_gateway(system: str, messages: list[dict[str, Any]], *,
                                   use_cache_override: bool | None = None) -> str:
    from .harness import gateway as gw

    res = gw.resolve("persona")
    do_cache = res.cfg.get("use_cache", True) if use_cache_override is None else use_cache_override
    result = await gw.call("persona", messages, system=system, max_tokens=_MAX_TOKENS,
                           use_cache=do_cache, client_id="daedalus")
    if not result.ok and not result.budget_stopped and result.error is not None:
        if result.error.type in ("auth", "routing") and result.error.message.startswith(("No API key", "No credentials")):
            raise DaedalusError(result.error.message)
    return gw.to_text_or_raise(result)


async def _call_claude_primary(system: str, messages: list[dict[str, Any]],
                                *,
                                use_cache_override: bool | None = None) -> str:
    """Direct provider call. Used by call_with_fallback.

    Goes through the harness gateway (model resolution, budget, images, typed errors, trace);
    ``DAEDALUS_GATEWAY_PERSONA=0`` restores the original provider code below.
    """
    from .harness import gateway as _gw
    if _gw.path_enabled("persona"):
        return await _call_claude_via_gateway(system, messages, use_cache_override=use_cache_override)
    import time as _t
    try:
        from argos_data_nexus.modules.llm_live_trace import record_llm_call
    except Exception:
        def record_llm_call(*_a, **_k):  # type: ignore
            return None

    cfg = get_config()
    provider = cfg.get("provider", "anthropic")
    model    = cfg.get("model", _MODEL)
    api_key  = cfg.get("api_key", "")
    base_url = cfg.get("base_url", "")
    sdk      = cfg.get("sdk", "anthropic")
    do_cache = cfg.get("use_cache", True) if use_cache_override is None else use_cache_override
    # Run-budget gate, same text and rule as llm_client: no provider call once the envelope is spent.
    from . import run_budget as _run_budget
    if _run_budget.would_block(provider):
        return _run_budget.stop_text()
    t0 = _t.perf_counter()
    err = ""
    try:
        if sdk == "anthropic":
            try:
                from anthropic import AsyncAnthropic
            except ImportError as exc:
                raise DaedalusError("anthropic SDK not installed") from exc
            if not api_key:
                raise DaedalusError(
                    "Anthropic API key not configured "
                    "(set DAEDALUS_API_KEY env var or configure in Admin › LLM)"
                )
            kwargs: dict = dict(api_key=api_key, max_retries=0)
            if base_url:
                kwargs["base_url"] = base_url

            client = AsyncAnthropic(**kwargs)

            if do_cache:
                system_param: list | str = [
                    {"type": "text", "text": system,
                     "cache_control": {"type": "ephemeral"}}
                ]
            else:
                system_param = system

            from .native_tools import to_anthropic_messages as _to_anth_msgs

            resp = await client.messages.create(
                model=model,
                max_tokens=_MAX_TOKENS,
                system=system_param,
                messages=_to_anth_msgs(messages),
            )
            try:
                from .llm_client import _meter
                _meter(resp, model=model, role="writer")
            except Exception:
                log.debug("persona metering skipped", exc_info=True)  # never fail a billed, successful call
            block = resp.content[0]
            return block.text if block.type == "text" else ""

        # OpenAI-compatible (openai SDK) — Groq, Gemini, OpenAI, custom
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise DaedalusError(
                "openai SDK not installed — run: pip install openai"
            ) from exc
        if not api_key:
            from .local_llm import dummy_api_key, is_local_provider
            if is_local_provider(provider) or cfg.get("local"):
                api_key = dummy_api_key(provider)
            else:
                raise DaedalusError(
                    f"API key not configured for provider '{provider}' "
                    "(set DAEDALUS_API_KEY env var or configure in Admin › LLM, "
                    "or DAEDALUS_PROVIDER=ollama for a local model)"
                )
        from .llm_config import openai_compat_headers
        oa_client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url or None,
            max_retries=1,
            default_headers=(openai_compat_headers(provider) or None),
        )
        oa_messages = [{"role": "system", "content": system}] + list(messages)
        resp = await oa_client.chat.completions.create(
            model=model,
            max_tokens=_MAX_TOKENS,
            messages=oa_messages,
        )
        try:
            from .llm_client import _meter
            _meter(resp, model=model, role="writer")
        except Exception:
            log.debug("persona metering skipped", exc_info=True)  # never fail a billed, successful call
        return resp.choices[0].message.content or ""
    except Exception as exc:
        err = str(exc)[:300]
        raise
    finally:
        try:
            record_llm_call(
                channel="daedalus_chat",
                provider=provider,
                model=model,
                model_requested=model,
                configured_provider=provider,
                configured_model=model,
                config_source=cfg.get("source", ""),
                client_id="daedalus",
                latency_ms=int((_t.perf_counter() - t0) * 1000),
                ok=not err,
                error=err,
            )
        except Exception:
            pass


async def _call_claude_with_tools(
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> dict[str, Any]:
    """Native tool round. Implementation lives in ``llm_client`` (no chroma)."""
    from .llm_client import call_with_tools
    return await call_with_tools(system, messages, tools, max_tokens=_MAX_TOKENS)


# ---------------------------------------------------------------------------
# Context formatting
# ---------------------------------------------------------------------------

def _format_injected_context(
    knowledge: list[dict[str, Any]],
    memory: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    session_recent: list[dict[str, Any]],
    lessons: list[dict[str, Any]] | None = None,
    episodes: list[dict[str, Any]] | None = None,
    mesh_block: str = "",
) -> str:
    parts: list[str] = []

    if mesh_block:
        parts.append(mesh_block)

    if lessons:
        parts.append("\n━━━ LESSONS YOU LEARNED (self-reflection) ━━━")
        for L in lessons:
            meta = L.get("metadata", {})
            parts.append(
                f"  • [{meta.get('kind','general')}] {L.get('content','')[:240]}"
            )

    if episodes:
        parts.append("\n━━━ EPISODIC MEMORIES (past session summaries) ━━━")
        for ep in episodes:
            meta = ep.get("metadata", {})
            parts.append(
                f"\n[session={meta.get('session_id','?')[:10]} "
                f"@ {meta.get('ended_at','?')[:19]} turns={meta.get('turn_count','?')}]"
            )
            parts.append(ep.get("content", "")[:1500])

    if knowledge:
        parts.append("\n━━━ RELEVANT IYVERIS KNOWLEDGE (auto-retrieved) ━━━")
        for k in knowledge:
            meta = k.get("metadata", {})
            title = meta.get("title", "?")
            src = meta.get("source", "?")
            parts.append(f"\n[{meta.get('category','?')}] {title}  ({src})")
            parts.append(k.get("content", "")[:1200])

    if memory:
        parts.append("\n━━━ RELEVANT PAST MEMORY (cross-session) ━━━")
        for m in memory:
            meta = m.get("metadata", {})
            parts.append(
                f"\n[{meta.get('role','?')} @ {meta.get('timestamp','?')[:19]} "
                f"session={meta.get('session_id','?')[:8]}]"
            )
            parts.append(m.get("content", "")[:800])

    if artifacts:
        parts.append("\n━━━ SIMILAR PAST ARTIFACTS YOU GENERATED ━━━")
        for a in artifacts:
            meta = a.get("metadata", {})
            parts.append(
                f"\n• {meta.get('filename','?')} ({meta.get('language','?')}) — "
                f"{meta.get('summary','')[:200]}"
            )

    if session_recent:
        parts.append("\n━━━ RECENT TURNS IN THIS SESSION (chronological) ━━━")
        for t in session_recent:
            meta = t.get("metadata", {})
            parts.append(
                f"\n[{meta.get('role','?')} @ {meta.get('timestamp','?')[:19]}]"
            )
            parts.append(t.get("content", "")[:800])

    if not parts:
        return ""
    return "\n".join(parts)


def _extract_marker(raw: str, name: str) -> str | None:
    """Pull a `<NEED_FOO:query text>` marker out of an LLM reply. Returns query or None."""
    if not raw or not name:
        return None
    m = re.search(rf"<{name}:\s*([^>]{{1,300}})>", raw)
    if not m:
        return None
    q = m.group(1).strip()
    return q or None


_SANDBOX_BLOCK_RE = re.compile(
    r"<NEED_SANDBOX>\s*\n(.*?)\n\s*</NEED_SANDBOX>",
    re.DOTALL,
)

# NEED_BATCH and NEED_VERIFY block patterns (from batch_verify.py)
# Defined here only as aliases — actual regexes live in batch_verify.py
_BATCH_BLOCK_RE = BATCH_BLOCK_RE
_VERIFY_BLOCK_RE = VERIFY_BLOCK_RE


def _extract_sandbox_code(raw: str) -> str | None:
    """Pull code from <NEED_SANDBOX>...</NEED_SANDBOX> block."""
    if not raw or "NEED_SANDBOX" not in raw:
        return None
    m = _SANDBOX_BLOCK_RE.search(raw)
    if not m:
        return None
    code = m.group(1).strip()
    return code or None


# ---------------------------------------------------------------------------
# Task markers — TASK_OPEN / TASK_DONE / TASK_REWORK / TASK_TOUCH
# ---------------------------------------------------------------------------

_TASK_OPEN_RE = re.compile(r"<TASK_OPEN:\s*([^|>\n]{1,80})\|\s*([^>\n]{1,400})>")
_TASK_DONE_RE = re.compile(r"<TASK_DONE:\s*([^>\n]{1,100})>")
_TASK_REWORK_RE = re.compile(r"<TASK_REWORK:\s*([^|>\n]{1,80})\|\s*([^>\n]{1,400})>")
_TASK_TOUCH_RE = re.compile(r"<TASK_TOUCH:\s*([^>\n]{1,100})>")
_MODE_RE = re.compile(r"<NEED_MODE:\s*(talk|build|auto)\s*>", re.IGNORECASE)


def process_mode_marker_only(raw: str) -> str:
    """Apply ONLY a NEED_MODE marker (last wins) and strip it from the text.

    Used on the cheap chat fast-path so a conversational reply can switch the
    conversation mode WITHOUT triggering any TASK_* / continuity side effects.
    """
    last = None
    for last in _MODE_RE.finditer(raw):
        pass
    if last:
        try:
            _mode.set_mode(last.group(1).strip().lower())
        except Exception as exc:
            log.debug("mode-only marker failed: %s", exc)
    return _MODE_RE.sub("", raw).strip()


def process_task_markers(raw: str, session_id: str) -> tuple[str, list[dict[str, Any]]]:
    """Execute TASK_* markers found in raw, remove them, return (cleaned_text, events).

    events = [{"action": "open|done|rework|touch", "task": {...}, "ok": bool, ...}]
    """
    if not raw:
        return raw, []
    events: list[dict[str, Any]] = []

    for m in _TASK_OPEN_RE.finditer(raw):
        title = m.group(1).strip()
        summary = m.group(2).strip()
        try:
            t = _continuity.create_task(title, summary, session_id=session_id)
            events.append({"action": "open", "task": t, "ok": True})
        except Exception as exc:
            events.append({"action": "open", "ok": False, "error": str(exc)})

    for m in _TASK_DONE_RE.finditer(raw):
        hint = m.group(1).strip()
        t = _continuity.find_task_by_hint(hint)
        if t:
            updated = _continuity.update_task(t["id"], status="done", add_session_id=session_id)
            events.append({"action": "done", "task": updated, "ok": True})
        else:
            events.append({"action": "done", "ok": False, "error": f"task not found: {hint}"})

    for m in _TASK_REWORK_RE.finditer(raw):
        hint = m.group(1).strip()
        reason = m.group(2).strip()
        t = _continuity.find_task_by_hint(hint)
        if t:
            new_summary = t.get("summary", "") + f" | Нуждае от преработка: {reason}"
            updated = _continuity.update_task(t["id"], status="rework",
                                              summary=new_summary, add_session_id=session_id)
            events.append({"action": "rework", "task": updated, "ok": True})
        else:
            events.append({"action": "rework", "ok": False, "error": f"task not found: {hint}"})

    for m in _TASK_TOUCH_RE.finditer(raw):
        hint = m.group(1).strip()
        t = _continuity.find_task_by_hint(hint)
        if t:
            updated = _continuity.update_task(t["id"], add_session_id=session_id)
            events.append({"action": "touch", "task": updated, "ok": True})
        else:
            events.append({"action": "touch", "ok": False, "error": f"task not found: {hint}"})

    # NEED_MODE — Дедал switches the conversation mode himself (talk/build/auto)
    _last_mode_m = None
    for _last_mode_m in _MODE_RE.finditer(raw):
        pass
    if _last_mode_m:
        _new_mode = _last_mode_m.group(1).strip().lower()
        try:
            _mode.set_mode(_new_mode)
            events.append({"action": "mode", "mode": _new_mode, "ok": True})
        except Exception as exc:
            events.append({"action": "mode", "ok": False, "error": str(exc)})

    cleaned = _TASK_OPEN_RE.sub("", raw)
    cleaned = _TASK_DONE_RE.sub("", cleaned)
    cleaned = _TASK_REWORK_RE.sub("", cleaned)
    cleaned = _TASK_TOUCH_RE.sub("", cleaned)
    cleaned = _MODE_RE.sub("", cleaned)

    if events:
        log.info("process_task_markers: %d events session=%s", len(events), session_id[:12])

    return cleaned.strip(), events


# NEED_READ_FILE: <NEED_READ_FILE: path/to/file> or <NEED_READ_FILE: path/to/file | 10-50>
_READ_FILE_RE = re.compile(r"<NEED_READ_FILE:\s*([^>|]{1,300})(?:\|\s*(\d+)-(\d+))?\s*>")

# NEED_LOGS: <NEED_LOGS: 50> or <NEED_LOGS: 50 | pattern>
_LOGS_RE = re.compile(r"<NEED_LOGS:\s*(\d+)(?:\s*\|\s*([^>]{1,200}))?\s*>")

_AUDIT_LOG = None  # lazy-initialised below


def _self_improve_audit_log() -> "pathlib.Path":  # noqa: F821  (imported inside)
    from pathlib import Path
    from .self_improve import ADN_ROOT  # type: ignore[attr-defined]
    p = ADN_ROOT / "data" / "daedalus" / "self_improve_audit.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


_TEXT_TOOL_CALL = re.compile(
    r"<tool_call\b[^>]*>(?:(?:(?!<tool_call\b)[\s\S]){0,4000}?</tool_call>|\s*\{[\s\S]{0,4000}\}\s*$)")
_NAMED_CALL: re.Pattern[str] | None = None
NOT_RUN_NOTE = "(The last step was a tool call written as text, so it did not run.)"


def strip_text_tool_calls(raw: str) -> str:
    """A tool call written as text never ran (live 2026-11-12: "<tool_call name=playtest>" and a bare line
    "browser({...})" reached the owner). Only real native tool names followed by ``({`` are removed, never inside a
    fenced code block, never across a blank line (review 2026-11-12: the first version could eat the owner's code)."""
    global _NAMED_CALL
    if _NAMED_CALL is None:
        try:
            from .native_tools import NATIVE_TOOL_SPECS
            names = sorted({str(t.get("name")) for t in NATIVE_TOOL_SPECS if t.get("name")}, key=len, reverse=True)
        except Exception:
            names = []
        alt = "|".join(re.escape(n) for n in names) or "(?!)"
        _NAMED_CALL = re.compile(r"^[ \t]*(?:" + alt + r")\(\{(?:(?!\n[ \t]*\n)[^`]){0,4000}?\}\)[ \t]*$",
                                 re.MULTILINE)
    source = raw or ""
    parts = re.split(r"(```[\s\S]*?```)", source)          # odd parts are fenced code: left untouched
    for i in range(0, len(parts), 2):
        text = _TEXT_TOOL_CALL.sub("", parts[i])
        text = _NAMED_CALL.sub("", text)
        parts[i] = re.sub(r"(?m)^[ \t]*<br\s*/?>[ \t]*$", "", text)
    out = re.sub(r"\n{3,}", "\n\n", "".join(parts)).strip()
    return out or (NOT_RUN_NOTE if source.strip() else "")



def _read_file_tool(raw_path: str, line_start: int | None = None, line_end: int | None = None) -> dict:
    """Execute NEED_READ_FILE: read a file safely and return its contents."""
    import time as _time
    from pathlib import Path
    from .self_improve import _resolve_safe, _is_hard_blocked, ADN_ROOT  # type: ignore

    path_str = raw_path.strip()
    target = _resolve_safe(path_str)
    if target is None or _is_hard_blocked(str(target)):
        return {"ok": False, "error": "path blocked or outside allowlist"}
    if not target.exists():
        return {"ok": False, "error": f"file not found: {path_str}"}
    try:
        content = target.read_text("utf-8", errors="replace")
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    lines = content.splitlines(keepends=True)
    if line_start is not None and line_end is not None:
        lines = lines[line_start - 1:line_end]
        content = "".join(lines)

    # Byte cap ~8k chars
    truncated = False
    truncated_at_line: int | None = None
    total_lines_before_range = len(content.splitlines())
    if len(content) > 8000:
        truncated_content = content[:8000]
        truncated_at_line = truncated_content.count("\n") + 1
        content = truncated_content
        truncated = True

    # Audit trail
    try:
        audit_entry = json.dumps({
            "ts": _time.time(), "event": "read_file",
            "path": path_str, "lines": f"{line_start}-{line_end}" if line_start else "all",
            "truncated": truncated,
        })
        with _self_improve_audit_log().open("a", encoding="utf-8") as f:
            f.write(audit_entry + "\n")
    except Exception:
        pass

    rel = str(target.relative_to(ADN_ROOT.parent)) if ADN_ROOT.parent in target.parents else path_str
    return {
        "ok": True,
        "path": rel,
        "content": content,
        "truncated": truncated,
        "truncated_at_line": truncated_at_line,
        "total_lines": total_lines_before_range,
    }


_LOGS_SECRET_RE = re.compile(
    r"(password|token|secret|key|auth|credential|apikey)",
    re.IGNORECASE
)


def _logs_tool(lines_n: int, grep_pattern: str | None = None) -> dict:
    """Execute NEED_LOGS: read recent ADN log lines with optional grep + secret redaction."""
    import glob as _glob
    from pathlib import Path

    lines_n = min(max(int(lines_n), 1), 500)
    log_dir = Path("/tmp/logs")
    if not log_dir.exists():
        return {"ok": False, "error": "/tmp/logs not found — no logs written yet"}

    # Gather all argos/nexus log files
    candidates = sorted(
        _glob.glob(str(log_dir / "*argos*")) +
        _glob.glob(str(log_dir / "*nexus*")) +
        _glob.glob(str(log_dir / "*daedalus*")),
        key=lambda p: Path(p).stat().st_mtime if Path(p).exists() else 0,
        reverse=True,
    )
    if not candidates:
        return {"ok": False, "error": "no matching log files in /tmp/logs"}

    collected: list[str] = []
    for fpath in candidates:
        try:
            with open(fpath, "r", errors="replace") as f:
                for line in f:
                    if _LOGS_SECRET_RE.search(line):
                        line = _LOGS_SECRET_RE.sub(r"[REDACTED]", line)
                    if grep_pattern:
                        if grep_pattern.lower() not in line.lower():
                            continue
                    collected.append(line.rstrip())
                    if len(collected) >= lines_n:
                        break
        except Exception:
            continue
        if len(collected) >= lines_n:
            break

    if not collected:
        return {"ok": True, "lines": [], "note": "no matching lines found"}
    return {"ok": True, "lines": collected, "count": len(collected), "source": candidates[0]}


def _format_web_results(query: str, ws: dict[str, Any]) -> str:
    if not ws.get("ok"):
        return f"[web_search query={query!r}] FAILED: {ws.get('error')}"
    lines = [f"[web_search query={query!r} — {ws.get('result_count', 0)} results]"]
    for i, r in enumerate(ws.get("results") or [], 1):
        title = (r.get("title") or "")[:160]
        url = r.get("url") or ""
        snip = (r.get("snippet") or r.get("content_preview") or "")[:300]
        lines.append(f"{i}. {title}\n   {url}\n   {snip}")
    return "\n".join(lines)


def _perm_gate(perm_class: str, tool: str) -> str | None:
    """Return denial message if permission missing, else None."""
    try:
        from .outbox import check_permission, format_permission_denied
        if check_permission(perm_class):
            return None
        return format_permission_denied(perm_class, tool=tool)
    except Exception as exc:
        # Fail closed: an unavailable permission check blocks the tool.
        log.warning("permission check unavailable for %s (%s) - blocked", tool, exc)
        from .native_tools import PERMISSION_CHECK_UNAVAILABLE
        return PERMISSION_CHECK_UNAVAILABLE.format(tool=tool)


def _format_github_results(query: str, gs: dict[str, Any]) -> str:
    if not gs.get("ok"):
        return f"[github_search query={query!r}] FAILED: {gs.get('error')}"
    lines = [f"[github_search query={query!r} — {gs.get('result_count', 0)} results]"]
    for i, r in enumerate(gs.get("results") or [], 1):
        name = r.get("full_name") or r.get("name") or ""
        url = r.get("url") or r.get("html_url") or ""
        stars = r.get("stars") or r.get("stargazers_count") or 0
        desc = (r.get("description") or "")[:240]
        install = r.get("install_command") or ""
        lines.append(f"{i}. {name} ★{stars}\n   {url}\n   {desc}\n   install: {install}")
    return "\n".join(lines)


def _format_github_results_extra(query: str, gs: dict[str, Any]) -> str:
    return _format_github_results(query, gs)


# ---------------------------------------------------------------------------
# NEED_PROPOSE block parser + debug loop
# ---------------------------------------------------------------------------

_PROPOSE_BLOCK_RE = re.compile(
    r"<NEED_PROPOSE_START>\s*\n"
    r"FILE:\s*(.+?)\n"
    r"REASON:\s*(.+?)\n"
    r"(?:RISK:\s*([1-5])[^\n]*\n)?"
    r"(?:TEST_START\n(.*?)TEST_END\n)?"
    r"CODE_START\n(.*?)CODE_END\s*\n?"
    r"</NEED_PROPOSE_START>",
    re.DOTALL,
)

_RISK_RATIONALE_RE = re.compile(r"RISK:\s*[1-5]\s*\|\s*(.+)")


def _extract_propose_block(raw: str) -> dict[str, Any] | None:
    """Parse a NEED_PROPOSE_START...NEED_PROPOSE_END block from LLM output.

    Block format (TEST and RISK are optional):
        <NEED_PROPOSE_START>
        FILE: argos_data_nexus/modules/daedalus/my_tool.py
        REASON: Explanation of what and why (in Bulgarian is fine)
        RISK: 2 | isolated component, no auth/DB touch
        TEST_START
        # optional test snippet — omit the whole section if no test
        from argos_data_nexus.modules.daedalus.my_tool import func
        assert func() == 42
        TEST_END
        CODE_START
        # full new file content goes here
        def func(): return 42
        CODE_END
        </NEED_PROPOSE_START>
    """
    if "<NEED_PROPOSE_START>" not in raw:
        return None
    m = _PROPOSE_BLOCK_RE.search(raw)
    if not m:
        return None
    risk_level = int(m.group(3)) if m.group(3) else None
    risk_rationale: str | None = None
    if risk_level is not None:
        rm = _RISK_RATIONALE_RE.search(raw[m.start():m.end()])
        if rm:
            risk_rationale = rm.group(1).strip()

    # [Phase 17A] Parse optional CONFIDENCE: and EVIDENCE: fields
    raw_block = raw[m.start():m.end()]
    _conf_m = re.search(r"CONFIDENCE:\s*([0-9.]+)", raw_block)
    _evid_m = re.search(
        r"EVIDENCE:\s*(.+?)(?=\n(?:RISK|TEST_START|CODE_START|</NEED_PROPOSE_START>))",
        raw_block, re.DOTALL,
    )
    _confidence: float | None = float(_conf_m.group(1)) if _conf_m else None
    _evidence_text = _evid_m.group(1).strip() if _evid_m else ""
    _evidence_count = len(re.findall(r"^\s*\d+[\.\)]\s+\S", _evidence_text, re.MULTILINE)) if _evidence_text else 0

    return {
        "file_path": m.group(1).strip(),
        "reason": m.group(2).strip(),
        "risk_level": risk_level,
        "risk_rationale": risk_rationale,
        "test_code": (m.group(4) or "").strip() or None,
        "code": m.group(5),
        "confidence": _confidence,
        "evidence_count": _evidence_count,
    }


def _strip_propose_block(raw: str) -> str:
    """Remove NEED_PROPOSE_START block from raw (so it's not shown to owner)."""
    return _PROPOSE_BLOCK_RE.sub("", raw).strip()


def _propose_max_retries() -> int:
    """Self-correction attempts for the propose loop (config via env, clamped)."""
    try:
        return max(1, int(os.environ.get("DAEDALUS_PROPOSE_MAX_RETRIES", "5")))
    except (ValueError, TypeError):
        return 5


async def _run_propose_loop(
    initial_raw: str,
    initial_block: dict[str, Any],
    user_content: str,
    persona: str,
    max_retries: int | None = None,
) -> tuple[str, str]:
    """Autonomous debug loop: sandbox → fix → propose.

    Returns (result_text, cleaned_raw).
    result_text will be injected into the final tool followup so Дедал
    can inform the owner.
    cleaned_raw is `initial_raw` with the NEED_PROPOSE_START block stripped.
    """
    from .sandbox import validate_patch, format_for_llm
    from .self_improve import propose_patch_validated

    if max_retries is None:
        max_retries = _propose_max_retries()

    block = initial_block
    raw = initial_raw
    attempt = 0
    sandbox_result: dict[str, Any] = {"ok": False, "errors": ["sandbox never executed"]}

    # [FIX #4] Fetch rejection warnings once — inject on sandbox failure retries
    _reject_warnings = ""
    try:
        from .rejection_lessons import retrieve_rejection_warnings
        _first_file = initial_block.get("file_path", "")
        _first_skill = None
        try:
            from .self_improve import _skill_for_path
            _first_skill = _skill_for_path(_first_file) or "edit_modules"
        except Exception:
            _first_skill = "edit_modules"
        _reject_warnings = retrieve_rejection_warnings([_first_file], _first_skill)
    except Exception as _rw_exc:
        log.debug("retrieve_rejection_warnings failed (non-fatal): %s", _rw_exc)

    # File edit permission — auto_approve bypasses; else create vs modify
    from .outbox import check_permission, format_permission_denied
    if not check_permission("auto_approve"):
        from .self_improve import _resolve_safe
        _fp = initial_block.get("file_path", "")
        _target = _resolve_safe(_fp) if _fp else None
        _is_new = _target is None or not _target.exists()
        _need = "create_files" if _is_new else "modify_files"
        if not check_permission(_need):
            return (
                format_permission_denied(_need, tool="NEED_PROPOSE_START"),
                _strip_propose_block(initial_raw),
            )

    # [Phase 17C] .daedalusignore — block proposals for protected files
    _gate_file_early = initial_block.get("file_path", "")
    if _gate_file_early:
        _ignore_pat = _daedalusignore.get_ignore_reason(_gate_file_early)
        if _ignore_pat:
            return (
                _daedalusignore.format_blocked_message(_gate_file_early, _ignore_pat),
                _strip_propose_block(initial_raw),
            )
    # [Phase 17C] Ensure .daedalusignore file exists on first run
    try:
        _daedalusignore.ensure_ignore_file()
    except Exception:
        pass

    # [Phase 17A] File Confidence Gate — soft warning if confidence is low/missing
    _gate_conf = initial_block.get("confidence")
    _gate_evid  = initial_block.get("evidence_count", 0)
    _gate_file  = initial_block.get("file_path", "?")
    if _gate_conf is not None and _gate_conf < 0.75:
        # Hard block: confidence declared but too low — don't even sandbox
        _gate_msg = (
            f"━━━ TOOL RESULTS — CONFIDENCE GATE BLOCKED ━━━\n"
            f"File       : {_gate_file}\n"
            f"Confidence : {_gate_conf:.2f} (min: 0.75)\n"
            f"Evidence   : {_gate_evid} piece(s) (min: 2)\n\n"
            f"Системата блокира edit при confidence < 0.75.\n"
            f"Направи ПЪРВО:\n"
            f"  1. search_code за ключовата функция/symbol в задачата\n"
            f"  2. NEED_ENDPOINTS или NEED_MODULES за потвърждение на файла\n"
            f"  3. Ако потвърдиш файла → добави CONFIDENCE: 0.XX и EVIDENCE: 1. ... 2. ...\n"
            f"Не емитирай нов NEED_PROPOSE_START докато не потвърдиш правилния файл."
        )
        return _gate_msg, _strip_propose_block(initial_raw)
    elif _gate_conf is None:
        # Soft: no confidence declared → log only, continue (Дедал учи постепенно)
        log.info(
            "[Phase 17A] propose block missing CONFIDENCE/EVIDENCE fields (file=%s) — "
            "add them per CODING PROTOCOL in identity.py", _gate_file
        )

    while attempt <= max_retries:
        attempt += 1
        file_path = block["file_path"]
        reason = block["reason"]
        code = block["code"]
        test_code = block.get("test_code")

        log.info("Propose loop attempt %d/%d — file=%s", attempt, max_retries + 1, file_path)

        # --- Sandbox validation (Layer 1-5) ---
        loop = asyncio.get_event_loop()
        sandbox_result = await loop.run_in_executor(
            None,
            lambda: validate_patch(
                file_path=file_path, new_content=code,
                test_code=test_code, timeout=12,
                changed_files=[file_path],
            ),
        )

        if sandbox_result.get("ok"):
            # Sandbox passed — create the validated proposal
            risk_level = block.get("risk_level")
            risk_rationale = block.get("risk_rationale")
            prop_result = await loop.run_in_executor(
                None,
                lambda: __import__('argos_data_nexus.modules.daedalus.self_improve',
                    fromlist=['propose_patch_validated']).propose_patch_validated(
                    file_path, code, reason, "daedalus", None, test_code, 12,
                    risk_level, risk_rationale, [file_path],
                ),
            )
            if prop_result.get("ok"):
                pid = prop_result["proposal_id"]
                sb = sandbox_result
                elapsed = sb.get("elapsed_ms", 0)
                layers_ok = sum(1 for l in sb.get("layers", []) if l.get("ok"))
                layers_total = len(sb.get("layers", []))
                risk_tag = f"\nRisk Level  : {risk_level} — {risk_rationale or ''}" if risk_level else ""
                # [FIX #2] Confidence info in result
                conf_score = prop_result.get("confidence_score")
                conf_tier  = prop_result.get("confidence_tier")
                conf_flags = prop_result.get("confidence_flags") or []
                conf_tag   = ""
                if conf_score is not None:
                    conf_tag = f"\nConfidence  : {conf_score}/100 [{conf_tier}]"
                    if conf_flags:
                        conf_tag += f"\nFlags       : {'; '.join(conf_flags[:3])}"
                # Static + tests summary
                static_sum = (sb.get("static") or {}).get("summary", "")
                tests_sum  = (sb.get("tests") or {}).get("summary", "")
                extra_lines = ""
                if static_sum and static_sum != "static: skip":
                    extra_lines += f"\nStatic      : {static_sum}"
                if tests_sum:
                    extra_lines += f"\nTests       : {tests_sum}"

                # [P1] Auto-apply if policy allows
                auto_applied = False
                auto_apply_reason = ""
                try:
                    from .auto_apply import should_auto_apply, apply_proposal_sync
                    # Build a slim proposal dict for policy check
                    _prop_for_check = {
                        "skill": prop_result.get("skill"),
                        "confidence_score": conf_score,
                        "confidence_tier": conf_tier,
                        "sandbox_result": sb,
                        "stats": prop_result.get("stats", {}),
                    }
                    _do_auto, _auto_why = should_auto_apply(_prop_for_check)
                    if _do_auto:
                        _apply_res = await loop.run_in_executor(
                            None, apply_proposal_sync, pid
                        )
                        auto_applied = _apply_res.get("ok", False)
                        auto_apply_reason = _auto_why
                        if not auto_applied:
                            auto_apply_reason = _apply_res.get("error", "unknown error")
                    else:
                        auto_apply_reason = _auto_why
                except Exception as _aa_exc:
                    log.debug("auto_apply check failed (non-fatal): %s", _aa_exc)

                if auto_applied:
                    result_text = (
                        f"━━━ TOOL RESULTS — PROPOSAL AUTO-APPLIED ✅ ━━━\n"
                        f"Proposal ID : {pid}\n"
                        f"File        : {file_path}\n"
                        f"Sandbox     : {layers_ok}/{layers_total} checks passed ({elapsed}ms)"
                        f"{extra_lines}{risk_tag}{conf_tag}\n"
                        f"Auto-apply  : ✅ Applied immediately (criteria: {auto_apply_reason})\n\n"
                        f"Промяната е приложена автоматично. Съобщи на Николай накратко какво си направил.\n"
                        f"Do NOT emit another NEED_PROPOSE_START."
                    )
                else:
                    result_text = (
                        f"━━━ TOOL RESULTS — CODE PROPOSAL CREATED ━━━\n"
                        f"Proposal ID : {pid}\n"
                        f"File        : {file_path}\n"
                        f"Sandbox     : {layers_ok}/{layers_total} checks passed ({elapsed}ms)"
                        f"{extra_lines}{risk_tag}{conf_tag}\n"
                        f"Auto-apply  : ⏳ Waiting ({auto_apply_reason})\n\n"
                        f"Предложението е записано и чака одобрение от Николай.\n"
                        f"Notify him: proposal {pid} is ready at /admin/daedalus/proposals\n"
                        f"Do NOT emit another NEED_PROPOSE_START. Just report the result clearly."
                        + _dod.format_dod_check()
                    )
            else:
                err = prop_result.get("error", "unknown")
                result_text = (
                    f"━━━ TOOL RESULTS — PROPOSAL CREATION FAILED ━━━\n"
                    f"Sandbox: PASSED\n"
                    f"Proposal error: {err}\n\n"
                    f"Tell Nikolai the sandbox passed but the proposal system rejected it: {err}"
                )
            return result_text, _strip_propose_block(raw)

        # --- Sandbox FAILED ---
        sandbox_feedback = format_for_llm(sandbox_result, file_path)
        log.warning("Propose loop attempt %d failed — %s", attempt, sandbox_result.get("errors"))
        # [Phase 17B] Error Brain — classify and append repair strategy
        try:
            _eb_cls = _error_brain.classify_from_sandbox(sandbox_result)
            if _eb_cls:
                sandbox_feedback += "\n\n" + _error_brain.format_for_prompt(_eb_cls)
        except Exception as _eb_exc:
            log.debug("error_brain classification skipped: %s", _eb_exc)

        if attempt > max_retries:
            break

        # [P2] Creative Retry — escalating strategy per attempt
        if attempt == 1:
            fix_prompt = (
                f"{sandbox_feedback}\n\n"
                f"{_reject_warnings}\n"
                f"Attempt {attempt}/{max_retries} FAILED. "
                f"Fix the specific error above and emit ONE corrected <NEED_PROPOSE_START> block. "
                f"Keep the same FILE and REASON. Focus only on the error — do not rewrite unrelated parts."
            )
        elif attempt == 2:
            fix_prompt = (
                f"{sandbox_feedback}\n\n"
                f"{_reject_warnings}\n"
                f"Attempt {attempt}/{max_retries} FAILED — same approach isn't working.\n\n"
                f"ALTERNATIVE APPROACH REQUIRED:\n"
                f"1. Identify what is structurally wrong with the current approach (1-2 sentences).\n"
                f"2. Propose a different implementation strategy that avoids the root cause.\n"
                f"3. Emit ONE new <NEED_PROPOSE_START> block using the alternative approach.\n"
                f"Do not repeat the same logic — try a genuinely different solution."
            )
        else:
            fix_prompt = (
                f"{sandbox_feedback}\n\n"
                f"{_reject_warnings}\n"
                f"Attempt {attempt}/{max_retries} — this approach has failed {attempt} times.\n\n"
                f"OPTIONS:\n"
                f"A) Simplify radically — split into a smaller, safer proposal that solves only PART of the problem.\n"
                f"   Emit a <NEED_PROPOSE_START> block for the minimal viable change.\n"
                f"B) If fundamentally blocked (missing dependency, needs external data, unclear requirement),\n"
                f"   emit: <NEED_BLOCKED: {block['file_path']} — describe root cause | "
                f"approaches tried so far | what you need to unblock>\n"
                f"   Then explain clearly to Николай what you need.\n"
                f"Choose the option that moves the task forward. Do not repeat the same failing code."
            )
        try:
            new_raw = await _call_claude(persona, [
                {"role": "user", "content": user_content},
                {"role": "assistant", "content": raw},
                {"role": "user", "content": fix_prompt},
            ])
        except Exception as exc:
            log.warning("Propose debug re-call failed: %s", exc)
            errors = sandbox_result.get("errors", [])
            errors_text = "\n".join(f"• {e[:200]}" for e in errors)
            result_text = (
                f"━━━ TOOL RESULTS — PROPOSE LOOP ABORTED (API ERROR) ━━━\n"
                f"File   : {block['file_path']}\n"
                f"Attempt: {attempt}/{max_retries}\n"
                f"API error: {exc}\n"
                f"Last sandbox errors:\n{errors_text or '  (none recorded)'}\n\n"
                f"Tell Nikolai that the Claude API call failed mid-loop with: {exc}. "
                f"Ask him if he wants to retry or review the sandbox output manually."
            )
            return result_text, _strip_propose_block(raw)

        new_block = _extract_propose_block(new_raw)
        if not new_block:
            log.warning("Propose loop: Claude did not emit a new block after fix attempt %d", attempt)
            errors = sandbox_result.get("errors", [])
            errors_text = "\n".join(f"• {e[:200]}" for e in errors)
            result_text = (
                f"━━━ TOOL RESULTS — PROPOSE LOOP STALLED ━━━\n"
                f"File   : {block['file_path']}\n"
                f"Attempt: {attempt}/{max_retries}\n"
                f"Sandbox errors:\n{errors_text or '  (none recorded)'}\n\n"
                f"Tell Nikolai that after the sandbox failed, Claude did not produce "
                f"a corrected code block. The sandbox errors are listed above. "
                f"Ask him if he wants to review the errors and try a different approach."
            )
            return result_text, _strip_propose_block(raw)
        block = new_block
        raw = new_raw

    # All retries exhausted — return failure report
    errors = sandbox_result.get("errors", [])  # type: ignore[possibly-undefined]
    errors_text = "\n".join(f"• {e[:200]}" for e in errors)
    result_text = (
        f"━━━ TOOL RESULTS — PROPOSAL FAILED AFTER {attempt} ATTEMPTS ━━━\n"
        f"File   : {block['file_path']}\n"
        f"Errors :\n{errors_text}\n\n"
        f"Tell Nikolai honestly that you tried {attempt} times but the sandbox "
        f"keeps failing with the errors above. Ask him if he wants to review "
        f"the error manually or try a different approach."
    )
    return result_text, _strip_propose_block(raw)


def _parse_json_response(raw: str) -> dict[str, Any]:
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except Exception:
        m = re.search(r"\{[\s\S]+\}", raw)
        if m:
            return json.loads(m.group(0))
        raise DaedalusError(f"LLM returned non-JSON: {raw[:200]}")


def _pg_norm_path(p: str) -> str:
    """Normalize a playground-relative path to canonical POSIX form for comparison."""
    p = (p or "").strip().replace("\\", "/")
    p = re.sub(r"/+", "/", p)
    while p.startswith("./"):
        p = p[2:]
    p = p.lstrip("/")
    return p


def _pg_manifest_forbidden_check(slug: str, path: str) -> tuple[bool, str]:
    """Validate a write/edit target against the project manifest's forbidden list.

    Fail-CLOSED: if a manifest exists for the slug but cannot be parsed/validated,
    the operation is BLOCKED (returns True) rather than silently allowed.
    Returns (blocked, reason). When no manifest exists, returns (False, "").
    """
    try:
        from .kv import db as _rdb_v
    except Exception:
        # No Replit DB available → no manifest enforcement possible.
        return (False, "")
    try:
        _man_str = _rdb_v.get(f"adn:daedalus_pg_manifest:{slug}")
    except Exception as exc:
        return (False, f"manifest DB read failed: {exc}")
    if not _man_str:
        return (False, "")
    # Manifest exists → from here on, parse failures are fail-CLOSED.
    try:
        _man = _man_str if isinstance(_man_str, dict) else json.loads(_man_str)
        _forb_raw = _man.get("forbidden") or _man.get("forbidden_files") or []
        _forb_list = [_pg_norm_path(f) for f in _forb_raw if isinstance(f, str) and f.strip()]
    except Exception as exc:
        return (True, f"договорът е повреден и не може да се валидира ({exc}) — write блокиран (fail-closed)")
    _cand = _pg_norm_path(path)
    for f in _forb_list:
        if _cand == f or _cand.endswith("/" + f) or _cand == f.split("/")[-1]:
            return (True, f"файлът е в forbidden списъка на договора ({f})")
    return (False, "")


# ---------------------------------------------------------------------------
# Main Daedalus agent
# ---------------------------------------------------------------------------

class Daedalus:
    """The single, persistent Daedalus instance."""

    _instance: "Daedalus | None" = None

    def __new__(cls) -> "Daedalus":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self) -> None:
        log.info("Daedalus: waking up")
        self.memory = DaedalusMemory()
        self.knowledge = DaedalusKnowledge(memory=self.memory)
        self.mem0 = Mem0Memory(base=self.memory)
        # Wire LLM-based fact extractor — runs as fire-and-forget background task
        # on every capture(). Adds 1 Claude call per turn (~$0.0002, async).
        try:
            self.mem0.attach_llm_extractor(_call_claude)
        except Exception as exc:
            log.warning("mem0: LLM extractor wiring failed (non-fatal): %s", exc)
        self._knowledge_loaded = False
        self.name = "Daedalus"
        # T07 — persona served via build_persona() hook (currently returns full
        # DAEDALUS_PERSONA; future split per intent/budget changes only the hook).
        self.persona = build_persona()
        # Hold strong refs to fire-and-forget tasks so the GC doesn't drop them
        # mid-flight (asyncio.create_task only weak-refs them otherwise).
        self._background_tasks: set[asyncio.Task] = set()

    async def ensure_knowledge_loaded(self) -> dict[str, Any]:
        """Run the knowledge loader once per process if not already done."""
        if self._knowledge_loaded:
            return {"already_loaded": True, "chunks": self.memory.global_col.count()}
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, self.knowledge.load_all, False)
        self._knowledge_loaded = True
        return result

    async def reload_knowledge(self, force: bool = True) -> dict[str, Any]:
        """Force re-scan of all skills/plans into memory."""
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, self.knowledge.load_all, force)
        self._knowledge_loaded = True
        return result

    # ------------------------------------------------------------------
    # Core: gather context + call LLM + capture
    # ------------------------------------------------------------------

    async def _build_context_and_call(
        self,
        user_message: str,
        session_id: str,
        retrieval_query: str | None = None,
        expect_json: bool = False,
        for_self: bool = False,
    ) -> tuple[str, dict[str, Any]]:
        """Shared pipeline: retrieve → format → call → capture.

        Returns (raw_llm_response, retrieval_summary).
        """
        import time as _time_mod
        started_at = _time_mod.time()
        await self.ensure_knowledge_loaded()

        # Phase 3 — steering queue: inject mid-run owner instructions
        _steering_block = pop_steering_messages(session_id or "")
        if _steering_block:
            user_message = _steering_block + "\n\n" + user_message

        # Semantic retrieval — Mem0 v2 hybrid (BM25 + dense + RRF + importance-scored
        # episodic).  Single fused call covers global/sessions/lessons/episodic/working.
        # We keep artifacts + session_recent + episodes separate (mem0 doesn't index them)
        # and rebuild the legacy lists for backward compatibility with the formatter.
        q = retrieval_query or user_message
        fused = self.mem0.fused_context(session_id, q, top_k=14)

        # Partition fused entries back into the buckets expected by _format_injected_context
        knowledge: list[dict[str, Any]] = []
        past_memory: list[dict[str, Any]] = []
        lessons: list[dict[str, Any]] = []
        episodic_fused: list[dict[str, Any]] = []
        for e in fused:
            row = {"id": e.id, "content": e.content, "metadata": e.metadata, "distance": None}
            if e.source == "global":
                knowledge.append(row)
            elif e.source == "lesson":
                lessons.append(row)
            elif e.source == "episodic":
                episodic_fused.append(row)
            else:  # semantic | working | hybrid
                past_memory.append(row)

        artifacts = self.memory.find_similar_artifacts(q, top_k=3)
        session_recent = self.memory.session_history(session_id, limit=6)
        # Phase 0 — pi-style context compaction when history is large
        _compaction_block: str | None = None
        try:
            session_recent, _compaction_block = maybe_compact_session_context(
                session_id, session_recent,
            )
        except Exception as _cmp_exc:
            log.debug("context compaction skipped: %s", _cmp_exc)
        episodes = self.memory.retrieve_episodes(q, top_k=2)
        # Promote episodic facts into lessons list for prompt visibility (treated as
        # owner-validated knowledge by the formatter)
        if episodic_fused:
            lessons = lessons + episodic_fused

        # Live mesh awareness (no HTTP — uses cached health_monitor data)
        mesh_block = ""
        try:
            mesh_block = format_mesh_for_prompt(get_mesh_snapshot())
        except Exception as exc:
            log.debug("mesh snapshot unavailable: %s", exc)

        injected = _format_injected_context(
            knowledge, past_memory, artifacts, session_recent,
            lessons=lessons, episodes=episodes, mesh_block=mesh_block,
        )
        try:
            from .preferences import format_block as _pref_block
            _prefs = _pref_block()
            if _prefs:
                injected = _prefs + ("\n\n" + injected if injected else "")
        except Exception as _mem_exc:
            log.debug("preference inject skipped: %s", _mem_exc)

        # Inject unread Agent Inbox messages (Replit Agent → Daedalus)
        inbox_block = ""
        try:
            from .planner import get_unread_inbox_for_prompt
            inbox_block = await get_unread_inbox_for_prompt()
        except Exception as exc:
            log.debug("inbox inject failed (non-fatal): %s", exc)

        # ─── NAVIGATOR (Phase 3): Haiku pre-flight briefing ─────────────────
        # Compresses the user's request + retrieved memory into a focused
        # briefing that prepends Sonnet's context. Never blocks the request —
        # any failure falls back to running Sonnet alone with full context.
        briefing_block = ""
        briefing_dict: dict[str, Any] | None = None
        try:
            # Live status: navigator фаза (преди да обвием Sonnet с reasoning)
            try:
                from .stream import set_live as _nav_set_live
                _nav_set_live(session_id, "navigator", 0,
                              "Haiku подготвя маршрут…")
            except Exception:
                pass
            from .navigator import navigate, preflight_feasibility
            briefing = await navigate(
                user_message,
                lessons=lessons,
                episodes=episodes,
                session_recent=session_recent,
            )
            # T10 — augment briefing.hint with feasibility red-flags BEFORE
            # serialising to prompt block, so Sonnet sees them inline with
            # the normal navigator hint.
            try:
                _preflight_hint = preflight_feasibility(user_message, briefing)
                if _preflight_hint:
                    _merged = (
                        (briefing.hint or "").strip() + " · " + _preflight_hint
                    ).strip(" ·")
                    # Briefing.hint contract: ≤ 300 chars (see navigator_vocab)
                    briefing.hint = _merged[:300]
                    log.info("preflight: session=%s flags=%s",
                             session_id[:10], _preflight_hint[:160])
            except Exception as _pf_exc:
                log.debug("preflight skipped: %s", _pf_exc)
            briefing_block = briefing.to_prompt_block()
            briefing_dict = briefing.to_dict()
        except Exception as exc:
            log.debug("navigator failed (non-fatal): %s", exc)

        # ── Conversation mode gate (talk / build / auto) ────────────────────
        # talk  → ALWAYS route to the chat model (pure conversation, no markers)
        # build → NEVER short-circuit to chat (run the full agent loop so Дедал acts)
        # auto  → defer to the Navigator's intent classification (original behaviour)
        try:
            _conv_mode = _mode.get_mode()
        except Exception:
            _conv_mode = "auto"
        from .playground_agent_routing import (
            is_playground_work_session,
            treat_as_conversational,
        )
        _force_chat = (_conv_mode == "talk")
        _block_chat = (
            _conv_mode == "build"
            or is_playground_work_session(session_id, user_message)
        )

        # ── Cost-aware routing (T11) — bypass Sonnet for chat/discussion ────
        # If Navigator classified this as chat (greetings, opinion questions,
        # discussions, meta-questions) AND no tools are needed, answer directly
        # with a cheap fast model (5-10× cheaper, 3× faster, perfectly capable
        # for conversational replies). No fast_path or budget=short requirement —
        # real discussions deserve a thoughtful answer, just not a full Sonnet loop.
        _nav_says_chat = bool(
            briefing_dict
            and briefing_dict.get("intent") == "chat"
            and not briefing_dict.get("tools")
        )
        from .llm_config import is_nvidia_free_only, persona_via_master_enabled
        _skip_cheap_chat = persona_via_master_enabled() or is_nvidia_free_only()
        if ((_force_chat or _nav_says_chat)
                and not _block_chat
                and not for_self
                and not expect_json
                and not _skip_cheap_chat):
            try:
                from . import daedalus_mesh as dm
                # Token budget: short greetings → 300, real discussions → 1200.
                # In explicit talk mode default to a fuller reply (real discussion).
                _chat_budget = (briefing_dict or {}).get("budget", "normal") if not _force_chat else "normal"
                _chat_max_tokens = 300 if _chat_budget == "short" else 1200
                _chat_hint = (briefing_dict or {}).get("hint", "")
                if _force_chat:
                    _chat_hint = (
                        (_chat_hint + " · " if _chat_hint else "")
                        + "Owner е в режим РАЗГОВОР — само обсъждай, без действия; "
                        "ако усетиш готова задача, можеш да предложиш минаване в режим Строеж."
                    )
                _chat_system = (
                    "You are Daedalus — a named, persistent engineering agent in the Iyveris ecosystem. "
                    "You speak Bulgarian when the owner writes in Bulgarian. "
                    "You have opinions, taste, and a direct style. You are NOT a sycophantic assistant. "
                    "This is a CONVERSATIONAL reply — no NEED_* markers, no JSON, no tool calls. "
                    "Just speak naturally. If discussing your own behavior or architecture, be honest and insightful. "
                    + (f"Navigator hint: {_chat_hint}" if _chat_hint else "")
                )
                _haiku_reply = ""
                if dm.use_mesh_router():
                    _mesh_res = await dm.complete(
                        system=_chat_system,
                        user=user_message,
                        intent="cheap" if _chat_budget == "short" else "chat",
                        max_tokens=_chat_max_tokens,
                        client_id=dm.client_id("chat", "fast-path"),
                        use_cache=True,
                    )
                    _haiku_reply = dm.extract_text(_mesh_res)
                    if _haiku_reply:
                        log.info(
                            "cost-aware route: session=%s intent=chat budget=%s mesh_router (%d chars, $%.6f)",
                            session_id, _chat_budget, len(_haiku_reply),
                            float(_mesh_res.get("cost_usd") or 0.0),
                        )
                from .harness import gateway as _gw
                _gw_fast = _gw.path_enabled("fastpath")
                if not _haiku_reply and _gw_fast:
                    # Gateway: owner-selected writer model, run budget enforced and metered, any SDK.
                    _gr = await _gw.call(
                        "persona", [{"role": "user", "content": user_message}],
                        system=_chat_system, max_tokens=_chat_max_tokens, client_id="daedalus/chat-fast-path",
                    )
                    if _gr.ok:
                        _haiku_reply = _gr.text
                        log.info("cost-aware route: session=%s intent=chat budget=%s served by gateway (%d chars)",
                                 session_id, _chat_budget, len(_haiku_reply))
                if not _haiku_reply and not _gw_fast:
                    from .llm_config import get_config
                    try:
                        _mcfg = get_config()
                        if _mcfg.get("api_key") and _mcfg.get("sdk") == "openai":
                            from openai import AsyncOpenAI
                            _hc = AsyncOpenAI(
                                api_key=_mcfg["api_key"],
                                base_url=_mcfg.get("base_url") or None,
                                max_retries=1,
                            )
                            _hr = await _hc.chat.completions.create(
                                model=_mcfg.get("model", ""),
                                max_tokens=_chat_max_tokens,
                                messages=[
                                    {"role": "system", "content": _chat_system},
                                    {"role": "user", "content": user_message},
                                ],
                            )
                            _haiku_reply = _hr.choices[0].message.content or ""
                            log.info(
                                "cost-aware route: session=%s intent=chat budget=%s served by master-config (%d chars)",
                                session_id, _chat_budget, len(_haiku_reply),
                            )
                    except Exception as _cfg_exc:
                        log.debug("chat fast-path master-config fallback failed: %s", _cfg_exc)
                if not _haiku_reply and _gw_fast:
                    _croute = _gw.cheap_route()
                    if _croute:
                        _gr = await _gw.call(
                            "summary", [{"role": "user", "content": user_message}],
                            system=_chat_system, max_tokens=_chat_max_tokens, route=_croute,
                            client_id="daedalus/chat-fast-path/cheap",
                        )
                        if _gr.ok:
                            _haiku_reply = _gr.text
                            log.info("cost-aware route: session=%s intent=chat budget=%s served by fast-model (%d chars)",
                                     session_id, _chat_budget, len(_haiku_reply))
                if not _haiku_reply and not _gw_fast:
                    from openai import AsyncOpenAI
                    from .cheap_llm import resolve_cheap_openai
                    _cheap = resolve_cheap_openai()
                    if _cheap:
                        _hc = AsyncOpenAI(
                            api_key=_cheap["api_key"],
                            base_url=_cheap["base_url"],
                            max_retries=1,
                        )
                        _hr = await _hc.chat.completions.create(
                            model=_cheap["model"],
                            max_tokens=_chat_max_tokens,
                            messages=[
                                {"role": "system", "content": _chat_system},
                                {"role": "user", "content": user_message},
                            ],
                        )
                        _haiku_reply = _hr.choices[0].message.content or ""
                        log.info("cost-aware route: session=%s intent=chat budget=%s served by fast-model (%d chars)",
                                 session_id, _chat_budget, len(_haiku_reply))
                if _haiku_reply:
                    # Honor a NEED_MODE marker even on the chat fast-path so Дедал
                    # can switch himself out of talk mode (and strip it from reply).
                    # Mode-only parser → no TASK_*/continuity side effects here.
                    try:
                        _haiku_reply = process_mode_marker_only(_haiku_reply)
                    except Exception as _mm_exc:
                        log.debug("chat fast-path marker processing skipped: %s", _mm_exc)
                    # Capture turn pair into memory so context survives
                    self.memory.capture_turn(session_id, "user", user_message)
                    self.memory.capture_turn(session_id, "assistant", _haiku_reply)
                    return _haiku_reply, {
                        "knowledge_used": 0, "past_memory_used": 0,
                        "similar_artifacts": 0, "session_turns_in_context": 0,
                        "lessons_used": 0, "episodes_used": 0,
                        "mesh_aware": False, "tool_calls": [],
                        "tool_iterations": 0,
                        "mem0": self.mem0.stats(),
                        "navigator": briefing_dict,
                        "routed_to": "mesh_router_chat" if dm.use_mesh_router() else "chat_fast_path",
                    }
            except Exception as _cr_exc:
                log.debug("cost-aware route failed (falling back to Sonnet): %s", _cr_exc)

        # Compose final user content — briefing goes FIRST so Sonnet sees the
        # focus before the raw memory dump.
        parts: list[str] = []
        if briefing_block:
            parts.append(briefing_block)
        # Progressive disclosure: catalog + at most one SKILL.md body.
        try:
            from .learned_skills import disclose as _disclose
            _intent_for_skills = (briefing_dict or {}).get("intent", "") if briefing_dict else ""
            _hint_for_skills = (briefing_dict or {}).get("hint", "") if briefing_dict else ""
            _skill_names = select_skills(user_message, _intent_for_skills, _hint_for_skills)
            try:
                from .game_engineer import extra_skill_ids, prefer_skill_id
                _skill_names = list(dict.fromkeys(
                    list(_skill_names) + extra_skill_ids(
                        user_message, _intent_for_skills, _hint_for_skills,
                    )
                ))
                _prefer = prefer_skill_id(user_message, intent=_intent_for_skills)
            except Exception:
                _prefer = None
            _pin_ids: list[str] = []
            try:
                from .session_context import current as _turn_now
                _pack = _turn_now()
                if _pack:
                    _pin_ids = list(_pack.skill_ids)
            except Exception:
                _pack = None
            _skill_block = _disclose(
                user_message, extra_ids=_skill_names, prefer_id=_prefer, pin_ids=_pin_ids,
            )
            if _skill_block:
                parts.append(_skill_block)
                log.info("skills disclosed: %s pins=%s (session=%s)", _skill_names, _pin_ids, session_id[:10])
            if _pack and _pack.mcp_ids:
                from .session_context import mcp_prompt_block as _mcp_block
                _mb = _mcp_block(_pack.mcp_ids)
                if _mb:
                    parts.append(_mb)
        except Exception as _sk_exc:
            log.debug("skill injection skipped: %s", _sk_exc)
        if injected:
            parts.append(injected)
        if inbox_block:
            parts.append(inbox_block)
        if _compaction_block:
            parts.append(_compaction_block)
        # Phase 3 — session branch lineage
        try:
            _branch_block = build_branch_context_block(session_id)
            if _branch_block:
                parts.append(_branch_block)
        except Exception as _br_exc:
            log.debug("branch context skipped: %s", _br_exc)

        # ── PROJECT AWARENESS (read-only snapshot injected at session start) ──
        # Gives Дедал situational context without prompting: playground state,
        # error patterns, pending tasks. Rule: awareness ≠ action.
        try:
            from .awareness import build_project_awareness as _bpa
            _awareness = await _bpa(session_id=session_id)
            if _awareness:
                parts.append(_awareness)
        except Exception as _aw_exc:
            log.debug("awareness injection skipped: %s", _aw_exc)

        if parts:
            user_content = "\n\n".join(parts) + f"\n\n━━━ NEW REQUEST FROM OWNER ━━━\n{user_message}"
        else:
            user_content = user_message

        # [Phase 17A] DoD injection — за coding задачи инжектираме checklist
        try:
            _dod_intent = (briefing_dict or {}).get("intent", "") if briefing_dict else ""
            if _dod.is_coding_task(user_message, _dod_intent):
                user_content = user_content + "\n\n" + _dod.generate_dod_block(user_message, _dod_intent)
        except Exception as _dod_exc:
            log.debug("DoD injection skipped: %s", _dod_exc)

        try:
            from .game_engineer import prompt_block as _eng_block
            _eng_intent = (briefing_dict or {}).get("intent", "") if briefing_dict else ""
            _eng = _eng_block(user_message, intent=_eng_intent)
            if _eng:
                user_content = user_content + "\n\n" + _eng
        except Exception as _eng_exc:
            log.debug("game engineer contract skipped: %s", _eng_exc)

        # [Phase 17B] Task state injection — ако има активна cross-session задача
        try:
            _ts_block = _task_state.get_context_block()
            if _ts_block:
                user_content = _ts_block + "\n\n" + user_content
        except Exception as _ts_exc:
            log.debug("task-state injection skipped: %s", _ts_exc)

        try:
            from .job_ledger import format_session_block as _job_block
            _jb = _job_block(session_id or "")
            if _jb:
                user_content = _jb + "\n\n" + user_content
        except Exception as _jb_exc:
            log.debug("job ledger injection skipped: %s", _jb_exc)

        # Inject continuity context into system prompt (per-request, always fresh)
        # [Playground Isolation] session_id filters tasks/digests to this project only
        _continuity_block = _continuity.build_continuity_context(session_id=session_id)
        _call_persona = (self.persona + "\n\n" + _continuity_block) if _continuity_block else self.persona
        # Inject the active conversation mode so Дедал knows whether to talk or act.
        try:
            _mode_block = _mode.build_mode_context(_conv_mode)
            if _mode_block:
                _call_persona = _call_persona + "\n\n" + _mode_block
        except Exception:
            pass

        # Trigger yesterday's digest generation in background (non-blocking)
        try:
            asyncio.create_task(asyncio.to_thread(_continuity.ensure_yesterday_digest))
        except Exception:
            pass

        # Live status — видимо в UI на всеки 1s докато агентът работи
        try:
            from .stream import (
                set_live as _set_live,
                clear_live as _clear_live,
                keepalive as _keepalive,
            )
            _set_live(session_id, "thinking", 0, "Анализирам задачата…")
        except Exception:
            def _set_live(*a, **kw): pass   # noqa: E731
            def _clear_live(*a, **kw): pass  # noqa: E731
            import contextlib as _cl_fallback
            @_cl_fallback.asynccontextmanager
            async def _keepalive(*a, **kw):  # type: ignore[no-redef]
                yield

        # ── Planning turn ────────────────────────────────────────────────────
        # Short focused call with a minimal system prompt asking ONLY for tools.
        # Models are far more compliant with a 10-line prompt than with the full
        # DAEDALUS_PERSONA.  If the planner returns a NEED_BATCH block, we use
        # it as `raw` and skip the expensive full initial call — saving ~1 RTT.
        # Non-critical: any failure falls back to the normal full call below.
        _PLANNING_SYSTEM = (
            "You are a tool-planning step inside the Daedalus agent.\n"
            "Your ONLY job: read the task and list the discovery tools needed.\n"
            "Respond with EXACTLY a <NEED_BATCH> block — no prose, no explanation.\n\n"
            "Available tools:\n"
            "  read: path/to/file.py|1-80        — read file lines\n"
            "  pg: SELECT …                       — PostgreSQL query\n"
            "  capability: keyword                — search endpoint registry\n"
            "  web: search query                  — web search\n"
            "  github: code query                 — GitHub code search\n"
            "  endpoints: filter                  — list ADN routes\n"
            "  modules: filter                    — list Python modules\n"
            "  db_schema: table1,table2           — describe DB tables\n"
            "  code_search: symbol                — semantic code search\n"
            "  logs: N                            — last N log lines\n"
            "  git_log: N                         — last N git commits\n\n"
            "Format EXACTLY (no text outside the block):\n"
            "<NEED_BATCH>\n"
            "read: argos_data_nexus/main.py|1-60\n"
            "pg: SELECT count(*) FROM mesh_call_log LIMIT 3\n"
            "capability: health check\n"
            "</NEED_BATCH>\n\n"
            "If no discovery tools are needed (conversational reply, opinion question, "
            "discussion, or already have context), respond with ONLY the word: DIRECT_ANSWER\n"
            "IMPORTANT: for questions about WHY/HOW something works conceptually, opinions, "
            "or discussions — always respond DIRECT_ANSWER (no tools needed)."
        )
        _planning_succeeded = False
        raw = ""
        # Skip planning for chat intents (Navigator already classified these as no-tool).
        # Playground / build must still run native tools even if Navigator said chat
        # (cheap Haiku down → fallback used to be intent=chat).
        _is_conversational = treat_as_conversational(
            user_message,
            nav_intent=(briefing_dict or {}).get("intent") or "",
            block_chat=_block_chat,
        )
        # Native tools are the discovery path. NEED_BATCH planning is leftover
        # text encoding — skip it when the typed protocol is on.
        if not _is_conversational and not native_tools_enabled():
            try:
                _set_live(session_id, "thinking", 0, "Планиране на инструменти…")
                planning_raw = await _call_claude_primary(
                    _PLANNING_SYSTEM,
                    [{"role": "user", "content": f"Task:\n{user_content[:4000]}"}],
                    use_cache_override=False,
                )
                from .batch_verify import extract_batch_block as _ebx
                _plan_batch = _ebx(planning_raw)
                if _plan_batch:
                    raw = planning_raw
                    _planning_succeeded = True
                    log.info(
                        "daedalus planning-turn: NEED_BATCH (%d lines) — skipping initial full call",
                        len(_plan_batch.splitlines()),
                    )
            except Exception as _pe:
                log.warning("daedalus planning-turn failed (non-fatal): %s", _pe)

        # Phase 2 — native tool API. Playground/game: BUILD with a tight tool
        # subset. Everything else: recon over the full catalog.
        _native_log: list[dict[str, Any]] = []
        _loop = None
        if (
            not _planning_succeeded
            and not _is_conversational
            and native_tools_enabled()
        ):
            try:
                from .game_engineer import looks_like_game as _looks_game
                from .native_tools import playground_build_specs
                from .tool_router import format_system, native_specs, route, run_consult
                from . import mentor_lead as _lead
                # The owner's own words (project chat wraps them in file context): route and plan on those, not on
                # the code in the prompt (found live 2026-11-11: keywords in file contents chose the playbook).
                _owner_words = _lead.take_request()   # once per turn: nested chats (subagents, planner) keep theirs
                if not _owner_words or _owner_words not in user_message:
                    _owner_words = user_message
                _pg_build = is_playground_work_session(session_id, user_message) or _looks_game(
                    _owner_words
                )
                _route = route(
                    _owner_words,
                    session_id=session_id or "",
                    intent=(briefing_dict or {}).get("intent") or "",
                )
                if _route.playbook.id.startswith("playground."):
                    _pg_build = True
                _consult_log: list[dict[str, Any]] = []
                _nt_specs = None
                _loop = None
                from . import project_chat_intent as _pci
                if not _pci.build_allowed():
                    # Project chat, owner asked a question: read and answer, no build loop, no write tools.
                    _nt_specs = _pci.read_only_specs()
                    _nt_sys = _pci.ANSWER_SYSTEM
                    max_native = 6
                elif _pg_build or _route.use_build_subset:
                    from .build_loop import BuildLoop
                    _consult_log, _consult_extra = run_consult(_route)
                    _nt_specs = native_specs(_route) or playground_build_specs(
                        _owner_words, session_id=session_id or "",
                    )
                    from .native_tools import _bound_project
                    _bound = _bound_project(session_id or "")
                    _nt_sys = format_system(
                        _route, extra=_consult_extra, user_message=_owner_words, offered=_nt_specs, slug=_bound,
                    )
                    _loop = BuildLoop(_route, _owner_words, seed_log=_consult_log)
                    if _bound and not _loop.slug:
                        _loop.slug = _bound          # project chat: the project exists; never steer to a new one
                    _loop.lead_plan = await _lead.kickoff(   # never raises: no plan just means no plan
                        _owner_words, slug=_loop.slug, kind=getattr(_route, "kind", "") or "",
                    )
                    max_native = 10
                    try:
                        from . import mentor as _mentor
                        if _mentor.enabled():
                            max_native = 16
                    except Exception:
                        pass
                    raw_nr = (os.environ.get("DAEDALUS_NATIVE_ROUNDS") or "").strip()
                    if raw_nr:
                        try:
                            max_native = max(1, min(64, int(raw_nr)))
                        except ValueError:
                            pass
                    else:
                        try:
                            from .task_effort import current as _eff_cur

                            _ed = _eff_cur()
                            _tier = str((_ed or {}).get("tier") or "").strip().lower()
                            if _tier == "whisper":
                                max_native = min(max_native, 8)
                            elif _tier == "epic":
                                max_native = max(max_native, 20)
                        except Exception:
                            pass
                else:
                    _nt_sys = (
                        "You are Daedalus tool-discovery. Use tools to gather context. "
                        "When done, summarize findings briefly."
                    )
                    max_native = 8
                _set_live(session_id, "native_tools", 0, "Native tools…")
                _native_in = user_content
                if _owner_words.strip() != (user_message or "").strip():
                    # Project chat: the request sits after the project's files; put it first so the cut below never
                    # drops it (a substring test is not enough: "continue" also appears in the code).
                    _native_in = f"OWNER REQUEST (this turn):\n{_owner_words}\n\n{user_content}"
                if _loop is not None and getattr(_loop, "lead_plan", None):
                    _native_in = _lead.plan_block(_loop.lead_plan) + "\n\n" + _native_in
                _native_cut = 12000
                if _loop is not None and getattr(_loop, "slug", ""):
                    from .project_bible import text as _bible_text
                    _bible = await asyncio.to_thread(_bible_text, _loop.slug, max_chars=2000)
                    if _bible:          # the game's bible: what earlier sessions decided and left open
                        _bible_block = "━━━ PROJECT BIBLE (earlier sessions) ━━━\n" + _bible + "\n\n"
                        _native_in = _bible_block + _native_in
                        _native_cut += len(_bible_block)       # the bible never pushes the request's context out
                _native_text, _native_log = await run_native_discovery_pass(
                    _nt_sys,
                    _native_in[:_native_cut],
                    _call_claude_with_tools,
                    memory=self.memory,
                    session_id=session_id or "",
                    read_file_fn=_read_file_tool,
                    max_rounds=max_native,
                    tools=_nt_specs,
                    loop=_loop,
                )
                if _consult_log:
                    _native_log = list(_consult_log) + list(_native_log or [])
                if _loop is not None:
                    _loop_row = _loop.log_row()
                    _native_log = list(_native_log or []) + [_loop_row]
                    _set_live(
                        session_id, "native_tools", 0,
                        "Build loop: complete" if _loop_row["complete"]
                        else f"Build loop: {_loop.stop_reason or 'partial'}",
                    )
                if _native_text:
                    user_content = user_content + "\n\n" + _native_text
                if _loop is not None:
                    user_content = user_content + "\n\n" + _loop.summary_block()
                    if _pg_build and any(
                        (t.get("tool") in {"write_file", "playground_new"} and t.get("ok"))
                        for t in _native_log
                    ):
                        from .tool_router import reply_language
                        user_content += (
                            "\n\n━━━ NATIVE BUILD ALREADY ACTED ━━━\n"
                            "You already created/wrote Playground files. "
                            "Reply with a short summary of the slice. "
                            "Do NOT emit NEED_* markers, JSON {\"tool\":...} dumps, or SQL.\n"
                            # Live 2026-11-12: the reply announced "I'll run playtest" and wrote the call as text
                            # after the work was done and accepted.
                            "Tools cannot run in this reply: do not announce next steps or write tool calls. "
                            "Say what was built, what was verified and what is still open.\n"
                            f"Write this reply in {reply_language(_owner_words)} (the owner's request).\n"
                        )
                        try:
                            from . import mentor as _mentor
                            wrap = _mentor.persona_wrapup(_loop)
                            if wrap:
                                user_content += "\n" + wrap + "\n"
                        except Exception:
                            pass
                    log.info(
                        "daedalus native-tools: %d tool calls — enriched context",
                        len(_native_log),
                    )
            except Exception as _nt_exc:
                log.warning("native tools preflight failed (non-fatal): %s", _nt_exc)
                _native_log = []
                _loop = None

        try:
            from .session_context import user_llm_payload as _user_payload
            _llm_user_content = _user_payload(user_content)
        except Exception:
            _llm_user_content = user_content
        if _loop is not None:
            _proof_vision = list(getattr(_loop, "proof_vision", None) or [])
            if _proof_vision:
                if isinstance(_llm_user_content, str):
                    _llm_user_content = [
                        {"type": "text", "text": _llm_user_content},
                        *_proof_vision,
                    ]
                elif isinstance(_llm_user_content, list):
                    _llm_user_content = list(_llm_user_content) + _proof_vision

        if not _planning_succeeded:
            # Normal path: full-persona initial call
            try:
                async with _keepalive(session_id, "reasoning", 0,
                                      "Дедал генерира първоначалния отговор…"):
                    raw = await _call_claude(_call_persona, [{"role": "user", "content": _llm_user_content}])
            except DaedalusError:
                raise
            except Exception as exc:
                raise DaedalusError(f"LLM call failed: {exc}") from exc

        # NEED_PROPOSE: autonomous code-write → sandbox → debug-loop → proposal.
        # Must be handled BEFORE the general tool re-roll because it may issue
        # multiple LLM sub-calls internally (debug loop).
        tool_calls_used: list[dict[str, Any]] = []
        if _native_log:
            tool_calls_used.extend(_native_log)
        propose_result_text = ""
        propose_block = _extract_propose_block(raw)
        if propose_block:
            propose_result_text, raw = await _run_propose_loop(
                initial_raw=raw,
                initial_block=propose_block,
                user_content=user_content,
                persona=self.persona,
                max_retries=_propose_max_retries(),
            )
            tool_calls_used.append({"tool": "propose_patch", "file": propose_block["file_path"]})

        # Tool re-roll: BOUNDED LOOP (max 20 iterations).
        # Supports chained markers: read_file → analyze → propose → reply.
        # Each iteration re-extracts markers from the latest raw response,
        # enabling "first read, then write" flows that a single re-roll cannot.
        # 20 iterations allow large multi-step tasks (recon + multi-batch inserts +
        # verification) without prematurely truncating action work.
        MAX_TOOL_ITERATIONS = 30
        _tool_loop_broke_with_fallback = False
        _promise_guard_count = 0  # no-silent-stall guard: max 3 re-rolls per session
        # The repeated-error breaker counts this turn only (review 2026-11-12: its tracker is per session and was
        # never reset, so failures from earlier turns cut later turns short once the breaker ran again).
        _breaker_key = f"{session_id or 'no-session'}:{uuid.uuid4().hex[:8]}"
        _calls_at_iter = 0
        for _tool_iter in range(MAX_TOOL_ITERATIONS):
            _calls_at_iter = len(tool_calls_used)       # the calls this iteration adds are tool_calls_used[this:]
            # Tool re-roll: if Claude emitted any NEED_* marker, execute tools and re-call.
            hit = scan_need_markers(raw)
            search_q = hit.search_q
            # R5: NEED_SEARCH is the same DDG search as NEED_WEB now (Sonar retired); one search, not two.
            web_q = hit.web_q or search_q
            fetch_url = hit.fetch_url
            screenshot_u = hit.screenshot_u
            gh_q = hit.gh_q
            mesh_marker = hit.mesh_marker
            skill_q = hit.skill_q
            doc_q = hit.doc_q
            pg_q = hit.pg_q
            backup_q = hit.backup_q
            gh_repo_q = hit.gh_repo_q
            gh_issue_q = hit.gh_issue_q
            pypi_q = hit.pypi_q
            npm_q = hit.npm_q
            docs_url = hit.docs_url
            dep_q = hit.dep_q
            git_log_q = hit.git_log_q
            git_diff_q = hit.git_diff_q
            sandbox_code = hit.sandbox_code
            batch_block = hit.batch_block
            verify_block = hit.verify_block
            edit_markers = hit.edit_markers
            _parsed_calls = hit.parsed_calls
            if _parsed_calls and _tool_iter == 0:
                log.debug(
                    "tool_parser: %d markers at positions %s",
                    len(_parsed_calls),
                    [c.name for c in _parsed_calls[:8]],
                )
            endpoints_q = hit.endpoints_q
            modules_q = hit.modules_q
            db_schema_q = hit.db_schema_q
            if db_schema_q:
                _tables = [t.strip() for t in re.split(r"[,\s]+", db_schema_q) if t.strip()]
                try:
                    mark_schema_read(session_id, _tables)
                except Exception:
                    pass
            code_search_q = hit.code_search_q
            capability_q = hit.capability_q
            ai_digest_q = hit.ai_digest_q
            skill_catalog_q = hit.skill_catalog_q
            lint_q = hit.lint_q
            tdd_m = hit.tdd_m
            pg_new_m = hit.pg_new_m
            pg_write_matches = hit.pg_write_matches
            pg_write_m = hit.pg_write_m
            pg_run_q = hit.pg_run_q
            pg_pub_m = hit.pg_pub_m
            pg_clone_m = hit.pg_clone_m
            pg_push_m = hit.pg_push_m
            pg_read_m = hit.pg_read_m
            pg_edit_matches = hit.pg_edit_matches
            pg_db_m = hit.pg_db_m
            pg_manifest_m = hit.pg_manifest_m
            drp_law_q = hit.drp_law_q
            drp_cases_q = hit.drp_cases_q
            drp_search_q = hit.drp_search_q
            inbox_msg_m = hit.inbox_msg_m
            project_plan_m = hit.project_plan_m
            data_prop_m = hit.data_prop_m
            _data_prop_truncated = hit.data_prop_truncated
            progress_m = hit.progress_m
            journal_m = hit.journal_m
            task_state_m = hit.task_state_m
            playbook_m = hit.playbook_m
            plan_lock_m = hit.plan_lock_m
            check_perm_m = hit.check_perm_m
            blocked_m = hit.blocked_m
            plan_step_m = hit.plan_step_m
            read_file_m = hit.read_file_m
            read_file_path = hit.read_file_path
            read_file_start = hit.read_file_start
            read_file_end = hit.read_file_end
            logs_m = hit.logs_m
            logs_n = hit.logs_n
            logs_grep = hit.logs_grep
            worker_calls = hit.worker_calls
            subagent_calls = hit.subagent_calls
            _bundle_start_title = hit.bundle_start_title
            _stage_blocks = hit.stage_blocks
            _bundle_commit = hit.bundle_commit
            from .tool_dumps import extract_tool_dumps, strip_tool_dumps
            _json_dumps = extract_tool_dumps(raw)
            has_markers = bool(propose_result_text) or hit.has_markers or bool(_json_dumps)
            if not has_markers:
                # ── Promise-without-marker guard (no-silent-stall) ──
                # До 3 re-roll-а с нарастваща принуда — моделите да emit-нат
                # NEED_* маркер вместо да обещаят в проза и да спрат.
                _clean = re.sub(r"```.*?```", " ", raw, flags=re.DOTALL)
                _clean = re.sub(r"`[^`\n]*`", " ", _clean)
                _clean = re.sub(r"(?m)^\s*>.*$", " ", _clean)
                _clean = re.sub(r'[\u201c\u201d\u00ab\u00bb]', " ", _clean)
                _promise_re = re.compile(
                    r"\b("
                    r"ще\s+(?:провер|потърс|намер|погледн|изчисл|анализир)|"
                    r"нека\s+(?:намер|провер|видим|потърс)|"
                    r"мога\s+да\s+(?:провер|намер|потърс|прочет)|"
                    r"трябва\s+да\s+(?:провер|намер|прочет|използ)|"
                    r"агентът\s+трябва|"
                    r"let\s+me\s+(?:check|find|search|look|read)|"
                    r"i(?:'ll| will)\s+(?:check|search|find|look|read)|"
                    r"should\s+(?:check|read|use|emit)|"
                    r"i\s+(?:need|must|have\s+to)\s+(?:check|read|find|look)|"
                    r"first\s+(?:i\s+(?:need|should|will|must)|let\s+me)|"
                    r"NEED_BATCH|NEED_VERIFY"
                    r")",
                    re.IGNORECASE,
                )
                if raw and _promise_guard_count < 3 and _promise_re.search(_clean):
                    _promise_guard_count += 1
                    log.info("promise-guard #%d: triggered session=%s iter=%d",
                             _promise_guard_count, (session_id or "")[:10], _tool_iter)
                    try:
                        _set_live(session_id, "reasoning", _tool_iter,
                                  f"Guard #{_promise_guard_count} — карам модела да emit-не маркер…")
                    except Exception:
                        pass

                    if _promise_guard_count == 1:
                        followup = (
                            "[GUARD] Обеща да провериш/намериш нещо, но НЕ емитира NEED_* маркер.\n\n"
                            "СЕГА emit ДИРЕКТНО маркер:\n"
                            "  • Един файл → <NEED_READ_FILE: path/to/file.py>\n"
                            "  • PostgreSQL → <NEED_PG: SELECT …>\n"
                            "  • Уеб → <NEED_WEB: заявка>\n"
                            "  • Множество неща наведнъж → NEED_BATCH блок:\n"
                            "<NEED_BATCH>\n"
                            "read: argos_data_nexus/main.py|1-80\n"
                            "pg: SELECT count(*) FROM endpoint_registry\n"
                            "capability: health check\n"
                            "</NEED_BATCH>\n\n"
                            "Никога не обещавай без да емитираш маркер."
                        )
                    elif _promise_guard_count == 2:
                        followup = (
                            "[GUARD #2] Отново без маркер. ПОСЛЕДНО ПРЕДУПРЕЖДЕНИЕ.\n\n"
                            "Отговори САМО с NEED_BATCH блок — без проза, без обяснение:\n"
                            "<NEED_BATCH>\n"
                            "tool: argument\n"
                            "</NEED_BATCH>\n\n"
                            "Ако нямаш нужда от инструменти, отговори САМО: DIRECT_ANSWER"
                        )
                    else:
                        followup = (
                            "[GUARD #3 — FINAL] Отговори с ЕДНО от двете:\n\n"
                            "Вариант A (ако трябват инструменти):\n"
                            "<NEED_BATCH>\n"
                            "read: argos_data_nexus/main.py|1-50\n"
                            "</NEED_BATCH>\n\n"
                            "Вариант B (ако можеш да отговориш директно):\n"
                            "DIRECT_ANSWER\n\n"
                            "НИЩО ДРУГО. БЕЗ ПРОЗА."
                        )

                    try:
                        async with _keepalive(
                            session_id, "reasoning", _tool_iter,
                            f"Re-roll #{_promise_guard_count} след promise-guard…",
                        ):
                            raw = await _call_claude(
                                _call_persona,
                                [
                                    {"role": "user",      "content": user_content},
                                    {"role": "assistant", "content": raw},
                                    {"role": "user",      "content": followup},
                                ],
                                use_cache_override=False,
                            )
                    except Exception as _g_exc:
                        log.debug("promise-guard re-roll #%d failed: %s",
                                  _promise_guard_count, _g_exc)
                        break
                    continue
                break  # no more tools requested; normal reply

            # Live status — кой инструмент се изпълнява в тази итерация
            try:
                _active_tool = (
                    "NEED_PG"            if pg_q else
                    "NEED_SEARCH"        if search_q else
                    "NEED_WEB"           if web_q else
                    "NEED_WEB_FETCH"     if fetch_url else
                    "NEED_WEB_SCREENSHOT" if screenshot_u else
                    "NEED_WORKER"        if worker_calls else
                    "NEED_DATA_PROPOSAL" if data_prop_m else
                    "NEED_PROPOSE_START" if propose_result_text else
                    "NEED_PROGRESS"      if progress_m else
                    "NEED_CHECK_PERM"    if check_perm_m else
                    "NEED_PROJECT_PLAN"  if project_plan_m else
                    "NEED_SKILL"         if skill_q else
                    "NEED_GITHUB"        if gh_q else
                    "NEED_MESH"          if mesh_marker else
                    "NEED_READ_FILE"     if endpoints_q or modules_q or db_schema_q else
                    "NEED_INBOX_MSG"     if inbox_msg_m else
                    "NEED_LINT"          if lint_q else
                    "NEED_TDD"           if tdd_m else
                    "thinking"
                )
                _set_live(session_id, _active_tool, _tool_iter,
                          f"итерация {_tool_iter + 1}/20")
            except Exception:
                pass

            tool_results_text_parts: list[str] = []
            vision_blocks: list[dict] = []   # base64 images for vision injection
            if _json_dumps:
                from .native_tools import execute_native_tool as _exec_dump
                dump_bits: list[str] = []
                _dump_skip = {"postgres_query", "mcp_call", "spawn_subagent"}
                for dump in _json_dumps:
                    if dump.name in _dump_skip:
                        continue
                    result_text = await _exec_dump(
                        dump.name, dump.args,
                        memory=self.memory, session_id=session_id or "",
                        read_file_fn=_read_file_tool,
                    )
                    from .tool_status import native_tool_result_ok

                    ok = native_tool_result_ok(result_text)
                    blocked = "BLOCKED" in (result_text or "")      # was an undefined name: NameError
                    tool_calls_used.append({
                        "tool": dump.name,
                        "ok": ok,
                        "blocked": blocked,
                        "source": "dump",
                    })
                    dump_bits.append(result_text)
                if dump_bits:
                    tool_results_text_parts.append(
                        "━━━ TOOL RESULTS — DUMPED CALLS ━━━\n" + "\n\n".join(dump_bits)
                    )
                raw = strip_tool_dumps(raw, _json_dumps)
            if propose_result_text:
                tool_results_text_parts.append(propose_result_text)
            if web_q:
                _denied = _perm_gate("web_search", "NEED_WEB")
                if _denied:
                    tool_results_text_parts.append(_denied)
                    tool_calls_used.append({"tool": "web_search", "ok": False, "denied": True})
                else:
                    ws = await web_search_tool(self.memory, web_q, limit=5, for_self=for_self)
                    tool_calls_used.append({"tool": "web_search", "query": web_q,
                                            "result_count": ws.get("result_count", 0),
                                            "discoveries_recorded": ws.get("discoveries_recorded", 0),
                                            "for_self": for_self})
                    tool_results_text_parts.append(_format_web_results(web_q, ws))
            if fetch_url:
                from .playground_agent_guards import should_block_web_fetch
                _pg_ctx = ""
                try:
                    from .kv import db as _rdb_wf
                    _pg_ctx = str(_rdb_wf.get(f"adn:daedalus_pg_context:{session_id}") or "")
                except Exception:
                    pass
                _wf_block = should_block_web_fetch(
                    session_id=session_id or "",
                    kind=_pg_ctx.split("|")[0] if "|" in _pg_ctx else "static",
                    project_text=_pg_ctx,
                )
                if _wf_block:
                    tool_results_text_parts.append(
                        f"━━━ TOOL RESULTS — WEB FETCH BLOCKED ━━━\n{_wf_block}"
                    )
                    tool_calls_used.append({"tool": "web_fetch", "url": fetch_url, "ok": False, "blocked": True})
                else:
                    _denied = _perm_gate("web_search", "NEED_FETCH")
                    if _denied:
                        tool_results_text_parts.append(_denied)
                        tool_calls_used.append({"tool": "web_fetch", "ok": False, "denied": True})
                    else:
                        fr = await web_fetch_tool(fetch_url)
                        trunc_note = (f"\n[...truncated — {fr.get('chars_total',0)} total chars]"
                                      if fr.get("truncated") else "")
                        if fr.get("ok"):
                            tool_results_text_parts.append(
                                f"━━━ TOOL RESULTS — WEB FETCH [{fetch_url}] ━━━\n"
                                f"{fr['text']}{trunc_note}"
                            )
                        else:
                            tool_results_text_parts.append(
                                f"[web_fetch {fetch_url!r}] FAILED: {fr.get('error')}"
                            )
                        tool_calls_used.append({"tool": "web_fetch", "url": fetch_url,
                                                "ok": fr.get("ok", False)})
            if screenshot_u:
                _denied = _perm_gate("web_search", "NEED_SCREENSHOT")
                if _denied:
                    tool_results_text_parts.append(_denied)
                    tool_calls_used.append({"tool": "web_screenshot", "ok": False, "denied": True})
                else:
                    sr = await web_screenshot_tool(screenshot_u)
                    if sr.get("ok"):
                        vision_blocks.append({
                            "type": "image",
                            "source": {"type": "base64",
                                       "media_type": sr["media_type"],
                                       "data": sr["image_b64"]},
                        })
                        tool_results_text_parts.append(
                            f"━━━ TOOL RESULTS — SCREENSHOT [{screenshot_u}] ━━━\n"
                            f"Страницата е заснета ({sr.get('size_kb', 0)} KB). "
                            f"Виж прикачения визуален блок за дизайн вдъхновение."
                        )
                    else:
                        tool_results_text_parts.append(
                            f"[web_screenshot {screenshot_u!r}] FAILED: {sr.get('error')}"
                        )
                    tool_calls_used.append({"tool": "web_screenshot", "url": screenshot_u,
                                            "ok": sr.get("ok", False)})
            if gh_q:
                _denied = _perm_gate("github_access", "NEED_GITHUB")
                if _denied:
                    tool_results_text_parts.append(_denied)
                    tool_calls_used.append({"tool": "github_search", "ok": False, "denied": True})
                else:
                    gs = await github_search_tool(self.memory, gh_q, limit=5, for_self=for_self)
                    tool_calls_used.append({"tool": "github_search", "query": gh_q,
                                            "result_count": gs.get("result_count", 0),
                                            "discoveries_recorded": gs.get("discoveries_recorded", 0),
                                            "for_self": for_self})
                    tool_results_text_parts.append(_format_github_results(gh_q, gs))
            if mesh_marker:
                # R5 (retired 2026-11-09): NEED_MESH called services of the old Iyveris mesh, which standalone Daedalus
                # does not have. Answer honestly instead of calling anything.
                tool_calls_used.append({"tool": "mesh_call", "ok": False, "error": "retired"})
                tool_results_text_parts.append(
                    "━━━ TOOL RESULTS — MESH CALL ━━━\n"
                    "[NEED_MESH] unavailable: the Iyveris mesh is retired in standalone Daedalus. Nothing was called."
                )
            if skill_q:
                try:
                    from .skill_acquisition import analyze_github_repo, format_skill_analysis_for_prompt, log_skill_interest
                    loop = asyncio.get_event_loop()
                    analysis = await loop.run_in_executor(None, analyze_github_repo, skill_q)
                    log_skill_interest(analysis.get("slug", skill_q), analysis)
                    tool_results_text_parts.append(
                        "━━━ TOOL RESULTS — SKILL ANALYSIS ━━━\n"
                        + format_skill_analysis_for_prompt(analysis)
                    )
                    tool_calls_used.append({"tool": "skill_analyze", "query": skill_q, "ok": analysis.get("ok", False)})
                except Exception as exc:
                    log.warning("skill_analyze failed: %s", exc)
                    tool_results_text_parts.append(f"━━━ TOOL RESULTS — SKILL ANALYSIS ━━━\nFAILED: {exc}")
                    tool_calls_used.append({"tool": "skill_analyze", "query": skill_q, "ok": False})
            if doc_q:
                try:
                    from .document_generator import generate_document
                    loop = asyncio.get_event_loop()
                    doc_result = await loop.run_in_executor(None, generate_document, doc_q)
                    if doc_result.get("ok"):
                        result_text = (
                            f"━━━ TOOL RESULTS — DOCUMENT GENERATED ━━━\n"
                            f"Format  : {doc_result['format'].upper()}\n"
                            f"Title   : {doc_result['title']}\n"
                            f"File    : {doc_result['filename']}\n"
                            f"Size    : {doc_result['size_bytes']:,} bytes\n"
                            f"Download: {doc_result['download_url']}\n\n"
                            f"Tell Nikolai the document is ready with a clear download link. "
                            f"Use the exact download_url above as a markdown link."
                        )
                    else:
                        result_text = (
                            f"━━━ TOOL RESULTS — DOCUMENT GENERATION FAILED ━━━\n"
                            f"Error: {doc_result.get('error', 'unknown')}"
                        )
                    tool_results_text_parts.append(result_text)
                    tool_calls_used.append({"tool": "generate_document", "format": doc_q.split("|")[0].strip(), "ok": doc_result.get("ok", False)})
                except Exception as exc:
                    log.warning("generate_document failed: %s", exc)
                    tool_results_text_parts.append(f"━━━ TOOL RESULTS — DOCUMENT GENERATION FAILED ━━━\nError: {exc}")
                    tool_calls_used.append({"tool": "generate_document", "ok": False})

            if pg_q and is_playground_work_session(session_id, user_message):
                tool_results_text_parts.append(
                    "━━━ TOOL RESULTS — PostgreSQL SKIPPED ━━━\n"
                    "Playground session — write the game with playground_new / write_file. No SQL."
                )
                tool_calls_used.append({
                    "tool": "postgres_query", "ok": True, "blocked": True, "reason": "playground",
                })
            elif pg_q:
                try:
                    pg_res = await postgres_query_tool(pg_q, session_id=session_id)
                    if pg_res.get("ok"):
                        rows_text = "\n".join(str(r) for r in (pg_res.get("rows") or [])[:20])
                        result_text = (
                            f"━━━ TOOL RESULTS — PostgreSQL QUERY ━━━\n"
                            f"Query: {pg_q}\n"
                            f"Rows returned: {pg_res['row_count']}\n\n"
                            f"{rows_text}"
                        )
                    else:
                        result_text = f"━━━ TOOL RESULTS — PostgreSQL QUERY FAILED ━━━\nError: {pg_res.get('error')}"
                    tool_results_text_parts.append(result_text)
                    tool_calls_used.append({"tool": "postgres_query", "ok": pg_res.get("ok", False), "rows": pg_res.get("row_count", 0), "query": (pg_q or "")[:160]})
                except Exception as exc:
                    log.warning("postgres_query failed: %s", exc)
                    tool_results_text_parts.append(f"━━━ TOOL RESULTS — PostgreSQL QUERY FAILED ━━━\nError: {exc}")
                    tool_calls_used.append({"tool": "postgres_query", "ok": False})

            if backup_q and "chromadb" in backup_q.lower():
                try:
                    bk_res = await backup_chromadb_tool(trigger="daedalus")
                    if bk_res.get("ok"):
                        result_text = (
                            f"━━━ TOOL RESULTS — CHROMADB BACKUP ━━━\n"
                            f"Path    : {bk_res['object_path']}\n"
                            f"Size    : {bk_res['size_bytes']:,} bytes\n"
                            f"Files   : {bk_res.get('file_count', '?')}\n"
                            f"Trigger : {bk_res['trigger']}"
                        )
                    else:
                        result_text = f"━━━ TOOL RESULTS — CHROMADB BACKUP FAILED ━━━\nError: {bk_res.get('error')}"
                    tool_results_text_parts.append(result_text)
                    tool_calls_used.append({"tool": "backup_chromadb", "ok": bk_res.get("ok", False)})
                except Exception as exc:
                    log.warning("backup_chromadb failed: %s", exc)
                    tool_results_text_parts.append(f"━━━ TOOL RESULTS — CHROMADB BACKUP FAILED ━━━\nError: {exc}")
                    tool_calls_used.append({"tool": "backup_chromadb", "ok": False})

            if gh_repo_q:
                _denied = _perm_gate("github_access", "NEED_GITHUB_REPO")
                if _denied:
                    tool_results_text_parts.append(_denied)
                    tool_calls_used.append({"tool": "github_repo_search", "ok": False, "denied": True})
                else:
                    try:
                        repo_res = await github_search_repos_tool(gh_repo_q, limit=5)
                        if repo_res.get("ok"):
                            repos_text = "\n".join(
                                f"  • {r['full_name']} ({'★'+str(r['stars'])+' ' if r.get('stars') else ''}{r.get('language','')}) — {r.get('description','')[:100]}\n    {r['url']}"
                                for r in (repo_res.get("repos") or [])
                            )
                            result_text = (
                                f"━━━ TOOL RESULTS — GITHUB REPO SEARCH ━━━\n"
                                f"Query: {gh_repo_q}\n\n{repos_text or 'No results.'}"
                            )
                        else:
                            result_text = f"━━━ TOOL RESULTS — GITHUB REPO SEARCH FAILED ━━━\nError: {repo_res.get('error')}"
                        tool_results_text_parts.append(result_text)
                        tool_calls_used.append({"tool": "github_repo_search", "ok": repo_res.get("ok", False)})
                    except Exception as exc:
                        log.warning("github_repo_search failed: %s", exc)
                        tool_results_text_parts.append(f"━━━ TOOL RESULTS — GITHUB REPO SEARCH FAILED ━━━\nError: {exc}")
                        tool_calls_used.append({"tool": "github_repo_search", "ok": False})

            if gh_issue_q:
                try:
                    parts_issue = [p.strip() for p in gh_issue_q.split("|", 2)]
                    if len(parts_issue) >= 2:
                        repo_full = parts_issue[0]
                        issue_title = parts_issue[1]
                        issue_body = parts_issue[2] if len(parts_issue) > 2 else ""
                        owner_r, repo_r = (repo_full.split("/", 1) + [""])[:2]
                        issue_res = await github_create_issue_tool(owner_r, repo_r, issue_title, body=issue_body)
                        if issue_res.get("ok"):
                            result_text = (
                                f"━━━ TOOL RESULTS — GITHUB ISSUE CREATED ━━━\n"
                                f"Issue #{issue_res['issue_number']}: {issue_res['title']}\n"
                                f"URL: {issue_res['url']}"
                            )
                        else:
                            result_text = f"━━━ TOOL RESULTS — GITHUB ISSUE FAILED ━━━\nError: {issue_res.get('error')}"
                    else:
                        result_text = "━━━ TOOL RESULTS — GITHUB ISSUE FAILED ━━━\nInvalid format. Use: owner/repo | Title | Body"
                    tool_results_text_parts.append(result_text)
                    tool_calls_used.append({"tool": "github_create_issue", "ok": "ok" in gh_issue_q})
                except Exception as exc:
                    log.warning("github_create_issue failed: %s", exc)
                    tool_results_text_parts.append(f"━━━ TOOL RESULTS — GITHUB ISSUE FAILED ━━━\nError: {exc}")
                    tool_calls_used.append({"tool": "github_create_issue", "ok": False})

            # --- PyPI search ---
            if pypi_q:
                try:
                    pr = await pypi_search_tool(pypi_q)
                    if pr.get("ok"):
                        lines = [f"━━━ TOOL RESULTS — PyPI SEARCH ━━━\nQuery: {pypi_q}\n"]
                        for p in pr.get("results", []):
                            lines.append(f"  • {p['name']} {p['version']} — {p.get('summary','')[:100]}\n    {p['install']}")
                        tool_results_text_parts.append("\n".join(lines))
                    else:
                        tool_results_text_parts.append(f"━━━ TOOL RESULTS — PyPI SEARCH FAILED ━━━\n{pr.get('error')}")
                    tool_calls_used.append({"tool": "pypi_search", "query": pypi_q, "ok": pr.get("ok", False)})
                except Exception as exc:
                    tool_results_text_parts.append(f"━━━ TOOL RESULTS — PyPI SEARCH FAILED ━━━\n{exc}")
                    tool_calls_used.append({"tool": "pypi_search", "ok": False})

            # --- npm search ---
            if npm_q:
                try:
                    nr = await npm_search_tool(npm_q)
                    if nr.get("ok"):
                        lines = [f"━━━ TOOL RESULTS — npm SEARCH ━━━\nQuery: {npm_q}\n"]
                        for p in nr.get("results", []):
                            lines.append(f"  • {p['name']} {p['version']} — {p.get('description','')[:100]}\n    {p['install']}")
                        tool_results_text_parts.append("\n".join(lines))
                    else:
                        tool_results_text_parts.append(f"━━━ TOOL RESULTS — npm SEARCH FAILED ━━━\n{nr.get('error')}")
                    tool_calls_used.append({"tool": "npm_search", "query": npm_q, "ok": nr.get("ok", False)})
                except Exception as exc:
                    tool_results_text_parts.append(f"━━━ TOOL RESULTS — npm SEARCH FAILED ━━━\n{exc}")
                    tool_calls_used.append({"tool": "npm_search", "ok": False})

            # --- Docs fetch ---
            if docs_url:
                try:
                    dr = await docs_fetch_tool(docs_url)
                    if dr.get("ok"):
                        tool_results_text_parts.append(
                            f"━━━ TOOL RESULTS — DOCS FETCH ━━━\nURL: {docs_url}\nChars: {dr['chars']}\n\n{dr['content']}"
                        )
                    else:
                        tool_results_text_parts.append(f"━━━ TOOL RESULTS — DOCS FETCH FAILED ━━━\n{dr.get('error')}")
                    tool_calls_used.append({"tool": "docs_fetch", "url": docs_url, "ok": dr.get("ok", False)})
                except Exception as exc:
                    tool_results_text_parts.append(f"━━━ TOOL RESULTS — DOCS FETCH FAILED ━━━\n{exc}")
                    tool_calls_used.append({"tool": "docs_fetch", "ok": False})

            # --- Dependency analyzer ---
            if dep_q:
                try:
                    parts_dep = [p.strip() for p in dep_q.split("|", 1)]
                    pkg_name = parts_dep[0]
                    eco = parts_dep[1] if len(parts_dep) > 1 else "auto"
                    dr = await dependency_analyze_tool(pkg_name, eco)
                    if dr.get("ok"):
                        vuln_lines = ""
                        if dr.get("vulnerabilities"):
                            vuln_lines = "\nVULNERABILITIES:\n" + "\n".join(
                                f"  [{v['severity']}] {v['id']}: {v['summary'][:120]}"
                                + (f" (fixed in {v['fixed_in']})" if v.get('fixed_in') else "")
                                for v in dr["vulnerabilities"]
                            )
                        else:
                            vuln_lines = "\nNo known CVEs found."
                        tool_results_text_parts.append(
                            f"━━━ TOOL RESULTS — DEPENDENCY ANALYSIS ━━━\n"
                            f"Package  : {dr['package']} ({dr['ecosystem']})\n"
                            f"Latest   : {dr['latest_version']} (released {dr.get('released','?')})\n"
                            f"Summary  : {dr.get('summary','')[:200]}\n"
                            f"Home     : {dr.get('home_url','')}\n"
                            f"CVE count: {dr['vuln_count']}"
                            f"{vuln_lines}"
                        )
                    else:
                        tool_results_text_parts.append(f"━━━ TOOL RESULTS — DEPENDENCY ANALYSIS FAILED ━━━\n{dr.get('error')}")
                    tool_calls_used.append({"tool": "dependency_analyze", "package": pkg_name, "ok": dr.get("ok", False)})
                except Exception as exc:
                    tool_results_text_parts.append(f"━━━ TOOL RESULTS — DEPENDENCY ANALYSIS FAILED ━━━\n{exc}")
                    tool_calls_used.append({"tool": "dependency_analyze", "ok": False})

            # --- Git log ---
            if git_log_q:
                try:
                    parts_gl = [p.strip() for p in git_log_q.split("|", 1)]
                    gl_n = int(parts_gl[0]) if parts_gl[0].isdigit() else 15
                    gl_path = parts_gl[1] if len(parts_gl) > 1 else None
                    glr = await git_log_tool(gl_n, gl_path)
                    if glr.get("ok"):
                        lines = [f"━━━ TOOL RESULTS — GIT LOG ━━━\nLast {glr['count']} commits:\n"]
                        for c in glr.get("commits", []):
                            lines.append(f"  {c['date']} [{c['hash']}] {c['author']}: {c['subject']}")
                        tool_results_text_parts.append("\n".join(lines))
                    else:
                        tool_results_text_parts.append(f"━━━ TOOL RESULTS — GIT LOG FAILED ━━━\n{glr.get('error')}")
                    tool_calls_used.append({"tool": "git_log", "ok": glr.get("ok", False)})
                except Exception as exc:
                    tool_results_text_parts.append(f"━━━ TOOL RESULTS — GIT LOG FAILED ━━━\n{exc}")
                    tool_calls_used.append({"tool": "git_log", "ok": False})

            # --- Git diff ---
            if git_diff_q:
                try:
                    parts_gd = [p.strip() for p in git_diff_q.split("|", 1)]
                    gd_path = parts_gd[0] if parts_gd[0] else None
                    gd_ref = parts_gd[1] if len(parts_gd) > 1 else "HEAD"
                    gdr = await git_diff_tool(gd_path, gd_ref)
                    if gdr.get("ok"):
                        tool_results_text_parts.append(
                            f"━━━ TOOL RESULTS — GIT DIFF ━━━\nPath: {gdr['path']} vs {gdr['ref']}\n\n{gdr['diff']}"
                        )
                    else:
                        tool_results_text_parts.append(f"━━━ TOOL RESULTS — GIT DIFF FAILED ━━━\n{gdr.get('error')}")
                    tool_calls_used.append({"tool": "git_diff", "ok": gdr.get("ok", False)})
                except Exception as exc:
                    tool_results_text_parts.append(f"━━━ TOOL RESULTS — GIT DIFF FAILED ━━━\n{exc}")
                    tool_calls_used.append({"tool": "git_diff", "ok": False})

            # --- Code sandbox ---
            if sandbox_code:
                try:
                    sbr = await code_sandbox_tool(sandbox_code)
                    if sbr.get("ok"):
                        stdout = sbr.get("stdout", "") or "(no output)"
                        stderr = sbr.get("stderr", "")
                        result_text = (
                            f"━━━ TOOL RESULTS — CODE SANDBOX ━━━\n"
                            f"Return code: {sbr.get('returncode', '?')}\n"
                            f"STDOUT:\n{stdout}"
                        )
                        if stderr:
                            result_text += f"\nSTDERR:\n{stderr}"
                    else:
                        result_text = f"━━━ TOOL RESULTS — CODE SANDBOX FAILED ━━━\n{sbr.get('error')}"
                        if sbr.get("timed_out"):
                            result_text += "\n(Execution timed out)"
                    tool_results_text_parts.append(result_text)
                    tool_calls_used.append({"tool": "code_sandbox", "ok": sbr.get("ok", False)})
                except Exception as exc:
                    tool_results_text_parts.append(f"━━━ TOOL RESULTS — CODE SANDBOX FAILED ━━━\n{exc}")
                    tool_calls_used.append({"tool": "code_sandbox", "ok": False})

            # --- NEED_READ_FILE ---
            if read_file_path:
                try:
                    loop = asyncio.get_event_loop()
                    rfr = await loop.run_in_executor(
                        None, _read_file_tool, read_file_path, read_file_start, read_file_end
                    )
                    if rfr.get("ok"):
                        try:
                            track_files_from_text(session_id or "", rfr.get("path", read_file_path))
                        except Exception:
                            pass
                        if rfr.get("truncated"):
                            trunc_at = rfr.get("truncated_at_line")
                            total = rfr.get("total_lines", "?")
                            trunc_note = (
                                f" [TRUNCATED at line ~{trunc_at} of {total} "
                                f"— use <NEED_READ_FILE: {rfr['path']} | {trunc_at}-{min((trunc_at or 0)+100, total if isinstance(total,int) else 9999)}> "
                                f"to read more]"
                            )
                        else:
                            trunc_note = ""
                        tool_results_text_parts.append(
                            f"━━━ TOOL RESULTS — READ FILE: {rfr['path']}{trunc_note} ━━━\n"
                            f"{rfr['content']}"
                        )
                    else:
                        tool_results_text_parts.append(
                            f"━━━ TOOL RESULTS — READ FILE FAILED ━━━\n{rfr.get('error')}"
                        )
                    tool_calls_used.append({"tool": "read_file", "path": read_file_path, "ok": rfr.get("ok", False)})
                except Exception as exc:
                    tool_results_text_parts.append(f"━━━ TOOL RESULTS — READ FILE FAILED ━━━\n{exc}")
                    tool_calls_used.append({"tool": "read_file", "ok": False})

            # --- NEED_LOGS ---
            if logs_n:
                try:
                    loop = asyncio.get_event_loop()
                    lgr = await loop.run_in_executor(
                        None, _logs_tool, logs_n, logs_grep
                    )
                    if lgr.get("ok"):
                        lines_text = "\n".join(lgr.get("lines", []))
                        grep_note = f" | grep={logs_grep!r}" if logs_grep else ""
                        tool_results_text_parts.append(
                            f"━━━ TOOL RESULTS — LOGS (last {logs_n}{grep_note}) ━━━\n"
                            f"Source: {lgr.get('source', '?')}\n"
                            f"{lines_text}"
                        )
                    else:
                        tool_results_text_parts.append(
                            f"━━━ TOOL RESULTS — LOGS FAILED ━━━\n{lgr.get('error')}"
                        )
                    tool_calls_used.append({"tool": "logs", "n": logs_n, "ok": lgr.get("ok", False)})
                except Exception as exc:
                    tool_results_text_parts.append(f"━━━ TOOL RESULTS — LOGS FAILED ━━━\n{exc}")
                    tool_calls_used.append({"tool": "logs", "ok": False})

            # --- NEED_EDIT (Phase 1 — surgical diff → propose loop) ---
            if edit_markers:
                try:
                    _set_live(session_id, "NEED_EDIT", _tool_iter, "Surgical edit…")
                except Exception:
                    pass
                try:
                    _edit_results, _edit_blocks = await process_need_edit(
                        raw, _read_file_tool, session_id=session_id or "",
                    )
                    for _ert in _edit_results:
                        tool_results_text_parts.append(_ert)
                    for _eb in _edit_blocks:
                        _edit_propose_text, _ = await _run_propose_loop(
                            initial_raw=raw,
                            initial_block=_eb,
                            user_content=user_content,
                            persona=self.persona,
                            max_retries=_propose_max_retries(),
                        )
                        tool_results_text_parts.append(_edit_propose_text)
                        tool_calls_used.append({
                            "tool": "need_edit",
                            "file": _eb.get("file_path"),
                            "ok": "FAILED" not in _edit_propose_text,
                        })
                except Exception as exc:
                    log.warning("need_edit failed: %s", exc)
                    tool_results_text_parts.append(f"━━━ NEED_EDIT FAILED ━━━\n{exc}")
                    tool_calls_used.append({"tool": "need_edit", "ok": False})

            # --- NEED_BATCH (Phase A — parallel multi-tool in 1 iteration) ---
            if batch_block:
                try:
                    _set_live(session_id, "NEED_BATCH", _tool_iter, "Паралелно изпълнение…")
                except Exception:
                    pass
                try:
                    batch_result = await execute_batch(
                        batch_block, self.memory, session_id or "",
                        read_file_fn=_read_file_tool,
                        logs_fn=_logs_tool,
                    )
                    tool_results_text_parts.append(batch_result)
                    tool_calls_used.append({"tool": "need_batch", "ok": True})
                except Exception as exc:
                    log.warning("need_batch failed: %s", exc)
                    tool_results_text_parts.append(f"━━━ TOOL RESULTS — NEED_BATCH FAILED ━━━\n{exc}")
                    tool_calls_used.append({"tool": "need_batch", "ok": False})

            # --- NEED_VERIFY (Phase A — HTTP smoke test + log tail) ---
            if verify_block:
                try:
                    _set_live(session_id, "NEED_VERIFY", _tool_iter, "Проверка на endpoint…")
                except Exception:
                    pass
                try:
                    verify_result = await execute_verify(
                        verify_block,
                        logs_fn=_logs_tool,
                    )
                    tool_results_text_parts.append(verify_result)
                    tool_calls_used.append({"tool": "need_verify", "ok": True})
                except Exception as exc:
                    log.warning("need_verify failed: %s", exc)
                    tool_results_text_parts.append(f"━━━ TOOL RESULTS — NEED_VERIFY FAILED ━━━\n{exc}")
                    tool_calls_used.append({"tool": "need_verify", "ok": False})

            # --- NEED_PROJECT_PLAN (Дедал → auto-plan голяма задача в planner) ---
            # Recursion guard: auto_plan_from_goal() and planner step runs call
            # daedalus.chat() recursively. Those nested sessions must NEVER be
            # allowed to spawn more plans (infinite loop + token burn).
            _is_nested_planner_session = bool(
                session_id and (
                    session_id.startswith("autoplan_")
                    or session_id.startswith("planner_")
                    or session_id.startswith("selfrepair_")
                )
            )
            if project_plan_m and _is_nested_planner_session:
                tool_results_text_parts.append(
                    "━━━ TOOL RESULTS — PROJECT PLAN BLOCKED ━━━\n"
                    "Recursion guard: вече си в planner/autoplan/selfrepair сесия. "
                    "НЕ можеш да създадеш нов план оттук. Завърши текущата стъпка."
                )
                tool_calls_used.append({"tool": "project_plan", "ok": False, "reason": "recursion_guard"})
                project_plan_m = None  # disarm to skip the real handler below
            if project_plan_m:
                try:
                    pp_goal = project_plan_m.group(1).strip()
                    from .planner import auto_plan_from_goal, update_plan_status
                    from .outbox import check_permission, push_outbox
                    pp_result = await auto_plan_from_goal(pp_goal)
                    if pp_result.get("ok"):
                        pp_plan_id = pp_result["plan_id"]
                        pp_steps = pp_result.get("steps", []) or []
                        # Auto-activate if owner pre-granted planner_auto_run
                        if check_permission("planner_auto_run"):
                            await update_plan_status(pp_plan_id, "active")
                            activation_note = (
                                "Permission 'planner_auto_run' is GRANTED — план е АКТИВИРАН.\n"
                                "Background scheduler ще изпълнява стъпките една по една "
                                "(всяка в нова Дедал сесия с пълни 20 итерации)."
                            )
                        else:
                            # Notify owner via outbox to approve
                            push_outbox(
                                type="message",
                                title=f"🗂  План за одобрение: {pp_result.get('title','?')[:60]}",
                                body=(
                                    f"Дедал генерира план с {len(pp_steps)} стъпки.\n\n"
                                    f"**Цел:** {pp_goal[:300]}\n"
                                    f"**Plan ID:** {pp_plan_id}\n\n"
                                    f"Approve: POST /admin/daedalus/planner/plans/{pp_plan_id}/approve\n"
                                    f"Или дай пермишън веднъж завинаги: "
                                    f"`planner_auto_run` → плановете тръгват автоматично."
                                ),
                                session_id=session_id,
                            )
                            activation_note = (
                                "Планът е в DRAFT — чака approve от Николай в admin UI "
                                "(/admin/daedalus → План режим). За auto-activate в бъдеще "
                                "Николай може да grant-не `planner_auto_run` permission."
                            )
                        steps_summary = "\n".join(
                            f"  {i+1}. {s.get('title','?')[:80]}"
                            for i, s in enumerate(pp_steps[:10])
                        )
                        result_text = (
                            f"━━━ TOOL RESULTS — PROJECT PLAN CREATED ━━━\n"
                            f"Plan ID  : {pp_plan_id}\n"
                            f"Title    : {pp_result.get('title','?')}\n"
                            f"Steps    : {len(pp_steps)}\n\n"
                            f"{steps_summary}\n\n"
                            f"{activation_note}\n\n"
                            f"Tell Nikolai: Планът е създаден с {len(pp_steps)} стъпки. "
                            f"Всяка стъпка ще се изпълни автономно в собствена сесия — "
                            f"никакво tool loop ограничение."
                        )
                        tool_calls_used.append({"tool": "project_plan", "plan_id": pp_plan_id, "steps": len(pp_steps)})
                    else:
                        result_text = (
                            f"━━━ TOOL RESULTS — PROJECT PLAN FAILED ━━━\n"
                            f"Error: {pp_result.get('error','?')}\n"
                            f"Опитай отново с по-конкретна цел или раздели сам ръчно."
                        )
                        tool_calls_used.append({"tool": "project_plan", "ok": False})
                    tool_results_text_parts.append(result_text)
                except Exception as exc:
                    tool_results_text_parts.append(f"━━━ TOOL RESULTS — PROJECT PLAN ERROR ━━━\n{exc}")
                    tool_calls_used.append({"tool": "project_plan", "ok": False})

            # --- NEED_DATA_PROPOSAL (Дедал → outbox data batch, no code sandbox) ---
            if data_prop_m:
                try:
                    dp_category = data_prop_m.group(1).strip()
                    dp_title    = data_prop_m.group(2).strip()
                    dp_raw_json = data_prop_m.group(3).strip()
                    from .outbox import push_outbox
                    import json as _json
                    try:
                        dp_payload = _json.loads(dp_raw_json)
                    except Exception:
                        dp_payload = dp_raw_json
                    # R2 (retired): a data batch is never applied from here; it is queued so it stays visible in the Outbox.
                    item = push_outbox(type="data_batch", title=dp_title,
                                       category=dp_category, payload=dp_payload,
                                       session_id=session_id)
                    result_text = (
                        f"━━━ TOOL RESULTS — DATA PROPOSAL QUEUED ━━━\n"
                        f"Outbox ID: {item['id']}\n"
                        f"Category : {dp_category}\n"
                        f"Title    : {dp_title}\n"
                        f"Records  : {len(dp_payload) if isinstance(dp_payload, list) else 1}\n\n"
                        f"It is listed in the Outbox. Standalone Daedalus does not apply data batches (that wrote into the old "
                        f"ADN host's tables); tell Nikolai it is there to read or dismiss, not that it was inserted."
                    )
                    tool_calls_used.append({"tool": "data_proposal_queued", "category": dp_category, "id": item["id"]})
                except Exception as exc:
                    tool_results_text_parts.append(f"━━━ TOOL RESULTS — DATA PROPOSAL FAILED ━━━\n{exc}")
                    tool_calls_used.append({"tool": "data_proposal", "ok": False})
                else:
                    tool_results_text_parts.append(result_text)

            # --- NEED_PROGRESS (Дедал reports live progress to admin UI) ---
            if progress_m:
                try:
                    from .outbox import set_progress
                    prog_task = progress_m.group(1).strip()
                    prog_pct  = float(progress_m.group(2).strip())
                    prog_msg  = progress_m.group(3).strip()
                    set_progress(prog_task, prog_pct, prog_msg, session_id=session_id or "")
                    tool_results_text_parts.append(
                        f"━━━ TOOL RESULTS — PROGRESS UPDATED ━━━\n"
                        f"Task: {prog_task}  {int(prog_pct * 100)}%  {prog_msg}\n"
                        f"Progress bar in admin panel updated."
                    )
                    tool_calls_used.append({"tool": "progress", "task": prog_task, "pct": prog_pct, "msg": prog_msg})
                except Exception as exc:
                    tool_results_text_parts.append(f"━━━ TOOL RESULTS — PROGRESS FAILED ━━━\n{exc}")

            # --- NEED_JOURNAL_WRITE (Дедал записва частичен резултат в scratch journal) ---
            if journal_m:
                try:
                    j_step = journal_m.group(1).strip()
                    j_summary = (journal_m.group(2) or "").strip()
                    j_data = (journal_m.group(3) or "").strip()
                    # Task name за първи запис — от briefing.intent или съобщението
                    _task_name = ""
                    try:
                        if briefing_dict:
                            _task_name = str(briefing_dict.get("intent") or "")[:60]
                    except Exception:
                        pass
                    step = _journal.write_step(
                        session_id or "", j_step, j_summary, j_data,
                        task_name=_task_name,
                    )
                    j_now = _journal.get_journal(session_id or "") or {}
                    tokens = int(j_now.get("tokens_est", 0))
                    pct = round(tokens / _journal.TOKEN_BUDGET_PER_SESSION * 100, 1)
                    tool_results_text_parts.append(
                        f"━━━ TOOL RESULTS — JOURNAL WRITE ━━━\n"
                        f"Step: {step.get('step', j_step)}\n"
                        f"Tokens: {step.get('tokens_est', 0)} (общо {tokens}, {pct}% от budget)\n"
                        f"Стъпки: {len(j_now.get('steps') or [])}\n"
                        f"Журналът се запази — owner може да види прогреса в admin panel."
                    )
                    tool_calls_used.append({
                        "tool": "journal_write", "ok": True,
                        "step": j_step, "tokens": tokens, "pct": pct,
                    })
                except Exception as exc:
                    tool_results_text_parts.append(
                        f"━━━ TOOL RESULTS — JOURNAL WRITE FAILED ━━━\n{exc}"
                    )
                    tool_calls_used.append({"tool": "journal_write", "ok": False})

            # --- NEED_TASK_STATE (Phase 17B — cross-session task tracking) ---
            if task_state_m:
                try:
                    ts_goal      = task_state_m.group(1).strip()
                    ts_phase     = task_state_m.group(2).strip()
                    ts_remaining = (task_state_m.group(3) or "").strip()
                    ts_result = _task_state.save(
                        goal=ts_goal,
                        phase=ts_phase,
                        remaining_steps=ts_remaining,
                    )
                    if ts_result.get("status") == "cleared":
                        tool_results_text_parts.append(
                            f"━━━ TOOL RESULTS — TASK STATE CLEARED ━━━\n"
                            f"Задачата '{ts_goal}' е маркирана като завършена.\n"
                            f"task-state.json е изчистен."
                        )
                    else:
                        remaining_list = ts_result.get("remaining_steps", [])
                        completed_list = ts_result.get("completed_steps", [])
                        tool_results_text_parts.append(
                            f"━━━ TOOL RESULTS — TASK STATE SAVED ━━━\n"
                            f"Цел    : {ts_result.get('goal', ts_goal)}\n"
                            f"Фаза   : {ts_result.get('phase', ts_phase)}\n"
                            f"Завършено : {len(completed_list)} стъпки\n"
                            f"Остава : {len(remaining_list)} стъпки: {', '.join(remaining_list[:3])}\n"
                            f"Task state е запазен на диск — ще се зареди автоматично в следващата сесия."
                        )
                    tool_calls_used.append({"tool": "task_state_write", "ok": True,
                                            "phase": ts_phase})
                except Exception as exc:
                    tool_results_text_parts.append(
                        f"━━━ TOOL RESULTS — TASK STATE FAILED ━━━\n{exc}"
                    )
                    tool_calls_used.append({"tool": "task_state_write", "ok": False})

            # --- NEED_PLAYBOOK (Phase 17C — процедурен наръчник за задачата) ---
            if playbook_m:
                try:
                    pb_query = playbook_m.group(1).strip()
                    pb_results = _playbooks.search(pb_query, top_k=2)
                    pb_text = _playbooks.format_for_prompt(pb_results)
                    tool_results_text_parts.append(
                        f"━━━ TOOL RESULTS — PLAYBOOK: {pb_query} ━━━\n"
                        f"{pb_text}\n\n"
                        f"Следвай стъпките по ред. Не прескачай."
                    )
                    tool_calls_used.append({
                        "tool": "playbook", "ok": True,
                        "query": pb_query,
                        "matched": [pb.get("name") for pb in pb_results],
                    })
                except Exception as exc:
                    tool_results_text_parts.append(
                        f"━━━ TOOL RESULTS — PLAYBOOK FAILED ━━━\n{exc}"
                    )
                    tool_calls_used.append({"tool": "playbook", "ok": False})

            # --- NEED_PLAN_LOCK (Phase 17C — заключване на активен план) ---
            if plan_lock_m:
                try:
                    pl_id     = plan_lock_m.group(1).strip()
                    pl_action = plan_lock_m.group(2).strip().lower()
                    if pl_action == "acquire":
                        pl_result = _plan_lock.acquire(pl_id)
                    elif pl_action == "release":
                        pl_result = _plan_lock.release(pl_id)
                    elif pl_action == "extend":
                        pl_result = _plan_lock.extend(pl_id)
                    else:  # check (default)
                        pl_result = _plan_lock.check(pl_id)
                    tool_results_text_parts.append(
                        _plan_lock.format_for_prompt(pl_action, pl_result)
                    )
                    tool_calls_used.append({
                        "tool": "plan_lock", "ok": pl_result.get("ok", True),
                        "action": pl_action, "plan_id": pl_id,
                    })
                except Exception as exc:
                    tool_results_text_parts.append(
                        f"━━━ TOOL RESULTS — PLAN LOCK FAILED ━━━\n{exc}"
                    )
                    tool_calls_used.append({"tool": "plan_lock", "ok": False})

            # --- NEED_CHECK_PERM (Дедал checks pre-granted permission before asking) ---
            if check_perm_m:
                try:
                    from .outbox import check_permission, list_permissions
                    cp_class = check_perm_m.group(1).strip()
                    granted = check_permission(cp_class)
                    if granted:
                        active = [p for p in list_permissions() if p["perm_class"] == cp_class]
                        rem = active[0]["remaining_min"] if active else 0
                        result_text = (
                            f"━━━ TOOL RESULTS — PERMISSION CHECK ━━━\n"
                            f"Permission : {cp_class}\n"
                            f"Status     : GRANTED (expires in {rem} min)\n\n"
                            f"You are AUTHORIZED to proceed with {cp_class} without asking Nikolai again."
                        )
                    else:
                        result_text = (
                            f"━━━ TOOL RESULTS — PERMISSION CHECK ━━━\n"
                            f"Permission : {cp_class}\n"
                            f"Status     : NOT GRANTED\n\n"
                            f"You must use NEED_DATA_PROPOSAL to queue the action for Nikolai's approval. "
                            f"He can grant '{cp_class}' from the Permissions panel in admin to skip "
                            f"asking next time."
                        )
                    tool_results_text_parts.append(result_text)
                    tool_calls_used.append({"tool": "check_perm", "class": cp_class, "granted": granted})
                except Exception as exc:
                    tool_results_text_parts.append(f"━━━ TOOL RESULTS — PERMISSION CHECK FAILED ━━━\n{exc}")

            # --- [P4] NEED_BLOCKED (Дедал записва блокер + спира loop) ---
            if blocked_m:
                try:
                    from .blockers import push_blocker
                    _blk_reason   = blocked_m.group(1).strip()
                    _blk_tried    = blocked_m.group(2).strip()
                    _blk_next     = blocked_m.group(3).strip()
                    _blk_file     = ""          # no proposal block exists in this scope (was a dir() guard on it)
                    _blk_id = push_blocker(
                        reason=_blk_reason,
                        tried=_blk_tried,
                        next_steps=_blk_next,
                        file_path=_blk_file,
                        session_id=session_id or "",
                    )
                    tool_results_text_parts.append(
                        f"━━━ TOOL RESULTS — BLOCKER RECORDED 🚧 ━━━\n"
                        f"Blocker ID : {_blk_id}\n"
                        f"Reason     : {_blk_reason[:200]}\n"
                        f"Tried      : {_blk_tried[:200]}\n"
                        f"Next steps : {_blk_next[:200]}\n\n"
                        f"Blocker е записан. Николай ще го види в 🚧 Blockers панела.\n"
                        f"Обясни му ясно какво ти трябва, за да продължиш."
                    )
                    tool_calls_used.append({"tool": "need_blocked", "id": _blk_id, "reason": _blk_reason[:80]})
                except Exception as _blk_exc:
                    tool_results_text_parts.append(
                        f"━━━ TOOL RESULTS — BLOCKER RECORD FAILED ━━━\n{_blk_exc}\n"
                        f"Въпреки грешката — обясни на Николай какво те блокира."
                    )

            # --- NEED_INBOX_MSG (Дедал → Replit Agent inbox) ---
            if inbox_msg_m:
                try:
                    subj = inbox_msg_m.group(1).strip()
                    body_ = inbox_msg_m.group(2).strip()
                    prio = int(inbox_msg_m.group(3)) if inbox_msg_m.group(3) else 2
                    from .planner import write_inbox
                    imr = await write_inbox(subject=subj, body=body_, from_who="daedalus", priority=prio)
                    tool_results_text_parts.append(
                        f"━━━ TOOL RESULTS — INBOX MSG SENT ━━━\n"
                        f"Message ID: {imr.get('message_id')}\n"
                        f"Subject: {subj}\n"
                        f"Replit Agent ще го прочете при следващата сесия."
                    )
                    tool_calls_used.append({"tool": "inbox_msg", "subject": subj, "ok": imr.get("ok", False)})
                except Exception as exc:
                    tool_results_text_parts.append(f"━━━ TOOL RESULTS — INBOX MSG FAILED ━━━\n{exc}")
                    tool_calls_used.append({"tool": "inbox_msg", "ok": False})

            # --- NEED_PLAN_STEP (Дедал добавя стъпка към план) ---
            if plan_step_m:
                try:
                    ps_plan_id = plan_step_m.group(1).strip()
                    ps_title   = plan_step_m.group(2).strip()
                    ps_desc    = (plan_step_m.group(3) or "").strip()
                    from .planner import add_step
                    psr = await add_step(ps_plan_id, ps_title, ps_desc)
                    tool_results_text_parts.append(
                        f"━━━ TOOL RESULTS — PLAN STEP ADDED ━━━\n"
                        f"Plan: {ps_plan_id}\n"
                        f"Step ID: {psr.get('step_id')}\n"
                        f"Title: {ps_title}\n"
                        f"Scheduler ще я изпълни автоматично."
                    )
                    tool_calls_used.append({"tool": "plan_step", "plan": ps_plan_id, "ok": psr.get("ok", False)})
                except Exception as exc:
                    tool_results_text_parts.append(f"━━━ TOOL RESULTS — PLAN STEP FAILED ━━━\n{exc}")
                    tool_calls_used.append({"tool": "plan_step", "ok": False})

            # ── Lint Gate ────────────────────────────────────────────
            if lint_q:
                try:
                    from .lint_gate import lint_file, format_for_daedalus as _fmt_lint
                    _lr = lint_file(lint_q.strip())
                    tool_results_text_parts.append(
                        f"━━━ TOOL RESULTS — NEED_LINT ━━━\n{_fmt_lint(_lr)}"
                    )
                    tool_calls_used.append({"tool": "lint", "path": lint_q, "ok": _lr.ok,
                                            "blockers": len(_lr.blockers), "warnings": len(_lr.warnings)})
                except Exception as exc:
                    tool_results_text_parts.append(f"━━━ TOOL RESULTS — NEED_LINT FAILED ━━━\n{exc}")

            # ── TDD Loop — autonomous test-driven fix cycle ──────────
            if tdd_m:
                try:
                    from .tdd_loop import tdd_loop as _tdd_loop
                    _tdd_file     = tdd_m["file_path"]
                    _tdd_test     = tdd_m["test_code"]
                    _tdd_initial  = tdd_m.get("initial_code", "")
                    _set_live(session_id, "NEED_TDD", _tool_iter,
                              f"TDD loop → {_tdd_file} — стартиран…")
                    _tdd_r = await _tdd_loop(
                        file_path=_tdd_file,
                        test_code=_tdd_test,
                        initial_code=_tdd_initial,
                        reason="Дедал TDD auto-loop",
                        proposed_by="daedalus-tdd",
                    )
                    tool_results_text_parts.append(
                        f"━━━ TOOL RESULTS — TDD LOOP ━━━\n"
                        f"{_tdd_r.format_for_daedalus()}"
                    )
                    tool_calls_used.append({
                        "tool": "tdd_loop",
                        "file": _tdd_file,
                        "ok": _tdd_r.ok,
                        "iterations": _tdd_r.iterations_used,
                        "proposal_id": _tdd_r.proposal_id,
                    })
                except Exception as exc:
                    tool_results_text_parts.append(
                        f"━━━ TOOL RESULTS — TDD LOOP FAILED ━━━\n{exc}"
                    )
                    tool_calls_used.append({"tool": "tdd_loop", "ok": False})

            # ── Self-awareness tools ─────────────────────────────────
            if capability_q:
                try:
                    from argos_data_nexus.modules.capability_search import search_capabilities
                    cr = search_capabilities(capability_q, limit=8)
                    matches = cr.get("matches", []) or []
                    if matches:
                        rows = "\n".join(
                            f"  [{m.get('score', 0):.2f}] {m['method']:<6} {m['path']}\n"
                            f"        → {m.get('summary', '')[:120]}"
                            for m in matches
                        )
                        tool_results_text_parts.append(
                            f"━━━ TOOL RESULTS — CAPABILITY MATCHES ━━━\n"
                            f"Query: {capability_q}\nMatches: {len(matches)}\n{rows}\n"
                            f"(Извикай конкретен endpoint директно вместо да предлагаш нов.)"
                        )
                    else:
                        tool_results_text_parts.append(
                            f"━━━ TOOL RESULTS — CAPABILITY MATCHES ━━━\n"
                            f"Query: {capability_q}\nНяма match. Може да липсва — "
                            f"предложи NEED_PROPOSE_START за нов endpoint."
                        )
                    tool_calls_used.append({"tool": "capability_search", "ok": True, "count": len(matches)})
                except Exception as exc:
                    tool_results_text_parts.append(f"━━━ TOOL RESULTS — CAPABILITY FAILED ━━━\n{exc}")
                    tool_calls_used.append({"tool": "capability_search", "ok": False})

            if skill_catalog_q:
                try:
                    from .skill_catalog import search_skills as _sc_search, catalog_status as _sc_status
                    _hits = _sc_search(skill_catalog_q, top_k=5)
                    if _hits:
                        _rows = "\n\n".join(
                            f"  [{h['score']:.2f}] **{h['title']}** ({h['category']})\n"
                            f"  {h['text'][:400]}"
                            for h in _hits
                        )
                        tool_results_text_parts.append(
                            f"━━━ TOOL RESULTS — SKILL CATALOG ━━━\n"
                            f"Query: {skill_catalog_q}\nMatches: {len(_hits)}\n\n{_rows}\n\n"
                            f"(Приложи тези техники директно — не предлагай нов код ако решението е тук.)"
                        )
                    else:
                        _st = _sc_status()
                        tool_results_text_parts.append(
                            f"━━━ TOOL RESULTS — SKILL CATALOG ━━━\n"
                            f"Query: {skill_catalog_q}\n"
                            f"Няма match в каталога ({_st.get('total_skills', 0)} умения индексирани).\n"
                            f"Продължи с NEED_WEB или NEED_AI_DIGEST за търсене на решение."
                        )
                    tool_calls_used.append({"tool": "skill_catalog", "query": skill_catalog_q, "ok": True, "hits": len(_hits)})
                except Exception as exc:
                    tool_results_text_parts.append(f"━━━ TOOL RESULTS — SKILL CATALOG FAILED ━━━\n{exc}")
                    tool_calls_used.append({"tool": "skill_catalog", "ok": False})

            if ai_digest_q:
                try:
                    from argos_data_nexus.modules.ai_digest import get_ai_digest
                    _refresh = "refresh" in (ai_digest_q or "").lower()
                    _d = await get_ai_digest(force_refresh=_refresh)
                    _papers = _d.get("papers", [])[:5]
                    _news   = _d.get("news",   [])[:4]
                    _repos  = _d.get("repos",  [])[:3]
                    _lines  = [
                        f"━━━ TOOL RESULTS — AI DIGEST ({_d.get('fetched_at_iso','')}) ━━━",
                        f"Papers: {len(_papers)} | News: {len(_news)} | Repos: {len(_repos)}",
                    ]
                    if _papers:
                        _lines.append("\n📄 LATEST PAPERS (arXiv):")
                        for p in _papers:
                            _lines.append(f"  • [{p.get('published','')}] {p['title'][:100]}\n    {p['url']}\n    {p.get('summary','')[:150]}")
                    if _news:
                        _lines.append("\n📰 COMMUNITY NEWS (Hacker News):")
                        for n in _news:
                            _lines.append(f"  • [{n.get('score',0)}pts] {n['title'][:100]}\n    {n['url']}")
                    if _repos:
                        _lines.append("\n⭐ TRENDING REPOS (GitHub):")
                        for r in _repos:
                            _lines.append(f"  • [{r.get('score',0)}⭐] {r['title']}\n    {r.get('summary','')[:120]}")
                    tool_results_text_parts.append("\n".join(_lines))
                    tool_calls_used.append({"tool": "ai_digest", "ok": True,
                                            "papers": len(_papers), "news": len(_news), "repos": len(_repos)})
                except Exception as exc:
                    tool_results_text_parts.append(f"━━━ TOOL RESULTS — AI DIGEST FAILED ━━━\n{exc}")
                    tool_calls_used.append({"tool": "ai_digest", "ok": False})

            # ── Playground tools (build → test → publish standalone apps) ──
            if pg_new_m:
                # Guard: 1 session = 1 playground project.
                # Check BOTH the current-turn list AND Replit DB (persists across turns).
                _existing_pg_slug = next(
                    (t.get("slug") for t in tool_calls_used
                     if t.get("tool") == "playground_new" and t.get("ok")),
                    None,
                )
                # Cross-turn check: look up session slug in Replit DB
                if not _existing_pg_slug and session_id:
                    try:
                        from .kv import db as _rdb_pg
                        _db_pg_key = f"adn:daedalus_pg_slug:{session_id}"
                        _existing_pg_slug = _rdb_pg.get(_db_pg_key) or None
                    except Exception:
                        pass

                if _existing_pg_slug:
                    tool_results_text_parts.append(
                        f"━━━ TOOL RESULTS — PLAYGROUND NEW BLOCKED ━━━\n"
                        f"⛔ Вече имаш проект за тази сесия: slug={_existing_pg_slug}\n"
                        f"ПРАВИЛО: 1 задача = 1 проект — важи за ВСИЧКИ turn-ове.\n"
                        f"ЗАДЪЛЖИТЕЛНО: Поправи съществуващия проект:\n"
                        f"  1. <NEED_PLAYGROUND_READ: {_existing_pg_slug}>\n"
                        f"  2. <NEED_PLAYGROUND_WRITE: {_existing_pg_slug} | <файл> | <fixed code> >>>\n"
                        f"НЕ СЪЗДАВАЙ НОВ ПРОЕКТ. НЕ ИЗБИРАЙ РАЗЛИЧЕН KIND."
                    )
                    tool_calls_used.append({"tool": "playground_new", "ok": False, "blocked": True})
                else:
                    try:
                        from . import playground as _pg
                        from .playground_agent_guards import resolve_existing_project_slug
                        _name = pg_new_m.group(1).strip()
                        _kind = (pg_new_m.group(2) or "").strip()
                        _desc = (pg_new_m.group(3) or "").strip()
                        _existing_proj = await resolve_existing_project_slug(_name)
                        if _existing_proj:
                            _proj = _existing_proj
                            if session_id:
                                try:
                                    from .kv import db as _rdb_pg2
                                    _rdb_pg2[f"adn:daedalus_pg_slug:{session_id}"] = _proj["slug"]
                                    _rdb_pg2[f"adn:daedalus_pg_context:{session_id}"] = (
                                        f"{_proj.get('kind', 'static')}|{_proj.get('name', '')} {_desc}"
                                    )
                                except Exception:
                                    pass
                            tool_results_text_parts.append(
                                f"━━━ TOOL RESULTS — PLAYGROUND NEW (EXISTING) ━━━\n"
                                f"Проектът вече съществува: slug={_proj['slug']} kind={_proj['kind']}\n"
                                f"НЕ създавай нов. Ползвай NEED_PLAYGROUND_WRITE за поправки.\n"
                                f"Slug фиксиран: '{_proj['slug']}'."
                            )
                            tool_calls_used.append({"tool": "playground_new", "ok": True, "slug": _proj["slug"], "reused": True})
                        else:
                            _proj = await _pg.create_project(_name, kind=_kind, source="built",
                                                             description=_desc)
                            # Persist slug to Replit DB for cross-turn guard
                            if session_id:
                                try:
                                    from .kv import db as _rdb_pg2
                                    _rdb_pg2[f"adn:daedalus_pg_slug:{session_id}"] = _proj["slug"]
                                    _rdb_pg2[f"adn:daedalus_pg_context:{session_id}"] = (
                                        f"{_proj.get('kind', 'static')}|{_name} {_desc}"
                                    )
                                except Exception:
                                    pass
                            tool_results_text_parts.append(
                                f"━━━ TOOL RESULTS — PLAYGROUND NEW ━━━\n"
                                f"Създаден проект: slug={_proj['slug']} kind={_proj['kind']}\n"
                                f"Папка: playground/projects/{_proj['slug']}/\n"
                                f"⚠️ ЗАПОМНИ: Slug-ът е '{_proj['slug']}' — ползвай САМО него за ВСИЧКИ следващи операции.\n"
                                f"СЛЕДВАЩА ТИ СТЪПКА: запиши реален код с NEED_PLAYGROUND_WRITE (НЕ READ първо).\n"
                                f"ЗАДЪЛЖИТЕЛНО използвай >>> като край (не >):\n"
                                f"<NEED_PLAYGROUND_WRITE: {_proj['slug']} | index.html |\n"
                                f"<!DOCTYPE html>...ЦЯЛ КОД...\n"
                                f">>>\n"
                                f"Пиши ПЪЛЕН работещ код — без TODO, без placeholder."
                            )
                            tool_calls_used.append({"tool": "playground_new", "ok": True, "slug": _proj["slug"]})
                    except Exception as exc:
                        tool_results_text_parts.append(f"━━━ TOOL RESULTS — PLAYGROUND NEW FAILED ━━━\n{exc}")
                        tool_calls_used.append({"tool": "playground_new", "ok": False})

            if pg_manifest_m:
                _man_slug = pg_manifest_m.group(1).strip()
                _man_raw = (pg_manifest_m.group(2) or "").strip()
                try:
                    import json as _json
                    _man_obj = _json.loads(_man_raw)
                    from .kv import db as _rdb_man
                    _rdb_man[f"adn:daedalus_pg_manifest:{_man_slug}"] = _man_raw
                    _mods = _man_obj.get("modules") or _man_obj.get("files") or []
                    _forb = _man_obj.get("forbidden") or _man_obj.get("forbidden_files") or []
                    tool_results_text_parts.append(
                        f"━━━ TOOL RESULTS — PLAYGROUND MANIFEST SAVED ━━━\n"
                        f"Договор записан за slug={_man_slug}.\n"
                        f"Модули/файлове: {len(_mods)} · Forbidden: {len(_forb)}\n"
                        f"От сега WRITE се валидира спрямо този договор (forbidden файлове = блок)."
                    )
                    tool_calls_used.append({"tool": "playground_manifest", "ok": True, "slug": _man_slug})
                except Exception as exc:
                    tool_results_text_parts.append(
                        f"━━━ TOOL RESULTS — PLAYGROUND MANIFEST FAILED ━━━\n"
                        f"Невалиден JSON: {exc}\n"
                        f"Формат: <NEED_PLAYGROUND_MANIFEST: slug | {{\"files\": [...], \"forbidden\": [...], \"rules\": [...]}}>>>"
                    )
                    tool_calls_used.append({"tool": "playground_manifest", "ok": False})

            if pg_write_matches:
                from . import playground as _pg
                from .lint_gate import lint_content as _lint_content
                written_summary = []
                all_ok = True
                for _slug_r, _path_r, _content_r in pg_write_matches:
                    _slug_r = _slug_r.strip()
                    _path_r = _path_r.strip()
                    # ── Contract validation: block forbidden files (fail-closed) ──
                    _blocked, _reason = _pg_manifest_forbidden_check(_slug_r, _path_r)
                    if _blocked:
                        written_summary.append(
                            f"  ⛔ {_slug_r}/{_path_r} БЛОКИРАН — {_reason}"
                        )
                        tool_calls_used.append({"tool": "playground_write", "ok": False, "slug": _slug_r, "blocked": "forbidden"})
                        all_ok = False
                        continue
                    try:
                        n = _pg.write_files(_slug_r, {_path_r: _content_r})
                        # ── Auto-lint Python files (soft gate — warn, don't block) ──
                        _lint_note = ""
                        try:
                            if _path_r.endswith(".py"):
                                _lr = _lint_content(_path_r, _content_r)
                                if _lr.blockers:
                                    _lint_note = (
                                        f" ⚠️ LINT: {len(_lr.blockers)} blocker(s) — "
                                        + "; ".join(f"{b.code} L{b.row}: {b.message}" for b in _lr.blockers[:3])
                                    )
                        except Exception:
                            pass
                        written_summary.append(f"  ✅ {_slug_r}/{_path_r} ({len(_content_r)} байта){_lint_note}")
                        tool_calls_used.append({"tool": "playground_write", "ok": True, "slug": _slug_r, "path": _path_r})
                    except Exception as exc:
                        written_summary.append(f"  ❌ {_slug_r}/{_path_r} FAILED: {exc}")
                        tool_calls_used.append({"tool": "playground_write", "ok": False, "slug": _slug_r})
                        all_ok = False
                tool_results_text_parts.append(
                    f"━━━ TOOL RESULTS — PLAYGROUND WRITE ({'OK' if all_ok else 'PARTIAL'}) ━━━\n"
                    + "\n".join(written_summary)
                )

            if pg_edit_matches:
                from . import playground as _pg
                edit_summary = []
                all_ok = True
                for _slug_e, _path_e, _search_e, _replace_e in pg_edit_matches:
                    _slug_e = _slug_e.strip()
                    _path_e = _path_e.strip()
                    # ── Contract validation: block forbidden files (fail-closed) ──
                    _blocked_e, _reason_e = _pg_manifest_forbidden_check(_slug_e, _path_e)
                    if _blocked_e:
                        edit_summary.append(
                            f"  ⛔ {_slug_e}/{_path_e} БЛОКИРАН — {_reason_e}"
                        )
                        tool_calls_used.append({"tool": "playground_edit", "ok": False, "slug": _slug_e, "blocked": "forbidden"})
                        all_ok = False
                        continue
                    try:
                        res = _pg.edit_file(_slug_e, _path_e, _search_e, _replace_e)
                        if res.get("ok"):
                            edit_summary.append(
                                f"  ✅ {_slug_e}/{_path_e} ({res.get('old_size')}→{res.get('new_size')} байта)"
                            )
                            tool_calls_used.append({"tool": "playground_edit", "ok": True, "slug": _slug_e, "path": _path_e})
                        else:
                            edit_summary.append(f"  ❌ {_slug_e}/{_path_e}: {res.get('error')}")
                            tool_calls_used.append({"tool": "playground_edit", "ok": False, "slug": _slug_e})
                            all_ok = False
                    except Exception as exc:
                        edit_summary.append(f"  ❌ {_slug_e}/{_path_e} FAILED: {exc}")
                        tool_calls_used.append({"tool": "playground_edit", "ok": False, "slug": _slug_e})
                        all_ok = False
                tool_results_text_parts.append(
                    f"━━━ TOOL RESULTS — PLAYGROUND EDIT ({'OK' if all_ok else 'PARTIAL'}) ━━━\n"
                    + "\n".join(edit_summary)
                    + ("" if all_ok else
                       "\nЗа неуспелите: прочети файла (NEED_PLAYGROUND_READ), копирай ТОЧНИЯ текст "
                       "(вкл. интервали) и опитай пак, или ползвай NEED_PLAYGROUND_WRITE за цял файл.")
                )

            if pg_run_q:
                try:
                    from . import playground as _pg_run
                    from . import playground_runner as _runner
                    from .playground_runtime import is_local_only_kind, local_only_kind_message
                    from .playground_agent_guards import playground_run_block_reason
                    _slug = pg_run_q.strip()
                    _proj = await _pg_run.get_project(_slug)
                    _kind = (_proj or {}).get("kind", "static")
                    _static_block = playground_run_block_reason(_kind)
                    if _static_block:
                        tool_results_text_parts.append(
                            f"━━━ TOOL RESULTS — PLAYGROUND RUN (static preview) ━━━\n"
                            f"{_static_block.replace('{slug}', _slug)}"
                        )
                        tool_calls_used.append({
                            "tool": "playground_run",
                            "ok": True,
                            "blocked": True,
                            "reason": "static_preview",
                        })
                    elif _proj and is_local_only_kind(_kind):
                        tool_results_text_parts.append(
                            f"━━━ TOOL RESULTS — PLAYGROUND RUN BLOCKED ━━━\n"
                            f"{local_only_kind_message(_proj.get('kind', ''))}\n"
                            f"За Unity: <NEED_PLAYGROUND_PUSH: {_slug}> или <NEED_PLAYGROUND_PUSH: {_slug} | | gitlab> "
                            f"— после отвори repo в Unity Hub."
                        )
                        tool_calls_used.append({"tool": "playground_run", "ok": False})
                    else:
                        rec = await _runner.start(_slug, install_deps=True)
                        tool_results_text_parts.append(
                            f"━━━ TOOL RESULTS — PLAYGROUND RUN ━━━\n"
                            f"Стартиран {_slug}: status={rec.get('status')} port={rec.get('port')}\n"
                            f"Preview: /admin/daedalus/playground/preview/{_slug}/\n"
                            f"Логове ще видиш в Playground UI."
                        )
                        tool_calls_used.append({"tool": "playground_run", "ok": True})
                except Exception as exc:
                    tool_results_text_parts.append(f"━━━ TOOL RESULTS — PLAYGROUND RUN FAILED ━━━\n{exc}")
                    tool_calls_used.append({"tool": "playground_run", "ok": False})

            if pg_clone_m:
                try:
                    from . import playground_github as _gh
                    _url = pg_clone_m.group(1).strip()
                    _nm = (pg_clone_m.group(2) or "").strip()
                    _proj = await _gh.clone_repo(_url, name=_nm)
                    tool_results_text_parts.append(
                        f"━━━ TOOL RESULTS — PLAYGROUND CLONE ━━━\n"
                        f"Clone-нат {_url} → slug={_proj['slug']} kind={_proj['kind']}\n"
                        f"Съживи проекта: чети файлове, run, поправи."
                    )
                    tool_calls_used.append({"tool": "playground_clone", "ok": True, "slug": _proj["slug"]})
                except Exception as exc:
                    tool_results_text_parts.append(f"━━━ TOOL RESULTS — PLAYGROUND CLONE FAILED ━━━\n{exc}")
                    tool_calls_used.append({"tool": "playground_clone", "ok": False})

            if pg_read_m:
                try:
                    from . import playground as _pg
                    from .playground_agent_guards import entrypoint_read_warning, read_file_guard
                    _slug = pg_read_m.group(1).strip()
                    _path = (pg_read_m.group(2) or "").strip()
                    _proj = await _pg.get_project(_slug)
                    _kind = (_proj or {}).get("kind", "static")
                    if _path:
                        _guard = read_file_guard(_slug, _path)
                        if _guard:
                            tool_results_text_parts.append(
                                f"━━━ TOOL RESULTS — PLAYGROUND READ BLOCKED ━━━\n{_guard}"
                            )
                            tool_calls_used.append({"tool": "playground_read", "ok": False, "slug": _slug})
                        else:
                            _hint = entrypoint_read_warning(_kind, _path)
                            _content = _pg.read_file(_slug, _path, max_bytes=80_000)
                            tool_results_text_parts.append(
                                f"━━━ TOOL RESULTS — PLAYGROUND READ ━━━\n"
                                f"{_hint + chr(10) if _hint else ''}"
                                f"Файл: {_slug}/{_path} ({len(_content)} байта)\n"
                                f"```\n{_content[:12000]}\n```"
                            )
                            tool_calls_used.append({"tool": "playground_read", "ok": True, "slug": _slug})
                    else:
                        _tree = _pg.read_tree(_slug)
                        lines = "\n".join(f"  {f['path']} ({f['size']}B)" for f in _tree)
                        from .playground_agent_guards import expo_web_kind_mismatch
                        _mismatch = expo_web_kind_mismatch(_kind, [f["path"] for f in _tree])
                        tool_results_text_parts.append(
                            f"━━━ TOOL RESULTS — PLAYGROUND READ (TREE) ━━━\n"
                            f"{(_mismatch + chr(10)) if _mismatch else ''}"
                            f"Проект: {_slug} ({len(_tree)} файла)\n{lines}"
                        )
                        tool_calls_used.append({"tool": "playground_read", "ok": True, "slug": _slug})
                except Exception as exc:
                    tool_results_text_parts.append(f"━━━ TOOL RESULTS — PLAYGROUND READ FAILED ━━━\n{exc}")
                    tool_calls_used.append({"tool": "playground_read", "ok": False})

            if pg_push_m:
                try:
                    from .playground_git_push import push_project_remote
                    _slug = pg_push_m.group(1).strip()
                    _repo = (pg_push_m.group(2) or "").strip()
                    _prov_raw = (pg_push_m.group(3) or "").strip().lower()
                    _provider = _prov_raw if _prov_raw in ("gitlab", "github", "auto") else "auto"
                    # If second segment is provider keyword, not repo path
                    if _repo in ("gitlab", "github") and not _prov_raw:
                        _provider = _repo
                        _repo = ""
                    res = await push_project_remote(
                        _slug,
                        provider=_provider,
                        repo_full_name=_repo,
                        create_if_missing=not bool(_repo),
                        private=True,
                    )
                    _prov = res.get("provider") or "git"
                    tool_results_text_parts.append(
                        f"━━━ TOOL RESULTS — PLAYGROUND PUSH ({_prov}) ━━━\n"
                        f"Push-нат {_slug} → {res.get('html_url')}\n"
                        f"Repo: {res.get('repo')} | Branch: {res.get('branch')}\n"
                        f"{'(Нов repo създаден)' if res.get('created') else '(Съществуващ repo актуализиран)'}"
                    )
                    tool_calls_used.append({"tool": "playground_push", "ok": True, "slug": _slug})
                except Exception as exc:
                    tool_results_text_parts.append(f"━━━ TOOL RESULTS — PLAYGROUND PUSH FAILED ━━━\n{exc}")
                    tool_calls_used.append({"tool": "playground_push", "ok": False})

            if pg_pub_m:
                try:
                    from .playground_agent_guards import publish_with_repair
                    _slug = pg_pub_m.group(1).strip()
                    _icon = (pg_pub_m.group(2) or "🎮").strip()
                    res = await publish_with_repair(_slug, icon=_icon)
                    if res.get("ok"):
                        _extra = ""
                        if res.get("repaired_files"):
                            _extra = f"\n🔧 Поправени: {', '.join(res['repaired_files'][:8])}"
                        tool_results_text_parts.append(
                            f"━━━ TOOL RESULTS — PLAYGROUND PUBLISH ━━━\n"
                            f"Публикуван {_slug} → {res.get('url')}{_extra}\n"
                            f"Durable (object storage): {res.get('durable')}. "
                            f"Вече е в менюто на /apps/."
                        )
                    else:
                        tool_results_text_parts.append(
                            f"━━━ TOOL RESULTS — PLAYGROUND PUBLISH ━━━\n{res.get('error')}"
                        )
                    tool_calls_used.append({"tool": "playground_publish", "ok": res.get("ok", False)})
                except Exception as exc:
                    tool_results_text_parts.append(f"━━━ TOOL RESULTS — PLAYGROUND PUBLISH FAILED ━━━\n{exc}")
                    tool_calls_used.append({"tool": "playground_publish", "ok": False})

            if pg_db_m:
                try:
                    from .kv import db as _rdb
                    from . import playground as _pg
                    import json as _j
                    _slug = pg_db_m.group(1).strip()
                    _action = (pg_db_m.group(2) or "list").strip().lower()
                    _key = (pg_db_m.group(3) or "").strip()
                    _value_raw = (pg_db_m.group(4) or "").strip()
                    _ns = f"playground:db:{_pg.slugify(_slug)}:"

                    if _action == "list":
                        _keys = [k[len(_ns):] for k in _rdb.prefix(_ns)]
                        _lines = []
                        for _k in _keys[:20]:
                            _v = _rdb.get(_ns + _k)
                            _lines.append(f"  • {_k} = {_j.dumps(_v, ensure_ascii=False)[:80]}")
                        tool_results_text_parts.append(
                            f"━━━ TOOL RESULTS — PLAYGROUND DB LIST ━━━\n"
                            f"App: {_slug} | {len(_keys)} ключа\n"
                            + ("\n".join(_lines) or "(пусто)")
                        )
                    elif _action == "get":
                        if not _key:
                            raise ValueError("get needs a key")
                        _val = _rdb.get(_ns + _key)
                        tool_results_text_parts.append(
                            f"━━━ TOOL RESULTS — PLAYGROUND DB GET ━━━\n"
                            f"{_slug}/{_key} = {_j.dumps(_val, ensure_ascii=False)[:2000]}"
                        )
                    elif _action == "set":
                        if not _key:
                            raise ValueError("set needs a key")
                        try:
                            _val = _j.loads(_value_raw) if _value_raw else ""
                        except Exception:
                            _val = _value_raw
                        _rdb[_ns + _key] = _val
                        tool_results_text_parts.append(
                            f"━━━ TOOL RESULTS — PLAYGROUND DB SET ━━━\n"
                            f"✅ {_slug}/{_key} записан ({len(str(_val))} chars)"
                        )
                    elif _action == "del":
                        if not _key:
                            raise ValueError("del needs a key")
                        _all_k = list(_rdb.prefix(_ns))
                        if _ns + _key in _all_k:
                            del _rdb[_ns + _key]
                            tool_results_text_parts.append(
                                f"━━━ TOOL RESULTS — PLAYGROUND DB DEL ━━━\n"
                                f"✅ {_slug}/{_key} изтрит"
                            )
                        else:
                            tool_results_text_parts.append(
                                f"━━━ TOOL RESULTS — PLAYGROUND DB DEL ━━━\n"
                                f"⚠ {_slug}/{_key} не е намерен"
                            )
                    elif _action == "clear":
                        _all_k = list(_rdb.prefix(_ns))
                        for _k in _all_k:
                            del _rdb[_k]
                        tool_results_text_parts.append(
                            f"━━━ TOOL RESULTS — PLAYGROUND DB CLEAR ━━━\n"
                            f"✅ Изтрити {len(_all_k)} ключа от {_slug}"
                        )
                    else:
                        raise ValueError(
                            f"Unknown action: {_action!r}. "
                            f"Allowed: list / get / set / del / clear"
                        )
                    tool_calls_used.append({"tool": "playground_db", "ok": True, "action": _action})
                except Exception as exc:
                    tool_results_text_parts.append(f"━━━ TOOL RESULTS — PLAYGROUND DB FAILED ━━━\n{exc}")
                    tool_calls_used.append({"tool": "playground_db", "ok": False})

            if endpoints_q:
                try:
                    er = await list_endpoints_tool(endpoints_q if endpoints_q != "*" else None)
                    if er.get("ok"):
                        rows = "\n".join(
                            f"  {','.join(r['methods']):<12} {r['path']}  ({r.get('name','')})"
                            for r in er.get("routes", [])[:80]
                        )
                        tool_results_text_parts.append(
                            f"━━━ TOOL RESULTS — FASTAPI ENDPOINTS ━━━\n"
                            f"Filter: {endpoints_q} | Total: {er['count']}\n{rows}"
                        )
                    else:
                        tool_results_text_parts.append(f"━━━ TOOL RESULTS — ENDPOINTS FAILED ━━━\n{er.get('error')}")
                    tool_calls_used.append({"tool": "list_endpoints", "ok": er.get("ok", False), "count": er.get("count", 0)})
                except Exception as exc:
                    tool_results_text_parts.append(f"━━━ TOOL RESULTS — ENDPOINTS FAILED ━━━\n{exc}")
                    tool_calls_used.append({"tool": "list_endpoints", "ok": False})

            if modules_q:
                try:
                    mr = await list_modules_tool(modules_q if modules_q != "*" else None)
                    if mr.get("ok"):
                        rows = "\n".join(
                            f"  {m['path']:<60} ({m['lines']}L) — {m.get('doc','')[:80]}"
                            for m in mr.get("modules", [])[:60]
                        )
                        tool_results_text_parts.append(
                            f"━━━ TOOL RESULTS — ADN MODULES ━━━\n"
                            f"Filter: {modules_q} | Total: {mr['count']}\n{rows}"
                        )
                    else:
                        tool_results_text_parts.append(f"━━━ TOOL RESULTS — MODULES FAILED ━━━\n{mr.get('error')}")
                    tool_calls_used.append({"tool": "list_modules", "ok": mr.get("ok", False), "count": mr.get("count", 0)})
                except Exception as exc:
                    tool_results_text_parts.append(f"━━━ TOOL RESULTS — MODULES FAILED ━━━\n{exc}")
                    tool_calls_used.append({"tool": "list_modules", "ok": False})

            if db_schema_q:
                try:
                    tbl = db_schema_q if db_schema_q != "*" else None
                    dr = await describe_db_tool(tbl)
                    if dr.get("ok"):
                        if dr.get("table"):
                            cols = "\n".join(
                                f"  {c['column_name']:<28} {c['data_type']:<20} "
                                f"{'NULL' if c['is_nullable']=='YES' else 'NOT NULL'}"
                                f"{' DEFAULT '+str(c['column_default']) if c.get('column_default') else ''}"
                                for c in dr.get("columns", [])
                            )
                            body = f"Table: {dr['table']} ({dr.get('row_count','?')} rows)\n{cols}"
                        else:
                            body = "\n".join(
                                f"  {t['table']:<40} cols={t['columns']:<4} rows={t.get('rows','?')}"
                                for t in dr.get("tables", [])
                            )
                        tool_results_text_parts.append(
                            f"━━━ TOOL RESULTS — DB SCHEMA ━━━\n{body}"
                        )
                    else:
                        tool_results_text_parts.append(f"━━━ TOOL RESULTS — DB SCHEMA FAILED ━━━\n{dr.get('error')}")
                    tool_calls_used.append({"tool": "describe_db", "ok": dr.get("ok", False)})
                except Exception as exc:
                    tool_results_text_parts.append(f"━━━ TOOL RESULTS — DB SCHEMA FAILED ━━━\n{exc}")
                    tool_calls_used.append({"tool": "describe_db", "ok": False})

            if code_search_q:
                try:
                    csr = await search_code_tool(code_search_q)
                    if csr.get("ok"):
                        sem_hits = csr.get("semantic_matches", []) or []
                        kw_hits = csr.get("keyword_matches", []) or []
                        sem_count = int(csr.get("semantic_count", len(sem_hits)))
                        kw_count = int(csr.get("keyword_count", len(kw_hits)))
                        sem_lines: list[str] = []
                        for h in sem_hits[:15]:
                            if isinstance(h, dict):
                                fp = h.get("filepath", h.get("file", "?"))
                                qn = h.get("qualified_name", h.get("name", ""))
                                sig = h.get("signature", "")
                                doc = (h.get("docstring") or "").strip().splitlines()[:1]
                                doc_line = f"  # {doc[0][:120]}" if doc else ""
                                sem_lines.append(f"• {fp} :: {qn} {sig}{doc_line}".strip())
                            else:
                                sem_lines.append(f"• {h}")
                        kw_lines = [f"  {ln}" for ln in kw_hits[:20]]
                        body_parts = []
                        if sem_lines:
                            body_parts.append("Semantic:\n" + "\n".join(sem_lines))
                        if kw_lines:
                            body_parts.append("Keyword (rg):\n" + "\n".join(kw_lines))
                        body = "\n\n".join(body_parts) if body_parts else "(no matches)"
                        total = sem_count + kw_count
                        tool_results_text_parts.append(
                            f"━━━ TOOL RESULTS — CODE SEARCH ━━━\n"
                            f"Query: {code_search_q} | Type: {csr.get('search_type', '?')} | "
                            f"Matches: {total} (semantic={sem_count}, keyword={kw_count})\n"
                            f"{body}"
                        )
                    else:
                        tool_results_text_parts.append(f"━━━ TOOL RESULTS — CODE SEARCH FAILED ━━━\n{csr.get('error')}")
                    tool_calls_used.append({"tool": "search_code", "query": code_search_q, "ok": csr.get("ok", False)})
                except Exception as exc:
                    tool_results_text_parts.append(f"━━━ TOOL RESULTS — CODE SEARCH FAILED ━━━\n{exc}")
                    tool_calls_used.append({"tool": "search_code", "ok": False})

            # ── DeutschesRechtsPortal tools ───────────────────────────────────
            # NEED_DRP_LAW: <NEED_DRP_LAW: BGB | 823>  or  <NEED_DRP_LAW: GG | 5 | dejure>
            if drp_law_q:
                try:
                    parts_dl = [p.strip() for p in drp_law_q.split("|")]
                    dl_abbr  = parts_dl[0] if len(parts_dl) > 0 else ""
                    dl_para  = parts_dl[1] if len(parts_dl) > 1 else ""
                    dl_src   = parts_dl[2] if len(parts_dl) > 2 else "auto"
                    dlr = await drp_law_tool(dl_abbr, dl_para, dl_src)
                    if dlr.get("ok"):
                        linked = dlr.get("linked_decisions") or []
                        linked_note = (
                            f"\nLinked decisions ({len(linked)}): "
                            + "; ".join(
                                f"{d.get('court','')} {d.get('date','')}".strip()
                                for d in linked[:5]
                            )
                        ) if linked else ""
                        tool_results_text_parts.append(
                            f"━━━ TOOL RESULTS — DRP LAW: {dl_abbr} §{dl_para} ━━━\n"
                            f"Source : {dlr.get('source')}\n"
                            f"Title  : {dlr.get('title','')}\n"
                            f"Text   :\n{dlr.get('text','')}"
                            + linked_note
                        )
                    else:
                        tool_results_text_parts.append(
                            f"━━━ TOOL RESULTS — DRP LAW FAILED ━━━\n{dlr.get('error')}"
                        )
                    tool_calls_used.append({"tool": "drp_law", "abbr": dl_abbr, "paragraph": dl_para, "ok": dlr.get("ok", False)})
                except Exception as exc:
                    tool_results_text_parts.append(f"━━━ TOOL RESULTS — DRP LAW FAILED ━━━\n{exc}")
                    tool_calls_used.append({"tool": "drp_law", "ok": False})

            # NEED_DRP_CASES: <NEED_DRP_CASES: § 823 BGB Schadensersatz>
            # or <NEED_DRP_CASES: Meinungsfreiheit | bverfg | 10>
            if drp_cases_q:
                try:
                    parts_dc = [p.strip() for p in drp_cases_q.split("|")]
                    dc_query = parts_dc[0]
                    dc_src   = parts_dc[1] if len(parts_dc) > 1 else "dejure"
                    dc_limit = int(parts_dc[2]) if len(parts_dc) > 2 and parts_dc[2].isdigit() else 8
                    dcr = await drp_cases_tool(dc_query, dc_src, dc_limit)
                    if dcr.get("ok"):
                        tool_results_text_parts.append(
                            f"━━━ TOOL RESULTS — DRP CASES: «{dc_query}» ━━━\n"
                            f"Source : {dc_src}  |  Total: {dcr.get('total',0)}\n"
                            f"{dcr.get('summary','(keine Ergebnisse)')}"
                        )
                    else:
                        tool_results_text_parts.append(
                            f"━━━ TOOL RESULTS — DRP CASES FAILED ━━━\n{dcr.get('error')}"
                        )
                    tool_calls_used.append({"tool": "drp_cases", "query": dc_query, "ok": dcr.get("ok", False), "total": dcr.get("total", 0)})
                except Exception as exc:
                    tool_results_text_parts.append(f"━━━ TOOL RESULTS — DRP CASES FAILED ━━━\n{exc}")
                    tool_calls_used.append({"tool": "drp_cases", "ok": False})

            # NEED_DRP_SEARCH: <NEED_DRP_SEARCH: fristlose Kündigung>
            # or <NEED_DRP_SEARCH: Datenschutz DSGVO | datenschutzrecht | 6>
            if drp_search_q:
                try:
                    parts_ds = [p.strip() for p in drp_search_q.split("|")]
                    ds_query  = parts_ds[0]
                    ds_gebiet = parts_ds[1] if len(parts_ds) > 1 else ""
                    ds_limit  = int(parts_ds[2]) if len(parts_ds) > 2 and parts_ds[2].isdigit() else 5
                    dsr = await drp_search_tool(ds_query, ds_gebiet, ds_limit)
                    if dsr.get("ok"):
                        dork_hint = (
                            f"\n\nFür tiefere Recherche: {dsr['google_dorking_queries'][0]}"
                            if dsr.get("google_dorking_queries") else ""
                        )
                        tool_results_text_parts.append(
                            f"━━━ TOOL RESULTS — DRP UNIFIED SEARCH: «{ds_query}» ━━━\n"
                            f"Rechtsgebiet: {ds_gebiet or 'alle'}  |  Total: {dsr.get('total',0)}\n"
                            f"{dsr.get('summary','(keine Ergebnisse)')}"
                            + dork_hint
                        )
                    else:
                        tool_results_text_parts.append(
                            f"━━━ TOOL RESULTS — DRP SEARCH FAILED ━━━\n{dsr.get('error')}"
                        )
                    tool_calls_used.append({"tool": "drp_search", "query": ds_query, "rechtsgebiet": ds_gebiet, "ok": dsr.get("ok", False), "total": dsr.get("total", 0)})
                except Exception as exc:
                    tool_results_text_parts.append(f"━━━ TOOL RESULTS — DRP SEARCH FAILED ━━━\n{exc}")
                    tool_calls_used.append({"tool": "drp_search", "ok": False})

            # ── NEED_BUNDLE_START / NEED_STAGE / NEED_BUNDLE_COMMIT ────────────
            if _bundle_start_title or _stage_blocks or _bundle_commit:
                try:
                    from .bundle import (
                        create_bundle, stage_file, commit_bundle,
                    )
                    try:
                        from .kv import db as _rdb_bundle
                        _rdb_bundle_available = True
                    except Exception:
                        _rdb_bundle = None
                        _rdb_bundle_available = False

                    _bnd_session_key = f"adn:daedalus_bundle_active:{session_id or 'default'}"

                    def _get_active_bid():
                        if _rdb_bundle_available and _rdb_bundle is not None:
                            return _rdb_bundle.get(_bnd_session_key)
                        return None

                    def _set_active_bid(bid):
                        if _rdb_bundle_available and _rdb_bundle is not None:
                            _rdb_bundle[_bnd_session_key] = bid

                    def _clear_active_bid():
                        if _rdb_bundle_available and _rdb_bundle is not None:
                            try:
                                del _rdb_bundle[_bnd_session_key]
                            except Exception:
                                pass

                    # NEED_BUNDLE_START — create new bundle for this session
                    if _bundle_start_title:
                        _new_bid = await create_bundle(_bundle_start_title, session_id or "")
                        _set_active_bid(_new_bid)
                        tool_results_text_parts.append(
                            f"━━━ TOOL RESULTS — BUNDLE STARTED 🎁 ━━━\n"
                            f"Bundle ID : {_new_bid}\n"
                            f"Title     : {_bundle_start_title}\n\n"
                            f"Bundle e отворен. Сега емитирай NEED_STAGE блокове за всеки файл.\n"
                            f"Накрая: <NEED_BUNDLE_COMMIT: {_bundle_start_title} | summary>"
                        )
                        tool_calls_used.append({"tool": "bundle_start", "id": _new_bid, "title": _bundle_start_title})

                    # NEED_STAGE — add files to active bundle
                    if _stage_blocks:
                        _active_bid = _get_active_bid()
                        if not _active_bid:
                            _auto_title = "Auto-bundle"
                            _active_bid = await create_bundle(_auto_title, session_id or "")
                            _set_active_bid(_active_bid)
                            tool_results_text_parts.append(
                                f"━━━ AUTO BUNDLE CREATED ━━━\n"
                                f"Bundle {_active_bid} auto-created (no active bundle found)."
                            )
                        _staged_summary = []
                        for _sb in _stage_blocks:
                            _sr = await stage_file(
                                bundle_id=_active_bid,
                                file_path=_sb["file_path"],
                                action=_sb["action"],
                                reason=_sb["reason"],
                                new_content=_sb["content"],
                            )
                            if _sr.get("ok"):
                                _staged_summary.append(
                                    f"  ✅ {_sb['action'].upper()} {_sb['file_path']} "
                                    f"(+{_sr.get('lines_new',0)} lines)"
                                )
                            else:
                                _staged_summary.append(f"  ❌ {_sb['file_path']}: {_sr.get('error','unknown error')}")
                        tool_results_text_parts.append(
                            f"━━━ TOOL RESULTS — FILES STAGED 📁 ━━━\n"
                            f"Bundle ID : {_active_bid}\n"
                            f"Staged files ({len(_stage_blocks)}):\n"
                            + "\n".join(_staged_summary)
                            + f"\n\nФайловете са в staging — НЕ са приложени към проекта.\n"
                            f"Продължи с повече NEED_STAGE или финализирай с NEED_BUNDLE_COMMIT."
                        )
                        tool_calls_used.append({
                            "tool": "bundle_stage",
                            "bundle_id": _active_bid,
                            "files": len(_stage_blocks),
                        })

                    # NEED_BUNDLE_COMMIT — finalize bundle, notify owner
                    if _bundle_commit:
                        _active_bid = _get_active_bid()
                        if not _active_bid:
                            tool_results_text_parts.append(
                                f"━━━ TOOL RESULTS — BUNDLE COMMIT FAILED ━━━\n"
                                f"Няма активен bundle за тази сесия.\n"
                                f"Емитирай първо NEED_BUNDLE_START или NEED_STAGE."
                            )
                        else:
                            _cr = await commit_bundle(
                                bundle_id=_active_bid,
                                title=_bundle_commit["title"],
                                summary=_bundle_commit["summary"],
                            )
                            _clear_active_bid()
                            if _cr.get("ok"):
                                _flist = "\n".join(
                                    f"  • {f['file_path']} [{f['action']}]"
                                    for f in _cr.get("files", [])
                                )
                                tool_results_text_parts.append(
                                    f"━━━ TOOL RESULTS — BUNDLE COMMITTED ✅ ━━━\n"
                                    f"Bundle ID  : {_active_bid}\n"
                                    f"Title      : {_cr['title']}\n"
                                    f"Files      : {_cr['files_count']}\n"
                                    f"{_flist}\n\n"
                                    f"Bundle е готов за преглед от Николай → /admin/daedalus/bundles\n"
                                    f"Той може да приложи всичко с един клик или да го отхвърли."
                                )
                                tool_calls_used.append({
                                    "tool": "bundle_commit",
                                    "bundle_id": _active_bid,
                                    "files": _cr["files_count"],
                                })
                            else:
                                tool_results_text_parts.append(
                                    f"━━━ TOOL RESULTS — BUNDLE COMMIT FAILED ━━━\n"
                                    f"{_cr.get('error','unknown error')}"
                                )
                except Exception as _bnd_exc:
                    log.warning("Bundle handler failed: %s", _bnd_exc)
                    tool_results_text_parts.append(
                        f"━━━ BUNDLE SYSTEM ERROR ━━━\n{_bnd_exc}\n"
                        f"Пробвай с NEED_PROPOSE_START за единичен файл."
                    )

            # ── NEED_WORKER: parallel stateless sub-agents ─────────────────────
            if worker_calls:
                try:
                    worker_results = await call_workers_parallel(worker_calls)
                    tool_results_text_parts.append(
                        f"━━━ WORKER RESULTS ({len(worker_results)} агент{'а' if len(worker_results)>1 else ''}) ━━━\n"
                        + format_worker_results(worker_results)
                    )
                    tool_calls_used.append({
                        "tool": "workers",
                        "count": len(worker_calls),
                        "slots": [c["slot"] for c in worker_calls],
                        "ok": all(r.get("ok") for r in worker_results),
                    })
                except Exception as exc:
                    log.warning("NEED_WORKER parallel call failed: %s", exc)
                    tool_results_text_parts.append(f"━━━ WORKER RESULTS — FAILED ━━━\n{exc}")

            if subagent_calls:
                try:
                    from .subagents import format_subagent_results, run_subagents_parallel
                    sub_results = await run_subagents_parallel(
                        subagent_calls,
                        memory=self.memory,
                        session_id=session_id or "",
                        read_file_fn=_read_file_tool,
                    )
                    tool_results_text_parts.append(
                        "━━━ SUBAGENT RESULTS ━━━\n" + format_subagent_results(sub_results)
                    )
                    tool_calls_used.append({
                        "tool": "subagent",
                        "count": len(subagent_calls),
                        "ok": all(r.get("ok") for r in sub_results),
                        "roles": [str(r.get("role") or "coder") for r in sub_results],
                    })
                except Exception as exc:
                    log.warning("NEED_SUBAGENT failed: %s", exc)
                    tool_results_text_parts.append(f"━━━ SUBAGENT RESULTS — FAILED ━━━\n{exc}")

            tool_block = "\n\n".join(tool_results_text_parts)

            # ── Repeated-failure breaker (improvements.ErrorTracker) ──────────
            # Hash the failed sections of tool_block. If the same failure
            # fingerprint appears in 2 consecutive iterations → break the loop
            # instead of letting Дедал repeat the same broken query 4+ times.
            try:
                _err_tracker = get_error_tracker()
                # Track ALL tool calls (success or fail) for budget enforcement
                for _tc in tool_calls_used[_calls_at_iter:]:
                    _err_tracker.track_call(_breaker_key, _tc.get("tool", "unknown"))
                _failed_now: list[tuple[str, str]] = []
                for _tc in tool_calls_used[_calls_at_iter:]:
                    if _tc.get("ok") is False:
                        _tool_name = _tc.get("tool", "unknown")
                        # Extract first FAILED line from tool_block as error sample
                        _err_sample = ""
                        for _ln in tool_block.split("\n"):
                            if "FAILED" in _ln or "Error" in _ln or "error" in _ln:
                                _err_sample = _ln[:200]
                                break
                        _failed_now.append((_tool_name, _err_sample))
                        # Persistent cross-session lesson promotion
                        try:
                            record_tool_error_for_promotion(
                                self.memory, _tool_name, _err_sample,
                                query=str(_tc.get("query", ""))[:200],
                            )
                        except Exception:
                            pass
                _should_break_repeat = False
                _repeat_tool = ""
                for _tn, _es in _failed_now:
                    if _err_tracker.should_break(_breaker_key, _tn, _es, threshold=2):
                        _should_break_repeat = True
                        _repeat_tool = _tn
                        break
                # Per-tool budget: max 4 NEED_PG calls per session
                if _err_tracker.tool_call_count(_breaker_key, "postgres_query") > 4:
                    _should_break_repeat = True
                    _repeat_tool = "postgres_query (budget exceeded)"
                if _should_break_repeat:
                    log.warning("repeated-error breaker: session=%s tool=%s — breaking loop",
                                session_id, _repeat_tool)
                    tool_block += (
                        f"\n\n━━━ BREAKER ENGAGED ━━━\n"
                        f"Същата грешка от [{_repeat_tool}] се появи повторно. "
                        f"Спирам опитите. Премини към NEED_BLOCKED или различен подход — "
                        f"НЕ повтаряй същия маркер."
                    )
                    # Force-exit the tool reroll loop so we don't keep burning iterations. (It appended to an
                    # undefined `messages` first: the NameError was swallowed below and the breaker never broke.)
                    raw = (
                        f"Спирам да опитвам — същата грешка от [{_repeat_tool}] "
                        f"се повтори. Премини към друг подход или ми кажи конкретно "
                        f"какво искаш да направя."
                    )
                    break
            except Exception as _bk_exc:
                log.debug("breaker check failed (non-fatal): %s", _bk_exc)

            # ── Fix 3: Action-forcing detector ────────────────────────────────
            # If Дедал expressed intent ("ще подготвя", "сега ще" etc.) without
            # emitting ANY action marker → inject a forcing nudge so the next
            # re-roll actually DOES something instead of describing what it will do.
            _INTENT_PHRASES = (
                "ще подготвя", "сега ще", "следващата стъпка", "ще създам",
                "ще генерирам", "ще вмъкна", "ще добавя", "next i'll", "i will now",
                "now i'll", "i'll now", "let me now", "i'll prepare", "i'll generate",
                "i'll create", "i'll insert", "i'll add", "i'll write",
                "preparing", "will prepare", "will generate", "will create",
            )
            _ACTION_MARKERS = (
                "NEED_DATA_PROPOSAL", "NEED_PG", "NEED_PROPOSE_START",
                "NEED_INBOX_MSG", "NEED_BLOCKED", "NEED_WORKER",
                "NEED_STAGE", "NEED_BUNDLE_START", "NEED_BUNDLE_COMMIT",
            )
            _raw_lower = (raw or "").lower()
            _has_intent  = any(p in _raw_lower for p in _INTENT_PHRASES)
            _has_action  = any(m in (raw or "") for m in _ACTION_MARKERS)
            _force_action_nudge = _has_intent and not _has_action

            # ── Cut-off recovery: truncated NEED_DATA_PROPOSAL ────────────────
            # If the previous response opened a NEED_DATA_PROPOSAL but the JSON
            # was cut off by max_tokens, instruct Дедал to split into smaller
            # batches. This takes priority over other followup tails.
            _is_final_iter = (_tool_iter == MAX_TOOL_ITERATIONS - 1)
            if _data_prop_truncated and not _is_final_iter:
                _cutoff_notice = (
                    "━━━ CUT-OFF DETECTED ━━━\n"
                    "Твоето NEED_DATA_PROPOSAL беше прекъснато на средата на JSON-а "
                    "(max_tokens лимит). Никакъв запис не е извършен.\n\n"
                    "ДЕЙСТВАЙ СЕГА — раздели на ПО-МАЛКИ batches:\n"
                    "  • Максимум 3 концепта/criteria в ЕДИН marker\n"
                    "  • Емитирай НЯКОЛКО NEED_DATA_PROPOSAL marker-а в един отговор\n"
                    "  • Всеки batch е независим — ако един фейлне, другите минават\n\n"
                    "Пример (3 batch-а в един отговор):\n"
                    "  <NEED_DATA_PROPOSAL: trading_concepts | Batch 1/3 | [{...},{...},{...}]>\n"
                    "  <NEED_DATA_PROPOSAL: trading_concepts | Batch 2/3 | [{...},{...},{...}]>\n"
                    "  <NEED_DATA_PROPOSAL: trading_concepts | Batch 3/3 | [{...},{...}]>\n\n"
                    "НЕ описвай отново структурата — направо емитирай marker-ите."
                )
                log.info("data_proposal cut-off detected at iter=%d, injecting split prompt", _tool_iter)
            # ── Fix 2: Final-iter prompt ───────────────────────────────────────
            if _is_final_iter:
                _followup_tail = (
                    "⚠️  ФИНАЛНА ИТЕРАЦИЯ — НЕ ОЩЕ ЕДИН ROUND OF TOOLS.\n"
                    "Трябва да ДЕЙСТВАШ сега — избери ЕДНО от следните:\n\n"
                    "• За trading данни (concepts/criteria/patterns): емитирай\n"
                    "  <NEED_DATA_PROPOSAL: category | Title | [json_array]>\n"
                    "  Може да емитираш МНОГО такива маркера в един отговор.\n\n"
                    "• За код: емитирай <NEED_PROPOSE_START> ... </NEED_PROPOSE_START>\n\n"
                    "• За блокер: емитирай <NEED_BLOCKED: причина | опитано | следващи стъпки>\n\n"
                    "• Ако вече свърши: пиши финалния отговор БЕЗ маркери.\n\n"
                    "ЗАБРАНЕНО: описания на това, което 'ще направиш'. Само действие."
                )
            elif _force_action_nudge:
                # Non-final iter but detected "intent without action" pattern.
                _followup_tail = (
                    "⚠️  ЗАСЕЧЕН INTENT БЕЗ ДЕЙСТВИЕ — спри да описваш, ДЕЙСТВАЙ.\n"
                    "Написа какво 'ще направиш', но не емитира никакъв маркер.\n\n"
                    "Емитирай СЕГА (в същия отговор — много маркера са ОК):\n"
                    "  <NEED_DATA_PROPOSAL: category | Title | [json_array]>  — за trading данни\n"
                    "  <NEED_PG: INSERT INTO ...>                              — за директен SQL (ако perms granted)\n"
                    "  <NEED_PROPOSE_START> ... </NEED_PROPOSE_START>          — за код\n"
                    "  <NEED_CHECK_PERM: insert_trading_concepts>              — за проверка на права\n\n"
                    "Оставащи итерации: "
                    f"{MAX_TOOL_ITERATIONS - _tool_iter - 1}. "
                    "Използвай ги за ДЕЙСТВИЕ, не за повече reconnaissance.\n\n"
                    "DATA CURATION: <NEED_DATA_PROPOSAL: trading_concepts | SMC Batch 1 | [{...},{...}]>\n"
                    "Можеш да пратиш всички концепти в ЕДИН маркер (json array). "
                    "Report progress: <NEED_PROGRESS: task | pct | msg>"
                )
                log.debug("action-forcing nudge injected at iter=%d", _tool_iter)
            else:
                _followup_tail = (
                    "You MAY emit more NEED_* markers (NEED_READ_FILE, NEED_CODE_SEARCH, "
                    "NEED_PG, NEED_DB_SCHEMA, NEED_PROPOSE_START, NEED_DATA_PROPOSAL, "
                    "NEED_PROGRESS, NEED_CHECK_PERM, NEED_WORKER etc.) if you need more "
                    "info or to write code now that you have it. When you have everything "
                    "needed to fully complete the task, reply with your final answer "
                    "WITHOUT any more markers.\n\n"
                    "PROJECT MODE: If the task is too big for this loop (10+ DB records, "
                "multi-domain analysis, multi-module refactor), emit:\n"
                "  <NEED_PROJECT_PLAN: detailed goal description>\n"
                "Дедал ще разбие целта на стъпки, всяка ще се изпълни в собствена сесия "
                "с пълни 20 итерации. Не се опитвай да побереш всичко в текущия loop.\n\n"
                "WORKER DELEGATION: To parallelize work, emit one or more NEED_WORKER markers:\n"
                    "  <NEED_WORKER: fast | task description>   — cheap/fast model (Haiku/Groq)\n"
                    "  <NEED_WORKER: balanced | task>           — same model as master\n"
                    "  <NEED_WORKER: deep | task>               — powerful model (Opus)\n"
                    "Multiple NEED_WORKER markers in one response run in parallel.\n"
                    "For a bounded file slice WITH tools, emit:\n"
                    "  <NEED_SUBAGENT: slug | task>\n"
                    "  <NEED_SUBAGENT: slug | task | explorer>  — read-only map/verify.\n"
                    "Explorer cannot write. Pass one slice, not the whole chat.\n"
                    "Workers are stateless — pass full context in the task description. "
                    "Wait for their results in the next tool iteration before synthesizing.\n\n"
                    "DATA CURATION REMINDER: For inserting trading_concepts / signal_criteria / "
                    "asset_behavior_patterns into the database, ALWAYS use NEED_DATA_PROPOSAL "
                    "instead of NEED_PROPOSE_START (code sandbox). "
                    "Format: <NEED_DATA_PROPOSAL: category | Title | [json_array]>\n"
                    "First check permission with NEED_CHECK_PERM: insert_<category> — "
                    "if GRANTED you can insert directly with NEED_PG; if DENIED use NEED_DATA_PROPOSAL. "
                    "Report progress with NEED_PROGRESS: task_name | pct | message (pct 0.0-1.0).\n\n"
                    f"Remaining tool iterations: {MAX_TOOL_ITERATIONS - _tool_iter - 1}. "
                    "Reconnaissance should be DONE by now — move to ACTION (emit markers)."
                )
            _cutoff_prefix = (
                f"{_cutoff_notice}\n\n" if _data_prop_truncated and not _is_final_iter else ""
            )
            followup = (
                _cutoff_prefix
                + f"━━━ TOOL RESULTS (your request) ━━━\n{tool_block}\n\n"
                + _followup_tail
            )
            # When a screenshot was taken, promote the followup to a content
            # array so the vision image is delivered alongside the text.
            # The Anthropic-compatible API accepts list[content_block] or str.
            if vision_blocks:
                followup_content: object = [{"type": "text", "text": followup}] + vision_blocks
            else:
                followup_content = followup
            try:
                # Iterations 2+ skip the heavy user_content (memory dump already seen).
                # Only the assistant's prior reply + tool results are needed.
                # Also skip prompt caching on re-roll calls — system prompt is already
                # cached from the first call in this 5-min window.
                if _tool_iter == 0:
                    reroll_messages = [
                        {"role": "user",      "content": user_content},
                        {"role": "assistant", "content": raw},
                        {"role": "user",      "content": followup_content},
                    ]
                else:
                    # Compact: only the previous assistant turn + new tool results.
                    # Saves ~60-80% of input tokens on chained tool calls.
                    reroll_messages = [
                        {"role": "user",      "content": "(context already provided in prior turns)"},
                        {"role": "assistant", "content": raw},
                        {"role": "user",      "content": followup_content},
                    ]
                # Live status — при LLM re-call показваме "generating" с
                # keepalive heartbeat за да не стои UI замръзнал докато Claude
                # мисли (30-60s между tool iteration-и)
                try:
                    async with _keepalive(
                        session_id, "generating", _tool_iter,
                        f"Генерирам следващ отговор… (итер. {_tool_iter + 1}/{MAX_TOOL_ITERATIONS})",
                    ):
                        raw = await _call_claude(_call_persona, reroll_messages,
                                                 use_cache_override=False)
                except NameError:
                    raw = await _call_claude(_call_persona, reroll_messages,
                                             use_cache_override=False)
                # Phase 16 — auto-pause check (token budget OR owner pause flag)
                try:
                    _extra = _journal.estimate_tokens(raw) + _journal.estimate_tokens(followup)
                    _do_pause, _pr = _journal.should_pause(session_id or "", _extra)
                    if _do_pause:
                        _journal.write_step(
                            session_id or "",
                            "auto_pause",
                            f"Paused след итер. {_tool_iter + 1}/{MAX_TOOL_ITERATIONS} "
                            f"({_pr}). Дедал ще resume-не при 'продължи'.",
                            data=(raw or "")[:4000],
                        )
                        _journal.mark_paused(session_id or "", _pr)
                        log.info("journal: auto-pause session=%s reason=%s iter=%d",
                                 (session_id or "")[:10], _pr, _tool_iter + 1)
                        _stripped_pause = re.sub(r"<NEED_[A-Z_]+(?:[^>]*)>", "", raw or "").strip()
                        raw = (
                            f"{_stripped_pause}\n\n"
                            f"⏸ ПАУЗА — стигнах ~{int(_journal.PAUSE_THRESHOLD*100)}% от token budget "
                            f"({_pr}). Запазих прогреса в journal-а (стъпки: "
                            f"{len((_journal.get_journal(session_id or '') or {}).get('steps', []))}). "
                            f"Напиши 'продължи' за да възстановя от където спрях."
                        ).strip()
                        _tool_loop_broke_with_fallback = True
                        break
                except Exception as _pe:
                    log.debug("auto-pause check skipped: %s", _pe)
            except Exception as exc:
                log.warning("tool re-roll failed: %s", exc)
                # Strip any leaked marker(s) so the owner never sees them, and
                # provide a deterministic fallback. For JSON-contract calls
                # (generate) we emit a valid minimal JSON envelope.
                # Strip ALL NEED_* markers (inline + block) so none leak to user-facing text.
                stripped = re.sub(r"<NEED_[A-Z_]+(?:[^>]*)>", "", raw or "")
                stripped = re.sub(
                    r"<NEED_(?:PROPOSE_START|SANDBOX)>.*?</NEED_(?:PROPOSE_START|SANDBOX)>",
                    "", stripped, flags=re.DOTALL,
                ).strip()
                tool_summary = "; ".join(
                    f"{c['tool']}({c['query']!r}): {c['result_count']} results"
                    for c in tool_calls_used
                )
                if expect_json:
                    raw = json.dumps({
                        "language": "text",
                        "filename": "fallback.txt",
                        "requirements": [],
                        "install_command": "",
                        "run_command": "",
                        "code": "",
                        "explanation": (
                            f"Tool re-roll failed ({exc}). Tools attempted: {tool_summary}. "
                            "Owner — please retry the request."
                        ),
                        "key_decisions": [],
                        "usage_example": "",
                        "warnings": ["LLM re-roll failure after tool call"],
                        "memory_notes": "tool re-roll failed; do not store as success"
                    })
                else:
                    raw = (
                        f"{stripped}\n\n"
                        f"(I needed to consult external sources [{tool_summary}] but the "
                        f"follow-up call failed: {exc}. Please retry.)"
                    ).strip()
                _tool_loop_broke_with_fallback = True
                break

            # After successful re-roll: check for new NEED_PROPOSE_START in new raw
            # so chained "read file → write code" works in a single turn.
            propose_result_text = ""  # consumed; reset for next iteration
            _new_pb = _extract_propose_block(raw)
            if _new_pb:
                propose_result_text, raw = await _run_propose_loop(
                    initial_raw=raw,
                    initial_block=_new_pb,
                    user_content=user_content,
                    persona=self.persona,
                    max_retries=_propose_max_retries(),
                )
                tool_calls_used.append({"tool": "propose_patch", "file": _new_pb["file_path"]})

        # ── Empty reply guard (T05) — Haiku wrap-up if loop consumed everything
        # When Дедал's last response was 100% tool markers (now stripped to empty)
        # we'd return a blank reply. Call Haiku with the tool results to produce
        # a brief synthesized answer instead.
        _raw_text_check = re.sub(r"<NEED_[A-Z_]+[^>]*>", "", raw or "")
        _raw_text_check = re.sub(
            r"<NEED_(?:PROPOSE_START|SANDBOX)>.*?</NEED_(?:PROPOSE_START|SANDBOX)>",
            "", _raw_text_check, flags=re.DOTALL,
        ).strip()
        if not _raw_text_check:
            try:
                log.info("empty-reply guard: session=%s tools=%d — Haiku wrap-up",
                         session_id, len(tool_calls_used))
                _tool_summary = "\n".join(
                    f"  • {c.get('tool')} → {'OK' if c.get('ok', True) else 'FAILED'}"
                    f"  {str(c.get('query',''))[:80]}"
                    for c in tool_calls_used[-8:]
                )
                _wrap_system = (
                    "You are Daedalus wrap-up assistant. The main agent used "
                    "tools but ran out of iterations without producing a final "
                    "reply. Write ONE concise Bulgarian-language paragraph (3-6 "
                    "изречения) обобщаващ какво е свършено и какво остава. "
                    "БЕЗ markers, БЕЗ apologies."
                )
                _wrap_user = (
                    f"Owner asked: {user_message[:400]}\n\n"
                    f"Tool calls executed:\n{_tool_summary}\n\n"
                    f"Last partial output:\n{(raw or '')[-1000:]}\n\n"
                    f"Дай кратко резюме за owner-а."
                )
                from . import daedalus_mesh as dm
                raw = ""
                if dm.use_mesh_router():
                    try:
                        _wrap_res = await dm.complete(
                            system=_wrap_system,
                            user=_wrap_user,
                            intent="cheap",
                            max_tokens=600,
                            client_id=dm.client_id("chat", "wrap-up"),
                            use_cache=False,
                        )
                        raw = dm.extract_text(_wrap_res)
                        if raw:
                            log.info(
                                "empty-reply guard: mesh_router produced %d chars ($%.6f)",
                                len(raw), float(_wrap_res.get("cost_usd") or 0.0),
                            )
                    except Exception as _wrm_exc:
                        log.debug("empty-reply guard mesh_router failed: %s", _wrm_exc)
                from .harness import gateway as _gw2
                if not raw and _gw2.path_enabled("fastpath"):
                    _wroute = _gw2.cheap_route()
                    if _wroute:
                        _wr = await _gw2.call(
                            "summary", [{"role": "user", "content": _wrap_user}], system=_wrap_system,
                            max_tokens=600, route=_wroute, client_id="daedalus/chat-wrap-up",
                        )
                        if _wr.ok:
                            raw = _wr.text
                            log.info("empty-reply guard: cheap-llm (%s) produced %d chars", _wr.model, len(raw))
                elif not raw:
                    from openai import AsyncOpenAI
                    from .cheap_llm import resolve_cheap_openai
                    _cheap = resolve_cheap_openai()
                    if _cheap:
                        _hc = AsyncOpenAI(
                            api_key=_cheap["api_key"],
                            base_url=_cheap["base_url"],
                            max_retries=1,
                        )
                        _hr = await _hc.chat.completions.create(
                            model=_cheap["model"],
                            max_tokens=600,
                            messages=[
                                {"role": "system", "content": _wrap_system},
                                {"role": "user", "content": _wrap_user},
                            ],
                        )
                        raw = _hr.choices[0].message.content or ""
                        log.info("empty-reply guard: cheap-llm (%s) produced %d chars", _cheap["model"], len(raw))
                if not (raw or "").strip():
                    # Haiku creds missing OR Haiku returned empty → deterministic fallback
                    raw = (
                        "Изпълних инструментите, но не успях да синтезирам финален "
                        f"отговор. Tools: {', '.join(c.get('tool','?') for c in tool_calls_used[-5:])}. "
                        "Моля, попитай ме отново или ми кажи какво конкретно искаш."
                    )
                    log.info("empty-reply guard: deterministic fallback used")
            except Exception as _wg_exc:
                log.warning("empty-reply guard Haiku failed: %s", _wg_exc)
                raw = (
                    "Изпълних инструментите, но не успях да синтезирам финален отговор. "
                    f"Tools: {', '.join(c.get('tool','?') for c in tool_calls_used[-5:])}. "
                    "Моля, попитай ме отново или ми кажи какво конкретно искаш да направя."
                )

        # Capture both sides — base ChromaDB + Mem0 episodic/working layer
        self.memory.capture_turn(session_id, "user", user_message)
        self.memory.capture_turn(session_id, "assistant", raw, metadata={"format": "json" if expect_json else "text"})
        self.mem0.capture(session_id, "user", user_message)
        self.mem0.capture(session_id, "assistant", raw)

        # Изчистваме live статуса след приключване
        try:
            _clear_live(session_id)
        except Exception:
            pass

        # Fire-and-forget reflection. We MUST keep a strong reference to the
        # task or the event loop's GC may drop it mid-flight.
        # SELF-IMPROVEMENT GUARD: reflection auto-writes lessons from the
        # assistant output. For for_self sessions that would let Daedalus
        # quietly adopt opinions about himself without owner approval — exactly
        # the bypass channel the discovery approval flow exists to close.
        # So we skip reflection entirely on for_self turns.
        # Opt-in (DAEDALUS_REFLECTION=1): it is one more paid model call per turn and writes lessons to memory. It
        # never actually ran before 2026-11-12 (a local "import asyncio" broke it); turning it on is the owner's call.
        _reflect = (os.environ.get("DAEDALUS_REFLECTION") or "").strip().lower() in ("1", "true", "yes", "on")
        if for_self:
            log.info("session=%s reflection skipped (for_self=True; owner approval required)", session_id)
        elif not _reflect:
            pass
        else:
            try:
                t = asyncio.create_task(run_reflection_cycle(
                    _call_claude, self.memory, session_id, user_message, raw,
                ))
                self._background_tasks.add(t)
                t.add_done_callback(self._background_tasks.discard)
            except Exception as exc:
                log.debug("reflection scheduling skipped: %s", exc)

        # ── Clean output: strip any leaked NEED_* markers from the final reply ──
        # Markers are execution instructions for the tool loop — the owner should
        # never see them in the chat. Strip both inline <NEED_FOO: ...> markers
        # and block <NEED_PROPOSE_START>...</NEED_PROPOSE_START> blocks.
        if raw:
            raw = re.sub(r"<NEED_[A-Z_]+[^>]*>", "", raw)
            raw = re.sub(
                r"<NEED_(?:PROPOSE_START|SANDBOX)>.*?</NEED_(?:PROPOSE_START|SANDBOX)>",
                "", raw, flags=re.DOTALL,
            )
            # A tool call written as text never ran (live 2026-11-12: "<tool_call name=playtest>" reached the owner).
            raw = strip_text_tool_calls(raw)

        # ── Phase 13: post-reply gates (review + question budget + outcome) ──
        try:
            _elapsed_total = _time_mod.time() - started_at
        except Exception:
            _elapsed_total = 0.0
        # Read-first warning baked into reply for owner visibility (non-blocking)
        try:
            _rf_hint = needs_read_first_hint(session_id, raw or "")
            if _rf_hint and raw:
                log.info("read-first hint emitted for session=%s", session_id[:10])
        except Exception:
            pass
        # Review-gate self-critique (only on TRULY substantial sessions to avoid
        # adding pressure to the Anthropic quota). Trigger when:
        #  - real tool work happened (>=3 calls OR any failures), AND
        #  - elapsed >= 20s (signals genuine engagement, not chat)
        # Skip if Anthropic was rate-limited during this session (no extra calls).
        _had_failures = any(c.get("ok") is False for c in (tool_calls_used or []))
        _rate_limited = "[fallback:" in (raw or "")
        try:
            _should_review = (
                not for_self
                and not expect_json
                and not _rate_limited
                and len((raw or "").strip()) > 80
                and (len(tool_calls_used) >= 3 or _had_failures)
                and _elapsed_total >= 20
            )
            if _should_review:
                _crit = await review_gate_critique(
                    user_message, raw or "", tool_calls_used, _elapsed_total,
                )
                if not _crit.get("ok", True) and _crit.get("issue"):
                    log.warning("review-gate flagged: %s", _crit.get("issue", "")[:200])
                    # Shown in the Studio's activity panel, not glued onto the owner's answer (it was internal jargon,
                    # in Bulgarian whatever the owner's language, at the end of every flagged reply).
                    _rg_rid = ""
                    try:
                        from .harness import runs as _rg_runs
                        _rg_rid = _rg_runs.current_run_id()
                        if _rg_rid:
                            _rg_runs.safe_event(_rg_rid, "self_review", {
                                "issue": str(_crit.get("issue") or "")[:300],
                                "fix": str(_crit.get("suggested_fix") or "")[:200]})
                    except Exception:
                        _rg_rid = ""
                    if not _rg_rid:      # no Studio run to show it in (blocking API): keep it with the answer
                        raw = (raw or "") + (
                            f"\n\n— Self-check: {_crit.get('issue', '')[:300]}"
                            + (f" Suggested fix: {_crit.get('suggested_fix', '')[:200]}"
                               if _crit.get("suggested_fix") else ""))
        except Exception as _rg_exc:
            log.debug("review-gate skipped: %s", _rg_exc)
        # Question budget enforcement (max 1 clarifying question per session)
        try:
            allowed, advice = question_budget_check_and_increment(session_id, raw or "")
            if not allowed and advice:
                raw = advice + "\n\n" + (raw or "")
        except Exception:
            pass
        # ── Action-forcing detector: caught "intent without action" replies ──
        # Дедал каза "ще извлека / ще проверя / ще добавя ..." но не emit-на
        # никакъв NEED_* маркер и не пусна tool. Лог + visible nudge към него
        # в conversation history (без extra LLM call, за да не натоварваме
        # rate-limit). Outcome автоматично се записва като 'partial' min.
        _intent_without_action = False
        try:
            _ifa = detect_intent_without_action(raw or "", tool_calls_used)
            if _ifa.get("detected"):
                _intent_without_action = True
                _phrase = _ifa.get("phrase", "")
                log.warning(
                    "action-forcing: intent without action detected (session=%s phrase=%r)",
                    session_id[:10], _phrase,
                )
                raw = (raw or "") + (
                    "\n\n⚠️ ВНИМАНИЕ (self-check): казах "
                    f"„{_phrase}…“ но не emit-нах NEED_* маркер и не направих "
                    "нищо конкретно. При следваща моя ход в тази сесия "
                    "трябва или да emit-на маркер за реалното действие, "
                    "или директно да изпълня задачата с конкретен код/SQL."
                )
        except Exception:
            pass
        # Record session outcome to Replit DB (cheap, non-blocking)
        try:
            record_outcome(
                session_id,
                user_message=user_message,
                reply=raw or "",
                elapsed=_elapsed_total,
                tool_calls=tool_calls_used,
                tool_iterations=_tool_iter + 1 if tool_calls_used else 0,
                intent=(briefing_dict or {}).get("intent", "") if briefing_dict else "",
                intent_without_action=_intent_without_action,
            )
        except Exception:
            pass

        # Clarification Tracker — засича когато сме поискали уточнение
        # и логва в DB за дневния стил-урок
        try:
            from .clarification_tracker import detect_clarifying_question, log_clarification
            _clr_qs = detect_clarifying_question(raw or "")
            if _clr_qs:
                # (a local "import asyncio" here made asyncio local to this whole function, so the digest task near
                # its top always raised UnboundLocalError, swallowed by its except: found 2026-11-12)
                asyncio.create_task(log_clarification(
                    session_id=session_id,
                    user_message=user_message,
                    agent_reply=raw or "",
                    questions=_clr_qs,
                ))
        except Exception:
            pass

        summary = {
            "knowledge_used": len(knowledge),
            "past_memory_used": len(past_memory),
            "similar_artifacts": len(artifacts),
            "session_turns_in_context": len(session_recent),
            "lessons_used": len(lessons),
            "episodes_used": len(episodes),
            "mesh_aware": bool(mesh_block),
            "tool_calls": tool_calls_used,
            "tool_iterations": _tool_iter + 1 if tool_calls_used else 0,
            "mem0": self.mem0.stats(),
            "navigator": briefing_dict,
            "build_kind": str(getattr(_loop, "kind", "") or "") if _loop is not None else "",
            "browser_ok": _loop.browser_ok_for_dod() if _loop is not None else None,
        }
        return raw, summary

    # ------------------------------------------------------------------
    # External-world tools (direct callable, also used by mesh actions)
    # ------------------------------------------------------------------

    async def web_search(self, query: str, limit: int = 5, fetch_content: bool = False,
                         for_self: bool = False) -> dict[str, Any]:
        return await web_search_tool(self.memory, query, limit=limit,
                                     fetch_content=fetch_content, for_self=for_self)

    async def github_search(self, query: str, language: str | None = None,
                            limit: int = 5, min_stars: int = 0,
                            for_self: bool = False) -> dict[str, Any]:
        return await github_search_tool(self.memory, query, language=language,
                                        limit=limit, min_stars=min_stars,
                                        for_self=for_self)

    def list_discoveries(self, status: str | None = None, limit: int = 100) -> dict[str, Any]:
        items = self.memory.list_discoveries(status=status, limit=limit)
        return {"count": len(items), "status_filter": status, "discoveries": items}

    def approve_discovery(self, discovery_id: str) -> dict[str, Any]:
        ok = self.memory.set_discovery_status(discovery_id, "approved")
        if ok:
            # Promote to lesson on approval
            res = self.memory.discoveries_col.get(ids=[discovery_id])
            if res.get("ids"):
                meta = (res.get("metadatas") or [{}])[0] or {}
                title = meta.get("title", "")
                url = meta.get("url", "")
                kind = meta.get("kind", "discovery")
                try:
                    self.memory.capture_lesson(
                        lesson=f"Owner-approved {kind}: {title} ({url}).",
                        kind="discovery",
                        confidence=0.95,
                    )
                except Exception as exc:
                    log.debug("approve→lesson failed: %s", exc)
                # R5 (retired 2026-11-09): approving no longer schedules a plan through the old ADN mesh registry.
                # A plan is made on request (Review → Discoveries → Regenerate plan), so plan_id stays None.
        return {"discovery_id": discovery_id, "approved": ok, "plan_id": None}

    def reject_discovery(self, discovery_id: str) -> dict[str, Any]:
        ok = self.memory.set_discovery_status(discovery_id, "rejected")
        return {"discovery_id": discovery_id, "rejected": ok}

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Fix 4 — Phase Resume (session continuity for long multi-step tasks)
    # ------------------------------------------------------------------
    # When Дедал works on a long task across multiple sessions (e.g. Trading
    # Knowledge Curator), he may finish one session mid-task. The next session
    # has no memory of where things stopped.
    #
    # Phase Resume saves the last NEED_PROGRESS checkpoint to Replit DB under
    # "adn:phase_resume:{session_id}". On next chat() call for the same
    # session_id, this context is prepended so Дедал knows exactly where to
    # continue WITHOUT repeating reconnaissance.
    #
    # Triggered by: NEED_PROGRESS markers emitted during the tool loop.
    # Storage: Replit DB (same pattern as outbox.py / permissions).
    # TTL: 72 hours (stale resume context is automatically discarded).

    _PHASE_KEY_PREFIX = "adn:phase_resume:"
    _PHASE_TTL_SECONDS = 72 * 3600  # 72 hours

    @staticmethod
    def _phase_save(session_id: str, pct: float, task_name: str, msg: str) -> None:
        """Save phase resume context for this session. A finished task (100%) clears it: a resume note on every later
        message for 72 h would steer unrelated requests back to it (review 2026-11-12)."""
        if pct >= 0.999:
            Daedalus._phase_clear(session_id)
            return
        try:
            from .kv import db  # type: ignore
            import time as _time
            payload = json.dumps({
                "session_id": session_id,
                "task_name":  task_name,
                "pct":        pct,
                "msg":        msg,
                "saved_at":   datetime.now(timezone.utc).isoformat(),
                "expires_at": _time.time() + Daedalus._PHASE_TTL_SECONDS,
            })
            db[f"{Daedalus._PHASE_KEY_PREFIX}{session_id}"] = payload
            log.debug("phase_resume saved: session=%s pct=%.0f%% %s", session_id, pct * 100, task_name)
        except Exception as exc:
            log.debug("phase_save failed (non-fatal): %s", exc)

    @staticmethod
    def _phase_load(session_id: str) -> dict | None:
        """Load saved phase resume context from Replit DB. Returns None if not found or expired."""
        try:
            import time as _time
            from .kv import db  # type: ignore
            raw = db.get(f"{Daedalus._PHASE_KEY_PREFIX}{session_id}")
            if raw is None:
                return None
            data = json.loads(str(raw)) if isinstance(raw, str) else raw
            if isinstance(data, dict) and (data.get("expires_at", 0) < _time.time()
                                           or float(data.get("pct") or 0) >= 0.999):
                try:
                    del db[f"{Daedalus._PHASE_KEY_PREFIX}{session_id}"]
                except Exception:
                    pass
                return None
            return data
        except Exception as exc:
            log.debug("phase_load failed (non-fatal): %s", exc)
            return None

    @staticmethod
    def _phase_clear(session_id: str) -> None:
        """Clear phase resume context (task completed or session closed)."""
        try:
            from .kv import db  # type: ignore
            key = f"{Daedalus._PHASE_KEY_PREFIX}{session_id}"
            if db.get(key) is not None:
                del db[key]
        except Exception:
            pass

    async def chat(self, message: str, session_id: str | None = None,
                   for_self: bool = False, effort: str = "auto",
                   skill_ids: list[str] | None = None,
                   mcp_ids: list[str] | None = None,
                   attachment_ids: list[str] | None = None) -> dict[str, Any]:
        """Free-form conversation with Daedalus (no JSON contract).

        ``for_self=True`` marks the turn as "searching for improvements to
        Daedalus himself" — any discoveries captured are forced to ``pending``
        and the model is explicitly told to seek owner approval.
        """
        if not message or not message.strip():
            raise DaedalusBadRequest("message cannot be empty")
        session_id = session_id or f"chat-{uuid.uuid4().hex[:10]}"
        started = datetime.now(timezone.utc)
        msg = message.strip()
        from .task_effort import bind as bind_effort, briefing as effort_briefing, classify as classify_effort
        from .run_budget import bind as bind_budget, briefing as budget_briefing, plan_for_turn
        effort_dec = classify_effort(msg, requested=effort)
        budget_plan = plan_for_turn()
        if for_self:
            msg = (
                "[SELF-IMPROVEMENT MODE — improvements you find here are for "
                "YOU (Daedalus), not the owner's product. NEVER claim a finding "
                "is auto-approved. ALWAYS end with: 'Owner approval required "
                "before I adopt this.']\n\n" + msg
            )

        # Phase 16 — Scratch Journal resume detection
        # Ако owner пише "продължи"/"resume" И има активен journal за тази
        # сесия — inject-вай recap преди да започнем нова tool loop.
        try:
            if _journal.is_resume_request(msg) and _journal.get_journal(session_id):
                _recap = _journal.build_resume_recap(session_id)
                if _recap:
                    msg = f"[JOURNAL RESUME]\n{_recap}\n\nUser: {msg}"
                    _journal.mark_resumed(session_id)
                    log.info("journal: resumed session=%s", session_id[:10])
        except Exception as _re_exc:
            log.debug("journal resume skipped: %s", _re_exc)

        # Fix 4: Inject phase resume context if a previous session saved progress
        phase_ctx = self._phase_load(session_id)
        if phase_ctx:
            pct_pct = int((phase_ctx.get("pct", 0)) * 100)
            resume_note = (
                "[PHASE RESUME: you were already working on this task in an earlier session]\n"
                f"Task: {phase_ctx.get('task_name', '?')}\n"
                f"Progress: {pct_pct}% ({phase_ctx.get('msg', '')})\n"
                f"Saved: {phase_ctx.get('saved_at', '?')}\n\n"
                f"Do not redo the reconnaissance: continue from {pct_pct}%.\n\n"
            )
            msg = resume_note + msg
            log.info("phase_resume injected: session=%s pct=%d%% task=%s",
                     session_id, pct_pct, phase_ctx.get("task_name", "?"))

        brief = effort_briefing(effort_dec)
        if brief:
            msg = brief + "\n\n" + msg
        bbrief = budget_briefing(budget_plan)
        if bbrief:
            msg = bbrief + "\n\n" + msg

        from .session_context import bind as bind_turn, resolve_turn
        turn_pack = resolve_turn(
            session_id,
            skill_ids=skill_ids,
            mcp_ids=mcp_ids,
            attachment_ids=attachment_ids,
        )
        if turn_pack.attachment_text:
            msg = turn_pack.attachment_text + "\n\n" + msg

        with bind_effort(effort_dec), bind_budget(budget_plan), bind_turn(turn_pack):
            raw, summary = await self._build_context_and_call(
                user_message=msg,
                session_id=session_id,
                expect_json=False,
                for_self=for_self,
            )
        # Process task markers — execute side-effects, strip from reply
        raw, task_events = process_task_markers(raw, session_id)

        # Fix 4: Save phase context if NEED_PROGRESS was emitted during this session
        progress_calls = [c for c in (summary.get("tool_calls") or [])
                         if c.get("tool") == "set_progress"]
        if not progress_calls:
            # Also check via outbox progress marker pattern
            progress_calls = [c for c in (summary.get("tool_calls") or [])
                             if "progress" in c.get("tool", "").lower()]
        if progress_calls:
            last_p = progress_calls[-1]
            self._phase_save(
                session_id=session_id,
                pct=float(last_p.get("pct", 0)),
                task_name=str(last_p.get("task", "task")),
                msg=str(last_p.get("msg", "")),
            )
        # Clear resume if task was marked done this session
        done_tasks = [e for e in (task_events or []) if e.get("action") == "done"]
        if done_tasks:
            self._phase_clear(session_id)

        elapsed = (datetime.now(timezone.utc) - started).total_seconds()

        try:
            from .preferences import observe_turn
            observe_turn(message, raw)
        except Exception:
            pass

        dod = None
        try:
            from .job_contract import evaluate, finish_nudge
            from .learned_skills import draft_from_failure, draft_from_success
            _open_slices = 0
            try:
                from .job_ledger import open_count_for_session
                _open_slices = open_count_for_session(session_id or "")
            except Exception:
                _open_slices = 0
            dod = evaluate(
                message, raw or "",
                tool_calls=summary.get("tool_calls") or [],
                open_slices=_open_slices,
                kind=str(summary.get("build_kind") or ""),
                browser_ok=summary.get("browser_ok"),
            )
            if not dod.complete and dod.coding_task:
                raw = (raw or "") + finish_nudge(dod)
                try:
                    draft_from_failure(
                        kind="chat",
                        errors=dod.blockers,
                        context=message[:400],
                    )
                except Exception:
                    pass
            elif dod.complete and dod.coding_task:
                try:
                    draft_from_success(kind="chat", summary=(message or "")[:400])
                except Exception:
                    pass
        except Exception as _dod_exc:
            log.debug("job contract skipped: %s", _dod_exc)

        # ── Self-improvement on failure (T09) ─────────────────────────────
        # If session was slow (>90s) OR >50% of tool calls failed → schedule
        # a fire-and-forget for_self reflection that asks Дедал to propose
        # a fix to himself (written to outbox for owner review).
        try:
            tool_calls = (summary.get("tool_calls") or [])
            fails = sum(1 for c in tool_calls if c.get("ok") is False)
            fail_rate = (fails / len(tool_calls)) if tool_calls else 0.0
            should_reflect = (elapsed > 90 and len(tool_calls) >= 3) or fail_rate > 0.5
            if should_reflect and not for_self:
                async def _self_improve_bg():
                    try:
                        from .outbox import push_outbox
                        prompt = (
                            f"Анализирай тази неуспешна сесия и предложи КОНКРЕТНО "
                            f"подобрение за себе си.\n\n"
                            f"Owner request: {message[:300]}\n"
                            f"Elapsed: {elapsed:.1f}s, tool_calls: {len(tool_calls)}, "
                            f"failures: {fails} ({fail_rate*100:.0f}%)\n"
                            f"Failed tools: "
                            + ", ".join(c.get("tool", "?") for c in tool_calls if c.get("ok") is False)[:200]
                            + f"\n\nКакво да променя в моите prompts/tools/logic за да "
                            f"не се повтори? Дай 1 конкретно предложение."
                        )
                        result = await self.chat(prompt, for_self=True)
                        try:
                            push_outbox(
                                type="message",
                                title=f"Self-improvement (session {session_id[:10]})",
                                body=result.get("reply", "")[:2000],
                                category="self_improvement",
                                payload={"trigger": "failure_reflection",
                                         "elapsed": elapsed, "fail_rate": fail_rate,
                                         "src_session": session_id},
                                session_id=session_id,
                                require_send_perm=True,
                            )
                        except Exception:
                            pass
                    except Exception as _si_exc:
                        log.debug("self-improvement bg failed: %s", _si_exc)
                _t = asyncio.create_task(_self_improve_bg())
                self._background_tasks.add(_t)
                _t.add_done_callback(self._background_tasks.discard)
                log.info("self-improvement reflection scheduled: session=%s elapsed=%.1fs fail_rate=%.2f",
                         session_id, elapsed, fail_rate)
        except Exception as _si_outer:
            log.debug("self-improvement gating failed: %s", _si_outer)

        # Detect if Daedalus created a code proposal — extract ID for inline Approve UI
        pending_proposal: dict[str, Any] | None = None
        pid_match = re.search(r"Proposal ID\s*:\s*(prop_[a-f0-9_]+)", raw)
        fp_match = re.search(r"File\s*:\s*(\S+)", raw)
        if pid_match:
            pending_proposal = {
                "proposal_id": pid_match.group(1),
                "file_path": fp_match.group(1) if fp_match else "",
            }
        budget_status = None
        try:
            from .run_budget import status as budget_status_fn
            budget_status = budget_status_fn()
        except Exception:
            budget_status = budget_plan
        return {
            "agent": "Daedalus",
            "session_id": session_id,
            "for_self": for_self,
            "reply": raw,
            "context": summary,
            "pending_proposal": pending_proposal,
            "task_events": task_events,
            "model": get_config().get("model", _MODEL),
            "effort": {
                k: effort_dec.get(k)
                for k in ("tier", "label", "role", "budget", "note", "world_kit",
                          "source", "model", "model_source")
            },
            "elapsed_seconds": round(elapsed, 2),
            "timestamp": started.isoformat(),
            "complete": None if dod is None else dod.complete,
            "dod": None if dod is None else dod.as_dict(),
            "run_budget": budget_status,
        }

    async def generate(
        self,
        task: str,
        session_id: str | None = None,
        language: str | None = None,
        extra_context: str | None = None,
    ) -> dict[str, Any]:
        """Generate code from a natural-language task (JSON contract)."""
        if not task or not task.strip():
            raise DaedalusBadRequest("task cannot be empty")
        if len(task) > 4000:
            raise DaedalusBadRequest("task too long (max 4000 chars)")
        session_id = session_id or f"gen-{uuid.uuid4().hex[:10]}"
        started = datetime.now(timezone.utc)

        user_message = f"TASK: {task.strip()}"
        if language:
            user_message += f"\nLANGUAGE: {language}"
        if extra_context:
            user_message += f"\nADDITIONAL CONTEXT: {extra_context}"
        user_message += "\n\nRespond with the JSON contract from your persona (code generation format)."

        raw, summary = await self._build_context_and_call(
            user_message=user_message,
            session_id=session_id,
            retrieval_query=task,
            expect_json=True,
        )

        try:
            parsed = _parse_json_response(raw)
        except Exception as exc:
            raise DaedalusError(f"Failed to parse Daedalus response: {exc}") from exc

        # Capture artifact if code present
        code = parsed.get("code", "")
        if code:
            try:
                self.memory.capture_artifact(
                    session_id=session_id,
                    filename=parsed.get("filename", "output.txt"),
                    language=parsed.get("language", language or "unknown"),
                    code=code,
                    summary=parsed.get("explanation", task[:200]),
                )
            except Exception as exc:
                log.warning("artifact capture failed: %s", exc)

        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        return {
            "agent": "Daedalus",
            "session_id": session_id,
            "task": task,
            **parsed,
            "context": summary,
            "model": _MODEL,
            "elapsed_seconds": round(elapsed, 2),
            "timestamp": started.isoformat(),
        }

    async def explain(self, code: str, session_id: str | None = None, language: str | None = None) -> dict[str, Any]:
        if not code or not code.strip():
            raise DaedalusBadRequest("code cannot be empty")
        session_id = session_id or f"exp-{uuid.uuid4().hex[:10]}"
        msg = f"Explain this code clearly. Language hint: {language or 'auto-detect'}.\n\n```\n{code[:6000]}\n```"
        result = await self.chat(msg, session_id=session_id)
        result["action"] = "explain"
        return result

    async def review(
        self,
        code: str,
        session_id: str | None = None,
        language: str | None = None,
        focus: str | None = None,
    ) -> dict[str, Any]:
        if not code or not code.strip():
            raise DaedalusBadRequest("code cannot be empty")
        session_id = session_id or f"rev-{uuid.uuid4().hex[:10]}"
        msg = (
            f"Review this code. Language: {language or 'auto-detect'}. "
            f"Focus: {focus or 'security, correctness, Iyveris patterns'}.\n\n"
            f"```\n{code[:6000]}\n```\n\n"
            "Give: overall score 1-10, top issues (severity + fix), improvement suggestions."
        )
        result = await self.chat(msg, session_id=session_id)
        result["action"] = "review"
        return result

    def stats(self) -> dict[str, Any]:
        return {
            "agent": "Daedalus",
            "memory": self.memory.stats(),
            "knowledge_loaded": self._knowledge_loaded,
            "persona_length_chars": len(self.persona),
            "model": _MODEL,
            "mesh_snapshot": get_mesh_snapshot(),
        }

    def branch_session(
        self,
        parent_session_id: str,
        *,
        label: str = "",
    ) -> dict[str, Any]:
        """Fork a session to explore an alternative without losing parent history."""
        return _branch_session_fn(parent_session_id, label=label, memory=self.memory)

    def clear_session(self, session_id: str) -> dict[str, Any]:
        n = self.memory.clear_session(session_id)
        # Clear the persisted playground slug + manifest for this session
        try:
            from .kv import db as _rdb_clr
            _pg_key = f"adn:daedalus_pg_slug:{session_id}"
            _slug_val = _rdb_clr.get(_pg_key)
            if _slug_val:
                _man_key = f"adn:daedalus_pg_manifest:{_slug_val}"
                if _rdb_clr.get(_man_key):
                    del _rdb_clr[_man_key]
                del _rdb_clr[_pg_key]
        except Exception:
            pass
        return {"session_id": session_id, "turns_deleted": n}

    # ------------------------------------------------------------------
    # Self-reflection — manual triggers + introspection
    # ------------------------------------------------------------------

    async def reflect_now(self, session_id: str) -> dict[str, Any]:
        """Manually trigger a reflection cycle on a session's latest turn."""
        history = self.memory.session_history(session_id, limit=2)
        if len(history) < 2:
            return {"ok": False, "reason": "not enough history in session"}
        user_msg = next((t for t in history if t["metadata"].get("role") == "user"), None)
        asst_msg = next((t for t in history if t["metadata"].get("role") == "assistant"), None)
        if not user_msg or not asst_msg:
            return {"ok": False, "reason": "could not pair user+assistant turns"}
        result = await run_reflection_cycle(
            _call_claude, self.memory, session_id,
            user_msg.get("content", ""), asst_msg.get("content", ""),
        )
        return {"ok": True, **result}

    def list_lessons(self, limit: int = 50) -> dict[str, Any]:
        lessons = self.memory.all_lessons(limit=limit)
        return {"count": len(lessons), "lessons": lessons}

    def list_episodes(self, query: str | None = None, top_k: int = 10) -> dict[str, Any]:
        if query:
            eps = self.memory.retrieve_episodes(query, top_k=top_k)
        else:
            try:
                res = self.memory.episodes_col.get(limit=top_k)
                eps = []
                for i, eid in enumerate(res.get("ids", [])):
                    eps.append({
                        "id": eid,
                        "content": (res.get("documents") or [])[i],
                        "metadata": (res.get("metadatas") or [{}])[i],
                    })
                eps.sort(key=lambda x: x["metadata"].get("ended_at", ""), reverse=True)
            except Exception:
                eps = []
        return {"count": len(eps), "episodes": eps}

    def mesh_snapshot(self) -> dict[str, Any]:
        return get_mesh_snapshot()


def get_daedalus() -> Daedalus:
    """Singleton accessor."""
    return Daedalus()
