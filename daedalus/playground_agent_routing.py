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


def native_build_system(user_message: str) -> str:
    """System prompt for Playground/game native-tool rounds — build, do not recon."""
    from .game_engineer import attach_to_sys

    sys = (
        "You are Daedalus BUILDING a Playground project now.\n"
        "Do not read daedalus/agent.py, kernel, native_tools, or map the harness.\n"
        "Do not code_search the product. Do not find / across the filesystem.\n"
        "Order:\n"
        "1. Heavy 3D math (SLERP, look-at, rays, projection) → math3d with claimed.\n"
        "2. Many colliders (~N>20) → spatial(action=advise, n=N, dim=2|3) BEFORE nested loops.\n"
        "3. Need GLM / engine sample → game_canon study. Steal structure; never copy books.\n"
        "4. playground_new (kind=static unless asked otherwise), then write_file(slug, path, content).\n"
        "5. Static HTML/canvas: do not start a dev server. Preview is enough.\n"
        "Stay in THIS game. When the slice's files exist, stop and summarize.\n"
    )
    return attach_to_sys(sys, text=user_message or "", kind="static")
