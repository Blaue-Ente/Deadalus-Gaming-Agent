"""Playground-specific Daedalus routing helpers (no heavy agent imports)."""
from __future__ import annotations


def is_playground_work_session(session_id: str, user_message: str) -> bool:
    """True when the turn must run the full agent loop (tools, NEED_*), not chat fast-path."""
    sid = (session_id or "").strip().lower()
    if sid.startswith((
        "playground:",
        "pg-",
        "pg_auto",
        "autopilot",
        "pg-auto",
    )):
        return True
    msg = user_message or ""
    return (
        "[🎮 PLAYGROUND РЕЖИМ" in msg
        or "[🔬 PLAYGROUND ПРОУЧВАНЕ" in msg
        or "[AUTOPILOT PLAYGROUND" in msg.upper()
    )
