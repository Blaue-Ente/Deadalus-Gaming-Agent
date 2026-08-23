"""Clarification Tracker — Дедал учи от неразбирания.

Следи всеки път когато Дедал пита уточняващ въпрос.
Веднъж дневно генерира "стил урок" в ChromaDB:
  → следващия път Дедал ще те разбира по-добре без да пита.

Storage: PostgreSQL `daedalus_clarifications` (вж. db.py ensure_schema)
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Question detection — Bulgarian + English patterns
# ---------------------------------------------------------------------------

_CLARIFY_PHRASES = re.compile(
    r"""
    (имаш\s+предвид|искаш\s+да\s+кажеш|уточни|моля\s+уточни|
     да\s+разбирам\s+ли|правилно\s+ли\s+разбирам|
     кое\s+точно|какво\s+точно|кой\s+файл|кой\s+модул|коя\s+страница|
     do\s+you\s+mean|could\s+you\s+clarify|can\s+you\s+specify|
     which\s+one|what\s+exactly|please\s+clarify|did\s+you\s+mean)
    """,
    re.IGNORECASE | re.VERBOSE,
)

_QUESTION_SENTENCE = re.compile(
    r'[^.!?]*\?',
    re.DOTALL,
)


def detect_clarifying_question(text: str) -> list[str]:
    """Return list of clarifying question sentences found in Дедал's reply.
    Empty list → no clarifications detected."""
    if not text or "?" not in text:
        return []
    questions = _QUESTION_SENTENCE.findall(text)
    questions = [q.strip() for q in questions if len(q.strip()) > 10]
    if not questions:
        return []
    # Only flag as "clarifying" if phrasing matches OR there are 2+ questions
    has_clarify_phrase = bool(_CLARIFY_PHRASES.search(text))
    if has_clarify_phrase or len(questions) >= 2:
        return questions[:5]
    # Single question only → flag if short (likely a clarification, not an answer)
    if len(questions) == 1 and len(questions[0]) < 120:
        return questions
    return []


# ---------------------------------------------------------------------------
# Database helpers (async PostgreSQL)
# ---------------------------------------------------------------------------

async def log_clarification(
    session_id: str,
    user_message: str,
    agent_reply: str,
    questions: list[str],
) -> None:
    """Persist a clarification event to PostgreSQL. Non-fatal."""
    try:
        from .host import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO daedalus_clarifications
                    (session_id, user_message, agent_reply, questions_asked, created_at)
                VALUES ($1, $2, $3, $4, NOW())
                """,
                session_id,
                user_message[:600],
                agent_reply[:1200],
                questions,
            )
        log.debug("clarification logged: session=%s questions=%d", session_id, len(questions))
    except Exception as exc:
        log.debug("clarification log failed (non-fatal): %s", exc)


async def get_recent_clarifications(limit: int = 20) -> list[dict]:
    """Fetch last N unresolved clarification events."""
    try:
        from .host import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, session_id, user_message, agent_reply,
                       questions_asked, created_at
                FROM daedalus_clarifications
                WHERE lesson_generated = FALSE
                ORDER BY created_at DESC
                LIMIT $1
                """,
                limit,
            )
            return [dict(r) for r in rows]
    except Exception as exc:
        log.warning("get_recent_clarifications failed: %s", exc)
        return []


async def get_clarification_stats() -> dict:
    """Quick stats for admin UI / Дедал context."""
    try:
        from .host import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            total = await conn.fetchval("SELECT COUNT(*) FROM daedalus_clarifications") or 0
            pending = await conn.fetchval(
                "SELECT COUNT(*) FROM daedalus_clarifications WHERE lesson_generated = FALSE"
            ) or 0
            last_7d = await conn.fetchval(
                """SELECT COUNT(*) FROM daedalus_clarifications
                   WHERE created_at > NOW() - INTERVAL '7 days'"""
            ) or 0
            return {
                "total": int(total),
                "pending_lessons": int(pending),
                "last_7d": int(last_7d),
            }
    except Exception as exc:
        log.warning("clarification_stats failed: %s", exc)
        return {}


async def _mark_lessons_generated(ids: list[int]) -> None:
    try:
        from .host import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE daedalus_clarifications SET lesson_generated = TRUE WHERE id = ANY($1)",
                ids,
            )
    except Exception as exc:
        log.warning("mark_lessons_generated failed: %s", exc)


# ---------------------------------------------------------------------------
# Daily lesson generation
# ---------------------------------------------------------------------------

async def generate_daily_lesson(agent: Any) -> dict:
    """Дедал чете последните неразбирания и пише урок в ChromaDB.

    Трябва да се вика от Planner scheduler (напр. в 03:00).
    Използва Haiku за пестене на токени.
    """
    rows = await get_recent_clarifications(limit=25)
    if not rows:
        return {"ok": True, "lesson": None, "reason": "no pending clarifications"}

    examples = []
    ids_to_mark = []
    for r in rows:
        qs = r.get("questions_asked") or []
        examples.append(
            f"Потребителят каза: «{r['user_message'][:200]}»\n"
            f"Дедал попита: {' / '.join(qs[:3])}"
        )
        ids_to_mark.append(r["id"])

    examples_text = "\n\n".join(examples[:15])

    prompt = f"""Ти си Дедал — анализираш собствените си неразбирания.

Последните {len(rows)} пъти поиска уточнение от Николай:

{examples_text}

На база тези примери:
1. Открий ЕДИН или ДВА ОСНОВНИ МОДЕЛИ на неразбиране (повтарящи се теми)
2. Формулирай конкретно правило за бъдеща комуникация
3. Бъди кратък (≤5 изречения)

Формат:
МОДЕЛ: [кратко описание]
ПРАВИЛО: [конкретно правило — какво да правя по-различно]"""

    try:
        from .agent import _call_claude_primary
        lesson_text = await _call_claude_primary(
            "Ти си Дедал. Анализирай комуникационните си модели и формулирай правило.",
            [{"role": "user", "content": prompt}],
            use_cache_override=False,
        )
        if not lesson_text:
            return {"ok": False, "reason": "empty LLM response"}

        agent.memory.capture_lesson(
            lesson=f"[СТИЛ УРОК] {lesson_text}",
            kind="style_adaptation",
            confidence=0.85,
        )
        await _mark_lessons_generated(ids_to_mark)
        log.info("style lesson generated and stored in ChromaDB (%d clarifications processed)", len(rows))
        return {"ok": True, "lesson": lesson_text, "processed": len(rows)}
    except Exception as exc:
        log.warning("generate_daily_lesson failed: %s", exc)
        return {"ok": False, "reason": str(exc)}
