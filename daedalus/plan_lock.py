"""Daedalus Plan Lock — Phase 17C.

Prevents Дедал from abandoning an active multi-step plan mid-execution.
When a plan is locked, new competing top-level task requests are held
until the lock is released or expires.

Storage: ``adn:plan_lock`` in Replit DB, else ``DAEDALUS_DATA/kv``.
TTL: 30 minutes (auto-expire prevents permanent orphan locks).

Marker:
  <NEED_PLAN_LOCK: plan_id | action>
  plan_id : short identifier for the plan (e.g. "phase-17b-agent-edits")
  action  : acquire | release | check | extend

Дедал uses this to:
1. acquire — lock before starting a multi-file / multi-step plan
2. release — unlock after plan is completed or abandoned  
3. check   — verify the lock is still held before each step
4. extend  — add 30 more minutes if the plan is taking longer
"""
from __future__ import annotations

import logging
import time
from typing import Any

log = logging.getLogger(__name__)

_LOCK_KEY = "adn:plan_lock"
_DEFAULT_TTL_MIN = 30
_EXTEND_MIN = 30


# ---------------------------------------------------------------------------
# Replit DB helpers (same pattern as outbox.py)
# ---------------------------------------------------------------------------

def _rdb_get(key: str) -> Any:
    from . import kv
    return kv.get(key)


def _rdb_set(key: str, value: Any) -> None:
    from . import kv
    kv.set(key, value)


def _rdb_delete(key: str) -> None:
    from . import kv
    kv.delete(key)


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------

def acquire(plan_id: str, ttl_min: int = _DEFAULT_TTL_MIN) -> dict[str, Any]:
    """Acquire the plan lock.

    Returns {"ok": True} if acquired, {"ok": False, "reason": ..., "lock": ...} if blocked.
    """
    existing = _rdb_get(_LOCK_KEY)
    if existing:
        # Check expiry
        expires_at = existing.get("expires_at", 0)
        if time.time() < expires_at:
            # Lock still valid — check if same plan_id (idempotent)
            if existing.get("plan_id") == plan_id:
                return {"ok": True, "status": "already_held", "lock": existing}
            return {
                "ok": False,
                "reason": f"Планът '{existing.get('plan_id')}' вече е заключен "
                          f"(изтича след {int((expires_at - time.time()) / 60)} мин.)",
                "lock": existing,
            }
        # Expired — auto-release
        _rdb_delete(_LOCK_KEY)
        log.info("plan_lock: expired lock for '%s' auto-released", existing.get("plan_id"))

    lock = {
        "plan_id": plan_id,
        "acquired_at": time.time(),
        "expires_at": time.time() + ttl_min * 60,
        "ttl_min": ttl_min,
    }
    _rdb_set(_LOCK_KEY, lock)
    log.info("plan_lock: acquired for plan_id=%r (ttl=%dm)", plan_id, ttl_min)
    return {"ok": True, "status": "acquired", "lock": lock}


def release(plan_id: str | None = None) -> dict[str, Any]:
    """Release the plan lock.

    If plan_id is given, only release if it matches (safety check).
    """
    existing = _rdb_get(_LOCK_KEY)
    if not existing:
        return {"ok": True, "status": "nothing_to_release"}
    if plan_id and existing.get("plan_id") != plan_id:
        return {
            "ok": False,
            "reason": f"Lock принадлежи на '{existing.get('plan_id')}', не на '{plan_id}'",
        }
    _rdb_delete(_LOCK_KEY)
    log.info("plan_lock: released (plan_id=%r)", existing.get("plan_id"))
    return {"ok": True, "status": "released", "plan_id": existing.get("plan_id")}


def check(plan_id: str | None = None) -> dict[str, Any]:
    """Check lock status without modifying it."""
    existing = _rdb_get(_LOCK_KEY)
    if not existing:
        return {"locked": False, "status": "free"}
    expires_at = existing.get("expires_at", 0)
    if time.time() >= expires_at:
        _rdb_delete(_LOCK_KEY)
        return {"locked": False, "status": "expired_and_cleared"}
    remaining_min = int((expires_at - time.time()) / 60)
    owned = plan_id is None or existing.get("plan_id") == plan_id
    return {
        "locked": True,
        "owned": owned,
        "plan_id": existing.get("plan_id"),
        "remaining_min": remaining_min,
        "status": "held",
    }


def extend(plan_id: str, extra_min: int = _EXTEND_MIN) -> dict[str, Any]:
    """Extend the TTL of the current lock if it matches plan_id."""
    existing = _rdb_get(_LOCK_KEY)
    if not existing:
        return {"ok": False, "reason": "Няма активен lock за extend"}
    if existing.get("plan_id") != plan_id:
        return {"ok": False, "reason": f"Lock принадлежи на '{existing.get('plan_id')}'"}
    existing["expires_at"] = existing.get("expires_at", time.time()) + extra_min * 60
    _rdb_set(_LOCK_KEY, existing)
    remaining_min = int((existing["expires_at"] - time.time()) / 60)
    log.info("plan_lock: extended plan_id=%r by %dm (now %dm remaining)", plan_id, extra_min, remaining_min)
    return {"ok": True, "status": "extended", "remaining_min": remaining_min}


def is_locked() -> bool:
    """True if there is a valid (non-expired) lock."""
    return check().get("locked", False)


def get_lock() -> dict[str, Any] | None:
    """Return current lock data or None."""
    status = check()
    if status.get("locked"):
        return status
    return None


def format_for_prompt(action: str, result: dict[str, Any]) -> str:
    """Format lock operation result for injection into tool results."""
    ok = result.get("ok", result.get("locked") is not None)
    if action == "check":
        if result.get("locked"):
            return (
                f"━━━ TOOL RESULTS — PLAN LOCK CHECK ━━━\n"
                f"Статус     : ЗАКЛЮЧЕН\n"
                f"Plan ID    : {result.get('plan_id')}\n"
                f"Owned      : {'ДА' if result.get('owned') else 'НЕ (чужд план!)'}\n"
                f"Остава     : {result.get('remaining_min')} мин.\n"
                f"Ако owned=ДА → можеш да продължиш.\n"
                f"Ако owned=НЕ → изчакай или release-ни чуждия lock."
            )
        return "━━━ TOOL RESULTS — PLAN LOCK CHECK ━━━\nСтатус: СВОБОДЕН — можеш да acquire-неш."

    if action == "acquire":
        if ok:
            lock = result.get("lock", {})
            return (
                f"━━━ TOOL RESULTS — PLAN LOCK ACQUIRED ━━━\n"
                f"Plan ID : {lock.get('plan_id')}\n"
                f"TTL     : {lock.get('ttl_min')} мин.\n"
                f"Lock-ът е активен. Не забравяй да го release-неш след завършване."
            )
        return (
            f"━━━ TOOL RESULTS — PLAN LOCK FAILED ━━━\n"
            f"Причина: {result.get('reason', 'unknown')}\n"
            f"Реши конфликта преди да започнеш нов план."
        )

    if action == "release":
        if ok:
            return (
                f"━━━ TOOL RESULTS — PLAN LOCK RELEASED ━━━\n"
                f"Plan ID: {result.get('plan_id')} → ОСВОБОДЕН."
            )
        return f"━━━ TOOL RESULTS — PLAN LOCK RELEASE FAILED ━━━\n{result.get('reason')}"

    if action == "extend":
        if ok:
            return (
                f"━━━ TOOL RESULTS — PLAN LOCK EXTENDED ━━━\n"
                f"Остава още {result.get('remaining_min')} мин."
            )
        return f"━━━ TOOL RESULTS — PLAN LOCK EXTEND FAILED ━━━\n{result.get('reason')}"

    return f"━━━ TOOL RESULTS — PLAN LOCK {action.upper()} ━━━\n{result}"
