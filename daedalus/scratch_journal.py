"""Daedalus Scratch Journal — temporary per-session work log.

Phase 16. Дава на Дедал възможност да:
- записва частични резултати по време на дълга задача (NEED_JOURNAL_WRITE)
- следи token budget и автоматично пауза при ~80% от лимита
- resume-ва от точката на спиране без да губи вече събраната информация
- бъде паузиран от owner UI бутон (background monitor)

Storage: Replit DB when present, else JSON files under ``DAEDALUS_DATA/kv``
(``adn:journal:{session_id}`` + pause flags). Standalone Docker has no Replit.
"""
from __future__ import annotations

import logging
import time
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Replit DB shim — keeps tests independent of replit module availability
# ---------------------------------------------------------------------------

def _rdb_get(key: str) -> Any:
    from .kv import get as kv_get
    return kv_get(key)


def _rdb_set(key: str, value: Any) -> None:
    from .kv import set as kv_set
    kv_set(key, value)


def _rdb_delete(key: str) -> None:
    from .kv import delete as kv_delete
    kv_delete(key)


# ---------------------------------------------------------------------------
# Token estimation — char/4 heuristic (no tiktoken dep).
# Близо до реалните цифри на Claude/GPT за смес от BG + EN + код.
# ---------------------------------------------------------------------------

def estimate_tokens(text: str | None) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


# Sonnet има 200k context; оставяме ~50k headroom за персона + briefing.
TOKEN_BUDGET_PER_SESSION = 150_000
PAUSE_THRESHOLD = 0.80
JOURNAL_TTL_SECONDS = 24 * 3600  # auto-expire след 24ч

_JOURNAL_PREFIX = "adn:journal:sess:"  # actual journal entries
_PAUSE_PREFIX = "adn:journal:pause:"

_MAX_STEPS_PER_JOURNAL = 50


# ---------------------------------------------------------------------------
# Core journal API
# ---------------------------------------------------------------------------

def _key(session_id: str) -> str:
    return _JOURNAL_PREFIX + session_id


def _list_session_ids() -> list[str]:
    """Enumerate journal sessions (Replit prefix scan, else DAEDALUS_DATA/kv files)."""
    from .kv import list_keys
    keys = list_keys(_JOURNAL_PREFIX)
    return [k[len(_JOURNAL_PREFIX):] for k in keys if k.startswith(_JOURNAL_PREFIX)]


def write_step(
    session_id: str,
    step_name: str,
    summary: str,
    data: str = "",
    task_name: str = "",
) -> dict:
    """Append a step to the session's scratch journal."""
    if not session_id:
        return {}
    j = get_journal(session_id) or {
        "session_id": session_id,
        "task_name": task_name or "untitled",
        "steps": [],
        "tokens_est": 0,
        "created_at": int(time.time()),
        "status": "active",
    }
    if task_name and not j.get("task_name") or j.get("task_name") == "untitled":
        j["task_name"] = task_name or j.get("task_name") or "untitled"

    summary = (summary or "").strip()[:1000]
    data = (data or "").strip()[:8000]
    tokens = estimate_tokens(summary) + estimate_tokens(data)
    step = {
        "step": (step_name or "step").strip()[:80],
        "summary": summary,
        "data": data,
        "tokens_est": tokens,
        "ts": int(time.time()),
    }
    j["steps"].append(step)
    # Cap at MAX — drop oldest if exceeded (keeps newest insights)
    if len(j["steps"]) > _MAX_STEPS_PER_JOURNAL:
        dropped = j["steps"].pop(0)
        j["tokens_est"] = max(0, j["tokens_est"] - int(dropped.get("tokens_est", 0)))
    j["tokens_est"] = int(j.get("tokens_est", 0)) + tokens
    j["updated_at"] = int(time.time())
    _rdb_set(_key(session_id), j)
    return step


def get_journal(session_id: str) -> dict | None:
    if not session_id:
        return None
    j = _rdb_get(_key(session_id))
    if not j:
        return None
    # Auto-expire очаквания
    if int(time.time()) - int(j.get("updated_at", j.get("created_at", 0))) > JOURNAL_TTL_SECONDS:
        clear_journal(session_id)
        return None
    return j


def clear_journal(session_id: str) -> bool:
    if not session_id:
        return False
    _rdb_delete(_key(session_id))
    _rdb_delete(_PAUSE_PREFIX + session_id)
    return True


def list_active_journals() -> list[dict]:
    """Snapshot of all active journals — scans prefix (race-free, no shared index)."""
    out: list[dict] = []
    now = int(time.time())
    stale: list[str] = []
    for sid in _list_session_ids():
        j = _rdb_get(_key(sid))
        if not j:
            continue
        updated = int(j.get("updated_at", j.get("created_at", 0)))
        if now - updated > JOURNAL_TTL_SECONDS:
            stale.append(sid)
            continue
        tokens = int(j.get("tokens_est", 0))
        last_step = (j.get("steps") or [{}])[-1]
        out.append({
            "session_id": sid,
            "task_name": j.get("task_name", ""),
            "status": j.get("status", "active"),
            "step_count": len(j.get("steps") or []),
            "tokens_est": tokens,
            "budget_pct": round(tokens / TOKEN_BUDGET_PER_SESSION * 100, 1),
            "last_step_name": last_step.get("step", ""),
            "last_summary": (last_step.get("summary") or "")[:200],
            "updated_at": updated,
            "pause_requested": is_pause_requested(sid),
            "paused": j.get("status") == "paused",
        })
    if stale:
        for sid in stale:
            clear_journal(sid)
    out.sort(key=lambda e: e["updated_at"], reverse=True)
    return out


# ---------------------------------------------------------------------------
# Pause / resume control
# ---------------------------------------------------------------------------

def request_pause(session_id: str, reason: str = "owner_requested") -> bool:
    if not session_id:
        return False
    _rdb_set(_PAUSE_PREFIX + session_id, {"reason": reason, "ts": int(time.time())})
    log.info("journal: pause requested for session=%s reason=%s",
             session_id[:10], reason)
    return True


def is_pause_requested(session_id: str) -> bool:
    if not session_id:
        return False
    return _rdb_get(_PAUSE_PREFIX + session_id) is not None


def clear_pause(session_id: str) -> None:
    if session_id:
        _rdb_delete(_PAUSE_PREFIX + session_id)


def mark_paused(session_id: str, reason: str) -> None:
    """Update journal status to paused (called from agent loop)."""
    j = get_journal(session_id)
    if not j:
        return
    j["status"] = "paused"
    j["pause_reason"] = reason
    j["updated_at"] = int(time.time())
    _rdb_set(_key(session_id), j)


def mark_resumed(session_id: str) -> None:
    j = get_journal(session_id)
    if not j:
        return
    j["status"] = "active"
    j.pop("pause_reason", None)
    j["updated_at"] = int(time.time())
    _rdb_set(_key(session_id), j)
    clear_pause(session_id)


# ---------------------------------------------------------------------------
# Helpers за agent.py integration
# ---------------------------------------------------------------------------

def should_pause(session_id: str, extra_tokens_used: int = 0) -> tuple[bool, str]:
    """Решава дали Дедал трябва да пауза СЕГА.

    Returns (should_pause, reason).
    """
    if not session_id:
        return False, ""
    if is_pause_requested(session_id):
        return True, "owner_requested"
    j = get_journal(session_id)
    if not j:
        return False, ""
    total = int(j.get("tokens_est", 0)) + max(0, int(extra_tokens_used))
    if total >= int(TOKEN_BUDGET_PER_SESSION * PAUSE_THRESHOLD):
        return True, f"token_budget_{int(total/TOKEN_BUDGET_PER_SESSION*100)}pct"
    return False, ""


_RESUME_TRIGGERS = (
    "продължи", "продължавай", "продължете", "продължи задачата",
    "resume", "continue", "go on", "продължи моля",
)


def is_resume_request(user_msg: str) -> bool:
    if not user_msg:
        return False
    msg = user_msg.strip().lower()
    if len(msg) > 80:
        return False
    return any(t in msg for t in _RESUME_TRIGGERS)


def build_resume_recap(session_id: str, max_chars: int = 280) -> str:
    """Compact recap за inject в briefing.hint при resume."""
    j = get_journal(session_id)
    if not j or not j.get("steps"):
        return ""
    task = j.get("task_name", "задача")
    steps = j.get("steps") or []
    last_3 = steps[-3:]
    bits = " | ".join(
        f"{s.get('step','')}: {(s.get('summary','') or '')[:60]}"
        for s in last_3
    )
    pause_reason = j.get("pause_reason", "")
    recap = (
        f"RESUME: '{task}' (стъпки={len(steps)}, last={bits}). "
        f"Pause: {pause_reason}. Продължи от където спря — НЕ започвай отначало."
    )
    return recap[:max_chars]
