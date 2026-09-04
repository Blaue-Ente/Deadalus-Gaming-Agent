"""Playground-specific Daedalus routing helpers (no heavy agent imports)."""
from __future__ import annotations

_CHAT_SMALLTALK = (
    "здравей", "как си", "добър ден", "hi", "hello",
    "благодар", "thanks", "супер", "добре", "окей", "ок",
    "мислиш ли", "какво смяташ", "не ми харесва", "защо е така",
    "общоприети", "как се настройва", "поговорим",
)


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


def treat_as_conversational(
    user_message: str,
    *,
    nav_intent: str = "",
    block_chat: bool = False,
) -> bool:
    """Whether to skip native-tool discovery / NEED_BATCH planning.

    Build mode and Playground work sessions always keep tools. Navigator
    falling back to intent=chat (cheap Haiku down) must not strip math3d
    / spatial / game_canon from a "направи игра" turn.
    """
    if block_chat:
        return False
    msg = user_message or ""
    if (nav_intent or "").strip().lower() == "chat":
        return True
    if len(msg) < 40:
        return True
    low = msg.lower()
    return any(kw in low for kw in _CHAT_SMALLTALK)
