"""Leaf adapter: future Run Engine calls the old chat path through here.

Does not rewrite agent.py. Lazy-imports get_daedalus. Do not import this
module from agent.py or native_tools.py. Prompt front matter is assembled
by context_assembler; this file still only wraps chat.
"""
from __future__ import annotations

import os
import re
import time
from collections.abc import Mapping
from typing import Any

from daedalus.contracts import (
    ContractError,
    ContractModel,
    EffortTier,
    ErrorClass,
    EventKind,
    Run,
    RunBudgetSpend,
    RunEvent,
    RunStage,
    RunStatus,
)
from daedalus.event_log import EventLog
from daedalus.run_state import RunState, from_run
from daedalus.stage_machine import from_legacy_stage, status_for_stage

_BLOCKED_LINE = re.compile(r"^BLOCKED:", re.MULTILINE | re.IGNORECASE)
_SPEND_FIELDS = (
    "cap_usd",
    "spent_usd",
    "remaining_usd",
    "calls",
    "prompt_tokens",
    "completion_tokens",
    "unknown_cost_calls",
    "exhausted",
    "last_model",
    "last_role",
    "last_source",
)
_CHEAP_ROUTES = frozenset({"mesh_router_chat", "chat_fast_path"})


class LegacyTurnRequest(ContractModel):
    message: str
    session_id: str | None = None
    run_id: str = ""
    slug: str = ""
    for_self: bool = False
    effort: str = "auto"
    skill_ids: list[str] | None = None
    mcp_ids: list[str] | None = None
    attachment_ids: list[str] | None = None


class LegacyTurnResult(ContractModel):
    chat: dict[str, Any]
    state: RunState
    stage: RunStage | None = None
    events: tuple[RunEvent, ...] = ()
    error: ContractError | None = None


def adapter_enabled() -> bool:
    flag = os.environ.get("DAEDALUS_LEGACY_ADAPTER", "").strip().lower()
    return flag in ("1", "true", "yes", "on")


def unwrap_chat(result: LegacyTurnResult) -> dict[str, Any]:
    """Identity: HTTP/jobs keep the old chat body."""
    return result.chat


async def run_turn(req: LegacyTurnRequest, *, agent: Any = None) -> LegacyTurnResult:
    """Call Daedalus.chat. Map the dict. Do not rewrite the loop."""
    from daedalus.agent import DaedalusBadRequest, get_daedalus

    actor = agent if agent is not None else get_daedalus()
    try:
        chat = await actor.chat(
            message=req.message,
            session_id=req.session_id,
            for_self=req.for_self,
            effort=req.effort,
            skill_ids=req.skill_ids,
            mcp_ids=req.mcp_ids,
            attachment_ids=req.attachment_ids,
        )
    except DaedalusBadRequest:
        raise
    except Exception as exc:
        return _failed_result(req, exc)
    return result_from_chat(req, chat if isinstance(chat, dict) else {})


def result_from_chat(req: LegacyTurnRequest, chat: Mapping[str, Any]) -> LegacyTurnResult:
    payload = dict(chat)
    session_id = str(payload.get("session_id") or req.session_id or "")
    run_id = req.run_id or session_id or "legacy-turn"
    stage = infer_stage(payload)
    now = time.time()
    run = Run(
        run_id=run_id,
        job_id=run_id,
        slug=req.slug or "",
        session_id=session_id,
        original_ask=(req.message or "").strip(),
        status=status_for_stage(stage) if stage is not None else RunStatus.IN_PROGRESS,
        created_at=now,
        updated_at=now,
        effort=_effort_tier(payload.get("effort")),
        model=str(payload.get("model") or ""),
        complete=payload.get("complete"),
        stop_reason=_stop_reason(payload),
        spend=_spend(payload.get("run_budget")),
    )
    events = _events_for_turn(run_id, payload, stage)
    return LegacyTurnResult(
        chat=payload,
        state=from_run(run),
        stage=stage,
        events=events,
    )


def infer_stage(chat: Mapping[str, Any]) -> RunStage | None:
    context = chat.get("context") if isinstance(chat.get("context"), dict) else {}
    navigator = context.get("navigator") if isinstance(context.get("navigator"), dict) else {}
    routed = str(context.get("routed_to") or "")
    intent = str(navigator.get("intent") or "")
    hint = str(navigator.get("hint") or "")
    build_kind = str(context.get("build_kind") or "")
    tool_calls = context.get("tool_calls") or []
    dod = chat.get("dod") if isinstance(chat.get("dod"), dict) else {}
    reply = str(chat.get("reply") or "")

    if _hard_block(dod, reply):
        return RunStage.BLOCKED
    if chat.get("complete") is True and bool(dod.get("coding_task")):
        return RunStage.REVIEW
    mapped_kind = from_legacy_stage(build_kind)
    if mapped_kind is not None:
        return mapped_kind
    if routed in _CHEAP_ROUTES or intent == "chat":
        return RunStage.INTAKE
    if tool_calls or intent == "code_change" or build_kind:
        return RunStage.IMPLEMENT
    mapped = from_legacy_stage(intent) or from_legacy_stage(hint)
    return mapped


def _failed_result(req: LegacyTurnRequest, exc: Exception) -> LegacyTurnResult:
    run_id = req.run_id or req.session_id or "legacy-turn"
    elog = EventLog(run_id)
    elog.append(EventKind.STATUS, {"status": RunStatus.IN_PROGRESS.value})
    elog.append(EventKind.ERROR, {"message": str(exc)[:400]})
    elog.append(EventKind.PHASE, {"stage": RunStage.FAILED.value, "label": RunStage.FAILED.value})
    now = time.time()
    run = Run(
        run_id=run_id,
        job_id=run_id,
        slug=req.slug or "",
        session_id=req.session_id or "",
        original_ask=(req.message or "").strip(),
        status=RunStatus.FAILED,
        created_at=now,
        updated_at=now,
        stop_reason=str(exc)[:200],
    )
    return LegacyTurnResult(
        chat={},
        state=from_run(run),
        stage=RunStage.FAILED,
        events=elog.events(),
        error=ContractError(error_class=ErrorClass.TOOL_FAILED, message=str(exc)[:240]),
    )


def _events_for_turn(
    run_id: str,
    chat: Mapping[str, Any],
    stage: RunStage | None,
) -> tuple[RunEvent, ...]:
    elog = EventLog(run_id)
    elog.append(EventKind.STATUS, {"status": RunStatus.IN_PROGRESS.value})
    context = chat.get("context") if isinstance(chat.get("context"), dict) else {}
    for call in context.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        data: dict[str, Any] = {"tool": str(call.get("tool") or call.get("name") or "")}
        if "ok" in call:
            data["ok"] = call["ok"]
        if "blocked" in call:
            data["blocked"] = call["blocked"]
        elog.append(EventKind.TOOL, data)
    for raw in chat.get("task_events") or []:
        if not isinstance(raw, dict):
            continue
        elog.append(
            EventKind.TASK,
            {
                "action": raw.get("action") or "",
                "ok": raw.get("ok"),
                "task": raw.get("task"),
                "error": raw.get("error") or "",
                "mode": raw.get("mode") or "",
            },
        )
    if stage is not None:
        elog.append(EventKind.PHASE, {"stage": stage.value, "label": stage.value})
    proposal = chat.get("pending_proposal")
    if isinstance(proposal, dict) and proposal:
        elog.append(
            EventKind.LOG,
            {
                "proposal_id": proposal.get("proposal_id") or "",
                "file_path": proposal.get("file_path") or "",
            },
        )
    if chat.get("complete") is True:
        elog.append(EventKind.DONE, {"complete": True})
    return elog.events()


def _effort_tier(raw: Any) -> EffortTier | None:
    if not isinstance(raw, dict):
        return None
    tier = str(raw.get("tier") or "").strip().lower()
    if not tier or tier == "auto":
        return None
    try:
        return EffortTier(tier)
    except ValueError:
        return None


def _spend(raw: Any) -> RunBudgetSpend | None:
    if not isinstance(raw, dict):
        return None
    body = raw.get("spend") if isinstance(raw.get("spend"), dict) else raw
    fields = {key: body[key] for key in _SPEND_FIELDS if key in body}
    if not fields:
        return None
    return RunBudgetSpend.model_validate(fields)


def _stop_reason(chat: Mapping[str, Any]) -> str:
    dod = chat.get("dod")
    if isinstance(dod, dict) and dod.get("complete") is False:
        return str(dod.get("summary") or "")[:200]
    return ""


def _hard_block(dod: Mapping[str, Any], reply: str) -> bool:
    blob = f"{dod.get('summary') or ''}\n{reply}"
    if "TASK_BLOCKED" in blob.upper():
        return True
    return bool(_BLOCKED_LINE.search(blob))
