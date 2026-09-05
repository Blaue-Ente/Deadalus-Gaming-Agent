"""User content for the build rounds: the ask first, knowledge second, bounded.

The persona turn gets the full injected context (preferences, memories,
awareness, then the owner's request at the very end). The native build rounds
used to receive the *same* string cut at 8000 chars — with knowledge loaded
that cut landed before the request, so the model that had to write the game
never saw what game to write. Here the request is the first block and is
never truncated; RAG knowledge and lessons follow, each capped, and the DoD
closes the message.
"""
from __future__ import annotations

from typing import Any

BUDGET_CHARS = 14_000
_KNOWLEDGE_ITEMS = 4
_KNOWLEDGE_ITEM_CHARS = 900
_LESSON_ITEMS = 5
_LESSON_CHARS = 220


def _knowledge_block(rows: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for k in rows[:_KNOWLEDGE_ITEMS]:
        meta = k.get("metadata") or {}
        title = str(meta.get("title") or meta.get("source") or "note")
        body = str(k.get("content") or "").strip()
        if not body:
            continue
        lines.append(f"── {title} ──\n{body[:_KNOWLEDGE_ITEM_CHARS]}")
    if not lines:
        return ""
    return "━━━ KNOWLEDGE (RAG — retrieved for this ask; apply, do not recite) ━━━\n" + "\n\n".join(lines)


def _lessons_block(rows: list[dict[str, Any]]) -> str:
    lines = []
    for L in rows[:_LESSON_ITEMS]:
        body = str(L.get("content") or "").strip()
        if body:
            kind = str((L.get("metadata") or {}).get("kind") or "general")
            lines.append(f"  • [{kind}] {body[:_LESSON_CHARS]}")
    if not lines:
        return ""
    return "━━━ LESSONS FROM EARLIER BUILDS ━━━\n" + "\n".join(lines)


def build_user_content(
    user_message: str,
    *,
    knowledge: list[dict[str, Any]] | None = None,
    lessons: list[dict[str, Any]] | None = None,
    dod_block: str = "",
    budget: int = BUDGET_CHARS,
) -> str:
    """Owner request → knowledge → lessons → DoD. Later blocks are dropped
    whole (never mid-sentence) when the budget is exceeded; the request stays."""
    request = f"━━━ OWNER REQUEST ━━━\n{(user_message or '').strip()}"
    parts = [request]
    used = len(request)
    for block in (_knowledge_block(knowledge or []), _lessons_block(lessons or []), (dod_block or "").strip()):
        if not block:
            continue
        if used + len(block) + 2 > budget:
            continue
        parts.append(block)
        used += len(block) + 2
    return "\n\n".join(parts)
