"""Offline kit — Daedalus keeps building when the internet is gone.

Not a second product: a mode. Local LLM + playground + shell + browser on
localhost stay on. Anything that phones home is refused with a Bulgarian
reason and a suggestion (Jina cache, local files, heuristic playtest).
"""
from __future__ import annotations

import os
from typing import Any

# Tools that need the public internet. MCP stdio may still be local.
NETWORK_TOOLS = frozenset({
    "web_search",
    "web_fetch",
    "web_read",
    "generate_image",
    "mirofish",
})


def enabled() -> bool:
    flag = (os.environ.get("DAEDALUS_OFFLINE") or "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    if flag in ("0", "false", "no", "off"):
        return False
    try:
        from .llm_wardrobe import load
        return bool(load().get("offline"))
    except Exception:
        return False


def deny(tool: str) -> str | None:
    if not enabled():
        return None
    name = (tool or "").strip()
    if name not in NETWORK_TOOLS:
        return None
    return (
        f"── OFFLINE KIT: {name} е изключен ──\n"
        "Интернетът е затворен нарочно. Ползвай локални файлове, shell, "
        "browser към localhost, playtest без LLM облак, или гардероба.\n"
        "Изключи офлайн режима в Studio → LLM, когато искаш Jina / картинки / облак."
    )


def snapshot() -> dict[str, Any]:
    return {
        "ok": True,
        "offline": enabled(),
        "blocked_tools": sorted(NETWORK_TOOLS),
        "still_works": [
            "chat (local LLM)", "read_file", "write_file", "run_command",
            "browser (localhost)", "playtest heuristic", "smoke/verify",
            "play_lab (local install)", "dissect_game (path/lab)",
            "capsule zip", "MCP stdio", "live publish (local /apps/ only)",
            "math3d", "spatial", "game_lint", "profiler parse", "game_canon study",
        ],
    }
