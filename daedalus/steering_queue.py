"""Steering / follow-up queue for Daedalus Autopilot (Phase 3 — pi-inspired).

Allows mid-run instructions to be injected into the next agent turn without
cancelling the current job. Used by playground autopilot and admin UI.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

log = logging.getLogger(__name__)

_RDB_PREFIX = "adn:daedalus:steer:"
_MAX_QUEUE = 20


def _rdb_get(key: str) -> Any:
    from . import kv
    return kv.get(key)


def _rdb_set(key: str, value: Any) -> None:
    from . import kv
    kv.set(key, value)


def _queue_key(session_id: str) -> str:
    return _RDB_PREFIX + session_id


def enqueue_steering(
    session_id: str,
    message: str,
    *,
    priority: int = 5,
    source: str = "owner",
) -> dict[str, Any]:
    """Add a follow-up instruction to the session queue (lower priority = sooner)."""
    if not session_id or not message.strip():
        return {"ok": False, "error": "session_id and message required"}

    item = {
        "id": uuid.uuid4().hex[:12],
        "message": message.strip()[:4000],
        "priority": max(1, min(10, int(priority))),
        "source": source[:40],
        "ts": int(time.time()),
    }
    key = _queue_key(session_id)
    queue: list[dict[str, Any]] = _rdb_get(key) or []
    queue.append(item)
    queue.sort(key=lambda x: (x.get("priority", 5), x.get("ts", 0)))
    queue = queue[:_MAX_QUEUE]
    _rdb_set(key, queue)

    log.info("steering enqueued: session=%s id=%s prio=%d", session_id[:10], item["id"], item["priority"])
    return {"ok": True, "item": item, "queue_depth": len(queue)}


def peek_steering(session_id: str) -> list[dict[str, Any]]:
    return list(_rdb_get(_queue_key(session_id)) or [])


def pop_steering_messages(session_id: str, *, max_items: int = 3) -> str | None:
    """Drain up to ``max_items`` queued messages for injection into the next turn.

    Returns formatted block or None if queue empty.
    """
    key = _queue_key(session_id)
    queue: list[dict[str, Any]] = _rdb_get(key) or []
    if not queue:
        return None

    items = queue[:max_items]
    remaining = queue[max_items:]
    _rdb_set(key, remaining)

    lines = ["━━━ STEERING QUEUE (mid-run instructions) ━━━"]
    for it in items:
        src = it.get("source", "owner")
        lines.append(f"[{src}] {it.get('message', '')}")
    lines.append("━━━ END STEERING — address these before continuing ━━━")
    return "\n".join(lines)


def clear_steering(session_id: str) -> int:
    queue: list[dict[str, Any]] = _rdb_get(_queue_key(session_id)) or []
    _rdb_set(_queue_key(session_id), [])
    return len(queue)


def enqueue_for_slug(slug: str, message: str, **kwargs: Any) -> dict[str, Any]:
    """Convenience: steer autopilot via playground slug session key."""
    session_key = f"pg-auto:{slug}"
    return enqueue_steering(session_key, message, **kwargs)
