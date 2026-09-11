"""Final response wrap-up, persist, and chat payload.

Leaf module. Do not import agent.py. Cheap-path ROUTING and leftover NEED_*
if-arms stay on Daedalus. ToolLoop is the round driver. VerificationController
is a sibling leaf — this façade calls accept() and does not re-implement DoD.
RunFinalizer is after-loop wrap then chat payload.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from . import continuity as _continuity
from . import mode as _mode
from .improvements import (
    detect_intent_without_action,
    needs_read_first_hint,
    question_budget_check_and_increment,
    record_outcome,
    review_gate_critique,
)
from .llm_config import get_config
from .reflection import run_reflection_cycle

log = logging.getLogger(__name__)

_MODEL = "claude-sonnet-4-6"

_TASK_OPEN_RE = re.compile(r"<TASK_OPEN:\s*([^|>\n]{1,80})\|\s*([^>\n]{1,400})>")
_TASK_DONE_RE = re.compile(r"<TASK_DONE:\s*([^>\n]{1,100})>")
_TASK_REWORK_RE = re.compile(r"<TASK_REWORK:\s*([^|>\n]{1,80})\|\s*([^>\n]{1,400})>")
_TASK_TOUCH_RE = re.compile(r"<TASK_TOUCH:\s*([^>\n]{1,100})>")
_MODE_RE = re.compile(r"<NEED_MODE:\s*(talk|build|auto)\s*>", re.IGNORECASE)

_INLINE_NEED_RE = re.compile(r"<NEED_[A-Z_]+[^>]*>")
_BLOCK_NEED_RE = re.compile(
    r"<NEED_(?:PROPOSE_START|SANDBOX)>.*?</NEED_(?:PROPOSE_START|SANDBOX)>",
    re.DOTALL,
)
_PROPOSAL_ID_RE = re.compile(r"Proposal ID\s*:\s*(prop_[a-f0-9_]+)")
_PROPOSAL_FILE_RE = re.compile(r"File\s*:\s*(\S+)")


@dataclass
class AfterLoopRequest:
    raw: str
    session_id: str
    user_message: str
    expect_json: bool
    for_self: bool
    tool_calls: list[dict[str, Any]]
    leftover_iterations: int
    started_at: float
    briefing_dict: dict[str, Any] | None
    retrieval: dict[str, Any]
    loop: Any | None
    memory: Any
    mem0: Any
    background_tasks: set
    complete_fn: Callable[..., Awaitable[str]]
    clear_live: Callable[..., Any]


@dataclass
class AfterLoopResult:
    raw: str
    summary: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatFinishRequest:
    raw: str
    summary: dict[str, Any]
    message: str
    session_id: str
    for_self: bool
    started: datetime
    effort_dec: dict[str, Any]
    budget_plan: Any
    background_tasks: set
    chat_fn: Callable[..., Awaitable[dict[str, Any]]]
    phase_save_fn: Callable[..., None]
    phase_clear_fn: Callable[..., None]


@dataclass
class ChatFinishResult:
    payload: dict[str, Any] = field(default_factory=dict)


def strip_need_markers(raw: str) -> str:
    """Remove leftover NEED_* markers the owner must not see."""
    text = _INLINE_NEED_RE.sub("", raw or "")
    text = _BLOCK_NEED_RE.sub("", text)
    return text.strip()


def scrape_pending_proposal(raw: str) -> dict[str, Any] | None:
    pid_match = _PROPOSAL_ID_RE.search(raw or "")
    if not pid_match:
        return None
    fp_match = _PROPOSAL_FILE_RE.search(raw or "")
    return {
        "proposal_id": pid_match.group(1),
        "file_path": fp_match.group(1) if fp_match else "",
    }


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
            updated = _continuity.update_task(
                t["id"], status="rework",
                summary=new_summary, add_session_id=session_id,
            )
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


class RunFinalizer:
    """Stateless façade. After-loop wrap + chat payload. Not VerificationController / ToolLoop."""

    async def after_loop(self, req: AfterLoopRequest) -> AfterLoopResult:
        raw = req.raw
        session_id = req.session_id
        user_message = req.user_message
        expect_json = req.expect_json
        for_self = req.for_self
        tool_calls_used = req.tool_calls
        _tool_iter = req.leftover_iterations
        briefing_dict = req.briefing_dict
        _loop = req.loop

        # ── Empty reply guard (T05) — Haiku wrap-up if loop consumed everything
        # When Дедал's last response was 100% tool markers (now stripped to empty)
        # we'd return a blank reply. Call Haiku with the tool results to produce
        # a brief synthesized answer instead.
        _raw_text_check = strip_need_markers(raw or "")
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
                from .model_gateway import CompleteRequest, ModelGateway
                _wrap_res = await ModelGateway().complete_cheap(
                    CompleteRequest(
                        system=_wrap_system,
                        messages=[{"role": "user", "content": _wrap_user}],
                        intent="cheap",
                        max_tokens=600,
                        client_id=dm.client_id("chat", "wrap-up"),
                        use_cache=False,
                        role="wrap",
                    ),
                    include_master_config=False,
                    swallow_mesh_error=True,
                )
                raw = _wrap_res.text
                if raw and _wrap_res.routed_via == "mesh":
                    log.info(
                        "empty-reply guard: mesh_router produced %d chars ($%.6f)",
                        len(raw), float(_wrap_res.cost_usd or 0.0),
                    )
                elif raw and _wrap_res.routed_via == "cheap":
                    log.info(
                        "empty-reply guard: cheap-llm (%s) produced %d chars",
                        _wrap_res.model, len(raw),
                    )
                if not (raw or "").strip():
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

        req.memory.capture_turn(session_id, "user", user_message)
        req.memory.capture_turn(
            session_id, "assistant", raw,
            metadata={"format": "json" if expect_json else "text"},
        )
        req.mem0.capture(session_id, "user", user_message)
        req.mem0.capture(session_id, "assistant", raw)

        try:
            req.clear_live(session_id)
        except Exception:
            pass

        if for_self:
            log.info("session=%s reflection skipped (for_self=True; owner approval required)", session_id)
        else:
            try:
                t = asyncio.create_task(run_reflection_cycle(
                    req.complete_fn, req.memory, session_id, user_message, raw,
                ))
                req.background_tasks.add(t)
                t.add_done_callback(req.background_tasks.discard)
            except Exception as exc:
                log.debug("reflection scheduling skipped: %s", exc)

        if raw:
            raw = strip_need_markers(raw)

        try:
            _elapsed_total = time.time() - req.started_at
        except Exception:
            _elapsed_total = 0.0
        try:
            _rf_hint = needs_read_first_hint(session_id, raw or "")
            if _rf_hint and raw:
                log.info("read-first hint emitted for session=%s", session_id[:10])
        except Exception:
            pass
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
                    raw = (raw or "") + (
                        f"\n\n— Self-review бележка: {_crit.get('issue','')[:300]} "
                        f"{('Предложение: ' + _crit.get('suggested_fix','')[:200]) if _crit.get('suggested_fix') else ''}"
                    )
        except Exception as _rg_exc:
            log.debug("review-gate skipped: %s", _rg_exc)
        try:
            allowed, advice = question_budget_check_and_increment(session_id, raw or "")
            if not allowed and advice:
                raw = advice + "\n\n" + (raw or "")
        except Exception:
            pass
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

        try:
            from .clarification_tracker import detect_clarifying_question, log_clarification
            _clr_qs = detect_clarifying_question(raw or "")
            if _clr_qs:
                asyncio.create_task(log_clarification(
                    session_id=session_id,
                    user_message=user_message,
                    agent_reply=raw or "",
                    questions=_clr_qs,
                ))
        except Exception:
            pass

        retrieved = req.retrieval or {}
        knowledge = retrieved.get("knowledge") or []
        past_memory = retrieved.get("past_memory") or []
        artifacts = retrieved.get("artifacts") or []
        session_recent = retrieved.get("session_recent") or []
        lessons = retrieved.get("lessons") or []
        episodes = retrieved.get("episodes") or []
        mesh_block = retrieved.get("mesh_block")
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
            "mem0": req.mem0.stats(),
            "navigator": briefing_dict,
            "build_kind": str(getattr(_loop, "kind", "") or "") if _loop is not None else "",
            "browser_ok": _loop.browser_ok_for_dod() if _loop is not None else None,
        }
        return AfterLoopResult(raw=raw, summary=summary)

    async def finish_chat(self, req: ChatFinishRequest) -> ChatFinishResult:
        raw, task_events = process_task_markers(req.raw, req.session_id)
        summary = req.summary
        session_id = req.session_id
        message = req.message
        for_self = req.for_self

        progress_calls = [c for c in (summary.get("tool_calls") or [])
                         if c.get("tool") == "set_progress"]
        if not progress_calls:
            progress_calls = [c for c in (summary.get("tool_calls") or [])
                             if "progress" in c.get("tool", "").lower()]
        if progress_calls:
            last_p = progress_calls[-1]
            req.phase_save_fn(
                session_id=session_id,
                pct=float(last_p.get("pct", 0)),
                task_name=str(last_p.get("task", "task")),
                msg=str(last_p.get("msg", "")),
            )
        done_tasks = [e for e in (task_events or []) if e.get("action") == "done"]
        if done_tasks:
            req.phase_clear_fn(session_id)

        elapsed = (datetime.now(timezone.utc) - req.started).total_seconds()

        try:
            from .preferences import observe_turn
            observe_turn(message, raw)
        except Exception:
            pass

        dod = None
        try:
            from .job_contract import finish_nudge
            from .learned_skills import draft_from_failure, draft_from_success
            from .verification_controller import VerificationController, VerifyRequest
            _open_slices = 0
            try:
                from .job_ledger import open_count_for_session
                _open_slices = open_count_for_session(session_id or "")
            except Exception:
                _open_slices = 0
            dod = VerificationController().accept(
                VerifyRequest(
                    ask=message,
                    reply=raw or "",
                    tool_calls=summary.get("tool_calls") or [],
                    open_slices=_open_slices,
                    kind=str(summary.get("build_kind") or ""),
                    browser_ok=summary.get("browser_ok"),
                )
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
                        result = await req.chat_fn(prompt, for_self=True)
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
                req.background_tasks.add(_t)
                _t.add_done_callback(req.background_tasks.discard)
                log.info("self-improvement reflection scheduled: session=%s elapsed=%.1fs fail_rate=%.2f",
                         session_id, elapsed, fail_rate)
        except Exception as _si_outer:
            log.debug("self-improvement gating failed: %s", _si_outer)

        pending_proposal = scrape_pending_proposal(raw)
        budget_status = None
        try:
            from .run_budget import status as budget_status_fn
            budget_status = budget_status_fn()
        except Exception:
            budget_status = req.budget_plan
        return ChatFinishResult(payload={
            "agent": "Daedalus",
            "session_id": session_id,
            "for_self": for_self,
            "reply": raw,
            "context": summary,
            "pending_proposal": pending_proposal,
            "task_events": task_events,
            "model": get_config().get("model", _MODEL),
            "effort": {
                k: req.effort_dec.get(k)
                for k in ("tier", "label", "role", "budget", "note", "world_kit",
                          "source", "model", "model_source")
            },
            "elapsed_seconds": round(elapsed, 2),
            "timestamp": req.started.isoformat(),
            "complete": None if dod is None else dod.complete,
            "dod": None if dod is None else (
                dod.as_dict() if hasattr(dod, "as_dict") else dod.model_dump()
            ),
            "run_budget": budget_status,
        })
